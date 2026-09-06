<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <span>个股决策 · 单股K线 / 信号 / 量价分析{{ stockName ? ` · ${stockName}` : '' }}</span>
      </template>

      <div class="toolbar">
        <el-input
          v-model="code"
          placeholder="6位代码，如 601857 / 159941"
          maxlength="6"
          clearable
          style="width: 200px"
          @keyup.enter="load"
        />
        <span v-if="stockName" class="stock-name">{{ stockName }}</span>
        <span v-else-if="code && code.length === 6" class="muted">正在识别名称…</span>
        <el-select v-if="!isMinute" v-model="strategy" style="width: 220px">
          <el-option
            v-for="s in strategies"
            :key="s.name"
            :label="`${s.label} (${s.name})`"
            :value="s.name"
          />
          <el-option label="包络通道 (envelope)" value="envelope" />
          <el-option label="追涨杀跌反指 (contrarian)" value="contrarian" />
        </el-select>
        <el-select v-else v-model="minuteStrategy" style="width: 180px">
          <el-option
            v-for="s in minuteStrategies"
            :key="s.value"
            :label="s.label"
            :value="s.value"
          />
        </el-select>
        <el-input-number
          v-model="maxBars"
          :min="100"
          :max="5000"
          :step="100"
          style="width: 160px"
          controls-position="right"
        />
        <span class="muted">根K线</span>
        <el-select v-model="interval" style="width: 130px">
          <el-option label="5分钟" value="5m" />
          <el-option label="15分钟" value="15m" />
          <el-option label="30分钟" value="30m" />
          <el-option label="60分钟" value="60m" />
          <el-option label="日K" value="1d" />
          <el-option label="周K" value="1w" />
          <el-option label="月K" value="1M" />
        </el-select>
        <el-checkbox v-model="showEnvelope" style="margin-left: 8px">包络通道</el-checkbox>
        <el-button type="primary" :loading="loading" @click="load">
          <el-icon style="margin-right: 4px"><Search /></el-icon>
          加载
        </el-button>
        <el-button v-if="data" @click="load(true)">刷新数据</el-button>
      </div>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="true"
        style="margin-top: 12px"
        @close="error = ''"
      />

      <template v-if="data">
        <el-alert
          v-if="!data.buy_signals?.length && !data.sell_signals?.length"
          title="当前策略在该股票/区间内没有命中任何买入或卖出条件。"
          type="warning"
          show-icon
          :closable="false"
          style="margin-top: 12px"
        />
        <el-row :gutter="12" style="margin-top: 16px">
          <el-col v-for="item in overviewItems" :key="item.label" :xs="12" :sm="8" :md="4">
            <div class="mini-stat">
              <div class="muted">{{ item.label }}</div>
              <div class="mini-value" :class="item.className || ''">{{ item.text }}</div>
            </div>
          </el-col>
        </el-row>

        <el-row v-if="data.performance" :gutter="12" style="margin-top: 12px">
          <el-col v-for="item in performanceItems" :key="item.label" :xs="12" :sm="8" :md="4">
            <div class="mini-stat">
              <div class="muted">{{ item.label }}</div>
              <div class="mini-value" :class="item.className || ''">{{ item.text }}</div>
            </div>
          </el-col>
        </el-row>

        <div class="chart-wrap">
          <EChart :option="chartOption" height="760px" />
        </div>

        <el-card v-if="signalRecords.length" shadow="never" style="margin-top: 16px">
          <template #header>
            <span>买卖信号明细（{{ signalRecords.length }} 条，含命中条件）</span>
          </template>
          <el-table :data="signalRecords" size="small" max-height="420" style="width: 100%">
            <el-table-column prop="date" label="时间" width="150" />
            <el-table-column label="方向" width="80">
              <template #default="{ row }">
                <span :class="row.side === '买入' ? 'up' : 'down'">{{ row.side }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="price" label="价格" width="100" align="right" />
            <el-table-column label="收益率" width="100" align="right">
              <template #default="{ row }">
                <span v-if="row.trade_return !== null && row.trade_return !== undefined" :class="row.trade_return >= 0 ? 'up' : 'down'">
                  {{ fmtPct(row.trade_return) }}
                </span>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="累计收益率" width="110" align="right">
              <template #default="{ row }">
                <span v-if="row.cum_return !== null && row.cum_return !== undefined" :class="row.cum_return >= 0 ? 'up' : 'down'">
                  {{ fmtPct(row.cum_return) }}
                </span>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column prop="reason" label="命中条件/说明" min-width="260" show-overflow-tooltip />
          </el-table>
        </el-card>

        <el-row :gutter="16" style="margin-top: 16px">
          <el-col :xs="24" :md="14">
            <el-card shadow="never" class="sub-card">
              <template #header>
                <span>趋势射线（共 {{ data.trends?.length || 0 }} 条，代表 {{ representativeTrends.length }} 条）</span>
              </template>
              <div v-if="representativeTrends.length" class="trend-list">
                <div v-for="(ray, i) in representativeTrends" :key="i" class="trend-item" :class="ray.kind">
                  <span class="trend-kind">{{ ray.kind === 'support' ? '支撑' : '压力' }}</span>
                  <span class="muted">{{ ray.start_date }} → {{ ray.end_date }}</span>
                  <span>起点 {{ fmtNum(ray.start_price) }}</span>
                  <span>末端 {{ fmtNum(ray.end_price) }}</span>
                  <el-tag size="small" :type="ray.lifecycle_state === 'confirmed' ? 'success' : 'info'">
                    {{ ray.lifecycle_state }}
                  </el-tag>
                  <el-tag size="small" type="warning">测试 {{ ray.test_count }}</el-tag>
                </div>
              </div>
              <el-empty v-else description="当前窗口未识别到有效趋势射线" :image-size="60" />
            </el-card>
          </el-col>
          <el-col :xs="24" :md="10">
            <el-card shadow="never" class="sub-card">
              <template #header>
                <span>VPVR 量价分布</span>
              </template>
              <div v-if="data.vpvr" class="vpvr-meta">
                <span><b>POC</b> {{ fmtNum(data.vpvr.poc) }}</span>
                <span><b>VAH</b> {{ fmtNum(data.vpvr.vah) }}</span>
                <span><b>VAL</b> {{ fmtNum(data.vpvr.val) }}</span>
              </div>
              <div v-if="vpvrOption" class="vpvr-chart">
                <EChart :option="vpvrOption" height="320px" />
              </div>
              <el-empty v-else description="暂无成交量数据" :image-size="60" />
            </el-card>
          </el-col>
        </el-row>
      </template>

      <el-empty v-else-if="!loading && !error" description="输入股票代码后点击“加载”" />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import EChart from '../components/EChart.vue'
import { usePersistentRef } from '../composables/usePersistentRef'

const route = useRoute()
const code = usePersistentRef('kline.code', '601857')
const strategy = usePersistentRef('kline.strategy', 'watchlist')
const minuteStrategy = usePersistentRef('kline.minuteStrategy', 'vwap')
const maxBars = usePersistentRef('kline.maxBars', 500)
const interval = usePersistentRef('kline.interval', '1d')
const showEnvelope = usePersistentRef('kline.showEnvelope', true)
const minuteStrategies = [
  { value: 'vwap', label: 'VWAP回归' },
  { value: 'macd', label: 'MACD金叉死叉' },
  { value: 'boll', label: '布林反转' },
  { value: 'contrarian', label: '追涨杀跌反指' }
]
const isMinute = computed(() => ['5m', '15m', '30m', '60m'].includes(interval.value))
const data = ref(null)
const loading = ref(false)
const error = ref('')
const stockName = ref('')
const strategies = ref([])
let nameTimer = null

onMounted(async () => {
  try {
    const meta = await api.meta()
    strategies.value = meta.strategies || []
    if (!strategy.value || !strategies.value.some(s => s.name === strategy.value)) {
      strategy.value = meta.strategy || (strategies.value[0]?.name) || 'reversal'
    }
    const queryCode = route.query.code ? String(route.query.code).replace(/\D/g, '').padStart(6, '0') : ''
    if (queryCode && queryCode !== code.value) code.value = queryCode
    // Restore last viewed stock after reload if no data is present yet.
    if (code.value && code.value !== '000000' && !data.value) {
      await load()
    }
  } catch (e) {
    error.value = e.message
  }
})

watch(
  () => route.query.code,
  (newCode) => {
    if (!newCode) return
    const next = String(newCode).replace(/\D/g, '').padStart(6, '0')
    if (next && next !== code.value) {
      code.value = next
      load()
    }
  }
)

watch(code, (val) => {
  if (nameTimer) {
    clearTimeout(nameTimer)
    nameTimer = null
  }
  const c = String(val || '').replace(/\D/g, '').padStart(6, '0')
  if (c.length === 6 && c !== '000000') {
    stockName.value = ''
    nameTimer = setTimeout(() => lookupStockName(c), 300)
  } else {
    stockName.value = ''
  }
})

async function lookupStockName(c) {
  try {
    const res = await api.stockName(c)
    const current = String(code.value || '').replace(/\D/g, '').padStart(6, '0')
    if (current === c && res?.name) stockName.value = res.name
  } catch (e) {
    // Ignore name lookup errors; data load will still show the name later.
    stockName.value = ''
  }
}

onBeforeUnmount(() => {
  if (nameTimer) clearTimeout(nameTimer)
})

const signalRecords = computed(() => {
  const rows = []
  for (const s of data.value?.buy_signals || []) {
    rows.push({ date: s.date, side: '买入', price: s.price, reason: s.reason || '买入条件命中', pnl_pct: null })
  }
  for (const s of data.value?.sell_signals || []) {
    rows.push({ date: s.date, side: '卖出', price: s.price, reason: s.reason || '卖出条件命中', pnl_pct: s.pnl_pct ?? null })
  }
  rows.sort((a, b) => String(a.date).localeCompare(String(b.date)))

  let cum = 0 // cumulative compounded return (0 = initial)
  for (const row of rows) {
    if (row.side === '卖出' && row.pnl_pct !== null && row.pnl_pct !== undefined) {
      row.trade_return = row.pnl_pct
      cum = (1 + cum) * (1 + row.pnl_pct) - 1
      row.cum_return = cum
    } else {
      row.trade_return = null
      row.cum_return = null
    }
  }
  return rows
})

const representativeTrends = computed(() => {
  return (data.value?.trends || []).filter(r => r.is_family_representative)
})

const performanceItems = computed(() => {
  const p = data.value?.performance
  if (!p) return []
  return [
    { label: '策略总收益', text: fmtPct(p.total_return), className: (p.total_return || 0) >= 0 ? 'up' : 'down' },
    { label: '交易次数', text: p.trade_count ?? 0 },
    { label: '胜率', text: fmtPct(p.win_rate) },
    { label: '平均盈利', text: fmtPct(p.avg_win), className: 'up' },
    { label: '平均亏损', text: fmtPct(p.avg_loss), className: 'down' },
    { label: '盈亏比', text: num(p.profit_loss_ratio, 2) }
  ]
})

const overviewItems = computed(() => {
  const o = data.value?.overview || {}
  const items = [
    { label: '最新价', text: o.close ?? '-' },
    { label: '涨跌幅', text: fmtPct(o.change_pct), className: (o.change_pct || 0) >= 0 ? 'up' : 'down' },
    { label: '量比', text: num(o.vol_ratio, 2) },
    { label: 'MA5', text: num(o.ma5, 3) },
    { label: 'MA20', text: num(o.ma20, 3) },
    { label: 'MA60', text: num(o.ma60, 3) }
  ]
  if (o.vwap !== undefined && o.vwap !== null) {
    items.push({ label: 'VWAP', text: num(o.vwap, 3) })
  }
  if (o.skdj_k !== undefined && o.skdj_k !== null) {
    items.push({ label: 'SKDJ', text: `${num(o.skdj_k, 1)} / ${num(o.skdj_d, 1)}` })
  }
  items.push({ label: 'MACD', text: num(o.macd_hist, 4) })
  return items
})

const chartOption = computed(() => {
  if (!data.value) return null
  const d = data.value
  const dates = d.dates || []
  const upColor = '#e8403a'
  const downColor = '#1ba27a'

  const volumeData = (d.volumes || []).map((v, i) => {
    const k = d.kline[i] || []
    const isUp = k[1] >= k[0]
    return {
      value: v,
      itemStyle: { color: isUp ? upColor : downColor, opacity: 0.7 }
    }
  })

  const buyData = (d.buy_signals || []).map(s => [s.date, s.price])
  const sellData = (d.sell_signals || []).map(s => [s.date, s.price])

  const categoryAxis = (gridIndex) => ({
    type: 'category',
    gridIndex,
    data: dates,
    axisLine: { lineStyle: { color: '#d9dde3' } },
    axisLabel: { show: gridIndex === 3 }
  })

  const valueAxis = (gridIndex, opts = {}) => ({
    type: 'value',
    gridIndex,
    scale: true,
    splitLine: { lineStyle: { color: '#eef1f4' } },
    axisLabel: { show: true },
    ...opts
  })

  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      backgroundColor: '#fff',
      borderColor: '#e5e8ec',
      textStyle: { color: '#1f2329' }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: {
      top: 4,
      data: [
        'K线', 'MA5', 'MA20', 'MA60',
        ...(showEnvelope.value && d.envelope_upper ? ['包络上轨', '包络下轨'] : []),
        ...(d.vwap ? ['VWAP'] : []),
        '买入信号', '卖出信号'
      ]
    },
    grid: [
      { left: 70, right: 24, top: 40, height: '42%' },
      { left: 70, right: 24, top: '56%', height: '11%' },
      { left: 70, right: 24, top: '72%', height: '11%' },
      { left: 70, right: 24, top: '88%', height: '9%' }
    ],
    xAxis: [
      categoryAxis(0), categoryAxis(1), categoryAxis(2), categoryAxis(3)
    ],
    yAxis: [
      valueAxis(0, { scale: true }),
      valueAxis(1, { scale: false }),
      valueAxis(2, { scale: false }),
      valueAxis(3, { min: 0, max: 100 })
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2, 3], start: 60, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2, 3], bottom: 0, height: 14 }
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: d.kline || [],
        xAxisIndex: 0, yAxisIndex: 0,
        itemStyle: { color: upColor, color0: downColor, borderColor: upColor, borderColor0: downColor },
        markLine: {
          silent: true,
          symbol: 'none',
          data: (d.trends || []).map(ray => [
            {
              coord: [ray.start_date, ray.start_price],
              lineStyle: {
                color: ray.kind === 'support' ? '#047857' : '#c2410c',
                width: ray.is_family_representative ? 1.8 : 0.8,
                type: ray.is_family_representative ? 'solid' : 'dashed',
                opacity: ray.is_family_representative ? 0.85 : 0.3
              }
            },
            { coord: [ray.end_date, ray.end_price] }
          ])
        }
      },
      { name: 'MA5', type: 'line', data: d.ma5 || [], smooth: true, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: '#f5a623' } },
      { name: 'MA20', type: 'line', data: d.ma20 || [], smooth: true, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: '#2c6fbb' } },
      { name: 'MA60', type: 'line', data: d.ma60 || [], smooth: true, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: '#86909c' } },
      ...(showEnvelope.value && d.envelope_upper ? [
        { name: '包络上轨', type: 'line', data: d.envelope_upper || [], smooth: true, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: '#94a3b8', type: 'dashed' } },
        { name: '包络下轨', type: 'line', data: d.envelope_lower || [], smooth: true, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1, color: '#94a3b8', type: 'dashed' } }
      ] : []),
      ...(d.vwap ? [{ name: 'VWAP', type: 'line', data: d.vwap || [], smooth: true, showSymbol: false, xAxisIndex: 0, yAxisIndex: 0, lineStyle: { width: 1.2, color: '#7e22ce' } }] : []),
      {
        name: '买入信号', type: 'scatter', data: buyData, xAxisIndex: 0, yAxisIndex: 0,
        symbol: 'circle', symbolSize: 16, symbolOffset: [0, -14], z: 30,
        itemStyle: { color: '#f59e0b', borderColor: '#fff', borderWidth: 2, shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.35)' },
        label: { show: true, formatter: '买', position: 'top', distance: 2, color: '#fff', fontSize: 11, fontWeight: 700, backgroundColor: '#f59e0b', padding: [1, 5], borderRadius: 4 }
      },
      {
        name: '卖出信号', type: 'scatter', data: sellData, xAxisIndex: 0, yAxisIndex: 0,
        symbol: 'circle', symbolSize: 16, symbolOffset: [0, 14], z: 30,
        itemStyle: { color: '#1ba27a', borderColor: '#fff', borderWidth: 2, shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.35)' },
        label: { show: true, formatter: '卖', position: 'bottom', distance: 2, color: '#fff', fontSize: 11, fontWeight: 700, backgroundColor: '#1ba27a', padding: [1, 5], borderRadius: 4 }
      },
      { name: '成交量', type: 'bar', data: volumeData, xAxisIndex: 1, yAxisIndex: 1 },
      { name: 'MACD', type: 'bar', data: d.macd_hist || [], xAxisIndex: 2, yAxisIndex: 2, itemStyle: { color: (p) => p.value >= 0 ? upColor : downColor } },
      { name: 'DIF', type: 'line', data: d.dif || [], showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, lineStyle: { width: 1, color: '#f5a623' } },
      { name: 'DEA', type: 'line', data: d.dea || [], showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, lineStyle: { width: 1, color: '#2c6fbb' } },
      { name: 'SKDJ K', type: 'line', data: d.skdj_k || [], showSymbol: false, xAxisIndex: 3, yAxisIndex: 3, lineStyle: { width: 1, color: '#e8403a' } },
      { name: 'SKDJ D', type: 'line', data: d.skdj_d || [], showSymbol: false, xAxisIndex: 3, yAxisIndex: 3, lineStyle: { width: 1, color: '#1ba27a' } },
      { name: 'SKDJ J', type: 'line', data: d.skdj_j || [], showSymbol: false, xAxisIndex: 3, yAxisIndex: 3, lineStyle: { width: 1, color: '#f5a623' } }
    ]
  }
})

const vpvrOption = computed(() => {
  const vpvr = data.value?.vpvr
  if (!vpvr || !vpvr.bins?.length) return null
  const bins = vpvr.bins
  const labels = bins.map(b => b.price_low.toFixed(2))
  const values = bins.map(b => ({
    value: b.volume_share,
    itemStyle: {
      color: b.is_poc ? '#b45309' : b.in_value_area ? '#64748b' : '#94a3b8',
      opacity: b.is_poc ? 0.9 : 0.55
    }
  }))
  return {
    animation: false,
    grid: { left: 10, right: 70, top: 10, bottom: 30 },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params) => {
        const p = params[0]
        const bin = bins[p.dataIndex]
        return `${bin.price_low.toFixed(2)} - ${bin.price_high.toFixed(2)}<br/>份额 ${(bin.volume_share * 100).toFixed(2)}%`
      }
    },
    xAxis: { type: 'value', name: '量占比', axisLabel: { formatter: (v) => (v * 100).toFixed(0) + '%' } },
    yAxis: {
      type: 'category',
      data: labels,
      inverse: true,
      axisLabel: { show: true, fontSize: 10, interval: 4 }
    },
    series: [{ type: 'bar', data: values, barWidth: '70%', showBackground: true, backgroundStyle: { color: '#f1f5f9' } }]
  }
})

async function load(force = false) {
  const c = String(code.value || '').replace(/\D/g, '').padStart(6, '0')
  if (c.length !== 6 || c === '000000') {
    error.value = '请输入正确的 6 位股票/ETF 代码'
    return
  }
  code.value = c
  loading.value = true
  error.value = ''
  try {
    // force=true bypasses browser URL cache by adding timestamp
    const activeStrategy = isMinute.value ? minuteStrategy.value : strategy.value
    const params = { strategy: activeStrategy, max_bars: maxBars.value, interval: interval.value }
    if (force) params.refresh = true
    data.value = await api.kline(c, params)
    const remoteName = data.value?.name
    if (remoteName && remoteName !== c) {
      stockName.value = remoteName
    } else {
      // Backend may fall back to the code itself for non-watchlist symbols.
      stockName.value = ''
      lookupStockName(c)
    }
  } catch (e) {
    error.value = e.message
    data.value = null
    stockName.value = ''
  } finally {
    loading.value = false
  }
}

function num(v, digits = 2) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return Number(v).toFixed(digits)
}

function fmtNum(v, digits = 3) {
  return num(v, digits)
}

function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v * 100).toFixed(2) + '%'
}
</script>

<style scoped>
.toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
}
.mini-stat {
  background: #f8fafc;
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 8px;
}
.mini-value {
  font-size: 18px;
  font-weight: 700;
  margin-top: 4px;
}
.chart-wrap {
  margin-top: 16px;
}
.up { color: #e8403a; }
.down { color: #1ba27a; }
.stock-name {
  color: #1d4ed8;
  font-weight: 600;
  font-size: 14px;
  white-space: nowrap;
}
.sub-card {
  height: 100%;
}
.trend-list {
  max-height: 360px;
  overflow: auto;
}
.trend-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px 6px;
  border-bottom: 1px solid #f1f5f9;
  font-size: 13px;
}
.trend-kind {
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  color: #fff;
  font-size: 12px;
}
.trend-item.support .trend-kind { background: #047857; }
.trend-item.resistance .trend-kind { background: #c2410c; }
.vpvr-meta {
  display: flex;
  gap: 16px;
  margin-bottom: 8px;
  font-size: 13px;
}
.vpvr-meta b {
  color: #1f2329;
}
</style>

<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <span>日内做T决策 · 分时MACD / 竞价跳空 / 早盘量能</span>
      </template>

      <div style="display: flex; flex-wrap: wrap; gap: 12px; align-items: center">
        <el-input v-model="code" placeholder="6位代码" maxlength="6" clearable style="width: 150px" @keyup.enter="run" />
        <div>
          <div class="muted">统计最近交易日（1分钟K线，约240根/天）</div>
          <el-input-number v-model="maxDays" :min="1" :max="120" :step="1" style="width: 130px" />
          <div class="muted" style="margin-top: 4px; max-width: 300px; line-height: 1.4">腾讯1分钟接口单次约回320根（≈1.3~2个交易日），请求天数大于可用数据时会按实际天数统计</div>
        </div>
        <el-button type="primary" :loading="running" @click="run">
          <el-icon style="margin-right: 4px"><DataAnalysis /></el-icon>
          分析今日做T
        </el-button>
      </div>

      <el-alert v-if="error" :title="error" type="error" show-icon :closable="true" style="margin-top: 12px" @close="error=''" />
      <el-alert v-if="result?.limit && result.limit.days_available < result.limit.requested_days"
        :title="`实际覆盖 ${result.limit.days_available} 个交易日（1分钟K线 ${result.limit.bars_1m} 根）；请求最近 ${result.limit.requested_days} 天。${result.limit.note}`"
        type="info" show-icon :closable="false" style="margin-top: 12px" />
    </el-card>

    <el-card v-if="running" shadow="never" style="margin-top: 16px">
      <template #header><span>日内做T分析进度</span></template>
      <el-progress :percentage="100" :indeterminate="true" :duration="2" :show-text="false" style="margin-bottom: 12px" />
      <div class="log-box">{{ progressLogs.join('\n') || '准备中...' }}</div>
    </el-card>

    <el-card v-if="monitor" shadow="never" style="margin-top: 16px">
      <template #header>
        <span>实时自选池监控 · 1分钟K线 → 飞书推送</span>
      </template>
      <el-row :gutter="12">
        <el-col :xs="12" :sm="8" :md="4">
          <div class="mini-stat">
            <div class="muted">运行状态</div>
            <div class="mini-value" :class="monitor.running ? 'up' : 'down'">{{ monitor.running ? '运行中' : '已停止' }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="5">
          <div class="mini-stat">
            <div class="muted">最近扫描</div>
            <div class="mini-value small">{{ monitor.last_scan_at || '非交易时段/未扫描' }}</div>
          </div>
        </el-col>
        <el-col :xs="12" :sm="8" :md="5">
          <div class="mini-stat">
            <div class="muted">已推送信号</div>
            <div class="mini-value">{{ monitor.total_signals_pushed ?? 0 }}</div>
          </div>
        </el-col>
        <el-col :xs="24" :sm="12" :md="10">
          <div class="mini-stat">
            <div class="muted">最近错误</div>
            <div class="mini-value small">{{ monitor.last_scan_error || '无' }}</div>
          </div>
        </el-col>
      </el-row>
      <div style="margin-top: 10px">
        <el-button size="small" @click="loadMonitor">刷新状态</el-button>
        <el-button size="small" :type="monitor.running ? 'danger' : 'primary'" @click="toggleMonitor">
          {{ monitor.running ? '停止监控' : '启动监控' }}
        </el-button>
      </div>
    </el-card>

    <template v-if="result">
      <el-card shadow="never" style="margin-top: 16px">
        <template #header>
          <span>今日做T建议 · {{ result.code }} {{ result.name }}</span>
        </template>
        <el-alert
          :title="`建议：${decision.mode}`"
          :description="decision.reason"
          :type="decision.mode === 'S->B' ? 'warning' : decision.mode === 'B->S' ? 'success' : 'info'"
          show-icon
          :closable="false"
        />
      </el-card>

      <el-card shadow="never" style="margin-top: 16px">
        <template #header><span>近{{ result.stats.days || 0 }}日规则回测统计</span></template>
        <el-row :gutter="12">
          <el-col v-for="item in statsItems" :key="item.label" :xs="12" :sm="8" :md="4">
            <div class="mini-stat">
              <div class="muted">{{ item.label }}</div>
              <div class="mini-value" :class="item.className || ''">{{ item.text }}</div>
            </div>
          </el-col>
        </el-row>
      </el-card>

      <el-card v-if="result.recent?.length" shadow="never" style="margin-top: 16px">
        <template #header><span>最近交易日做T明细</span></template>
        <el-table :data="result.recent" size="small" style="width: 100%; cursor: pointer" @row-click="openDetail">
          <el-table-column prop="date" label="日期" width="110" />
          <el-table-column prop="mode" label="模式" width="90" />
          <el-table-column label="跳空" width="90" align="right">
            <template #default="{ row }">{{ fmtPct(row.gap_pct) }}</template>
          </el-table-column>
          <el-table-column label="早盘量能" width="100" align="right">
            <template #default="{ row }">{{ (row.early_volume_ratio || 0).toFixed(2) }}x</template>
          </el-table-column>
          <el-table-column label="买入价" prop="buy_price" width="90" align="right" />
          <el-table-column label="卖出价" prop="sell_price" width="90" align="right" />
          <el-table-column label="收益" width="90" align="right">
            <template #default="{ row }">
              <span :class="(row.pnl || 0) >= 0 ? 'up' : 'down'">{{ fmtPct(row.pnl) }}</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <el-dialog v-model="detailVisible" :title="`${code} ${detailDate} 分时图`" width="80%" top="6vh">
      <div v-if="detailLoading" class="log-box">加载分时数据...</div>
      <el-alert v-if="detailError" :title="detailError" type="error" show-icon :closable="true" style="margin-bottom: 12px" @close="detailError=''" />
      <EChart v-if="detailChartOption" :option="detailChartOption" height="560px" />
      <div v-if="signalRows.length" style="margin-top: 12px">
        <el-table :data="signalRows" size="small" border style="width: 100%">
          <el-table-column label="方向" width="70" align="center">
            <template #default="{ row }">
              <span :class="row.side === '买' ? 'up' : 'down'">{{ row.side }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="time" label="时间" width="90" align="center" />
          <el-table-column prop="price" label="价格" width="100" align="right" />
          <el-table-column prop="reason" label="信号理由" />
        </el-table>
      </div>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import EChart from '../components/EChart.vue'

const code = ref('601857')
const maxDays = ref(30)
const running = ref(false)
const result = ref(null)
const error = ref('')
const progressLogs = ref([])
let progressTimer = null

const monitor = ref(null)

const detailVisible = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const detail = ref(null)
const detailDate = ref('')

function pushLog(msg) {
  progressLogs.value.push(msg)
}

function startFakeProgress() {
  progressLogs.value = ['开始拉取日线与1分钟数据...']
  progressTimer = setTimeout(() => pushLog('计算每日分时MACD...'), 1000)
  setTimeout(() => pushLog('统计跳空/早盘量能/历史做T胜率...'), 3000)
}

function stopProgress() {
  if (progressTimer) clearTimeout(progressTimer)
  progressTimer = null
}

async function loadMonitor() {
  try {
    monitor.value = await api.intradayMonitorStatus()
  } catch (e) {
    monitor.value = { running: false, total_signals_pushed: 0, last_scan_error: e.message }
  }
}

async function toggleMonitor() {
  if (!monitor.value) return
  try {
    if (monitor.value.running) {
      monitor.value = await api.intradayMonitorStop()
    } else {
      monitor.value = await api.intradayMonitorStart()
    }
  } catch (e) {
    monitor.value.last_scan_error = e.message
  }
}

onMounted(loadMonitor)
onBeforeUnmount(stopProgress)

const decision = computed(() => result.value?.decision || {})
const statsItems = computed(() => {
  const s = result.value?.stats || {}
  return [
    { label: '统计天数', text: s.days ?? 0 },
    { label: '配对完成', text: s.completed ?? 0 },
    { label: '历史胜率', text: fmtPct(s.win_rate), className: (s.win_rate || 0) >= 0.5 ? 'up' : 'down' },
    { label: '平均收益', text: fmtPct(s.avg_pnl), className: (s.avg_pnl || 0) >= 0 ? 'up' : 'down' },
    { label: '平均盈利', text: fmtPct(s.avg_win), className: 'up' },
    { label: '平均亏损', text: fmtPct(s.avg_loss), className: 'down' }
  ]
})

const signalRows = computed(() => {
  const d = detail.value
  if (!d) return []
  const rows = [
    ...(d.sell_points || []).map(p => ({ ...p, side: '卖' })),
    ...(d.buy_points || []).map(p => ({ ...p, side: '买' }))
  ]
  return rows.sort((a, b) => String(a.time).localeCompare(String(b.time)))
})

const detailChartOption = computed(() => {
  const d = detail.value
  if (!d?.bars?.length) return null
  const times = d.bars.map(b => b.time)
  const kline = d.bars.map(b => [b.open, b.close, b.low, b.high])
  const volumeData = d.bars.map(b => ({
    value: b.volume,
    itemStyle: { color: b.close >= b.open ? '#e8403a' : '#1ba27a', opacity: 0.6 }
  }))
  const buyData = (d.buy_points || []).map(p => [p.time, p.price])
  const sellData = (d.sell_points || []).map(p => [p.time, p.price])
  const macdSeries = d.macd_series || []
  const macdHist = macdSeries.map(m => ({
    value: m.hist,
    itemStyle: { color: m.hist >= 0 ? '#e8403a' : '#1ba27a' }
  }))
  const difData = macdSeries.map(m => m.dif)
  const deaData = macdSeries.map(m => m.dea)
  return {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: [
      { left: 60, right: 20, top: 25, height: '42%' },
      { left: 60, right: 20, top: '52%', height: '12%' },
      { left: 60, right: 20, top: '68%', height: '22%' }
    ],
    xAxis: [
      { type: 'category', data: times, gridIndex: 0, axisLabel: { show: false } },
      { type: 'category', data: times, gridIndex: 1, axisLabel: { show: false } },
      { type: 'category', data: times, gridIndex: 2 }
    ],
    yAxis: [
      { type: 'value', scale: true, gridIndex: 0 },
      { type: 'value', gridIndex: 1 },
      { type: 'value', gridIndex: 2, scale: true }
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2] },
      { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 5, height: 14 }
    ],
    series: [
      { name: '1分钟K线', type: 'candlestick', data: kline, xAxisIndex: 0, yAxisIndex: 0, itemStyle: { color: '#e8403a', color0: '#1ba27a', borderColor: '#e8403a', borderColor0: '#1ba27a' } },
      {
        name: '买入', type: 'scatter', data: buyData, xAxisIndex: 0, yAxisIndex: 0,
        symbol: 'circle', symbolSize: 18, symbolOffset: [0, -14],
        itemStyle: { color: '#f59e0b', borderColor: '#fff', borderWidth: 2, shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.35)' },
        label: { show: true, formatter: '买', position: 'top', distance: 2, color: '#fff', fontSize: 11, fontWeight: 700, backgroundColor: '#f59e0b', padding: [1, 5], borderRadius: 4 }
      },
      {
        name: '卖出', type: 'scatter', data: sellData, xAxisIndex: 0, yAxisIndex: 0,
        symbol: 'circle', symbolSize: 18, symbolOffset: [0, 14],
        itemStyle: { color: '#1ba27a', borderColor: '#fff', borderWidth: 2, shadowBlur: 6, shadowColor: 'rgba(0,0,0,0.35)' },
        label: { show: true, formatter: '卖', position: 'bottom', distance: 2, color: '#fff', fontSize: 11, fontWeight: 700, backgroundColor: '#1ba27a', padding: [1, 5], borderRadius: 4 }
      },
      { name: '成交量', type: 'bar', data: volumeData, xAxisIndex: 1, yAxisIndex: 1 },
      { name: 'MACD', type: 'bar', data: macdHist, xAxisIndex: 2, yAxisIndex: 2 },
      { name: 'DIF', type: 'line', data: difData, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, lineStyle: { color: '#f5a623', width: 1 } },
      { name: 'DEA', type: 'line', data: deaData, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2, lineStyle: { color: '#2c6fbb', width: 1 } }
    ]
  }
})

async function openDetail(row) {
  detailDate.value = row.date
  detail.value = null
  detailError.value = ''
  detailVisible.value = true
  detailLoading.value = true
  try {
    detail.value = await api.intradayTDetail(code.value, row.date)
  } catch (e) {
    detailError.value = e.message
  } finally {
    detailLoading.value = false
  }
}

async function run() {
  const c = String(code.value || '').replace(/\D/g, '').padStart(6, '0')
  if (c.length !== 6 || c === '000000') {
    error.value = '请输入正确的6位代码'
    return
  }
  code.value = c
  running.value = true
  error.value = ''
  stopProgress()
  startFakeProgress()
  try {
    result.value = await api.intradayT(c, { max_days: maxDays.value })
    pushLog('分析完成')
  } catch (e) {
    error.value = e.message
    result.value = null
    pushLog('分析失败: ' + e.message)
  } finally {
    stopProgress()
    running.value = false
  }
}

function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v * 100).toFixed(2) + '%'
}
</script>

<style scoped>
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
.mini-value.small {
  font-size: 13px;
  font-weight: 500;
  word-break: break-all;
}
.up { color: #e8403a; }
.down { color: #1ba27a; }
.log-box {
  background: #0f172a;
  color: #d1e7ff;
  border-radius: 8px;
  padding: 10px 12px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 180px;
  overflow: auto;
}
</style>

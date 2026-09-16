<template>
  <div class="chan-page">
    <el-card shadow="never" class="toolbar-card">
      <div class="toolbar">
        <el-select v-model="code" filterable allow-create default-first-option
                   :reserve-keyword="false" style="width: 200px"
                   placeholder="指数或 6 位代码" @change="loadStructure(false)">
          <el-option-group label="宽基指数">
            <el-option v-for="i in indices" :key="i.code"
                       :label="`${i.name} ${i.code}`" :value="i.code" />
          </el-option-group>
          <el-option-group v-if="watchlist.length" label="自选股">
            <el-option v-for="w in watchlist" :key="w.code"
                       :label="`${w.name} ${w.code}`" :value="w.code" />
          </el-option-group>
        </el-select>

        <el-radio-group v-model="interval" @change="loadStructure(false)">
          <el-radio-button value="1d">日线</el-radio-button>
          <el-radio-button value="1w">周线</el-radio-button>
          <el-radio-button value="1M">月线</el-radio-button>
        </el-radio-group>

        <el-divider direction="vertical" />
        <span class="layer-label">叠加</span>
        <el-checkbox v-model="show.bi" label="笔" />
        <el-checkbox v-model="show.zhongshu" label="中枢" />
        <el-checkbox v-model="show.signals" label="买卖点" />
        <el-checkbox v-model="show.divergence" label="背驰" />
        <el-checkbox v-model="show.ma" label="均线" />
        <el-select v-model="chosenMas" multiple collapse-tags collapse-tags-tooltip
                   :disabled="!show.ma" placeholder="均线" style="width: 190px" size="small">
          <el-option v-for="m in MA_KEYS" :key="m" :label="m.toUpperCase()" :value="m" />
        </el-select>

        <el-divider direction="vertical" />
        <el-select v-model="spanBars" style="width: 120px" size="small">
          <el-option label="全部" :value="0" />
          <el-option label="最近 250 根" :value="250" />
          <el-option label="最近 120 根" :value="120" />
          <el-option label="最近 60 根" :value="60" />
        </el-select>

        <div class="spacer" />
        <span v-if="data" class="as-of">
          {{ data.name }} · {{ data.level }} · 截至 {{ data.as_of }}
          <b :class="data.quote.change >= 0 ? 'up' : 'down'">
            {{ data.quote.close }} ({{ data.quote.change_pct >= 0 ? '+' : '' }}{{ data.quote.change_pct }}%)
          </b>
        </span>
        <el-button :icon="Refresh" :loading="loading" @click="loadStructure(true)">刷新</el-button>
      </div>
    </el-card>

    <el-alert v-if="error" type="error" :title="error" show-icon :closable="false" class="mt" />

    <div class="layout">
      <el-card shadow="never" class="chart-card">
        <el-skeleton v-if="loading && !data" :rows="10" animated />
        <el-empty v-else-if="!data" description="暂无数据" />
        <div v-else class="chart-wrap">
          <el-alert v-if="snapNote" type="warning" :title="snapNote" show-icon :closable="false"
                    class="mb" />
          <EChart :key="`${code}-${interval}`" :option="chartOption" height="720px" />
        </div>
      </el-card>

      <el-card shadow="never" class="side-card">
        <template v-if="data">
          <div class="side-head">
            <span class="side-title">研判</span>
            <span>
              <el-tag size="small" :type="data.kind === 'stock' ? 'warning' : 'info'">
                {{ data.kind === 'stock' ? '个股' : '指数' }}
              </el-tag>
              <el-tag size="small" type="info" class="ml4">{{ data.level }}</el-tag>
            </span>
          </div>

          <div class="verdict">
            <span class="verdict-label">走势类型</span>
            <span class="verdict-value">{{ data.trend.label }}</span>
          </div>

          <div v-for="(b, i) in data.summary.bullets" :key="i" class="bullet">
            <el-tag size="small" effect="plain" class="lesson-tag">{{ b.lesson }}课</el-tag>
            <span class="bullet-text">{{ b.text }}</span>
          </div>

          <el-divider />

          <div class="kv-grid">
            <div class="kv">
              <span class="k">当前中枢</span>
              <span v-if="data.last_zhongshu" class="v mono">
                [{{ data.last_zhongshu.zd }}, {{ data.last_zhongshu.zg }}]
                <em>{{ data.last_zhongshu.bi_count }}笔</em>
                <em v-if="data.last_zhongshu.is_extended" class="warn">已延伸至上限</em>
                <em :class="data.last_zhongshu.leave_date ? '' : 'ok'">
                  {{ data.last_zhongshu.leave_date ? '离开于 ' + data.last_zhongshu.leave_date : '尚无离开笔' }}
                </em>
              </span>
              <span v-else class="v muted">—</span>
            </div>
            <div class="kv">
              <span class="k">均线九分类</span>
              <span class="v">
                <b class="cat">{{ data.ma_category.category ?? '—' }}</b> / 9
                <em v-if="data.ma_category.partial" class="warn">均线不完整</em>
              </span>
            </div>
            <div class="kv">
              <span class="k">底部区间</span>
              <span v-if="data.bottom_zone.zone_low" class="v mono">
                [{{ data.bottom_zone.zone_low }}, {{ data.bottom_zone.zone_high }}]
                <em :class="statusClass(data.bottom_zone.status)">{{ statusText(data.bottom_zone.status) }}</em>
              </span>
              <span v-else class="v muted">—</span>
            </div>
            <div class="kv">
              <span class="k">中阴阶段</span>
              <span class="v">
                <el-tag size="small" :type="data.zhongyin.active ? 'warning' : 'success'">
                  {{ data.zhongyin.active === null ? '无法判定' : data.zhongyin.active ? '处于中阴' : '已结束' }}
                </el-tag>
                <em v-if="data.zhongyin.from_date">{{ data.zhongyin.from_date }} 起</em>
              </span>
            </div>
            <div class="kv">
              <span class="k">结构计数</span>
              <span class="v mono">
                {{ data.counts.bis }} 笔 · {{ data.counts.segments }} 线段 ·
                {{ data.counts.zhongshus }} 中枢
              </span>
            </div>
            <div class="kv">
              <span class="k">未完成笔</span>
              <span v-if="data.pending_bi" class="v mono">
                {{ data.pending_bi.direction === 'down' ? '向下' : '向上' }}
                {{ data.pending_bi.start_date }} {{ data.pending_bi.start_price }}
                →
                {{ data.pending_bi.end_date }} {{ data.pending_bi.end_price }}
                <em>{{ data.pending_bi.bars }}根</em>
                <em :class="data.pending_bi.at_latest ? 'warn' : 'ok'">
                  {{ data.pending_bi.at_latest ? '仍在创新极值' : '极值已过' }}
                </em>
              </span>
              <span v-else class="v muted">无(末笔之后没有反向走势)</span>
            </div>
          </div>

          <el-divider />

          <div class="side-title sm">最近信号</div>
          <el-empty v-if="!data.signals.length" description="无" :image-size="48" />
          <div v-else class="sig-list">
            <div v-for="(s, i) in data.signals.slice(-7).reverse()" :key="i" class="sig-row">
              <span class="sig-label" :class="s.direction">{{ s.label }}</span>
              <span class="sig-date mono">{{ s.date }}</span>
              <span class="sig-price mono">{{ s.price }}</span>
            </div>
          </div>

          <el-divider />
          <el-collapse>
            <el-collapse-item title="口径与出处说明" name="basis">
              <p class="basis">{{ data.basis.rules }}</p>
              <p class="basis">中枢口径:{{ data.basis.zhongshu_unit }}</p>
              <p class="basis">{{ data.basis.ambiguity }}</p>
              <p class="basis muted">原文语料:{{ data.basis.source }}</p>
            </el-collapse-item>
          </el-collapse>
        </template>
        <el-skeleton v-else :rows="6" animated />
      </el-card>
    </div>

    <el-card shadow="never" class="mt">
      <div class="side-head">
        <span class="side-title">宽基指数强弱对比</span>
        <span class="strength">
          第106课板块强弱指标(类别数均值):
          <b>日 {{ overview?.strength?.['1d'] ?? '—' }}</b> ·
          <b>周 {{ overview?.strength?.['1w'] ?? '—' }}</b> ·
          <b>月 {{ overview?.strength?.['1M'] ?? '—' }}</b>
        </span>
      </div>
      <el-table :data="tableRows" size="small" v-loading="overviewLoading"
                :default-sort="{ prop: 'c1d', order: 'descending' }">
        <el-table-column prop="name" label="指数" width="100" />
        <el-table-column v-for="iv in ['1d', '1w', '1M']" :key="iv"
                         :label="intervalLabel(iv)" :prop="'c' + iv" sortable width="112">
          <template #default="{ row }">
            <span class="cat-badge" :class="'c' + (row.levels[iv]?.ma_category || 0)">
              {{ row.levels[iv]?.ma_category ?? '—' }}
            </span>
            <span class="trend-mini">{{ row.levels[iv]?.trend || '—' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="日线末笔" width="90">
          <template #default="{ row }">
            <span :class="row.levels['1d']?.bi_direction === 'up' ? 'up' : 'down'">
              {{ row.levels['1d']?.bi_direction === 'up' ? '向上' : '向下' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="日线中枢" min-width="150">
          <template #default="{ row }">
            <span v-if="row.levels['1d']?.zhongshu" class="mono">
              [{{ row.levels['1d'].zhongshu.zd }}, {{ row.levels['1d'].zhongshu.zg }}]
            </span>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="日线中阴" width="90">
          <template #default="{ row }">
            <el-tag v-if="row.levels['1d']?.zhongyin === true" size="small" type="warning">中阴</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never" class="mt">
      <div class="side-head">
        <span class="side-title">系统交易模拟 · 缠论买卖点驱动</span>
        <span v-if="sim" class="strength">
          {{ sim.name }} · {{ intervalLabel(sim.interval) }} ·
          {{ sim.start_date }} ~ {{ sim.end_date }}({{ sim.trading_days }} 个交易日)·
          初始资金 {{ sim.capital.toLocaleString() }}
        </span>
      </div>

      <el-alert type="warning" :closable="false" show-icon class="mb"
                title="模拟严格逐根重算以消除未来函数,不含手续费与滑点,按份额连续计算;结果仅供研究,不构成投资建议。" />

      <el-skeleton v-if="simLoading" :rows="6" animated />
      <el-alert v-else-if="simError" type="error" :title="simError" show-icon :closable="false" />
      <template v-else-if="sim">
        <el-table :data="simRows" size="small" border>
          <el-table-column prop="name" label="策略" min-width="180">
            <template #default="{ row }">
              <span :class="{ 'muted': row.key === 'benchmark' }">{{ row.name }}</span>
              <el-tag v-if="row.open_position" size="small" type="warning" class="ml4">持仓中</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="总收益" width="100" align="right">
            <template #default="{ row }">
              <span :class="pctClass(row.total_return)">{{ pctText(row.total_return) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="年化" width="90" align="right">
            <template #default="{ row }">
              <span :class="pctClass(row.annual_return)">{{ pctText(row.annual_return) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="最大回撤" width="95" align="right">
            <template #default="{ row }">{{ pctText(-row.max_drawdown, false) }}</template>
          </el-table-column>
          <el-table-column label="夏普" width="70" align="right">
            <template #default="{ row }">{{ row.sharpe.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column label="平仓次数" width="85" align="right">
            <template #default="{ row }">{{ row.total_trades ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="胜率" width="75" align="right">
            <template #default="{ row }">
              {{ row.win_rate == null ? '—' : (row.win_rate * 100).toFixed(0) + '%' }}
            </template>
          </el-table-column>
          <el-table-column label="盈亏比" width="75" align="right">
            <template #default="{ row }">
              <!-- 只有盈利笔、没有亏损笔时盈亏比是未定义(metrics 会返回 0),不能显示成 0.00 -->
              {{ !row.avg_loss || row.profit_loss_ratio == null
                 ? '—' : row.profit_loss_ratio.toFixed(2) }}
            </template>
          </el-table-column>
          <el-table-column label="仓位暴露" width="85" align="right">
            <template #default="{ row }">{{ (row.exposure * 100).toFixed(0) }}%</template>
          </el-table-column>
        </el-table>

        <p class="basis mt8">
          仓位暴露 = 有持仓的交易日占比。策略大部分时间空仓时,单看总收益和「满仓拿到底」的
          基准比是不公平的 —— 请结合暴露度看年化。
        </p>

        <EChart :key="'sim-' + code + '-' + interval" :option="simChartOption" height="320px" />

        <div class="side-head mt">
          <span class="side-title sm">交易记录</span>
          <el-radio-group v-model="simPick" size="small">
            <el-radio-button v-for="s in sim.strategies" :key="s.key" :value="s.key">
              {{ s.name }}
            </el-radio-button>
          </el-radio-group>
        </div>

        <el-empty v-if="!simTrades.length" description="无成交记录" :image-size="48" />
        <el-table v-else :data="simTrades" size="small" border>
          <el-table-column prop="date" label="成交日" width="105" />
          <el-table-column label="方向" width="60">
            <template #default="{ row }">
              <span :class="row.direction === 'BUY' ? 'up' : 'down'">
                {{ row.direction === 'BUY' ? '买' : '卖' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="依据" width="120">
            <template #default="{ row }">
              <span class="tag-lesson">{{ row.label }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="price" label="成交价" width="90" align="right" class-name="mono" />
          <el-table-column label="仓位" width="70" align="right">
            <template #default="{ row }">
              {{ row.fraction == null ? '—' : (row.fraction * 100).toFixed(0) + '%' }}
            </template>
          </el-table-column>
          <el-table-column prop="signal_date" label="信号日" width="105" />
          <el-table-column label="本次盈亏" width="95" align="right">
            <template #default="{ row }">
              <span v-if="row.pnl_pct == null" class="muted">—</span>
              <span v-else :class="pctClass(row.pnl_pct)">{{ pctText(row.pnl_pct) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="信号与执行的间隔" min-width="150">
            <template #default="{ row }">
              <span class="basis">{{ lagText(row) }}</span>
            </template>
          </el-table-column>
        </el-table>

        <div v-if="simSkipped.length" class="basis mt">
          策略B 因「三卖后不回补」跳过的信号:
          <span v-for="(s, i) in simSkipped" :key="i" class="tag-lesson">
            {{ s.signal_date }} {{ s.label }}</span>
        </div>

        <el-collapse class="mt">
          <el-collapse-item title="模拟口径与规则出处" name="simrules">
            <p class="basis">信号:{{ sim.rules.signals }}</p>
            <p class="basis">成交:{{ sim.rules.execution }}</p>
            <p class="basis">策略A:{{ sim.rules.strategy_a }}</p>
            <p class="basis">策略B:{{ sim.rules.strategy_b }}</p>
            <p class="basis warn-text">实现口径说明:{{ sim.rules.reading_note }}</p>
            <p class="basis">成本:{{ sim.rules.cost }}</p>
            <p class="basis muted">绩效口径与全站一致(vnpy_quanti.metrics);出处课号 {{ sim.rules.lesson }}</p>
          </el-collapse-item>
        </el-collapse>
      </template>
      <el-empty v-else description="切换到某个标的与级别后自动计算" :image-size="60" />
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import EChart from '../components/EChart.vue'
import api from '../api'
import { usePersistentRef } from '../composables/usePersistentRef'

const UP = '#e8403a'
const DOWN = '#1ba27a'
// 笔是本页的主结构线,取最深的颜色并加粗;均线一律用浅色,避免和笔混淆
// (原先 MA233 用深石板色,和笔的深色几乎分不开)。
const BI_COLOR = '#0b1220'
const BI_DOT = '#0b1220'

const MA_KEYS = ['ma5', 'ma13', 'ma21', 'ma34', 'ma55', 'ma89', 'ma144', 'ma233']
const MA_COLORS = {
  ma5: '#f5a623', ma13: '#7e22ce', ma21: '#2c6fbb', ma34: '#0f766e',
  ma55: '#b45309', ma89: '#94a3b8', ma144: '#b8c4d4', ma233: '#d3dbe6'
}
const SIG_STYLE = {
  buy1: { color: '#b91c1c', symbol: 'triangle', side: 'bottom' },
  buy2: { color: '#ea580c', symbol: 'triangle', side: 'bottom' },
  buy3: { color: '#ca8a04', symbol: 'triangle', side: 'bottom' },
  sell1: { color: '#0f766e', symbol: 'triangle', side: 'top' },
  sell2: { color: '#0891b2', symbol: 'triangle', side: 'top' },
  sell3: { color: '#6366f1', symbol: 'triangle', side: 'top' }
}

const indices = ref([])
const watchlist = ref([])
const code = usePersistentRef('chan.code', '000001')
const interval = usePersistentRef('chan.interval', '1d')
const data = ref(null)
const overview = ref(null)
const loading = ref(false)
const overviewLoading = ref(false)
const error = ref('')

const show = reactive({
  bi: true, zhongshu: true, signals: true, divergence: true, ma: true
})
const chosenMas = usePersistentRef('chan.mas', ['ma5', 'ma13', 'ma34', 'ma89', 'ma233'])
const spanBars = usePersistentRef('chan.span', 250)

const intervalLabel = (iv) => ({ '1d': '日线', '1w': '周线', '1M': '月线' }[iv] || iv)
const statusText = (s) => ({ failed: '已跌破', holding: '已站住上沿', inside: '区间内', none: '—' }[s] || s)
const statusClass = (s) => ({ failed: 'warn', holding: 'ok', inside: '', none: 'muted' }[s] || '')

async function loadIndices() {
  try {
    const res = await api.marketIndices()
    indices.value = res.indices || []
    watchlist.value = res.watchlist || []
  } catch (e) {
    error.value = e.message
  }
}

async function loadStructure(refresh = false) {
  // 事件处理器可能把事件对象当参数传进来,这里只认真正的 true
  const force = refresh === true
  loading.value = true
  error.value = ''
  try {
    data.value = await api.marketChan(code.value, interval.value, force)
  } catch (e) {
    error.value = e.message
    data.value = null
  } finally {
    loading.value = false
  }
  // 模拟要逐根重算(首次约 2~4s),放在结构之后发起,让图先出来
  if (data.value) loadSim()
}

async function loadOverview() {
  overviewLoading.value = true
  try {
    overview.value = await api.marketOverview(false)
  } catch (e) {
    /* 概览失败不影响主图,主图会给出更具体的错误 */
  } finally {
    overviewLoading.value = false
  }
}

/**
 * 日期 → category 轴下标。ECharts 在 category 轴上按字符串匹配,匹配不上会**静默丢弃**
 * 该条 markLine/markArea,不报错。这里带吸附兜底,并把吸附条数报出来。
 */
function makeIndexLookup(dates, stats) {
  const key = (d) => String(d ?? '').slice(0, 10)
  const map = new Map()
  dates.forEach((d, i) => {
    const k = key(d)
    if (!map.has(k)) map.set(k, i)
  })
  const first = dates.length ? key(dates[0]) : ''
  const last = dates.length ? key(dates[dates.length - 1]) : ''
  return (date) => {
    const k = key(date)
    if (map.has(k)) return map.get(k)
    if (!dates.length) return null
    if (k < first || k > last) {
      stats.dropped += 1
      return null
    }
    let lo = 0
    let hi = dates.length - 1
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (key(dates[mid]) < k) lo = mid + 1
      else hi = mid
    }
    stats.snapped += 1
    return lo
  }
}

const build = computed(() => {
  const d = data.value
  if (!d || !d.dates?.length) return { option: null, note: '' }
  const stats = { snapped: 0, dropped: 0 }
  const idx = makeIndexLookup(d.dates, stats)
  const dates = d.dates

  // --- 笔:成对 coord 的 markLine,挂在 candlestick 上(与个股决策页的趋势射线同构) ---
  const biData = []
  const biVertices = []
  if (show.bi) {
    const seen = new Set()
    for (const b of d.bis || []) {
      const i0 = idx(b.start_date)
      const i1 = idx(b.end_date)
      if (i0 === null || i1 === null) continue
      biData.push([
        { coord: [i0, b.start_price], lineStyle: { color: BI_COLOR, width: 2, opacity: 0.9, type: 'solid' } },
        { coord: [i1, b.end_price] }
      ])
      if (!seen.has(i1)) {
        seen.add(i1)
        biVertices.push([i1, b.end_price])
      }
    }
  }

  // --- 未完成笔:从末笔端点到当前极值,虚线画,明确区别于已确认的笔 ---
  // 缠论只画端点分型已确认的笔,所以最新一段走势本来是断的;这段虚线让图上不断线,
  // 同时虚线本身就表示「待确认」。
  const pendingLine = []
  const pendingVertices = []
  if (show.bi && d.pending_bi) {
    const pb = d.pending_bi
    const i0 = idx(pb.start_date)
    const i1 = idx(pb.end_date)
    if (i0 !== null && i1 !== null && i1 > i0) {
      pendingVertices.push([i1, pb.end_price])
      pendingLine.push([
        { coord: [i0, pb.start_price],
          lineStyle: { color: '#64748b', width: 1.6, type: 'dashed', opacity: 0.95 } },
        { coord: [i1, pb.end_price],
          label: {
            show: true, formatter: '未完成笔', fontSize: 10, color: '#fff',
            backgroundColor: '#64748b', padding: [1, 4], borderRadius: 3,
            position: pb.direction === 'down' ? 'bottom' : 'top'
          } }
      ])
    }
  }

  // --- 背驰:在对应K线处画竖虚线并标注面积比值 ---
  const divData = []
  if (show.divergence) {
    for (const x of d.divergences || []) {
      const i = idx(x.date)
      if (i === null) continue
      // 用 xAxis 标记画整条竖虚线;成对 y 坐标只能画出很短一截。
      divData.push({
        xAxis: i,
        lineStyle: { color: x.kind === 'top' ? '#b91c1c' : '#0f766e',
                     width: 1, type: 'dashed', opacity: 0.85 },
        label: {
          show: true, formatter: `${x.kind === 'top' ? '顶' : '底'}背驰×${x.ratio}`
                  + (x.ratio_per_bar != null ? `(每K线×${x.ratio_per_bar})` : ''),
          position: 'insideEndTop', fontSize: 10,
          color: x.kind === 'top' ? '#b91c1c' : '#0f766e',
          backgroundColor: 'rgba(255,255,255,0.85)', padding: [1, 3], borderRadius: 3
        }
      })
    }
  }

  // --- 中枢:markArea 矩形 + gg/dd 细虚线 ---
  // markArea 用「成对 coord」的数组形式时,每项都要带上完整的 itemStyle:只写 itemStyle:{}
  // 会把系列级的样式整个覆盖掉,填充退回 ECharts 默认色,矩形重到盖住K线。
  const ZS_FILL = { color: 'rgba(44,111,187,0.08)', borderColor: 'rgba(44,111,187,0.55)', borderWidth: 1 }
  const zsArea = []
  const zsLines = []
  if (show.zhongshu) {
    for (const z of d.zhongshus || []) {
      const i0 = idx(z.start_date)
      const i1 = idx(z.end_date)
      if (i0 === null || i1 === null) continue
      const itemStyle = z.is_extended
        ? { ...ZS_FILL, color: 'rgba(44,111,187,0.05)', borderType: 'dashed' }
        : ZS_FILL
      zsArea.push([
        { coord: [i0, z.zd], itemStyle,
          label: (i1 - i0) >= 12
            ? { show: true, position: 'insideTop', fontSize: 10, color: '#2c6fbb',
                formatter: `${z.zd}~${z.zg}` }
            : { show: false } },
        { coord: [i1, z.zg], itemStyle }
      ])
      const thin = { color: 'rgba(44,111,187,0.4)', width: 0.8, type: 'dashed', opacity: 0.7 }
      zsLines.push([{ coord: [i0, z.gg], lineStyle: thin }, { coord: [i1, z.gg] }])
      zsLines.push([{ coord: [i0, z.dd], lineStyle: thin }, { coord: [i1, z.dd] }])
    }
  }

  // --- 均线 ---
  const maSeries = []
  if (show.ma) {
    for (const k of chosenMas.value) {
      const arr = d.mas?.[k]
      if (!arr) continue
      maSeries.push({
        name: k.toUpperCase(), type: 'line', data: arr, smooth: false, showSymbol: false,
        xAxisIndex: 0, yAxisIndex: 0, z: 6, connectNulls: false,
        lineStyle: { width: 1, color: MA_COLORS[k] || '#86909c' }
      })
    }
  }

  // --- 买卖点:买在下、卖在上,颜色按类别深浅表示信号强度 ---
  const sigSeries = []
  if (show.signals) {
    const groups = {}
    for (const s of d.signals || []) {
      const i = idx(s.date)
      if (i === null) continue
      ;(groups[s.kind] ||= []).push({ value: [i, s.price], meta: s })
    }
    for (const [kind, items] of Object.entries(groups)) {
      const st = SIG_STYLE[kind] || SIG_STYLE.buy3
      sigSeries.push({
        name: kind, type: 'scatter', xAxisIndex: 0, yAxisIndex: 0, z: 30,
        symbol: st.symbol, symbolRotate: st.side === 'top' ? 180 : 0,
        symbolSize: 11, data: items.map((it) => it.value),
        symbolOffset: st.side === 'bottom' ? [0, 18] : [0, -18],
        itemStyle: { color: st.color, borderColor: '#fff', borderWidth: 1.5 },
        label: {
          show: true, position: st.side === 'bottom' ? 'bottom' : 'top', distance: 3,
          formatter: (p) => items[p.dataIndex]?.meta?.label || '',
          fontSize: 10, fontWeight: 700, color: '#fff', backgroundColor: st.color,
          padding: [1, 4], borderRadius: 3
        }
      })
    }
  }

  const volData = (d.volume || []).map((v, i) => ({
    value: v,
    itemStyle: { color: (d.kline[i]?.[1] ?? 0) >= (d.kline[i]?.[0] ?? 0) ? UP : DOWN, opacity: 0.65 }
  }))

  const catAxis = (gi) => ({
    type: 'category', gridIndex: gi, data: dates, boundaryGap: true,
    axisLine: { lineStyle: { color: '#d9dde3' } },
    axisLabel: { show: gi === 2, color: '#86909c', fontSize: 10 },
    splitLine: { show: false }
  })
  const valAxis = (gi, extra = {}) => ({
    type: 'value', gridIndex: gi, scale: true,
    splitLine: { lineStyle: { color: '#eef1f4' } },
    axisLabel: { color: '#86909c', fontSize: 10 },
    ...extra
  })

  const zoomStart = spanBars.value > 0 && dates.length > spanBars.value
    ? Math.max(0, (1 - spanBars.value / dates.length) * 100)
    : 0

  const option = {
    animation: false,
    tooltip: {
      trigger: 'axis', axisPointer: { type: 'cross' },
      backgroundColor: '#fff', borderColor: '#e5e8ec', textStyle: { color: '#1f2329', fontSize: 12 }
    },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    legend: { top: 2, type: 'scroll', data: ['K线', ...maSeries.map((s) => s.name)] },
    grid: [
      { left: 74, right: 26, top: 34, height: '50%' },
      { left: 74, right: 26, top: '62%', height: '12%' },
      { left: 74, right: 26, top: '78%', height: '16%' }
    ],
    xAxis: [catAxis(0), catAxis(1), catAxis(2)],
    yAxis: [valAxis(0), valAxis(1, { scale: false }), valAxis(2)],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1, 2], start: zoomStart, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1, 2], bottom: 2, height: 14,
        start: zoomStart, end: 100 }
    ],
    series: [
      {
        name: 'K线', type: 'candlestick', data: d.kline, xAxisIndex: 0, yAxisIndex: 0, z: 2,
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
        markArea: {
          silent: true, z: 1,
          itemStyle: { color: 'rgba(44,111,187,0.08)', borderColor: 'rgba(44,111,187,0.55)',
                       borderWidth: 1 },
          data: zsArea
        },
        markLine: { silent: true, symbol: ['none', 'none'], animation: false,
                    data: [...biData, ...pendingLine, ...zsLines, ...divData] }
      },
      ...maSeries,
      ...(biVertices.length ? [{
        name: '笔顶点', type: 'scatter', data: biVertices, xAxisIndex: 0, yAxisIndex: 0, z: 20,
        symbol: 'circle', symbolSize: 5, tooltip: { show: false },
        itemStyle: { color: BI_DOT, borderColor: '#fff', borderWidth: 1 }
      }] : []),
      ...(pendingVertices.length ? [{
        name: '未完成笔端点', type: 'scatter', data: pendingVertices,
        xAxisIndex: 0, yAxisIndex: 0, z: 21,
        symbol: 'circle', symbolSize: 7, tooltip: { show: false },
        itemStyle: { color: '#fff', borderColor: '#64748b', borderWidth: 2 }
      }] : []),
      ...sigSeries,
      { name: '成交量', type: 'bar', data: volData, xAxisIndex: 1, yAxisIndex: 1,
        itemStyle: { opacity: 0.65 } },
      { name: 'MACD', type: 'bar', data: d.macd_hist, xAxisIndex: 2, yAxisIndex: 2,
        itemStyle: { color: (p) => (p.value >= 0 ? UP : DOWN) } },
      { name: 'DIF', type: 'line', data: d.dif, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2,
        lineStyle: { width: 1, color: '#f5a623' } },
      { name: 'DEA', type: 'line', data: d.dea, showSymbol: false, xAxisIndex: 2, yAxisIndex: 2,
        lineStyle: { width: 1, color: '#2c6fbb' } }
    ]
  }

  let note = ''
  if (stats.dropped > 0) {
    note = `有 ${stats.dropped} 个结构端点落在K线数据范围之外,已跳过未绘制。`
  } else if (stats.snapped > 0) {
    note = `有 ${stats.snapped} 个结构端点不在交易日上,已吸附到最近交易日。`
  }
  return { option, note }
})

const chartOption = computed(() => build.value.option)
const snapNote = computed(() => build.value.note)

const tableRows = computed(() => (overview.value?.indices || []).map((r) => ({
  ...r,
  c1d: r.levels['1d']?.ma_category ?? -1,
  c1w: r.levels['1w']?.ma_category ?? -1,
  c1M: r.levels['1M']?.ma_category ?? -1
})))

// ---------- 系统交易模拟 ----------
const sim = ref(null)
const simLoading = ref(false)
const simError = ref('')
const simPick = ref('full')

const pctText = (v, signed = true) =>
  (v == null ? '—' : (signed && v > 0 ? '+' : '') + (v * 100).toFixed(1) + '%')
const pctClass = (v) => (v == null ? 'muted' : v > 0 ? 'up' : v < 0 ? 'down' : '')
const lagText = (row) =>
  row.lag_bars == null ? '—' : `极值点后 ${row.lag_bars} 根K线才可成交`

const simRows = computed(() => {
  const s = sim.value
  if (!s) return []
  return [...s.strategies, s.benchmark].map((x) => ({ key: x.key, name: x.name, ...x.stats,
    exposure: x.exposure, open_position: x.open_position }))
})

const simPickStrategy = computed(() => {
  const s = sim.value
  if (!s) return null
  return s.strategies.find((x) => x.key === simPick.value) || s.strategies[0] || null
})
const simTrades = computed(() => (simPickStrategy.value?.trades || []).slice().reverse())
const simSkipped = computed(() => simPickStrategy.value?.skipped || [])

const simChartOption = computed(() => {
  const s = sim.value
  if (!s) return null
  const dates = s.benchmark.curve.map((p) => p[0])
  const series = [
    { name: '策略A · 满仓', data: s.strategies.find((x) => x.key === 'full'), color: '#dc2626', width: 2 },
    { name: '策略B · 第049课分仓', data: s.strategies.find((x) => x.key === 'rule049'), color: '#f59e0b', width: 2 },
    { name: '买入持有(基准)', data: s.benchmark, color: '#94a3b8', width: 1.4 }
  ].filter((x) => x.data)
  const cap = s.capital
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#fff', borderColor: '#e5e8ec', textStyle: { color: '#1f2329', fontSize: 12 },
      valueFormatter: (v) => (v == null ? '—' : v.toFixed(1) + '%')
    },
    legend: { top: 2, data: series.map((x) => x.name) },
    grid: { left: 62, right: 22, top: 34, bottom: 46 },
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      axisLine: { lineStyle: { color: '#d9dde3' } },
      axisLabel: { color: '#86909c', fontSize: 10 }
    },
    yAxis: {
      type: 'value', scale: true,
      splitLine: { lineStyle: { color: '#eef1f4' } },
      axisLabel: { color: '#86909c', fontSize: 10, formatter: (v) => v + '%' }
    },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', bottom: 4, height: 14, start: 0, end: 100 }
    ],
    series: series.map((x) => ({
      name: x.name, type: 'line', showSymbol: false, z: x.width > 1.5 ? 5 : 3,
      data: x.data.curve.map((p) => +((p[1] / cap - 1) * 100).toFixed(2)),
      lineStyle: { width: x.width, color: x.color },
      itemStyle: { color: x.color }
    }))
  }
})

async function loadSim() {
  simLoading.value = true
  simError.value = ''
  sim.value = null
  try {
    sim.value = await api.marketSimulate(code.value, interval.value)
    simPick.value = sim.value.strategies?.[0]?.key || 'full'
  } catch (e) {
    simError.value = e.message
  } finally {
    simLoading.value = false
  }
}

onMounted(async () => {
  await loadIndices()
  await loadStructure()
  loadOverview()
})
</script>

<style scoped>
.chan-page { padding: 16px; }
.toolbar-card :deep(.el-card__body) { padding: 12px 16px; }
.toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.layer-label { font-size: 12px; color: #86909c; }
.spacer { flex: 1; }
.as-of { font-size: 12px; color: #4b5563; }
.as-of b { margin-left: 4px; }
.up { color: #e8403a; }
.down { color: #1ba27a; }
.mono { font-variant-numeric: tabular-nums; font-family: ui-monospace, Menlo, Consolas, monospace; }
.muted { color: #9ca3af; }
.ml4 { margin-left: 4px; }
.mt8 { margin-top: 8px; }
.warn-text { color: #b45309; }
.tag-lesson {
  display: inline-block; font-size: 11px; color: #2c6fbb;
  border: 1px solid #cfe0f0; background: #f2f7fc;
  border-radius: 3px; padding: 0 5px; margin-right: 4px;
}
.mt { margin-top: 12px; }
.mb { margin-bottom: 8px; }
.layout { display: flex; gap: 12px; margin-top: 12px; align-items: flex-start; }
.chart-card { flex: 1; min-width: 0; }
.chart-wrap { width: 100%; }
.side-card { width: 400px; flex: 0 0 400px; max-height: 810px; overflow-y: auto; }
.side-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.side-title { font-weight: 700; font-size: 15px; color: #1f2329; }
.side-title.sm { font-size: 13px; margin-bottom: 6px; }
.strength { font-size: 12px; color: #4b5563; }
.verdict { display: flex; align-items: baseline; gap: 8px; margin: 8px 0 12px; }
.verdict-label { font-size: 12px; color: #86909c; }
.verdict-value { font-size: 18px; font-weight: 700; color: #2c6fbb; }
.bullet { display: flex; gap: 6px; margin-bottom: 8px; line-height: 1.6; }
.lesson-tag { flex: 0 0 auto; height: 18px; padding: 0 4px; font-size: 10px; }
.bullet-text { font-size: 12px; color: #374151; }
.kv-grid { display: flex; flex-direction: column; gap: 8px; }
.kv { display: flex; gap: 8px; align-items: baseline; font-size: 12px; }
.kv .k { flex: 0 0 74px; color: #86909c; }
.kv .v { color: #1f2329; }
.kv .v em { font-style: normal; font-size: 11px; color: #86909c; margin-left: 6px; }
.kv .v em.warn { color: #d97706; }
.kv .v em.ok { color: #0f766e; }
.cat { font-size: 16px; color: #2c6fbb; }
.sig-list { display: flex; flex-direction: column; gap: 4px; }
.sig-row { display: flex; gap: 8px; align-items: baseline; font-size: 12px; }
.sig-label { flex: 0 0 34px; font-weight: 700; }
.sig-label.buy { color: #b91c1c; }
.sig-label.sell { color: #0f766e; }
.sig-date { color: #6b7280; }
.sig-price { margin-left: auto; color: #1f2329; }
.basis { font-size: 12px; line-height: 1.7; color: #4b5563; margin: 0 0 6px; }
.cat-badge { display: inline-block; width: 20px; height: 20px; line-height: 20px; text-align: center;
  border-radius: 4px; font-size: 11px; font-weight: 700; color: #fff; background: #cbd5e1; margin-right: 6px; }
.cat-badge.c3 { background: #94a3b8; }
.cat-badge.c4 { background: #60a5fa; }
.cat-badge.c5 { background: #3b82f6; }
.cat-badge.c6 { background: #2563eb; }
.cat-badge.c7 { background: #1d4ed8; }
.cat-badge.c8 { background: #1e40af; }
.cat-badge.c9 { background: #b91c1c; }
.trend-mini { font-size: 11px; color: #6b7280; }
</style>

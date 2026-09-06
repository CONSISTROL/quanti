<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <span>组合决策 · 多股组合回测与策略研究</span>
      </template>

      <el-tabs v-model="mode">
        <el-tab-pane label="组合轮动回测" name="watchlist">
          <el-form label-width="100px" style="max-width: 760px">
            <el-form-item label="自选代码">
              <el-select
                v-model="form.watchlist"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="输入 6 位代码后回车"
                style="width: 100%"
              >
                <el-option
                  v-for="item in meta.watchlist || []"
                  :key="item"
                  :label="item"
                  :value="item"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="策略">
              <el-select v-model="form.strategy" style="width: 260px">
                <el-option
                  v-for="s in meta.strategies || []"
                  :key="s.name"
                  :label="`${s.label} (${s.name})`"
                  :value="s.name"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="开始日期">
              <el-date-picker v-model="form.start_date" type="date" value-format="YYYY-MM-DD" />
            </el-form-item>
            <el-form-item label="结束日期">
              <el-date-picker v-model="form.end_date" type="date" value-format="YYYY-MM-DD" placeholder="留空=最新交易日" clearable />
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <el-tab-pane label="策略研究测试" name="test">
          <el-form label-width="100px" style="max-width: 760px">
            <el-form-item label="测试模块">
              <el-select v-model="form.module" filterable style="width: 420px">
                <el-option
                  v-for="t in meta.tests || []"
                  :key="t.name"
                  :label="`${t.name} — ${t.description || ''}`"
                  :value="t.name"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="策略覆盖">
              <el-select v-model="form.strategy" clearable placeholder="继承 config" style="width: 260px">
                <el-option
                  v-for="s in meta.strategies || []"
                  :key="s.name"
                  :label="`${s.label} (${s.name})`"
                  :value="s.name"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="股票覆盖">
              <el-input v-model="form.stock" placeholder="部分测试使用，如 verify_signals" maxlength="6" style="width: 260px" />
            </el-form-item>
          </el-form>
        </el-tab-pane>
      </el-tabs>

      <el-alert
        v-if="busyError"
        :title="busyError"
        type="error"
        show-icon
        :closable="true"
        style="margin-bottom: 12px"
        @close="busyError = ''"
      />
      <el-button type="primary" :loading="starting" @click="startJob">
        <el-icon style="margin-right: 5px"><VideoPlay /></el-icon>
        启动任务
      </el-button>
    </el-card>

    <el-card shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; justify-content: space-between; align-items: center">
          <span>任务记录</span>
          <el-button size="small" @click="loadJobs">刷新</el-button>
        </div>
      </template>
      <el-table :data="jobs" size="small" highlight-current-row @current-change="onSelectJob" style="width: 100%">
        <el-table-column prop="id" label="ID" width="120" />
        <el-table-column prop="kind" label="类型" width="110">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ kindLabel(row.kind) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="参数" min-width="180">
          <template #default="{ row }">
            <span class="muted">{{ compactParams(row.params) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">{{ row.status }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="120">
          <template #default="{ row }">
            <el-button v-if="['queued','running','canceling'].includes(row.status)" size="small" type="danger" text @click="cancelJob(row.id)">取消</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card v-if="selectedJob" shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px">
          <span>任务 #{{ selectedJob.id }}</span>
          <el-tag :type="statusType(selectedJob.status)" size="small">{{ selectedJob.status }}</el-tag>
          <el-button v-if="selectedJob.has_result && selectedResult" size="small" type="primary" link @click="scrollToResult">查看结果</el-button>
        </div>
      </template>
      <div class="log-box">{{ logs.join('\n') || '暂无日志' }}</div>
    </el-card>

    <!-- Structured results -->
    <div v-if="selectedResult && structuredKind" ref="resultAnchor" style="margin-top: 16px">
      <el-card shadow="never">
        <template #header>
          <span>回测统计</span>
        </template>
        <el-row :gutter="12">
          <el-col v-for="(item, idx) in statItems" :key="idx" :xs="12" :sm="8" :md="4">
            <div class="mini-stat">
              <div class="muted">{{ item.label }}</div>
              <div class="mini-value" :class="{ up: item.value > 0, down: item.value < 0 }">{{ item.text }}</div>
            </div>
          </el-col>
        </el-row>
      </el-card>

      <el-card v-if="equityChartOption" shadow="never" style="margin-top: 16px">
        <template #header>
          <span>净值曲线（执行口径 / 信号口径）</span>
        </template>
        <EChart :option="equityChartOption" height="380px" />
      </el-card>

      <el-card v-if="selectedResult.result?.trades?.length" shadow="never" style="margin-top: 16px">
        <template #header>
          <span>交易明细（{{ selectedResult.result.trades.length }} 笔）</span>
        </template>
        <el-table :data="selectedResult.result.trades" size="small" max-height="480" style="width: 100%">
          <el-table-column prop="date" label="日期" width="110" />
          <el-table-column prop="side" label="方向" width="80">
            <template #default="{ row }">
              <span :class="row.side === '买入' ? 'up' : 'down'">{{ row.side }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="code" label="代码" width="90" />
          <el-table-column prop="name" label="名称" width="110" />
          <el-table-column prop="price" label="价格" width="90" align="right" />
          <el-table-column prop="shares" label="数量" width="90" align="right" />
          <el-table-column prop="amount" label="金额" width="110" align="right">
            <template #default="{ row }">{{ fmtMoney(row.amount) }}</template>
          </el-table-column>
          <el-table-column label="盈亏%" width="90" align="right">
            <template #default="{ row }">
              <span v-if="row.pnl_pct !== null && row.pnl_pct !== undefined" :class="row.pnl_pct >= 0 ? 'up' : 'down'">
                {{ (row.pnl_pct * 100).toFixed(2) }}%
              </span>
              <span v-else>-</span>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="原因" min-width="220" show-overflow-tooltip />
        </el-table>
      </el-card>

      <el-card v-if="selectedResult.mode === 'watchlist' && selectedResult.per_stock?.length" shadow="never" style="margin-top: 16px">
        <template #header>
          <span>个股独立满仓回测</span>
        </template>
        <el-table :data="selectedResult.per_stock" size="small" style="width: 100%">
          <el-table-column prop="code" label="代码" width="100" />
          <el-table-column prop="name" label="名称" width="140" />
          <el-table-column label="总收益" width="110" align="right">
            <template #default="{ row }">
              <span :class="row.summary.total_return >= 0 ? 'up' : 'down'">{{ (row.summary.total_return * 100).toFixed(2) }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="年化" width="110" align="right">
            <template #default="{ row }">{{ (row.summary.annual_return * 100).toFixed(2) }}%</template>
          </el-table-column>
          <el-table-column label="Sharpe" prop="summary.sharpe" width="90" align="right" />
          <el-table-column label="最大回撤" width="110" align="right">
            <template #default="{ row }">{{ (row.summary.max_drawdown * 100).toFixed(2) }}%</template>
          </el-table-column>
          <el-table-column label="交易" prop="summary.total_trades" width="80" align="right" />
          <el-table-column label="胜率" width="90" align="right">
            <template #default="{ row }">{{ (row.summary.win_rate * 100).toFixed(0) }}%</template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import { api } from '../api'
import EChart from '../components/EChart.vue'
import { usePersistentRef } from '../composables/usePersistentRef'

const route = useRoute()
const meta = ref({})
const jobs = ref([])
const selectedJob = ref(null)
const selectedResult = ref(null)
const logs = ref([])
const starting = ref(false)
const busyError = ref('')
const resultAnchor = ref(null)
let timer = null
let resultLoadedFor = ''

const mode = usePersistentRef('backtest.mode', 'watchlist')
const savedFormRaw = localStorage.getItem('quanti.ui.backtest.form')
let savedForm = null
if (savedFormRaw) {
  try {
    savedForm = JSON.parse(savedFormRaw)
  } catch (e) {
    savedForm = null
  }
}
const form = reactive({
  watchlist: [],
  stock: '601857',
  strategy: '',
  module: 'watchlist_backtest',
  start_date: '',
  end_date: ''
})
if (savedForm) Object.assign(form, savedForm)
watch(form, (v) => {
  localStorage.setItem('quanti.ui.backtest.form', JSON.stringify(v))
}, { deep: true })

onMounted(async () => {
  meta.value = await api.meta()
  const cfg = await api.config()
  if (!savedForm) {
    form.watchlist = [...(cfg.watchlist || [])].map(String)
    form.start_date = cfg.backtest?.start_date || ''
  }
  if (route.query.mode && ['watchlist', 'test'].includes(String(route.query.mode))) {
    mode.value = String(route.query.mode)
  } else {
    mode.value = 'watchlist'
  }
  if (mode.value === 'test' && route.query.module) form.module = String(route.query.module)
  if (cfg.test?.module && !form.module) form.module = cfg.test.module
  if (!form.strategy) {
    form.strategy = cfg.trading?.strategy || (cfg.test?.strategy) || 'reversal'
  }
  await loadJobs()
})

onBeforeUnmount(() => clearInterval(timer))

watch(selectedJob, async (job) => {
  if (!job) return
  selectedResult.value = null
  resultLoadedFor = ''
  logs.value = []
  clearInterval(timer)
  timer = setInterval(() => pollJob(job.id), 1200)
  await pollJob(job.id)
})

const structuredKind = computed(() => {
  const r = selectedResult.value
  if (!r) return false
  return r.mode === 'watchlist' || r.mode === 'stock'
})

const statItems = computed(() => {
  const stats = selectedResult.value?.result?.stats || {}
  const signal = selectedResult.value?.result?.signal_stats || {}
  const items = [
    { label: '初始资金', text: '¥' + fmtMoney(stats.initial_capital), value: 0 },
    { label: '期末资金', text: '¥' + fmtMoney(stats.final_value), value: 0 },
    { label: '总收益率', text: fmtPct(stats.total_return), value: stats.total_return },
    { label: '年化收益', text: fmtPct(stats.annual_return), value: stats.annual_return },
    { label: 'Sharpe', text: num(stats.sharpe, 2), value: stats.sharpe },
    { label: '最大回撤', text: fmtPct(stats.max_drawdown), value: -Math.abs(stats.max_drawdown || 0) },
    { label: '交易次数', text: stats.total_trades ?? 0, value: 0 },
    { label: '胜率', text: fmtPct(stats.win_rate), value: stats.win_rate }
  ]
  if (signal.total_return !== undefined) {
    items.push({ label: '信号总收益', text: fmtPct(signal.total_return), value: signal.total_return })
  }
  return items
})

const equityChartOption = computed(() => {
  const r = selectedResult.value?.result
  if (!r) return null
  const curves = [
    { key: 'equity_curve', name: '执行口径' },
    { key: 'signal_equity_curve', name: '信号口径' }
  ].filter(c => r[c.key]?.length)
  if (!curves.length) return null
  const dates = [...new Set(curves.flatMap(c => r[c.key].map(p => p.date)))].sort()
  const series = curves.map(c => ({
    name: c.name,
    type: 'line',
    showSymbol: false,
    smooth: true,
    data: dates.map(d => {
      const item = r[c.key].find(p => p.date === d)
      return item ? +(item.value / r.initial_capital).toFixed(6) : null
    })
  }))
  return {
    tooltip: { trigger: 'axis' },
    legend: { data: series.map(s => s.name) },
    grid: { left: 50, right: 20, top: 40, bottom: 60 },
    xAxis: { type: 'category', data: dates, boundaryGap: false },
    yAxis: { type: 'value', scale: true },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 16, bottom: 10 }],
    series
  }
})

async function loadJobs() {
  try {
    jobs.value = await api.jobs()
  } catch (e) {
    console.error(e)
  }
}

function kindLabel(kind) {
  return { watchlist: '自选池', stock: '个股', test: '测试' }[kind] || kind
}

function compactParams(params) {
  if (!params) return '-'
  const parts = []
  if (params.module) parts.push(params.module)
  if (params.stock) parts.push(params.stock)
  if (params.strategy) parts.push(params.strategy)
  return parts.join(' / ') || JSON.stringify(params)
}

function statusType(status) {
  return { success: 'success', error: 'danger', running: 'primary', queued: 'info', canceled: 'info', canceling: 'warning' }[status] || 'info'
}

async function startJob() {
  starting.value = true
  busyError.value = ''
  try {
    const payload = { kind: mode.value, ...form }
    if (mode.value !== 'test') delete payload.module
    if (mode.value !== 'watchlist') delete payload.watchlist
    if (!['stock', 'test'].includes(mode.value)) delete payload.stock
    if (mode.value === 'watchlist' && !payload.watchlist?.length) throw new Error('请至少选择一个自选代码')
    if (mode.value === 'stock' && !payload.stock) throw new Error('请输入股票代码')
    if (mode.value === 'test' && !payload.module) throw new Error('请选择测试模块')
    if (!payload.start_date) delete payload.start_date
    if (!payload.end_date) delete payload.end_date
    if (!payload.strategy) delete payload.strategy
    const job = await api.startJob(payload)
    await loadJobs()
    selectedJob.value = job
  } catch (e) {
    busyError.value = e.message
  } finally {
    starting.value = false
  }
}

async function onSelectJob(row) {
  if (row) selectedJob.value = row
}

async function pollJob(id) {
  try {
    const [job, logData] = await Promise.all([api.job(id), api.jobLogs(id, logs.value.length)])
    if (selectedJob.value) Object.assign(selectedJob.value, job)
    if (logData.logs?.length) logs.value.push(...logData.logs)
    if (job.status === 'success' && job.has_result && resultLoadedFor !== job.id) {
      const result = await api.jobResult(job.id)
      selectedResult.value = result
      resultLoadedFor = job.id
      if (structuredKind.value) {
        await nextTick()
        scrollToResult()
      }
    }
    if (['success', 'error', 'canceled'].includes(job.status)) {
      clearInterval(timer)
      await loadJobs()
    }
  } catch (e) {
    // Job may still be running while logs read.
    console.error(e)
  }
}

async function cancelJob(id) {
  try {
    await api.cancelJob(id)
    await loadJobs()
  } catch (e) {
    busyError.value = e.message
  }
}

function scrollToResult() {
  resultAnchor.value?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function num(v, d = 2) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return Number(v).toFixed(d)
}
function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return (v * 100).toFixed(2) + '%'
}
function fmtMoney(v) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
</script>

<style scoped>
.up { color: #e8403a; font-weight: 600; }
.down { color: #1ba27a; font-weight: 600; }
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
</style>

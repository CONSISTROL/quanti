<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <span>组合优化 · Cvxportfolio 均值-方差权重优化</span>
      </template>

      <div style="display: flex; flex-wrap: wrap; gap: 12px; align-items: center">
        <div style="width: 320px">
          <div class="muted" style="margin-bottom: 4px">优化标的（自选池）</div>
          <el-select v-model="watchlist" multiple filterable allow-create default-first-option style="width: 100%">
            <el-option
              v-for="c in watchlist"
              :key="c"
              :label="c"
              :value="c"
            />
          </el-select>
        </div>
        <div>
          <div class="muted" style="margin-bottom: 4px">风险厌恶 γ</div>
          <el-input-number v-model="gamma" :min="0.5" :max="20" :step="0.5" :precision="2" style="width: 120px" />
        </div>
        <div>
          <div class="muted" style="margin-bottom: 4px">回测期数</div>
          <el-input-number v-model="periods" :min="60" :max="1000" :step="30" style="width: 130px" />
        </div>
        <el-button type="primary" :loading="running" @click="run">
          <el-icon style="margin-right: 4px"><Cpu /></el-icon>
          运行优化
        </el-button>
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
    </el-card>

    <el-card v-if="running" shadow="never" style="margin-top: 16px">
      <template #header><span>组合优化进度</span></template>
      <el-progress :percentage="100" :indeterminate="true" :duration="2" :show-text="false" style="margin-bottom: 12px" />
      <div class="log-box">{{ progressLogs.join('\n') || '准备中...' }}</div>
    </el-card>

    <template v-if="result">
      <el-card shadow="never" style="margin-top: 16px">
        <template #header>
          <span>优化回测摘要 {{ result.start }} ~ {{ result.end }}</span>
        </template>
        <el-row :gutter="12">
          <el-col v-for="item in summaryItems" :key="item.label" :xs="12" :sm="8" :md="4">
            <div class="mini-stat">
              <div class="muted">{{ item.label }}</div>
              <div class="mini-value" :class="item.className || ''">{{ item.text }}</div>
            </div>
          </el-col>
        </el-row>
      </el-card>

      <el-row :gutter="16" style="margin-top: 16px">
        <el-col :xs="24" :md="12">
          <el-card shadow="never">
            <template #header><span>最新目标权重</span></template>
            <el-table :data="weightRows" size="small" style="width: 100%">
              <el-table-column prop="code" label="代码" width="100" />
              <el-table-column prop="name" label="名称" min-width="120" />
              <el-table-column label="权重" align="right">
                <template #default="{ row }">{{ fmtPct(row.weight) }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
        <el-col :xs="24" :md="12">
          <el-card shadow="never">
            <template #header><span>最新调仓（相对上一期）</span></template>
            <el-table :data="tradeRows" size="small" style="width: 100%">
              <el-table-column prop="code" label="代码" width="100" />
              <el-table-column prop="name" label="名称" min-width="120" />
              <el-table-column label="调整量（现金单位）" align="right">
                <template #default="{ row }">{{ fmtMoney(row.trade) }}</template>
              </el-table-column>
            </el-table>
          </el-card>
        </el-col>
      </el-row>

      <el-card v-if="tradeHistoryRows.length" shadow="never" style="margin-top: 16px">
        <template #header>
          <span>调仓历史 / 中间交易记录（{{ tradeHistoryRows.length }} 条）</span>
        </template>
        <el-table :data="tradeHistoryRows" size="small" max-height="480" style="width: 100%">
          <el-table-column prop="date" label="日期" width="110" />
          <el-table-column prop="code" label="代码" width="100" />
          <el-table-column prop="name" label="名称" min-width="120" />
          <el-table-column label="方向" width="80">
            <template #default="{ row }">
              <span :class="row.side === '买入' ? 'up' : 'down'">{{ row.side }}</span>
            </template>
          </el-table-column>
          <el-table-column label="金额（现金单位）" align="right">
            <template #default="{ row }">{{ fmtMoney(Math.abs(row.amount)) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'

const watchlist = ref([])
const gamma = ref(2.0)
const periods = ref(252)
const running = ref(false)
const result = ref(null)
const error = ref('')
const stockNames = ref({})
const progressLogs = ref([])
let progressTimer = null

function pushLog(msg) {
  progressLogs.value.push(msg)
}

function startFakeProgress() {
  progressLogs.value = ['拉取自选池日线行情...']
  progressTimer = setTimeout(() => pushLog('构建收益/成交量/价格矩阵...'), 1500)
  setTimeout(() => pushLog('运行 Cvxportfolio 优化与回测...'), 3500)
}

function stopProgress() {
  if (progressTimer) clearTimeout(progressTimer)
  progressTimer = null
}

onBeforeUnmount(stopProgress)

async function loadNames(codes) {
  const unique = [...new Set((codes || []).filter(Boolean).map(c => String(c).replace(/\D/g, '').padStart(6, '0')))]
  await Promise.all(unique.map(async (c) => {
    try {
      const res = await api.stockName(c)
      if (res?.name) stockNames.value[c] = res.name
    } catch (e) {
      // keep code only
    }
  }))
}

onMounted(async () => {
  try {
    const cfg = await api.config()
    watchlist.value = [...(cfg.watchlist || [])].map(String)
    loadNames(watchlist.value)
  } catch (e) {
    error.value = e.message
  }
})

const summaryItems = computed(() => {
  const s = result.value?.summary || {}
  return [
    { label: '总收益', text: fmtPct(s.total_return), className: (s.total_return || 0) >= 0 ? 'up' : 'down' },
    { label: '年化收益', text: fmtPct(s.annual_return), className: (s.annual_return || 0) >= 0 ? 'up' : 'down' },
    { label: '年化波动', text: fmtPct(s.volatility) },
    { label: 'Sharpe', text: num(s.sharpe, 2) },
    { label: '最大回撤', text: fmtPct(s.max_drawdown), className: 'down' },
    { label: '平均杠杆', text: num(s.avg_leverage, 2) }
  ]
})

const weightRows = computed(() => Object.entries(result.value?.latest_weights || {}).map(([code, weight]) => ({ code, name: stockNames.value[code] || code, weight })))
const tradeRows = computed(() => Object.entries(result.value?.latest_trades || {}).map(([code, trade]) => ({ code, name: stockNames.value[code] || code, trade })))
const tradeHistoryRows = computed(() => (result.value?.trade_history || []).map(r => ({ ...r, name: stockNames.value[r.code] || r.code })))

async function run() {
  if (!watchlist.value.length) {
    error.value = '请至少选择一个标的'
    return
  }
  running.value = true
  error.value = ''
  stopProgress()
  startFakeProgress()
  try {
    result.value = await api.portfolioOptimize({
      watchlist: watchlist.value,
      gamma: gamma.value,
      periods: periods.value
    })
    const codes = Object.keys(result.value?.latest_weights || {})
      .concat(Object.keys(result.value?.latest_trades || {}))
      .concat((result.value?.trade_history || []).map(r => r.code))
    loadNames(codes)
    pushLog('优化完成')
  } catch (e) {
    error.value = e.message
    result.value = null
    pushLog('优化失败: ' + e.message)
  } finally {
    stopProgress()
    running.value = false
  }
}

function num(v, digits = 2) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return Number(v).toFixed(digits)
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

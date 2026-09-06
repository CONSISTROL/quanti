<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <span>选股系统 · 全市场多因子选股</span>
      </template>

      <div class="toolbar">
        <span class="muted">扫描全市场并按综合因子评分排序，适合先选股再进入“个股决策”分析。</span>
        <div style="display: flex; gap: 10px; align-items: center; margin-top: 10px">
          <span>TOP N</span>
          <el-input-number v-model="topN" :min="10" :max="200" :step="10" />
          <el-button type="primary" :loading="starting" :disabled="busy" @click="startScan">
            <el-icon style="margin-right: 4px"><Search /></el-icon>
            {{ busy ? '任务运行中…' : '开始选股' }}
          </el-button>
        </div>
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

    <el-card v-if="currentJob" shadow="never" style="margin-top: 16px">
      <template #header>
        <div style="display: flex; align-items: center; gap: 12px">
          <span>任务 #{{ currentJob.id }}</span>
          <el-tag :type="statusType(currentJob.status)" size="small">{{ currentJob.status }}</el-tag>
          <span v-if="currentJob.status === 'success' && result" class="muted">
            已完成 · 有效池 {{ result.total }} · TOP {{ result.top }}
          </span>
        </div>
      </template>
      <div class="log-box">{{ logs.join('\n') || '暂无日志' }}</div>
    </el-card>

    <el-card v-if="result" shadow="never" style="margin-top: 16px">
      <template #header>
        <span>选股结果 TOP {{ result.top }} / 有效池 {{ result.total }}</span>
      </template>
      <el-table :data="result.records" size="small" max-height="640" style="width: 100%">
        <el-table-column label="排名" width="70" align="center">
          <template #default="{ row }">{{ row.rank ?? '-' }}</template>
        </el-table-column>
        <el-table-column prop="code" label="代码" width="100" />
        <el-table-column prop="name" label="名称" width="120" />
        <el-table-column prop="sector" label="行业" width="120" show-overflow-tooltip />
        <el-table-column prop="price" label="现价" width="90" align="right">
          <template #default="{ row }">{{ fmt(row.price, 2) }}</template>
        </el-table-column>
        <el-table-column label="综合" width="90" align="right">
          <template #default="{ row }">
            <span class="score">{{ fmt(row.composite_score, 2) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="价值" width="80" align="right">
          <template #default="{ row }">{{ fmt(row.value_score, 2) }}</template>
        </el-table-column>
        <el-table-column label="成长" width="80" align="right">
          <template #default="{ row }">{{ fmt(row.growth_score, 2) }}</template>
        </el-table-column>
        <el-table-column label="质量" width="80" align="right">
          <template #default="{ row }">{{ fmt(row.quality_score, 2) }}</template>
        </el-table-column>
        <el-table-column label="动量" width="80" align="right">
          <template #default="{ row }">{{ fmt(row.momentum_score, 2) }}</template>
        </el-table-column>
        <el-table-column label="风险" width="80" align="right">
          <template #default="{ row }">{{ fmt(row.risk_score, 2) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button size="small" type="primary" link @click="$router.push(`/kline?code=${row.code}`)">
              个股决策
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onActivated, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { usePersistentRef } from '../composables/usePersistentRef'

const topN = usePersistentRef('selection.topN', 30)
const starting = ref(false)
const busy = ref(false)
const currentJob = ref(null)
const logs = ref([])
const result = ref(null)
const error = ref('')
let timer = null
let resultLoaded = ''

onActivated(restoreLatest)
onBeforeUnmount(stopPolling)

function stopPolling() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

function startPolling(id) {
  stopPolling()
  timer = setInterval(() => poll(id), 1500)
}

async function restoreLatest() {
  try {
    const jobs = await api.jobs()
    // Mark any global running job so the start button is disabled.
    busy.value = jobs.some(j => ['queued', 'running', 'canceling'].includes(j.status))

    const selectionJobs = jobs.filter(j => j.kind === 'selection')
    if (!selectionJobs.length) return
    const latest = selectionJobs[0]
    currentJob.value = latest
    resultLoaded = ''
    result.value = null

    // Restore full log history from backend (not from component state).
    const logData = await api.jobLogs(latest.id, 0)
    logs.value = logData.logs || []

    if (latest.status === 'success' && latest.has_result) {
      const res = await api.jobResult(latest.id)
      result.value = res
      resultLoaded = latest.id
    } else if (['queued', 'running', 'canceling'].includes(latest.status)) {
      startPolling(latest.id)
      await poll(latest.id)
    }
  } catch (e) {
    error.value = e.message
  }
}

async function startScan() {
  if (busy.value) {
    error.value = '当前已有任务在运行，请等待完成后再启动新的选股任务。'
    return
  }
  starting.value = true
  error.value = ''
  stopPolling()
  logs.value = []
  result.value = null
  resultLoaded = ''
  currentJob.value = null
  try {
    const job = await api.startJob({ kind: 'selection', top: topN.value })
    currentJob.value = job
    busy.value = true
    startPolling(job.id)
    await poll(job.id)
  } catch (e) {
    error.value = e.message
  } finally {
    starting.value = false
  }
}

async function poll(id) {
  try {
    const [job, logData] = await Promise.all([
      api.job(id),
      api.jobLogs(id, logs.value.length)
    ])
    if (currentJob.value && currentJob.value.id === id) Object.assign(currentJob.value, job)
    if (logData.logs?.length) logs.value.push(...logData.logs)

    if (job.status === 'success' && job.has_result && resultLoaded !== job.id) {
      const res = await api.jobResult(job.id)
      result.value = res
      resultLoaded = job.id
      busy.value = false
      stopPolling()
    }
    if (['error', 'canceled'].includes(job.status)) {
      busy.value = false
      stopPolling()
    }
  } catch (e) {
    console.error(e)
  }
}

function statusType(status) {
  return { success: 'success', error: 'danger', running: 'primary', queued: 'info', canceled: 'info' }[status] || 'info'
}

function fmt(v, digits = 2) {
  if (v === null || v === undefined || isNaN(v)) return '-'
  return Number(v).toFixed(digits)
}
</script>

<style scoped>
.toolbar {
  font-size: 13px;
}
.log-box {
  background: #0f172a;
  color: #d1e7ff;
  border-radius: 10px;
  padding: 12px 14px;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 320px;
  overflow: auto;
}
.score {
  font-weight: 700;
  color: #1d4ed8;
}
</style>

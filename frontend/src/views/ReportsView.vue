<template>
  <div class="page">
    <el-row :gutter="16" style="height: calc(100vh - 40px)">
      <el-col :span="8" style="height: 100%">
        <el-card shadow="never" class="fill-card">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center">
              <span>历史报告 ({{ reports.length }})</span>
              <el-button size="small" @click="loadReports">刷新</el-button>
            </div>
          </template>
          <el-table :data="reports" size="small" height="100%" highlight-current-row @current-change="row => (selected = row)">
            <el-table-column prop="name" label="文件" min-width="220" show-overflow-tooltip />
            <el-table-column prop="mtime" label="修改时间" width="160" />
            <el-table-column label="大小" width="90" align="right">
              <template #default="{ row }">{{ fmtSize(row.size) }}</template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
      <el-col :span="16" style="height: 100%">
        <el-card shadow="never" class="fill-card">
          <template #header>
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 12px">
              <span>{{ selected?.name || '未选择报告' }}</span>
              <div>
                <el-button size="small" :disabled="!selected" type="danger" plain @click="removeReport">删除</el-button>
                <el-button size="small" :disabled="!selected" type="primary" @click="openInNewTab">新窗口打开</el-button>
              </div>
            </div>
          </template>
          <div v-if="selected" class="preview-content">
            <iframe :src="`/api/reports/${encodeURIComponent(selected.name)}`" style="width:100%; height:100%; border:0; border-radius: 6px; background: #fff" />
          </div>
          <el-empty v-else description="从左侧选择一份报告预览" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onActivated } from 'vue'
import { api } from '../api'

const reports = ref([])
const selected = ref(null)

onActivated(loadReports)

async function loadReports() {
  try {
    const list = await api.reports()
    // Sort by modification time descending (newest first); same timestamp -> filename asc.
    list.sort((a, b) => (b.mtime || '').localeCompare(a.mtime || '') || a.name.localeCompare(b.name))
    reports.value = list
    if (!selected.value && reports.value.length) selected.value = reports.value[0]
  } catch (e) {
    console.error(e)
  }
}

function fmtSize(bytes) {
  if (!bytes) return '0 B'
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / 1024 / 1024).toFixed(1) + ' MB'
}

async function removeReport() {
  if (!selected.value) return
  try {
    await api.deleteReport(selected.value.name)
    await loadReports()
  } catch (e) {
    console.error(e)
  }
}

function openInNewTab() {
  if (selected.value) window.open(`/api/reports/${encodeURIComponent(selected.value.name)}`, '_blank')
}
</script>

<style scoped>
.fill-card {
  height: 100%;
  display: flex;
  flex-direction: column;
}

.fill-card :deep(.el-card__body) {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  padding: 12px;
}

.fill-card :deep(.el-table) {
  flex: 1;
  min-height: 0;
}

.fill-card .preview-content {
  flex: 1;
  min-height: 0;
}
</style>

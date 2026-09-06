<template>
  <div class="page">
    <el-row :gutter="16">
      <el-col :xs="24" :sm="12" :md="6">
        <div class="stat-card">
          <div>
            <div class="muted">当前策略</div>
            <div class="value">{{ meta.strategy || '-' }}</div>
          </div>
          <el-icon :size="30" color="#3b82f6"><TrendCharts /></el-icon>
        </div>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <div class="stat-card">
          <div>
            <div class="muted">数据源</div>
            <div class="value">{{ meta.source || '-' }}</div>
          </div>
          <el-icon :size="30" color="#10b981"><DataLine /></el-icon>
        </div>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <div class="stat-card">
          <div>
            <div class="muted">自选标的</div>
            <div class="value">{{ (meta.watchlist || []).length }} 只</div>
          </div>
          <el-icon :size="30" color="#f59e0b"><Collection /></el-icon>
        </div>
      </el-col>
      <el-col :xs="24" :sm="12" :md="6">
        <div class="stat-card">
          <div>
            <div class="muted">历史报告</div>
            <div class="value">{{ meta.report_count }} 份</div>
          </div>
          <el-icon :size="30" color="#8b5cf6"><Document /></el-icon>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16" style="margin-top: 16px">
      <el-col :xs="24" :md="14">
        <el-card shadow="never">
          <template #header>
            <div class="card-header">
              <span>快速启动</span>
              <span class="muted">点击后在回测中心查看实时日志</span>
            </div>
          </template>
          <div class="quick-grid">
            <el-button type="primary" size="large" @click="$router.push('/backtest?mode=watchlist')">
              <el-icon style="margin-right: 6px"><DataAnalysis /></el-icon>
              组合回测
            </el-button>
            <el-button type="success" size="large" @click="$router.push('/kline')">
              <el-icon style="margin-right: 6px"><TrendCharts /></el-icon>
              个股决策
            </el-button>
            <el-button size="large" @click="$router.push('/backtest?mode=test')">
              <el-icon style="margin-right: 6px"><Cpu /></el-icon>
              策略研究测试
            </el-button>
            <el-button size="large" @click="$router.push('/reports')">
              <el-icon style="margin-right: 6px"><FolderOpened /></el-icon>
              查看报告
            </el-button>
          </div>
        </el-card>
      </el-col>
      <el-col :xs="24" :md="10">
        <el-card shadow="never">
          <template #header>
            <span>运行说明</span>
          </template>
          <ul class="tips">
            <li>任务运行在 FastAPI 后端进程内，一次只允许一个任务。</li>
            <li>自选池/个股回测完成后会显示结构化图表与交易明细。</li>
            <li>测试模块通常只输出文本日志，完成后可在“报告”页查看生成的 HTML。</li>
            <li>配置修改保存在 <code>config.json</code>，与 CLI 共用。</li>
          </ul>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { api } from '../api'

const meta = ref({})
onMounted(async () => {
  meta.value = await api.meta()
})
</script>

<style scoped>
.value {
  font-size: 20px;
  font-weight: 700;
  margin-top: 4px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.quick-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}
.quick-grid .el-button {
  margin: 0;
}
.tips {
  padding-left: 18px;
  line-height: 2;
  color: #4e5969;
  font-size: 13px;
}
code {
  background: #f1f5f9;
  padding: 1px 5px;
  border-radius: 4px;
}
</style>

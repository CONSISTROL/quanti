<template>
  <div class="page">
    <el-card shadow="never">
      <template #header>
        <div class="config-header">
          <span>系统配置</span>
          <div class="config-actions">
            <el-button size="small" @click="loadAll">重新加载</el-button>
            <el-button size="small" type="primary" :loading="saving" @click="save">保存配置</el-button>
          </div>
        </div>
      </template>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="true"
        style="margin-bottom: 12px"
        @close="error = ''"
      />

      <el-tabs v-model="activeTab" tab-position="left" class="config-tabs">
        <!-- 交易与风控 -->
        <el-tab-pane label="交易与风控" name="trading">
          <el-form label-width="170px" class="config-form">
            <el-divider content-position="left">交易策略</el-divider>
            <el-row :gutter="16">
              <el-col :xs="24" :md="12">
                <el-form-item label="策略">
                  <el-select v-model="config.trading.strategy" style="width: 100%">
                    <el-option
                      v-for="s in meta.strategies || []"
                      :key="s.name"
                      :label="`${s.label} (${s.name})`"
                      :value="s.name"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :md="12">
                <el-form-item label="买入最低评分">
                  <el-input-number v-model="config.trading.min_buy_score" :min="0" :max="10" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>

            <el-divider content-position="left">资金与仓位</el-divider>
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="初始资金">
                  <el-input-number v-model="config.trading.initial_capital" :min="1000" :step="10000" :controls="false" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="最大持仓数">
                  <el-input-number v-model="config.trading.max_positions" :min="1" :max="20" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="单只仓位">
                  <el-input-number v-model="config.trading.position_pct" :min="0" :max="1" :step="0.05" :precision="2" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="凯利公式">
                  <el-switch v-model="config.trading.kelly_mode" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="强制满仓">
                  <el-switch v-model="config.trading.full_position" />
                </el-form-item>
              </el-col>
            </el-row>

            <el-divider content-position="left">止损止盈</el-divider>
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="止损线">
                  <el-input-number v-model="config.trading.stop_loss" :min="-0.5" :max="0" :step="0.01" :precision="2" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="止盈线">
                  <el-input-number v-model="config.trading.take_profit" :min="0" :max="1" :step="0.01" :precision="2" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="最大持有天数">
                  <el-input-number v-model="config.trading.max_holding_days" :min="0" :max="999" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>

            <el-divider content-position="left">成交模式</el-divider>
            <el-row :gutter="16">
              <el-col :xs="24" :sm="8">
                <el-form-item label="次日尾盘成交">
                  <el-switch v-model="config.trading.exec_next_close" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="8">
                <el-form-item label="次日开盘成交">
                  <el-switch v-model="config.trading.exec_next_open" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="8">
                <el-form-item label="买入也按次日开盘">
                  <el-switch v-model="config.trading.buy_next_open" />
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>
        </el-tab-pane>

        <!-- 自选与组合 -->
        <el-tab-pane label="自选与组合" name="portfolio">
          <el-form label-width="170px" class="config-form">
            <el-divider content-position="left">自选池</el-divider>
            <el-form-item label="自选标的">
              <el-select
                v-model="config.watchlist"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="输入 6 位代码后回车"
                style="width: 100%"
              >
                <el-option
                  v-for="code in config.watchlist || []"
                  :key="code"
                  :label="watchLabel(code)"
                  :value="code"
                />
              </el-select>
            </el-form-item>
            <el-alert type="info" :closable="false" show-icon title="自选池用于“组合决策”页的组合轮动回测，以及数据源拉取。"/>
          </el-form>
        </el-tab-pane>

        <!-- 回测与选股 -->
        <el-tab-pane label="回测与选股" name="backtest">
          <el-form label-width="170px" class="config-form">
            <el-divider content-position="left">回测区间</el-divider>
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="开始日期">
                  <el-date-picker v-model="config.backtest.start_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="结束日期">
                  <el-date-picker
                    v-model="config.backtest.end_date"
                    type="date"
                    value-format="YYYY-MM-DD"
                    placeholder="留空=最新交易日"
                    clearable
                    style="width: 100%"
                  />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="回测策略">
                  <el-select v-model="config.backtest.strategy" clearable style="width: 100%">
                    <el-option
                      v-for="s in meta.backtest_strategies || []"
                      :key="s.name"
                      :label="`${s.label} (${s.name})`"
                      :value="s.name"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="策略对比">
                  <el-switch v-model="config.backtest.compare_strategies" />
                </el-form-item>
              </el-col>
            </el-row>

            <el-divider content-position="left">选股打分</el-divider>
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="12">
                <el-form-item label="因子权重">
                  <el-input v-model="config.scoring.weights" placeholder="如 0.25,0.20,0.25,0.20,0.10" style="width: 100%" />
                  <div class="form-tip">顺序：价值,成长,质量,动量,风险；逗号分隔，自动归一化</div>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="6">
                <el-form-item label="展示/回测 TOP">
                  <el-input-number v-model="config.scoring.top" :min="1" :max="1000" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>
        </el-tab-pane>

        <!-- 数据源 -->
        <el-tab-pane label="数据源" name="data">
          <el-form label-width="170px" class="config-form">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="数据源">
                  <el-select v-model="config.data.source" style="width: 100%">
                    <el-option
                      v-for="ds in meta.dataSources || []"
                      :key="ds.name"
                      :label="ds.label"
                      :value="ds.name"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="历史K线天数">
                  <el-input-number v-model="config.data.hist_days" :min="100" :max="4000" :step="100" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="下载并发数">
                  <el-input-number v-model="config.data.workers" :min="1" :max="32" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="请求间隔(秒)">
                  <el-input-number v-model="config.data.sleep" :min="0" :max="5" :step="0.05" :precision="2" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="8">
                <el-form-item label="QuantDash Key">
                  <el-input v-model="config.data.quantdash_key" show-password placeholder="sk_..." style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-divider content-position="left">数据范围</el-divider>
            <el-form-item label="范围过滤">
              <el-checkbox v-model="config.data.include_etf_lof">纳入 ETF/LOF</el-checkbox>
              <el-checkbox v-model="config.data.exclude_gem" style="margin-left: 16px">排除创业板</el-checkbox>
              <el-checkbox v-model="config.data.exclude_star" style="margin-left: 16px">排除科创板</el-checkbox>
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- 测试/研究 -->
        <el-tab-pane label="测试/研究" name="test">
          <el-form label-width="170px" class="config-form">
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="12">
                <el-form-item label="默认测试模块">
                  <el-select v-model="config.test.module" filterable style="width: 100%">
                    <el-option
                      v-for="t in meta.tests || []"
                      :key="t.name"
                      :label="`${t.name} — ${t.description || ''}`"
                      :value="t.name"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="6">
                <el-form-item label="测试股票">
                  <el-input v-model="config.test.stock" maxlength="6" placeholder="601857" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="6">
                <el-form-item label="测试策略">
                  <el-select v-model="config.test.strategy" clearable style="width: 100%">
                    <el-option
                      v-for="s in meta.strategies || []"
                      :key="s.name"
                      :label="s.label"
                      :value="s.name"
                    />
                  </el-select>
                </el-form-item>
              </el-col>
            </el-row>
          </el-form>
        </el-tab-pane>

        <!-- AI/推送 -->
        <el-tab-pane label="AI/推送" name="ai">
          <el-form label-width="170px" class="config-form">
            <el-divider content-position="left">AI 分析</el-divider>
            <el-row :gutter="16">
              <el-col :xs="24" :sm="12" :md="12">
                <el-form-item label="AI API Key">
                  <el-input v-model="config.ai.api_key" show-password placeholder="sk-..." style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="12">
                <el-form-item label="Base URL">
                  <el-input v-model="config.ai.base_url" style="width: 100%" />
                </el-form-item>
              </el-col>
              <el-col :xs="24" :sm="12" :md="12">
                <el-form-item label="模型">
                  <el-input v-model="config.ai.model" style="width: 100%" />
                </el-form-item>
              </el-col>
            </el-row>
            <el-divider content-position="left">飞书推送</el-divider>
            <el-form-item label="飞书 Webhook">
              <el-input
                v-model="config.alert.feishu_webhook"
                show-password
                placeholder="https://open.feishu.cn/..."
                style="width: 100%"
              />
            </el-form-item>
          </el-form>
        </el-tab-pane>

        <!-- JSON 高级 -->
        <el-tab-pane label="JSON高级" name="json">
          <div class="json-mode">
            <el-alert
              title="JSON 模式用于保留未知字段 / 高级参数；普通修改请在对应分类页完成。"
              type="info"
              show-icon
              :closable="false"
              style="margin-bottom: 12px"
            />
            <el-input
              v-model="jsonText"
              type="textarea"
              :rows="26"
              spellcheck="false"
              class="json-editor"
              placeholder="{ ... }"
            />
            <div style="margin-top: 12px" class="form-actions">
              <el-button type="primary" :loading="saving" @click="save">保存 JSON</el-button>
              <el-button @click="syncJsonFromConfig">放弃修改</el-button>
            </div>
          </div>
        </el-tab-pane>
      </el-tabs>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import { api } from '../api'
import { usePersistentRef } from '../composables/usePersistentRef'

const activeTab = usePersistentRef('config.tab', 'trading')
const config = ref({})
const jsonText = ref('')
const meta = ref({})
const saving = ref(false)
const error = ref('')

const WATCH_NAMES = {
  '601857': '中国石油',
  '159381': '创业板AI ETF',
  '588170': '科创半导体ETF',
  '600547': '山东黄金',
  '513580': '恒生科技ETF',
  '159941': '纳指ETF广发',
  '160723': '嘉实原油LOF'
}

function watchLabel(code) {
  const key = String(code).padStart(6, '0')
  return WATCH_NAMES[key] ? `${key} ${WATCH_NAMES[key]}` : String(code)
}

function defaults() {
  return {
    ai: { api_key: '', base_url: '', model: '' },
    trading: {
      initial_capital: 150000,
      max_positions: 4,
      position_pct: 0.25,
      kelly_mode: false,
      full_position: false,
      stop_loss: -0.03,
      take_profit: 0.08,
      max_holding_days: 0,
      min_buy_score: 4,
      strategy: 'reversal',
      exec_next_close: true,
      exec_next_open: false,
      buy_next_open: false
    },
    backtest: { start_date: '2025-01-01', end_date: '', strategy: 'composite', compare_strategies: true },
    scoring: { weights: '0.25,0.20,0.25,0.20,0.10', top: 30 },
    data: {
      hist_days: 4000,
      workers: 8,
      sleep: 0.15,
      include_etf_lof: false,
      exclude_gem: true,
      exclude_star: true,
      source: 'quantdash',
      quantdash_key: ''
    },
    test: { module: 'watchlist_backtest', stock: '601857', strategy: 'watchlist' },
    alert: { feishu_webhook: '' },
    watchlist: []
  }
}

function mergeDefaults() {
  const d = defaults()
  const raw = JSON.parse(JSON.stringify(config.value || {}))
  config.value = {
    ...d,
    ...raw,
    ai: { ...d.ai, ...(raw.ai || {}) },
    trading: { ...d.trading, ...(raw.trading || {}) },
    backtest: { ...d.backtest, ...(raw.backtest || {}) },
    scoring: { ...d.scoring, ...(raw.scoring || {}) },
    data: { ...d.data, ...(raw.data || {}) },
    test: { ...d.test, ...(raw.test || {}) },
    alert: { ...d.alert, ...(raw.alert || {}) },
    watchlist: Array.isArray(raw.watchlist) ? raw.watchlist.map(String) : []
  }
}

function syncJsonFromConfig() {
  jsonText.value = JSON.stringify(config.value, null, 4)
}

async function loadAll() {
  try {
    const [cfg, m] = await Promise.all([api.config(), api.meta()])
    config.value = cfg
    mergeDefaults()
    syncJsonFromConfig()
    meta.value = m
    error.value = ''
  } catch (e) {
    error.value = e.message
  }
}

function parseJson() {
  try {
    const obj = JSON.parse(jsonText.value)
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('必须是 JSON 对象')
    return obj
  } catch (e) {
    throw new Error(`JSON 格式错误: ${e.message}`)
  }
}

async function save() {
  try {
    saving.value = true
    let payload = config.value
    if (activeTab.value === 'json') {
      payload = parseJson()
    }
    await api.saveConfig(payload)
    config.value = payload
    mergeDefaults()
    syncJsonFromConfig()
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

watch(activeTab, (tab) => {
  if (tab === 'json') syncJsonFromConfig()
})

onMounted(loadAll)
</script>

<style scoped>
.config-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.config-actions {
  display: flex;
  gap: 8px;
}
.config-tabs {
  min-height: 560px;
}
.config-tabs :deep(.el-tabs__header) {
  margin-right: 12px;
}
.config-form {
  max-width: 1100px;
}
.form-tip {
  font-size: 12px;
  color: #86909c;
  line-height: 1.6;
}
.form-actions {
  margin-top: 12px;
}
.json-mode {
  max-width: 1100px;
}
.json-editor :deep(textarea) {
  font-family: 'JetBrains Mono', Consolas, 'Courier New', monospace;
  font-size: 12px;
  line-height: 1.6;
  background: #0f172a;
  color: #d1e7ff;
}
</style>

# Quanti 架构收敛与去重构方案

> 目标：在不破坏现有 CLI / 回测结果的前提下，合并重复入口、统一策略/数据/报告模型，让 Web 控制台与命令行共用同一套核心服务。

---

## 1. 现状问题

### 1.1 两套策略表
- `strategies/`：波段买卖策略（`reversal/momentum/bollinger/watchlist/gap_open`）
- `backtest.py::STRATEGIES`：Walk-forward 因子策略（`momentum/reversion/trend/composite`）

同一名称如 `momentum` 在两套表里含义不同，Web 配置里也因此出现“交易策略”和“回测策略”两个独立下拉。

### 1.2 多套回测引擎/入口
- `trading_engine.py`：波段持仓回测
- `backtest.py`：Walk-forward 定期再平衡回测
- `optimizer.py` + `tests/grid_search_*`：参数/策略搜索
- `tests/*`：散落的批量回测、验证脚本

### 1.3 报告输出重复
- `report_generator.py`、`report_echarts.py`
- Web 端 ECharts 结构化渲染
- 根目录大量 `report_*.html`

同一回测结果可能被生成多种 HTML，且 HTML 难以维护。

### 1.4 数据源入口重叠
- `data_fetcher.py`：AkShare 全市场数据
- `data_sources/`：quantdash / sina / tencent 自选池数据
- 二者存在函数级复用，但对外接口不统一。

---

## 2. 目标架构

```
┌─────────────────────────────────────────────┐
│  Vue3 Web Console                           │
│  Dashboard / Backtest / Reports / Config    │
└──────────────┬──────────────────────────────┘
               │ JSON API
┌──────────────▼──────────────────────────────┐
│  backend/ (FastAPI)                         │
│  - jobs: 后台任务统一调度                    │
│  - api: 统一 REST 接口                      │
└──────────────┬──────────────────────────────┘
               │ 调用统一 service
┌──────────────▼──────────────────────────────┐
│  core/ (新增共享核心层)                      │
│  - registry: 统一策略注册表                 │
│  - results: 统一回测结果模型/序列化         │
│  - reporting: JSON 结果 -> ECharts option   │
│  - data: 统一数据源 facade                  │
└───────┬──────────────┬──────────────┬───────┘
        │              │              │
┌───────▼─────┐ ┌──────▼──────┐ ┌─────▼──────────┐
│ strategies/ │ │ backtest.py │ │ data_sources/  │
│ 波段策略     │ │ Walk-forward │ │ 数据源实现      │
└─────────────┘ └─────────────┘ └────────────────┘
```

原则：
- **不合并交易引擎本身**：波段持仓回测与 Walk-forward 再平衡是两种不同模型。
- 合并的是**元数据、入口、数据格式、报告管线**。
- 保留旧模块作为兼容壳，逐步迁移调用方。

---

## 3. 建议合并项

### 3.1 统一策略注册表

新增 `core/registry.py`，把两套策略注册到同一张表：

```python
STRATEGY_REGISTRY = {
    # swing 波段买卖策略
    "reversal": {
        "category": "swing",
        "label": "弱转强趋势",
        "module": "strategies.reversal",
        "class": "ReversalStrategy",
    },
    # factor Walk-forward 策略
    "composite": {
        "category": "factor",
        "label": "复合策略",
        "weight": {...},
        "desc": "动量+趋势+低波动+技术面加权综合",
    },
}
```

保留兼容接口：

```python
# strategies/__init__.py 继续提供旧接口
def get_strategy(name): ...
def strategy_label(name): ...

# backtest.py 的 STRATEGIES 改为从 registry 读取 factor 类
```

API 输出：

```json
{
  "strategies": [
    {"name": "watchlist", "category": "swing", ...},
    {"name": "composite", "category": "factor", ...}
  ]
}
```

Web 配置页：
- “交易策略”只展示 `category=swing`
- “回测策略”只展示 `category=factor`
- 避免再次出现 composite 找不到的问题。

### 3.2 统一回测结果模型

新增 `core/results.py`，定义通用回测输出：

```python
{
    "mode": "watchlist" | "stock" | "factor" | "test",
    "params": {...},
    "stats": {...},
    "signal_stats": {...},       # swing 口径
    "equity_curves": [...],
    "trades": [...],
    "positions": [...],
    "per_stock": [...],          # 组合/批量回测
    "logs": [...],
    "artifacts": [...],          # 可下载 HTML/CSV
}
```

`backend/serializers.py` 收敛到 `core/results.py`。
`trading_engine` / `backtest.py` 返回原始对象，由 core 层统一序列化。

### 3.3 统一报告管线

新增 `core/reporting.py`：

```python
def build_echarts_options(result) -> dict: ...
def export_html(result, path) -> Path: ...
```

- Web 交互路径：后端返回 JSON -> Vue + ECharts。
- 可选导出路径：JSON -> HTML，供邮件/历史留存。
- 旧 `report_generator.py`、`report_echarts.py` 先保留为 `export_html` 的实现，再逐步替代。

建议把历史 HTML 移动到 `reports/` 目录，避免继续污染仓库根目录。

### 3.4 统一数据源 Facade

新增 `core/data_facade.py`：

```python
def fetch_data(source, scope, codes=None, config=None):
    # scope: watchlist | full_market | spot | financial
```

`data_sources/*` 负责真正实现；`data_fetcher.py` 变成兼容壳或移除。
缓存目录/文件格式保持不变。

### 3.5 统一任务入口

后端 `backend/jobs.py` 增加任务类型映射：

```python
JOB_KINDS = {
    "swing_watchlist": "run_watchlist_backtest",
    "swing_stock": "run_stock_backtest",
    "factor": "run_factor_backtest",
    "optimize": "run_optimization",
    "test": "run_test_module",
}
```

前端只面向“任务”，不再暴露底层 Python 函数。

---

## 4. 分阶段实施

### Phase 1：策略注册表统一（低风险）
- [ ] 新建 `core/registry.py`
- [ ] 把 `backtest.STRATEGIES` 元数据迁入 registry
- [ ] `strategies/__init__.py` 保留旧接口
- [ ] `/api/meta` 增加 `category` 字段
- [ ] ConfigView 按 category 分组渲染
- [ ] 回归：`python run_test.py --list`、CLI 策略选择不受影响

### Phase 2：报告/结果模型统一（中风险）
- [ ] 新建 `core/results.py`
- [ ] `backend/serializers.py` 改为引用 core
- [ ] Watchlist / Stock 任务统一返回通用结果模型
- [ ] Vue 图表组件改为消费通用结果
- [ ] 保留旧 HTML 生成，但 Web 路径不再重复生成
- [ ] 回归：Web 回测结果与 CLI 数字一致

### Phase 3：数据源 Facade 统一（较高风险，需真实数据回归）
- [ ] 抽象 `fetch_watchlist_data` / `fetch_full_market_data` / `fetch_spot_data`
- [ ] `data_fetcher.py` 改为调用 facade
- [ ] 全市场、自选池、个股三种模式跑通
- [ ] 缓存兼容性验证

### Phase 4：清理与文档
- [ ] 历史 HTML 移入 `reports/`
- [ ] 删除或废弃已合并的重复测试入口
- [ ] README 更新为“核心服务 + Web + CLI”结构

---

## 5. 不做的事

- 不合并 `trading_engine.py` 与 `backtest.py` 的交易模型。
- 不改变 `config.json` 现有字段名（可新增，但避免破坏旧配置）。
- 不删除用户可能依赖的旧 CLI 入口，统一后先保留兼容 shim。
- 不引入数据库/消息队列，当前单机任务模型够用。

---

## 6. 风险与对策

| 风险 | 对策 |
|------|------|
| 旧模块 import 被破坏 | 保留 `strategies/__init__.py`、`backtest.STRATEGIES` 等兼容导出 |
| 回测数字不一致 | Phase 2 增加“CLI vs Web 结果 diff”回归脚本 |
| 数据源缓存失效 | 缓存 key/格式不变，只改调用层 |
| HTML 历史报告消失 | 统一后支持从 JSON 重新导出 HTML |
| 改动范围过大 | 按 Phase 1→4 推进，每个 Phase 独立可运行 |

---

## 7. 完成标准

- [ ] `/api/meta` 能返回带 `category` 的完整策略列表
- [ ] Config 表单能正确区分 swing/factor 策略
- [ ] Web 回测结果与 CLI 回测结果数字一致
- [ ] 所有数据获取都通过统一 facade 调用
- [ ] 根目录不再新增重复 HTML 报告
- [ ] 旧 CLI 全部可继续运行

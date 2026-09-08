# Quanti × vnpy 迁移设计（重构当前交易系统为 vnpy 内核）

> 依据用户决策：用 [vnpy/vnpy](https://github.com/vnpy/vnpy.git)（master 4.4.0 模块化生态）重构**核心交易引擎与策略层**；
> 新建独立 venv（`.venv-vnpy`）承载 vnpy 重构版，旧系统（`.venv` + `quantlab/*` + `tests/*` + Web）**零改动、可回退**；
> 先出本设计文档，随即开始核心策略端到端迁移验证（spike）。

---

## 1. 背景与目标

仓库 `D:\Code\quanti`（Quanti v4）是自研 A 股日线波段交易系统：
`config.json` 驱动 → 数据采集（akshare / quantdash / sina / tencent）→ 因子打分 →
多策略多标的摆动交易回测（`quantlab/trading_engine.py`，自选轮动/全市场扫描/个股回测）→
HTML/ECharts 报告 + FastAPI/Vue3 Web 控制台 + 飞书推送辅助。

问题：引擎、策略接口、指标、数据源、报告全部自研且互相咬合，扩展/实盘化成本高、生态封闭。

目标：把 **“策略实现 + 回测执行 + 数据供给”三段核心**迁移到 vnpy 4.4.0 生态
（`vnpy` core + `vnpy_ctastrategy` + `vnpy_ctabacktester`），
使策略跑在 vnpy 的标准策略模板与回测引擎上，数据经 vnpy `BaseDatabase` 接口供给，
从而获得 vnpy 的事件驱动、订单/成交、优化、GUI/Web 生态的扩展能力；
同时保留对旧结果的可对比验证与随时回退。

---

## 2. 现状盘点

### 2.1 Quanti（旧，保留不动）

| 模块 | 职责 |
|---|---|
| `quantlab/strategies/*` | 7 个策略：`reversal/momentum/bollinger/watchlist/watchlist_weekly/gap_open/gap_open_open`，接口 `BaseStrategy.buy_signal(ind,**ctx)->(is_buy,score,reason)`、`sell_signal(ind,entry_price,holding_days,...)`、`rebound_signal` |
| `quantlab/indicator_cache.py` | 逐日指标增量预计算（SKDJ/MACD/BOLL/MA/周线冻结视图/动态周线…），产出 `{日期str: ind_dict}` |
| `quantlab/trading_engine.py` | 组合级摆动回测引擎：多标的、仓位上限/评分排序/自选优先级/满仓、同日不换手、止损止盈、最大持有、`signal`/`exec` 双口径记账、次日开盘/收盘成交模式 |
| `quantlab/backtest.py` / `factor_model.py` | Walk-forward 因子回测、多因子打分 |
| `quantlab/data_fetcher.py`、`data_sources/*` | akshare 全市场 / quantdash / sina / tencent 自选池数据，落 `cache/*.pkl` |
| `backend/*`、`frontend/*` | FastAPI + Vue3 Web 控制台（运行任务/看报告） |
| `tests/*` | 17 套研究/验证脚本（自选轮动、做T、龙头、日内口诀、概率模型…） |

### 2.2 vnpy 4.4.0（新，作为内核）

| 组件 | 关键能力 |
|---|---|
| `vnpy.trader` | `EventEngine`/`MainEngine`、`BarData/TradeData/OrderData`、`BaseDatabase`(默认落 `vnpy_sqlite`)、`BaseDatafeed`、`Exchange(SSE/SZSE/BJSE…)`、`Interval(d/1h/1m)`、`OptimizationSetting`、`vt_symbol` 规范 `601857.SSE` |
| `vnpy_ctastrategy`（插件） | `CtaTemplate`（单标的策略基类：`on_bar/on_trade/on_order`，`buy/sell` 限价单）+ `BacktestingEngine`（单标的 BAR/TICK 回放、订单撮合、`DailyResult` 逐日盯市、`calculate_statistics` 绩效、BF/GA 优化） |
| `vnpy_ctabacktester`（插件） | 回测任务管理/UI 之上的 API 封装 |
| 生态可选 | `vnpy_portfoliostrategy`(多标的一账户策略)、`vnpy_akshare`(A股数据源)、`vnpy_sqlite`(默认库) |

### 2.3 关键架构差异（决定迁移方式）

1. **单标的 vs 组合**：vnpy `CtaTemplate` 回测引擎是**单标的、单持仓**模型（一引擎一合约、逐日盯市、订单按后续 bar 撮合）；
   Quanti 引擎是**多标的一账户**组合模型（资金池、仓位上限、跨标的打分排序选买）。
   → 策略**判定逻辑**可 1:1 复用；**组合层语义**需另行映射（自选轮动属组合语义，见 Phase 4）。
2. **成交时点**：Quanti 支持信号日收盘 / 次日尾盘 / 次日开盘成交（signal 与 exec 双口径记账）；
   vnpy BAR 模式撮合在**下一根 bar**（挂单于信号 bar 收盘后，下一 bar 开盘撮合、成交价受限价约束、gap 穿越可能不成交）。
   → 信号日/次日差异显式化并文档化，见 §6。
3. **指标计算位置**：Quanti 用 `indicator_cache` 预计算“逐日全量指标字典”后由引擎查表；
   vnpy 惯例是策略内自算（`ArrayManager`/自定义）。→ 采用**判定逻辑单源复用**策略（见 §4.2），信号级严格可比。
4. **统计口径**：两者总收益/年化/Sharpe/回撤/胜率公式不同（如年化天数、回撤定义、胜率是否按笔）。
   → 不做“逐字节相等”承诺，按 §8 两级验证：**信号一致**（G2）+ **指标可比**（G3，记录口径差异）。

---

## 3. 迁移策略总原则

- **新旧并存**：旧 CLI/引擎/Web 一律不改；新包 `vnpy_quanti/` 跑在 `.venv-vnpy`。
- **判定逻辑单源**：指标与买卖判定**原样 import 复用** `quantlab`（必要时把纯函数抽为共享模块，旧模块保持 re-export 兼容），杜绝“两套策略两套数”。
- **数据不复采**：直接用 `cache/*.pkl` 既有历史，经 vnpy `BaseDatabase` 适配层供给，避免重复下载与口径漂移。
- **验证两级**：先证信号（日期+原因）逐日一致，再谈成交与绩效可比（差异必有成因说明）。
- **可回退**：任何阶段旧系统可独立运行；新代码全部新增目录内，不侵入既有 import 路径。

---

## 4. 目标架构

```
D:\Code\quanti\
├── quantlab\            (旧系统：保留不动，作为信号判定与数据唯一来源)
├── vnpy_quanti\         ★ 新：vnpy 内核重构版
│   ├── symbols.py       代码→vt_symbol 映射 (sh/sz/bj→SSE/SZSE/BJSE, ETF/LOF, 名称)
│   ├── database.py      QuantCacheDatabase(BaseDatabase): cache/*.pkl → load_bar_data()
│   ├── adapters.py      history DataFrame ↔ BarData；ind_dict ↔ bar 检索
│   ├── indicators.py    薄封装：调 quantlab.indicator_cache 对单标的产出 {date: ind}
│   ├── strategies\      CtaTemplate 子类（包装 legacy 判定逻辑）
│   │   ├── common.py    基类 QuantCtaTemplate：载数据→预计算 ind→按 bar.date 查表判定
│   │   ├── reversal_cta.py / bollinger_cta.py / momentum_cta.py / gap_open_cta.py
│   ├── backtest.py      运行器：stock / watchlist / scan 三种模式；统计窗口化；结果落盘
│   ├── compare.py       与 legacy 结果对比（G1/G2/G3）
│   ├── cli.py           python -m vnpy_quanti --stock 601857 ...
│   └── tests\           回归测试
└── .venv-vnpy\          (新 venv，Python 3.13 + vnpy 4.4.0 生态)
```

数据流：

```
cache/*.pkl (历史 dict, 前复权)
   │  QuantCacheDatabase.load_bar_data(symbol, exchange, d, start, end)
   ▼
BarData[] ──► BacktestingEngine(interval=d, mode=BAR)
                每 bar: 查 ind(date) → 调 legacy buy/sell 判定 → buy()/sell()
   ▼
DailyResult/daily_df + trades ──► compare.py (对 legacy 输出) ──► 报告/CSV
```

---

## 5. 模块映射表

| 旧（Quanti） | 新（vnpy 内核） | 方式 | Phase |
|---|---|---|---|
| `quantlab/strategies/base.py` BaseStrategy | `vnpy_quanti/strategies/common.py` QuantCtaTemplate（内部持 legacy 策略实例） | 复用判定 + 换外壳 | P2 |
| `reversal.py / bollinger.py / momentum.py / gap_open*.py` | `*_cta.py`（CtaTemplate 子类） | 判定函数原样复用 | P2–P3 |
| `watchlist.py / watchlist_weekly.py`（自选轮动，**组合语义**） | Phase4 组合运行器（评估 `vnpy_portfoliostrategy` 或自研多标的 runner） | 近似/移植 | P4 |
| `indicator_cache.py`（逐日指标+周线冻结） | `vnpy_quanti/indicators.py` 薄封装（同函数） | 复用 | P2 |
| `trading_engine.run_swing_backtest`（组合回测主循环/仓位/双口径/成交模式） | `backtest.py` 运行器 + vnpy 引擎（单标的）；组合语义单独模块 | 移植外壳 | P2–P4 |
| `trading_engine.backtest_single_stock`（个股满仓回测） | `stock` 模式：单标的单引擎 | 移植 | P2 |
| `data_sources/*`、`data_fetcher.py`（采集落 cache） | 保留（采集仍由旧侧完成）；新侧只读 cache | 复用 | P1 |
| 缓存 pkl（`cache/`） | `database.py` QuantCacheDatabase | 适配 | P1 |
| `optimizer.py` / `tests/grid_search_*` | vnpy `OptimizationSetting`+`run_bf/ga_optimization` | 移植 | P3+ |
| `report_generator.py` / ECharts HTML | vnpy `daily_df`+plotly；旧 HTML 生成保留给 legacy | 后续 | P4+ |
| `backend/*`、`frontend/*`（Web 控制台） | **暂不动**；后续可把 vnpy_quanti 结果接入 | 保留 | 后置 |
| `tests/*`（17 套研究脚本） | **暂不动**（继续用 legacy 引擎跑研究） | 保留 | — |
| `close_check.py`/`daily_plan.py`/`intraday_t_monitor` | 不动；实盘化方向 Phase 后置（vnpy MainEngine/EventEngine） | 保留 | 后置 |

---

## 6. 执行语义差异表（显式化，写进验证报告）

| 维度 | Quanti 旧语义 | vnpy CTA BAR 回测语义 | 处理 |
|---|---|---|---|
| 成交时点 | 信号日收盘（signal 账户）/ 次日尾盘 / 次日开盘（exec 模式，`exec_next_*`） | 挂单于信号 bar；**下一 bar** 撮合，成交价=min/max(挂单价, 下一 bar open)，挂单价外 gap 不成交 | 默认档=vnpy 原生（≈“次日”档）；如需严格复刻旧口径，扩展 `BacktestingEngine` 支持“当日收盘成交”档（可选，P3） |
| T+1 | 当日买入后最早次一交易日卖（引擎同日不换手） | 买 bar 撮合于次 bar，卖挂单再撮合于再次 bar → 天然 ≥1 bar 间隔 | 近似成立，差异记录 |
| 手续费 | 自定义费用模型（股票双边 ~0.102%、ETF 双边 0.05%，另有印花税语义在 exec 口径） | `rate`（按成交额比例）+ `slippage` + `size`，无印花税独立项 | 用 `rate` 拟合；差异进对比表 |
| 仓位/股数 | 按资金比例/满仓/凯利折算股数、按代码取整 | `pos` 以 volume 计（size=1，股票 1 股=1 volume） | 适配：股数取整规则在策略内换算 |
| 多标的一账户 | 资金池、max_positions、跨标的打分选买、自选优先级 | 单引擎单标的 | Phase4 组合运行器 |
| 每日盯市 | 自算净值曲线（signal/exec 双口径） | `DailyResult` 按 strategy.pos 盯市，净值为单标的 | 组合/双口径由上层重算 |
| 绩效公式 | 自研（年化=总收益/年数、Sharpe 自定义等） | vnpy `calculate_statistics`（对数收益、240 年化日等） | 两侧各自输出，对比表列双口径 |
| 涨跌停/买不进 | 部分策略有涨停排除/`gap_max` | 无内建 | 策略内以 bar 涨跌幅自过滤 |

> 结论：**信号层追求完全一致；成交/绩效层追求“同输入下各自按自洽口径跑通并输出”，数字差异一律给出成因**，不做逐笔相等承诺。

---

## 7. 验证口径（Parity Gates）

- **G1 数据**：cache→`BarData`→DataFrame，逐行 `date/open/high/low/close/volume` 与源相等。
- **G2 信号**：同一历史输入下，legacy 引擎与 vnpy 策略对每标的逐日买/卖判定一致（日期集合 + reason 关键字段），覆盖率 100%。
- **G3 绩效**：同区间同策略，两侧分别输出 `总收益/年化/最大回撤/交易笔数/胜率`，写入差异矩阵并标注成因（成交时点/费用/公式）。
- 回归脚本：`vnpy_quanti/tests/compare_legacy.py --stock 601857 --strategy reversal`

---

## 8. 分阶段实施（每阶段独立可运行、可回退）

| Phase | 内容 | 完成标准（Exit Criteria） |
|---|---|---|
| **P0** | `.venv-vnpy`（Python 3.13）+ 安装 `vnpy vnpy_ctastrategy vnpy_ctabacktester`（走 pypi.org index）+ `.gitignore` 增 `.venv-vnpy/` | `python -c "import vnpy, vnpy_ctastrategy"` 通过 |
| **P1** | `vnpy_quanti` 骨架：`symbols/adapters/database` + `stock` 模式最小 runner | G1 通过；`python -m vnpy_quanti --stock 601857` 能加载出 bar |
| **P2** | 首个策略端到端：`reversal` 包装为 CtaTemplate，`--stock 601857` 跑通 vnpy 回测并输出统计 | G2 全等；G3 对比表产出；本步即本目标的 spike |
| **P3** | 其余单标的策略 `bollinger/momentum/gap_open/gap_open_open` 逐一包装 + 回归矩阵 | 各策略 G2 通过 |
| **P4** | 组合/自选轮动语义（watchlist 资金池、仓位上限、优先级）→ 评估 `vnpy_portfoliostrategy`，必要时自研多标的运行器 | 与 legacy watchlist 结果同区间 G2 一致 |
| **P5** | 优化（vnpy OptimizationSetting 接 legacy 参数网格）、CLI/README 收尾、Web 接入评估 | 文档齐全、旧系统全绿 |

## 9. 明确不做（本期）

- 不迁移 `tests/*` 研究脚本、Web 控制台、close_check/日内监控（保留 legacy 运行）。
- 不做实盘/半自动下单（无券商接口接入），仅铺好 vnpy 事件驱动内核可扩展位。
- 不改 `config.json` 既有字段语义；新配置走 vnpy setting/独立 json。

## 10. 风险与回退

| 风险 | 对策 |
|---|---|
| vnpy 4.4 依赖重（PySide6/ta-lib 等）或镜像源缺包 | 独立 venv + 显式 `--index-url https://pypi.org/simple` |
| 指标口径漂移（周线冻结、EMA 种子） | 判定逻辑单源 import，杜绝重写 |
| 成交语义差异导致收益不可比 | §6 差异表 + G2/G3 分级验收 |
| 新包破坏旧 import | 新目录独立命名空间，旧代码零改动 |
| vnpy 策略/API 版本变动 | 锁定已装版本记录到 `vnpy_quanti/requirements.txt` |

## 11. 附录：vnpy 4.4 关键 API 速查

```python
# 数据侧
from vnpy.trader.database import get_database            # 返回全局 database
from vnpy.trader.object import BarData
from vnpy.trader.constant import Exchange, Interval
vt_symbol = "601857.SSE"                                 # symbol.exchange

# 回测侧 (vnpy_ctastrategy.backtesting)
engine = BacktestingEngine()
engine.set_parameters(vt_symbol=..., interval=Interval.DAILY,
                      start=..., end=..., rate=0.0005, slippage=0, size=1,
                      pricetick=0.01, capital=150_000)
engine.add_strategy(ReversalCta, {})                      # setting 传参
engine.load_data(); engine.run_backtesting()
engine.calculate_result(); stats = engine.calculate_statistics(output=True)

# 策略侧
class ReversalCta(CtaTemplate): ...
    # on_init 里用 legacy 指标函数预计算 {date: ind}；on_bar 按 bar.datetime.date() 查表
    # 买: self.buy(price=bar.close_price, volume=n)  卖: self.sell(...)
```

---

## 12. 实施进度（2026-09-09）

### ✅ P0 环境
- 新建 `.venv-vnpy`（Python 3.13.3），自 pypi.org 安装并锁版：`vnpy==4.4.0`、
  `vnpy_ctastrategy==1.4.1`、`vnpy_ctabacktester==1.3.0`、`vnpy_sqlite==1.1.3`（见 `vnpy_quanti/requirements.txt`）。
- `.gitignore` 增补 `.venv-*/`、`.pypicache/`。要点：pip 缓存默认在 C 盘曾致磁盘满，已改
  `PIP_CACHE_DIR=D:\Code\quanti\.pypicache`。

### ✅ P1 数据适配（vnpy_quanti 骨架）
- 新增包 `vnpy_quanti/`：`symbols.py`（sh/sz/bj→SSE/SZSE/BSE 与 vt_symbol）、
  `adapters.py`（`cache/*.pkl` → DataFrame/BarData，规范化与旧系统一致）、
  `database.py`（`QuantCacheDatabase(BaseDatabase)` 内存供给日线，免数据库迁移）、
  `indicators.py`（复用 `quantlab.indicator_cache._incremental_indicators`，判定与旧系统单源）。

### ✅ P2 首个策略端到端（spike）：reversal × 601857
- `vnpy_quanti/strategies/`：`SwingCtaTemplate`（镜像旧引擎个股决策流：最大持有/rebound 规则/
  sell_signal 卖出序、buy_signal/rebound 买入、整手全仓/按分仓位、执行日禁买）+ `ReversalCta`。
- 回测跑在 `vnpy_ctastrategy.BacktestingEngine` 派生的 `QuantiBacktestEngine`（日线 BAR，
  默认信号日收盘成交 = 旧引擎 immediate 口径；`--fill native` 为 vnpy 原生次 bar 开盘口径）。
- 数据：`cache/quantdash_watchlist_20260908.pkl`（601857，4000 根），区间 2025-01-01 ~ 2026-09-08。

**验证结果**（详见 `reports/vnpy_compare/{new,ref}_601857.json`）：

| Gate | 结果 |
|---|---|
| G1 数据 | 同源同规范化：history_rows 4000 = 4000 ✅ |
| G2 信号 | 决策 6 条（3 买 3 卖）日期+方向与 legacy **100% 一致** ✅ |
| G3 绩效 | fill=close：最终净值 211,026.13 vs 211,026.12、总收益均 +40.68%（逐分一致）✅ |

用法：
```bash
# vnpy 侧（.venv-vnpy）
python -m vnpy_quanti stock --stock 601857 --strategy reversal \
    --watchlist-pkl cache/quantdash_watchlist_20260908.pkl \
    --start 2025-01-01 --end 2026-09-08 --out out.json
# legacy 参考（.venv）
python vnpy_quanti/tests/gen_legacy_ref.py --stock 601857 --strategy reversal \
    --watchlist-pkl cache/quantdash_watchlist_20260908.pkl \
    --start 2025-01-01 --end 2026-09-08 --out ref.json
# 对比
python -m vnpy_quanti compare --new out.json --legacy ref.json
```

### ✅ P3 其余 swing 策略迁移 + 批量验证矩阵
- 新增 `vnpy_quanti/engine.py` `QuantiBacktestEngine(BacktestingEngine)`：**信号日收盘成交**档
  （on_bar 新挂单按当根 bar 收盘撮合），镜像旧引擎 immediate 口径；`--fill native` 可回到 vnpy 原生次 bar 开盘撮合。
- 迁移 `bollinger / momentum / gap_open / gap_open_open`（`strategies/*_cta.py`），判定仍单源复用 legacy。
- 关键发现（写进 `SwingCtaTemplate`）：旧引擎买入门槛 = **加分后 `score >= min_buy_score`**，
  策略内部 `is_buy` 仅用于“是否尝试超跌反弹买点”——momentum 返回 `(False, score3)` 的日子旧引擎照买
  （3+2≥4），初版 wrapper 误以 is_buy 拦截导致漏单，已按旧引擎语义修正。

**批量验证**（`vnpy_quanti/tests/run_p3_matrix.py`，区间 2024-01-01~数据最新，fill=close；产物 `reports/vnpy_compare/p3_*.json` + `p3_report.md`）：

| 策略×标的 | G2 命中 | vnpy 收益 | legacy 收益 |
|---|---|---|---|
| bollinger×601857 | 4/4 ✅ | +3.19% | +3.19% |
| bollinger×600547 | 2/2 ✅ | -7.36% | -7.36% |
| bollinger×002832 | 2/2 ✅ | -3.19% | -3.19% |
| momentum×601857 | 54/54 ✅ | +29.46% | +29.46% |
| momentum×600547 | 64/64 ✅ | +36.82% | +36.82% |
| gap_open×002832 | 12/12 ✅ | +14.50% | +14.50% |
| gap_open×600547 | 22/22 ✅ | -5.33% | -5.33% |
| gap_open_open×002832 / ×600547 | 0/0 ✅（大票无开盘跳空≥3%信号） | — | — |

→ 全部单标的 swing 策略 G2 信号 100% 一致，fill=close 下 G3 绩效与 legacy 逐分一致（费率 0）。

### ✅ P4（核心引擎·组合语义）：watchlist 自选轮动迁移 + 验证
- 新增 `vnpy_quanti/portfolio.py` `PortfolioEngine`：多标的一账户组合回测引擎，镜像旧引擎
  `run_swing_backtest` immediate（信号日收盘成交）决策流——逐 union 交易日：卖出判定（最大持有/
  rebound 规则/sell_signal，收盘卖出+当日禁买该 code）→ 买入判定（slots & cash>5% 门槛、
  加分后 score≥min_buy_score 选股、按分稳定排序取前 slots、龙头TOP10/自选优先级加分、
  full_position 或按分缩放仓位、整手）→ 收盘估值净值。数据/指标/判定逻辑与旧系统单源。
- 验证（`tests/{run_p4_vnpy,gen_legacy_p4_ref}.py` + 共享用例 `tests/p4_cases.json`，
  标的 = config watchlist 5 只，2025-01-01 ~ 数据最新；产物 `reports/vnpy_compare/p4_*.json`）：

| 用例 | 语义 | G2 命中 | 最终净值(两侧同) | 总收益率(均同) |
|---|---|---|---|---|
| A 单仓满仓 | watchlist max1/pct1/full | 65/65 ✅ | 378,642.48 | +152.43% |
| B 双仓各半 | watchlist max2/pct0.5 | 106/106 ✅ | 262,362.99 | +31.18% |
| C 跳空轮动池 | gap_open max5/pct0.2 | 42/42 ✅ | 199,099.92 | −0.45% |
| D 弱转强池 | reversal max2/pct0.5 | 20/20 ✅ | 242,626.74 | +21.31% |
| E 动量池 | momentum max2/pct0.5 | 120/120 ✅ | 213,436.97 | +6.72% |
| F 周线自选 | watchlist_weekly max2/pct0.5 | 281 去重对 ✅(含减仓/加仓) | 251,622.11 | +25.81% |
| G 次日尾盘成交 | watchlist max1/full, exec_next_close | 61/61 ✅ | 317,189.02 | +111.46% |
| H 次日开盘成交 | watchlist max2/pct0.5, exec_next_open | 104/104 ✅ | 263,968.30 | +31.98% |

- PortfolioEngine 现同时支持 **immediate**（信号日收盘成交）与 **exec**（T 日信号 → T+1
  开盘/收盘成交，含 buy_next_open/close 独立延迟）两套成交口径，先卖后买、执行日禁买与旧引擎 0b 步一致。

- 修复三处口径：买入回退需 ≥60 根（旧引擎阈值，卖出为 ≥20）；gap_open/open 策略对次新标的
  走“轻量回退”(≥2 根, 非 60 根)；补齐 **reduce_signal 高位减仓**(减50%就地记账、不进
  sold_today)与 **add_position_signal 低位加仓**(补足到 position_pct×总资产, 摊薄成本, 含
  旧引擎“无 _last_price 时持仓按候选价估值”的怪癖口径)——否则次新 ETF(588170) 与周线策略
  (watchlist_weekly) 在 `_incremental_indicators` n<120 窗口/高位减仓场景漂移。
- 组合语义现状边界（后续可扩展）：immediate 单账户口径；exec 延迟/双口径净值、momentum 按
  scored TOP 名单选池、watchlist_weekly（reduce_signal 高位减仓 + add_position_signal
  低位加仓）为后续项。

### ⏭ 下一步（P4 收尾 / P5）
- watchlist_weekly 组合（减仓/加仓）、exec_next_* 与双口径净值、全市场扫描多标的回归。
- 参数优化接入 vnpy `OptimizationSetting`；CLI/README 收尾；旧系统保持零改动。

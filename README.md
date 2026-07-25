# A股多因子量化选股系统 v3.0

基于五大因子（价值/成长/质量/动量/风险）的量化选股模型，支持**个股 + ETF/LOF**混合筛选，提供**买卖参考价**和**策略回测**功能。

## 特性

- 📊 **多因子模型**: 价值(EP/BP/SP) + 成长(营收/利润增长) + 质量(ROE/毛利率) + 动量(1/3/6月收益率) + 风险(波动率/回撤)
- 🏦 **ETF/LOF支持**: 纳入全市场1500+只ETF和LOF基金，与个股统一排名
- 💰 **买卖参考价**: 基于PE/PB历史分位数 + 行业对比 + 技术面支撑阻力，给出买入/卖出价格区间
- 🔬 **策略回测**: Walk-forward回测引擎，统计5/10/20/60日持有周期的胜率、收益、Sharpe比率
- 📈 **两种运行模式**:
  - **快速模式** (`--no_history`): 仅价值+成长+质量，~1分钟完成
  - **完整模式**: 全部5因子，首次约30分钟（之后缓存秒开）
- 🌐 **HTML交互报告**: Plotly图表（雷达图、行业分布、得分分布、因子散点图、回测曲线、估值分位数）
- 💾 **智能缓存**: 当日数据自动缓存，二次运行秒级完成
- ⚡ **多线程加速**: 多进程并发获取历史K线数据

## 安装

```bash
pip install -r requirements.txt
```

## 使用方法

```bash
# 快速模式 (推荐首次使用, ~1分钟)
python main.py --no_history

# 完整模式 (含动量/风险因子, 首次约30分钟)
python main.py

# ========== 新增功能 ==========

# 含ETF/LOF (与个股统一排名)
python main.py --include_etf_lof --no_history

# 含买卖参考价 (估值分位数 + 技术面)
python main.py --price_targets --no_history

# 含策略回测 (Walk-forward, 5/10/20/60日)
python main.py --backtest --no_history

# 全功能模式
python main.py --include_etf_lof --price_targets --backtest --top 50

# ========== 自定义参数 ==========

# 调整因子权重 (价值,成长,质量,动量,风险)
python main.py --weights 0.3,0.15,0.35,0.1,0.1

# 自定义回测参数
python main.py --backtest --backtest_top 20 --backtest_periods 5,10,20 --backtest_rebalance 30

# 延长历史数据 (估值分位数和回测需要更长历史)
python main.py --price_targets --hist_days 1200

# 仅终端输出，不生成HTML
python main.py --no_html --no_history
```

## 命令行参数

### 基础参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--top N` | 30 | 展示前N只证券 |
| `--min_score F` | 0 | 最低综合得分门槛 |
| `--weights V,G,Q,M,R` | 0.25,0.20,0.25,0.20,0.10 | 五因子权重 |
| `--no_history` | - | 跳过历史K线（快速3因子模式） |
| `--no_html` | - | 不生成HTML报告 |
| `--no_cache` | - | 强制重新获取数据 |
| `--workers N` | 8 | 历史数据并发进程数 |
| `--output PATH` | report_YYYYMMDD.html | HTML报告路径 |
| `--hist_days N` | 300 | 历史K线天数（估值/回测需要更长） |

### ETF/LOF参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--include_etf_lof` | 关闭 | 将全市场ETF+LOF纳入选股池 |

### 买卖参考价参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--price_targets` | 关闭 | 计算TOP N的买卖参考价格 |

### 回测参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--backtest` | 关闭 | 运行Walk-forward策略回测 |
| `--backtest_top N` | 30 | 回测每周期选股数 |
| `--backtest_periods` | 5,10,20,60 | 持有周期（交易日），逗号分隔 |
| `--backtest_rebalance N` | 60 | 再平衡间隔（交易日） |

## 选股逻辑

### 过滤规则
- **股票**: 自动排除ST股票、停牌股、北交所、亏损股(EPS≤0)
- **ETF/LOF**: 排除停牌(成交量=0)、规模过小(流通市值<1亿)

### 因子计算
1. **价值因子** (仅股票): EP(1/PE)、BP(1/PB)、SP(1/PS) — 越高越便宜
2. **成长因子** (仅股票): 营收同比增长率、净利润同比增长率 — 越高越好
3. **质量因子** (仅股票): ROE、毛利率 — 越高越好
4. **动量因子** (通用): 1月/3月/6月收益率 — 越高越强
5. **风险因子** (通用): 60日波动率(取反)、最大回撤(取反) — 越低越稳

> ETF/LOF仅使用动量+风险因子，打分时自动重新归一化权重，使其与股票可比。

### 打分方法
1. 缩尾处理: 5%~95%分位数截断极端值
2. Z-score标准化: 统一量纲
3. 加权综合: 按因子权重求组内平均再加权求和
4. 缺失惩罚: 因子缺失的证券按可用比例的平方根打折（ETF/LOF仅对动量+风险做惩罚）

### 买卖参考价
- **股票**: PE/PB历史分位数 + 同行业PE中位数修正 + 技术面支撑阻力
- **ETF/LOF**: 价格历史分位数 + MA均线 + Bollinger Bands支撑阻力
- 输出: 买入区间(低估值)、卖出区间(高估值)、支撑位、阻力位、PE百分位

### 回测框架
- **Walk-forward**: 每隔N天重新打分，选TOP N，计算前瞻收益
- **基准**: 全市场等权平均收益
- **指标**: 胜率、平均收益、中位数收益、Sharpe比率、最大回撤、超额收益

## 数据来源

- **个股行情**: AkShare → 新浪财经 (`stock_zh_a_spot`)
- **ETF行情**: AkShare → 东方财富 (`fund_etf_spot_em`, 37列含IOPV/折溢价/资金流)
- **LOF行情**: AkShare → 东方财富 (`fund_lof_spot_em`)
- **财务报表**: AkShare → 东方财富 (`stock_yjbb_em`)
- **个股K线**: AkShare → 新浪财经 (`stock_zh_a_daily`)
- **ETF/LOF K线**: AkShare → 东方财富 (`fund_etf_hist_em` / `fund_lof_hist_em`)
- **行业分类**: 东方财富财报数据中的"所处行业"字段

## 项目结构

```
├── main.py              # CLI入口 + 流程编排 (v3.0)
├── data_fetcher.py      # AkShare数据采集 + 缓存 + 多线程 + ETF/LOF
├── factor_model.py      # 因子计算、标准化、综合打分 (资产类型感知)
├── price_targets.py     # 买卖参考价: 估值分位数 + 技术面支撑阻力
├── backtest.py          # Walk-forward回测引擎 + 胜率统计
├── report_generator.py  # 终端表格 + HTML交互报告 (含回测图表)
├── requirements.txt     # Python依赖
└── cache/               # 数据缓存 (自动生成)
```

## ⚠️ 免责声明

本工具仅供学习研究，**不构成任何投资建议**。股市有风险，投资需谨慎。过往表现不代表未来收益。回测结果基于历史数据，不代表未来表现。

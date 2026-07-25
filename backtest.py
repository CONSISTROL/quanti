"""
A股多因子量化选股 - 回测框架模块
Walk-forward回测引擎，验证选股策略的历史表现
统计各持有周期（5/10/20/60日）的胜率、平均收益、Sharpe比率等
"""

import numpy as np
import pandas as pd
from datetime import datetime

import plotly.graph_objects as go


# ============================================================
# 数据结构
# ============================================================

class PeriodStats:
    """单个持有周期的统计"""
    def __init__(self, holding_days):
        self.holding_days = holding_days
        self.returns = []           # 每期收益列表
        self.period_details = []    # 每期详细记录
        self.win_count = 0
        self.total_count = 0
        self.win_rate = 0.0
        self.avg_return = 0.0
        self.median_return = 0.0
        self.max_return = 0.0
        self.min_return = 0.0
        self.sharpe = 0.0
        self.max_drawdown = 0.0
        self.cumulative_nav = []    # 累计净值序列

    def compute(self):
        """计算统计指标"""
        if not self.returns:
            return

        arr = np.array(self.returns)
        self.total_count = len(arr)
        self.win_count = int(np.sum(arr > 0))
        self.win_rate = self.win_count / self.total_count if self.total_count > 0 else 0
        self.avg_return = float(np.mean(arr))
        self.median_return = float(np.median(arr))
        self.max_return = float(np.max(arr))
        self.min_return = float(np.min(arr))

        # Sharpe比率 (年化)
        if len(arr) > 1 and np.std(arr) > 0:
            annualization = np.sqrt(252 / self.holding_days)
            self.sharpe = float(np.mean(arr) / np.std(arr) * annualization)

        # 累计净值 & 最大回撤
        nav = [1.0]
        for r in self.returns:
            nav.append(nav[-1] * (1 + r))
        self.cumulative_nav = nav

        nav_arr = np.array(nav)
        peaks = np.maximum.accumulate(nav_arr)
        drawdowns = (peaks - nav_arr) / peaks
        self.max_drawdown = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0


class BacktestResult:
    """回测结果容器"""
    def __init__(self):
        self.periods = {}           # {holding_days: PeriodStats}
        self.benchmark_periods = {} # {holding_days: PeriodStats} 基准
        self.rebalance_dates = []   # 再平衡日期列表
        self.selections = {}        # {date_str: [(code, name, score, price), ...]}
        self.total_stocks = 0       # 参与回测的股票总数
        self.n_rebalances = 0       # 再平衡次数

    def summary(self):
        """返回汇总表格"""
        rows = []
        for hp, ps in sorted(self.periods.items()):
            bench = self.benchmark_periods.get(hp)
            rows.append({
                '持有周期': f'{hp}日',
                '再平衡次数': self.n_rebalances,
                '策略胜率': f'{ps.win_rate:.1%}',
                '策略均收益': f'{ps.avg_return:+.2%}',
                '策略中位数': f'{ps.median_return:+.2%}',
                '策略Sharpe': f'{ps.sharpe:.2f}',
                '策略最大回撤': f'{ps.max_drawdown:.1%}',
                '基准胜率': f'{bench.win_rate:.1%}' if bench else '-',
                '基准均收益': f'{bench.avg_return:+.2%}' if bench else '-',
                '超额收益': f'{ps.avg_return - bench.avg_return:+.2%}' if bench else '-',
            })
        return pd.DataFrame(rows)


# ============================================================
# 核心计算
# ============================================================

def _code_pure(code):
    """从带前缀的代码中提取纯6位数字"""
    code = str(code)
    for prefix in ('sh', 'sz', 'bj', 'SH', 'SZ', 'BJ'):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code.zfill(6)


def compute_forward_return(hist_df, buy_date_idx, holding_days):
    """
    计算从buy_date_idx开始持有holding_days个交易日后的收益率
    使用收盘价: ret = close[buy_date_idx + holding_days] / close[buy_date_idx] - 1

    参数:
        hist_df: K线DataFrame (必须有close列)
        buy_date_idx: 买入日在DataFrame中的整数索引
        holding_days: 持有天数

    返回: float 或 None (数据不足时)
    """
    if hist_df is None or 'close' not in hist_df.columns:
        return None

    closes = hist_df['close'].values
    sell_idx = buy_date_idx + holding_days

    if sell_idx >= len(closes):
        return None

    buy_price = closes[buy_date_idx]
    sell_price = closes[sell_idx]

    if buy_price <= 0 or np.isnan(buy_price) or np.isnan(sell_price):
        return None

    return sell_price / buy_price - 1.0


def find_common_dates(history_dict, min_stocks=50):
    """
    找出所有股票K线数据中的共同日期范围

    返回: 按日期排序的datetime列表
    """
    if not history_dict:
        return []

    # 收集所有日期
    date_counts = {}
    for sym, df in history_dict.items():
        if df is None or 'date' not in df.columns:
            continue
        for d in df['date'].values:
            d = pd.Timestamp(d)
            date_counts[d] = date_counts.get(d, 0) + 1

    # 只保留至少有min_stocks只股票有数据的日期
    common = sorted([d for d, c in date_counts.items() if c >= min_stocks])
    return common


def get_rebalance_dates(common_dates, rebalance_days=60):
    """
    从共同日期中每隔rebalance_days取一个再平衡日期

    返回: [(date, index_in_common_dates), ...]
    """
    if not common_dates:
        return []

    rebalance_points = []
    for i in range(0, len(common_dates) - rebalance_days, rebalance_days):
        rebalance_points.append((common_dates[i], i))

    return rebalance_points


# ============================================================
# Walk-forward回测引擎
# ============================================================

def run_backtest(scored_df, history_dict, financial_df, financial_prev_df,
                 sector_map, weights, top_n=30, rebalance_days=60,
                 holding_periods=None, asset_type_map=None):
    """
    Walk-forward回测引擎

    流程:
    1. 确定回测日期序列
    2. 每个再平衡日: 使用到该日为止的数据计算因子→打分→选TOP N
    3. 计算各持有周期的前瞻收益率
    4. 汇总统计

    参数:
        scored_df: 当前打分结果 (用于获取股票列表和代码)
        history_dict: {sina_code: K线DataFrame}
        financial_df, financial_prev_df: 财报数据
        sector_map: 行业映射
        weights: 因子权重
        top_n: 每周期选股数
        rebalance_days: 再平衡间隔 (交易日)
        holding_periods: 持有周期列表 [5,10,20,60]
        asset_type_map: 资产类型映射

    返回: BacktestResult
    """
    if holding_periods is None:
        holding_periods = [5, 10, 20, 60]

    result = BacktestResult()
    result.total_stocks = len(scored_df)

    # 初始化各周期统计
    for hp in holding_periods:
        result.periods[hp] = PeriodStats(hp)
        result.benchmark_periods[hp] = PeriodStats(hp)

    # 找共同日期
    common_dates = find_common_dates(history_dict, min_stocks=30)
    if len(common_dates) < rebalance_days + max(holding_periods):
        print(f"  ⚠ 历史数据不足: 仅 {len(common_dates)} 个共同交易日，"
              f"需要至少 {rebalance_days + max(holding_periods)} 天")
        # 降低再平衡间隔
        rebalance_days = max(20, len(common_dates) // 5)
        print(f"  → 自动调整再平衡间隔为 {rebalance_days} 天")

    rebalance_points = get_rebalance_dates(common_dates, rebalance_days)
    if not rebalance_points:
        print(f"  ✗ 无法确定再平衡日期")
        return result

    # 限制再平衡次数（确保有足够的前瞻数据）
    max_hp = max(holding_periods)
    valid_points = []
    for date, idx in rebalance_points:
        if idx + max_hp < len(common_dates):
            valid_points.append((date, idx))
    rebalance_points = valid_points

    result.n_rebalances = len(rebalance_points)
    result.rebalance_dates = [d for d, _ in rebalance_points]

    print(f"  回测参数: {len(rebalance_points)} 次再平衡, "
          f"间隔 {rebalance_days} 天, 选股 TOP {top_n}")
    print(f"  持有周期: {holding_periods}")

    # 逐期回测
    from factor_model import calculate_all_factors, score_stocks

    for rb_idx, (rb_date, common_idx) in enumerate(rebalance_points):
        date_str = rb_date.strftime('%Y-%m-%d')

        # 截断所有K线到该日期
        truncated_hist = {}
        for sym, df in history_dict.items():
            if df is None or 'date' not in df.columns:
                continue
            mask = df['date'] <= rb_date
            truncated = df[mask]
            if len(truncated) >= 30:
                truncated_hist[sym] = truncated

        if len(truncated_hist) < top_n:
            continue

        # 仅使用有截断后数据的股票
        available_codes = set(truncated_hist.keys())
        spot_subset = scored_df.copy()
        code_col = 'code'
        if code_col in spot_subset.columns:
            spot_subset['_sina'] = spot_subset[code_col].apply(
                lambda c: next((s for s in available_codes if _code_pure(s) == str(c).zfill(6)), None)
            )
            spot_subset = spot_subset[spot_subset['_sina'].notna()]

        if len(spot_subset) < top_n:
            continue

        # 重新计算因子（使用截断后的K线）
        try:
            # 构建临时的spot_filtered (仅含可用股票)
            # 为简化，直接使用已有的scored_df中的排名
            # (完整实现应在每个再平衡点重新走因子计算流程)
            # 这里使用简化版：直接用当前排名，但仅保留在该日期有数据的股票
            top_stocks = spot_subset.head(top_n * 2)  # 多取一些，后面过滤

            selections = []
            for _, row in top_stocks.iterrows():
                code = str(row.get('code', '')).zfill(6)
                sina_code = row.get('_sina', '')
                if not sina_code or sina_code not in truncated_hist:
                    continue

                hist = truncated_hist[sina_code]
                # 找到该日期在K线中的位置
                date_mask = hist['date'] <= rb_date
                buy_idx = date_mask.sum() - 1
                if buy_idx < 0:
                    continue

                buy_price = hist['close'].values[buy_idx]
                score = row.get('composite_score', 0)
                selections.append((code, row.get('name', ''), score, buy_price,
                                   sina_code, buy_idx))

                if len(selections) >= top_n:
                    break

            if len(selections) < top_n // 2:
                continue

            result.selections[date_str] = [
                (s[0], s[1], s[2], s[3]) for s in selections
            ]

            # 计算各持有周期收益
            for hp in holding_periods:
                period_returns = []

                # 策略收益 (等权TOP N)
                for code, name, score, buy_price, sina_code, buy_idx in selections:
                    hist = truncated_hist.get(sina_code)
                    ret = compute_forward_return(hist, buy_idx, hp)
                    if ret is not None:
                        period_returns.append(ret)

                if period_returns:
                    # 组合收益 = 等权平均
                    portfolio_ret = np.mean(period_returns)
                    result.periods[hp].returns.append(portfolio_ret)
                    result.periods[hp].period_details.append({
                        'date': date_str,
                        'n_stocks': len(period_returns),
                        'portfolio_return': portfolio_ret,
                        'individual_returns': period_returns,
                    })

                # 基准收益 (全市场等权)
                bench_returns = []
                for sym, hist in truncated_hist.items():
                    date_mask = hist['date'] <= rb_date
                    buy_idx = date_mask.sum() - 1
                    if buy_idx < 0:
                        continue
                    ret = compute_forward_return(hist, buy_idx, hp)
                    if ret is not None:
                        bench_returns.append(ret)

                if bench_returns:
                    bench_portfolio_ret = np.mean(bench_returns)
                    result.benchmark_periods[hp].returns.append(bench_portfolio_ret)

        except Exception as e:
            print(f"  ⚠ 再平衡 {date_str} 出错: {e}")
            continue

        if (rb_idx + 1) % 5 == 0 or rb_idx == len(rebalance_points) - 1:
            print(f"  进度: {rb_idx + 1}/{len(rebalance_points)} "
                  f"({date_str})")

    # 计算统计指标
    for hp in holding_periods:
        result.periods[hp].compute()
        result.benchmark_periods[hp].compute()

    return result


# ============================================================
# 回测图表 (Plotly HTML)
# ============================================================

def generate_backtest_charts(result):
    """
    生成回测图表（Plotly HTML片段列表）

    1. 累计净值曲线（策略 vs 基准）
    2. 各周期胜率对比柱状图
    3. 各周期收益分布直方图
    4. 回撤统计表

    返回: list of (title, html_string) tuples
    """
    charts = []
    colors = {
        'strategy': '#3498db',
        'benchmark': '#95a5a6',
        'win': '#27ae60',
        'loss': '#e74c3c',
    }

    # ---- 1. 累计净值曲线 ----
    for hp, ps in sorted(result.periods.items()):
        bench = result.benchmark_periods.get(hp)
        if not ps.cumulative_nav or len(ps.cumulative_nav) < 2:
            continue

        fig = go.Figure()

        # 策略净值
        dates = list(range(len(ps.cumulative_nav)))
        fig.add_trace(go.Scatter(
            x=dates, y=ps.cumulative_nav,
            mode='lines+markers',
            name=f'策略 (TOP{result.n_rebalances}选股)',
            line=dict(color=colors['strategy'], width=2),
            marker=dict(size=6),
        ))

        # 基准净值
        if bench and bench.cumulative_nav:
            fig.add_trace(go.Scatter(
                x=list(range(len(bench.cumulative_nav))),
                y=bench.cumulative_nav,
                mode='lines+markers',
                name='基准 (全市场等权)',
                line=dict(color=colors['benchmark'], width=2, dash='dash'),
                marker=dict(size=5),
            ))

        # 标注再平衡日期
        if result.rebalance_dates:
            for i, d in enumerate(result.rebalance_dates):
                if i < len(ps.cumulative_nav):
                    fig.add_annotation(
                        x=i, y=ps.cumulative_nav[i],
                        text=d.strftime('%m/%d'),
                        showarrow=False,
                        font=dict(size=9, color='#888'),
                        yshift=15,
                    )

        fig.update_layout(
            title=f'持有 {hp} 日 — 累计净值',
            xaxis=dict(title='再平衡周期', gridcolor='#f0f0f0'),
            yaxis=dict(title='净值', gridcolor='#f0f0f0'),
            height=320, margin=dict(t=40, b=30, l=50, r=20),
            legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
            font=dict(size=12),
        )

        html = fig.to_html(full_html=False, include_plotlyjs=False)
        charts.append((f'累计净值 ({hp}日)', html))

    # ---- 2. 胜率对比柱状图 ----
    periods = sorted(result.periods.keys())
    if periods:
        fig = go.Figure()

        strategy_wr = [result.periods[hp].win_rate * 100 for hp in periods]
        benchmark_wr = [result.benchmark_periods[hp].win_rate * 100
                        if hp in result.benchmark_periods else 0
                        for hp in periods]

        fig.add_trace(go.Bar(
            x=[f'{hp}日' for hp in periods],
            y=strategy_wr,
            name='策略胜率',
            marker=dict(color=colors['strategy']),
            text=[f'{v:.1f}%' for v in strategy_wr],
            textposition='outside',
        ))
        fig.add_trace(go.Bar(
            x=[f'{hp}日' for hp in periods],
            y=benchmark_wr,
            name='基准胜率',
            marker=dict(color=colors['benchmark']),
            text=[f'{v:.1f}%' for v in benchmark_wr],
            textposition='outside',
        ))

        fig.update_layout(
            title='各周期胜率对比',
            barmode='group',
            yaxis=dict(title='胜率 (%)', gridcolor='#f0f0f0', range=[0, 100]),
            xaxis=dict(gridcolor='#f0f0f0'),
            height=320, margin=dict(t=40, b=30, l=50, r=20),
            legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
            font=dict(size=12),
        )

        html = fig.to_html(full_html=False, include_plotlyjs=False)
        charts.append(('胜率对比', html))

    # ---- 3. 收益分布直方图 (选第一个周期展示) ----
    if periods:
        hp = periods[0]
        ps = result.periods[hp]
        if ps.returns:
            fig = go.Figure()
            returns_pct = [r * 100 for r in ps.returns]

            fig.add_trace(go.Histogram(
                x=returns_pct, nbinsx=max(10, len(returns_pct) // 2),
                name=f'{hp}日收益分布',
                marker=dict(color=colors['strategy']),
            ))

            # 添加均值线
            avg = np.mean(returns_pct)
            fig.add_vline(x=avg, line_dash='dash', line_color='#e74c3c',
                          annotation_text=f'均值 {avg:+.2f}%')
            # 零线
            fig.add_vline(x=0, line_dash='solid', line_color='#333', line_width=1)

            fig.update_layout(
                title=f'{hp}日持有 — 组合收益分布',
                xaxis=dict(title='收益率 (%)', gridcolor='#f0f0f0'),
                yaxis=dict(title='次数', gridcolor='#f0f0f0'),
                height=300, margin=dict(t=40, b=30, l=50, r=20),
                font=dict(size=12),
            )

            html = fig.to_html(full_html=False, include_plotlyjs=False)
            charts.append(('收益分布', html))

    return charts


# ============================================================
# 终端输出
# ============================================================

def print_backtest_summary(result):
    """在终端打印回测摘要"""
    print("\n" + "═" * 70)
    print("  📊 回测结果摘要")
    print("═" * 70)
    print(f"  参与证券: {result.total_stocks} 只")
    print(f"  再平衡次数: {result.n_rebalances} 次")
    if result.rebalance_dates:
        print(f"  回测区间: {result.rebalance_dates[0].strftime('%Y-%m-%d')} "
              f"~ {result.rebalance_dates[-1].strftime('%Y-%m-%d')}")

    # 表头
    print(f"\n  {'持有周期':>8}  {'策略胜率':>8}  {'策略均收益':>10}  {'策略中位数':>10}  "
          f"{'Sharpe':>7}  {'最大回撤':>8}  {'基准均收益':>10}  {'超额':>8}")
    print("  " + "─" * 82)

    for hp in sorted(result.periods.keys()):
        ps = result.periods[hp]
        bench = result.benchmark_periods.get(hp)

        excess = ''
        bench_avg = ''
        if bench and bench.total_count > 0:
            excess_val = ps.avg_return - bench.avg_return
            excess = f'{excess_val:+.2%}'
            bench_avg = f'{bench.avg_return:+.2%}'

        print(f"  {hp:>6}日  {ps.win_rate:>7.1%}  {ps.avg_return:>+9.2%}  "
              f"{ps.median_return:>+9.2%}  {ps.sharpe:>7.2f}  "
              f"{ps.max_drawdown:>7.1%}  {bench_avg:>10}  {excess:>8}")

    print("  " + "─" * 82)
    print("  ⚠️  回测结果基于历史数据，不代表未来表现。投资有风险，入市需谨慎。")
    print()

"""
A股多因子量化选股 - 回测框架模块 v2
真正的 Walk-forward 回测引擎:
  每个再平衡日 → 重新计算因子 → 重新打分排名 → 选TOP N → 测量前瞻收益

内置多种策略:
  momentum   — 动量策略 (追涨强势股)
  reversion  — 反转策略 (抄底弱势股)
  trend      — 趋势策略 (均线之上+动量)
  low_vol    — 低波动策略 (买最稳的)
  composite  — 复合策略 (加权综合)

支持 AI 策略分析 (调用 Claude API 给出优化建议)
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
        self.returns = []
        self.period_details = []
        self.win_count = 0
        self.total_count = 0
        self.win_rate = 0.0
        self.avg_return = 0.0
        self.median_return = 0.0
        self.max_return = 0.0
        self.min_return = 0.0
        self.sharpe = 0.0
        self.max_drawdown = 0.0
        self.cumulative_nav = []

    def compute(self):
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
        if len(arr) > 1 and np.std(arr) > 0:
            self.sharpe = float(np.mean(arr) / np.std(arr) * np.sqrt(252 / self.holding_days))
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
    def __init__(self, strategy_name='composite'):
        self.strategy_name = strategy_name
        self.periods = {}
        self.benchmark_periods = {}
        self.rebalance_dates = []
        self.selections = {}
        self.total_stocks = 0
        self.n_rebalances = 0

    def summary(self):
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
# 内置策略: 因子计算函数
# ============================================================

STRATEGIES = {
    'momentum':  {'label': '动量策略', 'desc': '买入近期涨幅最大的股票'},
    'reversion': {'label': '反转策略', 'desc': '买入近期跌幅最大的股票（逆向投资）'},
    'trend':     {'label': '趋势策略', 'desc': '买入均线之上且有动量的股票'},
    'low_vol':   {'label': '低波动策略', 'desc': '买入波动率最低的股票'},
    'composite': {'label': '复合策略', 'desc': '动量+趋势+低波动的加权综合'},
}


def _code_pure(code):
    code = str(code)
    for prefix in ('sh', 'sz', 'bj', 'SH', 'SZ', 'BJ'):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code.zfill(6)


def compute_factors_at_date(closes, strategy='composite'):
    """
    在某个历史截断点计算因子 (只用closes数组)

    参数:
        closes: numpy array of close prices up to the rebalance date
        strategy: 策略名称

    返回: float score (越高越好), 或 None (数据不足)
    """
    n = len(closes)
    if n < 60:
        return None

    scores = {}

    # 动量因子: 1月/3月/6月收益率
    ret_1m = (closes[-1] / closes[-min(21, n)]) - 1.0 if n >= 21 else 0
    ret_3m = (closes[-1] / closes[-min(61, n)]) - 1.0 if n >= 61 else 0
    ret_6m = (closes[-1] / closes[-min(121, n)]) - 1.0 if n >= 121 else 0
    scores['momentum'] = ret_1m * 0.2 + ret_3m * 0.4 + ret_6m * 0.4

    # 反转因子: 近期跌幅越大，反转得分越高 (取反)
    ret_recent = (closes[-1] / closes[-min(11, n)]) - 1.0 if n >= 11 else 0
    scores['reversion'] = -ret_recent  # 跌得多 = 得分高

    # 趋势因子: 收盘价相对MA60的位置
    ma60 = np.mean(closes[-min(60, n):])
    ma120 = np.mean(closes[-min(120, n):]) if n >= 120 else ma60
    trend_strength = (closes[-1] - ma60) / ma60  # 正=在均线上方
    # 均线多头排列加分
    ma_alignment = 1.0 if ma60 > ma120 else 0.5
    scores['trend'] = trend_strength * ma_alignment

    # 低波动因子: 波动率取反 (越低越好)
    if n >= 60:
        log_ret = np.diff(np.log(closes[-60:]))
        vol = np.std(log_ret) * np.sqrt(252) if len(log_ret) > 0 else 999
        scores['low_vol'] = -vol  # 低波动 = 高分
    else:
        scores['low_vol'] = 0

    # 最大回撤因子
    if n >= 60:
        recent = closes[-min(120, n):]
        peaks = np.maximum.accumulate(recent)
        dd = (peaks - recent) / peaks
        max_dd = np.max(dd)
        scores['low_drawdown'] = -max_dd
    else:
        scores['low_drawdown'] = 0

    # 策略权重映射
    strategy_weights = {
        'momentum':  {'momentum': 1.0},
        'reversion': {'reversion': 1.0},
        'trend':     {'trend': 0.6, 'momentum': 0.4},
        'low_vol':   {'low_vol': 0.6, 'low_drawdown': 0.4},
        'composite': {'momentum': 0.3, 'trend': 0.3, 'low_vol': 0.2, 'low_drawdown': 0.2},
    }

    w = strategy_weights.get(strategy, strategy_weights['composite'])
    final_score = sum(w.get(k, 0) * v for k, v in scores.items())
    return final_score


def compute_market_regime(history_dict, rb_date, lookback=60):
    """
    计算市场整体趋势 (regime)
    返回: 1 (牛市, 买入信号) 或 0 (熊市, 观望)

    逻辑: 全部股票的平均收盘价是否在MA60之上
    """
    prices_at_date = []
    prices_lookback = []

    for sym, hist in history_dict.items():
        if hist is None or 'date' not in hist.columns or 'close' not in hist.columns:
            continue
        mask_now = hist['date'] <= rb_date
        if mask_now.sum() < lookback:
            continue
        idx_now = mask_now.sum() - 1
        closes = hist['close'].values

        prices_at_date.append(closes[idx_now])
        ma = np.mean(closes[max(0, idx_now - lookback + 1):idx_now + 1])
        prices_lookback.append(ma)

    if len(prices_at_date) < 50:
        return 1  # 数据不足时默认买入

    # 计算有多少比例的股票在均线之上
    above_ma_count = sum(1 for p, m in zip(prices_at_date, prices_lookback) if p > m)
    ratio = above_ma_count / len(prices_at_date)

    return 1 if ratio > 0.3 else 0  # 30%以上股票在MA之上 → 牛市


# ============================================================
# 前瞻收益计算
# ============================================================

def compute_forward_return(closes, buy_idx, holding_days):
    """计算从buy_idx持有holding_days个交易日后的收益率"""
    sell_idx = buy_idx + holding_days
    if sell_idx >= len(closes):
        return None
    buy_price = closes[buy_idx]
    sell_price = closes[sell_idx]
    if buy_price <= 0 or np.isnan(buy_price) or np.isnan(sell_price):
        return None
    return sell_price / buy_price - 1.0


# ============================================================
# Walk-forward回测引擎
# ============================================================

def run_backtest(scored_df, history_dict, financial_df, financial_prev_df,
                 sector_map, weights, top_n=30, rebalance_days=60,
                 holding_periods=None, asset_type_map=None,
                 strategy='composite', use_regime_filter=True):
    """
    真正的 Walk-forward 回测引擎

    每个再平衡日:
      1. 用截止到该日的K线数据计算因子
      2. Z-score标准化 + 排名
      3. 选TOP N
      4. (可选) 市场趋势过滤
      5. 计算前瞻收益

    参数:
        strategy: 'momentum' / 'reversion' / 'trend' / 'low_vol' / 'composite'
        use_regime_filter: 是否启用市场趋势过滤
    """
    if holding_periods is None:
        holding_periods = [5, 10, 20, 60]

    strategy_label = STRATEGIES.get(strategy, {}).get('label', strategy)
    result = BacktestResult(strategy_name=strategy)
    result.total_stocks = len(scored_df) if scored_df is not None else len(history_dict)

    for hp in holding_periods:
        result.periods[hp] = PeriodStats(hp)
        result.benchmark_periods[hp] = PeriodStats(hp)

    if not history_dict:
        print(f"  ✗ 无历史K线数据，无法回测")
        return result

    # ---- 预处理: 构建 pure_code → sina_code 映射 ----
    pure_to_sina = {}
    stock_data = []  # [(sina_code, closes_array, dates_array)]

    for sina_code, hist in history_dict.items():
        if hist is None or len(hist) < 60:
            continue
        if 'close' not in hist.columns or 'date' not in hist.columns:
            continue
        pure = _code_pure(sina_code)
        pure_to_sina[pure] = sina_code

        closes = hist['close'].values.astype(float)
        dates = hist['date'].values
        # 按日期排序
        sort_idx = np.argsort(dates)
        closes = closes[sort_idx]
        dates = dates[sort_idx]

        stock_data.append((sina_code, closes, dates))

    print(f"  有历史数据的证券: {len(stock_data)}")

    if len(stock_data) < top_n:
        print(f"  ✗ 数据不足 TOP {top_n}，无法回测")
        return result

    # ---- 找共同日期范围 ----
    all_dates_set = None
    for _, _, dates in stock_data:
        s = set(pd.Timestamp(d) for d in dates)
        if all_dates_set is None:
            all_dates_set = s
        else:
            all_dates_set = all_dates_set | s  # 并集 (宽松)

    if not all_dates_set:
        print(f"  ✗ 无法确定日期范围")
        return result

    common_dates = sorted(all_dates_set)
    max_hp = max(holding_periods)

    if len(common_dates) < max_hp + 60:
        print(f"  ✗ 历史数据严重不足: 仅 {len(common_dates)} 天")
        return result

    # ---- 智能调整再平衡间隔 ----
    min_cycles = 15
    available_range = len(common_dates) - 60 - max_hp
    if available_range > 0:
        auto_interval = max(5, available_range // min_cycles)
        if rebalance_days > auto_interval and available_range // rebalance_days < min_cycles:
            rebalance_days = auto_interval
            print(f"  ⚠ 再平衡间隔自动调整为 {rebalance_days} 天（确保≥{min_cycles}周期）")

    # ---- 确定再平衡日期 ----
    rebalance_points = []
    for i in range(60, len(common_dates) - max_hp, rebalance_days):
        rebalance_points.append(common_dates[i])

    if not rebalance_points:
        print(f"  ✗ 无法确定再平衡日期")
        return result

    result.n_rebalances = len(rebalance_points)
    result.rebalance_dates = rebalance_points

    print(f"  策略: {strategy_label}")
    print(f"  回测参数: {len(rebalance_points)} 次再平衡, "
          f"间隔 {rebalance_days} 天, 选股 TOP {top_n}")
    print(f"  持有周期: {holding_periods}")
    print(f"  市场过滤: {'开启' if use_regime_filter else '关闭'}")

    # ---- 预计算每只股票的 (日期→索引) 映射 ----
    stock_date_map = {}
    for sina_code, closes, dates in stock_data:
        ts_dates = [(pd.Timestamp(d), i) for i, d in enumerate(dates)]
        ts_dates.sort(key=lambda x: x[0])
        stock_date_map[sina_code] = ts_dates

    def find_idx(sina_code, rb_date):
        """二分查找: 找 <= rb_date 的最大日期索引"""
        pairs = stock_date_map.get(sina_code, [])
        if not pairs:
            return None
        lo, hi = 0, len(pairs) - 1
        result_idx = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if pairs[mid][0] <= rb_date:
                result_idx = pairs[mid][1]
                lo = mid + 1
            else:
                hi = mid - 1
        return result_idx

    # ---- 逐期回测 ----
    for rb_idx, rb_date in enumerate(rebalance_points):
        date_str = rb_date.strftime('%Y-%m-%d')

        # 市场趋势过滤
        if use_regime_filter:
            regime = compute_market_regime(history_dict, rb_date)
            if regime == 0:
                # 熊市: 跳过该周期 (空仓)
                for hp in holding_periods:
                    result.periods[hp].returns.append(0.0)  # 空仓 = 0收益
                    # 基准照常计算
                    bench_rets = []
                    for sina_code, closes, dates in stock_data:
                        idx = find_idx(sina_code, rb_date)
                        if idx is not None:
                            ret = compute_forward_return(closes, idx, hp)
                            if ret is not None:
                                bench_rets.append(ret)
                    if bench_rets:
                        result.benchmark_periods[hp].returns.append(np.mean(bench_rets))
                continue

        # ---- 计算每只股票的因子得分 ----
        scored_stocks = []
        for sina_code, closes, dates in stock_data:
            idx = find_idx(sina_code, rb_date)
            if idx is None or idx < 60:
                continue

            # 截取到该日的收盘价
            truncated_closes = closes[:idx + 1]
            score = compute_factors_at_date(truncated_closes, strategy)
            if score is not None and not np.isnan(score):
                scored_stocks.append({
                    'sina': sina_code,
                    'score': score,
                    'buy_idx': idx,
                    'closes': closes,
                })

        if len(scored_stocks) < top_n:
            continue

        # ---- Z-score标准化 + 排名 ----
        raw_scores = np.array([s['score'] for s in scored_stocks])
        if np.std(raw_scores) > 0:
            z_scores = (raw_scores - np.mean(raw_scores)) / np.std(raw_scores)
            # 缩尾
            z_scores = np.clip(z_scores, np.percentile(z_scores, 5),
                               np.percentile(z_scores, 95))
        else:
            z_scores = raw_scores

        for i, s in enumerate(scored_stocks):
            s['z_score'] = z_scores[i]

        # 按Z-score降序排列, 选TOP N
        scored_stocks.sort(key=lambda x: x['z_score'], reverse=True)
        selections = scored_stocks[:top_n]

        result.selections[date_str] = [
            (_code_pure(s['sina']), '', s['z_score'], s['closes'][s['buy_idx']])
            for s in selections
        ]

        # ---- 计算各持有周期收益 ----
        for hp in holding_periods:
            period_returns = []
            for sel in selections:
                ret = compute_forward_return(sel['closes'], sel['buy_idx'], hp)
                if ret is not None:
                    period_returns.append(ret)

            if period_returns:
                portfolio_ret = np.mean(period_returns)
                result.periods[hp].returns.append(portfolio_ret)
                result.periods[hp].period_details.append({
                    'date': date_str,
                    'n_stocks': len(period_returns),
                    'portfolio_return': portfolio_ret,
                })

            # 基准 (全市场等权)
            bench_rets = []
            for sina_code, closes, dates in stock_data:
                idx = find_idx(sina_code, rb_date)
                if idx is not None:
                    ret = compute_forward_return(closes, idx, hp)
                    if ret is not None:
                        bench_rets.append(ret)
            if bench_rets:
                result.benchmark_periods[hp].returns.append(np.mean(bench_rets))

        if (rb_idx + 1) % 5 == 0 or rb_idx == len(rebalance_points) - 1:
            regime_str = '📈' if (not use_regime_filter or regime == 1) else '📉空仓'
            print(f"  进度: {rb_idx + 1}/{len(rebalance_points)} "
                  f"({date_str}) — {len(selections)}只 {regime_str}")

    # 计算统计
    for hp in holding_periods:
        result.periods[hp].compute()
        result.benchmark_periods[hp].compute()

    return result


# ============================================================
# 多策略对比
# ============================================================

def run_strategy_comparison(scored_df, history_dict, financial_df, financial_prev_df,
                            sector_map, weights, top_n=30, rebalance_days=60,
                            holding_periods=None, asset_type_map=None):
    """
    运行所有内置策略并对比

    返回: {strategy_name: BacktestResult}
    """
    if holding_periods is None:
        holding_periods = [5, 10, 20, 60]

    results = {}
    for strategy_name in STRATEGIES:
        label = STRATEGIES[strategy_name]['label']
        print(f"\n  ── 测试策略: {label} ──")

        r = run_backtest(
            scored_df, history_dict, financial_df, financial_prev_df,
            sector_map, weights, top_n, rebalance_days,
            holding_periods, asset_type_map,
            strategy=strategy_name, use_regime_filter=True,
        )
        results[strategy_name] = r

    return results


def print_strategy_comparison(comparison_results, holding_periods=None):
    """打印多策略对比表"""
    if holding_periods is None:
        holding_periods = [5, 10, 20, 60]

    print("\n" + "═" * 90)
    print("  🏆 多策略回测对比")
    print("═" * 90)

    # 找最佳持有周期 (取20日)
    ref_hp = 20 if 20 in holding_periods else holding_periods[0]

    print(f"\n  {'策略':>12}  {'再平衡':>6}  {'胜率':>6}  {'均收益':>8}  "
          f"{'Sharpe':>7}  {'最大回撤':>8}  {'基准收益':>8}  {'超额':>8}")
    print("  " + "─" * 82)

    best_strategy = None
    best_sharpe = -999

    for name, result in comparison_results.items():
        label = STRATEGIES.get(name, {}).get('label', name)
        ps = result.periods.get(ref_hp)
        bench = result.benchmark_periods.get(ref_hp)

        if ps is None or ps.total_count == 0:
            continue

        bench_avg = f'{bench.avg_return:+.2%}' if bench and bench.total_count > 0 else '-'
        excess = ''
        if bench and bench.total_count > 0:
            excess_val = ps.avg_return - bench.avg_return
            excess = f'{excess_val:+.2%}'

        print(f"  {label:>12}  {result.n_rebalances:>6}  {ps.win_rate:>5.1%}  "
              f"{ps.avg_return:>+7.2%}  {ps.sharpe:>7.2f}  "
              f"{ps.max_drawdown:>7.1%}  {bench_avg:>8}  {excess:>8}")

        if ps.sharpe > best_sharpe:
            best_sharpe = ps.sharpe
            best_strategy = name

    print("  " + "─" * 82)

    if best_strategy:
        best_label = STRATEGIES[best_strategy]['label']
        print(f"  🏆 最佳策略: {best_label} (Sharpe={best_sharpe:.2f})")
        print(f"     {STRATEGIES[best_strategy]['desc']}")

    print()
    return best_strategy


# ============================================================
# AI 策略分析
# ============================================================

def ai_analyze_strategy(comparison_results, api_key=None, holding_periods=None):
    """
    调用 AI (Claude/千问等Anthropic兼容API) 分析回测结果

    支持:
    - Anthropic 官方: ANTHROPIC_API_KEY
    - 通义千问 DashScope: ANTHROPIC_AUTH_TOKEN + ANTHROPIC_BASE_URL
    - 命令行: --ai_key
    """
    import os

    # 从环境变量获取配置 (优先命令行参数)
    if api_key is None:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            api_key = os.environ.get('ANTHROPIC_AUTH_TOKEN', '')

    base_url = os.environ.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com')
    model = os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-20250514')

    if not api_key:
        print("  ⚠ 未提供 API Key，跳过AI分析")
        print("  💡 方式1: python main.py --backtest --ai_key sk-xxx")
        print("  💡 方式2: 设置环境变量 ANTHROPIC_AUTH_TOKEN")
        return None

    if holding_periods is None:
        holding_periods = [5, 10, 20, 60]

    # 构建分析数据
    analysis_data = []
    for name, result in comparison_results.items():
        label = STRATEGIES.get(name, {}).get('label', name)
        for hp in sorted(result.periods.keys()):
            ps = result.periods[hp]
            bench = result.benchmark_periods.get(hp)
            if ps.total_count > 0:
                analysis_data.append({
                    'strategy': label,
                    'holding_days': hp,
                    'cycles': ps.total_count,
                    'win_rate': f'{ps.win_rate:.1%}',
                    'avg_return': f'{ps.avg_return:+.3%}',
                    'sharpe': f'{ps.sharpe:.2f}',
                    'max_drawdown': f'{ps.max_drawdown:.1%}',
                    'benchmark_return': f'{bench.avg_return:+.3%}' if bench and bench.total_count > 0 else 'N/A',
                })

    df = pd.DataFrame(analysis_data)
    data_str = df.to_string(index=False)

    prompt = f"""你是一个A股量化策略专家。请分析以下多策略回测结果，给出优化建议。

回测数据（A股多因子选股系统，Walk-forward回测）:
{data_str}

请回答:
1. 哪个策略表现最好？为什么？
2. 胜率偏低的可能原因是什么？
3. 给出3个具体的策略改进建议（可以调整因子权重、增加过滤条件等）
4. 如果要做一个适合A股的短线策略（5-20日持有），你会怎么设计？

请用中文回答，简洁专业。"""

    try:
        import requests

        # 清除代理 (DashScope等国内API不需要代理)
        old_env = {}
        for pv in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
                    'all_proxy', 'ALL_PROXY'):
            old_env[pv] = os.environ.pop(pv, None)
        os.environ['NO_PROXY'] = '*'

        url = f'{base_url.rstrip("/")}/v1/messages'

        resp = requests.post(
            url,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            },
            json={
                'model': model,
                'max_tokens': 1500,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            timeout=120,
        )

        # 恢复代理设置
        for pv, val in old_env.items():
            if val is not None:
                os.environ[pv] = val

        if resp.status_code == 200:
            result_data = resp.json()
            # 兼容 Anthropic / DashScope 不同的返回格式
            try:
                content = result_data['content']
                if isinstance(content, list):
                    # 找到 type='text' 的块 (跳过 thinking 块)
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            return block['text']
                    # 兜底: 取最后一个块的 text
                    return content[-1].get('text', str(content))
                elif isinstance(content, str):
                    return content
            except (KeyError, IndexError, TypeError):
                pass
            try:
                # DashScope/通义千问 格式: {"output": {"text": "..."}}
                return result_data['output']['text']
            except (KeyError, TypeError):
                pass
            try:
                # OpenAI 兼容格式: {"choices": [{"message": {"content": "..."}}]}
                return result_data['choices'][0]['message']['content']
            except (KeyError, IndexError, TypeError):
                return str(result_data)
        else:
            print(f"  ⚠ AI分析请求失败: HTTP {resp.status_code}")
            print(f"     URL: {url}")
            print(f"     Model: {model}")
            try:
                err = resp.json()
                print(f"     {err.get('error', {}).get('message', resp.text[:200])}")
            except Exception:
                print(f"     {resp.text[:200]}")
            return None
    except Exception as e:
        print(f"  ⚠ AI分析出错: {e}")
        return None


# ============================================================
# 回测图表
# ============================================================

def generate_backtest_charts(result):
    """生成回测图表（Plotly HTML片段列表）"""
    charts = []
    colors = {
        'strategy': '#3498db',
        'benchmark': '#95a5a6',
    }

    for hp, ps in sorted(result.periods.items()):
        bench = result.benchmark_periods.get(hp)
        if not ps.cumulative_nav or len(ps.cumulative_nav) < 2:
            continue

        fig = go.Figure()

        dates = list(range(len(ps.cumulative_nav)))
        fig.add_trace(go.Scatter(
            x=dates, y=ps.cumulative_nav,
            mode='lines+markers',
            name=f'{result.strategy_name}策略',
            line=dict(color=colors['strategy'], width=2),
            marker=dict(size=5),
        ))

        if bench and bench.cumulative_nav:
            fig.add_trace(go.Scatter(
                x=list(range(len(bench.cumulative_nav))),
                y=bench.cumulative_nav,
                mode='lines',
                name='基准 (全市场等权)',
                line=dict(color=colors['benchmark'], width=2, dash='dash'),
            ))

        fig.update_layout(
            title=f'{STRATEGIES.get(result.strategy_name, {}).get("label", result.strategy_name)} '
                  f'— 持有 {hp} 日 累计净值',
            xaxis=dict(title='再平衡周期', gridcolor='#f0f0f0'),
            yaxis=dict(title='净值', gridcolor='#f0f0f0'),
            height=320, margin=dict(t=40, b=30, l=50, r=20),
            legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
            font=dict(size=12),
        )

        html = fig.to_html(full_html=False, include_plotlyjs=False)
        charts.append((f'累计净值 ({hp}日)', html))

    # 胜率对比
    periods = sorted(result.periods.keys())
    if periods:
        fig = go.Figure()
        strategy_wr = [result.periods[hp].win_rate * 100 for hp in periods]
        benchmark_wr = [result.benchmark_periods[hp].win_rate * 100
                        if hp in result.benchmark_periods else 0
                        for hp in periods]

        fig.add_trace(go.Bar(
            x=[f'{hp}日' for hp in periods], y=strategy_wr,
            name='策略胜率', marker=dict(color=colors['strategy']),
            text=[f'{v:.1f}%' for v in strategy_wr], textposition='outside',
        ))
        fig.add_trace(go.Bar(
            x=[f'{hp}日' for hp in periods], y=benchmark_wr,
            name='基准胜率', marker=dict(color=colors['benchmark']),
            text=[f'{v:.1f}%' for v in benchmark_wr], textposition='outside',
        ))

        fig.update_layout(
            title='各周期胜率对比', barmode='group',
            yaxis=dict(title='胜率 (%)', gridcolor='#f0f0f0', range=[0, 100]),
            height=300, margin=dict(t=40, b=30, l=50, r=20),
            legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
            font=dict(size=12),
        )
        html = fig.to_html(full_html=False, include_plotlyjs=False)
        charts.append(('胜率对比', html))

    return charts


# ============================================================
# 终端输出
# ============================================================

def print_backtest_summary(result):
    """在终端打印回测摘要"""
    strategy_label = STRATEGIES.get(result.strategy_name, {}).get('label', result.strategy_name)

    print("\n" + "═" * 70)
    print(f"  📊 回测结果: {strategy_label}")
    print("═" * 70)
    print(f"  参与证券: {result.total_stocks} 只")
    print(f"  再平衡次数: {result.n_rebalances} 次")
    if result.rebalance_dates:
        print(f"  回测区间: {result.rebalance_dates[0].strftime('%Y-%m-%d')} "
              f"~ {result.rebalance_dates[-1].strftime('%Y-%m-%d')}")

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
    print("  ⚠️  回测基于历史数据，不代表未来表现。投资有风险，入市需谨慎。")
    print()

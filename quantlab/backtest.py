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
    'macd':      {'label': 'MACD策略', 'desc': 'MACD金叉+柱状线扩大'},
    'skdj':      {'label': 'SKDJ策略', 'desc': 'SKDJ超卖区金叉反弹'},
    'tech':      {'label': '技术综合', 'desc': 'MACD+SKDJ+成交量+动量的加权综合'},
    'composite': {'label': '复合策略', 'desc': '动量+趋势+低波动+技术面的加权综合'},
}


def _code_pure(code):
    code = str(code)
    for prefix in ('sh', 'sz', 'bj', 'SH', 'SZ', 'BJ'):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code.zfill(6)


def _ema(data, period):
    """计算指数移动平均"""
    if len(data) < period:
        return np.full_like(data, np.nan)
    alpha = 2.0 / (period + 1)
    result = np.full_like(data, np.nan, dtype=float)
    result[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def _compute_macd(closes):
    """
    计算MACD指标
    返回: (macd_hist, macd_signal_cross, macd_hist_expanding)
    - macd_hist: MACD柱状线值 (正=多头)
    - macd_signal_cross: 1=金叉, -1=死叉, 0=无
    - macd_hist_expanding: 1=柱状线扩大, -1=缩小, 0=无
    """
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12 - ema26  # MACD线

    # 信号线 (DIF的9日EMA)
    valid_dif = dif[~np.isnan(dif)]
    if len(valid_dif) < 9:
        return 0, 0, 0
    dea_full = _ema(dif[~np.isnan(dif)], 9)
    dea = dea_full[-1] if len(dea_full) > 0 else np.nan
    dif_now = dif[-1]

    if np.isnan(dif_now) or np.isnan(dea):
        return 0, 0, 0

    macd_hist = (dif_now - dea) * 2  # 柱状线

    # 金叉/死叉判断 (最近3天内)
    cross = 0
    if len(valid_dif) >= 4 and len(dea_full) >= 4:
        for lag in range(1, 4):
            if lag < len(valid_dif) and lag < len(dea_full):
                prev_dif = valid_dif[-(lag + 1)]
                prev_dea = dea_full[-(lag + 1)]
                curr_dif = valid_dif[-lag]
                curr_dea = dea_full[-lag]
                if not (np.isnan(prev_dif) or np.isnan(prev_dea) or
                        np.isnan(curr_dif) or np.isnan(curr_dea)):
                    if prev_dif <= prev_dea and curr_dif > curr_dea:
                        cross = 1  # 金叉
                        break
                    elif prev_dif >= prev_dea and curr_dif < curr_dea:
                        cross = -1  # 死叉
                        break

    # 柱状线是否在扩大
    expanding = 0
    if len(dea_full) >= 2:
        prev_hist = (valid_dif[-2] - dea_full[-2]) * 2
        curr_hist = macd_hist
        if not np.isnan(prev_hist):
            if abs(curr_hist) > abs(prev_hist):
                expanding = 1
            else:
                expanding = -1

    return macd_hist, cross, expanding


def _compute_skdj(highs, lows, closes, n_period=9, m1=3, m2=3):
    """
    计算SKDJ (慢速KDJ)
    返回: (k, d, j, k_cross_d)
    - k, d, j: SKDJ三条线 (0-100)
    - k_cross_d: 1=金叉, -1=死叉, 0=无
    """
    if len(closes) < n_period + 6:
        return 50, 50, 50, 0

    # RSV
    k_values = []
    d_values = []

    k_prev = 50.0
    d_prev = 50.0

    for i in range(n_period - 1, len(closes)):
        window_high = np.max(highs[i - n_period + 1:i + 1])
        window_low = np.min(lows[i - n_period + 1:i + 1])

        if window_high == window_low:
            rsv = 50.0
        else:
            rsv = (closes[i] - window_low) / (window_high - window_low) * 100

        # K = (m1-1)/m1 * K_prev + 1/m1 * RSV (慢速平滑)
        k = (m1 - 1) / m1 * k_prev + 1.0 / m1 * rsv
        # D = (m2-1)/m2 * D_prev + 1/m2 * K
        d = (m2 - 1) / m2 * d_prev + 1.0 / m2 * k

        k_values.append(k)
        d_values.append(d)
        k_prev = k
        d_prev = d

    if len(k_values) < 2:
        return 50, 50, 50, 0

    k_now = k_values[-1]
    d_now = d_values[-1]
    j_now = 3 * k_now - 2 * d_now

    # J = 3K - 2D

    # K上穿D (金叉/死叉)
    cross = 0
    for lag in range(1, min(4, len(k_values))):
        if k_values[-(lag + 1)] <= d_values[-(lag + 1)] and k_values[-lag] > d_values[-lag]:
            cross = 1  # 金叉
            break
        elif k_values[-(lag + 1)] >= d_values[-(lag + 1)] and k_values[-lag] < d_values[-lag]:
            cross = -1  # 死叉
            break

    return k_now, d_now, j_now, cross


def _compute_volume_factor(volumes, closes):
    """
    成交量因子
    返回: (vol_ratio, vol_price_score)
    - vol_ratio: 近5日均量 / 近20日均量 (放量程度)
    - vol_price_score: 量价配合得分
      - 价涨量增: 正分 (健康上涨)
      - 价涨量缩: 负分 (上涨乏力)
      - 价跌量增: 负分 (恐慌出逃)
      - 价跌量缩: 正分 (缩量回调, 可能企稳)
    """
    if len(volumes) < 20 or len(closes) < 20:
        return 1.0, 0

    vol_5 = np.mean(volumes[-5:])
    vol_20 = np.mean(volumes[-20:])
    vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1.0

    # 量价关系
    price_chg = (closes[-1] / closes[-6]) - 1.0 if len(closes) >= 6 else 0
    vol_chg = vol_ratio - 1.0  # 正=放量, 负=缩量

    if price_chg > 0 and vol_chg > 0:
        # 价涨量增: 健康上涨, 加分
        vol_price_score = min(vol_chg, 2.0) * 0.5
    elif price_chg > 0 and vol_chg < 0:
        # 价涨量缩: 上涨乏力
        vol_price_score = vol_chg * 0.3
    elif price_chg < 0 and vol_chg > 0:
        # 价跌量增: 恐慌抛售, 减分
        vol_price_score = -min(vol_chg, 2.0) * 0.5
    else:
        # 价跌量缩: 缩量回调, 可能企稳, 小幅加分
        vol_price_score = 0.1

    return vol_ratio, vol_price_score


def compute_factors_at_date(closes, strategy='composite', volumes=None, highs=None, lows=None):
    """
    在某个历史截断点计算因子

    参数:
        closes: numpy array of close prices up to the rebalance date
        strategy: 策略名称
        volumes: numpy array of volumes (可选)
        highs: numpy array of high prices (可选, SKDJ需要)
        lows: numpy array of low prices (可选, SKDJ需要)

    返回: float score (越高越好), 或 None (数据不足)
    """
    n = len(closes)
    if n < 60:
        return None

    scores = {}

    # ---- 动量因子: 1月/3月/6月收益率 ----
    ret_1m = (closes[-1] / closes[-min(21, n)]) - 1.0 if n >= 21 else 0
    ret_3m = (closes[-1] / closes[-min(61, n)]) - 1.0 if n >= 61 else 0
    ret_6m = (closes[-1] / closes[-min(121, n)]) - 1.0 if n >= 121 else 0
    scores['momentum'] = ret_1m * 0.2 + ret_3m * 0.4 + ret_6m * 0.4

    # ---- 反转因子 ----
    ret_recent = (closes[-1] / closes[-min(11, n)]) - 1.0 if n >= 11 else 0
    scores['reversion'] = -ret_recent

    # ---- 趋势因子 ----
    ma60 = np.mean(closes[-min(60, n):])
    ma120 = np.mean(closes[-min(120, n):]) if n >= 120 else ma60
    trend_strength = (closes[-1] - ma60) / ma60
    ma_alignment = 1.0 if ma60 > ma120 else 0.5
    scores['trend'] = trend_strength * ma_alignment

    # ---- 低波动因子 ----
    if n >= 60:
        log_ret = np.diff(np.log(closes[-60:]))
        vol = np.std(log_ret) * np.sqrt(252) if len(log_ret) > 0 else 999
        scores['low_vol'] = -vol
    else:
        scores['low_vol'] = 0

    # ---- 最大回撤因子 ----
    if n >= 60:
        recent = closes[-min(120, n):]
        peaks = np.maximum.accumulate(recent)
        dd = (peaks - recent) / peaks
        max_dd = np.max(dd)
        scores['low_drawdown'] = -max_dd
    else:
        scores['low_drawdown'] = 0

    # ---- MACD因子 ----
    macd_hist, macd_cross, macd_expanding = _compute_macd(closes)
    macd_score = 0
    if macd_cross == 1:    # 金叉: 强烈看多
        macd_score += 2.0
    elif macd_cross == -1:  # 死叉: 看空
        macd_score -= 2.0
    if macd_hist > 0:       # 柱状线为正: 多头
        macd_score += 0.5
    if macd_expanding == 1 and macd_hist > 0:  # 多头+柱状线扩大
        macd_score += 0.5
    scores['macd'] = macd_score

    # ---- SKDJ因子 ----
    if highs is not None and lows is not None:
        k, d, j, skdj_cross = _compute_skdj(highs, lows, closes)
        skdj_score = 0
        if skdj_cross == 1 and k < 30:    # 超卖区金叉: 强烈看多
            skdj_score += 2.0
        elif skdj_cross == 1 and k < 50:  # 中低位金叉
            skdj_score += 1.0
        elif skdj_cross == -1 and k > 70:  # 超买区死叉: 看空
            skdj_score -= 2.0
        elif skdj_cross == -1 and k > 50:  # 中高位死叉
            skdj_score -= 1.0
        # J值极端区域
        if j < 0:
            skdj_score += 0.5  # 超卖反弹概率大
        elif j > 100:
            skdj_score -= 0.5  # 超买回调概率大
        scores['skdj'] = skdj_score
    else:
        scores['skdj'] = 0

    # ---- 成交量因子 ----
    if volumes is not None:
        vol_ratio, vol_price_score = _compute_volume_factor(volumes, closes)
        scores['volume'] = vol_price_score
    else:
        scores['volume'] = 0

    # ---- 策略权重映射 ----
    strategy_weights = {
        'momentum':  {'momentum': 1.0},
        'reversion': {'reversion': 1.0},
        'trend':     {'trend': 0.6, 'momentum': 0.4},
        'low_vol':   {'low_vol': 0.6, 'low_drawdown': 0.4},
        'macd':      {'macd': 0.5, 'trend': 0.3, 'volume': 0.2},
        'skdj':      {'skdj': 0.5, 'reversion': 0.3, 'volume': 0.2},
        'tech':      {'macd': 0.25, 'skdj': 0.25, 'volume': 0.25, 'momentum': 0.25},
        'composite': {'momentum': 0.2, 'trend': 0.2, 'low_vol': 0.1, 'low_drawdown': 0.1,
                      'macd': 0.15, 'skdj': 0.15, 'volume': 0.1},
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
        volumes = hist['volume'].values.astype(float) if 'volume' in hist.columns else None
        highs = hist['high'].values.astype(float) if 'high' in hist.columns else None
        lows = hist['low'].values.astype(float) if 'low' in hist.columns else None
        # 按日期排序
        sort_idx = np.argsort(dates)
        closes = closes[sort_idx]
        dates = dates[sort_idx]
        if volumes is not None:
            volumes = volumes[sort_idx]
        if highs is not None:
            highs = highs[sort_idx]
        if lows is not None:
            lows = lows[sort_idx]

        stock_data.append((sina_code, closes, dates, volumes, highs, lows))

    print(f"  有历史数据的证券: {len(stock_data)}")

    if len(stock_data) < top_n:
        print(f"  ✗ 数据不足 TOP {top_n}，无法回测")
        return result

    # ---- 找共同日期范围 ----
    all_dates_set = None
    for _, _, dates, _, _, _ in stock_data:
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
    for sina_code, closes, dates, volumes, highs, lows in stock_data:
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
                    for sina_code, closes, dates, volumes, highs, lows in stock_data:
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
        for sina_code, closes, dates, volumes, highs, lows in stock_data:
            idx = find_idx(sina_code, rb_date)
            if idx is None or idx < 60:
                continue

            # 截取到该日的数据
            truncated_closes = closes[:idx + 1]
            truncated_vols = volumes[:idx + 1] if volumes is not None else None
            truncated_highs = highs[:idx + 1] if highs is not None else None
            truncated_lows = lows[:idx + 1] if lows is not None else None
            score = compute_factors_at_date(
                truncated_closes, strategy,
                volumes=truncated_vols, highs=truncated_highs, lows=truncated_lows
            )
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
            for sina_code, closes, dates, volumes, highs, lows in stock_data:
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

def _generate_swing_charts(result):
    """生成波段回测图表 (dict结构 from run_swing_backtest) — ECharts"""
    from quantlab.reports.echarts import echarts_script, UP, DOWN, GRID, TEXT, BLUE, GRAY
    charts = []

    equity_curve = result.get('equity_curve', [])
    if len(equity_curve) >= 2:
        dates = [pd.Timestamp(e[0]).strftime('%Y-%m-%d') for e in equity_curve]
        values = [e[1] for e in equity_curve]
        initial = result.get('initial_capital', values[0])
        nav = [v / initial for v in values]

        buy_pts, sell_pts = [], []
        trades = result.get('trades', [])
        for t in trades:
            d = pd.Timestamp(t.date).strftime('%Y-%m-%d')
            if d in dates:
                idx = dates.index(d)
                if t.direction == 'BUY':
                    buy_pts.append({'value': [d, round(nav[idx], 4)], 'code': t.code, 'price': t.price, 'reason': t.reason})
                else:
                    sell_pts.append({'value': [d, round(nav[idx], 4)], 'code': t.code, 'price': t.price, 'pnl': round(t.pnl_pct * 100, 1)})

        option = {
            'tooltip': {'trigger': 'axis', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                        'textStyle': {'color': '#1f2329'}},
            'legend': {'top': 0, 'textStyle': {'color': TEXT}},
            'grid': {'left': 55, 'right': 20, 'top': 36, 'bottom': 60},
            'xAxis': {'type': 'category', 'data': dates, 'boundaryGap': False,
                      'axisLine': {'lineStyle': {'color': '#d9dde3'}}, 'axisLabel': {'color': TEXT}},
            'yAxis': {'type': 'value', 'scale': True, 'splitLine': {'lineStyle': {'color': GRID}},
                      'axisLabel': {'color': TEXT}},
            'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 16, 'bottom': 10}],
            'series': [
                {'name': '策略净值', 'type': 'line', 'data': nav, 'showSymbol': False, 'smooth': True,
                 'lineStyle': {'color': BLUE, 'width': 2.5},
                 'areaStyle': {'color': 'rgba(44,111,187,0.08)'}},
                {'name': '买入', 'type': 'scatter', 'data': buy_pts, 'symbol': 'triangle', 'symbolSize': 10,
                 'itemStyle': {'color': DOWN}},
                {'name': '卖出', 'type': 'scatter', 'data': sell_pts, 'symbol': 'triangle', 'symbolRotate': 180,
                 'symbolSize': 10, 'itemStyle': {'color': UP}},
            ],
        }
        charts.append(('累计净值', echarts_script('bt_nav', option, 340)))

    # 交易盈亏分布
    trades = result.get('trades', [])
    sell_trades = [t for t in trades if t.direction == 'SELL']
    if sell_trades:
        pnls = [round(t.pnl_pct * 100, 1) for t in sell_trades]
        option = {
            'tooltip': {'trigger': 'axis', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                        'textStyle': {'color': '#1f2329'}},
            'grid': {'left': 55, 'right': 20, 'top': 30, 'bottom': 40},
            'xAxis': {'type': 'category', 'data': list(range(len(pnls))),
                      'axisLine': {'lineStyle': {'color': '#d9dde3'}}, 'axisLabel': {'color': TEXT, 'show': False}},
            'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'color': GRID}},
                      'axisLabel': {'color': TEXT, 'formatter': 'function(v){return v + "%";}'}},
            'series': [{
                'type': 'bar', 'data': pnls, 'barWidth': '60%',
                'itemStyle': {'color': 'function(p){return p.value >= 0 ? "#e8403a" : "#1ba27a";}'},
            }],
        }
        stats = result.get('stats', {})
        charts.append((f'盈亏分布 (共{len(sell_trades)}笔, 胜率{stats.get("win_rate", 0):.0%})',
                       echarts_script('bt_pnl', option, 300)))

    return charts

def generate_backtest_charts(result):
    """生成回测图表（ECharts HTML片段列表）"""
    # 处理dict结构 (from run_swing_backtest / backtest_single_stock)
    if isinstance(result, dict):
        return _generate_swing_charts(result)

    from quantlab.reports.echarts import echarts_script, UP, DOWN, GRID, TEXT, BLUE, GRAY
    charts = []
    colors = {'strategy': BLUE, 'benchmark': GRAY}

    for hp, ps in sorted(result.periods.items()):
        bench = result.benchmark_periods.get(hp)
        if not ps.cumulative_nav or len(ps.cumulative_nav) < 2:
            continue
        option = {
            'tooltip': {'trigger': 'axis', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                        'textStyle': {'color': '#1f2329'}},
            'legend': {'top': 0, 'textStyle': {'color': TEXT}},
            'grid': {'left': 55, 'right': 20, 'top': 36, 'bottom': 40},
            'xAxis': {'type': 'category', 'data': list(range(len(ps.cumulative_nav))),
                      'axisLine': {'lineStyle': {'color': '#d9dde3'}}, 'axisLabel': {'color': TEXT}},
            'yAxis': {'type': 'value', 'scale': True, 'splitLine': {'lineStyle': {'color': GRID}},
                      'axisLabel': {'color': TEXT}},
            'series': [
                {'name': f'{result.strategy_name}策略', 'type': 'line', 'data': ps.cumulative_nav,
                 'showSymbol': False, 'lineStyle': {'color': colors['strategy'], 'width': 2}},
            ],
        }
        if bench and bench.cumulative_nav:
            option['series'].append({'name': '基准 (全市场等权)', 'type': 'line',
                                     'data': bench.cumulative_nav, 'showSymbol': False,
                                     'lineStyle': {'color': colors['benchmark'], 'width': 2, 'type': 'dashed'}})
        charts.append((f'累计净值 ({hp}日)', echarts_script(f'btnav_{hp}', option, 320)))

    # 胜率对比
    periods = sorted(result.periods.keys())
    if periods:
        strategy_wr = [round(result.periods[hp].win_rate * 100, 1) for hp in periods]
        benchmark_wr = [round(result.benchmark_periods[hp].win_rate * 100, 1)
                        if hp in result.benchmark_periods else 0 for hp in periods]
        option = {
            'tooltip': {'trigger': 'axis', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                        'textStyle': {'color': '#1f2329'}},
            'legend': {'top': 0, 'textStyle': {'color': TEXT}},
            'grid': {'left': 55, 'right': 20, 'top': 36, 'bottom': 40},
            'xAxis': {'type': 'category', 'data': [f'{hp}日' for hp in periods],
                      'axisLine': {'lineStyle': {'color': '#d9dde3'}}, 'axisLabel': {'color': TEXT}},
            'yAxis': {'type': 'value', 'min': 0, 'max': 100, 'splitLine': {'lineStyle': {'color': GRID}},
                      'axisLabel': {'color': TEXT, 'formatter': 'function(v){return v + "%";}'}},
            'series': [
                {'name': '策略胜率', 'type': 'bar', 'data': strategy_wr,
                 'itemStyle': {'color': colors['strategy'], 'borderRadius': [4, 4, 0, 0]},
                 'label': {'show': True, 'position': 'top', 'color': TEXT,
                           'formatter': 'function(p){return p.value + "%";}'}},
                {'name': '基准胜率', 'type': 'bar', 'data': benchmark_wr,
                 'itemStyle': {'color': colors['benchmark'], 'borderRadius': [4, 4, 0, 0]}},
            ],
        }
        charts.append(('胜率对比', echarts_script('bt_win', option, 300)))

    return charts
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

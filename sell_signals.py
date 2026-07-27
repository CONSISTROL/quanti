"""
A股多因子量化选股 - 卖出信号模块
基于 MACD/SKDJ/成交量/均线 给出卖出建议
"""

import numpy as np
import pandas as pd


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


def compute_sell_signals(code, sina_code, hist_df):
    """
    计算单只股票的卖出信号

    返回: {
        'signals': [(type, strength, description), ...],
        'overall': 'strong_sell' / 'sell' / 'hold' / 'buy',
        'score': float (越高越应该卖出, -3~+3)
    }
    """
    signals = []

    if hist_df is None or len(hist_df) < 60:
        return {'signals': [], 'overall': 'hold', 'score': 0}

    closes = hist_df['close'].values.astype(float)
    volumes = hist_df['volume'].values.astype(float) if 'volume' in hist_df.columns else None
    highs = hist_df['high'].values.astype(float) if 'high' in hist_df.columns else None
    lows = hist_df['low'].values.astype(float) if 'low' in hist_df.columns else None
    n = len(closes)
    score = 0

    # ---- 1. MACD 死叉信号 ----
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12 - ema26
    valid_dif = dif[~np.isnan(dif)]
    if len(valid_dif) >= 9:
        dea_full = _ema(valid_dif, 9)
        if len(dea_full) >= 4:
            dif_now = valid_dif[-1]
            dea_now = dea_full[-1]
            macd_hist = (dif_now - dea_now) * 2

            # 死叉检测 (最近3天)
            for lag in range(1, 4):
                if lag < len(valid_dif) and lag < len(dea_full):
                    prev_dif = valid_dif[-(lag + 1)]
                    prev_dea = dea_full[-(lag + 1)]
                    curr_dif = valid_dif[-lag]
                    curr_dea = dea_full[-lag]
                    if not any(np.isnan(x) for x in [prev_dif, prev_dea, curr_dif, curr_dea]):
                        if prev_dif >= prev_dea and curr_dif < curr_dea:
                            if dif_now > 0:
                                signals.append(('MACD', 2, '零轴上方死叉，短线回调'))
                                score += 1.5
                            else:
                                signals.append(('MACD', 3, '零轴下方死叉，趋势走弱'))
                                score += 2.5
                            break

            # MACD柱状线持续缩短 (多头衰竭)
            if len(dea_full) >= 3 and macd_hist > 0:
                h1 = (valid_dif[-1] - dea_full[-1]) * 2
                h2 = (valid_dif[-2] - dea_full[-2]) * 2
                h3 = (valid_dif[-3] - dea_full[-3]) * 2
                if h1 < h2 < h3 and h1 > 0:
                    signals.append(('MACD', 1, '多头柱状线持续缩短，动能衰减'))
                    score += 0.5

    # ---- 2. SKDJ 超买/死叉信号 ----
    if highs is not None and lows is not None and n >= 18:
        k_prev, d_prev = 50.0, 50.0
        k_values, d_values = [], []
        for i in range(8, n):
            wh = np.max(highs[i-8:i+1])
            wl = np.min(lows[i-8:i+1])
            rsv = (closes[i] - wl) / (wh - wl) * 100 if wh != wl else 50
            k = 2/3 * k_prev + 1/3 * rsv
            d = 2/3 * d_prev + 1/3 * k
            k_values.append(k)
            d_values.append(d)
            k_prev, d_prev = k, d

        if len(k_values) >= 4:
            k_now = k_values[-1]
            d_now = d_values[-1]
            j_now = 3 * k_now - 2 * d_now

            # 超买区死叉
            for lag in range(1, 4):
                if lag < len(k_values):
                    if (k_values[-(lag+1)] >= d_values[-(lag+1)] and
                            k_values[-lag] < d_values[-lag]):
                        if k_now > 80:
                            signals.append(('SKDJ', 3, f'超买区死叉 (K={k_now:.0f})，强烈卖出'))
                            score += 2.5
                        elif k_now > 50:
                            signals.append(('SKDJ', 2, f'中高位死叉 (K={k_now:.0f})，注意风险'))
                            score += 1.5
                        break

            # J值超买
            if j_now > 100:
                signals.append(('SKDJ', 1, f'J值超买 ({j_now:.0f})，短期可能回调'))
                score += 0.5

    # ---- 3. 放量下跌信号 ----
    if volumes is not None and n >= 20:
        vol_5 = np.mean(volumes[-5:])
        vol_20 = np.mean(volumes[-20:])
        vol_ratio = vol_5 / vol_20 if vol_20 > 0 else 1
        price_chg_5d = (closes[-1] / closes[-6]) - 1 if n >= 6 else 0

        if vol_ratio > 2.0 and price_chg_5d < -0.03:
            signals.append(('成交量', 3, f'放量下跌 (量比{vol_ratio:.1f}x, 跌{price_chg_5d:.1%})，主力出逃'))
            score += 2.5
        elif vol_ratio > 1.5 and price_chg_5d < -0.02:
            signals.append(('成交量', 2, f'温和放量下跌 (量比{vol_ratio:.1f}x)，抛压增加'))
            score += 1.5
        elif vol_ratio > 3.0 and price_chg_5d > 0.05:
            signals.append(('成交量', 1, f'高位放量 (量比{vol_ratio:.1f}x)，可能见顶'))
            score += 1.0

    # ---- 4. 均线破位信号 ----
    ma20 = np.mean(closes[-20:]) if n >= 20 else closes[-1]
    ma60 = np.mean(closes[-60:]) if n >= 60 else closes[-1]

    if closes[-1] < ma60 and closes[-2] >= np.mean(closes[-61:-1]):
        signals.append(('均线', 3, '跌破60日均线，趋势转弱'))
        score += 2.0
    elif closes[-1] < ma20 and closes[-1] < ma60:
        signals.append(('均线', 1, '价格在20日和60日均线下方，弱势'))
        score += 0.5

    # ---- 综合判断 ----
    if score >= 4:
        overall = 'strong_sell'
    elif score >= 2:
        overall = 'sell'
    elif score >= 1:
        overall = 'watch'
    else:
        overall = 'hold'

    return {
        'signals': signals,
        'overall': overall,
        'score': round(score, 1),
    }


def batch_sell_signals(scored_df, history_dict, top_n=50):
    """
    批量计算卖出信号

    返回: scored_df新增列 sell_signals, sell_overall, sell_score
    """
    # 构建 pure_code → sina_code 映射
    pure_to_sina = {}
    for sina_code in history_dict:
        pure = _code_pure(sina_code)
        pure_to_sina[pure] = sina_code

    print(f"  计算卖出信号: TOP {min(top_n, len(scored_df))} 只证券...")

    results = []
    for idx, row in scored_df.head(top_n).iterrows():
        code = str(row.get('code', '')).zfill(6)
        sina = pure_to_sina.get(code)
        hist = history_dict.get(sina) if sina else None

        result = compute_sell_signals(code, sina, hist)
        results.append(result)

    # 合并到scored_df
    for col in ['signals', 'overall', 'score']:
        col_name = f'sell_{col}'
        vals = [r[col] for r in results]
        # 补齐剩余行
        vals.extend([[] if col == 'signals' else 'hold' if col == 'overall' else 0]
                     * (len(scored_df) - len(results)))
        scored_df[col_name] = vals[:len(scored_df)]

    # 统计
    n_sell = sum(1 for r in results if r['overall'] in ('sell', 'strong_sell'))
    n_watch = sum(1 for r in results if r['overall'] == 'watch')
    print(f"  ✓ 卖出信号: {n_sell} 只建议卖出, {n_watch} 只需关注, "
          f"{len(results) - n_sell - n_watch} 只继续持有")

    return scored_df


def print_sell_signals(scored_df, top_n=20):
    """在终端打印卖出建议"""
    if 'sell_overall' not in scored_df.columns:
        return

    sell_stocks = scored_df[scored_df['sell_overall'].isin(
        ['strong_sell', 'sell', 'watch'])].head(top_n)

    if sell_stocks.empty:
        print("\n  ✅ TOP 证券均无卖出信号，建议继续持有")
        return

    print(f"\n  ⚠️  卖出/关注建议 ({len(sell_stocks)} 只):\n")
    print(f"  {'代码':<8} {'名称':<10} {'现价':>8} {'信号':>6} "
          f"{'详情'}")
    print("  " + "─" * 80)

    labels = {
        'strong_sell': '🔴清仓',
        'sell': '🟠减仓',
        'watch': '🟡关注',
    }

    for _, row in sell_stocks.iterrows():
        code = str(row.get('code', '')).zfill(6)
        name = str(row.get('name', ''))
        price = row.get('price', 0)
        overall = row.get('sell_overall', 'hold')
        label = labels.get(overall, '持有')
        signals = row.get('sell_signals', [])

        detail = '; '.join([s[2] for s in signals[:2]]) if signals else '-'

        print(f"  {code:<8} {name:<10} {price:>8.2f} {label:>6} {detail}")

    print("  " + "─" * 80)
    print("  ⚠️  以上信号仅供参考，请结合基本面和大盘环境综合判断。")
    print()

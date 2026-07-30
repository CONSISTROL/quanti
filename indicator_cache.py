"""
指标预计算与缓存 (增量计算版)
对每只股票只遍历一次历史数据, 增量计算所有日期的指标
首次计算后缓存到文件, 后续秒加载
"""

import os
import pickle
import numpy as np
import pandas as pd
from tqdm import tqdm


def _ema_incremental(prev_ema, new_val, period):
    """增量EMA计算"""
    alpha = 2.0 / (period + 1)
    return alpha * new_val + (1 - alpha) * prev_ema


def _incremental_indicators(closes, volumes, highs, lows, dates_arr, target_dates_set):
    """
    增量计算一只股票在所有日期上的指标 (只遍历一次)

    返回: {date_str: {k, d, j, cross, vol_ratio, ma60, ma120, close, ret_5d}}
    """
    n = len(closes)
    if n < 120:
        return {}

    result = {}

    # 预计算SKDJ
    k_vals = np.full(n, 50.0)
    d_vals = np.full(n, 50.0)
    j_vals = np.full(n, 50.0)
    cross_vals = np.zeros(n, dtype=int)

    # SKDJ参数
    skdj_period = 9

    for i in range(skdj_period - 1, n):
        window_high = np.max(highs[i - skdj_period + 1:i + 1])
        window_low = np.min(lows[i - skdj_period + 1:i + 1])

        if window_high == window_low:
            rsv = 50.0
        else:
            rsv = (closes[i] - window_low) / (window_high - window_low) * 100

        if i == skdj_period - 1:
            k_vals[i] = rsv
            d_vals[i] = k_vals[i]
        else:
            k_vals[i] = 2/3 * k_vals[i-1] + 1/3 * rsv
            d_vals[i] = 2/3 * d_vals[i-1] + 1/3 * k_vals[i]
        j_vals[i] = 3 * k_vals[i] - 2 * d_vals[i]

        # 金叉/死叉检测
        if i >= skdj_period:
            prev_k = k_vals[i-1]
            prev_d = d_vals[i-1]
            curr_k = k_vals[i]
            curr_d = d_vals[i]
            if prev_k <= prev_d and curr_k > curr_d:
                cross_vals[i] = 1  # 金叉
            elif prev_k >= prev_d and curr_k < curr_d:
                cross_vals[i] = -1  # 死叉

    # MA5, MA20, MA60, MA120 (用cumsum加速)
    cumsum = np.cumsum(closes)
    ma5_vals = np.full(n, np.nan)
    ma20_vals = np.full(n, np.nan)
    ma60_vals = np.full(n, np.nan)
    ma120_vals = np.full(n, np.nan)
    for i in range(4, n):
        ma5_vals[i] = (cumsum[i] - (cumsum[i-5] if i >= 5 else 0)) / 5
    for i in range(19, n):
        ma20_vals[i] = (cumsum[i] - (cumsum[i-20] if i >= 20 else 0)) / 20
    for i in range(59, n):
        ma60_vals[i] = (cumsum[i] - (cumsum[i-60] if i >= 60 else 0)) / 60
    for i in range(119, n):
        ma120_vals[i] = (cumsum[i] - (cumsum[i-120] if i >= 120 else 0)) / 120

    # 成交量均线
    vol_ratio_vals = np.full(n, 1.0)
    if volumes is not None and len(volumes) >= 20:
        vol_cumsum = np.cumsum(volumes)
        vol5 = np.full(n, np.nan)
        vol20 = np.full(n, np.nan)
        for i in range(4, n):
            vol5[i] = (vol_cumsum[i] - (vol_cumsum[i-5] if i >= 5 else 0)) / 5
        for i in range(19, n):
            vol20[i] = (vol_cumsum[i] - (vol_cumsum[i-20] if i >= 20 else 0)) / 20
        for i in range(19, n):
            if vol20[i] and vol20[i] > 0:
                vol_ratio_vals[i] = vol5[i] / vol20[i]

    # ret_5d, ret_60d
    ret5d_vals = np.zeros(n)
    ret60d_vals = np.zeros(n)
    for i in range(5, n):
        if closes[i-5] > 0:
            ret5d_vals[i] = closes[i] / closes[i-5] - 1
    for i in range(60, n):
        if closes[i-60] > 0:
            ret60d_vals[i] = closes[i] / closes[i-60] - 1

    # MACD (EMA12, EMA26, DIF, DEA, MACD柱)
    ema12_vals = np.full(n, np.nan)
    ema26_vals = np.full(n, np.nan)
    dif_vals = np.full(n, np.nan)
    dea_vals = np.full(n, np.nan)
    macd_hist_vals = np.full(n, np.nan)
    macd_cross_vals = np.zeros(n, dtype=int)

    # 计算EMA12和EMA26
    if n >= 26:
        ema12_vals[11] = np.mean(closes[:12])
        for i in range(12, n):
            ema12_vals[i] = _ema_incremental(ema12_vals[i-1], closes[i], 12)

        ema26_vals[25] = np.mean(closes[:26])
        for i in range(26, n):
            ema26_vals[i] = _ema_incremental(ema26_vals[i-1], closes[i], 26)

        # DIF = EMA12 - EMA26
        for i in range(25, n):
            if not np.isnan(ema12_vals[i]) and not np.isnan(ema26_vals[i]):
                dif_vals[i] = ema12_vals[i] - ema26_vals[i]

        # DEA = EMA9(DIF)
        dif_valid = dif_vals[~np.isnan(dif_vals)]
        if len(dif_valid) >= 9:
            dea_start = np.where(~np.isnan(dif_vals))[0][0]
            dea_vals[dea_start + 8] = np.mean(dif_vals[dea_start:dea_start + 9])
            for i in range(dea_start + 9, n):
                if not np.isnan(dif_vals[i]) and not np.isnan(dea_vals[i-1]):
                    dea_vals[i] = _ema_incremental(dea_vals[i-1], dif_vals[i], 9)

            # MACD柱 = (DIF - DEA) * 2
            for i in range(dea_start + 8, n):
                if not np.isnan(dif_vals[i]) and not np.isnan(dea_vals[i]):
                    macd_hist_vals[i] = (dif_vals[i] - dea_vals[i]) * 2

            # MACD金叉/死叉检测
            for i in range(1, n):
                if not np.isnan(dif_vals[i]) and not np.isnan(dif_vals[i-1]) and \
                   not np.isnan(dea_vals[i]) and not np.isnan(dea_vals[i-1]):
                    if dif_vals[i-1] <= dea_vals[i-1] and dif_vals[i] > dea_vals[i]:
                        macd_cross_vals[i] = 1  # 金叉
                    elif dif_vals[i-1] >= dea_vals[i-1] and dif_vals[i] < dea_vals[i]:
                        macd_cross_vals[i] = -1  # 死叉

    # 构建日期→索引映射
    date_to_idx = {}
    for i in range(120, n):
        d_str = pd.Timestamp(dates_arr[i]).strftime('%Y-%m-%d')
        if d_str in target_dates_set:
            date_to_idx[d_str] = i

    # 提取目标日期的指标
    for d_str, idx in date_to_idx.items():
        # 近3天是否有SKDJ交叉 (因为金叉可能在当天或前1-2天)
        skdj_cross = 0
        for lag in range(0, min(3, idx)):
            if cross_vals[idx - lag] != 0:
                skdj_cross = cross_vals[idx - lag]
                break

        # 近3天是否有MACD交叉
        macd_cross = 0
        for lag in range(0, min(3, idx)):
            if macd_cross_vals[idx - lag] != 0:
                macd_cross = macd_cross_vals[idx - lag]
                break

        result[d_str] = {
            'skdj_k': float(k_vals[idx]),
            'skdj_d': float(d_vals[idx]),
            'skdj_j': float(j_vals[idx]),
            'skdj_cross': int(skdj_cross),
            'macd_cross': int(macd_cross),
            'dif': float(dif_vals[idx]) if not np.isnan(dif_vals[idx]) else 0,
            'dea': float(dea_vals[idx]) if not np.isnan(dea_vals[idx]) else 0,
            'macd_hist': float(macd_hist_vals[idx]) if not np.isnan(macd_hist_vals[idx]) else 0,
            'vol_ratio': float(vol_ratio_vals[idx]),
            'ma5': float(ma5_vals[idx]) if not np.isnan(ma5_vals[idx]) else 0,
            'ma5_prev': float(ma5_vals[idx-1]) if idx > 0 and not np.isnan(ma5_vals[idx-1]) else 0,
            'ma20': float(ma20_vals[idx]) if not np.isnan(ma20_vals[idx]) else 0,
            'ma60': float(ma60_vals[idx]) if not np.isnan(ma60_vals[idx]) else 0,
            'ma120': float(ma120_vals[idx]) if not np.isnan(ma120_vals[idx]) else 0,
            'close': float(closes[idx]),
            'skdj_close': float(closes[idx]),
            'ret_5d': float(ret5d_vals[idx]),
            'ret_60d': float(ret60d_vals[idx]),
        }

    return result


def precompute_all_indicators(history_dict, pure_to_sina, trading_dates,
                              cache_dir='cache', start_date='2025-01-01',
                              end_date='2026-07-27', use_cache=True):
    """
    预计算所有股票所有交易日的技术指标 (增量计算版)

    每只股票只遍历一次历史, 比逐日期调用compute_indicators快10-50倍
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f'indicators_{start_date}_{end_date}.pkl')

    # 加载缓存
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, 'rb') as f:
                cached = pickle.load(f)
            # 检查覆盖度
            sample = next(iter(cached.values()), {})
            if len(sample) >= len(trading_dates) * 0.9:
                print(f"  📦 缓存命中: {len(cached)} 只股票, {len(sample)} 个交易日")
                return cached
            else:
                print(f"  📦 缓存部分命中, 增量更新...")
        except Exception:
            cached = {}
    else:
        cached = {}

    # 目标日期集合
    target_dates = set()
    for td in trading_dates:
        target_dates.add(pd.Timestamp(td).strftime('%Y-%m-%d'))

    # 增量计算
    print(f"  🔧 增量预计算: {len(pure_to_sina)} 只股票...")
    result = dict(cached)

    for code, sina in tqdm(pure_to_sina.items(), desc="  预计算", ncols=80):
        if code in result and len(result[code]) >= len(target_dates) * 0.9:
            continue  # 已有完整数据

        hist = history_dict.get(sina)
        if hist is None:
            continue
        if 'close' not in hist.columns or 'date' not in hist.columns:
            continue

        closes = hist['close'].values.astype(float)
        dates_arr = hist['date'].values
        volumes = hist['volume'].values.astype(float) if 'volume' in hist.columns else None
        highs = hist['high'].values.astype(float) if 'high' in hist.columns else None
        lows = hist['low'].values.astype(float) if 'low' in hist.columns else None

        if highs is None:
            highs = closes.copy()
        if lows is None:
            lows = closes.copy()

        if len(closes) < 120:
            continue

        code_data = _incremental_indicators(closes, volumes, highs, lows, dates_arr, target_dates)
        if code_data:
            result[code] = code_data

    # 保存缓存
    print(f"  💾 保存缓存: {len(result)} 只股票 → {cache_path}")
    with open(cache_path, 'wb') as f:
        pickle.dump(result, f)

    return result

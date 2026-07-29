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

    # MA60, MA120 (用cumsum加速)
    cumsum = np.cumsum(closes)
    ma60_vals = np.full(n, np.nan)
    ma120_vals = np.full(n, np.nan)
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

    # ret_5d
    ret5d_vals = np.zeros(n)
    for i in range(5, n):
        if closes[i-5] > 0:
            ret5d_vals[i] = closes[i] / closes[i-5] - 1

    # 构建日期→索引映射
    date_to_idx = {}
    for i in range(120, n):
        d_str = pd.Timestamp(dates_arr[i]).strftime('%Y-%m-%d')
        if d_str in target_dates_set:
            date_to_idx[d_str] = i

    # 提取目标日期的指标
    for d_str, idx in date_to_idx.items():
        # 近3天是否有交叉 (因为金叉可能在当天或前1-2天)
        cross = 0
        for lag in range(0, min(3, idx)):
            if cross_vals[idx - lag] != 0:
                cross = cross_vals[idx - lag]
                break

        result[d_str] = {
            'k': float(k_vals[idx]),
            'd': float(d_vals[idx]),
            'j': float(j_vals[idx]),
            'cross': int(cross),
            'vol_ratio': float(vol_ratio_vals[idx]),
            'ma60': float(ma60_vals[idx]) if not np.isnan(ma60_vals[idx]) else 0,
            'ma120': float(ma120_vals[idx]) if not np.isnan(ma120_vals[idx]) else 0,
            'close': float(closes[idx]),
            'ret_5d': float(ret5d_vals[idx]),
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

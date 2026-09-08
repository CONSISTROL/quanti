"""
指标预计算 — 直接调用 quantlab.indicator_cache._incremental_indicators（旧系统同款函数，
保证信号层逐日一致）。返回 {date_str: ind_dict}。
"""
from __future__ import annotations

from datetime import date
from typing import Iterable

import pandas as pd

from .legacy_access import ensure_legacy_importable


def build_ind_map(df: pd.DataFrame, window_dates: Iterable) -> dict[str, dict]:
    """
    与 legacy quantlab/trading_engine.backtest_single_stock 相同的指标构建路径：
      _incremental_indicators(closes, volumes, highs, lows, dates_arr, target_dates_set)
    df 为前复权日线（date/open/high/low/close/volume），window_dates 为回测窗口交易日。
    """
    ensure_legacy_importable()
    from quantlab.indicator_cache import _incremental_indicators

    dates_arr = df["date"].values
    closes = df["close"].values.astype(float)
    volumes = df["volume"].values.astype(float) if "volume" in df.columns else None
    highs = df["high"].values.astype(float) if "high" in df.columns else None
    lows = df["low"].values.astype(float) if "low" in df.columns else None
    if highs is None:
        highs = closes.copy()
    if lows is None:
        lows = closes.copy()

    target = set()
    for d in window_dates:
        ts = pd.Timestamp(d)
        target.add(ts.strftime("%Y-%m-%d"))

    return _incremental_indicators(closes, volumes, highs, lows, dates_arr, target)


def window_trading_dates(df: pd.DataFrame, start_date: str, end_date: str) -> list:
    """窗口内的交易日（升序，pd.Timestamp）。"""
    start_ts = pd.Timestamp(start_date) if start_date else pd.Timestamp.min
    end_ts = pd.Timestamp(end_date) if end_date else pd.Timestamp.max
    dates = []
    for d in df["date"].values:
        ts = pd.Timestamp(d)
        if start_ts <= ts <= end_ts:
            dates.append(ts)
    return dates


def dkey(d) -> str:
    ts = pd.Timestamp(d)
    return ts.strftime("%Y-%m-%d")

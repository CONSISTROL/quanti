"""
cache/*.pkl → vnpy BarData / DataFrame 适配。

legacy 缓存两种形态：
  1. 自选池: cache/quantdash_watchlist_YYYYMMDD.pkl  -> {sina_code: DataFrame}
  2. 单股:   cache/kline_1d_601857.pkl              -> {'saved_at': dt, 'df': DataFrame}
DataFrame 列: date/open/high/low/close/volume/amount（前复权日线，date 升序）。
"""
from __future__ import annotations

import os
import pickle
from datetime import datetime
from typing import Iterable

import pandas as pd
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from .symbols import sina_to_vt, vt_to_parts

KLINE_COLS = ["date", "open", "high", "low", "close", "volume", "amount"]


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    """统一列、类型、排序，剔除坏行。返回新 DataFrame（不改原对象）。"""
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col not in out.columns:
            out[col] = 0.0 if col != "amount" else 0.0
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["close"])
    out = out[out["close"] > 0]
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return out.reset_index(drop=True)


def load_pkl_df(path: str) -> dict[str, pd.DataFrame]:
    """读取缓存 pkl，返回 {sina_code: 规范化 DataFrame}。"""
    with open(path, "rb") as f:
        obj = pickle.load(f)

    out: dict[str, pd.DataFrame] = {}
    if isinstance(obj, pd.DataFrame):
        out["df"] = normalize_df(obj)
    elif isinstance(obj, dict):
        if set(obj.keys()) == {"saved_at", "df"} and isinstance(obj["df"], pd.DataFrame):
            out["df"] = normalize_df(obj["df"])
        else:
            for k, v in obj.items():
                if isinstance(v, pd.DataFrame):
                    out[str(k)] = normalize_df(v)
    else:
        raise TypeError(f"无法识别的缓存结构: {type(obj)} @ {path}")

    # 无 sina 前缀的 key 也按 6 位代码补成 vt key 用（保留原始 key 以便查回）
    return out


def pick_cache_file(cache_dir: str, code6: str) -> str | None:
    """找含指定 6 位代码的缓存：优先单股 kline_1d_{code}.pkl。"""
    single = os.path.join(cache_dir, f"kline_1d_{code6}.pkl")
    if os.path.exists(single):
        return single
    if os.path.isdir(cache_dir):
        for fn in sorted(os.listdir(cache_dir), reverse=True):
            if fn.startswith(("quantdash_watchlist_", "sina_watchlist_", "hist_batch_")) \
                    and fn.endswith(".pkl"):
                p = os.path.join(cache_dir, fn)
                try:
                    frames = load_pkl_df(p)
                except Exception:
                    continue
                pure = code6.zfill(6)
                for sina_key in frames:
                    if sina_key.strip().lower().lstrip("shszbj").zfill(6) == pure:
                        return p
    return None


def load_symbol_df(cache_dir: str, code6: str, prefer_watchlist: str | None = None) \
        -> tuple[pd.DataFrame, str]:
    """
    加载某 6 位代码的历史日线。
    返回 (df, sina_code)。sina_code 用于 legacy 端同源（quantlab 历史 key 形态 sh601857）。
    优先 prefer_watchlist 指定 pkl；否则自动探测。
    """
    paths: list[str] = []
    if prefer_watchlist and os.path.exists(prefer_watchlist):
        paths.append(prefer_watchlist)
    f = pick_cache_file(cache_dir, code6)
    if f and f not in paths:
        paths.append(f)

    pure = code6.zfill(6)
    for p in paths:
        frames = load_pkl_df(p)
        for sina_key, df in frames.items():
            k = sina_key.strip().lower().lstrip("shszbj").zfill(6)
            if k == pure:
                return df, sina_key
    raise FileNotFoundError(
        f"缓存中找不到 {pure} 的历史数据 (cache_dir={cache_dir})，请先用旧系统采集")


def df_of_vt(vt_symbol: str, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """按 vt_symbol 从 {sina_key: df} 取 df。"""
    code, _ = vt_to_parts(vt_symbol)
    pure = code.zfill(6)
    for sina_key, df in frames.items():
        if sina_key.strip().lower().lstrip("shszbj").zfill(6) == pure:
            return df
    raise KeyError(f"{vt_symbol} 不在数据中")


def bars_from_df(vt_symbol: str, df: pd.DataFrame,
                 interval: Interval = Interval.DAILY) -> list[BarData]:
    """DataFrame → vnpy BarData 列表（date 升序，逐行逐字段复制 = G1 全等）。"""
    code, exchange = vt_to_parts(vt_symbol)
    bars: list[BarData] = []
    for row in df.itertuples(index=False):
        d = row.date
        if isinstance(d, pd.Timestamp):
            d = d.to_pydatetime()
        dt = d.replace(hour=0, minute=0, second=0, microsecond=0) \
            if isinstance(d, datetime) else datetime.combine(d.date(), datetime.min.time())
        bars.append(BarData(
            gateway_name="QUANT",
            symbol=code,
            exchange=exchange,
            datetime=dt,
            interval=interval,
            volume=float(row.volume or 0),
            turnover=float(row.amount or 0),
            open_interest=0,
            open_price=float(row.open),
            high_price=float(row.high),
            low_price=float(row.low),
            close_price=float(row.close),
        ))
    return bars


def itertuples_cols(df: pd.DataFrame) -> Iterable[tuple]:
    """供读取行用（保持与 bars_from_df 相同顺序）。"""
    return df.itertuples(index=False)

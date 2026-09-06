#!/usr/bin/env python3
"""Crawl A-share 1-minute K-line and persist to local CSV history.

Free web APIs only provide recent 1-minute data (about 1-2 days). To build a
longer history, this script repeatedly fetches during trading time and appends
new bars to local CSV files under cache/1m_history/.

Usage:
    python crawl_1m.py                 # crawl once
    python crawl_1m.py --loop           # keep crawling every 60s
    python crawl_1m.py --code 002832    # crawl one code
    python crawl_1m.py --interval 60    # loop interval seconds
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.kline import _fetch_tencent_1m  # noqa: E402

OUT_DIR = ROOT / "cache" / "1m_history"


def _load_config():
    import json
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def _merge_and_save(code: str, df: pd.DataFrame) -> int:
    """Merge new bars into local CSV and return number of new bars."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{code}.csv"
    old_len = 0
    if path.exists():
        old = pd.read_csv(path, parse_dates=["datetime"])
        old_len = len(old)
        combined = pd.concat([old, df[["datetime", "open", "high", "low", "close", "volume"]]], ignore_index=True)
    else:
        combined = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
    combined = combined.drop_duplicates(subset="datetime", keep="last")
    combined = combined.sort_values("datetime").reset_index(drop=True)
    combined.to_csv(path, index=False, encoding="utf-8-sig")
    return max(0, len(combined) - old_len)


def crawl_once(codes: list[str]) -> None:
    total_new = 0
    for code in codes:
        code = str(code).zfill(6)
        try:
            df = _fetch_tencent_1m(code, max_bars=320, refresh=True)
            df["datetime"] = pd.to_datetime(df["date"])
            new = _merge_and_save(code, df)
            total_new += new
            print(f"[{datetime.now():%H:%M:%S}] {code}: {len(df)} bars, +{new} new")
        except Exception as e:
            print(f"[{datetime.now():%H:%M:%S}] {code} ERROR: {e}")
        time.sleep(0.5)
    return total_new


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--code", help="single code, otherwise use config watchlist")
    parser.add_argument("--loop", action="store_true", help="keep crawling")
    parser.add_argument("--interval", type=int, default=60, help="loop seconds")
    args = parser.parse_args()

    cfg = _load_config()
    codes = [args.code] if args.code else [str(c).zfill(6) for c in cfg.get("watchlist", [])]
    if not codes:
        print("No codes. Use --code or config watchlist.")
        return

    print(f"1m crawler output: {OUT_DIR}")
    if args.loop:
        while True:
            crawl_once(codes)
            print(f"sleeping {args.interval}s ...")
            time.sleep(args.interval)
    else:
        crawl_once(codes)
        print("done.")


if __name__ == "__main__":
    main()

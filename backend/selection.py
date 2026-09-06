"""Stock selection service for the Web Console."""
from __future__ import annotations

import os
import sys
import math
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _py(value: Any) -> Any:
    """Convert numpy/pandas scalar to Python scalar."""
    import numpy as np
    import pandas as pd
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        v = float(value)
        return None if math.isnan(v) or math.isinf(v) else round(v, 6)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def run_stock_selection(config: dict, top: int | None = None) -> dict:
    """Run full-market multi-factor selection and return top-N records."""
    data_cfg = config.get("data", {})
    sc_cfg = config.get("scoring", {})

    from quantlab.data_fetcher import fetch_all_data

    class Args:
        pass

    args = Args()
    args.cache_dir = data_cfg.get("cache_dir", "cache")
    args.no_cache = False
    args.no_history = False
    args.hist_days = data_cfg.get("hist_days", 1200)
    args.workers = data_cfg.get("workers", 8)
    args.sleep = data_cfg.get("sleep", 0.15)
    args.include_etf_lof = data_cfg.get("include_etf_lof", False)
    args.exclude_gem = data_cfg.get("exclude_gem", False)
    args.exclude_star = data_cfg.get("exclude_star", False)

    data = fetch_all_data(args)

    from quantlab.factor_model import calculate_all_factors, score_stocks
    weights_str = sc_cfg.get("weights", "0.25,0.20,0.25,0.20,0.10")
    w_vals = [float(x) for x in weights_str.split(",")]
    if abs(sum(w_vals) - 1.0) > 0.01:
        w_vals = [v / sum(w_vals) for v in w_vals]
    weights = dict(zip(["value", "growth", "quality", "momentum", "risk"], w_vals))

    factor_df = calculate_all_factors(
        data["spot_filtered"],
        data["financial"],
        data["financial_prev"],
        data["history"],
        data.get("sector_map", {}),
        data.get("asset_type_map", {}),
    )
    scored_df = score_stocks(factor_df, weights)
    scored_df = scored_df[scored_df["composite_score"].notna()].copy()

    top = int(top or sc_cfg.get("top", 30))
    head = scored_df.head(top)
    records = []
    for _, row in head.iterrows():
        rec = {col: _py(row.get(col)) for col in head.columns}
        records.append(rec)

    return {
        "mode": "selection",
        "total": int(len(scored_df)),
        "top": top,
        "records": records,
        "columns": [str(c) for c in head.columns],
    }

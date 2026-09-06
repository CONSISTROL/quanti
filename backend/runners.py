"""Backtest runners used by the web console.

These functions intentionally reuse the same modules/strategies as the CLI,
but return JSON-friendly structured results so the Vue frontend can render
charts/tables without parsing terminal output or HTML.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import pandas as pd

# Make repository root importable when backend is launched from anywhere.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.serializers import (  # noqa: E402
    backtest_result_to_dict,
    position_to_dict,
    stats_to_dict,
    trades_to_list,
    equity_curve_to_list,
)

WATCH_NAMES = {
    "601857": "中国石油",
    "159381": "创业板AI ETF",
    "588170": "科创半导体ETF",
    "600547": "山东黄金",
    "513580": "恒生科技ETF",
    "159941": "纳指ETF广发",
    "160723": "嘉实原油LOF",
}


def _pure(code: Any) -> str:
    from quantlab.data_fetcher import _code_pure
    return _code_pure(code)


def _find_sina_key(hist: dict, code: str):
    code = str(code).zfill(6)
    for k in hist:
        if _pure(k) == code:
            return k
    return None


def _apply_strategy(config: dict, strategy: str | None) -> dict:
    """Return a shallow-copied trading config with strategy override applied."""
    from quantlab.cli.main import apply_strategy_override
    cfg = dict(config)
    trading = dict(cfg.get("trading", {}))
    if strategy:
        trading = apply_strategy_override(trading, strategy)
    cfg["trading"] = trading
    return cfg


def _load_data(hist: dict, precomputed: dict, code: str):
    """Get OHLC arrays from hist and precompute indicators for one code."""
    closes = hist["close"].values.astype(float)
    volumes = hist["volume"].values.astype(float) if "volume" in hist.columns else None
    highs = hist["high"].values.astype(float) if "high" in hist.columns else None
    lows = hist["low"].values.astype(float) if "low" in hist.columns else None
    if highs is None:
        highs = closes.copy()
    if lows is None:
        lows = closes.copy()
    dates_arr = hist["date"].values
    all_dates = set(pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates_arr)
    from quantlab.indicator_cache import _incremental_indicators
    precomputed[code] = _incremental_indicators(
        closes, volumes, highs, lows, dates_arr, all_dates
    )
    return closes, volumes, highs, lows


def _run_single_on_hist(hist, code, name, config, start, end, precomputed):
    """Run run_swing_backtest for a single code against an existing history dict."""
    from quantlab.trading_engine import run_swing_backtest
    single_cfg = dict(config)
    single_cfg["watchlist"] = [str(code).zfill(6)]
    single_cfg["max_positions"] = 1
    single_cfg["position_pct"] = 1.0
    single_cfg["kelly_mode"] = False

    import pandas as pd
    scored_df = pd.DataFrame([{
        "code": str(code).zfill(6),
        "name": name or str(code).zfill(6),
        "rank": 1,
        "composite_score": 1.0,
    }])
    return run_swing_backtest(
        hist, scored_df, single_cfg,
        start_date_str=start or "2025-01-01",
        end_date_str=end or "",
        precomputed={str(code).zfill(6): precomputed.get(str(code).zfill(6), {})},
    )


def _fetch_hist(config: dict, codes: list[str]) -> dict:
    data_cfg = config.get("data", {})
    from quantlab.data_sources import get_data_source
    ds = get_data_source(data_cfg.get("source", "quantdash"))
    return ds.fetch_watchlist_data(
        codes,
        cache_dir=data_cfg.get("cache_dir", "cache"),
        use_cache=not data_cfg.get("no_cache", False),
        max_bars=data_cfg.get("hist_days", 1200),
    )


def run_watchlist_backtest(config: dict, params: dict | None = None) -> dict:
    params = params or {}
    strategy = params.get("strategy") or config.get("trading", {}).get("strategy", "watchlist")
    config = _apply_strategy(config, strategy)
    cfg_trading = config["trading"]
    cfg_backtest = config.get("backtest", {})
    start = params.get("start_date") or cfg_backtest.get("start_date", "2025-01-01")
    end = params.get("end_date") or cfg_backtest.get("end_date", "")

    watchlist = [str(c).zfill(6) for c in (params.get("watchlist") or config.get("watchlist", []))]
    if not watchlist:
        raise ValueError("未配置 watchlist，无法运行自选池回测")

    hist = _fetch_hist(config, watchlist)

    # Build per-code indicators
    precomputed: dict[str, dict] = {}
    names: dict[str, str] = {}
    for code in watchlist:
        sina = _find_sina_key(hist, code)
        if sina is None:
            print(f"  ⚠ {code} 无历史数据，跳过")
            continue
        _load_data(hist[sina], precomputed, code)
        names[code] = WATCH_NAMES.get(code, code)

    if not precomputed:
        raise ValueError("没有可回测的标的（历史数据为空）")

    # Pool backtest (single full position, same as tests/watchlist_backtest)
    tr_cfg = dict(cfg_trading)
    tr_cfg["watchlist"] = list(precomputed.keys())
    tr_cfg["max_positions"] = 1
    tr_cfg["position_pct"] = 1.0
    tr_cfg["kelly_mode"] = False

    import pandas as pd
    scored_df = pd.DataFrame([
        {"code": c, "name": names[c], "rank": i + 1, "composite_score": 1.0}
        for i, c in enumerate(precomputed)
    ])

    from quantlab.trading_engine import run_swing_backtest
    result = run_swing_backtest(
        hist, scored_df, tr_cfg,
        start_date_str=start or "2025-01-01",
        end_date_str=end or "",
        precomputed=precomputed,
    )
    if result is None:
        raise ValueError("自选池回测无结果（可能区间无交易日）")

    # Per-stock independent full-position backtests
    per_stock = []
    for code, name in names.items():
        r = _run_single_on_hist(hist, code, name, tr_cfg, start, end, precomputed)
        if r is None:
            continue
        per_stock.append({
            "code": code,
            "name": name,
            "result": backtest_result_to_dict(r, f"{code} {name}"),
            "summary": {
                "total_return": r["stats"]["total_return"],
                "annual_return": r["stats"]["annual_return"],
                "sharpe": r["stats"]["sharpe"],
                "max_drawdown": r["stats"]["max_drawdown"],
                "total_trades": r["stats"]["total_trades"],
                "win_rate": r["stats"]["win_rate"],
                "final_value": r["stats"]["final_value"],
            },
        })

    return {
        "mode": "watchlist",
        "pool": [{"code": c, "name": names[c]} for c in precomputed],
        "result": backtest_result_to_dict(result, "组合轮动"),
        "per_stock": per_stock,
        "start_date": start,
        "end_date": end or "",
    }


def run_stock_backtest(config: dict, params: dict | None = None) -> dict:
    params = params or {}
    stock = str(params.get("stock") or config.get("test", {}).get("stock", "601857")).zfill(6)
    strategy = params.get("strategy") or config.get("trading", {}).get("strategy", "reversal")
    config = _apply_strategy(config, strategy)
    cfg_backtest = config.get("backtest", {})
    start = params.get("start_date") or cfg_backtest.get("start_date", "2025-01-01")
    end = params.get("end_date") or cfg_backtest.get("end_date", "")

    hist = _fetch_hist(config, [stock])
    sina = _find_sina_key(hist, stock)
    if sina is None:
        raise ValueError(f"未获取到 {stock} 的历史数据")

    df = hist[sina]
    name = WATCH_NAMES.get(stock, stock)

    # Precompute indicators for this code
    precomputed: dict[str, dict] = {}
    _load_data(df, precomputed, stock)

    from quantlab.trading_engine import backtest_single_stock
    result = backtest_single_stock(
        stock, hist, config.get("trading", {}),
        start or "2025-01-01", end or "",
    )
    if result is None:
        # backtest_single_stock may fail if name lookup needs spot; fallback direct engine
        print("  ℹ 使用简化名称直接回测")
        result = _run_single_on_hist(hist, stock, name, config.get("trading", {}), start, end, precomputed)
    if result is None:
        raise ValueError(f"{stock} 回测无结果")

    return {
        "mode": "stock",
        "stock": {"code": stock, "name": name},
        "result": backtest_result_to_dict(result, f"{stock} {name}"),
        "start_date": start,
        "end_date": end or "",
    }


def list_test_modules() -> list[dict]:
    import importlib
    import pkgutil
    import tests

    modules = []
    for m in pkgutil.iter_modules(tests.__path__):
        try:
            mod = importlib.import_module(f"tests.{m.name}")
            doc = (mod.__doc__ or "").strip().split("\n")[0]
        except Exception:
            doc = ""
        modules.append({"name": m.name, "description": doc})
    return sorted(modules, key=lambda x: x["name"])

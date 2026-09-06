"""Cvxportfolio-based portfolio optimization service (A-share universe)."""
from __future__ import annotations

import os
import sys
import math
from typing import Any

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from quantlab.data_fetcher import _code_pure  # noqa: E402


def _py(value: Any) -> Any:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        v = float(value)
        return None if math.isnan(v) or math.isinf(v) else round(v, 6)
    return value


def run_portfolio_optimization(config: dict, params: dict | None = None) -> dict:
    """Run cvxportfolio optimization on config.watchlist and return summary."""
    params = params or {}
    codes = [str(c).zfill(6) for c in (params.get("watchlist") or config.get("watchlist", []))]
    if not codes:
        raise ValueError("未配置 watchlist，无法运行组合优化")

    data_cfg = config.get("data", {})
    from quantlab.data_sources import get_data_source
    ds = get_data_source(data_cfg.get("source", "quantdash"))
    hist = ds.fetch_watchlist_data(
        codes,
        cache_dir=data_cfg.get("cache_dir", "cache"),
        use_cache=not data_cfg.get("no_cache", False),
        max_bars=data_cfg.get("hist_days", 1200),
    )

    frames = {}
    for k, df in hist.items():
        pure = _code_pure(k)
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        cols = ["open", "close", "volume"]
        if "amount" in df.columns:
            cols.append("amount")
        frames[pure] = df[cols]

    if not frames:
        raise ValueError("没有获取到可优化的标的行情")

    common = None
    for f in frames.values():
        idx = f.index
        common = idx if common is None else common.intersection(idx)
    common = common.sort_values()
    if len(common) < 60:
        raise ValueError("标的共同交易日太少，无法进行组合优化")

    open_px = pd.DataFrame({c: frames[c]["open"] for c in frames}).reindex(common)
    close_px = pd.DataFrame({c: frames[c]["close"] for c in frames}).reindex(common)
    vol = pd.DataFrame({c: frames[c]["volume"] for c in frames}).reindex(common)
    has_amount = "amount" in frames[codes[0]].columns
    amt = pd.DataFrame({c: frames[c]["amount"] for c in frames}).reindex(common) if has_amount else vol * close_px

    # Cvxportfolio expects open-to-open returns: p_{t+1}/p_t - 1
    returns = open_px.shift(-1) / open_px - 1
    returns = returns.iloc[:-1]
    volumes = amt.reindex(returns.index)
    prices = open_px.reindex(returns.index)
    returns["cash"] = 0.0

    import cvxportfolio as cvx
    market_data = cvx.UserProvidedMarketData(
        returns, volumes=volumes, prices=prices, cash_key="cash"
    )
    gamma = float(params.get("gamma", 2.0))
    objective = cvx.ReturnsForecast() - gamma * cvx.FullCovariance() - cvx.StocksTransactionCost()
    policy = cvx.SinglePeriodOptimization(
        objective, constraints=[cvx.LeverageLimit(1)]
    )
    sim = cvx.StockMarketSimulator(market_data=market_data, round_trades=True)

    start_time = returns.index[-min(int(params.get("periods", 252)), len(returns))]
    end_time = returns.index[-1]
    result = sim.backtest(policy, start_time=start_time, end_time=end_time)

    # Summary fields
    initial = float(result.initial_value)
    final = float(result.final_value)
    drawdowns = np.asarray(result.drawdown, dtype=float) if hasattr(result, "drawdown") else np.array([])
    max_dd = float(np.min(drawdowns)) if len(drawdowns) else 0.0

    latest_weights = {}
    if hasattr(result, "w_plus") and result.w_plus is not None and len(result.w_plus) > 0:
        clean_w = result.w_plus.dropna()
        if len(clean_w) > 0:
            last = clean_w.iloc[-1]
            for col in last.index:
                if str(col) == "cash":
                    continue
                latest_weights[str(col)] = _py(float(last[col])) if not pd.isna(last[col]) else 0.0

    trade_history = []
    if hasattr(result, "u") and result.u is not None and len(result.u) > 0:
        clean_u = result.u.dropna()
        for trade_date, row in clean_u.iterrows():
            for col in row.index:
                if str(col) == "cash":
                    continue
                val = float(row[col])
                if val is None or pd.isna(val) or abs(val) < 1e-6:
                    continue
                trade_history.append({
                    "date": str(pd.Timestamp(trade_date).date()),
                    "code": str(col),
                    "amount": _py(val),
                    "side": "买入" if val > 0 else "卖出",
                })
        trade_history.sort(key=lambda x: x["date"])

    latest_trades = {}
    if hasattr(result, "u") and result.u is not None and len(result.u) > 0:
        clean_u = result.u.dropna()
        # Show the most recent row that actually changed a non-cash position.
        for _, row in clean_u.iloc[::-1].iterrows():
            trades = {}
            for col in row.index:
                if str(col) == "cash":
                    continue
                val = float(row[col])
                if abs(val) > 1e-9:
                    trades[str(col)] = _py(val)
            if trades:
                latest_trades = trades
                break

    return {
        "mode": "portfolio_optimization",
        "codes": codes,
        "universe": list(frames.keys()),
        "start": str(start_time.date()),
        "end": str(end_time.date()),
        "periods": int(getattr(result, "periods_per_year", 0)) or None,
        "summary": {
            "initial_value": _py(initial),
            "final_value": _py(final),
            "total_return": _py(final / initial - 1 if initial else 0.0),
            "annual_return": _py(getattr(result, "annualized_average_return", None)),
            "volatility": _py(getattr(result, "annualized_volatility", None)),
            "sharpe": _py(getattr(result, "sharpe_ratio", None)),
            "max_drawdown": _py(max_dd),
            "avg_leverage": _py(getattr(result, "leverage", np.nan).mean() if hasattr(result, "leverage") else None),
            "avg_turnover": _py(getattr(result, "turnover", np.nan).mean() if hasattr(result, "turnover") else None),
        },
        "latest_weights": latest_weights,
        "latest_trades": latest_trades,
        "trade_history": trade_history,
    }

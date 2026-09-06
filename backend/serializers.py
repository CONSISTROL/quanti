"""Convert trading engine objects into JSON-friendly dictionaries."""
from __future__ import annotations

import math
from typing import Any


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        if math.isnan(v) or math.isinf(v):
            return default
        return round(v, 6)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _date_str(value: Any) -> str:
    if value is None:
        return ""
    s = str(value)
    # Timestamp / datetime / date all convert to ISO date when sliced.
    return s[:10]


def stats_to_dict(stats: dict | None) -> dict:
    if not stats:
        return {}
    out = {}
    percent_keys = {
        "total_return", "annual_return", "max_drawdown",
        "win_rate", "avg_win", "avg_loss", "profit_loss_ratio",
    }
    for k, v in stats.items():
        if k in percent_keys:
            out[k] = _safe_float(v)
        elif isinstance(v, (int, float)):
            out[k] = _safe_float(v) if isinstance(v, float) else _safe_int(v)
        else:
            out[k] = v
    return out


def trade_to_dict(trade: Any) -> dict:
    return {
        "code": str(getattr(trade, "code", "")),
        "name": str(getattr(trade, "name", "")),
        "direction": str(getattr(trade, "direction", "")),
        "side": "买入" if getattr(trade, "direction", "") == "BUY" else "卖出",
        "date": _date_str(getattr(trade, "date", "")),
        "signal_date": _date_str(getattr(trade, "signal_date", "") or getattr(trade, "date", "")),
        "price": _safe_float(getattr(trade, "price", 0)),
        "signal_price": _safe_float(getattr(trade, "signal_price", None) or getattr(trade, "price", 0)),
        "shares": _safe_int(getattr(trade, "shares", 0)),
        "amount": _safe_float(getattr(trade, "amount", 0)),
        "reason": str(getattr(trade, "reason", "")),
        "pnl_pct": None if getattr(trade, "direction", "") != "SELL" else _safe_float(getattr(trade, "pnl_pct", 0), None),
        "pnl_signal": None if getattr(trade, "direction", "") != "SELL" else _safe_float(getattr(trade, "pnl_signal", None) or getattr(trade, "pnl_pct", 0), None),
    }


def trades_to_list(trades: list | None) -> list:
    if not trades:
        return []
    return [trade_to_dict(t) for t in trades]


def equity_curve_to_list(curve: list | None) -> list:
    """[(date, value), ...] -> [{'date': 'YYYY-MM-DD', 'value': 123.0}, ...]"""
    if not curve:
        return []
    out = []
    for item in curve:
        if isinstance(item, (tuple, list)) and len(item) >= 2:
            out.append({"date": _date_str(item[0]), "value": _safe_float(item[1])})
        elif isinstance(item, dict):
            out.append({"date": _date_str(item.get("date")), "value": _safe_float(item.get("value"))})
    return out


def position_to_dict(pos: Any) -> dict:
    return {
        "code": str(getattr(pos, "code", "")),
        "name": str(getattr(pos, "name", "")),
        "entry_price": _safe_float(getattr(pos, "entry_price", 0)),
        "entry_date": _date_str(getattr(pos, "entry_date", "")),
        "shares": _safe_int(getattr(pos, "shares", 0)),
        "capital": _safe_float(getattr(pos, "capital", 0)),
        "last_price": _safe_float(getattr(pos, "_last_price", getattr(pos, "entry_price", 0))),
    }


def positions_to_list(positions: list | None) -> list:
    if not positions:
        return []
    return [position_to_dict(p) for p in positions]


def backtest_result_to_dict(result: dict | None, name: str = "") -> dict:
    if result is None:
        return {}
    return {
        "name": name,
        "initial_capital": _safe_float(result.get("initial_capital", 0)),
        "stats": stats_to_dict(result.get("stats")),
        "signal_stats": stats_to_dict(result.get("signal_stats")),
        "equity_curve": equity_curve_to_list(result.get("equity_curve")),
        "signal_equity_curve": equity_curve_to_list(result.get("signal_equity_curve") or result.get("equity_curve")),
        "trades": trades_to_list(result.get("trades")),
        "final_positions": positions_to_list(result.get("final_positions")),
        "signal_positions": positions_to_list(result.get("signal_positions")),
    }

"""
绩效统计 — 与 quantlab/trading_engine 相同公式（G3 同口径对比用）。
"""
from __future__ import annotations

import numpy as np


def calc_legacy_style_stats(curve, capital, trading_days, trades) -> dict:
    """输入: equity curve [[date,value],...] / 初始资金 / 交易日数 / trade dicts(BUY/SELL)。"""
    values = [float(v) for _, v in curve]
    if not values:
        return {}
    final_value = values[-1]
    total_return = final_value / capital - 1
    annual_return = (1 + total_return) ** (252 / max(trading_days, 1)) - 1

    sell_pnls = []
    shares_held = 0.0
    cost = 0.0
    for t in trades:
        if t["direction"] == "BUY":
            denom = shares_held + t["volume"]
            cost = (cost * shares_held + t["amount"]) / denom if denom > 0 else t["price"]
            shares_held += t["volume"]
        else:
            pnl = (t["price"] / cost - 1) if cost > 0 else 0
            sell_pnls.append(pnl)
            shares_held -= t["volume"]
    win_trades = [p for p in sell_pnls if p > 0]
    loss_trades = [p for p in sell_pnls if p <= 0]
    win_rate = len(win_trades) / len(sell_pnls) if sell_pnls else 0

    daily_rets = []
    for i in range(1, len(values)):
        prev = values[i - 1]
        if prev > 0:
            daily_rets.append(values[i] / prev - 1)
    sharpe = (np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)
              if daily_rets and np.std(daily_rets) > 0 else 0)

    peaks = np.maximum.accumulate(values)
    drawdowns = (peaks - values) / peaks
    max_drawdown = float(np.max(drawdowns)) if len(drawdowns) else 0
    max_dd_days, pk_idx = 0, 0
    for i in range(1, len(values)):
        if values[i] >= values[pk_idx]:
            pk_idx = i
        elif i - pk_idx > max_dd_days:
            max_dd_days = i - pk_idx

    return {
        "initial_capital": capital, "final_value": round(final_value, 2),
        "total_return": float(total_return), "annual_return": float(annual_return),
        "sharpe": float(sharpe), "max_drawdown": float(max_drawdown),
        "max_drawdown_days": max_dd_days, "total_trades": len(sell_pnls),
        "win_rate": float(win_rate),
        "avg_win": float(np.mean(win_trades)) if win_trades else 0,
        "avg_loss": float(np.mean(loss_trades)) if loss_trades else 0,
        "profit_loss_ratio": abs(float(np.mean(win_trades) / np.mean(loss_trades)))
        if win_trades and loss_trades and np.mean(loss_trades) != 0 else 0,
        "trading_days": trading_days,
    }

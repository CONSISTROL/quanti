"""
单标的回测运行器 — 把同一份 cache 历史同时喂给 vnpy BacktestingEngine 并输出
   决策事件 / 成交 / 净值 / 统计（JSON + 控制台），供与 legacy 结果做 G2/G3 对比。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

import numpy as np

from . import adapters, indicators
from .database import QuantCacheDatabase
from .indicators import dkey
from .legacy_access import REPO_ROOT, ensure_legacy_importable
from .strategies import get_cta_strategy

ensure_legacy_importable()

from vnpy.trader.constant import Interval          # noqa: E402
from vnpy_ctastrategy.backtesting import BacktestingEngine  # noqa: E402


def load_config_defaults() -> dict:
    """读取 repo config.json 的 trading/backtest 默认（文件不存在时用内置默认）。"""
    cfg_path = REPO_ROOT / "config.json"
    try:
        import json as _json
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = _json.load(f)
        trading = cfg.get("trading", {}) or {}
        backtest = cfg.get("backtest", {}) or {}
        return {
            "initial_capital": float(trading.get("initial_capital", 150000)),
            "min_buy_score": float(trading.get("min_buy_score", 4)),
            "max_holding_days": int(trading.get("max_holding_days", 0)),
            "position_pct": float(trading.get("position_pct", 1.0)),
            "full_position": bool(trading.get("full_position", True)),
            "start_date": backtest.get("start_date", "2025-01-01"),
            "end_date": backtest.get("end_date", ""),
        }
    except Exception:
        return {"initial_capital": 150000, "min_buy_score": 4, "max_holding_days": 0,
                "position_pct": 1.0, "full_position": True,
                "start_date": "2025-01-01", "end_date": ""}


def run_stock_backtest(code6: str, strategy: str = "reversal", *,
                       cache_dir: str = "cache", prefer_watchlist: str | None = None,
                       start_date: str | None = None, end_date: str | None = None,
                       initial_capital: float | None = None,
                       verbose: bool = True) -> dict:
    """单标的 vnpy 回测。返回结构化结果 dict。"""
    from vnpy.trader.constant import Interval as Ivl

    code6 = str(code6).zfill(6)
    df, sina_code = adapters.load_symbol_df(cache_dir, code6, prefer_watchlist)
    cfg = load_config_defaults()
    start = start_date or cfg["start_date"]
    end = end_date or cfg["end_date"] or df["date"].max().strftime("%Y-%m-%d")
    capital = initial_capital or cfg["initial_capital"]

    window_dates = indicators.window_trading_dates(df, start, end)
    if not window_dates:
        raise ValueError(f"{code6} 在 {start}~{end} 无交易日")
    ind_map = indicators.build_ind_map(df, window_dates)

    # —— vnpy 引擎 ——
    from .symbols import sina_to_vt
    vt_symbol = sina_to_vt(sina_code)

    QuantCacheDatabase.install({vt_symbol: df})
    engine = BacktestingEngine()
    engine.set_parameters(
        vt_symbol=vt_symbol,
        interval=Interval.DAILY,
        start=datetime.combine(pd_ts(window_dates[0]).date(), datetime.min.time()),
        end=datetime.combine(pd_ts(window_dates[-1]).date(), datetime.min.time()),
        rate=0.0, slippage=0.0, size=1, pricetick=0.01,
        capital=capital,
    )
    strat_cls = get_cta_strategy(strategy)
    setting = {
        "initial_capital": capital,
        "min_buy_score": cfg["min_buy_score"],
        "max_holding_days": cfg["max_holding_days"],
        "position_pct": cfg["position_pct"],
        "full_position": cfg["full_position"],
        "leader_bonus": 2.0,
        "apply_leader_bonus": True,
    }
    engine.add_strategy(strat_cls, setting)
    # 注入预计算上下文
    engine.strategy.ind_map = ind_map
    engine.strategy.window_dates = window_dates

    if verbose:
        print(f"[vnpy_quanti] {code6} {sina_code} → {vt_symbol}  策略={strategy}")
        print(f"  区间 {start} ~ {end}  窗口交易日 {len(window_dates)}  历史K线 {len(df)} 根")

    engine.load_data()
    engine.run_backtesting()
    engine.calculate_result()
    vnpy_stats = engine.calculate_statistics(output=False)

    strat = engine.strategy
    decisions = list(strat.events)
    trades = []
    for t in engine.trades.values():
        trades.append({
            "date": t.datetime.date().strftime("%Y-%m-%d"),
            "direction": "BUY" if t.offset.value == "开" else "SELL",
            "price": round(float(t.price), 4),
            "volume": int(t.volume),
            "amount": round(float(t.price * t.volume), 2),
        })

    # —— 净值重建（按成交日逐笔记账, 收盘市值 = 旧引擎 equity 语义）——
    close_map = {dkey(d): float(c)
                 for d, c in zip(df["date"].values, df["close"].values)}
    curve = []
    cash = float(capital)
    shares = 0.0
    # engine.trades 按插入序 → 与撮合序一致
    trades_by_date: dict[str, list] = {}
    for t in trades:
        trades_by_date.setdefault(t["date"], []).append(t)
    for wd in window_dates:
        ds = dkey(wd)
        for t in trades_by_date.get(ds, []):
            if t["direction"] == "BUY":
                cash -= t["amount"]
                shares += t["volume"]
            else:
                cash += t["amount"]
                shares -= t["volume"]
        px = close_map.get(ds)
        if px is not None:
            value = cash + shares * px
        else:
            value = cash
        curve.append([ds, round(value, 2)])

    stats = calc_legacy_style_stats(curve, capital, len(window_dates), trades)

    result = {
        "meta": {
            "engine": "vnpy 4.4 BacktestingEngine",
            "code": code6, "sina_code": sina_code, "vt_symbol": vt_symbol,
            "strategy": strategy, "start": start, "end": end,
            "initial_capital": capital, "window_days": len(window_dates),
            "history_rows": len(df),
        },
        "decisions": decisions,
        "trades": trades,
        "equity_curve": curve,
        "stats": stats,
        "vnpy_stats": {k: (None if isinstance(v, float) and not np.isfinite(v) else v)
                       for k, v in vnpy_stats.items()},
    }

    if verbose:
        s = stats
        print("─" * 60)
        print(f"  初始资金 {capital:,.0f}   最终 {s['final_value']:,.2f}")
        print(f"  总收益率 {s['total_return']:+.2%}   年化 {s['annual_return']:+.2%}   "
              f"Sharpe {s['sharpe']:.2f}")
        print(f"  最大回撤 {s['max_drawdown']:.2%}   (天数 {s['max_drawdown_days']})")
        print(f"  成交 {len(trades)} 笔 (决策 {len(decisions)} 条)  卖出胜率 {s['win_rate']:.1%}")
        for d in decisions:
            print(f"    {d['date']}  {d['direction']:<4} 价{d['price']:.3f}  "
                  f"{d['reason'][:60]}")
    return result


def calc_legacy_style_stats(curve, capital, trading_days, trades) -> dict:
    """与 quantlab/trading_engine 相同公式的统计（便于 G3 同口径对比）。"""
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
            cost = (cost * shares_held + t["amount"]) / (shares_held + t["volume"]) \
                if (shares_held + t["volume"]) > 0 else t["price"]
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


def pd_ts(x):
    import pandas as pd
    return pd.Timestamp(x)


def _jdefault(o):
    """json 兜底: numpy 标量 → python 标量; 其余 → str。"""
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="vnpy_quanti.backtest")
    parser.add_argument("--stock", required=True, help="6 位股票代码")
    parser.add_argument("--strategy", default="reversal")
    parser.add_argument("--cache-dir", default="cache")
    parser.add_argument("--watchlist-pkl", default=None, help="指定自选池 pkl")
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--capital", type=float, default=None)
    parser.add_argument("--out", default=None, help="结果 JSON 输出路径")
    args = parser.parse_args(argv)

    code = args.stock.strip().lower()
    for pre in ("sh", "sz", "bj"):
        if code.startswith(pre):
            code = code[len(pre):]
            break
    result = run_stock_backtest(
        code.zfill(6), args.strategy,
        cache_dir=args.cache_dir, prefer_watchlist=args.watchlist_pkl,
        start_date=args.start, end_date=args.end, initial_capital=args.capital,
    )
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1, default=_jdefault)
        print(f"  [ok] JSON: {os.path.abspath(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

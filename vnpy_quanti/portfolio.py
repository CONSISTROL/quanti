"""
PortfolioEngine — 多标的一账户组合回测（vnpy 化重构版）

镜像 legacy quantlab.trading_engine.run_swing_backtest 在 immediate(信号日收盘成交)下的
决策流（不启用 exec 延迟/凯利/降频/加仓/reduce 等可选特性——它们默认关闭，后续按需接入）：
  逐交易日(union dates):
    1) 卖出判定: 每持仓 → 最大持有天数 / rebound 规则 / strategy.sell_signal → 收盘价卖出, 当日禁买该 code
    2) 买入判定: slots>0 且 cash>5% 资金 → 依策略买点+加分(龙头TOP10/自选优先级) → 按分排序取前 slots 只,
       收盘价买入 (现金/仓位规则与旧引擎一致: full_position 或按分缩放 position_pct)
净值 = cash + Σ持仓市值(收盘, 缺bar前值填充)。

数据/指标沿用 vnpy_quanti 单源 (cache pkl + quantlab.indicator_cache)；
判定策略对象复用 quantlab.strategies 的 buy_signal/sell_signal。
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Any

import pandas as pd

from . import indicators
from .indicators import dkey
from .metrics import calc_legacy_style_stats


def _ts(x):
    return pd.Timestamp(x)


@dataclass
class Position:
    code: str
    name: str
    shares: int
    entry_price: float
    entry_date: Any          # pd.Timestamp
    entry_type: str = "swing"
    max_profit_seen: float = 0.0


class PortfolioEngine:
    """自选池轮动组合回测引擎（immediate / 同收盘价成交）。"""

    def __init__(self, *, strategy: str = "watchlist", initial_capital: float = 150000,
                 max_positions: int = 1, position_pct: float = 1.0,
                 full_position: bool = True, min_buy_score: float = 4,
                 max_holding_days: int = 0,
                 watchlist_priority: dict | None = None,
                 ranks: dict | None = None, leader_bonus: float = 2.0,
                 names: dict | None = None) -> None:
        self.strategy_name = strategy
        self.initial_capital = float(initial_capital)
        self.max_positions = int(max_positions)
        self.position_pct = float(position_pct)
        self.full_position = bool(full_position)
        self.min_buy_score = float(min_buy_score)
        self.max_holding_days = int(max_holding_days)
        self.watchlist_priority = watchlist_priority or {}
        self.ranks = ranks or {}
        self.leader_bonus = float(leader_bonus)
        self.names = names or {}

        # 运行期状态
        self.cash = self.initial_capital
        self.positions: list[Position] = []
        self.events: list[dict] = []
        self.curve: list = []
        self.trades: list[dict] = []

        self.codes: list[str] = []
        self._dfs: dict[str, Any] = {}
        self._pre: dict[str, dict] = {}
        self._window_dates: list = []
        self._close_last: dict[str, list] = {}     # code -> [(date, close)] 升序

    # ------------------------------------------------------------------ #
    def load(self, code_dfs: dict[str, Any], start_date: str, end_date: str,
             precomputed: dict | None = None) -> None:
        """code_dfs: {6位code: DataFrame}。precomputed: {code: {dstr: ind}} 可选。"""
        self.codes = list(code_dfs.keys())
        window = set()
        for c, df in code_dfs.items():
            w = indicators.window_trading_dates(df, start_date, end_date)
            window.update(w)
            self._dfs[c] = df
            arr = sorted((indicators.dkey(d), float(px))
                         for d, px in zip(df["date"].values, df["close"].values))
            self._close_last[c] = arr
        self._window_dates = sorted(window)
        if not self._window_dates:
            raise ValueError("窗口内无交易日")

        # 指标: 每个 code 只算其自身窗口日期 (与 legacy precomputed 相同)
        if precomputed is None:
            precomputed = {}
            for c, df in code_dfs.items():
                wd = indicators.window_trading_dates(df, start_date, end_date)
                precomputed[c] = indicators.build_ind_map(df, wd)
        self._pre = precomputed

    # ------------------------------------------------------------------ #
    def _ind(self, code: str, dstr: str) -> dict | None:
        return self._pre.get(code, {}).get(dstr)

    def _gap_ind(self, code: str, today) -> dict | None:
        """旧引擎 gap 策略买入回退: 轻量 ind (>=2根, 无需60根)。

        镜像 run_swing_backtest 对 gap_open/gap_open_open 的特殊回退路径:
          {'close': 最近收盘, 'prev_close': 前一根收盘, 'open'?, 'vol_ratio'?}
        """
        df = self._dfs.get(code)
        if df is None:
            return None
        import numpy as np
        mask = df["date"] <= _ts(today)
        if int(mask.sum()) < 2:
            return None
        closes = df["close"].values.astype(float)[: mask.sum()]
        ind = {"close": float(closes[-1]), "prev_close": float(closes[-2])}
        if "open" in df.columns:
            ind["open"] = float(df["open"].values.astype(float)[mask.sum() - 1])
        if self.strategy_name == "gap_open_open" and "volume" in df.columns:
            vols = df["volume"].values.astype(float)[: mask.sum()]
            v5 = float(vols[-5:].mean()) if len(vols) >= 5 else float(vols.mean())
            ind["vol_ratio"] = float(vols[-1] / v5) if v5 > 0 else 1.0
        return ind

    def _fallback_ind(self, code: str, today, min_rows: int = 20) -> dict | None:
        """旧引擎回退: 无预计算时用 compute_indicators 实时算。
        卖出回退要求 >=20 根; 买入回退要求 >=60 根 (与旧引擎一致)。"""
        arr = self._close_last.get(code, [])
        if not arr:
            return None
        keys = [x[0] for x in arr]
        idx = bisect.bisect_right(keys, indicators.dkey(today)) - 1
        df = self._dfs.get(code)
        if df is None or idx + 1 < min_rows:
            return None
        sub = df.iloc[: idx + 1]
        from quantlab.trading_engine import compute_indicators
        return compute_indicators(
            sub["close"].values.astype(float),
            sub["volume"].values.astype(float) if "volume" in sub else None,
            sub["high"].values.astype(float) if "high" in sub else None,
            sub["low"].values.astype(float) if "low" in sub else None,
        )

    def _holding_days(self, pos: Position, today) -> int:
        return sum(1 for d in self._window_dates
                   if pos.entry_date < _ts(d) <= today)

    def _close_on(self, code: str, today) -> float | None:
        """该 code 在 <=today 的最近收盘价 (估值/成交用, 前值填充)。"""
        arr = self._close_last.get(code, [])
        if not arr:
            return None
        keys = [x[0] for x in arr]
        i = bisect.bisect_right(keys, indicators.dkey(today)) - 1
        return arr[i][1] if i >= 0 else None

    # ------------------------------------------------------------------ #
    def _sell_position(self, pos: Position, today, dstr: str) -> str | None:
        """镜像旧引擎当日卖出判定 + 高位减仓(reduce)就地执行。
        返回“整仓卖出”的理由(若触发), 减仓为副作用(不进 sold_today)。"""
        ind = self._ind(pos.code, dstr)
        if ind is None:
            ind = self._fallback_ind(pos.code, today)
        if ind is None:
            return None

        close = float(ind.get("skdj_close", ind.get("close", 0)))
        holding_days = self._holding_days(pos, today)
        pnl = close / pos.entry_price - 1 if pos.entry_price > 0 else 0
        pos.max_profit_seen = max(pos.max_profit_seen, pnl)
        strat = self._strategy()

        reason: str | None = None
        if self.max_holding_days > 0 and holding_days >= self.max_holding_days:
            reason = f"持有{holding_days}天到期"
        elif pos.entry_type == "rebound":
            ma20 = float(ind.get("ma20", 0))
            if holding_days >= 5:
                reason = f"反弹到期({holding_days}天)"
            elif holding_days >= 2 and ma20 > 0 and close >= ma20:
                reason = f"反弹到MA20({ma20:.2f})"

        # 高位减仓 (策略可选实现 reduce_signal -> (is_reduce, ratio))
        if reason is None:
            reduce_fn = getattr(strat, "reduce_signal", None)
            if reduce_fn:
                is_reduce, ratio = reduce_fn(ind, pos.entry_price)
                if is_reduce and 0 < ratio < 1:
                    reduce_shares = int(pos.shares * ratio / 100) * 100
                    if 100 <= reduce_shares < pos.shares:
                        amount = reduce_shares * close
                        self.cash += amount
                        pos.shares -= reduce_shares
                        self.trades.append({"date": dstr, "code": pos.code,
                                            "direction": "SELL",
                                            "price": round(close, 4),
                                            "volume": reduce_shares,
                                            "amount": round(amount, 2)})
                        self.events.append({"date": dstr, "code": pos.code,
                                            "direction": "SELL",
                                            "price": round(close, 4),
                                            "volume": reduce_shares,
                                            "reason": f"高位减仓{ratio:.0%}"})

        if reason is None:
            is_sell, r = strat.sell_signal(ind, pos.entry_price, holding_days,
                                           pos.max_profit_seen)
            if is_sell:
                reason = r
        return reason

    # 复用 quantlab 策略实例 (each code same instance acceptable: stateless per call)
    _strat_cache: dict = {}

    def _strategy(self):
        from quantlab.strategies import get_strategy
        key = self.strategy_name
        if key not in self._strat_cache:
            self._strat_cache[key] = get_strategy(key)
        return self._strat_cache[key]

    # ------------------------------------------------------------------ #
    def run(self, verbose: bool = True) -> dict:
        from quantlab.strategies.base import BaseStrategy
        strat = self._strategy()
        has_rebound = type(strat).rebound_signal is not BaseStrategy.rebound_signal

        wpri = {str(k).zfill(6): int(v) for k, v in self.watchlist_priority.items()}

        for day in self._window_dates:
            dstr = indicators.dkey(day)
            sold_today: set[str] = set()

            # ---- 1. 卖出 ----
            for pos in list(self.positions):
                reason = self._sell_position(pos, day, dstr)
                if not reason:
                    continue
                px = self._close_on(pos.code, day)
                if px is None or px <= 0:
                    px = pos.entry_price
                amount = pos.shares * px
                self.cash += amount
                self.trades.append({"date": dstr, "code": pos.code,
                                    "direction": "SELL", "price": round(px, 4),
                                    "volume": pos.shares,
                                    "amount": round(amount, 2)})
                self.events.append({"date": dstr, "code": pos.code, "direction": "SELL",
                                    "price": round(px, 4), "volume": pos.shares,
                                    "reason": reason})
                self.positions.remove(pos)
                sold_today.add(pos.code)

            # ---- 2. 买入 ----
            add_fn = getattr(strat, "add_position_signal", None) or None
            slots = self.max_positions - len(self.positions)
            if slots > 0 and self.cash > self.initial_capital * 0.05:
                held_pos = {p.code: p for p in self.positions}
                cands = []
                for code in self.codes:
                    if code in sold_today:
                        continue
                    # 已持仓代码仅在策略支持 add_position_signal 时进入(低位加仓)
                    if code in held_pos and add_fn is None:
                        continue
                    ind = self._ind(code, dstr)
                    if ind is None:
                        if self.strategy_name in ("gap_open", "gap_open_open"):
                            ind = self._gap_ind(code, day)          # gap 轻量回退(>=2根)
                        else:
                            ind = self._fallback_ind(code, day, min_rows=60)
                    if ind is None:
                        continue
                    is_buy, score, reason = strat.buy_signal(ind)
                    entry_type = "swing"
                    if not is_buy and has_rebound:
                        is_rb, r = strat.rebound_signal(
                            ind, self._last_close_of(code, day, dstr))
                        if is_rb:
                            score, reason, entry_type = 4, r, "rebound"
                    # 龙头加分 (rank<=10)
                    if int(self.ranks.get(code, 999)) <= 10:
                        score += self.leader_bonus
                        reason = f"{reason}+龙头TOP10" if reason else "龙头TOP10"
                    # 自选池优先级
                    bonus = wpri.get(code)
                    if bonus:
                        score += bonus
                        reason = f"{reason}+优先级{bonus}" if reason else f"优先级{bonus}"
                    if score < self.min_buy_score:
                        continue
                    cands.append((code, score, reason, entry_type))

                cands.sort(key=lambda x: x[1], reverse=True)   # 稳定排序
                for code, score, reason, entry_type in cands[:slots]:
                    px = self._close_on(code, day)
                    if px is None or px <= 0:
                        continue
                    pos = held_pos.get(code)
                    if pos is not None:
                        # 低位加仓: 补足到 position_pct * total_assets (旧引擎同款,
                        # 含其“无 _last_price 时全部持仓按本候选价估值”的怪癖口径)
                        total_assets = self.cash + sum(
                            p.shares * px for p in self.positions)
                        target = self.position_pct * total_assets
                        cur_value = pos.shares * px
                        add_alloc = max(0.0, min(self.cash, target - cur_value))
                        if add_alloc < px * 100:
                            continue
                        add_shares = int(add_alloc / px / 100) * 100
                        if add_shares <= 0:
                            continue
                        amount = add_shares * px
                        old_cost = pos.entry_price * pos.shares
                        pos.shares += add_shares
                        pos.entry_price = (old_cost + amount) / pos.shares  # 摊薄
                        self.cash -= amount
                        self.trades.append({"date": dstr, "code": code,
                                            "direction": "BUY", "price": round(px, 4),
                                            "volume": add_shares,
                                            "amount": round(amount, 2)})
                        self.events.append({"date": dstr, "code": code,
                                            "direction": "BUY", "price": round(px, 4),
                                            "volume": add_shares,
                                            "reason": f"低位加仓:{reason}"})
                        continue

                    if self.full_position:
                        alloc = self.cash * self.position_pct
                    else:
                        weight = self.position_pct * min(max(score, 0) / 10.0, 1.0)
                        alloc = self.cash * weight
                    if alloc < px * 100:
                        continue
                    shares = int(alloc / px / 100) * 100
                    if shares <= 0:
                        continue
                    amount = shares * px
                    self.cash -= amount
                    self.positions.append(Position(
                        code=code, name=self.names.get(code, code),
                        shares=shares, entry_price=px, entry_date=day,
                        entry_type=entry_type))
                    self.trades.append({"date": dstr, "code": code,
                                        "direction": "BUY", "price": round(px, 4),
                                        "volume": shares, "amount": round(amount, 2)})
                    self.events.append({"date": dstr, "code": code, "direction": "BUY",
                                        "price": round(px, 4), "volume": shares,
                                        "reason": reason})

            # ---- 3. 估值 ----
            mv = 0.0
            for pos in self.positions:
                px = self._close_on(pos.code, day)
                mv += pos.shares * (px if px else pos.entry_price)
            self.curve.append([dstr, round(self.cash + mv, 2)])

        stats = calc_legacy_style_stats(self.curve, self.initial_capital,
                                        len(self._window_dates), self.trades)
        return {
            "meta": {
                "engine": "vnpy_quanti PortfolioEngine (immediate close)",
                "strategy": self.strategy_name,
                "codes": list(self.codes),
                "initial_capital": self.initial_capital,
                "max_positions": self.max_positions,
                "position_pct": self.position_pct,
                "full_position": self.full_position,
                "min_buy_score": self.min_buy_score,
                "window_days": len(self._window_dates),
            },
            "decisions": self.events,
            "trades": self.trades,
            "equity_curve": self.curve,
            "stats": stats,
        }

    def _last_close_of(self, code: str, day, dstr: str) -> float | None:
        """前一日 ind 收盘（rebound prev_close 用，与旧引擎一致）。"""
        arr = self._pre.get(code, {})
        dates = sorted(arr)
        i = bisect.bisect_left(dates, dstr) - 1
        if i >= 0:
            return arr[dates[i]].get("close")
        return None


# --------------------------------------------------------------------- #
def run_watchlist(code_dfs, *, strategy="watchlist", start_date="2025-01-01",
                  end_date="", cache_cfg: dict | None = None,
                  verbose=False) -> dict:
    """便捷入口：按 config 风格跑自选池轮动，返回引擎结果 dict。"""
    cfg = cache_cfg or {}
    engine = PortfolioEngine(
        strategy=strategy,
        initial_capital=cfg.get("initial_capital", 150000),
        max_positions=cfg.get("max_positions", 1),
        position_pct=cfg.get("position_pct", 1.0),
        full_position=cfg.get("full_position", True),
        min_buy_score=cfg.get("min_buy_score", 4),
        max_holding_days=cfg.get("max_holding_days", 0),
        watchlist_priority=cfg.get("watchlist_priority") or {},
        ranks=cfg.get("ranks") or {},
    )
    engine.load(code_dfs, start_date, end_date)
    return engine.run(verbose=verbose)

"""
SwingCtaTemplate — 单标的 vnpy CTA 基类。

设计：复用 legacy quantlab 策略类的 buy_signal/sell_signal/rebound_signal 判定逻辑
（单源，保证与旧引擎逐日信号一致），外层语义精确镜像旧引擎 run_swing_backtest 在
max_positions=1 / full_position 个股模式下的决策流：

  逐日（仅处理窗口内交易日）:
    持仓中 → 按序判定卖出: 最大持有天数 / rebound 到期·MA20 / 高位减仓(可选) /
              strategy.sell_signal(...)  → 命中即发卖出（下次 bar 开盘撮合成交）
    空仓   → strategy.buy_signal / rebound 买点 → 评分加分 → >= min_buy_score
              买入（下次 bar 开盘撮合成交）

成交语义 = vnpy BAR 原生（信号日收盘决策 → 次 bar 开盘撮合成交），与旧引擎
exec_next_open 类口径对齐；决策日期记录到 events 供 G2 信号对比。
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd
from vnpy.trader.constant import Direction, Offset
from vnpy.trader.object import BarData, OrderData, TradeData
from vnpy_ctastrategy import CtaTemplate

from ..indicators import dkey
from ..legacy_access import ensure_legacy_importable

ensure_legacy_importable()


def _ts(x):
    return pd.Timestamp(x)


class SwingCtaTemplate(CtaTemplate):
    """组合 legacy 单标的判定 + vnpy 订单/成交外壳。子类覆写 logic_class 与默认参数。"""

    author = "vnpy_quanti"
    parameters: list = [
        "initial_capital", "min_buy_score", "max_holding_days",
        "position_pct", "full_position", "leader_bonus", "apply_leader_bonus",
    ]
    variables: list = ["cash", "entry_date", "entry_price", "entry_type", "events"]

    # 参数默认值（runner 可用 config.json trading.* 覆盖）
    initial_capital: float = 150000
    min_buy_score: float = 4
    max_holding_days: int = 0
    position_pct: float = 1.0
    full_position: bool = True
    leader_bonus: float = 2
    apply_leader_bonus: bool = True

    logic_class = None          # 子类指定: quantlab 策略类
    MAX_ORDER_PRICE = 1e9       # 市价化限价单（次 bar open 撮合）

    def __init__(self, cta_engine: Any, strategy_name: str, vt_symbol: str,
                 setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)

        # 外部注入（runner 在 run_backtesting 前设置）
        self.ind_map: dict[str, dict] = {}
        self.window_dates: list = []            # 窗口交易日（pd.Timestamp 升序）

        # 状态
        self.cash: float = float(self.initial_capital)
        self.entry_date: date | None = None
        self.entry_price: float = 0.0
        self.entry_type: str = ""
        self.events: list[dict] = []

        self.logic = None
        self.has_rebound = False
        self.reduce_fn = None

        self._pending_entry_type: str = ""
        self._block_buy_date: date | None = None
        self._last_close: float | None = None      # 上一窗口交易日收盘（rebound 用）
        self._max_profit_seen: float = 0.0
        self._seen_dates: set = set()

    # ------------------------------------------------------------------ #
    # vnpy 生命周期
    # ------------------------------------------------------------------ #
    def on_init(self) -> None:
        if self.logic_class is None:
            raise RuntimeError(f"{self.__class__.__name__} 未指定 logic_class")
        self.logic = self.logic_class()
        # legacy 同款判断: 是否覆写了 BaseStrategy.rebound_signal
        from quantlab.strategies.base import BaseStrategy
        self.has_rebound = type(self.logic).rebound_signal is not BaseStrategy.rebound_signal
        self.reduce_fn = getattr(self.logic, "reduce_signal", None) or None

    def on_start(self) -> None:
        return

    def on_stop(self) -> None:
        return

    def on_bar(self, bar: BarData) -> None:
        today = bar.datetime.date()
        ds = dkey(today)
        ind = self.ind_map.get(ds)
        if ind is None:
            return                                  # 非窗口交易日: 不决策
        if ds in self._seen_dates:
            return
        self._seen_dates.add(ds)

        # —— 卖出判定（持仓中）——
        if self.pos > 0:
            self._sell_logic(today, ds, ind)
        elif self._block_buy_date == today:
            # 当日卖出刚撮合（引擎语义: 执行日禁买）
            self._block_buy_date = None
        # —— 买入判定（空仓 且 现金门槛满足）——
        elif self.cash > self.initial_capital * 0.05:
            self._buy_logic(today, ds, ind)

        if "close" in ind:
            self._last_close = float(ind["close"])

    def on_trade(self, trade: TradeData) -> None:
        vol = float(trade.volume)
        px = float(trade.price)
        if trade.offset == Offset.OPEN:             # 买入开仓
            self.cash -= vol * px
            self.entry_date = trade.datetime.date()
            self.entry_price = px
            self.entry_type = self._pending_entry_type or "swing"
            self._pending_entry_type = ""
            self._max_profit_seen = 0.0
        else:                                       # 卖出平仓
            self.cash += vol * px
            self._block_buy_date = trade.datetime.date()
            self.entry_date = None
            self.entry_price = 0.0
            self.entry_type = ""

    # ------------------------------------------------------------------ #
    # 判定（镜像旧引擎个股决策流）
    # ------------------------------------------------------------------ #
    def _holding_days(self, today: date) -> int:
        if self.entry_date is None:
            return 0
        return sum(1 for d in self.window_dates
                   if self.entry_date < _ts(d).date() <= today)

    def _sell_logic(self, today: date, ds: str, ind: dict) -> None:
        close = float(ind.get("skdj_close", ind.get("close", 0)))
        holding_days = self._holding_days(today)

        current_pnl = close / self.entry_price - 1 if self.entry_price > 0 else 0
        self._max_profit_seen = max(self._max_profit_seen, current_pnl)

        reason: str | None = None
        # 1) 最大持有天数强制卖出
        if self.max_holding_days > 0 and holding_days >= self.max_holding_days:
            reason = f"持有{holding_days}天到期"
        # 2) 超跌反弹仓专用规则
        elif self.entry_type == "rebound":
            ma20 = float(ind.get("ma20", 0))
            if holding_days >= 5:
                reason = f"反弹到期({holding_days}天)"
            elif holding_days >= 2 and ma20 > 0 and close >= ma20:
                reason = f"反弹到MA20({ma20:.2f})"
        # 3) 高位减仓（策略可选实现; 本类只读部分卖出但状态按整仓处理 → 跳过）
        # 4) 策略自身卖出信号
        if reason is None:
            is_sell, r = self.logic.sell_signal(ind, self.entry_price, holding_days,
                                                self._max_profit_seen)
            if is_sell:
                reason = r

        if reason:
            self._fire_order("SELL", ds, today, close, reason)

    def _buy_logic(self, today: date, ds: str, ind: dict) -> None:
        is_buy, score, reason = self.logic.buy_signal(ind)
        entry_type = "swing"

        # 镜像旧引擎: is_buy 仅用于是否尝试“超跌反弹”买点;
        # 最终买入门槛 = 加分后 score >= min_buy_score（策略内部 is_buy 不参与最终门槛）
        if not is_buy and self.has_rebound:
            is_rebound, r = self.logic.rebound_signal(ind, self._last_close)
            if is_rebound:
                score, reason, entry_type = 4, r, "rebound"

        # 龙头加分: 个股模式 rank=1 恒在 TOP10 → +leader_bonus（旧引擎同款）
        if self.apply_leader_bonus:
            score += self.leader_bonus
            reason = f"{reason}+龙头TOP10" if reason else "龙头TOP10"

        if score < self.min_buy_score:
            return

        close = float(ind.get("skdj_close", ind.get("close", 0)))
        if close <= 0:
            return

        # 仓位: 镜像旧引擎（full_position → 全仓; 否则按分缩放 position_pct）
        if self.full_position:
            alloc = self.cash * self.position_pct
        else:
            weight = self.position_pct * min(max(score, 0) / 10.0, 1.0)
            alloc = self.cash * weight
        if alloc < close * 100:
            return
        volume = int(alloc / close / 100) * 100      # 整手
        if volume <= 0:
            return

        self._pending_entry_type = entry_type
        self._fire_order("BUY", ds, today, close, reason, volume=volume)

    # ------------------------------------------------------------------ #
    def _fire_order(self, direction: str, ds: str, today: date, px: float,
                    reason: str, volume: float = 0) -> None:
        """发单 + 记录决策事件（事件日期 = 信号日, 供 G2 对比）。"""
        if direction == "BUY":
            self.buy(self.MAX_ORDER_PRICE, volume)
            self.events.append({"date": ds, "direction": "BUY",
                                "price": px, "volume": volume, "reason": reason})
        else:
            vol = self.pos
            if vol > 0:
                self.sell(0.0, vol)
                self.events.append({"date": ds, "direction": "SELL",
                                    "price": px, "volume": vol, "reason": reason})

    def on_order(self, order: OrderData) -> None:
        return

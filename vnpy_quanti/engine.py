"""
QuantiBacktestEngine — 镜像旧引擎“信号日收盘成交”口径的 vnpy 回测引擎扩展。

旧引擎 quantlab.trading_engine 默认(immediate)语义: 当根 bar 收盘产生信号 → 当根 bar
收盘价成交（T+1 通过 sold_today/隔日判定天然满足）。vnpy 原生 BAR 撮合为“下一根 bar 开盘”，
与本系统语义错开一天。

本子类在标准 vnpy 撮合之外增加一步：on_bar 内新挂的限价单按**当根 bar 收盘价**撮合，
从而与旧引擎 immediate 口径对齐（费率=0 时可逐笔同价）。

- same_bar_close_fill=True  (默认): 新挂单当根 bar 收盘价成交（旧引擎 immediate 口径）
- same_bar_close_fill=False: 回到 vnpy 原生（次根 bar 开盘撮合）

适用: A股日线摆动策略的判定复用层（vnpy_quanti/strategies/common.py）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from vnpy.trader.constant import Direction, Offset, Status
from vnpy.trader.object import BarData, TradeData
from vnpy_ctastrategy.backtesting import BacktestingEngine


class QuantiBacktestEngine(BacktestingEngine):
    """"""

    def __init__(self) -> None:
        super().__init__()
        self.same_bar_close_fill: bool = True

    # ------------------------------------------------------------------ #
    def new_bar(self, bar: BarData) -> None:
        """扩展 new_bar: 标准流程后, 把 on_bar 内新挂限价单按本根收盘价撮合。"""
        self.bar = bar
        self.datetime = bar.datetime

        active_before: set[str] = set(self.active_limit_orders)

        self.cross_limit_order()
        self.cross_stop_order()

        self.strategy.on_bar(bar)

        if self.same_bar_close_fill:
            self.cross_close_orders(active_before)

        self.update_daily_close(bar.close_price)

    # ------------------------------------------------------------------ #
    def cross_close_orders(self, before_ids: set[str]) -> None:
        """
        把 on_bar 期间新增（active_before 之后）的限价单按当前 bar 收盘价撮合成交。
        忽略挂单价（调用方以市价化极限价挂单，价格仅用于记录）。
        """
        if not self.strategy.trading:
            return

        for vt_orderid in list(self.active_limit_orders):
            if vt_orderid in before_ids:
                continue

            order = self.active_limit_orders[vt_orderid]

            if order.status == Status.SUBMITTING:
                order.status = Status.NOTTRADED
                self.strategy.on_order(order)

            order.traded = order.volume
            order.status = Status.ALLTRADED
            self.strategy.on_order(order)
            self.active_limit_orders.pop(vt_orderid, None)

            self.trade_count += 1

            if order.direction == Direction.LONG and order.offset == Offset.OPEN:
                pos_change = order.volume
            else:
                pos_change = -order.volume

            trade: TradeData = TradeData(
                symbol=order.symbol,
                exchange=order.exchange,
                orderid=order.orderid,
                tradeid=str(self.trade_count),
                direction=order.direction,
                offset=order.offset,
                price=float(self.bar.close_price),
                volume=order.volume,
                datetime=self.datetime,
                gateway_name=self.gateway_name,
            )

            self.strategy.pos += pos_change
            self.strategy.on_trade(trade)

            self.trades[trade.vt_tradeid] = trade

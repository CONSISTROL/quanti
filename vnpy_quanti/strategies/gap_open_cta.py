"""gap_open / gap_open_open (跳空高开隔日轮动) 的 vnpy CTA 移植。

注意: 旧引擎对 gap 策略强制“当日成交、禁 exec 延迟”；其本质是信号当日收盘(或开盘)成交的
隔日轮动——对应 QuantiBacktestEngine 的 same-bar-close 口径。gap_open_open 在旧引擎中
buy_at_open 按**信号日开盘价**成交，本移植在 bar 引擎上无法回溯当日开盘，G3 以 G2 信号
一致为准，成交价差异见 REFACTOR_TO_VNPY.md §6。
"""
from __future__ import annotations

from .common import SwingCtaTemplate


class GapOpenCta(SwingCtaTemplate):
    """收盘涨幅>=7%买入, 持有1个交易日收盘卖出 (涨停收盘复刻版)。"""

    def on_init(self) -> None:
        from quantlab.strategies.gap_open import GapOpenStrategy
        self.logic_class = GapOpenStrategy
        super().on_init()


class GapOpenOpenCta(SwingCtaTemplate):
    """开盘跳空>=3%+放量买入, 持有1个交易日尾盘卖出 (米筐模板开盘口径)。"""

    def on_init(self) -> None:
        from quantlab.strategies.gap_open import GapOpenOpenStrategy
        self.logic_class = GapOpenOpenStrategy
        super().on_init()

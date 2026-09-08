"""momentum (动量趋势) 的 vnpy CTA 移植 — 判定复用 quantlab MomentumStrategy。"""
from __future__ import annotations

from .common import SwingCtaTemplate


class MomentumCta(SwingCtaTemplate):
    """龙头股 SKDJ 超卖金叉买入, 超买死叉/止盈止损/到期卖出。"""

    def on_init(self) -> None:
        from quantlab.strategies.momentum import MomentumStrategy
        self.logic_class = MomentumStrategy
        super().on_init()

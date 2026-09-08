"""bollinger (布林线均值回归) 的 vnpy CTA 移植 — 判定复用 quantlab BollingerStrategy。"""
from __future__ import annotations

from .common import SwingCtaTemplate


class BollingerCta(SwingCtaTemplate):
    """明显跌破日线BOLL下轨买入, 反弹中轨/止损/到期卖出。"""

    def on_init(self) -> None:
        from quantlab.strategies.bollinger import BollingerStrategy
        self.logic_class = BollingerStrategy
        super().on_init()

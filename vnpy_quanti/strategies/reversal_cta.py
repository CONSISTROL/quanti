"""reversal (弱转强趋势) 的 vnpy CTA 移植 — 判定逻辑复用 quantlab ReversalStrategy。"""
from __future__ import annotations

from .common import SwingCtaTemplate


class ReversalCta(SwingCtaTemplate):
    """周线触底四要素 + 日线右侧确认 + 超跌反弹（legacy 默认策略）。"""

    logic_class = None

    def on_init(self) -> None:
        from quantlab.strategies.reversal import ReversalStrategy
        self.logic_class = ReversalStrategy
        super().on_init()

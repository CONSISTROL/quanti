"""vnpy_quanti 策略注册（CtaTemplate 子类）。"""
from __future__ import annotations

from .bollinger_cta import BollingerCta
from .common import SwingCtaTemplate
from .gap_open_cta import GapOpenCta, GapOpenOpenCta
from .momentum_cta import MomentumCta
from .reversal_cta import ReversalCta

CTA_STRATEGIES: dict[str, type[SwingCtaTemplate]] = {
    "reversal": ReversalCta,
    "bollinger": BollingerCta,
    "momentum": MomentumCta,
    "gap_open": GapOpenCta,
    "gap_open_open": GapOpenOpenCta,
    # P4: watchlist / watchlist_weekly（组合语义，见设计文档）
}


def get_cta_strategy(name: str) -> type[SwingCtaTemplate]:
    if name not in CTA_STRATEGIES:
        raise ValueError(f"未知 vnpy 策略: {name}, 可选: {', '.join(CTA_STRATEGIES)}")
    return CTA_STRATEGIES[name]

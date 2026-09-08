"""vnpy_quanti 策略注册（CtaTemplate 子类）。"""
from __future__ import annotations

from .common import SwingCtaTemplate
from .reversal_cta import ReversalCta

CTA_STRATEGIES: dict[str, type[SwingCtaTemplate]] = {
    "reversal": ReversalCta,
    # P3: bollinger / momentum / gap_open / gap_open_open
}


def get_cta_strategy(name: str) -> type[SwingCtaTemplate]:
    if name not in CTA_STRATEGIES:
        raise ValueError(f"未知 vnpy 策略: {name}, 可选: {', '.join(CTA_STRATEGIES)}")
    return CTA_STRATEGIES[name]

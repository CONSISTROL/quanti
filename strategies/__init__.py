"""
策略注册表 — 通过 config.json 的 trading.strategy 选择策略

可选策略:
  reversal  弱转强趋势 (周线触底四要素+右侧确认+超跌反弹) [默认]
  momentum  动量趋势 (龙头+SKDJ超卖金叉)
  bollinger 布林线均值回归 (收盘≤下轨买, ≥中轨卖)
"""
from .base import BaseStrategy
from .reversal import ReversalStrategy
from .momentum import MomentumStrategy
from .bollinger import BollingerStrategy

STRATEGIES = {
    'reversal': ReversalStrategy,
    'momentum': MomentumStrategy,
    'bollinger': BollingerStrategy,
}


def get_strategy(name):
    """按名称获取策略实例"""
    if name is None:
        name = 'reversal'
    cls = STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f'未知策略: {name}, 可选: {", ".join(STRATEGIES)}')
    return cls()


def strategy_label(name):
    """获取策略显示名称"""
    cls = STRATEGIES.get(name)
    return cls.label if cls else name

"""
布林线均值回归策略 (收盘≤下轨买, ≥中轨或亏5%卖)

适合震荡股: 跌破下轨低吸, 反弹到中轨卖出
风险: 单边下跌中连续破轨会接飞刀 (可用周线超卖过滤)
"""
from .base import BaseStrategy


class BollingerStrategy(BaseStrategy):
    name = 'bollinger'
    label = '布林线均值回归 (收盘≤下轨买, ≥中轨卖)'
    description = '跌破日线BOLL下轨买入, 反弹到中轨或止损5%卖出'

    def buy_signal(self, ind, **ctx):
        """收盘价 <= 日线BOLL下轨 买入 (全仓)"""
        close = ind.get('skdj_close', ind.get('close', 0))
        lower = ind.get('boll_low', 0)
        if lower > 0 and close <= lower:
            return True, 4, f'跌破BOLL下轨(价/下轨={close / lower:.3f})'
        return False, 0, ''

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """收盘价 >= 中轨(MA20) 或 亏损 >= 5% 卖出"""
        close = ind.get('skdj_close', ind.get('close', 0))
        middle = ind.get('ma20', 0)
        pnl = (close / entry_price - 1) if entry_price > 0 else 0

        if middle > 0 and close >= middle:
            return True, f'反弹到中轨(MA20={middle:.2f})'

        if pnl <= -0.05:
            return True, f'止损({pnl:.1%})'

        return False, ''

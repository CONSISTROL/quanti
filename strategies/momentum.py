"""
动量趋势策略 (龙头股 + SKDJ超卖金叉)

买入: SKDJ金叉 + K<50超卖区 (深度超卖加分), J超卖/缩量/MA60支撑/长期趋势加分
卖出: SKDJ超买死叉(K>65), 止盈>=8%, 止损-3%, J超买+盈利, 到期20天
"""
from .base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    name = 'momentum'
    label = '动量趋势 (龙头+SKDJ超卖金叉)'
    description = '龙头股SKDJ超卖金叉买入, 超买死叉/止盈止损卖出'

    def buy_signal(self, ind, **ctx):
        """龙头股 + SKDJ超卖金叉买入"""
        score = 0
        reasons = []

        price = ind.get('close', 0)
        if price <= 0:
            return False, 0, ''

        k = ind['skdj_k']
        j = ind['skdj_j']
        cross = ind['skdj_cross']

        # ---- SKDJ金叉 (必须) ----
        if cross != 1:
            return False, 0, ''

        # ---- 超卖区 ----
        if k > 50:
            return False, 0, ''

        if k < 15:
            score += 5
            reasons.append(f'深度超卖金叉(K={k:.0f})')
        elif k < 25:
            score += 4
            reasons.append(f'超卖金叉(K={k:.0f})')
        elif k < 35:
            score += 3
            reasons.append(f'低位金叉(K={k:.0f})')
        else:
            score += 2
            reasons.append(f'中位金叉(K={k:.0f})')

        # J值加分
        if j < -10:
            score += 2
            reasons.append(f'J极超卖({j:.0f})')
        elif j < 0:
            score += 1
            reasons.append(f'J超卖({j:.0f})')

        # 缩量回调 (龙头洗盘)
        vol_ratio = ind.get('vol_ratio', 1.0)
        if vol_ratio < 0.7:
            score += 2
            reasons.append(f'缩量洗盘({vol_ratio:.1f}x)')
        elif vol_ratio < 0.9:
            score += 1
            reasons.append(f'缩量({vol_ratio:.1f}x)')

        # MA60支撑
        ma60 = ind.get('ma60', 0)
        import numpy as np
        if not np.isnan(ma60) and ma60 > 0:
            dist = (price - ma60) / ma60
            if -0.03 < dist < 0.03:
                score += 1
                reasons.append('MA60支撑')

        # 长期趋势
        ma120 = ind.get('ma120', 0)
        if not np.isnan(ma120) and price > ma120:
            score += 1
            reasons.append('长期↑')

        is_buy = score >= 6
        return is_buy, score, '+'.join(reasons)

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """SKDJ卖出信号:
        1. SKDJ超买死叉 (K>65)
        2. 止盈 >= 8%
        3. 止损 <= -3%
        4. J超买 + 盈利
        5. 到期 >= 20天
        """
        price = ind.get('close', 0)
        pnl = (price / entry_price - 1) if entry_price > 0 else 0
        k = ind['skdj_k']
        cross = ind['skdj_cross']
        j = ind['skdj_j']

        if cross == -1 and k > 65:
            return True, f'SKDJ超买死叉(K={k:.0f})'

        if pnl >= 0.08:
            return True, f'止盈({pnl:.1%})'

        if pnl <= -0.03:
            return True, f'止损({pnl:.1%})'

        if j > 100 and pnl > 0:
            return True, f'J超买(J={j:.0f},{pnl:+.1%})'

        if holding_days >= 20:
            return True, f'到期({holding_days}天{pnl:+.1%})'

        return False, ''

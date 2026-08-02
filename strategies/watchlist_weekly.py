"""
自选轮动策略 (周线版) — 买卖信号全部基于周线指标

同一时刻只满仓自选池中"最强"的一只, 判断全用动态周线指标:
  周线强弱分 = 周线趋势(站上/上行周线MA5) + 周线动能(DIF-DEA回升/金叉状态)
               + 周线超卖机会(贴近/跌破周线下轨, SKDJ低位)
  买入: 周线强弱分>=门槛 + 位置过滤(周线SKDJ非高位) + 动能确认(周线MACD回升)
  卖出: 周线SKDJ死叉 / 跌破周线MA5 / 止损-5%
"""
from .base import BaseStrategy

WATCH_BUY_MIN = 5       # 周线强弱分门槛
WATCH_WEEK_K_HIGH = 65  # 周线SKDJ高位阈值


class WatchlistWeeklyStrategy(BaseStrategy):
    name = 'watchlist_weekly'
    label = '自选轮动·周线版 (纯周线指标买卖)'
    description = '周线强弱评分+单持仓满仓, 周线金叉/动能/超卖判断买卖'

    def strength_score(self, ind):
        """周线强弱分 (0~10): 周线趋势+动能+超卖机会"""
        close = ind.get('skdj_close', ind.get('close', 0))
        score = 0
        reasons = []

        wk_ma5 = ind.get('wk_ma5', 0)
        wk_boll_low = ind.get('wk_boll_low', 0)
        wk_k = ind.get('skdj_weekly_k_dyn', 50)

        # ---- 周线趋势 (0~4) ----
        if wk_ma5 > 0 and close > wk_ma5:
            score += 2
            reasons.append('站上周线MA5')
        if ind.get('wk_ma5_rise', False):
            score += 2
            reasons.append('周线MA5上行')

        # ---- 周线动能 (0~3) ----
        if ind.get('wk_macd_rise', False):
            score += 2
            reasons.append('周线MACD回升')
        if ind.get('wk_dif_dea', 0) > 0:
            score += 1
            reasons.append('周线金叉状态')

        # ---- 周线超卖反弹机会 (0~3) ----
        if wk_boll_low > 0:
            ratio = close / wk_boll_low
            if ratio < 0.99:
                score += 2
                reasons.append(f'周线跌破下轨(价/下轨={ratio:.2f})')
            elif ratio < 1.05:
                score += 1
                reasons.append('周线贴近下轨')
        if wk_k < 30:
            score += 1
            reasons.append(f'周线SKDJ低位(K={wk_k:.0f})')

        return score, '+'.join(reasons)

    def buy_signal(self, ind, **ctx):
        """周线强弱分买入 + 位置/动能过滤"""
        # 动能确认: 周线MACD回升 (DIF-DEA较前日增长)
        if not ind.get('wk_macd_rise', False):
            return False, 0, '周线MACD未回升'

        # 位置: 周线SKDJ非高位
        wk_k = ind.get('skdj_weekly_k_dyn', 50)
        if wk_k > WATCH_WEEK_K_HIGH:
            return False, 0, f'周线SKDJ高位(K={wk_k:.0f})'

        score, reason = self.strength_score(ind)
        if score >= WATCH_BUY_MIN:
            return True, score, f'周线强弱分{score}: {reason}'
        return False, score, ''

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """周线转弱卖出:
        1. 周线SKDJ死叉状态 (K<D 且 K从高位回落)
        2. 跌破周线MA5 且 周线MA5下行
        3. 止损: -5%
        """
        close = ind.get('skdj_close', ind.get('close', 0))
        pnl = (close / entry_price - 1) if entry_price > 0 else 0

        wk_k = ind.get('skdj_weekly_k_dyn', 50)
        wk_d = ind.get('skdj_weekly_d_dyn', 50)
        wk_ma5 = ind.get('wk_ma5', 0)

        # 止损
        if pnl <= -0.05:
            return True, f'止损({pnl:.1%})'

        # 周线SKDJ死叉 (K<D 且 K 不低)
        if wk_k < wk_d and wk_k < 60:
            return True, f'周线SKDJ死叉(K={wk_k:.0f}<D={wk_d:.0f})'

        # 跌破周线MA5 且 周线MA5下行
        if wk_ma5 > 0 and close < wk_ma5 and not ind.get('wk_ma5_rise', False):
            return True, f'跌破周线MA5({wk_ma5:.3f})趋势转弱'

        return False, ''

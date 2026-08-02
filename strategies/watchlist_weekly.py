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

    def _daily_stop_signal(self, ind):
        """日线止跌信号 (必须): SKDJ低位金叉 / MACD金叉 / BOLL明显下穿下轨
        弱止跌(阴线缩)不算 - 2025-03-10纳指ETF SKDJ死叉中仅阴线缩, 误买"""
        k = ind.get('skdj_k', 50)
        if ind.get('skdj_cross', 0) == 1 and k < 50:
            return f'日线SKDJ低位金叉(K={k:.0f})'
        if ind.get('macd_cross', 0) == 1:
            return '日线MACD金叉'
        close = ind.get('skdj_close', ind.get('close', 0))
        boll_low = ind.get('boll_low', 0)
        if boll_low > 0 and close < boll_low * 0.97:
            return f'日线深跌破BOLL下轨(价/下轨={close / boll_low:.2f})'
        return None

    def buy_signal(self, ind, **ctx):
        """周线强弱分买入 + 周线位置过滤 + 日线止跌确认"""
        # 动能确认: 周线MACD回升 (DIF-DEA较前日增长)
        if not ind.get('wk_macd_rise', False):
            return False, 0, '周线MACD未回升'

        # 位置: 周线SKDJ非高位
        wk_k = ind.get('skdj_weekly_k_dyn', 50)
        if wk_k > WATCH_WEEK_K_HIGH:
            return False, 0, f'周线SKDJ高位(K={wk_k:.0f})'

        score, reason = self.strength_score(ind)
        if score < WATCH_BUY_MIN:
            return False, score, ''

        # 日线止跌确认 (必须): 止跌信号出现 或 BOLL明显下穿LOW
        stop = self._daily_stop_signal(ind)
        if stop is None:
            return False, 0, '日线未止跌(无SKDJ金叉/MACD金叉/深跌破轨)'

        return True, score, f'周线强弱分{score}: {reason}+{stop}'

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """周线转弱卖出 + 日线高位缩小提前止盈:
        1. 日线SKDJ高位(K>70) 且 DIF-DEA缩小 → 提前止盈控制回撤
        2. 周线SKDJ死叉状态 (K<D 且 K从高位回落)
        3. 跌破周线MA5 且 周线MA5下行
        4. 止损: -5%
        """
        close = ind.get('skdj_close', ind.get('close', 0))
        pnl = (close / entry_price - 1) if entry_price > 0 else 0

        wk_k = ind.get('skdj_weekly_k_dyn', 50)
        wk_d = ind.get('skdj_weekly_d_dyn', 50)
        wk_ma5 = ind.get('wk_ma5', 0)

        # 止损
        if pnl <= -0.05:
            return True, f'止损({pnl:.1%})'

        # 日线SKDJ高位(K>75) + DIF-DEA连续2天缩小 + 有浮盈 → 提前止盈 (控制回撤)
        dk = ind.get('skdj_k', 50)
        if dk > 75 and ind.get('hist_fall_win', False) and pnl > 0:
            return True, f'日线高位+MACD缩2天(K={dk:.0f})'

        # 周线SKDJ死叉 (K<D 且 K 不低)
        if wk_k < wk_d and wk_k < 60:
            return True, f'周线SKDJ死叉(K={wk_k:.0f}<D={wk_d:.0f})'

        # 跌破周线MA5 且 周线MA5下行
        if wk_ma5 > 0 and close < wk_ma5 and not ind.get('wk_ma5_rise', False):
            return True, f'跌破周线MA5({wk_ma5:.3f})趋势转弱'

        return False, ''

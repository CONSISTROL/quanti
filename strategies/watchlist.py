"""
自选轮动策略 (单持仓满仓 + 强弱评分 + 卖弱买强)

同一时刻只满仓自选池中"最强"的一只:
  强弱分 = 趋势分 + 动量分 + 超跌反弹机会分 + 强势确认分
  空仓时: 买入分数最高且>=门槛的自选股
  持仓时: 持仓股转弱(跌破MA20且MA5<MA10 / SKDJ高位死叉 / 止损) → 卖出 → 次日买入当前最强

适用: config.json watchlist 指定的自选股列表 (可含ETF/LOF)
"""
from .base import BaseStrategy

WATCH_BUY_MIN = 4       # 强弱分门槛: 达到才买入 (空仓等待弱市)
WATCH_SELL_MA = 20      # 趋势破坏判定均线


class WatchlistStrategy(BaseStrategy):
    name = 'watchlist'
    label = '自选轮动 (强弱评分+单持仓满仓+卖弱买强)'
    description = '每天给自选股打强弱分, 满仓持有最强的一只, 转弱换股'

    def strength_score(self, ind):
        """强弱分: 趋势+动量+超跌机会+强势确认 (0~10)"""
        close = ind.get('skdj_close', ind.get('close', 0))
        score = 0
        reasons = []

        ma5 = ind.get('ma5', 0)
        ma10 = ind.get('ma20', 0)  # 引擎无ma10, 用ma20近似
        ma20 = ind.get('ma20', 0)
        ma60 = ind.get('ma60', 0)

        # ---- 趋势分 (0~4) ----
        if ma20 > 0 and close > ma20:
            score += 2
            reasons.append('站上MA20')
        if ma5 > 0 and ma20 > 0 and ma5 > ma20:
            score += 1
            reasons.append('MA5>MA20')
        if ma60 > 0 and close > ma60:
            score += 1
            reasons.append('站上MA60')

        # ---- 动量分 (0~2) ----
        ret20 = ind.get('ret_20d', 0)
        ret60 = ind.get('ret_60d', 0)
        if ret20 > 0:
            score += 1
            reasons.append('20日为正')
        if ret60 > 0:
            score += 1
            reasons.append('60日为正')

        # ---- 超跌反弹机会 (0~4): 回调到支撑位的机会 ----
        lower = ind.get('boll_low', 0)
        if lower > 0:
            ratio = close / lower
            if ratio < 0.96:
                score += 4
                reasons.append(f'深度超跌(价/下轨={ratio:.2f})')
            elif ratio < 0.99:
                score += 3
                reasons.append(f'明显超跌(价/下轨={ratio:.2f})')
            elif ratio < 1.02:
                score += 1
                reasons.append('贴近下轨')

        # ---- 强势确认 (0~1) ----
        if ind.get('macd_gold_win', False):
            score += 1
            reasons.append('MACD金叉状态')

        return score, '+'.join(reasons)

    def buy_signal(self, ind, **ctx):
        """强弱分最高的买入 (引擎按分数排序选最强)
        位置/动能过滤 (不在高位/动能减弱/趋势停滞时买入):
          1. DIF-DEA较前日上升 (动能增强)
          2. MA5上行 (趋势确认)
          3. SKDJ非高位 (K<=65, 不在高位追入)
        """
        # 动能: DIF-DEA较前日上升
        if not ind.get('hist_rise', False):
            return False, 0, 'DIF-DEA未回升'

        # 趋势: MA5上行
        ma5 = ind.get('ma5', 0)
        ma5_prev = ind.get('ma5_prev', 0)
        if ma5 <= ma5_prev:
            return False, 0, 'MA5未上行'

        # 位置: SKDJ非高位
        k = ind.get('skdj_k', 50)
        if k > 65:
            return False, 0, f'SKDJ高位(K={k:.0f})'

        score, reason = self.strength_score(ind)
        if score >= WATCH_BUY_MIN:
            return True, score, f'强弱分{score}: {reason}'
        return False, score, ''

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """持仓转弱卖出:
        1. 趋势破坏: close<MA20 且 MA5<MA20
        2. SKDJ高位死叉 (K>70且死叉)
        3. 止损: -5%
        """
        close = ind.get('skdj_close', ind.get('close', 0))
        pnl = (close / entry_price - 1) if entry_price > 0 else 0

        ma5 = ind.get('ma5', 0)
        ma20 = ind.get('ma20', 0)

        # 止损
        if pnl <= -0.05:
            return True, f'止损({pnl:.1%})'

        # 趋势破坏
        if ma20 > 0 and close < ma20 and ma5 > 0 and ma5 < ma20:
            return True, f'跌破MA20趋势转弱(MA5={ma5:.2f}<MA20={ma20:.2f})'

        # SKDJ高位死叉
        k = ind.get('skdj_k', 50)
        if k > 70 and ind.get('skdj_cross', 0) == -1:
            return True, f'SKDJ高位死叉(K={k:.0f})'

        return False, ''

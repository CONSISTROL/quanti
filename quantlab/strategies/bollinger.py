"""
布林线均值回归策略 (明显跌破下轨买, ≥中轨或亏5%卖)

适合震荡股: 明显跌破下轨(价/下轨<0.99)低吸, 反弹到中轨卖出
优化: 仅深度跌破(低于下轨1%+)才买 - 刚跌破(0.99~1.0)胜率极低(+0.9% vs +5.7%)
风险: 单边下跌中连续破轨会接飞刀 (可用周线超卖过滤)
"""
from .base import BaseStrategy

BOLL_BUY_DEPTH = 0.99       # 明显跌破阈值: 价/下轨 < 0.99 (低于下轨1%+) 才买入
BOLL_MAX_HOLD_DAYS = 4      # 最大持有天数: 4个交易日到期 (平均持股约3.5天)
BOLL_SELL_SPIKE = 0.05      # 单日涨幅>=5% 视为反弹高潮, 卖出落袋


class BollingerStrategy(BaseStrategy):
    name = 'bollinger'
    label = '布林线均值回归 (明显跌破下轨买, ≥中轨卖)'
    description = '明显跌破日线BOLL下轨(1%+)买入, 反弹到中轨或止损5%卖出'

    def buy_signal(self, ind, **ctx):
        """收盘价明显跌破日线BOLL下轨 (价/下轨 < 0.99) 买入
        评分差异化 (用于全市场择优): 超跌越深 + 周线越超卖 → 分越高 → 优先买入"""
        close = ind.get('skdj_close', ind.get('close', 0))
        lower = ind.get('boll_low', 0)
        if lower <= 0 or close > lower * BOLL_BUY_DEPTH:
            return False, 0, ''

        # 周线MACD深死叉 (DIF-DEA<-0.02) 不买: 周线仍在深跌的破轨=下跌中继, 非超跌反弹
        if ind.get('wk_dif_dea', 0) < -0.02:
            return False, 0, ''

        score = 4
        reasons = [f'明显跌破BOLL下轨(价/下轨={close / lower:.3f})']

        # 超跌越深, 反弹空间越大 → 加分
        depth = close / lower
        if depth < 0.96:
            score += 2
            reasons.append('深度超跌')
        elif depth < 0.98:
            score += 1

        # 周线超卖确认 (周K越低越超卖, 反弹越可靠) → 加分
        weekly_k = ind.get('skdj_weekly_k', 50)
        if weekly_k < 20:
            score += 2
            reasons.append(f'周线超跌(K={weekly_k:.0f})')
        elif weekly_k < 30:
            score += 1
            reasons.append(f'周线超卖(K={weekly_k:.0f})')

        return True, score, '+'.join(reasons)

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """快进快出: 最大持有4个交易日到期 (平均持股约3.5天)
        延长规则: 到期日仍明显超跌(仍跌破下轨) 或 日线SKDJ D-K在缩小(走强中) → 延长持有等反弹
        卖出规则: 单日高涨幅(>=5%)落袋 / 反弹到中轨 / 止损5%"""
        close = ind.get('skdj_close', ind.get('close', 0))
        middle = ind.get('ma20', 0)
        lower = ind.get('boll_low', 0)
        pnl = (close / entry_price - 1) if entry_price > 0 else 0

        # 单日高涨幅 → 反弹高潮, 落袋为安
        prev_close = ind.get('prev_close', 0)
        if prev_close > 0:
            chg = close / prev_close - 1
            if chg >= BOLL_SELL_SPIKE:
                return True, f'单日大涨({chg:.1%})落袋'

        # 快进快出: 到期强制卖出 (平均持股时间约3.5天)
        if holding_days >= BOLL_MAX_HOLD_DAYS:
            # 延长: 当日仍明显超跌(反弹还没来) 或 SKDJ D-K在缩小(走强中, 反弹进行时)
            if lower > 0 and close <= lower * BOLL_BUY_DEPTH:
                return False, ''
            if ind.get('skdj_dk_shrink', False):
                return False, ''
            return True, f'到期({holding_days}天, {pnl:+.1%})'

        if middle > 0 and close >= middle:
            return True, f'反弹到中轨(MA20={middle:.2f})'

        if pnl <= -0.05:
            return True, f'止损({pnl:.1%})'

        return False, ''

"""
弱转强趋势策略 (周线触底四要素 + 日线右侧确认 + 短线超跌反弹)

周线定趋势定触底 (四要素):
  1. 周线SKDJ触底: 上一完成周 K<20 且 0<K-D<5 (K刚上穿D, 股价触底)
  2. 周线超卖: 价格贴近/低于周线BOLL下轨 (价/下轨<1.05, 未跌透的假触底不做)
  3. 周线MACD止跌: DIF-DEA较前日增长, 且非深死叉(DIF-DEA>=-0.02)
  4. 周线MA5止跌 (加分): 周线MA5拐头向上

日线定买卖点 (右侧交易):
  - 买入: MACD金叉(阴转阳) 或 右侧确认(MA5止跌转涨+DIF-DEA回升), SKDJ近3天无死叉, K<=65
  - 卖出: 周线/日线SKDJ高位死叉(K>60), 止损-5%
  - 短线超跌反弹: 日线深跌破BOLL下轨(价/下轨<0.97) + 当日急跌>=4% + 周线K<20
"""
import numpy as np

from .base import BaseStrategy


class ReversalStrategy(BaseStrategy):
    name = 'reversal'
    label = '弱转强趋势 (周线触底四要素+右侧确认+超跌反弹)'
    description = '周线SKDJ触底+BOLL超卖+MACD止跌确认趋势, 日线右侧确认买卖点'

    def buy_signal(self, ind, **ctx):
        """周线触底四要素 + 日线确认"""
        score = 0
        reasons = []

        price = ind.get('skdj_close', ind.get('close', 0))
        k = ind.get('skdj_k', 50)
        skdj_cross = ind.get('skdj_cross', 0)
        macd_cross = ind.get('macd_cross', 0)
        macd_hist = ind.get('macd_hist', 0)
        ma5 = ind.get('ma5', 0)
        ma5_prev = ind.get('ma5_prev', 0)

        # 条件0: 周线判断 — 触底确认 (四个要素)
        # 用冻结视图(上一完成周的最终值): 周线金叉/死叉以上周收盘确认, 与行情软件一致
        weekly_k = ind.get('skdj_weekly_k', 50)              # 冻结周线K
        weekly_d = ind.get('skdj_weekly_d', 50)              # 冻结周线D
        k_minus_d = weekly_k - weekly_d
        close = ind.get('skdj_close', ind.get('close', 0))
        boll_low = ind.get('wk_boll_low', 0)

        # 要素2 (必须): 周线SKDJ触底 — K<20 且 0<K-D<5 (K刚上穿D, 股价触底)
        if not (weekly_k < 20 and 0 < k_minus_d < 5):
            return False, 0, f'周线SKDJ未触底(K={weekly_k:.0f} K-D={k_minus_d:.1f})'

        # 要素3 (必须): 周线MACD止跌增长 (DIF-DEA较前日上涨)
        if not ind.get('wk_macd_rise', False):
            return False, 0, '周线MACD未止跌'

        # 周线MACD深死叉 (DIF-DEA<-0.02) 不交易: 周线级别还在深跌, 行情不确定
        wk_diff = ind.get('wk_dif_dea', 0)
        if wk_diff < -0.02:
            return False, 0, f'周线MACD深死叉(DIF-DEA={wk_diff:.3f})不做'

        # 要素1 (必须): 价格贴近/低于周线BOLL下轨 (超卖区) — 未跌透的假触底不做
        if boll_low <= 0 or close > boll_low * 1.05:
            return False, 0, f'价格未到周线超卖区(价/下轨={close / boll_low:.2f})' if boll_low > 0 else '周线BOLL无数据'

        score += 4
        reasons.append(f'周线SKDJ触底(K={weekly_k:.0f} K-D={k_minus_d:.1f})+MACD止跌+周线超卖')

        # 要素1 加强 (加分): 跌破BOLL下轨 — 深度超卖
        if close < boll_low:
            score += 1
            reasons.append('周线破BOLL下轨')

        # 要素4 (加分): 周线MA5止跌转涨
        if ind.get('wk_ma5_rise', False):
            score += 1
            reasons.append('周线MA5拐头')

        # 日线BOLL下轨下方 (日线超卖, 可做反弹) — 加分项
        boll_low_d = ind.get('boll_low', 0)
        if boll_low_d > 0 and close < boll_low_d:
            score += 1
            reasons.append('日线破BOLL下轨')

        # 条件1: 日线MACD金叉 (阴转阳) - 核心买入信号 (必须)
        if macd_cross == 1:
            score += 3
            reasons.append(f'MACD阴转阳金叉(DIF={ind.get("dif", 0):.3f})')
        else:
            # 右侧确认 (金叉前的趋势反转): MA5止跌转涨 且 DIF-DEA较前一日上涨
            # (替代左侧的"阴线缩"抄底信号, 符合右侧交易原则)
            if ma5 > ma5_prev and ind.get('hist_rise', False):
                score += 2
                reasons.append('MA5拐头+DIF回升')
            else:
                return False, 0, ''

        # 条件2 (必须): 日线SKDJ近3天无死叉 (周线定趋势, 日线定买卖点)
        if skdj_cross == -1:
            return False, 0, f'日线SKDJ死叉(K={k:.0f})'

        # 条件3 (必须): 日线SKDJ高位 (K>65) 不买 - 不在高位追入
        if k > 65:
            return False, 0, f'日线SKDJ高位(K={k:.0f})'

        # 加分: 日线SKDJ低位金叉 (K<40)
        if skdj_cross == 1 and k < 40:
            score += 2
            reasons.append(f'日线SKDJ低位金叉(K={k:.0f})')

        # 额外加分: MA5拐头
        if not np.isnan(ma5) and not np.isnan(ma5_prev) and ma5 > ma5_prev:
            score += 1
            reasons.append('MA5↑')

        is_buy = score >= 4
        return is_buy, score, '+'.join(reasons)

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """趋势跟踪卖出 (持有到趋势结束):
        1. 周线SKDJ高位死叉 (K>60且K下穿D) — 最强卖出
        2. 日线SKDJ高位死叉 (K>60且K下穿D)
        3. 止损: -5%
        """
        price = ind.get('skdj_close', ind.get('close', 0))
        pnl = (price / entry_price - 1) if entry_price > 0 else 0
        k = ind.get('skdj_k', 50)
        skdj_cross = ind.get('skdj_cross', 0)

        # 条件1: 周线SKDJ高位死叉 (K>60且死叉) — 最强卖出信号
        weekly_k = ind.get('skdj_weekly_k', 50)
        weekly_cross = ind.get('skdj_weekly_cross', 0)
        if weekly_k > 60 and weekly_cross == -1:
            return True, f'周线SKDJ高位死叉(K={weekly_k:.0f})'

        # 条件2: 日线SKDJ高位死叉 (K>60且死叉)
        if k > 60 and skdj_cross == -1:
            return True, f'日线SKDJ高位死叉(K={k:.0f})'

        # 条件3: 止损
        if pnl <= -0.05:
            return True, f'止损({pnl:.1%})'

        return False, ''

    def rebound_signal(self, ind, prev_close=None):
        """短线超跌反弹买点 (日线BOLL明显超卖):
        1. 收盘价明显跌破日线BOLL下轨 (价/下轨 < 0.97, 深跌破轨=明显超跌)
        2. 当日跌幅 >= 4% (急跌超卖, 非阴跌)
        3. 周线SKDJ K<20 (周线级别超卖确认)
        卖出: 反弹到日线BOLL中轨(MA20) 或 持有5个交易日到期 (由引擎处理)
        """
        close = ind.get('skdj_close', ind.get('close', 0))
        boll_low = ind.get('boll_low', 0)
        if boll_low <= 0 or close >= boll_low * 0.97:
            return False, ''

        # 当日跌幅 (前一交易日收盘, 必须可计算)
        prev_close = prev_close if prev_close is not None else ind.get('prev_close', 0)
        if prev_close <= 0:
            return False, ''
        chg = close / prev_close - 1
        if chg > -0.04:
            return False, ''

        weekly_k = ind.get('skdj_weekly_k', 50)  # 冻结周线K (上周收盘确认)
        if weekly_k >= 20:
            return False, ''

        return True, f'日线破BOLL下轨超卖(跌{chg:.1%})+周线超跌(K={weekly_k:.0f})'

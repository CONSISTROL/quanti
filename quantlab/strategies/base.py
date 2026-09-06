"""
策略基类 — 所有策略继承此接口

策略职责:
  buy_signal(ind, **ctx)     -> (is_buy, score, reason)   判断买入
  sell_signal(ind, entry_price, holding_days, max_profit_seen) -> (is_sell, reason)  判断卖出
  rebound_signal(ind, prev_close) -> (is_buy, reason)      可选: 短线超跌反弹买点

ind 为预计算的单日指标字典 (来自 indicator_cache), 包含:
  skdj_k/d/j, skdj_cross, macd_cross, dif/dea/macd_hist, ma5/ma20/ma60/ma120,
  skdj_weekly_k/d (冻结周线), skdj_weekly_k_dyn/d_dyn (动态周线),
  wk_dif_dea/wk_macd_rise (动态周线MACD), wk_boll_low (周线下轨),
  boll_low (日线下轨), prev_close, k_up_win/max_k_5d/k_dn_win 等窗口flags
"""


class BaseStrategy:
    """策略基类"""
    name = 'base'
    label = '基础策略'
    description = ''

    def buy_signal(self, ind, **ctx):
        """买入信号. 返回 (is_buy, score, reason)"""
        raise NotImplementedError

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """卖出信号. 返回 (is_sell, reason)"""
        raise NotImplementedError

    def rebound_signal(self, ind, prev_close=None):
        """可选: 短线超跌反弹买点 (不实现则返回False)"""
        return False, ''

    def is_death_cross(self, ind):
        """可选: 当日是否为SKDJ高位死叉状态 (供引擎 death_cross_confirm 连续确认用)"""
        return False

"""
跳空高开隔日轮动策略 (GapOpen)

参考米筐(Ricequant)模板思路: 问财条件选股 + 开盘买入 + 次日卖出
本地无问财接口, 用本地数据近似模板条件:
  跳空高开>3%   → open/prev_close - 1 >= GAP_MIN        (可精确计算)
  主力净流入    → 无资金流数据, 用放量确认近似 vol_ratio >= VOL_RATIO_MIN
  低估值/市值<200亿 → 无历史财务数据(最新财报回测历史有前视偏差), 跳过

执行方式 (引擎配合):
  开盘决策 → 当日开盘价买入 (引擎为 gap_open 提供 ctx['open'], 买入价=开盘价)
  持有满 HOLD_DAYS (默认1个交易日) → 次日收盘价卖出 (引擎默认当日收盘成交)
"""
from .base import BaseStrategy

GAP_MIN = 0.03        # 跳空高开阈值: 开盘价/昨收 - 1 >= 3%
VOL_RATIO_MIN = 1.5   # 放量确认: 近5日均量/近20日均量 (主力净流入的代理条件)
HOLD_DAYS = 1         # 隔日轮动: 持有1个交易日后收盘卖出
STOP_LOSS = -0.05     # 止损兜底 (持有期大跌保护; 1日轮动下通常与到期卖出同日生效)
MIN_PRICE = 1.0       # 排除低价仙股


class GapOpenStrategy(BaseStrategy):
    name = 'gap_open'
    label = '跳空高开隔日轮动 (开盘决策+开盘买入+次日收盘卖出)'
    description = '全市场扫描跳空高开>3%且放量的股票, 开盘价买入, 持有1个交易日收盘卖出'

    # 引擎/主流程在该策略下使用的推荐参数 (main.py 应用, 不写入 config.json)
    recommended = {
        'max_positions': 5,         # 模板 max_size=5
        'full_position': False,     # 等分资金 (不按强弱分缩放)
        'position_pct': 0.2,        # 5只等分: 每只投入20%
        'min_buy_score': 5,         # 布尔条件给固定8分, 门槛不约束
    }

    def buy_signal(self, ind, **ctx):
        """跳空高开 + 放量确认. 开盘决策, 当日开盘价由引擎传入 ctx['open']"""
        open_px = ctx.get('open')
        if not open_px or open_px <= 0:
            return False, 0, '无开盘价'
        if open_px < MIN_PRICE:
            return False, 0, f'低价股({open_px:.2f}<{MIN_PRICE}元)'
        prev_close = ind.get('prev_close', 0)
        if prev_close <= 0:
            return False, 0, '无昨收'
        gap = open_px / prev_close - 1
        if gap < GAP_MIN:
            return False, 0, f'跳空不足({gap:.2%})'
        vol_ratio = ind.get('vol_ratio', 1.0) or 0
        if vol_ratio < VOL_RATIO_MIN:
            return False, 0, f'量能不足({vol_ratio:.2f}倍)'
        return True, 8, f'跳空高开{gap:.2%}+放量({vol_ratio:.2f}倍)'

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """持有满 HOLD_DAYS 个交易日收盘卖出 (隔日轮动); 止损兜底"""
        close = ind.get('skdj_close', ind.get('close', 0))
        pnl = (close / entry_price - 1) if entry_price > 0 else 0
        if pnl <= STOP_LOSS:
            return True, f'止损({pnl:.1%})'
        if holding_days >= HOLD_DAYS:
            return True, f'持有{HOLD_DAYS}日到期(隔日轮动)'
        return False, ''

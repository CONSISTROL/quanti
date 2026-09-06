"""
跳空高开隔日轮动策略 (GapOpen) — 涨停收盘复刻版

来源: report_20260802.html 逆向工程 (该策略代码当时未提交, 已丢失)
报告实测特征 (2025-01-02 ~ 2026-08-02 全市场, +3160.9%/年化+896%/Sharpe 7.63/回撤9.2%/1905笔/胜率56%):
  买入: 当日收盘涨幅 >= 7% (主体为 10% 涨停收盘股; 以收盘价成交 — 报告买入价=当日收盘价)
  卖出: 持有满 1 个交易日收盘卖出, 无止损 (报告 1905 笔卖出全部为"持有1天到期")
  仓位: 5 只等分 (每笔=当前现金×20%)

执行方式: 当日收盘决策 → 当日收盘价买入 → 次日收盘价卖出 (引擎默认模式)
注意: 策略名沿用报告的"跳空高开"叫法, 实际条件为收盘涨幅 (报告中 600186 开盘仅+4.6%
但收盘涨停+10.0%被买入, 买入价=收盘价, 证实为收盘口径)。数据快照差异会显著改变
该策略的选股结果 (前复权基准变化影响收盘涨幅), 详见 README。
"""
from .base import BaseStrategy

GAP_MIN = 0.07        # 收盘涨幅阈值: 收盘价/昨收 - 1 >= 7%
GAP_MAX = 1.0         # 收盘涨幅上限: 默认无限制 (设为 0.099 排除涨停收盘股 — 涨停买不进)
HOLD_DAYS = 1         # 隔日轮动: 持有1个交易日后收盘卖出 (报告特征: 无止损)


class GapOpenStrategy(BaseStrategy):
    name = 'gap_open'
    label = '跳空高开隔日轮动 (收盘涨幅>=7%买入, 次日收盘卖出, 涨停收盘复刻版)'
    description = '全市场扫描收盘涨幅>=7%的股票(主体为涨停收盘), 收盘价买入, 持有1个交易日收盘卖出'
    gap_min = GAP_MIN  # 引擎信号预筛阈值 (向量化预筛与 buy_signal 共用, 避免口径漂移)
    gap_max = GAP_MAX  # 收盘涨幅上限 (引擎预筛 + buy_signal 共用; 可经 config gap_max 覆盖)

    # 引擎/主流程在该策略下使用的推荐参数 (main.py 应用, 不写入 config.json)
    recommended = {
        'max_positions': 5,         # 5只等分
        'full_position': False,     # 每笔=当前现金×20% (递减, 与报告一致)
        'position_pct': 0.2,
        'min_buy_score': 5,         # 布尔条件给固定8分, 门槛不约束
    }

    def buy_signal(self, ind, **ctx):
        """收盘涨幅 >= 7% 且 < 涨停上限 (当日收盘决策, 收盘价成交; 与报告口径一致)
        gap_max 默认无限制; 配置后排除涨停收盘股 (涨停封死买不进, 模拟真实可成交性)"""
        close = ind.get('close', 0)
        prev_close = ind.get('prev_close', 0)
        if close <= 0 or prev_close <= 0:
            return False, 0, '无收盘/昨收数据'
        rise = close / prev_close - 1
        if rise < GAP_MIN:
            return False, 0, f'涨幅不足({rise:.2%})'
        if rise >= getattr(self, 'gap_max', 1.0):
            return False, 0, f'涨停收盘买不进(涨幅{rise:.1%})'
        return True, 8, f'跳空高开{rise:.1%}'

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """持有满 HOLD_DAYS 个交易日收盘卖出 (复刻版: 无止损, 全部到期卖出)"""
        if holding_days >= HOLD_DAYS:
            return True, f'持有{HOLD_DAYS}天到期'
        return False, ''


# ============================================================
# 米筐模板开盘口径复刻版 (GapOpenOpen)
# ============================================================

GAP_OPEN_MIN = 0.03    # 开盘跳空阈值: 开盘价/昨收 - 1 >= 3% (集合竞价定盘价)
VOL_MIN = 1.5          # 放量确认: 当日成交量/5日均量 >= 1.5 (主力净流入的代理, 无历史资金流数据)


class GapOpenOpenStrategy(BaseStrategy):
    """跳空高开隔日轮动 — 米筐模板开盘口径复刻版

    来源: 米筐模板 (get_iwencai "跳空高开大于3%；主力净流入；低估值；市值小于200亿")
    本地替代: 全市场扫描, 开盘跳空>=3% (开盘价/昨收-1, 集合竞价定盘价) + 放量确认
    (vol_ratio>=1.5 代理主力净流入); 低估值/市值条件跳过 (无历史数据, 避免前视偏差)

    执行方式 (与模板一致):
      选股: 开盘集合竞价 (09:25定盘价) — 开盘跳空>=3%
      买入: 当日开盘价成交 (模板 09:30 市价单 ≈ 开盘价)
      卖出: 持有满1个交易日尾盘(收盘价)卖出 (模板 before_close 14:57)
      仓位: 5只等分 (每笔=当前现金×20%)
    """
    name = 'gap_open_open'
    label = '跳空高开隔日轮动 (开盘跳空>=3%+放量买入, 次日尾盘卖出, 米筐模板开盘口径复刻版)'
    description = '全市场扫描开盘跳空>=3%且放量的股票(主力净流入代理), 开盘价买入, 持有1个交易日尾盘卖出'
    gap_min = GAP_OPEN_MIN  # 引擎信号预筛阈值 (向量化预筛与 buy_signal 共用)
    vol_min = VOL_MIN       # 放量阈值 (引擎预筛 + buy_signal 共用)

    # 引擎/主流程在该策略下使用的推荐参数
    recommended = {
        'max_positions': 5,         # 5只等分 (模板 context.max_size = 5)
        'full_position': False,
        'position_pct': 0.2,
        'min_buy_score': 5,
        'buy_at_open': True,        # 当日开盘价成交 (模板 09:30 开盘买入)
    }

    def buy_signal(self, ind, **ctx):
        """开盘跳空 >= 3% 且放量 (开盘集合竞价定盘价决策, 开盘价成交; 与米筐模板一致)
        主力净流入无历史数据, 用 vol_ratio>=1.5 放量代理"""
        open_px = ind.get('open', 0)
        prev_close = ind.get('prev_close', 0)
        if open_px <= 0 or prev_close <= 0:
            return False, 0, '无开盘/昨收数据'
        gap = open_px / prev_close - 1
        if gap < getattr(self, 'gap_min', GAP_OPEN_MIN):
            return False, 0, f'开盘跳空不足({gap:.2%})'
        vol_ratio = ind.get('vol_ratio', 1.0)
        if vol_ratio < getattr(self, 'vol_min', VOL_MIN):
            return False, 0, f'未放量({vol_ratio:.1f}x, 主力净流入代理)'
        return True, 8, f'开盘跳空{gap:.1%}+放量{vol_ratio:.1f}x'

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """持有满1个交易日尾盘(收盘价)卖出 (模板 before_close 定时器)"""
        if holding_days >= HOLD_DAYS:
            return True, f'持有{HOLD_DAYS}天到期'
        return False, ''

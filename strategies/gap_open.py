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
HOLD_DAYS = 1         # 隔日轮动: 持有1个交易日后收盘卖出 (报告特征: 无止损)


class GapOpenStrategy(BaseStrategy):
    name = 'gap_open'
    label = '跳空高开隔日轮动 (收盘涨幅>=7%买入, 次日收盘卖出, 涨停收盘复刻版)'
    description = '全市场扫描收盘涨幅>=7%的股票(主体为涨停收盘), 收盘价买入, 持有1个交易日收盘卖出'
    gap_min = GAP_MIN  # 引擎信号预筛阈值 (向量化预筛与 buy_signal 共用, 避免口径漂移)

    # 引擎/主流程在该策略下使用的推荐参数 (main.py 应用, 不写入 config.json)
    recommended = {
        'max_positions': 5,         # 5只等分
        'full_position': False,     # 每笔=当前现金×20% (递减, 与报告一致)
        'position_pct': 0.2,
        'min_buy_score': 5,         # 布尔条件给固定8分, 门槛不约束
    }

    def buy_signal(self, ind, **ctx):
        """收盘涨幅 >= 7% (当日收盘决策, 收盘价成交; 与报告口径一致)"""
        close = ind.get('close', 0)
        prev_close = ind.get('prev_close', 0)
        if close <= 0 or prev_close <= 0:
            return False, 0, '无收盘/昨收数据'
        rise = close / prev_close - 1
        if rise < GAP_MIN:
            return False, 0, f'涨幅不足({rise:.2%})'
        return True, 8, f'跳空高开{rise:.1%}'

    def sell_signal(self, ind, entry_price, holding_days, max_profit_seen=0):
        """持有满 HOLD_DAYS 个交易日收盘卖出 (复刻版: 无止损, 全部到期卖出)"""
        if holding_days >= HOLD_DAYS:
            return True, f'持有{HOLD_DAYS}天到期'
        return False, ''

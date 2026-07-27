"""
波段交易引擎
============
核心交易体系:
  - 选股: 基本面优质 + 技术面买入信号
  - 买入: MACD金叉 + 趋势向上 + 量价配合
  - 卖出: 止损(-8%) / 止盈(+20%) / MACD死叉 / 跌破均线 / SKDJ超买
  - 仓位: 最多5只, 每只20%资金

回测输出: 操作记录 + 收益曲线 + 统计指标
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

import plotly.graph_objects as go


# ============================================================
# 技术指标计算
# ============================================================

def _ema(data, period):
    if len(data) < period:
        return np.full_like(data, np.nan, dtype=float)
    alpha = 2.0 / (period + 1)
    result = np.full_like(data, np.nan, dtype=float)
    result[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def compute_indicators(closes, volumes=None, highs=None, lows=None):
    """计算全部技术指标, 返回字典"""
    n = len(closes)
    ind = {}

    # MA均线
    for p in [5, 10, 20, 60, 120]:
        ind[f'ma{p}'] = np.mean(closes[-p:]) if n >= p else np.nan

    # MACD
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12 - ema26
    valid_dif = dif[~np.isnan(dif)]
    dea = _ema(valid_dif, 9) if len(valid_dif) >= 9 else np.array([np.nan])
    ind['dif'] = dif[-1] if len(dif) > 0 else 0
    ind['dea'] = dea[-1] if len(dea) > 0 and not np.isnan(dea[-1]) else 0
    ind['macd_hist'] = (ind['dif'] - ind['dea']) * 2

    # MACD金叉/死叉 (最近3天)
    ind['macd_cross'] = 0
    if len(valid_dif) >= 4 and len(dea) >= 4:
        for lag in range(1, 4):
            if lag < len(valid_dif) and lag < len(dea):
                pd_, pe_ = valid_dif[-(lag+1)], dea[-(lag+1)]
                cd_, ce_ = valid_dif[-lag], dea[-lag]
                if not any(np.isnan(x) for x in [pd_, pe_, cd_, ce_]):
                    if pd_ <= pe_ and cd_ > ce_:
                        ind['macd_cross'] = 1  # 金叉
                        break
                    elif pd_ >= pe_ and cd_ < ce_:
                        ind['macd_cross'] = -1  # 死叉
                        break

    # SKDJ
    if highs is not None and lows is not None and n >= 18:
        k_prev, d_prev = 50.0, 50.0
        k_vals, d_vals = [], []
        for i in range(8, n):
            wh = np.max(highs[i-8:i+1])
            wl = np.min(lows[i-8:i+1])
            rsv = (closes[i] - wl) / (wh - wl) * 100 if wh != wl else 50
            k = 2/3 * k_prev + 1/3 * rsv
            d = 2/3 * d_prev + 1/3 * k
            k_vals.append(k)
            d_vals.append(d)
            k_prev, d_prev = k, d
        ind['skdj_k'] = k_vals[-1] if k_vals else 50
        ind['skdj_d'] = d_vals[-1] if d_vals else 50
        ind['skdj_j'] = 3 * ind['skdj_k'] - 2 * ind['skdj_d']
        # SKDJ交叉
        ind['skdj_cross'] = 0
        if len(k_vals) >= 4:
            for lag in range(1, 4):
                if k_vals[-(lag+1)] <= d_vals[-(lag+1)] and k_vals[-lag] > d_vals[-lag]:
                    ind['skdj_cross'] = 1; break
                elif k_vals[-(lag+1)] >= d_vals[-(lag+1)] and k_vals[-lag] < d_vals[-lag]:
                    ind['skdj_cross'] = -1; break
    else:
        ind['skdj_k'] = ind['skdj_d'] = ind['skdj_j'] = 50
        ind['skdj_cross'] = 0

    # 成交量
    if volumes is not None and n >= 20:
        ind['vol_ratio'] = np.mean(volumes[-5:]) / np.mean(volumes[-20:]) if np.mean(volumes[-20:]) > 0 else 1
        ind['vol_ma5'] = np.mean(volumes[-5:])
        ind['vol_ma20'] = np.mean(volumes[-20:])
    else:
        ind['vol_ratio'] = 1.0
        ind['vol_ma5'] = ind['vol_ma20'] = 0

    # 收益率
    ind['ret_5d'] = (closes[-1] / closes[-6] - 1) if n >= 6 else 0
    ind['ret_20d'] = (closes[-1] / closes[-21] - 1) if n >= 21 else 0

    # 波动率
    if n >= 20:
        log_ret = np.diff(np.log(closes[-20:]))
        ind['volatility'] = np.std(log_ret) * np.sqrt(252) if len(log_ret) > 0 else 0
    else:
        ind['volatility'] = 0

    ind['close'] = closes[-1]
    ind['price'] = closes[-1]

    return ind


# ============================================================
# 交易信号
# ============================================================

def check_buy_signal(ind):
    """
    买入信号判断 — 返回 (is_buy, score, reason)

    核心逻辑 (优化版):
    1. 趋势必须向上: 价格 > MA20 > MA60
    2. MACD: 必须金叉或柱状线为正且扩大
    3. SKDJ: 不在超买区(K<75), 低位金叉加分
    4. 量价配合: 放量上涨加分, 缩量扣分
    5. 近期动量正向: 20日收益 > 0
    """
    score = 0
    reasons = []

    # 条件1: 趋势 — 必须站上MA20且MA20>MA60
    ma20 = ind.get('ma20', 0)
    ma60 = ind.get('ma60', 0)
    price = ind.get('close', 0)

    if price <= 0 or np.isnan(ma20) or np.isnan(ma60):
        return False, 0, ''

    if price > ma20 > ma60:
        score += 2
        reasons.append('均线多头')
    else:
        return False, 0, ''  # 必须均线多头排列

    # 条件2: MACD — 金叉强信号
    if ind['macd_cross'] == 1:
        score += 3
        reasons.append('MACD金叉')
    elif ind['macd_hist'] > 0:
        score += 1
        reasons.append('MACD多头')
    else:
        return False, 0, ''  # MACD空头不买

    # 条件3: SKDJ — 低位金叉加分, 超买扣分
    k = ind['skdj_k']
    if ind['skdj_cross'] == 1 and k < 50:
        score += 2
        reasons.append('SKDJ低位金叉')
    elif ind['skdj_cross'] == 1 and k < 70:
        score += 1
        reasons.append('SKDJ金叉')
    elif k > 80:
        score -= 2
        reasons.append('SKDJ超买')
    elif ind['skdj_cross'] == -1 and k > 60:
        score -= 1

    # 条件4: 成交量 — 放量确认
    if ind['vol_ratio'] > 1.3:
        score += 1
        reasons.append('放量')
    elif ind['vol_ratio'] < 0.5:
        score -= 1
        reasons.append('缩量')

    # 条件5: 动量 — 要求正向
    if ind['ret_20d'] > 0.05:
        score += 1
        reasons.append('20日涨5%+')
    elif ind['ret_20d'] > 0:
        score += 0.5
    elif ind['ret_20d'] < -0.05:
        score -= 2
        reasons.append('近期下跌')

    # 波动率惩罚
    if ind['volatility'] > 0.6:
        score -= 1

    # 提高门槛: 需要 >= 6分
    is_buy = score >= 6
    return is_buy, score, '+'.join(reasons)


def check_sell_signal(ind, entry_price, holding_days, max_profit_seen=0):
    """
    卖出信号判断 — 返回 (is_sell, reason)

    卖出条件 (任一触发):
    1. 硬止损: 亏损 >= 7%
    2. 止盈: 盈利 >= 25%
    3. 移动止盈: 盈利曾超过10%后回落至盈利5%以下
    4. 时间止损: 持有超过15天且收益为负
    5. MACD零轴下方死叉 (强卖出)
    6. 放量下跌 (量比>2且5日跌幅>5%)
    """
    price = ind.get('close', 0)
    pnl = (price / entry_price - 1) if entry_price > 0 else 0

    # 1. 硬止损
    if pnl <= -0.10:
        return True, f'止损({pnl:.1%})'

    # 2. 止盈
    if pnl >= 0.25:
        return True, f'止盈({pnl:.1%})'

    # 3. 移动止盈: 曾盈利>10%但回落到5%以下
    if max_profit_seen > 0.10 and pnl < 0.05:
        return True, f'移动止盈(曾涨{max_profit_seen:.1%},现{pnl:.1%})'

    # 4. 时间止损: 持有超20天且亏损
    if holding_days > 20 and pnl < -0.03:
        return True, f'时间止损({holding_days}天{pnl:.1%})'

    # 5. MACD零轴下方死叉 (趋势恶化)
    if ind['macd_cross'] == -1 and ind['dif'] < 0:
        return True, 'MACD零轴下死叉'

    # 6. 放量下跌
    if ind['vol_ratio'] > 2.0 and ind['ret_5d'] < -0.05:
        return True, f'放量下跌(量比{ind["vol_ratio"]:.1f}x)'

    return False, ''


# ============================================================
# 交易引擎
# ============================================================

class Position:
    """持仓"""
    def __init__(self, code, name, entry_price, entry_date, shares, capital):
        self.code = code
        self.name = name
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.shares = shares
        self.capital = capital  # 投入资金

    def pnl(self, current_price):
        return (current_price / self.entry_price - 1) if self.entry_price > 0 else 0

    def market_value(self, current_price):
        return self.shares * current_price


class TradeRecord:
    """交易记录"""
    def __init__(self, code, name, direction, price, date, shares, amount, reason, pnl_pct=0):
        self.code = code
        self.name = name
        self.direction = direction  # 'BUY' or 'SELL'
        self.price = price
        self.date = date
        self.shares = shares
        self.amount = amount
        self.reason = reason
        self.pnl_pct = pnl_pct  # 卖出时的盈亏比例


def run_swing_backtest(history_dict, scored_df, config, start_date_str='2026-01-01',
                       end_date_str='2026-07-27'):
    """
    波段交易回测引擎

    参数:
        history_dict: {sina_code: K线DataFrame}
        scored_df: 当前打分排名 (用于股票池筛选)
        config: 交易配置字典
        start_date_str: 回测开始日期
        end_date_str: 回测结束日期

    返回: {
        'trades': [TradeRecord, ...],
        'equity_curve': [(date, value), ...],
        'stats': dict,
        'final_positions': [Position, ...],
    }
    """
    # ---- 参数 ----
    initial_capital = config.get('initial_capital', 1000000)
    max_positions = config.get('max_positions', 4)
    position_pct = config.get('position_pct', 0.25)
    stop_loss = config.get('stop_loss', -0.08)
    take_profit = config.get('take_profit', 0.20)
    max_holding_days = config.get('max_holding_days', 40)
    min_buy_score = config.get('min_buy_score', 5)

    start_date = pd.Timestamp(start_date_str)
    end_date = pd.Timestamp(end_date_str)

    # ---- 准备数据 ----
    # 构建 pure_code → sina_code 映射
    pure_to_sina = {}
    for sina_code in history_dict:
        code = str(sina_code)
        for prefix in ('sh', 'sz', 'bj'):
            if code.startswith(prefix):
                code = code[len(prefix):]
                break
        pure_to_sina[code.zfill(6)] = sina_code

    # 获取候选股票列表 (从scored_df中取排名靠前的)
    candidate_codes = []
    if scored_df is not None and 'code' in scored_df.columns:
        for _, row in scored_df.head(200).iterrows():
            code = str(row['code']).zfill(6)
            if code in pure_to_sina:
                candidate_codes.append(code)

    if not candidate_codes:
        candidate_codes = list(pure_to_sina.keys())[:200]

    print(f"  候选股票: {len(candidate_codes)} 只")

    # 找共同交易日
    all_dates = set()
    for sina_code in [pure_to_sina[c] for c in candidate_codes if c in pure_to_sina]:
        hist = history_dict.get(sina_code)
        if hist is not None and 'date' in hist.columns:
            for d in hist['date'].values:
                all_dates.add(pd.Timestamp(d))

    trading_dates = sorted([d for d in all_dates if start_date <= d <= end_date])
    if not trading_dates:
        print(f"  ✗ 在 {start_date_str} ~ {end_date_str} 范围内无交易日")
        return None

    print(f"  交易日: {trading_dates[0].strftime('%Y-%m-%d')} ~ "
          f"{trading_dates[-1].strftime('%Y-%m-%d')} ({len(trading_dates)} 天)")

    # ---- 回测主循环 ----
    cash = initial_capital
    positions = []  # [Position, ...]
    trades = []     # [TradeRecord, ...]
    equity_curve = []
    max_profit_tracker = {}  # {code: max_profit_seen}
    cooldown = {}  # {code: sell_date} — 卖出后冷却5天不买回

    for day_idx, today in enumerate(trading_dates):
        # ---- 1. 检查持仓, 判断是否卖出 ----
        to_sell = []
        for pos in positions:
            sina = pure_to_sina.get(pos.code)
            if not sina:
                continue
            hist = history_dict.get(sina)
            if hist is None:
                continue

            mask = hist['date'] <= today
            if mask.sum() == 0:
                continue
            idx = mask.sum() - 1
            closes = hist['close'].values.astype(float)[:idx+1]
            volumes_arr = hist['volume'].values.astype(float)[:idx+1] if 'volume' in hist.columns else None
            highs_arr = hist['high'].values.astype(float)[:idx+1] if 'high' in hist.columns else None
            lows_arr = hist['low'].values.astype(float)[:idx+1] if 'low' in hist.columns else None

            if len(closes) < 20:
                continue

            ind = compute_indicators(closes, volumes_arr, highs_arr, lows_arr)
            holding_days = sum(1 for d in trading_dates if pos.entry_date < d <= today)

            # 更新最大浮盈
            current_pnl = (ind['close'] / pos.entry_price - 1) if pos.entry_price > 0 else 0
            max_profit_tracker[pos.code] = max(max_profit_tracker.get(pos.code, 0), current_pnl)

            # 最大持有天数强制卖出
            if holding_days >= max_holding_days:
                to_sell.append((pos, ind['close'], f'持有{holding_days}天到期'))
                continue

            is_sell, reason = check_sell_signal(ind, pos.entry_price, holding_days,
                                                 max_profit_tracker.get(pos.code, 0))
            if is_sell:
                to_sell.append((pos, ind['close'], reason))

        # 执行卖出
        for pos, sell_price, reason in to_sell:
            pnl_pct = pos.pnl(sell_price)
            amount = pos.shares * sell_price
            cash += amount
            trades.append(TradeRecord(
                pos.code, pos.name, 'SELL', sell_price, today,
                pos.shares, amount, reason, pnl_pct
            ))
            positions.remove(pos)
            cooldown[pos.code] = today  # 卖出后冷却
            max_profit_tracker.pop(pos.code, None)

        # ---- 2. 扫描买入信号 ----
        available_slots = max_positions - len(positions)
        if available_slots > 0 and cash > initial_capital * 0.05:
            buy_candidates = []
            held_codes = {p.code for p in positions}

            for code in candidate_codes:
                if code in held_codes:
                    continue
                # 冷却期: 卖出后5个交易日内不买回
                if code in cooldown:
                    sell_date = cooldown[code]
                    days_since_sell = sum(1 for d in trading_dates if sell_date < d <= today)
                    if days_since_sell < 10:
                        continue
                sina = pure_to_sina.get(code)
                if not sina:
                    continue
                hist = history_dict.get(sina)
                if hist is None:
                    continue

                mask = hist['date'] <= today
                if mask.sum() < 60:
                    continue
                idx = mask.sum() - 1
                closes = hist['close'].values.astype(float)[:idx+1]
                volumes_arr = hist['volume'].values.astype(float)[:idx+1] if 'volume' in hist.columns else None
                highs_arr = hist['high'].values.astype(float)[:idx+1] if 'high' in hist.columns else None
                lows_arr = hist['low'].values.astype(float)[:idx+1] if 'low' in hist.columns else None

                if len(closes) < 60:
                    continue

                ind = compute_indicators(closes, volumes_arr, highs_arr, lows_arr)
                is_buy, score, reason = check_buy_signal(ind)

                if is_buy and score >= min_buy_score:
                    # 获取名称
                    name = ''
                    if scored_df is not None and 'code' in scored_df.columns:
                        match = scored_df[scored_df['code'].astype(str).str.zfill(6) == code]
                        if not match.empty:
                            name = str(match.iloc[0].get('name', ''))
                    buy_candidates.append((code, name, ind['close'], score, reason))

            # 按信号强度排序, 买入前 available_slots 个
            buy_candidates.sort(key=lambda x: x[3], reverse=True)
            for code, name, buy_price, score, reason in buy_candidates[:available_slots]:
                alloc = cash * position_pct
                if alloc < buy_price * 100:  # 至少买1手
                    continue
                shares = int(alloc / buy_price / 100) * 100  # 整手
                if shares <= 0:
                    continue
                amount = shares * buy_price
                cash -= amount
                positions.append(Position(code, name, buy_price, today, shares, amount))
                trades.append(TradeRecord(
                    code, name, 'BUY', buy_price, today, shares, amount, reason
                ))

        # ---- 3. 记录当日净值 ----
        portfolio_value = cash
        for pos in positions:
            sina = pure_to_sina.get(pos.code)
            if sina:
                hist = history_dict.get(sina)
                if hist is not None:
                    mask = hist['date'] <= today
                    if mask.sum() > 0:
                        idx = mask.sum() - 1
                        portfolio_value += pos.shares * hist['close'].values[idx]
                    else:
                        portfolio_value += pos.capital
            else:
                portfolio_value += pos.capital

        equity_curve.append((today, portfolio_value))

    # ---- 统计 ----
    final_value = equity_curve[-1][1] if equity_curve else initial_capital
    total_return = (final_value / initial_capital - 1)
    trading_days = len(trading_dates)
    annual_return = (1 + total_return) ** (252 / max(trading_days, 1)) - 1

    sell_trades = [t for t in trades if t.direction == 'SELL']
    win_trades = [t for t in sell_trades if t.pnl_pct > 0]
    win_rate = len(win_trades) / len(sell_trades) if sell_trades else 0

    avg_win = np.mean([t.pnl_pct for t in win_trades]) if win_trades else 0
    avg_loss = np.mean([t.pnl_pct for t in sell_trades if t.pnl_pct <= 0]) if sell_trades else 0

    # Sharpe
    if len(equity_curve) > 1:
        daily_rets = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i-1][1]
            curr = equity_curve[i][1]
            if prev > 0:
                daily_rets.append(curr / prev - 1)
        if daily_rets and np.std(daily_rets) > 0:
            sharpe = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)
        else:
            sharpe = 0
        # 最大回撤
        values = [e[1] for e in equity_curve]
        peaks = np.maximum.accumulate(values)
        drawdowns = (peaks - values) / peaks
        max_drawdown = np.max(drawdowns)
    else:
        sharpe = 0
        max_drawdown = 0

    stats = {
        'initial_capital': initial_capital,
        'final_value': final_value,
        'total_return': total_return,
        'annual_return': annual_return,
        'sharpe': sharpe,
        'max_drawdown': max_drawdown,
        'total_trades': len(sell_trades),
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_loss_ratio': abs(avg_win / avg_loss) if avg_loss != 0 else 0,
        'trading_days': trading_days,
        'max_positions': max_positions,
    }

    return {
        'trades': trades,
        'equity_curve': equity_curve,
        'stats': stats,
        'final_positions': positions,
        'initial_capital': initial_capital,
    }


# ============================================================
# 输出
# ============================================================

def print_trade_summary(result):
    """打印交易摘要"""
    if result is None:
        return

    stats = result['stats']
    trades = result['trades']

    print("\n" + "═" * 70)
    print("  📊 波段交易回测结果")
    print("═" * 70)
    print(f"  初始资金:   ¥{stats['initial_capital']:>12,.0f}")
    print(f"  最终资金:   ¥{stats['final_value']:>12,.0f}")
    print(f"  总收益率:   {stats['total_return']:>+12.2%}")
    print(f"  年化收益率: {stats['annual_return']:>+12.2%}")
    print(f"  Sharpe:     {stats['sharpe']:>12.2f}")
    print(f"  最大回撤:   {stats['max_drawdown']:>12.1%}")
    print(f"  交易次数:   {stats['total_trades']:>12d} 笔")
    print(f"  胜率:       {stats['win_rate']:>12.1%}")
    print(f"  平均盈利:   {stats['avg_win']:>+12.2%}")
    print(f"  平均亏损:   {stats['avg_loss']:>+12.2%}")
    print(f"  盈亏比:     {stats['profit_loss_ratio']:>12.2f}")
    print(f"  交易天数:   {stats['trading_days']:>12d} 天")
    print("═" * 70)

    # 操作记录
    print(f"\n  📋 操作记录 (共 {len(trades)} 笔):\n")
    print(f"  {'日期':>12} {'方向':>4} {'代码':<8} {'名称':<8} "
          f"{'价格':>8} {'数量':>6} {'金额':>10} {'盈亏':>7}  原因")
    print("  " + "─" * 95)

    for t in trades:
        dir_label = '🟢买入' if t.direction == 'BUY' else '🔴卖出'
        pnl_str = f'{t.pnl_pct:+.1%}' if t.direction == 'SELL' else ''
        print(f"  {t.date.strftime('%Y-%m-%d'):>12} {dir_label:>4} {t.code:<8} {t.name:<8} "
              f"{t.price:>8.2f} {t.shares:>6d} {t.amount:>10,.0f} {pnl_str:>7}  {t.reason}")

    # 当前持仓
    if result['final_positions']:
        print(f"\n  📦 当前持仓 ({len(result['final_positions'])} 只):")
        for pos in result['final_positions']:
            print(f"    {pos.code} {pos.name} | 买入价 {pos.entry_price:.2f} | "
                  f"{pos.shares}股 | 投入 ¥{pos.capital:,.0f}")

    print("\n  ⚠️  回测基于历史数据，不代表未来表现。投资有风险，入市需谨慎。")
    print()


def generate_equity_chart(result):
    """生成收益曲线图 (Plotly HTML)"""
    if result is None or not result['equity_curve']:
        return ''

    dates = [e[0] for e in result['equity_curve']]
    values = [e[1] for e in result['equity_curve']]
    initial = result['initial_capital']

    # 净值
    nav = [v / initial for v in values]
    # 基准 (假设买入持有沪深300等权)
    benchmark_nav = [1.0] * len(nav)  # 简化: 基准为1

    fig = go.Figure()

    # 策略净值
    fig.add_trace(go.Scatter(
        x=dates, y=nav, mode='lines',
        name='策略净值',
        line=dict(color='#3498db', width=2),
        fill='tozeroy', fillcolor='rgba(52,152,219,0.1)',
    ))

    # 买卖标记
    for t in result['trades']:
        if t.date in dates:
            idx = dates.index(t.date)
            if t.direction == 'BUY':
                fig.add_trace(go.Scatter(
                    x=[t.date], y=[nav[idx]],
                    mode='markers', marker=dict(symbol='triangle-up', size=10, color='#27ae60'),
                    name='买入' if t == result['trades'][0] else '',
                    showlegend=(t == next((x for x in result['trades'] if x.direction == 'BUY'), None)),
                    hovertext=f'{t.code} {t.name}<br>{t.reason}<br>¥{t.price:.2f}',
                ))
            else:
                fig.add_trace(go.Scatter(
                    x=[t.date], y=[nav[idx]],
                    mode='markers', marker=dict(symbol='triangle-down', size=10, color='#e74c3c'),
                    name='卖出' if t == result['trades'][0] else '',
                    showlegend=(t == next((x for x in result['trades'] if x.direction == 'SELL'), None)),
                    hovertext=f'{t.code} {t.name}<br>{t.reason}<br>{t.pnl_pct:+.1%}',
                ))

    fig.update_layout(
        title='策略净值曲线',
        xaxis=dict(title='日期', gridcolor='#f0f0f0'),
        yaxis=dict(title='净值', gridcolor='#f0f0f0'),
        height=400, margin=dict(t=40, b=30, l=50, r=20),
        legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
        font=dict(size=12),
        hovermode='closest',
    )

    return fig.to_html(full_html=False, include_plotlyjs=False)

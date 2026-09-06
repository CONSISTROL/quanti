"""
次日买卖触发价预测 — 每日收盘后运行, 回答"明天收盘到什么价格该卖/该买"

原理: 模拟"明日收盘价 = 网格上的每个价格 P"时的完整技术指标, 逐点调用策略的
      卖出/买入信号, 找出各触发条件 (止损/趋势转弱/SKDJ高位死叉/强弱分达标) 的
      临界价, 给出明日的持有区间与切标买入触发价.

用法: 由 tests/watchlist_backtest.py 自动调用, 也可独立使用:
    from quantlab.sell_price_forecast import next_sell_prices, next_buy_prices
    from quantlab.strategies import get_strategy
    info = next_sell_prices(df, entry_price=1.42, strategy=get_strategy('watchlist'))
    buy  = next_buy_prices(df, strategy, bonus=2, min_buy_score=4)
"""
import numpy as np


def _ind_with_next_close(closes, vols, highs, lows, base_vol, p):
    """构造"明日收盘价=p"的假想bar追加到序列末尾, 重算完整技术指标"""
    from quantlab.trading_engine import compute_indicators
    last = closes[-1]
    c2 = np.append(closes, p)
    h2 = np.append(highs, max(last, p))   # 假想明日bar: 平开, 高低按P
    l2 = np.append(lows, min(last, p))
    v2 = np.append(vols, base_vol) if vols is not None else None
    return compute_indicators(c2, v2, h2, l2)


def _tail_arrays(df):
    """取最后400根bar的收盘/高低/量数组 (400根足够指标收敛, MA/MACD/SKDJ完整一致)"""
    tail = df.tail(400).reset_index(drop=True)
    closes = tail['close'].values.astype(float)
    highs = tail['high'].values.astype(float)
    lows = tail['low'].values.astype(float)
    vols = tail['volume'].values.astype(float) if 'volume' in tail.columns else None
    base_vol = vols[-1] if vols is not None else 1e6
    return closes, vols, highs, lows, base_vol


def next_sell_prices(df, entry_price, strategy):
    """对单只标的计算次日卖出触发价

    参数:
        df: 日线DataFrame (含 date/open/high/low/close/volume), 至少200根
        entry_price: 持仓成本价
        strategy: 策略实例 (用其 sell_signal 判断)
    返回:
        dict: {现价, 止损价, 趋势价, 死叉价, 安全低, 安全高}
              趋势价 = 跌破MA20触发临界 (明日收盘≤此价卖出, None=今日未临近)
              死叉价 = SKDJ高位死叉触发临界 (明日收盘≥此价卖出, None=今日指标未支持)
    """
    from quantlab.trading_engine import compute_indicators

    closes, vols, highs, lows, base_vol = _tail_arrays(df)
    last = closes[-1]

    # 网格: 现价 ±20%, 241点 (~0.17%精度, 覆盖止损-5%/趋势线/SKDJ死叉区间)
    prices = np.linspace(last * 0.80, last * 1.20, 241)
    triggers = []  # [(P, reason)] 触发卖出的模拟价格
    for p in prices:
        ind = _ind_with_next_close(closes, vols, highs, lows, base_vol, p)
        is_sell, reason = strategy.sell_signal(ind, entry_price, 5, 0)
        if is_sell and reason:
            triggers.append((float(p), reason))

    stop_px = entry_price * 0.95          # 止损线: 固定解析 (成本-5%)
    trend_px = None                       # 趋势转弱: 低价端临界 (取触发区间的最高价)
    death_px = None                       # SKDJ死叉: 高价端临界 (取触发区间的最低价)
    for p, reason in triggers:
        if '止损' in reason:
            continue                      # 止损用解析价, 网格只是验证
        if 'MA20' in reason or '趋势' in reason:
            trend_px = p if trend_px is None else max(trend_px, p)
        if '死叉' in reason:
            death_px = p if death_px is None else min(death_px, p)

    low_bound = max(stop_px, trend_px) if trend_px is not None else stop_px
    return {
        '现价': last,
        '止损价': stop_px,
        '趋势价': trend_px,
        '死叉价': death_px,
        '安全低': low_bound,
        '安全高': death_px,
        '触发数': len(triggers),
    }


def next_buy_prices(df, strategy, bonus=0, min_buy_score=4):
    """对单只标的计算次日买入触发价 (切标候选用)

    复刻引擎买入判定: 引擎只按"加分后的分数 ≥ 门槛"买入
    (buy_signal 返回的分数在过滤失败时为0, 但优先级/龙头加分后仍可过门槛),
    所以触发条件 = buy_signal返回分 + bonus ≥ min_buy_score, 与引擎逐字一致.
    买入触发可能在上方 (追涨: 收盘≥P触发) 也可能在下方 (超跌: 收盘≤P触发),
    函数通过网格扫描找出距现价最近的触发临界.

    参数:
        df: 日线DataFrame
        strategy: 策略实例 (用其 buy_signal 判断)
        bonus: 引擎加分的总和 (龙头TOP10 +2 + watchlist_priority 加分)
        min_buy_score: 引擎买入门槛 (config trading.min_buy_score)
    返回:
        dict: {现价, 已触发, 得分, 原因, 方向, 触发价, 触发原因}
              已触发=True → 今日收盘已满足买入条件, 明日开盘即可买
              方向: 'above' 收盘≥触发价 / 'below' 收盘≤触发价 / 'both' 两向均可 / 'none' 明日不触发
    """
    from quantlab.trading_engine import compute_indicators

    closes, vols, highs, lows, base_vol = _tail_arrays(df)
    last = closes[-1]

    # 今日收盘状态 (复刻引擎: 加分后的分数过门槛即买入)
    ind = compute_indicators(closes, vols, highs, lows)
    is_buy, score, reason = strategy.buy_signal(ind)
    score = float(score) + bonus
    if score >= min_buy_score:
        return {'现价': last, '已触发': True, '得分': score, '原因': reason,
                '方向': 'now', '触发价': None, '触发原因': reason}

    # 网格扫描: 明日收盘价 → 是否触发买入
    above, below = [], []   # 触发价区间 (上方=追涨, 下方=超跌)
    trig_reason = {}
    for p in np.linspace(last * 0.80, last * 1.20, 241):
        ind2 = _ind_with_next_close(closes, vols, highs, lows, base_vol, p)
        is_buy2, score2, reason2 = strategy.buy_signal(ind2)
        if float(score2) + bonus >= min_buy_score:
            if p >= last * 0.999:
                above.append(float(p))
            else:
                below.append(float(p))
            trig_reason[float(p)] = reason2

    if above and below:
        # 两向都触发 → 取距现价近的一侧为主, 同时给出另一侧
        near_a = min(above)
        near_b = max(below)
        if (near_a - last) <= (last - near_b):
            return {'现价': last, '已触发': False, '得分': score, '原因': reason,
                    '方向': 'both', '触发价': near_a, '触发原因': trig_reason.get(near_a, '')}
        return {'现价': last, '已触发': False, '得分': score, '原因': reason,
                '方向': 'both', '触发价': near_b, '触发原因': trig_reason.get(near_b, '')}
    if above:
        px = min(above)
        return {'现价': last, '已触发': False, '得分': score, '原因': reason,
                '方向': 'above', '触发价': px, '触发原因': trig_reason.get(px, '')}
    if below:
        px = max(below)
        return {'现价': last, '已触发': False, '得分': score, '原因': reason,
                '方向': 'below', '触发价': px, '触发原因': trig_reason.get(px, '')}
    return {'现价': last, '已触发': False, '得分': score, '原因': reason,
            '方向': 'none', '触发价': None, '触发原因': ''}

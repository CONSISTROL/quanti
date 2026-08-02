"""
次日卖出触发价预测 — 每日收盘后运行, 回答"明天收盘到什么价格该卖出"

原理: 模拟"明日收盘价 = 网格上的每个价格 P"时的完整技术指标, 逐点调用策略卖出信号,
      找出各卖出条件 (止损/趋势转弱/SKDJ高位死叉) 的触发临界价, 给出明日的持有区间.

用法: 由 tests/watchlist_backtest.py 自动调用, 也可独立使用:
    from sell_price_forecast import next_sell_prices
    from strategies import get_strategy
    info = next_sell_prices(df, entry_price=1.42, strategy=get_strategy('watchlist'))
"""
import numpy as np


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
    from trading_engine import compute_indicators

    tail = df.tail(400).reset_index(drop=True)
    closes = tail['close'].values.astype(float)
    highs = tail['high'].values.astype(float)
    lows = tail['low'].values.astype(float)
    vols = tail['volume'].values.astype(float) if 'volume' in tail.columns else None
    last = closes[-1]
    base_vol = vols[-1] if vols is not None else 1e6

    # 网格: 现价 ±20%, 241点 (~0.17%精度, 覆盖止损-5%/趋势线/SKDJ死叉区间)
    prices = np.linspace(last * 0.80, last * 1.20, 241)
    triggers = []  # [(P, reason)] 触发卖出的模拟价格
    for p in prices:
        c2 = np.append(closes, p)
        h2 = np.append(highs, max(last, p))   # 假想明日bar: 平开, 高低按P
        l2 = np.append(lows, min(last, p))
        v2 = np.append(vols, base_vol) if vols is not None else None
        ind = compute_indicators(c2, v2, h2, l2)
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

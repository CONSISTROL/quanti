"""
7信号验证 — 用指定策略在指定股票上验证用户期望的买卖信号日期 (±2交易日)

用法: python run_test.py --module verify_signals
     python tests/verify_signals.py 601857 reversal
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pickle
import pandas as pd
import numpy as np

USER_SIGNALS = [
    ('2025-03-12', 'B'), ('2025-04-03', 'S'), ('2025-04-14', 'B'),
    ('2025-06-23', 'S'), ('2025-10-13', 'B'), ('2025-11-14', 'S'),
    ('2026-07-06', 'B'),
]

TOL = 2  # 允许的交易日误差


def main(config=None, stock=None, strategy=None, cache_path='cache/hist_batch_20260801.pkl'):
    """config: 完整配置dict (含test节: stock/strategy)"""
    test_cfg = (config or {}).get('test', {})
    stock = stock or test_cfg.get('stock', '601857')
    strategy = strategy or test_cfg.get('strategy', 'reversal')

    from indicator_cache import _incremental_indicators
    from strategies import get_strategy, strategy_label

    hist = pickle.load(open(cache_path, 'rb'))
    df = None
    for k, v in hist.items():
        if k.endswith(str(stock).zfill(6)):
            df = v
            break
    if df is None:
        print(f'  ✗ 缓存中无 {stock}')
        return 1

    dates_arr = df['date'].values
    closes = df['close'].values.astype(float)
    volumes = df['volume'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    all_dates = set(pd.Timestamp(d).strftime('%Y-%m-%d') for d in dates_arr)
    res = _incremental_indicators(closes, volumes, highs, lows, dates_arr, all_dates)

    strat = get_strategy(strategy)
    B_days, S_days = set(), set()
    for d in sorted(res):
        ok, _, _ = strat.buy_signal(res[d])
        if ok:
            B_days.add(d)
        ok, _ = strat.sell_signal(res[d], 1.0, 0)
        if ok:
            S_days.add(d)

    idx = pd.to_datetime(sorted(res.keys()))
    n_hit = 0
    print(f'== {stock} 信号验证 ({strategy_label(strategy)}, ±{TOL}交易日) ==')
    for date, sig in USER_SIGNALS:
        pos = np.searchsorted(idx.values, np.datetime64(date))
        win = [pd.Timestamp(idx[i]).strftime('%Y-%m-%d')
               for i in range(max(0, pos - TOL), min(len(idx), pos + TOL + 1))]
        hits = [x for x in win if x in (B_days if sig == 'B' else S_days)]
        if hits:
            n_hit += 1
        print(f'  {date} {sig}: {"命中" if hits else "MISS"} {hits}')
    print(f'命中 {n_hit}/{len(USER_SIGNALS)}')
    return 0


if __name__ == '__main__':
    stock = sys.argv[1] if len(sys.argv) > 1 else None
    strategy = sys.argv[2] if len(sys.argv) > 2 else None
    sys.exit(main(None, stock, strategy))

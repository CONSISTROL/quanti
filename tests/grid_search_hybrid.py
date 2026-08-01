"""混合策略搜索: 参考策略日线逻辑 + 旧策略周线低位金叉(冻结视图)
买入周线条件变体 × 止损开关, 约束: 7/7信号命中, 目标: 最大收益
"""
import pickle
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np


def main(config=None):
    hist = pickle.load(open('cache/hist_batch_20260731.pkl', 'rb'))
    df = None
    for k, v in hist.items():
        if k.endswith('601857'):
            df = v.copy(); break
    dates_arr = df['date'].values
    closes = df['close'].values.astype(float)
    volumes = df['volume'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)

    from indicator_cache import _incremental_indicators
    all_dates = set(pd.Timestamp(d).strftime('%Y-%m-%d') for d in dates_arr)
    res = _incremental_indicators(closes, volumes, highs, lows, dates_arr, all_dates)

    d = pd.DataFrame(res).T
    d.index = pd.to_datetime(d.index)
    d = d.sort_index()
    d['open'] = pd.Series(df['open'].values.astype(float), index=pd.to_datetime(df['date'].values))
    d['K'] = d['skdj_k']; d['D'] = d['skdj_d']
    d['WK_dyn'] = d['skdj_weekly_k_dyn']; d['WD_dyn'] = d['skdj_weekly_d_dyn']
    d['WK_frz'] = d['skdj_weekly_k']; d['WD_frz'] = d['skdj_weekly_d']

    US = [('2025-03-12', 'B'), ('2025-04-03', 'S'), ('2025-04-14', 'B'),
          ('2025-06-23', 'S'), ('2025-10-13', 'B'), ('2025-11-14', 'S'), ('2026-07-06', 'B')]
    idx = d.index
    us_pos = {date: (sig, np.searchsorted(idx.values, np.datetime64(date))) for date, sig in US}
    bt_mask = (idx >= np.datetime64('2025-01-01'))
    open_bt = d['open'].values[bt_mask]
    close_bt = d['close'].values[bt_mask]
    n_bt = len(open_bt)

    # 参考卖出 (固定)
    S_ref = ((d['max_k_5d'] > 65) & d['k_dn_win'] & (d['K'] < d['D'])) & \
            (d['hist_fall_win'] & (d['macd_hist'] > 0)) & \
            ((d['close'] < d['ma5']) | d['no_new_high3']) & \
            (d['WK_dyn'] > 60)

    # 买入日线部分 (固定, 参考策略)
    daily_part = (d['K'] < 50) & d['k_up_win'] & \
                 (d['macd_gold_win'] | (d['hist_rise_win'] & (d['macd_hist'] < 0)))

    # 周线条件变体
    wk_ref = (d['WK_dyn'] < 60) | d['wk_gold_low_win']                      # 参考: 动态WK<60或低位金叉3天
    wk_old = (d['WK_frz'] < 40) & (d['WK_frz'] > d['WD_frz'])               # 旧: 冻结周线低位金叉
    week_variants = {
        '参考周线': wk_ref,
        '旧周线低位金叉': wk_old,
        '两者都需': wk_ref & wk_old,
        '任一满足': wk_ref | wk_old,
    }

    def calc_pnl(B, S, stop_loss=None):
        Bb, Sb = B.values[bt_mask], S.values[bt_mask]
        cash = 100000.0; shares = 0; pos = 0; entry = 0.0
        for i in range(1, n_bt):
            op = open_bt[i]
            if pos == 0 and Bb[i - 1]:
                lot = int(cash / (op * 100 * 1.00025))
                if lot > 0:
                    cash -= lot * 100 * op * 1.00025
                    shares = lot * 100; pos = 1; entry = op
            elif pos == 1:
                sell = bool(Sb[i - 1])
                if stop_loss and op <= entry * (1 + stop_loss):
                    sell = True
                if sell:
                    cash += shares * op * (1 - 0.00125)
                    shares = 0; pos = 0
        if pos == 1:
            cash += shares * close_bt[-1] * (1 - 0.00125)
        return cash / 100000 - 1

    def verify(B, S):
        hits = 0
        for date, (sig, pos) in us_pos.items():
            lo, hi = max(0, pos - 2), min(len(idx), pos + 3)
            if (B.values[lo:hi].any() if sig == 'B' else S.values[lo:hi].any()):
                hits += 1
        return hits

    print(f"{'周线条件':<12} {'止损':<8} {'命中':<4} {'收益':<10} {'交易'}")
    for name, wk in week_variants.items():
        B = daily_part & wk
        for stop in [None, -0.03, -0.05]:
            hits = verify(B, S_ref)
            ret = calc_pnl(B, S_ref, stop)
            # 统计交易数
            Bb = B.values[bt_mask]; Sb = S_ref.values[bt_mask]
            n_trades = 0; pos = 0
            for i in range(1, n_bt):
                if pos == 0 and Bb[i-1]: pos = 1
                elif pos == 1 and Sb[i-1]: pos = 0; n_trades += 1
            stop_label = '无' if stop is None else f'{stop:.0%}'
            print(f'{name:<12} {stop_label:<8} {hits}/7    {ret*100:+7.2f}%   {n_trades}笔')


if __name__ == '__main__':
    main()
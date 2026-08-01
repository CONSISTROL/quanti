"""参考策略参数网格搜索 (先>=6/7命中, 再最大化收益) — 支持动态/冻结周线两种视图"""
import pickle, pandas as pd, numpy as np
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from collections import deque
from itertools import product


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
    n = len(closes)

    from indicator_cache import _incremental_indicators
    all_dates = set(pd.Timestamp(d).strftime('%Y-%m-%d') for d in dates_arr)
    res = _incremental_indicators(closes, volumes, highs, lows, dates_arr, all_dates)
    daily = pd.DataFrame(res).T
    daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()
    daily['open'] = pd.Series(df['open'].values.astype(float), index=pd.to_datetime(df['date'].values))
    daily['K'] = daily['skdj_k']; daily['D'] = daily['skdj_d']
    daily['DIF'] = daily['dif']; daily['DEA'] = daily['dea']
    daily['MACD'] = daily['macd_hist']; daily['MA5'] = daily['ma5']

    # 动态周线 (当前部分周): 9根窗口 = 8个已完成周 + 当前部分周
    wk_k = np.full(n, np.nan); wk_d = np.full(n, np.nan)
    week_deque = deque(); current_week = None; ph, pl = None, None
    for i in range(n):
        ts = pd.Timestamp(dates_arr[i]); wk = (ts.isocalendar().year, ts.isocalendar().week)
        if wk != current_week:
            if current_week is not None:
                week_deque.append((current_week, ph, pl))
                while len(week_deque) > 8:
                    week_deque.popleft()
            current_week = wk; ph, pl = highs[i], lows[i]
        ph = max(ph, highs[i]); pl = min(pl, lows[i])
        wh = max([ph] + [h for _, h, _ in week_deque]); wl = min([pl] + [l for _, _, l in week_deque])
        rsv = 50.0 if wh == wl else (closes[i] - wl) / (wh - wl) * 100
        if i == 0:
            wk_k[i] = rsv; wk_d[i] = rsv
        else:
            wk_k[i] = 2/3 * wk_k[i-1] + 1/3 * rsv
            wk_d[i] = 2/3 * wk_d[i-1] + 1/3 * wk_k[i]
    daily['WK_dyn'] = pd.Series(wk_k, index=pd.to_datetime(dates_arr))
    daily['WD_dyn'] = pd.Series(wk_d, index=pd.to_datetime(dates_arr))
    daily['WK_frz'] = daily['skdj_weekly_k']; daily['WD_frz'] = daily['skdj_weekly_d']

    US = [('2025-03-12', 'B'), ('2025-04-03', 'S'), ('2025-04-14', 'B'),
          ('2025-06-23', 'S'), ('2025-10-13', 'B'), ('2025-11-14', 'S'), ('2026-07-06', 'B')]

    d = daily
    HH60 = d['close'].rolling(60, min_periods=1).max()
    k_up = (d['K'] > d['K'].shift(1)).fillna(False).values
    k_dn = (d['K'] < d['K'].shift(1)).fillna(False).values
    m_up = (d['DIF'] > d['DEA']).values
    hist_rise = (d['MACD'] > d['MACD'].shift(1)).fillna(False).values
    hist_fall = (d['MACD'] < d['MACD'].shift(1)).fillna(False).values
    K_arr = d['K'].values; D_arr = d['D'].values
    MACD_arr = d['MACD'].values; MA5_arr = d['MA5'].values
    close_arr = d['close'].values
    idx = d.index
    open_arr_full = d['open'].values

    def rollmax(a, w):
        return pd.Series(a).rolling(w, min_periods=1).max().values

    def rollmin(a, w):
        return pd.Series(a).rolling(w, min_periods=1).min().values

    R = {}
    for w in [2, 3, 5, 7]:
        R[('k_up', w)] = rollmax(k_up, w)
        R[('k_dn', w)] = rollmax(k_dn, w)
        R[('m_up', w)] = rollmax(m_up, w)
        R[('hr', w)] = rollmax(hist_rise, w)
        R[('hf', w)] = rollmin(hist_fall, w)

    # 连续3天未创新高 (HH60)
    hh_shift = np.full(len(HH60), np.nan)
    hh_shift[1:] = HH60.values[:-1]
    c1 = close_arr <= hh_shift
    c2 = np.full(len(close_arr), False); c2[1:] = close_arr[:-1] <= hh_shift[1:]
    c3 = np.full(len(close_arr), False); c3[2:] = close_arr[:-2] <= hh_shift[2:]
    no_new_high3 = c1 & c2 & c3
    no_new_high3[:3] = False

    us_pos = {}
    for date, sig in US:
        us_pos[date] = (sig, np.searchsorted(idx.values, np.datetime64(date)))

    bt_mask = (idx >= np.datetime64('2025-01-01'))
    open_bt = open_arr_full[bt_mask]
    close_bt = close_arr[bt_mask]
    n_bt = len(open_bt)

    param_grids = dict(
        b_kdj_win=[2, 3, 5], b_macd_win=[3, 5, 7], b_k_th=[45, 50, 55],
        b_week_th=[50, 55, 60], b_week_gold_win=[3, 4], b_yin_days=[2, 3],
        s_kdj_win=[3, 5, 7], s_k_th=[65, 70, 75], s_yang_days=[2, 3],
        s_week_th=[55, 60, 65], s_use_week=[True, False],
    )
    keys = list(param_grids.keys())
    combos = [dict(zip(keys, v)) for v in product(*[param_grids[k] for k in keys])]
    print(f'共 {len(combos)} 组参数 × 2种周线视图', flush=True)


    def eval_combo(p, WK_arr, WD_arr, wk_gold_cache):
        b_kdj_win, b_macd_win, bkth = p['b_kdj_win'], p['b_macd_win'], p['b_k_th']
        bwkth, b_week_gold_win, yin_days = p['b_week_th'], p['b_week_gold_win'], p['b_yin_days']
        s_kdj_win, skth, yang_days = p['s_kdj_win'], p['s_k_th'], p['s_yang_days']
        swkth, use_week_s = p['s_week_th'], p['s_use_week']

        if (bwkth, b_week_gold_win) not in wk_gold_cache:
            wgl = (WK_arr < bwkth) & (WK_arr > WD_arr)
            wk_gold_cache[(bwkth, b_week_gold_win)] = rollmax(wgl.astype(float), b_week_gold_win)
        wk_gold_low_win = wk_gold_cache[(bwkth, b_week_gold_win)]

        B = (K_arr < bkth) & (R[('k_up', b_kdj_win)] > 0) & \
            ((R[('m_up', b_macd_win)] > 0) | ((R[('hr', yin_days)] > 0) & (MACD_arr < 0))) & \
            ((WK_arr < bwkth) | (wk_gold_low_win > 0))

        k_high_win = rollmax((K_arr > skth).astype(float), s_kdj_win)
        cond_kdj_high = (k_high_win > 0) & (R[('k_dn', 3)] > 0) & (K_arr < D_arr)
        cond_macd_shrink = (R[('hf', yang_days)] > 0) & (MACD_arr > 0)
        cond_price = (close_arr < MA5_arr) | no_new_high3
        cond_week_high = WK_arr > swkth
        if not use_week_s:
            cond_week_high = np.ones(len(WK_arr), dtype=bool)
        S = cond_kdj_high & cond_macd_shrink & cond_price & cond_week_high

        hits = 0
        for date, (sig, pos) in us_pos.items():
            lo, hi = max(0, pos - 2), min(len(idx), pos + 3)
            if (B[lo:hi].any() if sig == 'B' else S[lo:hi].any()):
                hits += 1
        if hits < 6:
            return hits, None

        Bb = B[bt_mask]; Sb = S[bt_mask]
        cash = 100000.0; shares = 0; pos = 0
        for i in range(1, n_bt):
            op = open_bt[i]
            if pos == 0 and Bb[i - 1]:
                lot = int(cash / (op * 100 * 1.00025))
                if lot > 0:
                    cash -= lot * 100 * op * 1.00025
                    shares = lot * 100
                    pos = 1
            elif pos == 1 and Sb[i - 1]:
                cash += shares * op * (1 - 0.00125)
                shares = 0
                pos = 0
        if pos == 1:
            cash += shares * close_bt[-1] * (1 - 0.00125)
        return hits, cash / 100000 - 1


    for view, WK_arr, WD_arr in [
        ('动态周线', d['WK_dyn'].values, d['WD_dyn'].values),
        ('冻结周线', d['WK_frz'].values, d['WD_frz'].values),
    ]:
        wk_gold_cache = {}
        best_by_hit = {}
        for i, p in enumerate(combos):
            hits, ret = eval_combo(p, WK_arr, WD_arr, wk_gold_cache)
            if hits is not None and ret is not None:
                best_by_hit.setdefault(hits, []).append((ret, p))
            if (i + 1) % 5000 == 0:
                print(f'  [{view}] 进度 {i+1}/{len(combos)}', flush=True)

        for h in sorted(best_by_hit.keys(), reverse=True):
            lst = best_by_hit[h]
            lst.sort(key=lambda x: x[0], reverse=True)
            print(f'\n== [{view}] 命中{h}/7 共{len(lst)}组, 收益TOP3 ==')
            for ret, p in lst[:3]:
                print(f'  {ret*100:+.2f}%  {p}', flush=True)
        if not best_by_hit:
            print(f'== [{view}] 无>=6/7命中的参数 ==', flush=True)


if __name__ == '__main__':
    main()
"""
参考策略实验 (用户提供的高收益实现)
周线+日线共振信号:
  买入: 日线SKDJ低位企稳(K<50且近3天K上行) + MACD走强(近5天金叉 或 阴线缩3天) + 周线低位(WK<55 或 近4天低位金叉)
  卖出: 日线SKDJ高位转弱(近5天K曾>70且近3天K下行且K<D) + MACD阳线缩(近2天柱连续下降且MACD>0)
        + 价格走弱(close<MA5 或 连续3天未创新高) + 周线高位(WK>60)

用法: python strategy_ref.py
"""

import pickle
import pandas as pd
import numpy as np

CODE = '601857'
BT_START = '2025-01-01'

USER_SIGNALS = [
    ('2025-03-12', 'B'), ('2025-04-03', 'S'), ('2025-04-14', 'B'),
    ('2025-06-23', 'S'), ('2025-10-13', 'B'), ('2025-11-14', 'S'),
    ('2026-07-06', 'B'),
]

DEFAULT_PARAMS = dict(
    b_kdj_win=3, b_macd_win=5, b_k_th=50, b_week_th=55, b_week_gold_win=4,
    b_yin_days=3, s_kdj_win=5, s_k_th=70, s_yang_days=2,
    s_week_th=60, s_use_week=True,
)

# 变体开关
VARIANT = dict(
    m_up='gold',       # 'gold'=DIF>DEA金叉状态, 'dif_up'=DIF上升
    wk_gold_low='low', # 'low'=(WK<thr)&(WK>WD), 'gold'=仅(WK>WD)
)


def build_daily():
    """构建日线指标DataFrame (复用项目的预计算指标)"""
    hist = pickle.load(open('cache/hist_batch_20260731.pkl', 'rb'))
    df = None
    for k, v in hist.items():
        if k.endswith(CODE):
            df = v.copy()
            break
    if df is None:
        raise SystemExit(f'未找到 {CODE} 的历史数据')

    dates_arr = df['date'].values
    closes = df['close'].values.astype(float)
    volumes = df['volume'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)

    from indicator_cache import _incremental_indicators
    all_dates = set(pd.Timestamp(d).strftime('%Y-%m-%d') for d in dates_arr)
    res = _incremental_indicators(closes, volumes, highs, lows, dates_arr, all_dates)

    daily = pd.DataFrame(res).T
    daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()

    daily['open'] = pd.Series(df['open'].values.astype(float),
                              index=pd.to_datetime(df['date'].values))
    daily['K'] = daily['skdj_k']
    daily['D'] = daily['skdj_d']
    daily['J'] = daily['skdj_j']
    daily['DIF'] = daily['dif']
    daily['DEA'] = daily['dea']
    daily['MACD'] = daily['macd_hist']
    daily['MA5'] = daily['ma5']
    daily['WK'] = daily['skdj_weekly_k']
    daily['WD'] = daily['skdj_weekly_d']

    # 衍生列
    daily['HH60'] = daily['close'].rolling(60, min_periods=1).max()
    daily['k_up'] = (daily['K'] > daily['K'].shift(1)).fillna(False).astype(bool)
    daily['k_dn'] = (daily['K'] < daily['K'].shift(1)).fillna(False).astype(bool)
    if VARIANT['m_up'] == 'gold':
        daily['m_up'] = (daily['DIF'] > daily['DEA']).astype(bool)  # MACD金叉状态
    else:
        daily['m_up'] = (daily['DIF'] > daily['DIF'].shift(1)).fillna(False).astype(bool)  # DIF上行
    daily['hist_rise'] = (daily['MACD'] > daily['MACD'].shift(1)).fillna(False).astype(bool)
    daily['hist_fall'] = (daily['MACD'] < daily['MACD'].shift(1)).fillna(False).astype(bool)
    return daily


def gen_signals(daily, p):
    """生成B/S信号 (忠实还原参考实现)"""
    df = daily.copy()
    b_kdj_win, b_macd_win, bkth = p['b_kdj_win'], p['b_macd_win'], p['b_k_th']
    bwkth, b_week_gold_win, yin_days = p['b_week_th'], p['b_week_gold_win'], p['b_yin_days']
    s_kdj_win, skth, yang_days = p['s_kdj_win'], p['s_k_th'], p['s_yang_days']
    swkth, use_week_s = p['s_week_th'], p['s_use_week']

    # ---- 买入 ----
    k_up_win = df['k_up'].rolling(b_kdj_win, min_periods=1).max().astype(bool)
    m_up_win = df['m_up'].rolling(b_macd_win, min_periods=1).max().astype(bool)
    hist_rise_win = df['hist_rise'].rolling(yin_days, min_periods=1).max().astype(bool)
    if VARIANT['wk_gold_low'] == 'low':
        wk_gold_low = (df['WK'] < bwkth) & (df['WK'] > df['WD'])  # 周线低位金叉
    else:
        wk_gold_low = (df['WK'] > df['WD'])  # 仅周线金叉状态
    wk_gold_low_win = wk_gold_low.rolling(b_week_gold_win, min_periods=1).max().astype(bool)

    cond_kdj_low = (df['K'] < bkth) & k_up_win
    cond_macd = m_up_win | (hist_rise_win & (df['MACD'] < 0))
    cond_week_low = (df['WK'] < bwkth) | wk_gold_low_win
    df['B'] = cond_kdj_low & cond_macd & cond_week_low

    # ---- 卖出 ----
    hist_fall_win = df['hist_fall'].rolling(yang_days, min_periods=1).min().astype(bool)
    k_high_win = (df['K'] > skth).rolling(s_kdj_win, min_periods=1).max().astype(bool)
    k_dn_win = df['k_dn'].rolling(3, min_periods=1).max().astype(bool)

    cond_kdj_high = k_high_win & k_dn_win & (df['K'] < df['D'])
    cond_macd_shrink = hist_fall_win & (df['MACD'] > 0)
    no_new_high3 = (df['close'] <= df['HH60'].shift(1)) & \
        (df['close'].shift(1) <= df['HH60'].shift(2)) & \
        (df['close'].shift(2) <= df['HH60'].shift(3))
    cond_price = (df['close'] < df['MA5']) | no_new_high3
    cond_week_high = df['WK'] > swkth
    if not use_week_s:
        cond_week_high = pd.Series(True, index=df.index)
    df['S'] = cond_kdj_high & cond_macd_shrink & cond_price & cond_week_high
    return df


def verify(df, tol=2):
    """验证用户信号 (±tol交易日)"""
    idx = df.index
    result = []
    for date, sig in USER_SIGNALS:
        ts = pd.Timestamp(date)
        pos = np.searchsorted(idx.values, np.datetime64(ts))
        lo = max(0, pos - tol)
        hi = min(len(idx), pos + tol + 1)
        win = df.iloc[lo:hi]
        hit = win[sig].any()
        hit_dates = [d.strftime('%Y-%m-%d') for d in win[sig][win[sig]].index]
        result.append((date, sig, hit, hit_dates))
    n_hit = sum(1 for _, _, h, _ in result if h)
    return result, n_hit


def calc_pnl(df, start_date=BT_START, init_cash=100000.0,
             fee_rate=0.00025, stamp=0.001, stop_loss=None):
    """简单回测: 信号次日开盘价成交 (忠实还原参考实现)"""
    df = df[df.index >= start_date]
    cash = init_cash
    shares = 0
    trades = []
    pos = 0
    entry_price = 0.0
    for i in range(1, len(df)):
        date = df.index[i]
        prev = df.iloc[i - 1]
        row = df.iloc[i]
        open_p = row['open']
        if pos == 0 and prev['B']:
            lot = int(cash / (open_p * 100 * (1 + fee_rate)))
            if lot > 0:
                cost = lot * 100 * open_p
                fee = cost * fee_rate
                cash -= cost + fee
                shares = lot * 100
                pos = 1
                entry_price = open_p
                trades.append((date, 'B', open_p, cost + fee))
        elif pos == 1:
            sell = prev['S']
            if stop_loss and open_p <= entry_price * (1 + stop_loss):
                sell = True
            if sell:
                proceeds = shares * open_p
                fee = proceeds * (fee_rate + stamp)
                cash += proceeds - fee
                trades.append((date, 'S', open_p, proceeds - fee))
                shares = 0
                pos = 0
    if pos == 1:
        date = df.index[-1]
        row = df.iloc[-1]
        proceeds = shares * row['close']
        fee = proceeds * (fee_rate + stamp)
        cash += proceeds - fee
        trades.append((date, 'S(末)', row['close'], proceeds - fee))
        shares = 0
    ret = cash / init_cash - 1
    return cash, ret, trades


def main():
    daily = build_daily()
    p = DEFAULT_PARAMS
    df = gen_signals(daily, p)
    result, n_hit = verify(df)
    print('== 参数 ==')
    print(p)
    print(f'== 变体: {VARIANT} ==')
    print('== 用户信号验证(±2交易日) ==')
    for date, sig, hit, hit_dates in result:
        print(f"  {date} {sig}: {'命中' if hit else '未命中'} {hit_dates}")
    print(f'命中 {n_hit}/{len(USER_SIGNALS)}')

    cash, ret, trades = calc_pnl(df)
    print(f'\n回测(2025-01-01起, 无止损): 期末资产 {cash:.0f}, 总收益 {ret*100:.2f}%')
    print('交易记录:')
    for d, s, price, amt in trades:
        print(f'  {d.strftime("%Y-%m-%d")} {s} 价格{price:.2f}')

    cash2, ret2, _ = calc_pnl(df, stop_loss=-0.03)
    print(f'\n回测(带-3%止损): 总收益 {ret2*100:.2f}%')

    sig_df = df[df['B'] | df['S']][['close', 'MA5', 'K', 'D', 'J', 'DIF', 'DEA', 'MACD', 'WK', 'B', 'S']]
    print('\n== 全部信号 ==')
    print(sig_df.to_string())


if __name__ == '__main__':
    main()

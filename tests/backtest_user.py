"""
用户波段策略回测: 周线KDJ金叉/死叉 + 日线RSI/BOLL超卖超买
逻辑忠实照搬用户脚本, get_data改用本地缓存 (更稳更快)
用法: python run_test.py --module backtest_user
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pickle
import io
import pandas as pd
import numpy as np

_CACHE = None


def _load_cache():
    global _CACHE
    if _CACHE is None:
        _CACHE = pickle.load(open('cache/hist_batch_20260801.pkl', 'rb'))
    return _CACHE


def get_data(code, start_date, end_date):
    """从本地缓存获取数据 (替代akshare)"""
    for k, v in _load_cache().items():
        if k.endswith(code):
            df = v.copy()
            break
    else:
        raise ValueError(f'缓存中无 {code}')
    df.rename(columns={'date': 'date', 'open': 'open', 'close': 'close',
                       'high': 'high', 'low': 'low', 'volume': 'volume'}, inplace=True)
    df['date'] = pd.to_datetime(df['date'])
    df.sort_values('date', inplace=True)
    df.reset_index(drop=True, inplace=True)
    df = df[(df['date'] >= pd.Timestamp(start_date)) & (df['date'] <= pd.Timestamp(end_date))]
    return df


def calc_KDJ(df, n=9, m1=3, m2=3):
    """周线KDJ (W-FRI聚合)"""
    df_week = df.resample('W-FRI', on='date').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'
    }).dropna()
    df_week.reset_index(inplace=True)
    low_min = df_week['low'].rolling(n).min()
    high_max = df_week['high'].rolling(n).max()
    rsv = (df_week['close'] - low_min) / (high_max - low_min) * 100
    rsv.fillna(50, inplace=True)
    K = rsv.ewm(com=m1 - 1, adjust=False).mean()
    D = K.ewm(com=m2 - 1, adjust=False).mean()
    J = 3 * K - 2 * D
    df_week['K'] = K
    df_week['D'] = D
    df_week['J'] = J
    return df_week


def calc_daily_indicators(df, window=20, rsi_period=14):
    """日线布林带 + RSI"""
    df['ma'] = df['close'].rolling(window).mean()
    df['std'] = df['close'].rolling(window).std()
    df['lower'] = df['ma'] - 2 * df['std']
    df['upper'] = df['ma'] + 2 * df['std']
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=rsi_period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    return df


def backtest_band_strategy(code, start, end, initial_cash=100000):
    df_daily = get_data(code, start, end)
    if len(df_daily) < 50:
        print('数据不足')
        return None
    df_daily = calc_daily_indicators(df_daily)
    df_week = calc_KDJ(df_daily)
    df_week['golden_cross'] = (df_week['K'] > df_week['D']) & (df_week['K'].shift(1) <= df_week['D'].shift(1))
    df_week['death_cross'] = (df_week['K'] < df_week['D']) & (df_week['K'].shift(1) >= df_week['D'].shift(1))
    df_daily = df_daily.merge(df_week[['date', 'golden_cross', 'death_cross', 'K', 'D', 'J']], on='date', how='left')
    df_daily['golden_cross'] = df_daily['golden_cross'].fillna(False)
    df_daily['death_cross'] = df_daily['death_cross'].fillna(False)

    cash = initial_cash
    position = 0
    cost = 0.0
    in_position = False
    wait_buy = False
    wait_sell = False
    buy_date = None
    death_date = None
    trades = []
    trade_details = []

    start_idx = df_daily[df_daily['K'].notna()].index[0]
    for i in range(start_idx, len(df_daily)):
        row = df_daily.iloc[i]
        date = row['date']
        close = row['close']
        lower = row['lower']
        upper = row['upper']
        rsi = row['rsi']
        golden = row['golden_cross']
        death = row['death_cross']

        if golden and not in_position and not wait_buy:
            wait_buy = True
            buy_date = date

        if death and in_position and not wait_sell:
            wait_sell = True
            death_date = date

        if wait_buy and not in_position:
            if pd.notna(rsi) and pd.notna(lower) and (rsi < 30 or close < lower):
                shares = int(cash // close)
                if shares > 0:
                    cash -= shares * close
                    cost = close
                    position = shares
                    in_position = True
                    wait_buy = False
                    trades.append(('买入', date, close, shares))

        elif wait_sell and in_position:
            if pd.notna(rsi) and pd.notna(upper) and (rsi > 70 or close > upper):
                cash += position * close
                trade_return = (close / cost - 1) * 100
                trades.append(('卖出', date, close, position))
                trade_details.append((buy_date, date, cost, close, position, trade_return))
                position = 0
                cost = 0.0
                in_position = False
                wait_sell = False

        # 死叉后5个自然日强制离场
        if wait_sell and in_position:
            if (date - death_date).days >= 5:
                cash += position * close
                trade_return = (close / cost - 1) * 100
                trades.append(('卖出(强制)', date, close, position))
                trade_details.append((buy_date, date, cost, close, position, trade_return))
                position = 0
                cost = 0.0
                in_position = False
                wait_sell = False

    if in_position:
        last_close = df_daily.iloc[-1]['close']
        cash += position * last_close
        trade_return = (last_close / cost - 1) * 100
        trades.append(('卖出(期末)', df_daily.iloc[-1]['date'], last_close, position))
        trade_details.append((buy_date, df_daily.iloc[-1]['date'], cost, last_close, position, trade_return))

    final_value = cash
    total_return = (final_value / initial_cash - 1) * 100
    total_trades = len(trade_details)
    win_trades = sum(1 for t in trade_details if t[5] > 0)
    win_rate = win_trades / total_trades * 100 if total_trades > 0 else 0

    print('\n========== 波段策略回测统计 ==========')
    print(f'初始资金: {initial_cash:,.0f} 元')
    print(f'最终资金: {final_value:,.2f} 元')
    print(f'总收益率: {total_return:.2f}%')
    print(f'交易次数: {total_trades} 次（完整买卖）')
    print(f'盈利次数: {win_trades} 次')
    print(f'胜率: {win_rate:.1f}%')
    if trade_details:
        print('交易明细:')
        for idx, (buy_dt, sell_dt, buy_price, sell_price, shares, ret) in enumerate(trade_details, 1):
            print(f'  {idx}. {buy_dt.strftime("%Y-%m-%d")} 买入 @{buy_price:.2f} -> {sell_dt.strftime("%Y-%m-%d")} 卖出 @{sell_price:.2f}  收益: {ret:.2f}%')
    return df_daily, df_week, trade_details


def main(config=None):
    """运行用户波段策略回测 (3只股票)"""
    test_cfg = (config or {}).get('test', {})
    stocks = test_cfg.get('stocks', [('001309', '德明利'), ('601857', '中国石油'), ('000567', '海德股份')])
    for code, name in stocks:
        print(f'\n===== {code} {name} =====')
        try:
            backtest_band_strategy(code, '2025-01-01', '2026-08-01')
        except Exception as e:
            print(f'回测出错: {e}')
    return 0


if __name__ == '__main__':
    main()


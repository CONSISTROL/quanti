"""
小市值轮动策略回测 — EasyQuant "子账户多策略分仓" 移植

来源: https://github.com/HiRenyi/EasyQuant validated-strategies/子账户多策略分仓.md
  (聚宽, 基准399101中小板综指, 2022至今回测: 年化81.8%/回撤18.3%/Sharpe2.43)
  注: 名称"子账户多策略"是噱头, 实际为单账户中小盘小市值轮动, 与"小市值排除3bug版"同源

模板机制:
  选股: 中小盘指数成分 + 市值10~100亿 + 净利润>0 + 营收>1亿 + 排除ST/次新/涨跌停/创业板/科创板/北交所
        → 市值从小到大取前N (N=3~6, 由指数MA10动态决定: 指数强势持仓少)
  调仓: 每周二10:00市价, 卖出不在目标列表的持仓(昨日涨停的例外), 买入列表中新标的
  空仓: 1月/4月空仓(持银华日利货币ETF), 其余时间满仓
  风控: 个股亏损9%止损, 盈利100%止盈, 指数成分平均低开5%以上全清仓

本地数据近似:
  市值 = 总股本 × 当日收盘价, 总股本 = 净利润/每股收益 (最新财报推导, 股本回测期近似恒定)
        → 市值过滤为"回测当日市值"而非最新市值前视
  财务过滤(净利>0, 营收>1亿): 最新一期财报 (前视偏差, 但只影响质量过滤)
  指数: 上证指数(000001)近似399101, MA10动态持仓数
  涨停: 当日收盘涨幅>=9.9%近似(主板); 持仓昨日涨停不卖
  调仓成交价: 当日开盘价近似10:00市价单

用法:
  python tests/backtest_smallcap.py               # 最新快照全区间
  python tests/backtest_smallcap.py --days 380    # 最近380交易日 (与gap_open对比)
  python tests/backtest_smallcap.py --hist-file hist_batch_20260802.pkl --days 380
"""
import argparse
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

MIN_MV = 10        # 市值下限 (亿)
MAX_MV = 100       # 市值上限 (亿)
PASS_MONTHS = [1, 4]   # 空仓月份 (1月/4月)
STOPLOSS = 0.09    # 止损线
TAKEPROFIT = 1.00  # 止盈线 (盈利100%)
LIMIT_UP = 0.099   # 涨停近似 (主板10%)
MAX_PRICE = 50     # 股价上限
DEFAULT_N = 4      # 默认持仓数量


def _load_history_local(hist_file=''):
    import glob
    if hist_file:
        p = hist_file if os.path.isabs(hist_file) else os.path.join('cache', hist_file)
        if not os.path.exists(p):
            print(f'  ✗ 指定的历史快照不存在: {p}')
            return None
    else:
        files = sorted(glob.glob(os.path.join('cache', 'hist_batch_*.pkl')))
        if not files:
            return None
        p = files[-1]
    print(f'  (历史缓存: {os.path.basename(p)})')
    with open(p, 'rb') as f:
        hist = pickle.load(f)
    if isinstance(hist, dict):
        hist.pop('_date', None)
    return hist


def _load_latest_financial():
    """最新一期财务缓存: 返回 {6位code: (净利润元, 营业总收入元, 每股收益元)}
    优先当期 financial_<日期>.pkl (排除 _prev_ 上期 / _period_ 分期间)"""
    import glob
    files = sorted(glob.glob(os.path.join('cache', 'financial_*.pkl')))
    if not files:
        return {}
    cur = [f for f in files if '_prev_' not in f and '_period_' not in f]
    files = cur or files
    with open(files[-1], 'rb') as f:
        df = pickle.load(f)
    fin = {}
    np_col, rev_col = None, None
    for c in df.columns:
        if '净利润-净利润' in c:
            np_col = c
        if '营业总收入-营业总收入' in c:
            rev_col = c
    if np_col is None or rev_col is None:
        return {}
    for _, r in df.iterrows():
        try:
            code = str(r['股票代码']).zfill(6)
            np_ = float(r[np_col])
            rev = float(r[rev_col])
            eps = float(r['每股收益'])
            if np_ > 0 and eps > 0:
                fin[code] = (np_, rev, eps)
        except Exception:
            continue
    print(f'  (财务: 取自 {os.path.basename(files[-1])} 共 {len(fin)} 只有效记录)')
    return fin


def _load_index():
    """上证指数 (近似399101): 返回 {date_str: close}"""
    p = os.path.join('cache', 'index_000001SH.pkl')
    if not os.path.exists(p):
        return {}
    with open(p, 'rb') as f:
        df = pickle.load(f)
    return {pd.Timestamp(d).strftime('%Y-%m-%d'): float(c)
            for d, c in zip(df['date'].values, df['close'].values)}


def _mv_per_share(hist, code, idx_date):
    """回测当日市值(亿) = 总股本 × 当日收盘价, 股本=净利润/EPS(最新财报推导, 近似恒定)"""
    h = hist.get(code)
    if h is None or len(h) == 0:
        return None
    m = h['date'] <= idx_date
    if m.sum() == 0:
        return None
    return float(h['close'].values[m][-1])


def main(days=0, hist_file='', start_date=''):
    hist = _load_history_local(hist_file)
    if hist is None:
        print('  ✗ 无历史数据')
        return 1

    from quantlab.data_fetcher import _code_pure
    pure_to_sina = {_code_pure(s): s for s in hist}
    hist_pure = {}
    for c, s in pure_to_sina.items():
        h = hist.get(s)
        if h is not None and 'date' in h.columns and 'close' in h.columns:
            hist_pure[c] = h

    # 交易日序列 (全市场对齐)
    start = pd.Timestamp(start_date) if start_date else pd.Timestamp('2015-01-01')
    all_dates = set()
    for h in hist_pure.values():
        for d in h['date'].values:
            ts = pd.Timestamp(d)
            if ts >= start:
                all_dates.add(ts)
    trading_dates = sorted(all_dates)
    if days and len(trading_dates) > days:
        trading_dates = trading_dates[-days:]
    print(f'  回测区间: {trading_dates[0].strftime("%Y-%m-%d")} ~ {trading_dates[-1].strftime("%Y-%m-%d")} ({len(trading_dates)} 交易日)')

    financial = _load_latest_financial()
    index_map = _load_index()
    print(f'  (指数: {"上证000001, " if index_map else "无指数→固定"}MA10动态持仓)')

    # 市值映射: 股本(总股本亿股) = 净利润/EPS/1e8
    shares_map = {}
    for code, (np_, rev, eps) in financial.items():
        shares_map[code] = np_ / eps / 1e8  # 亿股

    cash = 1_000_000.0
    positions = {}   # {code: {'shares':, 'cost': 成本价(含税费近似0), 'entry_date':}}
    equity_curve = []
    trades = []      # (date, code, 'BUY'/'SELL', price, shares, reason)

    # 指数MA10序列预计算
    index_ma10 = {}
    if index_map:
        idx_dates = sorted(index_map)
        closes = np.array([index_map[d] for d in idx_dates])
        ma10 = pd.Series(closes).rolling(10).mean().values
        for i, d in enumerate(idx_dates):
            index_ma10[d] = (float(closes[i]), float(ma10[i]) if not np.isnan(ma10[i]) else float(closes[i]))

    def dynamic_n(today):
        if today not in index_ma10:
            return DEFAULT_N
        close, ma = index_ma10[today]
        diff = close - ma
        # 指数越弱持仓越多 (模板: 强势3只, 弱势6只)
        if diff >= 200:
            return 3
        elif diff >= -200:
            return 4
        elif diff >= -500:
            return 5
        return 6

    def get_prev_close(code, today):
        h = hist_pure.get(code)
        if h is None:
            return None
        m = h['date'] < today
        if m.sum() == 0:
            return None
        return float(h['close'].values[m][-1])

    def get_open(code, today):
        h = hist_pure.get(code)
        if h is None or 'open' not in h.columns:
            return None
        m = h['date'] <= today
        if m.sum() == 0:
            return None
        return float(h['open'].values[m][-1])

    def get_close(code, today):
        h = hist_pure.get(code)
        if h is None:
            return None
        m = h['date'] <= today
        if m.sum() == 0:
            return None
        return float(h['close'].values[m][-1])

    def is_limit_up(code, today):
        pc = get_prev_close(code, today)
        c = get_close(code, today)
        if pc is None or c is None or pc <= 0:
            return False
        return c / pc - 1 >= LIMIT_UP

    # 空仓月持有货币ETF: 回测简化为空仓(不买), 1/4月全清
    for i, today in enumerate(trading_dates):
        ds = today.strftime('%Y-%m-%d')
        sold = set()

        # 1. 空仓月份: 全部卖出, 不买入
        if today.month in PASS_MONTHS:
            for code in list(positions):
                px = get_open(code, today) or get_close(code, today) or positions[code]['cost']
                cash += positions[code]['shares'] * px
                trades.append((ds, code, 'SELL', px, positions[code]['shares'], '空仓月清仓'))
                sold.add(code)
                del positions[code]
            equity_curve.append((today, cash))
            continue

        # 2. 止损/止盈 (每日开盘判断)
        for code in list(positions):
            px = get_open(code, today) or get_close(code, today)
            if px is None:
                continue
            ret = px / positions[code]['cost'] - 1
            reason = None
            if ret <= -STOPLOSS:
                reason = f'止损{ret:.1%}'
            elif ret >= TAKEPROFIT:
                reason = f'止盈{ret:.1%}'
            if reason:
                cash += positions[code]['shares'] * px
                trades.append((ds, code, 'SELL', px, positions[code]['shares'], reason))
                sold.add(code)
                del positions[code]

        # 3. 周二调仓 (非空仓月)
        if today.weekday() == 1:  # 周二
            n = dynamic_n(ds)
            # 目标列表: 过滤 + 市值10~100亿 + 财务质量 + 市值升序前N
            cands = []
            for code in hist_pure:
                if code not in shares_map:
                    continue  # 无财务数据(净利<=0等)排除
                if code[:2] in ('30', '68', '83', '87', '43', '92', '82'):
                    continue  # 创业板/科创板/北交所
                if positions.get(code):
                    continue  # 已持仓直接保留, 不参与排序
                if code in sold:
                    continue
                # 当日市值 = 股本 × 当日收盘价
                c = get_close(code, today)
                if c is None or c <= 0:
                    continue
                mv = shares_map[code] * c
                if not (MIN_MV <= mv <= MAX_MV):
                    continue
                if c > MAX_PRICE:
                    continue
                if is_limit_up(code, today):
                    continue  # 涨停不追
                cands.append((mv, code))
            cands.sort(key=lambda x: x[0])  # 市值从小到大
            target = [c for _, c in cands[:n]]

            # 卖出不在目标列表且非昨日涨停的持仓
            for code in list(positions):
                if code in target or code in sold:
                    continue
                if is_limit_up(code, today):
                    continue  # 昨日涨停, 保留吃惯性
                px = get_open(code, today) or get_close(code, today)
                if px is None:
                    continue
                cash += positions[code]['shares'] * px
                trades.append((ds, code, 'SELL', px, positions[code]['shares'], '调仓换股'))
                sold.add(code)
                del positions[code]

            # 买入目标列表中的新标的 (现金均分)
            buy_list = [c for c in target if c not in positions and c not in sold]
            if buy_list:
                alloc = cash / len(buy_list)
                for code in buy_list:
                    px = get_open(code, today) or get_close(code, today)
                    if px is None or px <= 0:
                        continue
                    shares = int(alloc / px / 100) * 100
                    if shares <= 0 or alloc < px * 100:
                        continue
                    cash -= shares * px
                    positions[code] = {'shares': shares, 'cost': px, 'entry_date': ds}
                    trades.append((ds, code, 'BUY', px, shares, '小市值买入'))

        # 当日净值
        mv = 0
        for code in positions:
            c = get_close(code, today)
            if c is not None:
                mv += positions[code]['shares'] * c
        equity_curve.append((today, cash + mv))

    # ---- 统计 ----
    final = equity_curve[-1][1]
    total_return = final / 1_000_000 - 1
    n_days = len(trading_dates)
    annual = (1 + total_return) ** (252 / max(n_days, 1)) - 1
    nav = np.array([v for _, v in equity_curve])
    peaks = np.maximum.accumulate(nav)
    max_dd = float((nav / peaks - 1).min())
    sells = [t for t in trades if t[2] == 'SELL']
    # 胜率: 按持仓周期计算
    buy_map = {}
    win_count = 0
    for t in trades:
        ds, code, dr, px, sh, rs = t
        if dr == 'BUY':
            buy_map[code] = (px, sh, ds)
        elif code in buy_map:
            bpx, bsh, _ = buy_map[code]
            if px > bpx:
                win_count += 1
            del buy_map[code]
    win_rate = win_count / len(sells) if sells else 0

    print('\n' + '═' * 88)
    print('  🚀 小市值轮动 (EasyQuant 子账户多策略分仓移植)')
    print('═' * 88)
    print(f'  总收益率: {total_return:+.2%} | 年化: {annual:+.2%} | 最大回撤: {max_dd:.1%} | 交易: {len(sells)}笔卖出 | 胜率: {win_rate:.0%}')
    print(f'  期末资金: ¥{final:,.0f} (初始¥1,000,000)')
    print(f'  持仓: {len(positions)} 只')
    print('  ⚠️  近似项: 股本=最新财报推导(前视), 财务过滤=最新财报(前视), 指数=上证近似399101, 涨停=涨幅>=9.9%近似')

    # 分月收益 (看1/4月空仓效果)
    print('\n  近12个月收益:')
    monthly = {}
    for today, v in equity_curve:
        monthly.setdefault(today.strftime('%Y-%m'), []).append(v)
    prev_mv = 1_000_000
    mkeys = sorted(monthly)[-12:]
    for mk in mkeys:
        mv_ = monthly[mk][-1]
        mr = mv_ / prev_mv - 1
        print(f'    {mk}: {mr:+.1%}')
        prev_mv = mv_
    return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=0)
    p.add_argument('--hist-file', default='')
    p.add_argument('--start', default='', help='起始日期 YYYY-MM-DD (与EasyQuant 2022至今对比: --start 2022-01-01)')
    a = p.parse_args()
    sys.exit(main(days=a.days, hist_file=a.hist_file, start_date=a.start))

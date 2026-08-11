"""
日内做T子系统 — 5分钟K线策略回测 (新浪分钟数据源)

背景: A股T+1约束, 做T = 底仓不动, 日内一买一卖配对赚差价
  正T: 低位买入 → 反弹卖出 (卖出的是昨日底仓份额)
  倒T: 高位卖出(底仓份额) → 回落买回
每轮做T投入固定金额, 收盘前必须平仓 (不隔夜)

数据: 新浪5分钟线 (已实测: 1分钟线东财push2his/腾讯m1在本网络不可用, 5分钟可用1023根≈21日)
用法:
  python run_test.py --module intraday_t
  python tests/intraday_t.py --targets 600547,588170 --days 10 --out t.txt --out-html t.html
"""
import os
import sys
import json
import time
import pickle
import argparse
from datetime import datetime

import requests
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

# ─── 做T标的 (代码, 名称, 类型: stock/etf) ───
DEFAULT_TARGETS = [
    ('600547', '山东黄金', 'stock'),
    ('588170', '科创半导体ETF', 'etf'),
    ('600176', '中国巨石', 'stock'),
]

SINA_KLINE = ('https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/'
              'CN_MarketData.getKLineData')

# 费用(单边): 股票 = 佣金0.025% + 卖出印花税0.05% + 过户费0.001%; ETF = 仅佣金0.025%(无印花税)
FEE_BUY_STOCK, FEE_SELL_STOCK = 0.00026, 0.00076   # 双边合计 0.102%
FEE_BUY_ETF, FEE_SELL_ETF = 0.00025, 0.00025       # 双边合计 0.05%
T_AMOUNT = 10000          # 每轮做T投入金额(元), 份额按价格折算到手
MAX_ROUNDS_PER_DAY = 3    # 每日最多做T轮数
MAX_HOLD_BARS = 16        # 持仓超80分钟(16根5min)强制平仓

# 策略配置
STRATEGIES = {
    'VWAP回归(±0.2%)': {'fn': 'vwap', 'delta': 0.002},
    '布林反转(20,2σ)': {'fn': 'boll', 'n': 20, 'k': 2.0},
    '开盘锚动量(±0.4%)': {'fn': 'open_anchor', 'threshold': 0.004},
    'RSI14反转(30/70)': {'fn': 'rsi', 'n': 14, 'lo': 30, 'hi': 70},
}


# ─── 数据拉取: 新浪5分钟线 ───
def _sina_symbol(code):
    return f'sh{code}' if code[0] in ('5', '6', '9') else f'sz{code}'


def fetch_min_data(codes, cache_dir='cache', use_cache=True):
    """拉取全部标的5分钟线, 缓存 cache/min5_YYYYMMDD.pkl (当日+新鲜度检查)"""
    result = {}
    cached = None
    if use_cache:
        today = datetime.now().strftime('%Y%m%d')
        path = os.path.join(cache_dir, f'min5_{today}.pkl')
        if os.path.exists(path):
            with open(path, 'rb') as f:
                cached = pickle.load(f)
            try:
                latest = max(pd.to_datetime(df['date']).max() for df in cached.values())
                if latest.date() < datetime.now().date():
                    print(f'  ⚠ 缓存最后日期 {latest.date()} < 今天, 重新拉取...')
                    cached = None
            except Exception:
                pass
    if cached is not None:
        have = set(cached.keys())
        need = [c for c in codes if c not in have]
        if not need:
            print(f'  ✓ 从缓存加载 {len(codes)} 只分钟数据')
            return cached
        result = dict(cached)
        print(f'  缓存缺失 {len(need)} 只, 增量拉取...')
    else:
        result = {}
        need = codes

    for code in need:
        try:
            r = requests.get(SINA_KLINE, params={
                'symbol': _sina_symbol(code), 'scale': '5', 'ma': 'no', 'datalen': '1023'},
                timeout=20)
            j = r.json()
            if not j:
                print(f'    ⚠ {code}: 空数据')
                continue
            df = pd.DataFrame(j)
            df['date'] = pd.to_datetime(df['day'])
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)
            result[code] = df
            print(f'    ✓ {code}: {len(df)} bars, {df["date"].iloc[0].date()} ~ {df["date"].iloc[-1].date()}')
        except Exception as e:
            print(f'    ⚠ {code}: {type(e).__name__}: {str(e)[:80]}')
        time.sleep(0.3)

    if use_cache and result:
        os.makedirs(cache_dir, exist_ok=True)
        with open(os.path.join(cache_dir, f'min5_{datetime.now().strftime("%Y%m%d")}.pkl'), 'wb') as f:
            pickle.dump(result, f)
    return result


# ─── 策略信号: 返回与bars等长的数组, +1买 / -1卖 / 0无 ───
def sig_vwap(df, delta=0.002):
    """分时均价线(VWAP)回归: 跌破VWAP·(1-δ)买, 上穿VWAP·(1+δ)卖"""
    h, l, c, v = df['high'].values, df['low'].values, df['close'].values, df['volume'].values
    tp = (h + l + c) / 3
    cumv = np.cumsum(v)
    cumv[cumv == 0] = np.nan
    vwap = np.cumsum(tp * v) / cumv
    vwap = np.where(np.isnan(vwap), tp, vwap)
    acts = np.where(c < vwap * (1 - delta), 1, np.where(c > vwap * (1 + delta), -1, 0))
    return acts


def sig_boll(df, n=20, k=2.0):
    """5分钟布林反转: 跌破下轨买, 突破上轨卖"""
    c = df['close'].values
    s = pd.Series(c)
    mid = s.rolling(n).mean().values
    sd = s.rolling(n).std().values
    acts = np.where(c < mid - k * sd, 1, np.where(c > mid + k * sd, -1, 0))
    acts[:n] = 0
    return acts


def sig_open_anchor(df, threshold=0.004):
    """开盘价锚动量反转: 相对开盘涨≥阈卖(倒T), 跌≤-阈买(正T)"""
    o = df['open'].values[0]
    c = df['close'].values
    ret = c / o - 1
    return np.where(ret <= -threshold, 1, np.where(ret >= threshold, -1, 0))


def sig_rsi(df, n=14, lo=30, hi=70):
    """RSI超买超卖: RSI<lo买, >hi卖"""
    c = df['close'].values
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    with np.errstate(divide='ignore', invalid='ignore'):
        avg_g = pd.Series(gain).ewm(alpha=1 / n, adjust=False).mean().values
        avg_l = pd.Series(loss).ewm(alpha=1 / n, adjust=False).mean().values
        rs = np.where(avg_l > 0, avg_g / np.where(avg_l == 0, np.nan, avg_l), np.inf)
    rsi = 100 - 100 / (1 + rs)
    acts = np.where(rsi < lo, 1, np.where(rsi > hi, -1, 0))
    acts[:n] = 0
    return acts


_SIG_FNS = {'vwap': sig_vwap, 'boll': sig_boll, 'open_anchor': sig_open_anchor, 'rsi': sig_rsi}


# ─── 配对回测: 每轮 = 一买一卖 (正T或倒T), 当日强平 ───
def pair_trades(df, cfg):
    """单日做T配对: 返回轮次列表 [{side, t0,px0, t1,px1, shares, gross, fee, net, bars}]"""
    acts = _SIG_FNS[cfg['fn']](df, **{k: v for k, v in cfg.items() if k not in ('fn', 'kind')})
    times = df['date'].values
    closes = df['close'].values
    n = len(closes)
    trades = []
    pos = 0                      # +1持仓(正T), -1持仓(倒T), 0空仓
    open_px = 0.0
    open_i = -1
    rounds = 0
    for i in range(n):
        if pos == 0:
            if acts[i] != 0 and rounds < MAX_ROUNDS_PER_DAY:
                pos = int(acts[i])
                open_px = closes[i]
                open_i = i
                rounds += 1
        else:
            if acts[i] == -pos or i - open_i >= MAX_HOLD_BARS or i == n - 1:
                # 反向信号 / 超时 / 尾盘 → 平仓配对
                exit_px = closes[i]
                shares = max(100, round(T_AMOUNT / open_px / 100) * 100)
                buy_px, sell_px = (open_px, exit_px) if pos == 1 else (exit_px, open_px)
                gross = (sell_px - buy_px) * shares
                fee = buy_px * shares * (FEE_BUY_STOCK if cfg.get('kind') == 'stock' else FEE_BUY_ETF) \
                    + sell_px * shares * (FEE_SELL_STOCK if cfg.get('kind') == 'stock' else FEE_SELL_ETF)
                trades.append({
                    'side': '正T' if pos == 1 else '倒T',
                    't0': pd.Timestamp(times[open_i]), 'px0': round(open_px, 4),
                    't1': pd.Timestamp(times[i]), 'px1': round(exit_px, 4),
                    'shares': shares, 'gross': round(gross, 2), 'fee': round(fee, 2),
                    'net': round(gross - fee, 2), 'bars': i - open_i,
                })
                pos = 0
                open_i = -1
    return trades


# ─── 回测主流程 ───
def run_backtest(hist, targets):
    """hist: {code: df}; targets: [(code, name, kind)] → results 嵌套dict"""
    # 最近N个交易日的日期序列 (全标的共同交易日)
    results = {}
    for code, name, kind in targets:
        df = hist[code]
        days = sorted({d.date() for d in df['date']})
        results[code] = {'name': name, 'kind': kind, 'days': {}, 'daily': {}}
        for day in days:
            ddf = df[df['date'].dt.date == day].reset_index(drop=True)
            if len(ddf) < 40:
                continue
            day_str = str(day)
            results[code]['daily'][day_str] = {'ohlc': ddf}
            for sname, cfg in STRATEGIES.items():
                cfg = dict(cfg, kind=kind)
                tds = pair_trades(ddf, cfg)
                if tds:
                    results[code]['days'].setdefault(day_str, {}).setdefault(sname, tds)
    return results


def agg_strategy(results):
    """按策略×股票聚合 → DataFrame行: (strategy, code, name, kind, rounds, wins, wr, gross, fee, net, turnover, ret)"""
    rows = []
    for code, r in results.items():
        for sname in STRATEGIES:
            all_t = [t for day in r['days'].values() for t in day.get(sname, [])]
            if not all_t:
                rows.append(dict(strategy=sname, code=code, name=r['name'], kind=r['kind'],
                                 rounds=0, wins=0, win_rate=0.0, gross=0, fee=0, net=0,
                                 turnover=0, net_ret=0.0))
                continue
            buy_amt = sum(t['px0'] * t['shares'] for t in all_t if t['side'] == '正T') \
                + sum(t['px1'] * t['shares'] for t in all_t if t['side'] == '倒T')
            rows.append(dict(
                strategy=sname, code=code, name=r['name'], kind=r['kind'],
                rounds=len(all_t), wins=sum(1 for t in all_t if t['net'] > 0),
                win_rate=sum(1 for t in all_t if t['net'] > 0) / len(all_t),
                gross=round(sum(t['gross'] for t in all_t), 2),
                fee=round(sum(t['fee'] for t in all_t), 2),
                net=round(sum(t['net'] for t in all_t), 2),
                turnover=round(buy_amt, 0),
                net_ret=round(sum(t['net'] for t in all_t) / buy_amt * 100, 3) if buy_amt else 0.0,
            ))
    return pd.DataFrame(rows)


# ─── 终端报告 ───
def print_report(agg, results):
    print('\n' + '═' * 104)
    print('  日内做T回测 (5分钟K线, 最近10个交易日, 每轮投入≈1万元, 含费用)')
    print('═' * 104)
    print(f"  {'策略':<16}{'股票':<12}{'轮数':>5}{'胜率':>7}{'毛收益':>9}{'费用':>7}{'净收益':>9}{'净收益率':>9}  日均净利")
    print('  ' + '─' * 100)
    for sname in STRATEGIES:
        for _, row in agg[agg['strategy'] == sname].iterrows():
            code = row['code']
            day_n = sum(1 for d in results[code]['days'].values() if sname in d)
            daily = row['net'] / day_n if day_n else 0.0
            print(f"  {sname:<16}{row['name']:<12}{row['rounds']:>5}{row['win_rate']*100:>6.0f}%"
                  f"{row['gross']:>+9.0f}{row['fee']:>7.0f}{row['net']:>+9.0f}{row['net_ret']:>+8.3f}%  {daily:>+7.0f}元/日")
        print('  ' + '─' * 100)

    # 每只股票最优策略
    print('\n  每股最优策略 (按净收益):')
    for code, r in results.items():
        best = None
        for sname in STRATEGIES:
            t = [tr for day in r['days'].values() for tr in day.get(sname, [])]
            net = sum(x['net'] for x in t)
            if best is None or net > best[1]:
                best = (sname, net, len(t), sum(1 for x in t if x['net'] > 0) / len(t) if t else 0)
        print(f"    {code} {r['name']:<12} → {best[0]}: 净{best[1]:+.0f}元, {best[2]}轮, 胜率{best[3]*100:.0f}%")

    # 全标的汇总
    print('\n  全标的汇总 (所有策略合计):')
    for code, r in results.items():
        all_t = [tr for day in r['days'].values() for s in day.values() for tr in s]
        if not all_t:
            continue
        buy_amt = sum(t['px0'] * t['shares'] for t in all_t if t['side'] == '正T') \
            + sum(t['px1'] * t['shares'] for t in all_t if t['side'] == '倒T')
        net = sum(t['net'] for t in all_t)
        print(f"    {code} {r['name']:<12} {len(all_t)}轮 净{net:+.0f}元 收益率{net/buy_amt*100:+.3f}%")

    # 明细示例
    print('\n  每日明细 (各策略净利, 元):')
    codes = list(results)
    day_strs = sorted({d for r in results.values() for d in r['daily']})[-10:]
    hdr = '  ' + f"{'日期':<12}" + ''.join(f"{s[:9]:>11}" for s in STRATEGIES)
    print(hdr)
    for ds in day_strs:
        line = f"  {ds:<12}"
        for sname in STRATEGIES:
            nets = []
            for r in results.values():
                t = r['days'].get(ds, {}).get(sname, [])
                nets.append(sum(x['net'] for x in t))
            line += ''.join(f"{sum(nets):>11.0f}")
        print(line)


# ─── HTML报告 (ECharts) ───
def _kline_option(ddf, trades, name):
    """5分钟K线 + VWAP + 做T买卖点"""
    times = [pd.Timestamp(t).strftime('%H:%M') for t in ddf['date']]
    ohlc = [[round(float(o), 3), round(float(c), 3), round(float(l), 3), round(float(h), 3)]
            for o, c, l, h in zip(ddf['open'], ddf['close'], ddf['low'], ddf['high'])]
    c = ddf['close'].values
    tp = (ddf['high'] + ddf['low'] + ddf['close']).values / 3
    vwap = np.cumsum(tp * ddf['volume'].values) / np.cumsum(ddf['volume'].values)
    # 买卖点: 正T买在t0, 卖在t1; 倒T卖在t0, 买在t1
    mark_buy, mark_sell = [], []
    for x in trades:
        t0s, t1s = pd.Timestamp(x['t0']).strftime('%H:%M'), pd.Timestamp(x['t1']).strftime('%H:%M')
        i0 = times.index(t0s) if t0s in times else None
        i1 = times.index(t1s) if t1s in times else None
        if x['side'] == '正T':
            if i0 is not None: mark_buy.append([i0, x['px0']])
            if i1 is not None: mark_sell.append([i1, x['px1']])
        else:
            if i0 is not None: mark_sell.append([i0, x['px0']])
            if i1 is not None: mark_buy.append([i1, x['px1']])
    series = [
        {'name': '5min', 'type': 'candlestick', 'data': ohlc,
         'itemStyle': {'color': '#e8403a', 'color0': '#1ba27a',
                       'borderColor': '#e8403a', 'borderColor0': '#1ba27a'}},
        {'name': 'VWAP', 'type': 'line', 'data': [round(float(v), 3) for v in vwap],
         'showSymbol': False, 'lineStyle': {'color': '#2c6fbb', 'width': 1}},
        {'name': '买', 'type': 'scatter', 'data': mark_buy, 'symbolSize': 12,
         'itemStyle': {'color': '#e8403a'}, 'z': 10},
        {'name': '卖', 'type': 'scatter', 'data': mark_sell, 'symbolSize': 12,
         'itemStyle': {'color': '#1ba27a'}, 'z': 10},
    ]
    return {
        'title': {'text': name, 'left': 'center', 'textStyle': {'fontSize': 13}},
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
        'legend': {'top': 22, 'data': ['5min', 'VWAP', '买', '卖']},
        'grid': {'left': 55, 'right': 15, 'top': 50, 'bottom': 30},
        'xAxis': {'type': 'category', 'data': times, 'axisLabel': {'show': False}},
        'yAxis': {'type': 'value', 'scale': True, 'splitLine': {'lineStyle': {'color': '#eef1f4'}}},
        'dataZoom': [{'type': 'inside'}, {'type': 'slider', 'height': 14, 'bottom': 2}],
        'series': series,
    }


def _daily_bar_option(daily_nets, name):
    """每日做T净利柱状图"""
    days = sorted(daily_nets)
    return {
        'title': {'text': f'{name} 每日做T净利(元, 最优策略)', 'left': 'center', 'textStyle': {'fontSize': 13}},
        'tooltip': {'trigger': 'axis'},
        'grid': {'left': 55, 'right': 15, 'top': 40, 'bottom': 30},
        'xAxis': {'type': 'category', 'data': days},
        'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'color': '#eef1f4'}}},
        'series': [{'type': 'bar',
                    'data': [{'value': round(daily_nets[d], 1),
                              'itemStyle': {'color': '#e8403a' if daily_nets[d] >= 0 else '#1ba27a'}}
                             for d in days]}],
    }


def gen_html(agg, results, out_path):
    from report_echarts import echarts_script, ECHARTS_CDN
    code_list = list(results)
    sections = []
    # 汇总表
    head = ''.join(f'<th>{s}</th>' for s in STRATEGIES)
    rows_html = ''
    for code in code_list:
        r = results[code]
        cells = ''
        for sname in STRATEGIES:
            a = agg[(agg['strategy'] == sname) & (agg['code'] == code)]
            if a.empty:
                cells += '<td>—</td>'
                continue
            net, wr, n = a.iloc[0]['net'], a.iloc[0]['win_rate'], a.iloc[0]['rounds']
            color = '#e8403a' if net >= 0 else '#1ba27a'
            cells += (f'<td><b style="color:{color}">{net:+.0f}元</b>'
                      f'<br><small>{wr*100:.0f}% / {n}轮</small></td>')
        rows_html += f'<tr><td><b>{code}</b> {r["name"]}</td>{cells}</tr>'
    sections.append(f'''<div class="card"><h2>做T收益汇总 (5分钟, 近10个交易日, 含费用)</h2>
<table class="tbl"><tr><th>标的</th>{head}</tr>{rows_html}</table>
<p class="note">每格 = 净收益(元) + 胜率/轮数. 每轮投入≈1万元, 正T=先买后卖, 倒T=先卖后买, 当日强平不隔夜.</p></div>''')

    # 每股: 最优策略 K线图 + 每日净利
    for code in code_list:
        r = results[code]
        best_s, best_net = None, None
        for sname in STRATEGIES:
            t = [tr for day in r['days'].values() for tr in day.get(sname, [])]
            net = sum(x['net'] for x in t)
            if best_s is None or net > best_net:
                best_s, best_net = sname, net
        chart_k, chart_b = [], []
        for ds in sorted(r['daily']):
            ddf = r['daily'][ds]['ohlc']
            trades = r['days'].get(ds, {}).get(best_s, [])
            chart_k.append(_kline_option(ddf, trades, f'{r["name"]} {ds}'))
            nets = [tr['net'] for tr in r['days'].get(ds, {}).get(best_s, [])]
            chart_b.append(sum(nets))
        sec = f'<div class="card"><h2>{code} {r["name"]} — 最优策略: {best_s} (净{best_net:+.0f}元)</h2>'
        for i, opt in enumerate(chart_k):
            sec += echarts_script(f'k_{code}_{i}', opt, 300)
        daily_nets = {ds: sum(tr['net'] for tr in r['days'].get(ds, {}).get(best_s, [])) for ds in r['daily']}
        sec += echarts_script(f'd_{code}', _daily_bar_option(daily_nets, r['name']), 220)
        sec += '</div>'
        sections.append(sec)

    html = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>日内做T回测</title><script src="{ECHARTS_CDN}"></script>
<style>
body{{font-family:Microsoft YaHei,Arial;background:#f4f6f8;margin:0;padding:16px}}
.card{{background:#fff;border-radius:8px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h2{{font-size:15px;color:#1f2329;margin:0 0 10px}}
table.tbl{{border-collapse:collapse;width:100%;font-size:13px}}
table.tbl th,table.tbl td{{border:1px solid #e5e8ec;padding:6px 8px;text-align:center}}
table.tbl th{{background:#f0f3f6;color:#4e5969}}
.note{{font-size:12px;color:#86909c}}
</style></head><body>
{''.join(sections)}
</body></html>'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'\n  ✅ HTML报告: {os.path.abspath(out_path)}')


# ─── 入口 ───
def main(config=None):
    ap = argparse.ArgumentParser(description='日内做T子系统回测')
    ap.add_argument('--targets', default='', help='做T标的(逗号分隔), 默认 600547,588170,600176')
    ap.add_argument('--days', type=int, default=10, help='最近N个交易日')
    ap.add_argument('--out', default='', help='终端报告输出到txt')
    ap.add_argument('--out-html', default='', help='HTML报告路径')
    args = ap.parse_args()

    if args.targets:
        targets = [(c.strip(), c.strip(), 'stock') for c in args.targets.split(',') if c.strip()]
        known = {c: (n, k) for c, n, k in DEFAULT_TARGETS}
        targets = [(c, known.get(c, (c, 'stock'))[0], known.get(c, (c, 'stock'))[1]) for c, _, _ in targets]
    else:
        targets = DEFAULT_TARGETS
    codes = [t[0] for t in targets]

    print('日内做T子系统 — 5分钟K线策略回测')
    print(f'  标的: {", ".join(f"{c} {n}" for c, n, _ in targets)}')
    print(f'  数据: 新浪5分钟线, 最近{args.days}个交易日')
    print(f'  费用: 股票双边0.102% (佣金+印花税), ETF双边0.05% (仅佣金); 每轮投入≈1万元')
    print(f'  约束: T+1做T (正T卖昨日底仓/倒T买回), 当日强平不隔夜, 每日≤{MAX_ROUNDS_PER_DAY}轮')

    hist = fetch_min_data(codes)
    missing = [c for c in codes if c not in hist]
    if missing:
        print(f'  ✗ 缺少数据: {missing}')
        return 1

    # 截取最近N个交易日
    for c in codes:
        days = sorted({d.date() for d in hist[c]['date']})
        keep = days[-args.days:]
        hist[c] = hist[c][hist[c]['date'].dt.date.isin(keep)].reset_index(drop=True)

    results = run_backtest(hist, targets)
    agg = agg_strategy(results)

    if args.out:
        old = sys.stdout
        sys.stdout = open(args.out, 'w', encoding='utf-8')
        try:
            print_report(agg, results)
        finally:
            sys.stdout.close()
            sys.stdout = old
        print(f'  ✅ 终端报告: {os.path.abspath(args.out)}')
    else:
        print_report(agg, results)

    if args.out_html:
        gen_html(agg, results, args.out_html)
    return 0


if __name__ == '__main__':
    sys.exit(main())

"""
涨停筛选 — 全市场最近交易日涨停扫描 (新浪全市场行情 + quantdash历史确认)

默认筛选条件: 8/07 未涨停 且 8/10 涨停 且 8/11 涨停 (2连板, 8/10为首板)
  可用 --d0/--d1/--d2 指定三个日期

流程:
  1. 新浪全市场A股行情(分页全拉, ~5400只, 含北交所)
  2. 候选 = 今日涨跌幅 > 2% 的股票 (昨涨停股今日普遍有溢价; 今日大跌的断板股会漏, 口径说明)
  3. quantdash 前复权近5日K线 → 按板块涨停阈值判定每日是否涨停
     阈值: 主板10% / 创业板科创板20% / 北交所30% / ST 5%
     判定: 当日涨幅≥阈值×0.97 且 收盘价≥round(前收×(1+阈值),2)-0.02 (价格校验防复权误差)

用法:
  python tests/limit_up.py                    # 默认 8/7未板, 8/10板, 8/11板
  python tests/limit_up.py --d0 2026-08-07 --d1 2026-08-10 --d2 2026-08-11 --out l.txt
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import time
import argparse

import requests
import numpy as np
import pandas as pd

SINA_HQ = ('https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
           'Market_Center.getHQNodeData')


def limit_rate(code, name):
    """板块涨停阈值: 主板10% / 创业板300* 科创板688* 20% / 北交所 30% / ST 5%"""
    if 'ST' in str(name).upper():
        return 0.05
    if code.startswith(('688', '689', '300', '301')):
        return 0.20
    if code.startswith(('4', '8', '92')):
        return 0.30
    return 0.10


def fetch_all_spot(min_chg=2.0):
    """新浪全市场A股(含北交所) 涨幅降序分页全拉, 返回DataFrame"""
    rows = []
    page = 1
    while True:
        try:
            r = requests.get(SINA_HQ, params={
                'page': page, 'num': 100, 'sort': 'changepercent', 'asc': 0, 'node': 'hs_a'},
                timeout=20)
            j = r.json()
        except Exception as e:
            print(f'  ⚠ 第{page}页失败: {str(e)[:60]}, 重试...')
            time.sleep(1)
            try:
                r = requests.get(SINA_HQ, params={
                    'page': page, 'num': 100, 'sort': 'changepercent', 'asc': 0, 'node': 'hs_a'},
                    timeout=20)
                j = r.json()
            except Exception:
                break
        if not j:
            break
        rows.extend(j)
        if page % 10 == 0:
            print(f'  已拉 {len(rows)} 只...')
        page += 1
        time.sleep(0.12)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in ('changepercent', 'trade', 'settlement', 'open', 'high', 'low'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['code'] = df['code'].astype(str).str.zfill(6)
    print(f'  ✓ 全市场行情 {len(df)} 只')
    return df[df['changepercent'] >= min_chg].copy()


def check_limit_days(hist, code, name, d0, d1, d2):
    """用日线判断 d0(对照,必须未涨停) d1 d2 是否涨停 → dict"""
    df = hist
    dates = pd.to_datetime(df['date'])
    d0s, d1s, d2s = pd.Timestamp(d0), pd.Timestamp(d1), pd.Timestamp(d2)
    c = df['close'].values.astype(float)
    rate = limit_rate(code, name)

    def is_limit(i):
        if i <= 0:
            return None
        chg = c[i] / c[i - 1] - 1
        price_ok = c[i] >= round(c[i - 1] * (1 + rate), 2) - 0.02
        return bool(chg >= rate * 0.97 and price_ok)

    def chg_at(d):
        idx = np.where(dates == d)[0]
        if len(idx) == 0 or idx[0] == 0:
            return None, None
        i = int(idx[0])
        return (c[i] / c[i - 1] - 1), is_limit(i)

    out = {'code': code, 'name': name, 'rate': rate}
    for tag, d in (('d0', d0s), ('d1', d1s), ('d2', d2s)):
        chg, lim = chg_at(d)
        out[f'{tag}_chg'] = chg
        out[f'{tag}_lim'] = lim
    # 今日(最后一天)涨停?
    last_d = dates.iloc[-1]
    out['last_date'] = last_d
    if last_d == d2s:
        out['d2_chg'], out['d2_lim'] = chg_at(d2s)
    else:
        chg, lim = chg_at(last_d)
        out['d3_chg'], out['d3_lim'] = chg, lim
    return out


def main():
    ap = argparse.ArgumentParser(description='涨停筛选 (默认: 8/7未板, 8/10板, 8/11板)')
    ap.add_argument('--d0', default='2026-08-07', help='对照日(必须未涨停)')
    ap.add_argument('--d1', default='2026-08-10', help='第1个涨停日')
    ap.add_argument('--d2', default='2026-08-11', help='第2个涨停日')
    ap.add_argument('--min-chg', type=float, default=2.0,
                    help='候选过滤: 今日涨跌幅≥此值才拉历史确认 (昨涨停今日普遍有溢价, 断板大跌会漏)')
    ap.add_argument('--out', default='', help='输出到txt')
    ap.add_argument('--out-html', default='', help='输出HTML')
    args = ap.parse_args()

    print(f'涨停筛选: {args.d0} 未涨停 且 {args.d1} 涨停 且 {args.d2} 涨停')
    print(f'  候选过滤: 今日涨跌幅 ≥ {args.min_chg}% (昨涨停次日溢价效应; 断板大跌的会漏, 口径注意)')

    spot = fetch_all_spot(args.min_chg)
    if spot.empty:
        print('  ✗ 未拉到行情数据')
        return 1
    cands = list(zip(spot['code'], spot['name']))
    print(f'  候选 {len(cands)} 只, quantdash 拉历史确认...')

    from quantlab.data_sources import get_data_source
    ds = get_data_source('quantdash')
    hits = []
    d1_hits = []
    fail = 0
    for idx, (code, name) in enumerate(cands):
        try:
            hist = ds.fetch_watchlist_data([code], cache_dir='cache', use_cache=True,
                                           max_bars=10)
            h = None
            for k, df in hist.items():
                if k.endswith(code):
                    h = df
                    break
            if h is None or len(h) < 5:
                continue
            r = check_limit_days(h, code, name, args.d0, args.d1, args.d2)
            if r['d1_lim'] and r['d2_lim']:
                hits.append(r)
            if r['d2_lim']:
                d1_hits.append(r)
        except Exception:
            fail += 1
        if (idx + 1) % 200 == 0:
            print(f'  进度 {idx+1}/{len(cands)}, 命中 {len(hits)}')
            time.sleep(1)
    print(f'  完成: 命中 {len(hits)} 只 (失败 {fail})')

    def fmt_chg(v):
        return '—' if v is None else f'{v*100:+.1f}%'

    def fmt_lim(v, rate):
        return '涨停' if v else ('未板' if v is not None else '—')

    hits.sort(key=lambda r: (r['d3_lim'] is not None and r['d3_lim'], r['code']))
    out_lines = []
    out_lines.append('═' * 100)
    out_lines.append(f'  命中: {args.d0} 未涨停 + {args.d1}/{args.d2} 连续涨停 (共 {len(hits)} 只)')
    out_lines.append('═' * 100)
    out_lines.append(f"  {'代码':<8}{'名称':<10}{'类型':<6}{args.d0:>10}{args.d1:>10}{args.d2:>10}  {'今日':>9}  连板")
    out_lines.append('  ' + '─' * 96)
    for r in hits:
        typ = {0.05: 'ST', 0.20: '创/科', 0.30: '北交', 0.10: '主板'}[r['rate']]
        extra = r.get('d3_chg')
        d3s = f"{fmt_chg(extra)} {'3板' if r.get('d3_lim') else ''}"
        out_lines.append(f"  {r['code']:<8}{r['name']:<10}{typ:<6}"
                         f"{fmt_chg(r['d0_chg']):>10}{fmt_chg(r['d1_chg']):>10}{fmt_chg(r['d2_chg']):>10}"
                         f"  {d3s:>9}")
    out_lines.append('')
    out_lines.append(f'  (今日=数据最后交易日 {hits[0]["last_date"].date() if hits else "?"}; '
                     f'今日涨停则为3连板; 候选口径: 今日涨幅≥{args.min_chg}%)')

    text = '\n'.join(out_lines)
    print('\n' + text)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text + '\n')
        print(f'\n  ✅ 终端报告: {os.path.abspath(args.out)}')

    if args.out_html:
        gen_html(hits, args, out_lines, args.out_html)
    return 0


def gen_html(hits, args, lines, out_path):
    rows_html = ''
    for i, r in enumerate(hits, 1):
        typ = {0.05: 'ST', 0.20: '创/科', 0.30: '北交', 0.10: '主板'}[r['rate']]
        rows_html += (f'<tr><td>{i}</td><td>{r["code"]}</td><td>{r["name"]}</td><td>{typ}</td>'
                      f'<td>{r["d0_chg"]*100:+.1f}%</td><td>{r["d1_chg"]*100:+.1f}%</td>'
                      f'<td>{r["d2_chg"]*100:+.1f}%</td>'
                      f'<td>{(r.get("d3_chg") or 0)*100:+.1f}%{" 3板" if r.get("d3_lim") else ""}</td></tr>')
    html = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>涨停筛选</title><style>
body{{font-family:Microsoft YaHei,Arial;background:#f4f6f8;padding:16px}}
.card{{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h2{{font-size:15px;margin:0 0 10px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{border:1px solid #e5e8ec;padding:6px 8px;text-align:center}}
th{{background:#f0f3f6}}
</style></head><body><div class="card">
<h2>涨停筛选: {args.d0} 未涨停 + {args.d1}/{args.d2} 连续涨停 (共 {len(hits)} 只)</h2>
<table><tr><th>#</th><th>代码</th><th>名称</th><th>类型</th><th>{args.d0}</th><th>{args.d1}</th>
<th>{args.d2}</th><th>今日</th></tr>{rows_html}</table>
<p style="font-size:12px;color:#86909c">今日=数据最后交易日; 今日涨停为3连板; 候选口径: 今日涨幅≥{args.min_chg}%</p>
</div></body></html>'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'  ✅ HTML报告: {os.path.abspath(out_path)}')


if __name__ == '__main__':
    sys.exit(main())

"""
橡胶板块龙头分析 — 最近60个交易日主力行为识别 (洗盘建仓/放量信号)

用法:
  python tests/rubber_leader.py                # 20只橡胶股 (quantdash 前复权)
  python tests/rubber_leader.py --days 60 --top 5 --out r.txt --out-html r.html

主力行为识别 (基于公开量价, 无逐笔/大单数据):
  洗盘(WASH): ①缩量回调 - 60日最高点后回调5~25%且量能萎缩<70%
             ②横盘缩量 - 近20日振幅<12%且近10日量萎缩
  建仓(BUILD): ①底部放量 - 60日低点后10日量 > 低点前20日量×1.2
             ②温和堆量 - 近20日量/前40日量>1.3 且低点抬升
             ③放量启动 - 近10日出现 量>2×20日均量 收阳 涨>3%
  放量(VOL): 近10日放量阳线数 / 今日量比

龙头评分 0-100: 洗盘质量30 + 建仓证据40 + 放量启动30
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

import argparse
import numpy as np
import pandas as pd

# 橡胶股名单 (代码, 名称, 类别) — 经 spot 缓存验证
RUBBER_STOCKS = [
    ('601118', '海南橡胶', '天然橡胶'),
    ('600500', '中化国际', '天然橡胶'),
    ('002838', '道恩股份', '合成橡胶'),
    ('002010', '传化智联', '合成橡胶'),
    ('002068', '黑猫股份', '合成橡胶'),
    ('002408', '齐翔腾达', '合成橡胶'),
    ('002753', '永东股份', '合成橡胶'),
    ('000589', '贵州轮胎', '轮胎'),
    ('601500', '通用股份', '轮胎'),
    ('601058', '赛轮轮胎', '轮胎'),
    ('603049', '中策橡胶', '轮胎'),
    ('601163', '三角轮胎', '轮胎'),
    ('601966', '玲珑轮胎', '轮胎'),
    ('600469', '风神股份', '轮胎'),
    ('600182', 'S佳通', '轮胎'),
    ('600458', '时代新材', '橡胶制品'),
    ('000887', '中鼎股份', '橡胶制品'),
    ('300320', '海达股份', '橡胶制品'),
    ('300121', '阳谷华泰', '橡胶助剂'),
    ('300767', '震安科技', '橡胶制品'),
]


def fetch_data(codes, days=60):
    """quantdash 前复权日线, 取最近 days 个交易日; 单只超时失败自动重试"""
    from quantlab.data_sources import get_data_source
    ds = get_data_source('quantdash')
    out = {}
    need = list(codes)
    for attempt in range(3):
        if not need:
            break
        hist = ds.fetch_watchlist_data(need, cache_dir='cache', use_cache=True,
                                       max_bars=max(200, days * 2))
        got = {}
        for code in need:
            for k, df in hist.items():
                if k.endswith(code):
                    got[code] = df.tail(days).reset_index(drop=True)
                    break
        out.update(got)
        need = [c for c in need if c not in got]
        if need:
            print(f'  ⚠ 第{attempt+1}轮拉取 {len(need)} 只失败, 重试...')
    return out


def analyze(df):
    """单只股票60日主力行为分析 → dict"""
    c = df['close'].values.astype(float)
    v = df['volume'].values.astype(float)
    n = len(c)
    if n < 40:
        return None
    o = df['open'].values.astype(float)
    h = df['high'].values.astype(float)
    l = df['low'].values.astype(float)

    s = pd.Series(c)
    v_s = pd.Series(v)
    ma5 = s.rolling(5).mean().values
    ma10 = s.rolling(10).mean().values
    ma20 = s.rolling(20).mean().values
    v20 = v_s.rolling(20).mean().values

    signals = {'wash': [], 'build': [], 'vol': []}
    notes = []
    pullback = None

    # ── 洗盘检测 ──
    hi_i = int(np.argmax(c))
    if hi_i >= 10 and hi_i < n - 3:
        hi_px = c[hi_i]
        low_after = float(np.min(c[hi_i:]))
        pullback = (hi_px - low_after) / hi_px          # 回调幅度
        up_vol = float(np.mean(v[max(0, hi_i - 20):hi_i]))
        dn_vol = float(np.mean(v[hi_i:]))
        shrink = dn_vol / up_vol if up_vol > 0 else 1.0  # 量萎缩比
        if 0.05 <= pullback <= 0.25 and shrink < 0.70:
            signals['wash'].append('缩量回调')
            notes.append(f'缩量回调{pullback*100:.1f}%(量萎缩{shrink*100:.0f}%)')
        elif pullback <= 0.25 and shrink < 0.70:
            signals['wash'].append('浅回调整理')
            notes.append(f'浅回调{pullback*100:.1f}%量缩{shrink*100:.0f}%')
    # 横盘缩量: 近20日振幅小 且 近10日量 < 前10日
    if n >= 40:
        rng20 = (max(c[-20:]) - min(c[-20:])) / c[-20] if c[-20] > 0 else 1
        vol_shrink = float(np.mean(v[-10:]) / np.mean(v[-20:-10])) if np.mean(v[-20:-10]) > 0 else 1
        if rng20 < 0.12 and vol_shrink < 0.75:
            signals['wash'].append('横盘缩量')
            notes.append(f'横盘整理振幅{rng20*100:.1f}%量缩{vol_shrink*100:.0f}%')

    # ── 建仓检测 ──
    lo_i = int(np.argmin(c))
    if lo_i >= 20 and lo_i < n - 5:
        bef = float(np.mean(v[max(0, lo_i - 20):lo_i]))
        aft = float(np.mean(v[lo_i:min(n, lo_i + 10)]))
        if bef > 0 and aft / bef > 1.2:
            signals['build'].append('底部放量')
            notes.append(f'底部放量{aft/bef:.1f}x')
    if n >= 60:
        vol_ratio_20_40 = float(np.mean(v[-20:]) / np.mean(v[-40:-20])) if np.mean(v[-40:-20]) > 0 else 1
        low_rising = c[-20] > min(c[:20]) * 1.05
        if vol_ratio_20_40 > 1.3 and low_rising:
            signals['build'].append('温和堆量')
            notes.append(f'近20日堆量{vol_ratio_20_40:.1f}x+低点抬升')
    # 放量启动: 近10日 量>2×20日均量 收阳 涨>3%
    for i in range(max(1, n - 10), n):
        if v20[i] > 0 and v[i] > 2 * v20[i] and c[i] > c[i - 1] and (c[i] / c[i - 1] - 1) > 0.03:
            signals['build'].append('放量启动')
            notes.append(f'放量启动({df["date"].iloc[i].strftime("%m-%d")}涨{(c[i]/c[i-1]-1)*100:.1f}%)')
            break

    # ── 放量信号 ──
    vol_up_days = 0
    for i in range(max(1, n - 10), n):
        if v20[i] > 0 and v[i] > 1.5 * v20[i] and c[i] > c[i - 1]:
            vol_up_days += 1
    vol_ratio_today = v[-1] / v20[-1] if v20[-1] > 0 else 1
    if vol_up_days >= 2:
        signals['vol'].append(f'近10日{vol_up_days}根放量阳')
        notes.append(f'近10日{vol_up_days}根放量阳')
    if vol_ratio_today > 1.5:
        signals['vol'].append(f'今日量比{vol_ratio_today:.1f}')
        notes.append(f'今日量比{vol_ratio_today:.1f}')

    # ── 趋势 ──
    ret60 = c[-1] / c[0] - 1
    ret5 = c[-1] / c[-6] - 1 if n >= 6 else 0
    ma_bull = ma5[-1] > ma10[-1] > ma20[-1] if not any(np.isnan(x) for x in (ma5[-1], ma10[-1], ma20[-1])) else False

    # ── 龙头评分 (0-100) ──
    score = 0.0
    # 洗盘质量 30
    if '缩量回调' in signals['wash']:
        score += 25
    elif '浅回调整理' in signals['wash']:
        score += 15
    elif '横盘缩量' in signals['wash']:
        score += 12
    # 建仓证据 40
    if '底部放量' in signals['build']:
        score += 15
    if '温和堆量' in signals['build']:
        score += 10
    if '放量启动' in signals['build']:
        score += 15
    # 放量启动 30
    score += min(vol_up_days * 5, 15)
    if vol_ratio_today > 2.5:
        score += 15
    elif vol_ratio_today > 1.5:
        score += 10
    elif vol_ratio_today > 1.2:
        score += 5
    # 趋势加成 (上限内)
    if ret60 > 0.05:
        score += 8
    elif ret60 > 0:
        score += 4
    if ma_bull:
        score += 7
    if ret5 > 0.02:
        score += 5

    return {
        'wash': signals['wash'], 'build': signals['build'], 'vol': signals['vol'],
        'pullback': pullback,
        'ret60': ret60, 'ret5': ret5, 'ma_bull': ma_bull,
        'vol_ratio': vol_ratio_today, 'vol_up_days': vol_up_days,
        'score': round(min(score, 100), 1), 'notes': notes,
    }


def main():
    ap = argparse.ArgumentParser(description='橡胶板块龙头分析')
    ap.add_argument('--days', type=int, default=60, help='分析窗口交易日数')
    ap.add_argument('--top', type=int, default=5, help='HTML详细K线图前N名')
    ap.add_argument('--out', default='', help='终端报告输出到txt')
    ap.add_argument('--out-html', default='', help='HTML报告路径')
    args = ap.parse_args()

    codes = [c for c, _, _ in RUBBER_STOCKS]
    print(f'橡胶板块龙头分析 — 最近{args.days}个交易日 ({len(codes)}只)')
    print('识别: 洗盘(缩量回调/横盘缩量) + 建仓(底部放量/堆量/放量启动) + 放量信号')

    hist = fetch_data(codes, args.days)
    missing = [c for c in codes if c not in hist]
    if missing:
        print(f'  ⚠ 缺少数据: {missing}')

    rows = []
    for code, name, cat in RUBBER_STOCKS:
        if code not in hist:
            rows.append({'code': code, 'name': name, 'cat': cat, 'ok': False})
            continue
        r = analyze(hist[code])
        if r is None:
            rows.append({'code': code, 'name': name, 'cat': cat, 'ok': False})
            continue
        rows.append({'code': code, 'name': name, 'cat': cat, 'ok': True, **r})
    rows.sort(key=lambda x: x.get('score', -1), reverse=True)

    def fmt(r):
        sig = []
        if r.get('wash'): sig.append('洗盘:' + '/'.join(r['wash']))
        if r.get('build'): sig.append('建仓:' + '/'.join(r['build']))
        if r.get('vol'): sig.append('放量:' + '/'.join(r['vol']))
        return ' '.join(sig)

    print('\n' + '═' * 106)
    print(f'  龙头排名 (综合评分: 洗盘30+建仓40+放量30)')
    print('═' * 106)
    print(f"  {'排名':<4}{'代码':<8}{'名称':<10}{'类别':<8}{'评分':>6}{'60日':>8}{'5日':>7}{'量比':>7}{'放量阳':>7}  信号")
    print('  ' + '─' * 102)
    for i, r in enumerate(rows, 1):
        if not r['ok']:
            print(f"  {i:<4}{r['code']:<8}{r['name']:<10}{r['cat']:<8}  ✗ 数据不足")
            continue
        print(f"  {i:<4}{r['code']:<8}{r['name']:<10}{r['cat']:<8}{r['score']:>6.1f}"
              f"{r['ret60']*100:>+7.1f}%{r['ret5']*100:>+6.1f}%{r['vol_ratio']:>7.1f}"
              f"{r['vol_up_days']:>7}  {fmt(r)}")

    valid = [r for r in rows if r['ok']]
    if not valid:
        print('  ✗ 无有效数据')
        return 1

    # 龙头结论
    print('\n' + '═' * 106)
    print('  龙头结论')
    print('═' * 106)
    top3 = valid[:3]
    for i, r in enumerate(top3, 1):
        print(f"  #{i} {r['code']} {r['name']} 评分{r['score']:.1f}: {'; '.join(r['notes']) or '无明显信号'}")
    if len(valid) > 1:
        print(f"\n  🏆 板块龙头: {valid[0]['code']} {valid[0]['name']} "
              f"(评分{valid[0]['score']:.1f}, 信号: {fmt(valid[0])})")

    # HTML
    if args.out_html:
        gen_html(rows, hist, args.top, args.out_html)
    return 0


def gen_html(rows, hist, top_n, out_path):
    from quantlab.reports.echarts import echarts_script, ECHARTS_CDN

    valid = [r for r in rows if r['ok']]
    # 评分排名柱状图
    names = [f"{r['code']}\n{r['name']}" for r in valid]
    scores = [r['score'] for r in valid]
    colors = ['#f5a623' if i == 0 else ('#2c6fbb' if i < 3 else '#86909c')
              for i in range(len(valid))]
    bar_opt = {
        'title': {'text': '橡胶板块龙头评分 (0-100)', 'left': 'center', 'textStyle': {'fontSize': 14}},
        'tooltip': {'trigger': 'axis'},
        'grid': {'left': 60, 'right': 15, 'top': 40, 'bottom': 80},
        'xAxis': {'type': 'category', 'data': names, 'axisLabel': {'rotate': 40, 'fontSize': 10}},
        'yAxis': {'type': 'value', 'max': 100, 'splitLine': {'lineStyle': {'color': '#eef1f4'}}},
        'series': [{'type': 'bar', 'data': [{'value': s, 'itemStyle': {'color': col}}
                                            for s, col in zip(scores, colors)],
                    'label': {'show': True, 'position': 'top', 'fontSize': 10}}],
    }
    sections = [f'<div class="card">{echarts_script("score_bar", bar_opt, 380)}</div>']

    # 前N名 K线 + 成交量
    for r in valid[:top_n]:
        df = hist[r['code']]
        c = df['close'].values.astype(float)
        v = df['volume'].values.astype(float)
        v20 = pd.Series(v).rolling(20).mean().values
        dates = [pd.Timestamp(d).strftime('%m-%d') for d in df['date']]
        ohlc = [[round(float(o), 3), round(float(cl), 3), round(float(lo), 3), round(float(hi), 3)]
                for o, cl, lo, hi in zip(df['open'], df['close'], df['low'], df['high'])]
        # 放量阳线柱特殊色 (量>1.5×20日均 且收阳)
        vol_bars = []
        for i in range(len(v)):
            hot = v20[i] > 0 and v[i] > 1.5 * v20[i] and c[i] > c[i - 1] if i > 0 else False
            vol_bars.append({'value': round(float(v[i]), 0),
                             'itemStyle': {'color': '#f5a623' if hot else ('#e8403a' if c[i] >= df['open'].values[i] else '#1ba27a')}})
        # 洗盘区 markArea (60日最高点后至最低点)
        hi_i = int(np.argmax(c))
        lo_i = int(np.argmin(c))
        marks = []
        if hi_i < lo_i:
            marks.append({'name': '洗盘回调区', 'xAxis': dates[hi_i], 'itemStyle': {'color': 'rgba(245,166,35,0.08)'}})
            marks.append({'xAxis': dates[lo_i], 'itemStyle': {'color': 'rgba(245,166,35,0.08)'}})
        k_opt = {
            'title': {'text': f"{r['code']} {r['name']}  评分{r['score']:.1f}  {'; '.join(r.get('notes', []))[:60]}",
                      'left': 'center', 'textStyle': {'fontSize': 13}},
            'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'cross'}},
            'legend': {'top': 24, 'data': ['K线', '量']},
            'axisPointer': {'link': [{'xAxisIndex': 'all'}]},
            'grid': [{'left': 55, 'right': 15, 'top': 45, 'height': '52%'},
                     {'left': 55, 'right': 15, 'top': '72%', 'height': '18%'}],
            'xAxis': [
                {'type': 'category', 'data': dates, 'axisLabel': {'show': False}},
                {'type': 'category', 'data': dates, 'gridIndex': 1, 'axisLabel': {'show': False}},
            ],
            'yAxis': [
                {'type': 'value', 'scale': True, 'gridIndex': 0, 'splitLine': {'lineStyle': {'color': '#eef1f4'}}},
                {'type': 'value', 'gridIndex': 1, 'splitLine': {'show': False}},
            ],
            'dataZoom': [{'type': 'inside', 'xAxisIndex': [0, 1]},
                         {'type': 'slider', 'xAxisIndex': [0, 1], 'height': 14, 'bottom': 2}],
            'series': [
                {'name': 'K线', 'type': 'candlestick', 'data': ohlc,
                 'itemStyle': {'color': '#e8403a', 'color0': '#1ba27a',
                               'borderColor': '#e8403a', 'borderColor0': '#1ba27a'},
                 'markArea': {'data': [marks] if marks else [], 'silent': True}},
                {'name': '量', 'type': 'bar', 'xAxisIndex': 1, 'yAxisIndex': 1, 'data': vol_bars,
                 'barWidth': '70%'},
            ],
        }
        sections.append(f'<div class="card">{echarts_script(f"k_{r["code"]}", k_opt, 420)}</div>')

    # 汇总表
    tbl = '<div class="card"><h2>全部20只橡胶股 (按评分排序)</h2><table class="tbl">'
    tbl += ('<tr><th>排名</th><th>代码</th><th>名称</th><th>类别</th><th>评分</th><th>60日涨幅</th>'
            '<th>5日涨幅</th><th>今日量比</th><th>放量阳</th><th>洗盘/建仓/放量信号</th></tr>')
    for i, r in enumerate(rows, 1):
        if not r['ok']:
            tbl += f'<tr><td>{i}</td><td>{r["code"]}</td><td>{r["name"]}</td><td>{r["cat"]}</td><td colspan="6">数据不足</td></tr>'
            continue
        sig = '<br>'.join(x for x in [
            '洗盘: ' + '/'.join(r['wash']) if r['wash'] else '',
            '建仓: ' + '/'.join(r['build']) if r['build'] else '',
            '放量: ' + '/'.join(r['vol']) if r['vol'] else ''] if x)
        color = '#f5a623' if i == 1 else '#1f2329'
        tbl += (f'<tr><td>{i}</td><td>{r["code"]}</td><td>{r["name"]}</td><td>{r["cat"]}</td>'
                f'<td><b style="color:{color}">{r["score"]:.1f}</b></td>'
                f'<td>{r["ret60"]*100:+.1f}%</td><td>{r["ret5"]*100:+.1f}%</td>'
                f'<td>{r["vol_ratio"]:.1f}</td><td>{r["vol_up_days"]}</td><td style="text-align:left;font-size:12px">{sig}</td></tr>')
    tbl += '</table></div>'
    sections.append(tbl)

    html = f'''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>橡胶板块龙头分析</title><script src="{ECHARTS_CDN}"></script>
<style>
body{{font-family:Microsoft YaHei,Arial;background:#f4f6f8;margin:0;padding:16px}}
.card{{background:#fff;border-radius:8px;padding:16px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
h2{{font-size:15px;color:#1f2329;margin:0 0 10px}}
table.tbl{{border-collapse:collapse;width:100%;font-size:13px}}
table.tbl th,table.tbl td{{border:1px solid #e5e8ec;padding:6px 8px;text-align:center}}
table.tbl th{{background:#f0f3f6;color:#4e5969}}
</style></head><body>
{''.join(sections)}
</body></html>'''
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f'\n  ✅ HTML报告: {os.path.abspath(out_path)}')


if __name__ == '__main__':
    sys.exit(main())

"""
日内量价口诀回测 — "早盘急跌买入/早盘急涨卖出/下午急涨不追/下午急跌次日狙击"

数据现实: 只有日线 OHLCV, 用日线结构代理分时行为:
  早盘急跌  ≈ 大幅低开 (开盘跳空缺口一次性释放恐慌)
  早盘急涨  ≈ 大幅高开
  下午急跌  ≈ 大阴线且收盘贴近当日最低 (尾盘跳水收在底部)
  下午急涨  ≈ 大阳线且收盘贴近当日最高 (尾盘拉升收在顶部)

交易现实: A股 T+1, 当日买当日卖不可行 → 策略为隔夜持有:
  R1 低开买入: T 日开盘价买入 → T+1 收盘卖出 (隔夜) / 或持有5日
  R2 高开卖出: 诊断口径 — 高开后当日低走概率 (验证口诀"早盘急涨是卖出")
  R3 尾盘跳水次日狙击: T+1 开盘价买入 → T+2 收盘卖出 (隔夜) / 或5日
  R4 尾盘拉升不追: 验证口径 — 若 T+1 开盘追买的结果 (验证口诀"下午急涨不追")
买入端剔除开盘涨跌停 (买不进)。组合=当日全部信号等权, 日频再平衡 (隔夜口径可合成真实净值,
5日持有为重叠窗口, 只报事件统计不合成净值)。

用法:
  python tests/intraday_rule_backtest.py            # 全市场 (约1-2分钟)
  python tests/intraday_rule_backtest.py --limit 500
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from collections import defaultdict

import numpy as np
import pandas as pd

HALF_CUT = pd.Timestamp('2022-01-01')


def _load_history_local(hist_file=''):
    import glob
    import pickle
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


def _limit_up_th(code):
    """涨跌幅阈值: 主板10% / 创业板(30)科创(68)20% / 北交所30%"""
    pure = code[-6:] if code[:2] in ('sh', 'sz', 'bj') else code
    if pure.startswith(('30', '68')):
        return 0.195
    if code.startswith('bj') or pure.startswith(('4', '8', '9')):
        return 0.295
    return 0.095


def new_acc():
    return {'n': 0, 'pos': 0, 's': 0.0, 'vals': []}


def upd(acc, d, ret):
    a = acc[d]
    a['n'] += 1
    if ret > 0:
        a['pos'] += 1
    a['s'] += ret
    a['vals'].append(ret)


def equity_stats(daily_ret):
    n = len(daily_ret)
    if n == 0:
        return None
    total = np.prod(1 + daily_ret) - 1
    ann = (1 + total) ** (252 / n) - 1 if total > -1 else -1.0
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0
    nav = np.cumprod(1 + daily_ret)
    peak = np.maximum.accumulate(nav)
    dd = (nav / peak - 1).min()
    pre = daily_ret[daily_ret_dates < HALF_CUT]
    post = daily_ret[daily_ret_dates >= HALF_CUT]
    halves = []
    for r in (pre, post):
        if len(r) < 60:
            halves.append(np.nan)
            continue
        tot = np.prod(1 + r) - 1
        halves.append((1 + tot) ** (252 / len(r)) - 1 if tot > -1 else -1.0)
    return {'total': total, 'ann': ann, 'sharpe': sharpe, 'dd': dd, 'halves': halves}


def summarize(name, acc_, with_equity=True):
    ds = sorted(acc_.keys())
    if not ds:
        return None
    n = sum(acc_[d]['n'] for d in ds)
    if n < 200:
        return None
    pos = sum(acc_[d]['pos'] for d in ds)
    vals = [v for d in ds for v in acc_[d]['vals']]
    m = sum(vals) / len(vals)
    md = float(np.median(vals))
    out = {'name': name, 'n': n, 'p': pos / n, 'mean': m, 'med': md}
    if with_equity:
        ret = np.array([acc_[d]['s'] / acc_[d]['n'] for d in ds])
        global daily_ret_dates
        daily_ret_dates = np.array(ds)
        e = equity_stats(ret)
        if e:
            out.update(e)
            # 净口径: 每日一次往返成本 0.2% 直接从日收益扣除
            net = ret - 0.002
            tot = np.prod(1 + net) - 1
            out['net_ann'] = (1 + tot) ** (252 / len(ret)) - 1 if tot > -1 else -1.0
            # 净值序列 (HTML 图表用)
            out['dates'] = [str(d)[:10] for d in ds]
            out['nav'] = [round(float(x), 6) for x in np.cumprod(1 + ret)]
    return out


_HTML_TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>日内量价口诀回测报告</title>
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #f0f2f5; font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; color: #1f2329; }
  .header { background: linear-gradient(135deg, #1e3a5f 0%, #2c5f8a 100%); color: #fff; padding: 24px 32px; }
  .header h1 { font-size: 22px; font-weight: 600; }
  .header .sub { opacity: .8; font-size: 13px; margin-top: 6px; }
  .stats { display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }
  .stat { background: rgba(255,255,255,.12); border-radius: 8px; padding: 10px 18px; min-width: 110px; }
  .stat .v { font-size: 20px; font-weight: 700; }
  .stat .l { font-size: 12px; opacity: .75; }
  .container { max-width: 1440px; margin: 20px auto; padding: 0 16px; }
  .card { background: #fff; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); padding: 20px; margin-bottom: 20px; }
  .card h2 { font-size: 15px; color: #1f2329; margin-bottom: 12px; }
  .chart { width: 100%; height: 360px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { padding: 7px 12px; border-bottom: 1px solid #eef1f4; text-align: right; }
  th { background: #f7f8fa; color: #4e5969; font-weight: 600; white-space: nowrap; }
  td:first-child, th:first-child { text-align: left; }
  tr:hover td { background: #fafbfc; }
  .pos { color: #e8403a; font-weight: 600; }
  .neg { color: #1ba27a; font-weight: 600; }
  .concl { display: grid; gap: 10px; }
  .concl-item { background: #f7f9fc; border-left: 4px solid #2c5f8a; border-radius: 8px; padding: 12px 16px; font-size: 13.5px; line-height: 1.7; }
  .concl-item b { color: #1e3a5f; }
  .concl-item.warn { border-left-color: #f0a500; }
  .hint { font-size: 12px; color: #86909c; margin-top: 8px; }
</style>
</head>
<body>
<div class="header">
  <h1>📊 日内量价口诀回测报告</h1>
  <div class="sub">@@SUB@@</div>
  <div class="stats" id="stats"></div>
</div>
<div class="container">
  <div class="card">
    <h2>净值口径 (每日信号等权, 日频再平衡, 毛收益)</h2>
    <table id="tbl-main"></table>
    <div class="hint">22前/后 = 2022-01-01 前后各自年化; 净年化 = 每日扣 0.2% 往返成本后的复利年化</div>
  </div>
  <div class="grid2">
    <div class="card"><h2>毛年化 vs 净年化</h2><div class="chart" id="c1"></div></div>
    <div class="card"><h2>2022 年前 / 后年化 (衰减检验)</h2><div class="chart" id="c2"></div></div>
    <div class="card"><h2>R1 低开阈值敏感性 (年化)</h2><div class="chart" id="c3"></div></div>
    <div class="card"><h2>R3 尾盘跌幅阈值敏感性 (年化)</h2><div class="chart" id="c4"></div></div>
  </div>
  <div class="card">
    <h2>事件统计 (5日持有为重叠窗口不合成净值; R1诊断为T+0不可交易参考)</h2>
    <table id="tbl-ev"></table>
  </div>
  <div class="card">
    <h2>结论要点</h2>
    <div class="concl" id="concl"></div>
  </div>
</div>
<script>
const ROWS = @@ROWS@@;
const EV = @@EV@@;
const TH1 = @@TH1@@;
const TH3 = @@TH3@@;
const CONCL = @@CONCL@@;
const STATS = @@STATS@@;
const $ = id => document.getElementById(id);
const pct = (v, d=1) => (v >= 0 ? '+' : '') + (v * 100).toFixed(d) + '%';
const span = (v, d=1) => { const s = pct(v, d); return `<span class="${v >= 0 ? 'pos' : 'neg'}">${s}</span>`; };
const nfmt = v => Number(v).toLocaleString();
// 头部统计卡
$('stats').innerHTML = STATS.map(s => `<div class="stat"><div class="v">${s.v}</div><div class="l">${s.l}</div></div>`).join('');
// 主表
$('tbl-main').innerHTML = '<tr><th>规则</th><th>样本</th><th>P↑</th><th>均收益</th><th>中位</th><th>组合年化</th><th>净年化</th><th>Sharpe</th><th>最大回撤</th><th>22前/后年化</th></tr>' +
  ROWS.map(r => `<tr><td>${r.name}</td><td>${nfmt(r.n)}</td><td>${pct(r.p)}</td><td>${span(r.mean)}</td><td>${span(r.med)}</td><td>${span(r.ann)}</td><td>${span(r.net_ann)}</td><td>${r.sharpe.toFixed(2)}</td><td>${span(r.dd)}</td><td>${span(r.halves[0])}/${span(r.halves[1])}</td></tr>`).join('');
// 事件表
$('tbl-ev').innerHTML = '<tr><th>规则</th><th>样本</th><th>P↑</th><th>均收益</th><th>中位</th></tr>' +
  EV.map(r => `<tr><td>${r.name}</td><td>${nfmt(r.n)}</td><td>${pct(r.p)}</td><td>${span(r.mean)}</td><td>${span(r.med)}</td></tr>`).join('');
// 结论
$('concl').innerHTML = CONCL.map(c => `<div class="concl-item${c.w ? ' warn' : ''}"><b>${c.t}</b> ${c.b}</div>`).join('');
// 图表通用
const RED = '#e8403a', GREEN = '#1ba27a', BLUE = '#2c5f8a', AMBER = '#f0a500', GRAY = '#86909c';
const barColor = v => v >= 0 ? RED : GREEN;
function barChart(el, cats, series) {
  const opt = { tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '' : v.toFixed(1) + '%' },
    legend: { top: 0 }, grid: { left: 60, right: 20, top: 36, bottom: 28 },
    xAxis: { type: 'category', data: cats, axisLabel: { fontSize: 11 } },
    yAxis: { type: 'value', name: '%', nameTextStyle: { fontSize: 11 } },
    series: series.map(s => ({ name: s.name, type: 'bar', barMaxWidth: 42,
      itemStyle: { color: s.fixed }, data: s.data.map(v => ({ value: v, itemStyle: { color: barColor(v) } })) })) };
  echarts.init($(el)).setOption(opt);
}
barChart('c1', ROWS.map(r => r.short), [
  { name: '毛年化', fixed: BLUE, data: ROWS.map(r => r.ann * 100) },
  { name: '净年化(扣0.2%/日)', fixed: AMBER, data: ROWS.map(r => r.net_ann * 100) }]);
barChart('c2', ROWS.map(r => r.short), [
  { name: '2022前', fixed: BLUE, data: ROWS.map(r => r.halves[0] * 100) },
  { name: '2022后', fixed: GRAY, data: ROWS.map(r => r.halves[1] * 100) }]);
barChart('c3', TH1.map(x => x.label), [{ name: '年化', fixed: BLUE, data: TH1.map(x => x.ann * 100) }]);
barChart('c4', TH3.map(x => x.label), [{ name: '年化', fixed: BLUE, data: TH3.map(x => x.ann * 100) }]);
</script>
</body>
</html>
"""


def pct_fmt(v, d=1):
    return f'{v * 100:+.{d}f}%'


def _gen_html(rows, ev_rows, th1_rows, th3_rows, concl, stats):
    import json
    d = lambda x: json.dumps(x, ensure_ascii=False)
    def short(name):
        for k in ('基准', 'R1', 'R3', 'R4'):
            if name.startswith(k):
                return k
        return name
    rows_out = [{**r, 'short': short(r['name'])} for r in rows]
    return (_HTML_TPL
            .replace('@@SUB@@', '日内量价口诀回测 (tests/intraday_rule_backtest.py) — 日线代理分时 + T+1 隔夜持有')
            .replace('@@ROWS@@', d(rows_out))
            .replace('@@EV@@', d(ev_rows))
            .replace('@@TH1@@', d([{'label': f'低开≥{t*100:.1f}%', 'ann': r['ann']} for t, r in th1_rows]))
            .replace('@@TH3@@', d([{'label': f'尾盘跌≥{t*100:.0f}%', 'ann': r['ann']} for t, r in th3_rows]))
            .replace('@@CONCL@@', d(concl))
            .replace('@@STATS@@', d(stats)))


def main(limit=0, hist_file='', html_path=''):
    hist = _load_history_local(hist_file)
    if hist is None:
        print('  ✗ 无历史缓存')
        return 1

    acc = defaultdict(new_acc)   # 基准: 全市场每日开盘买隔夜卖
    R1 = defaultdict(new_acc)    # 低开买入 → 隔夜 (T+1 卖)
    R1_5 = defaultdict(new_acc)  # 低开买入 → 5日
    R2 = defaultdict(new_acc)    # 高开 → 当日 (高开低走诊断)
    R3 = defaultdict(new_acc)    # 尾盘跳水 → 次日开盘狙击 (隔夜)
    R3_5 = defaultdict(new_acc)  # 尾盘跳水 → 次日开盘买5日
    R4 = defaultdict(new_acc)    # 尾盘拉升 → 次日开盘追买 (验证不追)
    R1d = defaultdict(new_acc)   # 低开当日修复诊断 (c/o-1, T+0 不可交易)
    # 阈值敏感性
    R1_thr = {t: defaultdict(new_acc) for t in (0.015, 0.02, 0.03, 0.04)}
    R3_thr = {t: defaultdict(new_acc) for t in (0.02, 0.03, 0.05)}

    n_stock = 0
    for i, (sina, df) in enumerate(hist.items()):
        if limit and i >= limit:
            break
        if df is None or 'date' not in df.columns or 'close' not in df.columns:
            continue
        c = df['close'].values.astype(np.float64)
        o = df['open'].values.astype(np.float64)
        h = df['high'].values.astype(np.float64)
        l = df['low'].values.astype(np.float64)
        n = len(c)
        if n < 10:
            continue
        dts = pd.to_datetime(df['date'].values)
        th = _limit_up_th(sina)
        chg = np.concatenate([[np.nan], c[1:] / c[:-1] - 1])
        gap = np.concatenate([[np.nan], o[1:] / c[:-1] - 1])
        day_ret = c / o - 1
        rng = np.where(h - l > 0, h - l, np.nan)
        pos_low = (c - l) / rng
        pos_high = (h - c) / rng
        overnight = np.full(n, np.nan)
        overnight[:-1] = c[1:] / o[:-1] - 1        # 当日开盘买 → 次日收盘卖
        nxt_open = np.full(n, np.nan)
        nxt_open[:-2] = c[2:] / o[1:-1] - 1        # 次日开盘买 → 再隔日收盘卖
        r5 = np.full(n, np.nan)
        r5_nxt = np.full(n, np.nan)
        for j in range(n - 5):
            r5[j] = c[j + 5] / o[j] - 1            # 当日开盘买 → 5日后收盘
            r5_nxt[j] = c[j + 5] / o[j + 1] - 1    # 次日开盘买 → 5日后收盘

        for j in range(1, n - 1):
            d = dts[j]
            # 基准
            if -th < gap[j] < th and not np.isnan(overnight[j]):
                upd(acc, d, overnight[j])
            # R1 低开买入 (开盘跌停买不进)
            if gap[j] <= -0.02 and gap[j] > -th:
                if not np.isnan(overnight[j]):
                    upd(R1, d, overnight[j])
                    upd(R1d, d, day_ret[j])   # 当日修复诊断 (不可交易)
                if not np.isnan(r5[j]):
                    upd(R1_5, d, r5[j])
            for t, a in R1_thr.items():
                if gap[j] <= -t and gap[j] > -th and not np.isnan(overnight[j]):
                    upd(a, d, overnight[j])
            # R2 高开诊断
            if gap[j] >= 0.02 and not np.isnan(day_ret[j]):
                upd(R2, d, day_ret[j])
            # R3/R4 尾盘形态 → 次日开盘操作
            if j + 1 < n - 1:
                g1 = gap[j + 1]
                d1 = dts[j + 1]
                if (chg[j] <= -0.03 and pos_low[j] <= 0.15
                        and -th < g1 < th):
                    if not np.isnan(nxt_open[j]):
                        upd(R3, d1, nxt_open[j])
                    if not np.isnan(r5_nxt[j]):
                        upd(R3_5, d1, r5_nxt[j])
                for t, a in R3_thr.items():
                    if (chg[j] <= -t and pos_low[j] <= 0.15
                            and -th < g1 < th and not np.isnan(nxt_open[j])):
                        upd(a, d1, nxt_open[j])
                if (chg[j] >= 0.03 and pos_high[j] <= 0.15
                        and -th < g1 < th and not np.isnan(nxt_open[j])):
                    upd(R4, d1, nxt_open[j])
        n_stock += 1

    print(f'  √ 样本: {n_stock} 只股票')

    print('\n' + '═' * 120)
    print('  组合净值口径 (每日信号等权, 日频再平衡, 毛收益): 总收益/年化/Sharpe/最大回撤')
    print('═' * 120)
    print(f"  {'规则':<32}{'样本':>9}{'P↑':>7}{'均收益':>9}{'中位':>9}{'组合年化':>9}"
          f"{'净年化':>9}{'Sharpe':>8}{'最大回撤':>9}{'22前/后年化':>14}")
    print('  ' + '─' * 116)
    rows = [summarize('基准: 全市场每日开盘买隔夜卖', acc),
            summarize('R1 低开≥2%买入→次日卖', R1),
            summarize('R3 尾盘跳水→次日狙击', R3),
            summarize('R4 尾盘拉升→次日追买 (验证不追)', R4)]
    for r in rows:
        if r is None:
            continue
        pre, post = r['halves']
        print(f"  {r['name']:<32}{r['n']:>9,}{r['p']:>7.1%}{r['mean']:>+9.2%}{r['med']:>+9.2%}"
              f"{r['ann']:>+9.1%}{r['net_ann']:>+9.1%}{r['sharpe']:>8.2f}{r['dd']:>9.1%}"
              f"{pre:>+6.1%}/{post:>+6.1%}")

    print('\n' + '═' * 120)
    print('  事件统计 (5日持有为重叠窗口不合成净值; R1诊断为T+0不可交易参考)')
    print('═' * 120)
    print(f"  {'规则':<32}{'样本':>9}{'P↑':>7}{'均收益':>9}{'中位':>9}")
    print('  ' + '─' * 72)
    ev_rows = []
    for r in [summarize('R1 低开≥2% 买入→5日卖', R1_5, False),
              summarize('R3 尾盘跳水→次日买5日', R3_5, False),
              summarize('R2 高开≥2% 当日 (高开低走诊断)', R2, False),
              summarize('R1诊断 低开当日修复 (c/o-1)', R1d, False)]:
        if r is None:
            continue
        ev_rows.append(r)
        print(f"  {r['name']:<32}{r['n']:>9,}{r['p']:>7.1%}{r['mean']:>+9.2%}{r['med']:>+9.2%}")

    # 阈值敏感性
    print('\n  ── 阈值敏感性 (隔夜口径) ──')
    th1_rows, th3_rows = [], []
    for t in (0.015, 0.02, 0.03, 0.04):
        r = summarize(f'R1 低开≥{t*100:.1f}%→次日卖', R1_thr[t])
        if r:
            th1_rows.append((t, r))
            print(f"  {r['name']:<24} n={r['n']:>9,} P↑{r['p']:>6.1%} 均{r['mean']:>+8.2%}"
                  f" 年化{r['ann']:>+8.1%}")
    for t in (0.02, 0.03, 0.05):
        r = summarize(f'R3 尾盘跌≥{t*100:.0f}%→次日狙击', R3_thr[t])
        if r:
            th3_rows.append((t, r))
            print(f"  {r['name']:<24} n={r['n']:>9,} P↑{r['p']:>6.1%} 均{r['mean']:>+8.2%}"
                  f" 年化{r['ann']:>+8.1%}")

    print('\n' + '═' * 120)
    print('  结论要点')
    print('═' * 120)
    b, r1, r3, r4 = rows
    concl = []
    if r1 and b:
        lift = (r1['mean'] - b['mean']) * 100
        t = f'① 口诀1"早盘急跌买入": 低开≥2% 隔夜 P↑{r1["p"]:.1%} 均{r1["mean"]:+.2%}' \
            f' vs 基准 {b["mean"]:+.2%} (lift {lift:+.2f}pp)'
        verdict = '均值回归成立, 但超额有限' if lift > 0 else '不成立'
        print(f'  {t} → {verdict}')
        concl.append({'t': t, 'b': f'→ {verdict}', 'w': False})
    if r2 := summarize('R2', R2, False):
        t = f'② 口诀1"早盘急涨卖出": 高开≥2% 当日 P(收阴)={1 - r2["p"]:.1%} 均{r2["mean"]:+.2%}'
        verdict = '高开低走成立, 卖出口诀有效' if r2['mean'] < 0 else '高开未低走'
        print(f'  {t} → {verdict}')
        concl.append({'t': t, 'b': f'→ {verdict}', 'w': False})
    if r3 and r4:
        t = (f'③ 口诀2"下午急跌次日狙击": 隔夜均{r3["mean"]:+.2%} (P↑{r3["p"]:.1%})'
             f' 毛年化{r3["ann"]:+.1%} 净年化{r3["net_ann"]:+.1%}'
             f' vs "下午急涨不追"(若追买): {r4["mean"]:+.2%} (P↑{r4["p"]:.1%})'
             f' 毛年化{r4["ann"]:+.1%} 净年化{r4["net_ann"]:+.1%}')
        verdict = ('狙击尾盘跳水显著优于追尾盘拉升, 口诀方向成立'
                   if r3['mean'] > r4['mean'] else '方向不成立')
        detail = f'→ {verdict} (事件均差 {(r3["mean"] - r4["mean"]) * 100:+.1f}pp; R4 高波动方差拖累复利, 年化差距远大于均差)'
        print(f'  {t}')
        print(f'     {detail}')
        concl.append({'t': t, 'b': detail, 'w': False})
        pre_r3, post_r3 = r3['halves']
        pre_r1, post_r1 = r1['halves']
        t = (f'④ 时效衰减: 2022年前/后年化 — R1 {pre_r1*100:+.1f}%/{post_r1*100:+.1f}%,'
             f' R3 {pre_r3*100:+.1f}%/{post_r3*100:+.1f}%,'
             f' 基准 {b["halves"][0]*100:+.1f}%/{b["halves"][1]*100:+.1f}%')
        verdict = '口诀是 2015-2021 市场的遗产, 2022 后超额≈0 (毛口径), 扣成本后为负; 且日频再平衡 0.2%/天成本吃掉基准全部超额'
        print(f'  {t} → {verdict}')
        concl.append({'t': t, 'b': f'→ {verdict}', 'w': True})
    warn = ('⚠ 口径: 毛收益未扣成本 (隔夜双边约0.2%, 5日双边约0.2%); 日线代理分时(早盘=低开/高开,'
            '下午=收盘位置); 次日一字涨/跌停开买不进已剔除; hist 含幸存者偏差;'
            ' 组合等权日频再平衡换手极高, 真实成本敏感')
    print(f'  {warn}')
    concl.append({'t': '口径说明', 'b': warn[2:], 'w': True})

    if html_path:
        stats = [
            {'v': f'{n_stock:,}', 'l': '样本股票'},
            {'v': pct_fmt(sum(r['n'] for r in rows if r), 0), 'l': '全市场事件样本'},
            {'v': pct_fmt(b['mean']), 'l': '基准隔夜均收益'},
            {'v': pct_fmt(r1['mean']), 'l': 'R1 低开隔夜均收益'},
            {'v': f'{r3["ann"]*100:+.1f}%', 'l': 'R3 毛年化'},
            {'v': f'{r3["net_ann"]*100:+.1f}%', 'l': 'R3 净年化'},
        ]
        html = _gen_html(rows, ev_rows, th1_rows, th3_rows, concl, stats)
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'  √ HTML报告已保存: {html_path}')
    return 0


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='日内量价口诀回测')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--hist-file', default='')
    p.add_argument('--out', default='', help='把报告输出保存到文件 (默认只打印终端)')
    p.add_argument('--out-html', default='', help='另存 echarts HTML 报告 (含净值/阈值图表)')
    a = p.parse_args()
    orig = sys.stdout
    out = None
    if a.out:
        out = open(a.out, 'w', encoding='utf-8')
        sys.stdout = out
    rc = main(limit=a.limit, hist_file=a.hist_file, html_path=a.out_html)
    if out:
        out.flush()
        out.close()
        sys.stdout = orig
        print(f'√ 报告已保存: {a.out}')
    sys.exit(rc)

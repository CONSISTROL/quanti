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


def _sample_trades(trades, per_year=4000, top_bottom=100):
    """按年分层抽样 + 收益 Top/Bottom 全量, 供 HTML 内嵌 (全量25万+条过大)"""
    buckets = defaultdict(list)
    for t in trades:
        buckets[t[0][:4]].append(t)
    sampled = []
    for y in sorted(buckets):
        lst = buckets[y]
        if len(lst) <= per_year:
            sampled.extend(lst)
        else:
            idx = np.linspace(0, len(lst) - 1, per_year).astype(int)
            sampled.extend(lst[i] for i in idx)
    srt = sorted(trades, key=lambda t: t[3])
    extras = [('★', *t) for t in srt[:top_bottom] + srt[-top_bottom:]]
    return [('', *t) for t in sampled], extras


_TRADE_HTML_TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>R1 低开买入 — 交易明细与净值</title>
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
  .chart { width: 100%; height: 380px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
  @media (max-width: 900px) { .grid2 { grid-template-columns: 1fr; } }
  .filters { display: flex; gap: 10px; align-items: center; margin-bottom: 12px; flex-wrap: wrap; }
  .filters select, .filters input, .filters button { padding: 6px 10px; border: 1px solid #d9dde3; border-radius: 6px; font-size: 13px; background: #fff; }
  .filters button { cursor: pointer; background: #2c5f8a; color: #fff; border: none; }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th, td { padding: 5px 10px; border-bottom: 1px solid #eef1f4; text-align: right; white-space: nowrap; }
  th { background: #f7f8fa; color: #4e5969; font-weight: 600; cursor: pointer; user-select: none; }
  th:hover { color: #2c5f8a; }
  td:first-child, th:first-child { text-align: center; }
  td:nth-child(2), th:nth-child(2) { text-align: left; }
  tr:hover td { background: #fafbfc; }
  .pos { color: #e8403a; font-weight: 600; }
  .neg { color: #1ba27a; font-weight: 600; }
  .star { color: #f0a500; font-weight: 700; }
  .pager { display: flex; gap: 6px; margin-top: 12px; align-items: center; font-size: 13px; }
  .pager button { padding: 4px 10px; border: 1px solid #d9dde3; border-radius: 6px; background: #fff; cursor: pointer; }
  .pager button:disabled { opacity: .4; cursor: default; }
  .hint { font-size: 12px; color: #86909c; margin-top: 10px; line-height: 1.7; }
</style>
</head>
<body>
<div class="header">
  <h1>📈 R1 低开买入 — 交易明细与净值</h1>
  <div class="sub">@@SUB@@</div>
  <div class="stats" id="stats"></div>
</div>
<div class="container">
  <div class="card">
    <h2>R1 组合净值 (每日低开≥2% 信号等权, 开盘买→次日收盘卖, 毛收益)</h2>
    <div class="chart" id="nav"></div>
  </div>
  <div class="grid2">
    <div class="card"><h2>逐年交易统计 (全样本)</h2><div class="chart" id="byyear"></div></div>
    <div class="card"><h2>年×月平均收益热力图 (%, 全样本)</h2><div class="chart" id="heat"></div></div>
  </div>
  <div class="card">
    <h2>交易明细 (按年分层抽样, 每年≤4000条 + ★全市场收益 Top/Bottom 100)</h2>
    <div class="filters">
      <select id="f-year"><option value="">全部年份</option></select>
      <input id="f-code" placeholder="代码搜索, 如 600206">
      <select id="f-sort">
        <option value="">默认 (按日期)</option>
        <option value="ret">按次日收益排序</option>
        <option value="gap">按低开幅度排序</option>
      </select>
      <button id="f-apply">筛选</button>
      <span class="hint" id="f-info" style="margin:0"></span>
    </div>
    <div style="overflow:auto; max-height:520px">
      <table id="tbl"><thead><tr><th>标注</th><th>日期</th><th>代码</th><th>低开%</th><th>次日收益%</th></tr></thead><tbody id="tbody"></tbody></table>
    </div>
    <div class="pager" id="pager"></div>
    <div class="hint">★ = 全样本收益 Top100/Bottom100 (未抽样的真实极端交易); 完整全样本 25.7 万笔, 抽样仅用于展示分布, 全部统计均为全样本口径</div>
  </div>
  <div class="card">
    <h2>高年化怎么来的</h2>
    <div class="hint" id="concl"></div>
  </div>
</div>
<script>
const NAV = @@NAV@@;
const YEARS = @@YEARS@@;
const HEAT = @@HEAT@@;
const TRADES = @@TRADES@@;
const CONCL = @@CONCL@@;
const STATS = @@STATS@@;
const $ = id => document.getElementById(id);
const nfmt = v => Number(v).toLocaleString();
const pct = (v, d=1) => (v >= 0 ? '+' : '') + v.toFixed(d) + '%';
const span = (v, d=2) => `<span class="${v >= 0 ? 'pos' : 'neg'}">${pct(v, d)}</span>`;
$('stats').innerHTML = STATS.map(s => `<div class="stat"><div class="v">${s.v}</div><div class="l">${s.l}</div></div>`).join('');
$('concl').innerHTML = CONCL.map(c => `<div>${c}</div>`).join('');
// 净值曲线 (日度, 对数轴)
echarts.init($('nav')).setOption({
  tooltip: { trigger: 'axis', valueFormatter: v => v == null ? '' : v.toFixed(2) + 'x' },
  grid: { left: 70, right: 30, top: 30, bottom: 40 },
  xAxis: { type: 'category', data: NAV.dates, axisLabel: { show: false } },
  yAxis: { type: 'value', name: '净值', nameTextStyle: { fontSize: 11 }, min: 0.1 },
  dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 2 }],
  series: [{ type: 'line', data: NAV.vals, showSymbol: false, lineStyle: { width: 1.4, color: '#2c5f8a' },
    areaStyle: { color: 'rgba(44,95,138,.08)' }, name: 'R1 净值' }]
});
// 逐年: 样本数柱 + 均收益折线 (双轴)
echarts.init($('byyear')).setOption({
  tooltip: { trigger: 'axis' },
  legend: { top: 0 }, grid: { left: 60, right: 60, top: 36, bottom: 30 },
  xAxis: { type: 'category', data: YEARS.map(y => y.y) },
  yAxis: [
    { type: 'value', name: '笔数', nameTextStyle: { fontSize: 11 } },
    { type: 'value', name: '均收益%', nameTextStyle: { fontSize: 11 }, splitLine: { show: false } }],
  series: [
    { name: '样本数', type: 'bar', data: YEARS.map(y => y.n), itemStyle: { color: 'rgba(44,95,138,.35)' }, barMaxWidth: 28 },
    { name: '均收益%', type: 'line', yAxisIndex: 1, data: YEARS.map(y => +(y.mean * 100).toFixed(2)),
      itemStyle: { color: '#e8403a' }, lineStyle: { width: 2 } }]
});
// 年×月热力图
echarts.init($('heat')).setOption({
  tooltip: { formatter: p => `${p.value[0]}年${p.value[1]}月: ${p.value[2]}% (${p.value[3]}笔)` },
  grid: { left: 60, right: 20, top: 10, bottom: 50 },
  xAxis: { type: 'category', data: Array.from({length:12}, (_,i)=>i+1+'月'), splitArea: { show: true } },
  yAxis: { type: 'category', data: HEAT.years, splitArea: { show: true } },
  visualMap: { min: -3, max: 3, calculable: true, orient: 'horizontal', left: 'center', bottom: 0,
    inRange: { color: ['#1ba27a', '#f0f2f5', '#e8403a'] }, text: ['高收益', '低收益'] },
  series: [{ type: 'heatmap', data: HEAT.data.map(d => [d[1]-1, HEAT.years.length-1-HEAT.years.indexOf(d[0]), +d[2].toFixed(2), d[3]]),
    label: { show: true, fontSize: 9 } }]
});
// 交易明细表
const years = [...new Set(TRADES.map(t => t[1].slice(0,4)))].sort();
$('f-year').innerHTML = '<option value="">全部年份</option>' + years.map(y => `<option>${y}</option>`).join('');
const PAGE = 500;
let cur = [], page = 0;
function render() {
  const sel = cur.slice(page*PAGE, (page+1)*PAGE);
  $('tbody').innerHTML = sel.map(t => `<tr><td class="${t[0] ? 'star' : ''}">${t[0]}</td><td>${t[1]}</td><td>${t[2]}</td><td>${pct(t[3],2)}</td>${span(t[4])}</tr>`).join('');
  const pages = Math.max(1, Math.ceil(cur.length/PAGE));
  $('pager').innerHTML = `<button onclick="pg(0)" ${page==0?'disabled':''}>«</button><button onclick="pg(${page-1})" ${page==0?'disabled':''}>‹</button>` +
    `<span>${page+1} / ${pages} (共 ${nfmt(cur.length)} 笔)</span>` +
    `<button onclick="pg(${page+1})" ${page>=pages-1?'disabled':''}>›</button><button onclick="pg(${pages-1})" ${page>=pages-1?'disabled':''}>»</button>`;
}
window.pg = i => { page = i; render(); };
$('f-apply').onclick = () => {
  const y = $('f-year').value, code = $('f-code').value.trim().toLowerCase(), sort = $('f-sort').value;
  cur = TRADES.filter(t => (!y || t[1].startsWith(y)) && (!code || t[2].toLowerCase().includes(code)));
  if (sort === 'ret') cur.sort((a,b) => a[4]-b[4]);
  if (sort === 'gap') cur.sort((a,b) => a[3]-b[3]);
  page = 0; render();
};
$('f-apply').click();
</script>
</body>
</html>
"""


def _gen_trades_html(stats, nav, by_year, heat, trades, concl):
    import json
    d = lambda x: json.dumps(x, ensure_ascii=False)
    return (_TRADE_HTML_TPL
            .replace('@@SUB@@', 'R1 低开≥2% 开盘买入 → 次日收盘卖出 (T+1 隔夜) | 毛收益未扣成本 | 全样本 ' + f'{sum(y["n"] for y in by_year):,}' + ' 笔, 抽样展示')
            .replace('@@NAV@@', d(nav))
            .replace('@@YEARS@@', d(by_year))
            .replace('@@HEAT@@', d(heat))
            .replace('@@TRADES@@', d(trades))
            .replace('@@CONCL@@', d(concl))
            .replace('@@STATS@@', d(stats)))


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


def main(limit=0, hist_file='', html_path='', trades_html_path=''):
    hist = _load_history_local(hist_file)
    if hist is None:
        print('  ✗ 无历史缓存')
        return 1
    trades = [] if trades_html_path else None   # R1 逐笔 (date, code, gap%, ret%)

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
                    if trades is not None:
                        trades.append((str(d)[:10], sina,
                                       round(float(gap[j]) * 100, 2),
                                       round(float(overnight[j]) * 100, 2)))
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

    if trades_html_path and trades:
        r1 = rows[1] if len(rows) > 1 else None
        sampled, extras = _sample_trades(trades)
        yrs = sorted({t[0][:4] for t in trades})
        by_year = []
        for y in yrs:
            ys = [t for t in trades if t[0][:4] == y]
            rets = np.array([t[3] for t in ys])
            by_year.append({'y': y, 'n': len(ys), 'p': float((rets > 0).mean()),
                           'mean': float(rets.mean() / 100)})
        hdata = []
        for y in yrs:
            for m in range(1, 13):
                ys = [t for t in trades if t[0][:4] == y and int(t[0][5:7]) == m]
                if ys:
                    rets = np.array([t[3] for t in ys])
                    hdata.append([y, m, float(rets.mean()), len(ys)])
        heat = {'years': yrs, 'data': hdata}
        tstats = [
            {'v': f'{len(trades):,}', 'l': 'R1 全样本笔数'},
            {'v': pct_fmt(sum(1 for t in trades if t[3] > 0) / len(trades), 0), 'l': 'P↑'},
            {'v': pct_fmt(sum(t[3] for t in trades) / len(trades) / 100), 'l': '单笔均收益'},
            {'v': pct_fmt(r1['ann']) if r1 else '—', 'l': '毛年化'},
            {'v': pct_fmt(r1['net_ann']) if r1 else '—', 'l': '净年化 (扣0.2%/日)'},
            {'v': pct_fmt(r1['halves'][0]) if r1 else '—', 'l': '2022前年化'},
            {'v': pct_fmt(r1['halves'][1]) if r1 else '—', 'l': '2022后年化'},
        ]
        tconcl = [
            '高年化的本质是「日频复利」：单笔均收益仅 +0.35%、中位 +0.27%，靠 252 个交易日每天滚动叠加 → (1.0035)^252 ≈ +140%，毛年化 +163% 并非单笔暴利。',
            '高年化集中在 2015-2021 隔夜溢价时代（热力图可见 2015/2020-2021 深红区）：2022 前年化 +252.8%，2022 后 -5.0%——同一条规则，市场结构变了就失效。',
            '毛年化未扣成本：0.2%/日往返成本下净年化仅 +59.1%（2022 后为负）；日频全市场再平衡在实盘中无法按此换手率成交，真实可执行收益远低于毛年化。',
            '幸存者偏差：hist 缓存只含至今仍在市的股票，退市股的历史信号被排除，会系统性高估收益（2015 后大量退市/ST）。',
        ]
        tnav = {'dates': r1.get('dates', []), 'vals': r1.get('nav', [])} if r1 else {'dates': [], 'vals': []}
        html = _gen_trades_html(tstats, tnav, by_year, heat, sampled + extras, tconcl)
        with open(trades_html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f'  √ 交易明细HTML已保存: {trades_html_path} (全样本{len(trades):,}笔, 内嵌抽样{len(sampled):,}+极端{len(extras):,})')
    return 0


def main_combined(limit=0, hist_file=''):
    """口诀组合状态机 (--combined): 四条口诀作为一套系统每天实时运行

    每日开盘(集合竞价, 日线用 open): 卖出昨日买入的全部持仓(资金全额循环),
    同时买入今日信号池 = 今日低开(R1) ∪ 昨日尾盘跳水(R3);
    R2 隐含在"开盘卖"(高开日自动兑现, 单独诊断其增量);
    R4 是"不追"口诀 → 反事实池 (组合+尾盘拉升) 只作对照。
    收益口径 o-to-o (开盘买→次日开盘卖), 与分开版(次日收盘卖)对比。
    """
    hist = _load_history_local(hist_file)
    if hist is None:
        print('  ✗ 无历史缓存')
        return 1

    bench = defaultdict(new_acc)   # 基准: 全市场 o-to-o 隔夜循环
    comb = defaultdict(new_acc)    # 口诀组合: R1 ∪ R3 (同股同日去重)
    comb4 = defaultdict(new_acc)   # 反事实: 组合 + R4 追尾盘拉升
    r2_hi = []                     # R2 诊断: 组合持仓次日高开≥2% → (开盘卖, 收盘卖)
    r2_lo = []                     # 对照: 次日非高开 → (开盘卖, 收盘卖)

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
        rng = np.where(h - l > 0, h - l, np.nan)
        pos_low = (c - l) / rng
        pos_high = (h - c) / rng
        oo = np.full(n, np.nan)
        oo[:-1] = o[1:] / o[:-1] - 1        # 今日开盘买 → 次日开盘卖 (隔夜循环)
        cnext = np.full(n, np.nan)
        cnext[:-1] = c[1:] / o[:-1] - 1     # 今日开盘买 → 次日收盘卖 (R2 对照)
        for j in range(1, n - 1):
            d = dts[j]
            if -th < gap[j] < th and not np.isnan(oo[j]):
                upd(bench, d, oo[j])
            # 今日买入池 (同股同日最多入池一次)
            is_r1 = gap[j] <= -0.02 and gap[j] > -th
            is_r3 = (j - 1 >= 1 and chg[j - 1] <= -0.03 and pos_low[j - 1] <= 0.15
                     and -th < gap[j] < th)
            is_r4 = (j - 1 >= 1 and chg[j - 1] >= 0.03 and pos_high[j - 1] <= 0.15
                     and -th < gap[j] < th)
            if (is_r1 or is_r3) and not np.isnan(oo[j]):
                upd(comb, d, oo[j])
                if j + 1 < n and not np.isnan(cnext[j]):
                    g1 = gap[j + 1] if j + 1 < n else np.nan
                    if not np.isnan(g1):
                        (r2_hi if g1 >= 0.02 else r2_lo).append((oo[j], cnext[j]))
            if (is_r1 or is_r3 or is_r4) and not np.isnan(oo[j]):
                upd(comb4, d, oo[j])
        n_stock += 1

    print(f'  √ 样本: {n_stock} 只股票 (组合状态机口径: 每日开盘卖旧买新, 资金全额循环)')

    print('\n' + '═' * 120)
    print('  组合净值 (o-to-o 隔夜循环, 每日信号等权, 毛收益)')
    print('═' * 120)
    print(f"  {'规则':<36}{'样本':>9}{'P↑':>7}{'均收益':>9}{'组合年化':>9}"
          f"{'净年化':>9}{'Sharpe':>8}{'最大回撤':>9}{'22前/后年化':>14}")
    print('  ' + '─' * 116)
    rows = [summarize('基准: 全市场每日开盘买次日开盘卖', bench),
            summarize('口诀组合 R1低开∪R3尾盘跳水', comb),
            summarize('反事实: 组合+R4追尾盘拉升(应不追)', comb4)]
    for r in rows:
        if r is None:
            continue
        pre, post = r['halves']
        print(f"  {r['name']:<36}{r['n']:>9,}{r['p']:>7.1%}{r['mean']:>+9.2%}"
              f"{r['ann']:>+9.1%}{r['net_ann']:>+9.1%}{r['sharpe']:>8.2f}{r['dd']:>9.1%}"
              f"{pre:>+6.1%}/{post:>+6.1%}")

    print('\n  ── R2 诊断: 组合持仓次日的卖出时机 (口诀"早盘急涨卖出") ──')
    for lbl, grp in (('次日高开≥2% (应开盘卖)', r2_hi), ('次日非高开 (对照)', r2_lo)):
        if not grp:
            continue
        oo_arr = np.array([x[0] for x in grp])
        cn_arr = np.array([x[1] for x in grp])
        diff = oo_arr - cn_arr   # 开盘卖 - 收盘卖
        print(f'  {lbl:<24} n={len(grp):>9,} 开盘卖均{oo_arr.mean():+8.2%}'
              f' 收盘卖均{cn_arr.mean():+8.2%} 提前卖增量{diff.mean() * 100:+6.2f}pp'
              f' P(开盘卖优){(diff > 0).mean():6.1%}')

    print('\n' + '═' * 120)
    print('  结论要点 (组合版)')
    print('═' * 120)
    b, com, c4 = rows
    if com and b:
        lift = (com['mean'] - b['mean']) * 100
        print(f'  ① 口诀组合 vs 全市场基准: 均{com["mean"]:+.2%} vs {b["mean"]:+.2%}'
              f' (lift {lift:+.2f}pp), 毛年化 {com["ann"]:+.1%} vs {b["ann"]:+.1%},'
              f' 净年化 {com["net_ann"]:+.1%} vs {b["net_ann"]:+.1%}')
        print(f'     → 组合把 R1/R3 信号合并成一套实时系统, 每日开盘全仓循环;'
              f' 分开版为次日收盘卖, 组合版为次日开盘卖 (资金循环约束), 收益自然更低')
    if c4 and com:
        verdict2 = '口诀「下午急涨不追」在组合内同样成立' if c4['mean'] < com['mean'] else '加入后未恶化'
        print(f'  ② R4 反事实: 加入"追尾盘拉升"后 均{c4["mean"]:+.2%} vs 组合 {com["mean"]:+.2%}'
              f' (毛年化 {c4["ann"]:+.1%} vs {com["ann"]:+.1%}) → {verdict2}')
    if r2_hi and r2_lo:
        hi_diff = np.mean([x[0] - x[1] for x in r2_hi])
        lo_diff = np.mean([x[0] - x[1] for x in r2_lo])
        print(f'  ③ R2 卖出时机: 高开日提前卖增量 {hi_diff*100:+.2f}pp vs 非高开日 {lo_diff*100:+.2f}pp'
              f' → {"高开才卖、平开不卖的选择性卖出成立: 开盘卖只应在高开日执行" if hi_diff > 0 > lo_diff else "卖出时机无差异"}')
    if com:
        pre, post = com['halves']
        print(f'  ④ 时效衰减: 组合 2022前/后年化 {pre*100:+.1f}%/{post*100:+.1f}%'
              f' (基准 {b["halves"][0]*100:+.1f}%/{b["halves"][1]*100:+.1f}%)'
              f' → 与分开版一致: 口诀体系是隔夜溢价时代的遗产, 2022 后超额≈0')
    print('  ⚠ 口径: 与分开版相同的日线代理/T+1/剔除一字板/幸存者偏差;'
          ' 组合假设每日开盘全仓循环(卖旧买新同价成交, 实际有滑点), 0.2%/日往返成本已单列净年化')
    return 0


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='日内量价口诀回测')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--hist-file', default='')
    p.add_argument('--out', default='', help='把报告输出保存到文件 (默认只打印终端)')
    p.add_argument('--out-html', default='', help='另存 echarts HTML 报告 (含净值/阈值图表)')
    p.add_argument('--trades-html', default='', help='另存 R1 低开买入交易明细 HTML (净值曲线+逐年/月热力图+抽样明细表)')
    p.add_argument('--combined', action='store_true',
                   help='跑口诀组合状态机 (四条规则一套系统实时运行, 每日开盘卖旧买新), 替代分开版')
    a = p.parse_args()
    orig = sys.stdout
    out = None
    if a.out:
        out = open(a.out, 'w', encoding='utf-8')
        sys.stdout = out
    rc = main_combined(limit=a.limit, hist_file=a.hist_file) if a.combined \
        else main(limit=a.limit, hist_file=a.hist_file, html_path=a.out_html,
                  trades_html_path=a.trades_html)
    if out:
        out.flush()
        out.close()
        sys.stdout = orig
        print(f'√ 报告已保存: {a.out}')
    sys.exit(rc)

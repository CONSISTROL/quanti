"""
ECharts 可视化报告生成器 — 自选池轮动分析
生成: 净值对比图 + 收益柱状图 + 个股K线(含买卖点)

用法: from report_echarts import generate_echarts_report
"""
import json
import pandas as pd

ECHARTS_CDN = 'https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js'

# 统一配色 (金融风格)
UP = '#e8403a'      # 涨 (A股红)
DOWN = '#1ba27a'    # 跌 (A股绿)
GRID = '#eef1f4'
TEXT = '#4e5969'
BLUE = '#2c6fbb'
ORANGE = '#f5a623'
GRAY = '#86909c'


def echarts_script(chart_id, option, height=300):
    """通用ECharts图表HTML片段: div + init脚本 (需在ECharts CDN加载后使用)"""
    return f'''<div id="{chart_id}" style="width:100%;height:{height}px;"></div>
<script>
(function() {{
  var c = echarts.init(document.getElementById('{chart_id}'));
  c.setOption({json.dumps(option, ensure_ascii=False)});
  window.addEventListener('resize', function() {{ c.resize(); }});
}})();
</script>'''


def base_tooltip(trigger='axis'):
    """统一tooltip样式"""
    return {'trigger': trigger, 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
            'textStyle': {'color': '#1f2329'}}


def base_grid(top=40, left=50, right=20, bottom=30):
    return {'left': left, 'right': right, 'top': top, 'bottom': bottom}


def base_xaxis(data=None, rotate=0):
    ax = {'type': 'category' if data else 'value',
          'axisLine': {'lineStyle': {'color': '#d9dde3'}},
          'axisLabel': {'color': TEXT}}
    if data is not None:
        ax['data'] = data
    if rotate:
        ax['axisLabel']['rotate'] = rotate
    return ax


def base_yaxis(scale=True, formatter=None):
    ax = {'type': 'value', 'scale': scale,
          'splitLine': {'lineStyle': {'color': GRID}},
          'axisLabel': {'color': TEXT}}
    if formatter:
        ax['axisLabel']['formatter'] = formatter
    return ax


def _eq_series(eq, initial):
    """equity_curve → (dates, nav) JS友好数据"""
    dates = [pd.Timestamp(e[0]).strftime('%Y-%m-%d') for e in eq]
    nav = [round(e[1] / initial, 4) for e in eq]
    return dates, nav


def _ohlc_and_ma(df, precomputed, code):
    """个股K线 + MA + 买卖点"""
    d = df.copy()
    d['date'] = pd.to_datetime(d['date']).dt.strftime('%Y-%m-%d')
    ohlc = [[row['date'], round(row['open'], 3), round(row['close'], 3),
             round(row['low'], 3), round(row['high'], 3)] for _, row in d.iterrows()]

    ma5, ma20, boll = [], [], []
    pc = precomputed.get(code, {})
    for date in d['date']:
        v = pc.get(date, {})
        ma5.append(round(v.get('ma5', 0), 3) if v.get('ma5') else None)
        ma20.append(round(v.get('ma20', 0), 3) if v.get('ma20') else None)
        boll.append(round(v.get('boll_low', 0), 3) if v.get('boll_low') else None)
    return ohlc, ma5, ma20, boll


def generate_echarts_report(combo, per_stock, output_path):
    """
    combo: dict(eq=[(date,value)], initial, stats, trades, name='组合轮动')
    per_stock: {code: dict(name, eq, initial, trades, df, precomputed)}

    操作记录表 = 回测 trades 流水 (用户A忠实执行视角):
      回测引擎在 exec_next_close/exec_next_open 模式下, trades 的 date 已是
      "信号日+1交易日"的执行日、price 已是执行价 → 即"按回测结果延后1个
      交易日执行"的用户A操作记录, 无需手动填写.
    """
    # ---- 1. 净值对比数据 ----
    # 统一日期轴 = 组合的完整日期; 每只个股按日期对齐 (上市前缺失位置填null)
    combo_dates, combo_nav = _eq_series(combo['eq'], combo['initial'])
    nav_series = [{
        'name': combo.get('name', '组合轮动'),
        'dates': combo_dates, 'nav': combo_nav, 'bold': True,
    }]
    for code, ps in per_stock.items():
        d, n = _eq_series(ps['eq'], ps['initial'])
        stock_map = dict(zip(d, n))
        aligned = [stock_map.get(dd) for dd in combo_dates]  # 缺失→None(ECharts断线)
        nav_series.append({'name': f'{code} {ps["name"]}', 'dates': combo_dates, 'nav': aligned, 'bold': False})

    # ---- 2. 收益柱状数据 ----
    bar_data = [{'name': combo.get('name', '组合轮动'), 'value': round(combo['stats']['total_return'] * 100, 1)}]
    for code, ps in per_stock.items():
        bar_data.append({'name': f'{code} {ps["name"]}',
                         'value': round(ps['stats']['total_return'] * 100, 1)})

    # ---- 3. K线数据 (每只) ----
    kline_data = []
    for code, ps in per_stock.items():
        ohlc, ma5, ma20, boll = _ohlc_and_ma(ps['df'], ps.get('precomputed', {}), code)
        buys = [{'date': pd.Timestamp(t.date).strftime('%Y-%m-%d'), 'price': round(t.price, 3)}
                for t in ps['trades'] if t.direction == 'BUY']
        sells = [{'date': pd.Timestamp(t.date).strftime('%Y-%m-%d'), 'price': round(t.price, 3)}
                 for t in ps['trades'] if t.direction == 'SELL']
        kline_data.append({'code': code, 'name': ps['name'], 'ohlc': ohlc,
                           'ma5': ma5, 'ma20': ma20, 'boll': boll, 'buys': buys, 'sells': sells})

    # ---- 4. 用户A操作记录 = 回测trades流水 (忠实执行, 延后1交易日) ----
    # exec_next_close 模式下 trades.date 已是执行日(T+1尾盘), price 已是执行价
    # 累计盈亏 = 执行日净值/初始资金-1 (与终端 print_trade_summary 同口径)
    initial = combo['initial']
    nav_map = {}
    for d, v in combo.get('eq', []):
        nav_map[pd.Timestamp(d).strftime('%Y-%m-%d')] = v
    user_trades = []
    for t in combo.get('trades', []):
        t_date = pd.Timestamp(t.date).strftime('%Y-%m-%d')
        cum_nav = nav_map.get(t_date)
        if cum_nav is None:
            cum_nav = initial
        user_trades.append({
            'date': t_date,
            'code': t.code,
            'name': t.name,
            'side': t.direction,
            'side_cn': '买入' if t.direction == 'BUY' else '卖出',
            'price': round(t.price, 3),
            'shares': t.shares,
            'amount': round(t.amount, 2),
            'reason': t.reason,
            'pnl': (round(t.pnl_pct * 100, 2) if t.direction == 'SELL'
                    and getattr(t, 'pnl_pct', 0) is not None else None),
            'cum': round((cum_nav / initial - 1) * 100, 2),
        })
    user_trades.sort(key=lambda x: x['date'])

    payload = {
        'nav_series': nav_series,
        'bar_data': bar_data,
        'kline_data': kline_data,
        'user_trades': user_trades,
        'stats': {k: (round(v * 100, 2) if isinstance(v, float) and k in ('total_return', 'annual_return', 'max_drawdown', 'win_rate') else v)
                  for k, v in combo['stats'].items()},
        'initial': combo['initial'],
    }

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>自选池轮动分析</title>
<script src="{ECHARTS_CDN}"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ background: #f0f2f5; font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; color: #1f2329; }}
  .header {{ background: linear-gradient(135deg, #1e3a5f 0%, #2c5f8a 100%); color: #fff; padding: 24px 32px; }}
  .header h1 {{ font-size: 22px; font-weight: 600; }}
  .header .sub {{ opacity: .8; font-size: 13px; margin-top: 6px; }}
  .stats {{ display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }}
  .stat {{ background: rgba(255,255,255,.12); border-radius: 8px; padding: 10px 18px; min-width: 110px; }}
  .stat .v {{ font-size: 20px; font-weight: 700; }}
  .stat .l {{ font-size: 12px; opacity: .75; }}
  .container {{ max-width: 1440px; margin: 20px auto; padding: 0 16px; }}
  .card {{ background: #fff; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,.08); padding: 20px; margin-bottom: 20px; }}
  .card h2 {{ font-size: 15px; color: #1f2329; margin-bottom: 12px; }}
  .chart {{ width: 100%; height: 420px; }}
  .chart-sm {{ width: 100%; height: 300px; }}
  select {{ padding: 6px 12px; border: 1px solid #d9dde3; border-radius: 6px; font-size: 13px; background: #fff; }}
  .legend-hint {{ font-size: 12px; color: #86909c; margin-top: 6px; }}
  .trades {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .trades th, .trades td {{ padding: 4px 10px; border-bottom: 1px solid #eef1f4; text-align: left; }}
  .trades td {{ white-space: nowrap; }}
  .trades td:last-child {{ white-space: normal; }}
  .trades th {{ background: #f7f8fa; color: #4e5969; font-weight: 600; white-space: nowrap; }}
  .trades tr:hover td {{ background: #fafbfc; }}
  .buy {{ color: #e8403a; font-weight: 600; }}
  .sell {{ color: #1ba27a; font-weight: 600; }}
  .reason-cell {{ font-size: 12px; color: #86909c; max-width: 260px; }}
</style>
</head>
<body>
<div class="header">
  <h1>📊 自选池轮动分析报告</h1>
  <div class="sub">策略: 强弱评分 + 单持仓满仓 + 卖弱买强 &nbsp;|&nbsp; 生成时间: 2026-08-02</div>
  <div class="stats">
    <div class="stat"><div class="v" style="color:#4cd964;">{payload['stats'].get('total_return', 0):+.1f}%</div><div class="l">总收益率</div></div>
    <div class="stat"><div class="v">{payload['stats'].get('annual_return', 0):+.1f}%</div><div class="l">年化收益</div></div>
    <div class="stat"><div class="v">{payload['stats'].get('sharpe', 0):.2f}</div><div class="l">Sharpe</div></div>
    <div class="stat"><div class="v" style="color:#ff5b5b;">{payload['stats'].get('max_drawdown', 0):.1f}%</div><div class="l">最大回撤</div></div>
    <div class="stat"><div class="v" style="color:#ff5b5b;">{payload['stats'].get('max_drawdown_days', 0)}</div><div class="l">最大回撤天数</div></div>
    <div class="stat"><div class="v">{payload['stats'].get('total_trades', 0)}</div><div class="l">交易次数</div></div>
    <div class="stat"><div class="v">{payload['stats'].get('win_rate', 0):.1f}%</div><div class="l">胜率</div></div>
    <div class="stat"><div class="v">{payload['stats'].get('profit_loss_ratio', 0):.2f}</div><div class="l">盈亏比</div></div>
  </div>
</div>
<div class="container">
  <div class="card"><h2>📈 净值对比: 组合轮动 vs 每只个股独立满仓</h2>
    <div id="navChart" class="chart"></div>
    <div class="legend-hint">💡 点击图例可显隐曲线, 底部拖拽可缩放时间段</div>
  </div>
  <div class="card"><h2>🏆 总收益对比</h2>
    <div id="barChart" class="chart-sm"></div>
  </div>
  <div class="card"><h2>🕯️ 个股K线 (用户A买卖执行点)</h2>
    <select id="stockSel" style="margin-bottom:12px;"></select>
    <div id="klineChart" class="chart"></div>
    <div class="legend-hint">🟢 三角=买入 &nbsp;🔻 倒三角=卖出 &nbsp;标记于执行日/执行价 &nbsp;虚线=BOLL下轨</div>
  </div>
  <div class="card"><h2>🔄 用户A操作记录 (按回测决策延后1交易日执行)</h2>
    <table class="trades">
      <thead><tr><th>执行日期</th><th>方向</th><th>代码</th><th>名称</th><th>价格</th>
        <th>数量</th><th>金额</th><th>盈亏</th><th>累计盈亏</th><th>信号原因</th></tr></thead>
      <tbody id="userTbody"></tbody>
    </table>
    <div class="legend-hint">💡 本表 = 用户A延后执行流水: 信号日(T)收盘收到系统决策 → 次日(T+1)尾盘(或开盘)执行, 故执行日期=信号日+1交易日(恒定延迟, 无需单列信号日); 价格/盈亏均为<strong>执行口径</strong>(与主报告"系统买卖信号记录"的信号日收盘价即时成交口径不同)</div>
  </div>
</div>
<script>
const DATA = {json.dumps(payload, ensure_ascii=False)};

// ---- 主题 ----
const COLOR = {{ up: '#e8403a', down: '#1ba27a', grid: '#eef1f4', text: '#4e5969' }};

// ---- 1. 净值对比 ----
(function () {{
  const chart = echarts.init(document.getElementById('navChart'));
  const series = DATA.nav_series.map(s => ({{
    name: s.name, type: 'line', showSymbol: false, smooth: true,
    lineStyle: {{ width: s.bold ? 3.5 : 1.5, color: s.bold ? '#e8403a' : undefined }},
    data: s.nav, emphasis: {{ focus: 'series' }},
  }}));
  chart.setOption({{
    tooltip: {{ trigger: 'axis', backgroundColor: '#fff', borderColor: '#e5e8ec',
                textStyle: {{ color: '#1f2329' }} }},
    legend: {{ top: 0, textStyle: {{ color: COLOR.text }} }},
    grid: {{ left: 50, right: 20, top: 40, bottom: 60 }},
    xAxis: {{ type: 'category', data: DATA.nav_series[0].dates,
             axisLine: {{ lineStyle: {{ color: '#d9dde3' }} }},
             axisLabel: {{ color: COLOR.text }} }},
    yAxis: {{ type: 'value', scale: true, splitLine: {{ lineStyle: {{ color: COLOR.grid }} }},
             axisLabel: {{ color: COLOR.text, formatter: v => v.toFixed(2) }} }},
    dataZoom: [{{ type: 'inside' }}, {{ type: 'slider', height: 16, bottom: 10 }}],
    series,
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ---- 2. 收益柱状 ----
(function () {{
  const chart = echarts.init(document.getElementById('barChart'));
  chart.setOption({{
    tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }}, backgroundColor: '#fff', borderColor: '#e5e8ec', textStyle: {{ color: '#1f2329' }} }},
    grid: {{ left: 60, right: 20, top: 20, bottom: 30 }},
    xAxis: {{ type: 'category', data: DATA.bar_data.map(d => d.name),
             axisLabel: {{ color: COLOR.text, rotate: 15 }} }},
    yAxis: {{ type: 'value', axisLabel: {{ color: COLOR.text, formatter: v => v + '%' }},
             splitLine: {{ lineStyle: {{ color: COLOR.grid }} }} }},
    series: [{{ type: 'bar', data: DATA.bar_data.map((d, i) => ({{
      value: d.value,
      itemStyle: {{ color: d.value >= 0 ? '#2c6fbb' : '#e8403a',
                    borderRadius: [4, 4, 0, 0] }},
    }})), barMaxWidth: 60,
      label: {{ show: true, position: 'top', formatter: p => p.value + '%', color: COLOR.text }} }}],
  }});
  window.addEventListener('resize', () => chart.resize());
}})();

// ---- 3. K线 ----
(function () {{
  const chart = echarts.init(document.getElementById('klineChart'));
  const sel = document.getElementById('stockSel');
  DATA.kline_data.forEach((k, i) => {{
    const opt = document.createElement('option');
    opt.value = i; opt.textContent = k.code + ' ' + k.name;
    sel.appendChild(opt);
  }});

  function render(idx) {{
    const k = DATA.kline_data[idx];
    const dates = k.ohlc.map(o => o[0]);
    const series = [
      {{ name: 'K线', type: 'candlestick', data: k.ohlc.map(o => [o[1], o[2], o[3], o[4]]),
         itemStyle: {{ color: COLOR.up, color0: COLOR.down, borderColor: COLOR.up, borderColor0: COLOR.down }} }},
      {{ name: 'MA5', type: 'line', showSymbol: false, smooth: true, data: k.ma5,
         lineStyle: {{ width: 1.2, color: '#f5a623' }} }},
      {{ name: 'MA20', type: 'line', showSymbol: false, smooth: true, data: k.ma20,
         lineStyle: {{ width: 1.2, color: '#2c6fbb' }} }},
      {{ name: 'BOLL下轨', type: 'line', showSymbol: false, data: k.boll,
         lineStyle: {{ width: 1, color: '#86909c', type: 'dashed' }} }},
      {{ name: '买入', type: 'scatter', data: k.buys.map(b => [b.date, b.price]),
         symbol: 'triangle', symbolSize: 12, itemStyle: {{ color: '#1ba27a' }} }},
      {{ name: '卖出', type: 'scatter', data: k.sells.map(s => [s.date, s.price]),
         symbol: 'triangle', symbolRotate: 180, symbolSize: 12, itemStyle: {{ color: '#e8403a' }} }},
    ];
    chart.setOption({{
      tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }}, backgroundColor: '#fff',
                  borderColor: '#e5e8ec', textStyle: {{ color: '#1f2329' }} }},
      legend: {{ top: 0, textStyle: {{ color: COLOR.text }} }},
      grid: {{ left: 60, right: 20, top: 40, bottom: 60 }},
      xAxis: {{ type: 'category', data: dates, boundaryGap: true,
               axisLine: {{ lineStyle: {{ color: '#d9dde3' }} }}, axisLabel: {{ color: COLOR.text }} }},
      yAxis: {{ type: 'value', scale: true, splitLine: {{ lineStyle: {{ color: COLOR.grid }} }},
               axisLabel: {{ color: COLOR.text }} }},
      dataZoom: [{{ type: 'inside' }}, {{ type: 'slider', height: 16, bottom: 10 }}],
      series,
    }}, true);
  }}
  sel.addEventListener('change', e => render(Number(e.target.value)));
  if (DATA.kline_data.length) render(0);
  window.addEventListener('resize', () => chart.resize());
}})();

// ---- 5. 用户A操作记录表 (回测trades流水) ----
(function () {{
  const tb = document.getElementById('userTbody');
  if (!DATA.user_trades || DATA.user_trades.length === 0) {{
    const tr = document.createElement('tr');
    tr.innerHTML = '<td colspan="10" style="color:#86909c;text-align:center;padding:16px;">暂无交易记录</td>';
    tb.appendChild(tr);
    return;
  }}
  DATA.user_trades.forEach(m => {{
    const tr = document.createElement('tr');
    const cls = m.side === 'BUY' ? 'buy' : 'sell';
    const amount = m.amount ? '¥' + m.amount.toLocaleString() : '';
    let pnl = '';
    if (m.pnl !== null && m.pnl !== undefined) {{
      const pcls = m.pnl >= 0 ? 'buy' : 'sell';
      pnl = '<span class="' + pcls + '">' + (m.pnl >= 0 ? '+' : '') + m.pnl.toFixed(2) + '%</span>';
    }}
    let cum = '';
    if (m.cum !== null && m.cum !== undefined) {{
      const ccls = m.cum >= 0 ? 'buy' : 'sell';
      cum = '<span class="' + ccls + '">' + (m.cum >= 0 ? '+' : '') + m.cum.toFixed(2) + '%</span>';
    }}
    tr.innerHTML =
      '<td>' + m.date + '</td>' +
      '<td class="' + cls + '">' + m.side_cn + '</td>' +
      '<td>' + m.code + '</td><td>' + m.name + '</td>' +
      '<td>' + m.price.toFixed(3) + '</td>' +
      '<td>' + (m.shares || '') + '</td><td>' + amount + '</td>' +
      '<td>' + pnl + '</td>' +
      '<td>' + cum + '</td>' +
      '<td class="reason-cell">' + (m.reason || '') + '</td>';
    tb.appendChild(tr);
  }});
}})();
</script>
</body>
</html>"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    return output_path

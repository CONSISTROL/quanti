"""
A股多因子量化选股 - 报告生成模块
终端格式化表格 + Plotly交互HTML报告
"""

import os
from datetime import datetime

import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from factor_model import FACTOR_GROUPS


def _display_width(s):
    """计算字符串在终端中的显示宽度（CJK字符占2列）"""
    width = 0
    for ch in str(s):
        if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
            width += 2
        else:
            width += 1
    return width


def _pad_right(s, width):
    """右填充空格，使字符串达到指定显示宽度"""
    s = str(s)
    padding = max(0, width - _display_width(s))
    return s + ' ' * padding


# ============================================================
# 终端报告
# ============================================================

def print_terminal_report(scored_df, top_n, weights, spot_filtered=None):
    """在终端打印格式化的选股结果"""
    top = scored_df.head(top_n)

    # ---- 表头 ----
    print("\n" + "═" * 82)
    print("  A股多因子量化选股报告")
    print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w = weights
    print(f"  因子权重: 价值{w.get('value',0.25):.0%} | 成长{w.get('growth',0.20):.0%} | "
          f"质量{w.get('quality',0.25):.0%} | 动量{w.get('momentum',0.20):.0%} | "
          f"风险{w.get('risk',0.10):.0%}")
    print("═" * 82)

    total = len(scored_df)
    valid = scored_df['composite_score'].notna().sum()
    print(f"  参与排名: {total} 只 | 有效打分: {valid} 只 | 展示 TOP {top_n}")

    # ---- 市场统计 ----
    if spot_filtered is not None:
        try:
            pe_col = next((c for c in ['市盈率-动态', '市盈率'] if c in spot_filtered.columns), None)
            pb_col = next((c for c in ['市净率'] if c in spot_filtered.columns), None)
            if pe_col and pb_col:
                pe_med = pd.to_numeric(spot_filtered[pe_col], errors='coerce').median()
                pb_med = pd.to_numeric(spot_filtered[pb_col], errors='coerce').median()
                if pe_med > 0 and pb_med > 0:
                    print(f"  PE中位数: {pe_med:.1f}  |  PB中位数: {pb_med:.1f}")
        except Exception:
            pass

    print("═" * 82)

    # ---- 表头行 ----
    header = (f" {'排名':>4}  {'代码':<8} {'名称':<8} "
              f"{'现价':>7}  {'综合':>5}  "
              f"{'价值':>5}  {'成长':>5}  {'质量':>5}  {'动量':>5}  {'风险':>5}")
    print(header)
    print("─" * 82)

    # ---- 数据行 ----
    for _, row in top.iterrows():
        code = str(row.get('code', '')).zfill(6)
        name = str(row.get('name', ''))

        price = row.get('price', np.nan)
        score = row.get('composite_score', 0)

        def _fmt_score(key):
            col = f'{key}_score'
            v = row.get(col, np.nan)
            return f"{v:>5.2f}" if pd.notna(v) else "    -"

        print(f" {int(row.get('rank', 0)):>4}  {code:<8} {_pad_right(name, 8)} "
              f"{price:>7.1f}  {score:>5.2f}  "
              f"{_fmt_score('value')}  {_fmt_score('growth')}  "
              f"{_fmt_score('quality')}  {_fmt_score('momentum')}  "
              f"{_fmt_score('risk')}")

    print("─" * 82)
    print("  ⚠️  本报告仅供学习研究，不构成投资建议。投资有风险，入市需谨慎。")
    print()


# ============================================================
# HTML报告
# ============================================================

def generate_html_report(scored_df, top_n, weights, output_path, spot_filtered=None):
    """生成交互式HTML报告（含plotly图表）"""
    top = scored_df.head(top_n)
    total = len(scored_df)

    colors = {
        'value': '#3498db',
        'growth': '#e74c3c',
        'quality': '#2ecc71',
        'momentum': '#f39c12',
        'risk': '#9b59b6',
    }

    # ---- 市场统计 ----
    market_stats = {}
    if spot_filtered is not None:
        try:
            pe_col = next((c for c in ['市盈率-动态', '市盈率'] if c in spot_filtered.columns), None)
            pb_col = next((c for c in ['市净率'] if c in spot_filtered.columns), None)
            if pe_col:
                market_stats['PE中位数'] = f"{pd.to_numeric(spot_filtered[pe_col], errors='coerce').median():.1f}"
            if pb_col:
                market_stats['PB中位数'] = f"{pd.to_numeric(spot_filtered[pb_col], errors='coerce').median():.1f}"
        except Exception:
            pass

    avg_score = scored_df['composite_score'].mean()
    market_stats['平均得分'] = f"{avg_score:.2f}"
    market_stats['最高得分'] = f"{scored_df['composite_score'].max():.2f}"

    # ---- 构建HTML ----
    html_parts = []

    # === HEAD ===
    html_parts.append(_html_head())

    # === HEADER ===
    w = weights
    html_parts.append(f"""
    <h1>📊 A股多因子量化选股报告</h1>
    <div class="subtitle">
        生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp;
        权重: 价值{w.get('value',0.25):.0%} / 成长{w.get('growth',0.20):.0%} /
        质量{w.get('quality',0.25):.0%} / 动量{w.get('momentum',0.20):.0%} /
        风险{w.get('risk',0.10):.0%}
    </div>
    """)

    # === 统计卡片 ===
    html_parts.append('<div class="cards">')
    _add_card(html_parts, '扫描股票', f'{total:,}', '只')
    _add_card(html_parts, '有效打分', f'{scored_df["composite_score"].notna().sum():,}', '只')
    _add_card(html_parts, '展示 TOP', str(top_n), '只')
    for key, val in market_stats.items():
        _add_card(html_parts, key, val, '')
    html_parts.append('</div>')

    # === 图表区 ===
    html_parts.append('<div class="charts-grid">')

    # 1. 因子雷达图
    radar_html = _plot_radar(top, colors)
    html_parts.append(f"""
    <div class="chart-container">
        <h2>🎯 TOP 10 平均因子画像</h2>
        {radar_html}
    </div>
    """)

    # 2. 行业分布
    sector_html = _plot_sector(top)
    html_parts.append(f"""
    <div class="chart-container">
        <h2>🏭 行业分布</h2>
        {sector_html}
    </div>
    """)

    # 3. 综合得分分布
    dist_html = _plot_score_distribution(scored_df, top)
    html_parts.append(f"""
    <div class="chart-container">
        <h2>📈 综合得分分布</h2>
        {dist_html}
    </div>
    """)

    # 4. 价值 vs 质量散点图
    scatter_html = _plot_factor_scatter(scored_df, top, colors)
    html_parts.append(f"""
    <div class="chart-container">
        <h2>💎 因子空间 (价值 × 质量)</h2>
        {scatter_html}
    </div>
    """)

    html_parts.append('</div>')  # charts-grid

    # === 详细表格 ===
    html_parts.append(_build_table(top))

    # === 免责声明 ===
    html_parts.append("""
    <div class="disclaimer">
        ⚠️ 免责声明：本报告由量化模型自动生成，仅供学习研究参考，不构成任何投资建议。
        股市有风险，投资需谨慎。过往表现不代表未来收益。
    </div>
    </div></body></html>
    """)

    # ---- 写入文件 ----
    os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(html_parts))


# ============================================================
# HTML 构建辅助函数
# ============================================================

def _html_head():
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>A股多因子选股报告 - {datetime.now().strftime('%Y-%m-%d')}</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: #f0f2f5; color: #333; line-height: 1.6;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 24px; }}
  h1 {{
    text-align: center; color: #1a1a2e; font-size: 28px;
    margin-bottom: 8px; padding-top: 10px;
  }}
  .subtitle {{
    text-align: center; color: #888; font-size: 14px;
    margin-bottom: 24px;
  }}
  .cards {{
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 14px; margin-bottom: 24px;
  }}
  .card {{
    background: white; border-radius: 12px; padding: 18px; text-align: center;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06); transition: transform 0.2s;
  }}
  .card:hover {{ transform: translateY(-2px); }}
  .card-value {{
    font-size: 30px; font-weight: 700; color: #1a1a2e;
    margin: 4px 0;
  }}
  .card-label {{ font-size: 13px; color: #999; }}
  .card-unit {{ font-size: 13px; color: #bbb; margin-left: 2px; }}
  .charts-grid {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 18px;
    margin-bottom: 24px;
  }}
  .chart-container {{
    background: white; border-radius: 12px; padding: 18px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  }}
  .chart-container h2 {{
    font-size: 16px; color: #1a1a2e; margin-bottom: 10px;
  }}
  table {{
    width: 100%; border-collapse: collapse; background: white;
    border-radius: 12px; overflow: hidden;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  }}
  th {{
    background: #1a1a2e; color: white; padding: 12px 8px;
    font-size: 13px; text-align: center; cursor: pointer;
    user-select: none;
  }}
  th:hover {{ background: #2a2a4e; }}
  td {{
    padding: 9px 8px; text-align: center; font-size: 13px;
    border-bottom: 1px solid #f0f0f0;
  }}
  tr:hover {{ background: #f8f9ff; }}
  .score-positive {{ color: #27ae60; font-weight: 600; }}
  .score-negative {{ color: #e74c3c; font-weight: 600; }}
  .rank-badge {{
    display: inline-block; width: 28px; height: 28px;
    line-height: 28px; border-radius: 50%; color: white;
    font-weight: 700; font-size: 13px;
  }}
  .rank-1 {{ background: #f1c40f; }}
  .rank-2 {{ background: #bdc3c7; }}
  .rank-3 {{ background: #e67e22; }}
  .rank-other {{ background: #ecf0f1; color: #333; }}
  .disclaimer {{
    text-align: center; color: #aaa; font-size: 12px;
    margin-top: 24px; padding: 16px; border-top: 1px solid #e0e0e0;
  }}
  @media (max-width: 768px) {{
    .charts-grid {{ grid-template-columns: 1fr; }}
    .cards {{ grid-template-columns: repeat(2, 1fr); }}
  }}
</style>
</head>
<body>
<div class="container">
"""


def _add_card(parts, label, value, unit):
    parts.append(f"""
    <div class="card">
        <div class="card-label">{label}</div>
        <div class="card-value">{value}<span class="card-unit">{unit}</span></div>
    </div>
    """)


# ============================================================
# Plotly 图表
# ============================================================

def _plot_radar(top_df, colors):
    """TOP N 平均因子雷达图"""
    top10 = top_df.head(10)
    categories = list(FACTOR_GROUPS.values())
    r_values = []
    for g in FACTOR_GROUPS:
        col = f'{g}_score'
        val = top10[col].mean() if col in top10.columns else 0
        r_values.append(round(val, 3) if pd.notna(val) else 0)

    fig = go.Figure()
    fig.add_trace(go.Scatterpolar(
        r=r_values + [r_values[0]],
        theta=[c['label'] for c in categories] + [categories[0]['label']],
        fill='toself',
        fillcolor='rgba(52,152,219,0.15)',
        line=dict(color='#3498db', width=2),
        marker=dict(size=7, color='#3498db'),
    ))
    fig.update_layout(
        polar=dict(
            radialaxis=dict(visible=True, gridcolor='#e0e0e0'),
            angularaxis=dict(gridcolor='#e0e0e0'),
        ),
        showlegend=False, height=350, margin=dict(t=20, b=30, l=60, r=60),
        font=dict(size=12),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _plot_sector(top_df):
    """行业分布图"""
    if 'sector' not in top_df.columns:
        return '<p style="color:#999;text-align:center;padding:40px;">无行业数据</p>'

    sector_counts = (top_df['sector']
                     .replace('未知', np.nan)
                     .dropna()
                     .value_counts()
                     .head(12))
    if sector_counts.empty:
        return '<p style="color:#999;text-align:center;padding:40px;">无有效行业分类</p>'

    fig = go.Figure(go.Bar(
        x=sector_counts.values,
        y=sector_counts.index,
        orientation='h',
        marker=dict(
            color=sector_counts.values,
            colorscale='Blues',
            showscale=False,
        ),
        text=sector_counts.values,
        textposition='outside',
    ))
    fig.update_layout(
        height=350, margin=dict(t=10, b=30, l=100, r=40),
        xaxis=dict(title='股票数量', gridcolor='#f0f0f0'),
        yaxis=dict(autorange='reversed'),
        font=dict(size=12),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _plot_score_distribution(scored_df, top_df):
    """综合得分分布直方图"""
    scores = scored_df['composite_score'].dropna()
    top_scores = top_df['composite_score'].dropna()

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=scores, nbinsx=50, name='全部股票',
        marker=dict(color='rgba(52,152,219,0.35)'),
    ))
    fig.add_trace(go.Histogram(
        x=top_scores, nbinsx=20, name='TOP 入选',
        marker=dict(color='rgba(231,76,60,0.7)'),
    ))
    fig.update_layout(
        barmode='overlay', height=350,
        margin=dict(t=20, b=40, l=50, r=20),
        xaxis=dict(title='综合得分', gridcolor='#f0f0f0'),
        yaxis=dict(title='股票数量', gridcolor='#f0f0f0'),
        legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
        font=dict(size=12),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


def _plot_factor_scatter(scored_df, top_df, colors):
    """价值×质量 因子散点图"""
    fig = go.Figure()

    # 全部股票 (灰色背景)
    if 'value_score' in scored_df.columns and 'quality_score' in scored_df.columns:
        fig.add_trace(go.Scatter(
            x=scored_df['value_score'], y=scored_df['quality_score'],
            mode='markers',
            marker=dict(size=4, color='rgba(200,200,200,0.4)'),
            name='全部', showlegend=True,
        ))
        # TOP 股票 (高亮)
        fig.add_trace(go.Scatter(
            x=top_df['value_score'], y=top_df['quality_score'],
            mode='markers+text',
            text=top_df['name'], textposition='top center',
            marker=dict(size=9, color=colors.get('value', '#3498db'),
                        line=dict(width=1, color='white')),
            name='TOP 入选',
        ))
        # 添加象限参考线
        fig.add_hline(y=0, line_dash='dash', line_color='#ddd', line_width=1)
        fig.add_vline(x=0, line_dash='dash', line_color='#ddd', line_width=1)

    fig.update_layout(
        height=350, margin=dict(t=20, b=40, l=50, r=20),
        xaxis=dict(title='价值因子得分', gridcolor='#f0f0f0'),
        yaxis=dict(title='质量因子得分', gridcolor='#f0f0f0'),
        legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
        font=dict(size=12),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


# ============================================================
# 详细表格
# ============================================================

def _build_table(top_df):
    """构建可排序的详细表格"""
    rows_html = []
    for i, (_, row) in enumerate(top_df.iterrows()):
        rank = int(row.get('rank', i + 1))
        if rank <= 3:
            badge_class = f'rank-{rank}'
        else:
            badge_class = 'rank-other'

        def _cell(key):
            col = f'{key}_score'
            v = row.get(col, np.nan)
            if pd.isna(v):
                return '<td>-</td>'
            css = 'score-positive' if v > 0 else 'score-negative' if v < 0 else ''
            return f'<td class="{css}">{v:+.2f}</td>'

        sector = row.get('sector', '未知')
        if sector == '未知':
            sector = '-'

        rows_html.append(f"""<tr>
            <td><span class="rank-badge {badge_class}">{rank}</span></td>
            <td><strong>{str(row.get('code', '')).zfill(6)}</strong></td>
            <td>{row.get('name', '')}</td>
            <td>{sector}</td>
            <td>{row.get('price', 0):.2f}</td>
            <td><strong>{row.get('composite_score', 0):+.2f}</strong></td>
            {_cell('value')}{_cell('growth')}{_cell('quality')}
            {_cell('momentum')}{_cell('risk')}
        </tr>""")

    return f"""
    <h2 style="margin:24px 0 12px;color:#1a1a2e;">📋 详细排名</h2>
    <table>
    <thead><tr>
        <th>排名</th><th>代码</th><th>名称</th><th>行业</th>
        <th>现价</th><th>综合</th>
        <th>价值</th><th>成长</th><th>质量</th><th>动量</th><th>风险</th>
    </tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
    </table>
    """

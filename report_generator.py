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

def print_terminal_report(scored_df, top_n, weights, spot_filtered=None,
                          backtest_result=None, backtest_date=None, fwd_summary=None):
    """在终端打印格式化的选股结果"""
    top = scored_df.head(top_n)

    # ---- 表头 ----
    print("\n" + "═" * 95)
    if backtest_date:
        print(f"  A股多因子量化选股报告（模拟日期: {backtest_date.strftime('%Y-%m-%d')}）")
        print(f"  模拟时间: {backtest_date.strftime('%Y-%m-%d')}  |  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    else:
        print("  A股多因子量化选股报告")
        print(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    w = weights
    print(f"  因子权重: 价值{w.get('value',0.25):.0%} | 成长{w.get('growth',0.20):.0%} | "
          f"质量{w.get('quality',0.25):.0%} | 动量{w.get('momentum',0.20):.0%} | "
          f"风险{w.get('risk',0.10):.0%}")
    print("═" * 95)

    total = len(scored_df)
    valid = scored_df['composite_score'].notna().sum()

    # 资产类型统计
    has_asset_type = 'asset_type' in scored_df.columns
    type_info = ''
    if has_asset_type:
        n_stock = (scored_df['asset_type'] == 'stock').sum()
        n_etf = (scored_df['asset_type'] == 'etf').sum()
        n_lof = (scored_df['asset_type'] == 'lof').sum()
        type_info = f' (股票{n_stock} + ETF{n_etf} + LOF{n_lof})'

    print(f"  参与排名: {total} 只{type_info} | 有效打分: {valid} 只 | 展示 TOP {top_n}")

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

    print("═" * 95)

    # 是否有买卖参考价
    has_targets = 'buy_low' in scored_df.columns and scored_df['buy_low'].notna().any()

    # ---- 表头行 ----
    header = (f" {'排名':>4}  {'类型':>4} {'代码':<8} {'名称':<8} "
              f"{'现价':>7}  {'综合':>5}  "
              f"{'价值':>5}  {'成长':>5}  {'质量':>5}  {'动量':>5}  {'风险':>5}")
    if has_targets:
        header += f"  {'买入区间':>12}  {'卖出区间':>12}"
    print(header)
    print("─" * (95 + (28 if has_targets else 0)))

    # ---- 数据行 ----
    for _, row in top.iterrows():
        code = str(row.get('code', '')).zfill(6)
        name = str(row.get('name', ''))

        # 资产类型标签
        atype = row.get('asset_type', 'stock')
        type_label = {'stock': '股票', 'etf': 'ETF', 'lof': 'LOF'}.get(atype, '股票')

        price = row.get('price', np.nan)
        score = row.get('composite_score', 0)

        def _fmt_score(key):
            col = f'{key}_score'
            v = row.get(col, np.nan)
            return f"{v:>5.2f}" if pd.notna(v) else "    -"

        line = (f" {int(row.get('rank', 0)):>4}  {type_label:>4} {code:<8} {_pad_right(name, 8)} "
              f"{price:>7.2f}  {score:>5.2f}  "
              f"{_fmt_score('value')}  {_fmt_score('growth')}  "
              f"{_fmt_score('quality')}  {_fmt_score('momentum')}  "
              f"{_fmt_score('risk')}")

        if has_targets:
            bl = row.get('buy_low', np.nan)
            bh = row.get('buy_high', np.nan)
            sl = row.get('sell_low', np.nan)
            sh = row.get('sell_high', np.nan)

            buy_str = f"{bl:.2f}~{bh:.2f}" if pd.notna(bl) and pd.notna(bh) else "  -"
            sell_str = f"{sl:.2f}~{sh:.2f}" if pd.notna(sl) and pd.notna(sh) else "  -"
            line += f"  {buy_str:>12}  {sell_str:>12}"

        print(line)

    print("─" * (95 + (28 if has_targets else 0)))
    print("  ⚠️  本报告仅供学习研究，不构成投资建议。投资有风险，入市需谨慎。")
    print()


# ============================================================
# HTML报告
# ============================================================

def generate_html_report(scored_df, top_n, weights, output_path, spot_filtered=None,
                         backtest_result=None, backtest_date=None, fwd_summary=None,
                         stock_details=None, trade_result=None):
    """生成交互式HTML报告（含plotly图表）"""
    # 处理scored_df为None的情况 (个股回测模式)
    if scored_df is None:
        import pandas as pd
        scored_df = pd.DataFrame()

    top = scored_df.head(top_n) if len(scored_df) > 0 else pd.DataFrame()
    total = len(scored_df)

    # 如果是空DataFrame (个股回测模式), 跳过选股相关统计
    is_single_stock_mode = len(scored_df) == 0

    # 根据是否有模拟日期调整输出文件名
    if backtest_date and output_path:
        date_tag = backtest_date.strftime('%Y%m%d')
        output_path = output_path.replace(
            datetime.now().strftime('%Y%m%d'),
            f'sim_{date_tag}'
        )

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

    if not is_single_stock_mode:
        avg_score = scored_df['composite_score'].mean()
        market_stats['平均得分'] = f"{avg_score:.2f}"
        market_stats['最高得分'] = f"{scored_df['composite_score'].max():.2f}"

        # 资产类型统计
        has_asset_type = 'asset_type' in scored_df.columns
        if has_asset_type:
            n_stock = (scored_df['asset_type'] == 'stock').sum()
            n_etf = (scored_df['asset_type'] == 'etf').sum()
            n_lof = (scored_df['asset_type'] == 'lof').sum()
            market_stats['股票/ETF/LOF'] = f'{n_stock}/{n_etf}/{n_lof}'

    # ---- 构建HTML ----
    html_parts = []

    # === HEAD ===
    html_parts.append(_html_head(backtest_date))

    # === HEADER ===
    w = weights
    if backtest_date:
        title = f'📊 A股多因子量化选股报告<br><span style="color:#e74c3c;font-size:20px;">🕰️ 模拟日期: {backtest_date.strftime("%Y-%m-%d")}</span>'
        subtitle = f'''模拟时间: {backtest_date.strftime('%Y-%m-%d')} &nbsp;|&nbsp;
            生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp;
            权重: 价值{w.get('value',0.25):.0%} / 成长{w.get('growth',0.20):.0%} /
            质量{w.get('quality',0.25):.0%} / 动量{w.get('momentum',0.20):.0%} /
            风险{w.get('risk',0.10):.0%}'''
    else:
        title = '📊 A股多因子量化选股报告'
        subtitle = f'''生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')} &nbsp;|&nbsp;
            权重: 价值{w.get('value',0.25):.0%} / 成长{w.get('growth',0.20):.0%} /
            质量{w.get('quality',0.25):.0%} / 动量{w.get('momentum',0.20):.0%} /
            风险{w.get('risk',0.10):.0%}'''

    html_parts.append(f"""
    <h1>{title}</h1>
    <div class="subtitle">
        {subtitle}
    </div>
    """)

    # === 统计卡片 ===
    html_parts.append('<div class="cards">')
    _add_card(html_parts, '扫描证券', f'{total:,}', '只')
    if not is_single_stock_mode:
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

    # 3. 综合得分分布 (仅在全市场扫描模式)
    if not is_single_stock_mode:
        dist_html = _plot_score_distribution(scored_df, top)
        html_parts.append(f"""
    <div class="chart-container">
        <h2>📈 综合得分分布</h2>
        {dist_html}
    </div>
    """)

    # 4. 价值 vs 质量散点图 (仅在全市场扫描模式)
    if not is_single_stock_mode:
        scatter_html = _plot_factor_scatter(scored_df, top, colors)
        html_parts.append(f"""
    <div class="chart-container">
        <h2>💎 因子空间 (价值 × 质量)</h2>
        {scatter_html}
    </div>
    """)

    # 5. 资产类型分布 (如果有ETF/LOF)
    if not is_single_stock_mode and has_asset_type and scored_df['asset_type'].nunique() > 1:
        pie_html = _plot_asset_type_pie(scored_df, top)
        html_parts.append(f"""
    <div class="chart-container">
        <h2>📦 资产类型分布</h2>
        {pie_html}
    </div>
    """)

    # 6. 估值分位数分布 (如果有买卖参考价)
    has_targets = 'pe_percentile' in scored_df.columns and scored_df['pe_percentile'].notna().any()
    if has_targets:
        val_html = _plot_valuation_distribution(scored_df, top)
        html_parts.append(f"""
    <div class="chart-container">
        <h2>📊 估值分位数分布</h2>
        {val_html}
    </div>
    """)

    html_parts.append('</div>')  # charts-grid

    # === 回测图表区 ===
    if backtest_result is not None:
        html_parts.append('<h2 style="margin:24px 0 12px;color:#1a1a2e;">🔬 回测结果</h2>')

        # 回测摘要表
        html_parts.append(_build_backtest_summary_table(backtest_result))

        # 回测图表 (仅在有交易时显示)
        has_trades = False
        if isinstance(backtest_result, dict):
            trades = backtest_result.get('trades', [])
            has_trades = len([t for t in trades if t.direction == 'SELL']) > 0
        else:
            has_trades = backtest_result.n_rebalances > 0

        if has_trades:
            from backtest import generate_backtest_charts
            bt_charts = generate_backtest_charts(backtest_result)
            if bt_charts:
                html_parts.append('<div class="charts-grid">')
                for title, chart_html in bt_charts:
                    html_parts.append(f"""
    <div class="chart-container">
        <h2>{title}</h2>
        {chart_html}
    </div>
    """)
                html_parts.append('</div>')
        else:
            html_parts.append("""
    <div class="chart-container">
        <p style="text-align:center; color:#666; padding:40px;">
            ⚠️ 回测期间未触发任何买卖信号<br>
            可能原因：买入条件过于严格，或市场未满足信号条件
        </p>
    </div>
    """)

    # === 指定日期选股: 前瞻收益验证 ===
    if fwd_summary:
        html_parts.append(f"""
    <h2 style="margin:24px 0 12px;color:#1a1a2e;">
        🕰️ 模拟选股验证（{backtest_date.strftime('%Y-%m-%d') if backtest_date else ''} 买入后的实际表现）
    </h2>
    <table style="margin-bottom:20px;">
    <thead><tr>
        <th>持有天数</th><th>平均收益</th><th>胜率</th><th>盈利/总数</th><th>评价</th>
    </tr></thead>
    <tbody>""")
        for item in fwd_summary:
            avg = item['avg_return']
            wr = item['win_rate']
            avg_css = 'color:#27ae60;font-weight:600;' if avg > 0 else 'color:#e74c3c;font-weight:600;'
            wr_css = 'color:#27ae60;font-weight:600;' if wr > 0.5 else 'color:#e74c3c;'
            if wr >= 0.6 and avg > 0.02:
                tag = '<span style="background:#27ae60;color:white;padding:2px 8px;border-radius:10px;font-size:11px;">优秀</span>'
            elif wr >= 0.5 and avg > 0:
                tag = '<span style="background:#f39c12;color:white;padding:2px 8px;border-radius:10px;font-size:11px;">良好</span>'
            elif avg > 0:
                tag = '<span style="background:#3498db;color:white;padding:2px 8px;border-radius:10px;font-size:11px;">一般</span>'
            else:
                tag = '<span style="background:#e74c3c;color:white;padding:2px 8px;border-radius:10px;font-size:11px;">较差</span>'
            # 显示持有天数标签
            if 'label' in item:
                days_text = item['label']
            else:
                days_text = f"{item['days']}日"
            html_parts.append(f"""<tr>
                <td><strong>{days_text}</strong></td>
                <td style="{avg_css}">{avg:+.2%}</td>
                <td style="{wr_css}">{wr:.0%}</td>
                <td>{item['win_count']}/{item['total_count']}</td>
                <td>{tag}</td>
            </tr>""")
        html_parts.append("""</tbody>
    </table>
    <p style="color:#999;font-size:12px;">⚠️ 以上收益为模拟推荐日的实际后续表现，用于验证策略有效性。不构成投资建议。</p>
    """)

    # === 个股前瞻收益明细表 ===
    if stock_details:
        # 获取至今天数（从第一条记录取）
        today_days_label = ''
        for s in stock_details:
            td = s.get('today_days')
            if td is not None:
                today_days_label = f'至今({td}日)'
                break
        if not today_days_label:
            today_days_label = '至今'

        html_parts.append(f"""
    <h2 style="margin:24px 0 12px;color:#1a1a2e;">📋 个股前瞻收益明细</h2>
    <table style="margin-bottom:20px;">
    <thead><tr>
        <th>排名</th><th>代码</th><th>名称</th><th>买入价</th>
        <th>5日后</th><th>10日后</th><th>20日后</th><th>60日后</th><th>{today_days_label}</th>
    </tr></thead>
    <tbody>""")
        for s in stock_details:
            def _fwd_cell(val):
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    return '<td>-</td>'
                css = 'color:#27ae60;font-weight:600;' if val > 0 else 'color:#e74c3c;font-weight:600;'
                return f'<td style="{css}">{val:+.2%}</td>'

            html_parts.append(f"""<tr>
                <td><strong>{s['rank']}</strong></td>
                <td><strong>{s['code']}</strong></td>
                <td>{s['name']}</td>
                <td>{s['price']:.2f}</td>
                {_fwd_cell(s.get('fwd_5'))}{_fwd_cell(s.get('fwd_10'))}
                {_fwd_cell(s.get('fwd_20'))}{_fwd_cell(s.get('fwd_60'))}
                {_fwd_cell(s.get('fwd_today'))}
            </tr>""")
        html_parts.append("</tbody></table>")

    # === 波段交易回测结果 ===
    if trade_result:
        stats = trade_result['stats']
        trades = trade_result['trades']
        equity_chart_html = ''
        try:
            from trading_engine import generate_equity_chart
            equity_chart_html = generate_equity_chart(trade_result)
        except Exception:
            pass

        # 统计卡片
        html_parts.append("""
    <h2 style="margin:24px 0 12px;color:#1a1a2e;">📈 波段交易回测</h2>
    <div class="cards">""")
        ret_css = 'color:#27ae60;' if stats['total_return'] > 0 else 'color:#e74c3c;'
        html_parts.append(f"""
        <div class="card"><div class="card-label">总收益率</div>
            <div class="card-value" style="{ret_css}">{stats['total_return']:+.1%}</div></div>
        <div class="card"><div class="card-label">年化收益</div>
            <div class="card-value" style="{ret_css}">{stats['annual_return']:+.1%}</div></div>
        <div class="card"><div class="card-label">Sharpe</div>
            <div class="card-value">{stats['sharpe']:.2f}</div></div>
        <div class="card"><div class="card-label">最大回撤</div>
            <div class="card-value" style="color:#e74c3c;">{stats['max_drawdown']:.1%}</div></div>
        <div class="card"><div class="card-label">交易次数</div>
            <div class="card-value">{stats['total_trades']}</div></div>
        <div class="card"><div class="card-label">胜率</div>
            <div class="card-value">{stats['win_rate']:.0%}</div></div>
        <div class="card"><div class="card-label">盈亏比</div>
            <div class="card-value">{stats['profit_loss_ratio']:.2f}</div></div>
        <div class="card"><div class="card-label">最终资金</div>
            <div class="card-value" style="{ret_css}">¥{stats['final_value']:,.0f}</div></div>
    </div>""")

        # 收益曲线
        if equity_chart_html:
            html_parts.append(f"""
    <div class="chart-container" style="margin-bottom:20px;">
        {equity_chart_html}
    </div>""")

        # 操作记录表
        if trades:
            # 日期→净值映射
            date_nav = {}
            init_cap = trade_result['initial_capital']
            for d, v in trade_result.get('equity_curve', []):
                date_nav[pd.Timestamp(d).strftime('%m-%d')] = v

            html_parts.append("""
    <h2 style="margin:24px 0 12px;color:#1a1a2e;">📋 操作记录</h2>
    <table style="margin-bottom:20px;">
    <thead><tr>
        <th>日期</th><th>方向</th><th>代码</th><th>名称</th>
        <th>价格</th><th>数量</th><th>金额</th><th>盈亏</th><th>累计</th><th>总市值</th><th>仓位</th><th>原因</th>
    </tr></thead>
    <tbody>""")
            held = {}  # 跟踪持仓
            for t in trades:
                dir_css = 'color:#27ae60;font-weight:600;' if t.direction == 'BUY' else 'color:#e74c3c;font-weight:600;'
                dir_label = '买入' if t.direction == 'BUY' else '卖出'
                pnl_str = f'{t.pnl_pct:+.1%}' if t.direction == 'SELL' else '-'
                pnl_css = ''
                if t.direction == 'SELL':
                    pnl_css = 'color:#27ae60;' if t.pnl_pct > 0 else 'color:#e74c3c;'
                # 更新持仓
                if t.direction == 'BUY':
                    held[t.code] = (t.shares, t.price)
                else:
                    held.pop(t.code, None)
                # 累计收益 + 总市值 + 仓位
                d_short = t.date.strftime('%Y-%m-%d')
                nav = date_nav.get(d_short, init_cap)
                cum = (nav / init_cap - 1) if init_cap > 0 else 0
                cum_css = 'color:#27ae60;font-weight:600;' if cum >= 0 else 'color:#e74c3c;font-weight:600;'
                held_val = sum(s * p for s, p in held.values())
                pos_ratio = held_val / nav if nav > 0 else 0
                html_parts.append(f"""<tr>
                    <td>{t.date.strftime('%Y-%m-%d')}</td>
                    <td style="{dir_css}">{dir_label}</td>
                    <td><strong>{t.code}</strong></td>
                    <td>{t.name}</td>
                    <td>{t.price:.2f}</td>
                    <td>{t.shares}</td>
                    <td>¥{t.amount:,.0f}</td>
                    <td style="{pnl_css}font-weight:600;">{pnl_str}</td>
                    <td style="{cum_css}">{cum:+.1%}</td>
                    <td>¥{nav:,.0f}</td>
                    <td>{pos_ratio:.0%}</td>
                    <td>{t.reason}</td>
                </tr>""")
            html_parts.append("</tbody></table>")

        # 当前持仓
        if trade_result.get('final_positions'):
            html_parts.append("""
    <h2 style="margin:24px 0 12px;color:#1a1a2e;">📦 当前持仓建议</h2>
    <table style="margin-bottom:20px;">
    <thead><tr><th>代码</th><th>名称</th><th>成本价</th><th>现价</th><th>数量</th><th>浮动盈亏</th><th>盈亏%</th><th>投入资金</th></tr></thead>
    <tbody>""")
            total_float = 0
            for pos in trade_result['final_positions']:
                cur = getattr(pos, '_last_price', pos.entry_price)
                fpnl = (cur - pos.entry_price) * pos.shares
                fpct = (cur / pos.entry_price - 1) if pos.entry_price > 0 else 0
                total_float += fpnl
                css = 'color:#10b981;font-weight:600;' if fpnl >= 0 else 'color:#ef4444;font-weight:600;'
                sign = '+' if fpnl >= 0 else ''
                html_parts.append(f"""<tr>
                    <td><strong>{pos.code}</strong></td>
                    <td>{pos.name}</td>
                    <td>¥{pos.entry_price:.2f}</td>
                    <td>¥{cur:.2f}</td>
                    <td>{pos.shares}</td>
                    <td style="{css}">{sign}¥{fpnl:,.0f}</td>
                    <td style="{css}">{sign}{fpct:.1%}</td>
                    <td>¥{pos.capital:,.0f}</td>
                </tr>""")
            total_css = 'color:#10b981;font-weight:600;' if total_float >= 0 else 'color:#ef4444;font-weight:600;'
            total_sign = '+' if total_float >= 0 else ''
            html_parts.append(f"""<tr style="border-top:2px solid #e2e8f0;font-weight:600;">
                <td colspan="5">合计浮动盈亏</td>
                <td style="{total_css}">{total_sign}¥{total_float:,.0f}</td>
                <td colspan="2"></td>
            </tr>""")
            html_parts.append("</tbody></table>")

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

    return output_path


# ============================================================
# HTML 构建辅助函数
# ============================================================

def _html_head(backtest_date=None):
    date_str = backtest_date.strftime('%Y-%m-%d') if backtest_date else datetime.now().strftime('%Y-%m-%d')
    title_str = f'A股选股报告(模拟{date_str})' if backtest_date else f'A股多因子选股报告 - {date_str}'
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title_str}</title>
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
    has_asset_type = 'asset_type' in top_df.columns
    has_targets = 'buy_low' in top_df.columns and top_df['buy_low'].notna().any()

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

        # 资产类型标签
        type_html = ''
        if has_asset_type:
            atype = row.get('asset_type', 'stock')
            type_colors = {'stock': '#3498db', 'etf': '#e67e22', 'lof': '#9b59b6'}
            type_labels = {'stock': '股票', 'etf': 'ETF', 'lof': 'LOF'}
            color = type_colors.get(atype, '#3498db')
            label = type_labels.get(atype, '股票')
            type_html = f'<td><span style="background:{color};color:white;padding:2px 8px;border-radius:10px;font-size:11px;">{label}</span></td>'

        # 买卖参考价
        target_html = ''
        if has_targets:
            bl = row.get('buy_low', np.nan)
            bh = row.get('buy_high', np.nan)
            sl = row.get('sell_low', np.nan)
            sh = row.get('sell_high', np.nan)
            pe_pct = row.get('pe_percentile', np.nan)

            buy_str = f'{bl:.2f}~{bh:.2f}' if pd.notna(bl) and pd.notna(bh) else '-'
            sell_str = f'{sl:.2f}~{sh:.2f}' if pd.notna(sl) and pd.notna(sh) else '-'
            pe_str = f'{pe_pct:.0f}%' if pd.notna(pe_pct) else '-'

            # 估值颜色
            pe_css = ''
            if pd.notna(pe_pct):
                if pe_pct < 30:
                    pe_css = 'style="color:#27ae60;font-weight:600;"'  # 低估 绿色
                elif pe_pct > 70:
                    pe_css = 'style="color:#e74c3c;font-weight:600;"'  # 高估 红色

            target_html = f'<td style="color:#27ae60;">{buy_str}</td><td style="color:#e74c3c;">{sell_str}</td><td {pe_css}>{pe_str}</td>'

        rows_html.append(f"""<tr>
            <td><span class="rank-badge {badge_class}">{rank}</span></td>
            {type_html}
            <td><strong>{str(row.get('code', '')).zfill(6)}</strong></td>
            <td>{row.get('name', '')}</td>
            <td>{sector}</td>
            <td>{row.get('price', 0):.2f}</td>
            <td><strong>{row.get('composite_score', 0):+.2f}</strong></td>
            {_cell('value')}{_cell('growth')}{_cell('quality')}
            {_cell('momentum')}{_cell('risk')}
            {target_html}
        </tr>""")

    # 表头
    type_th = '<th>类型</th>' if has_asset_type else ''
    target_ths = '<th>买入区间</th><th>卖出区间</th><th>PE百分位</th>' if has_targets else ''

    return f"""
    <h2 style="margin:24px 0 12px;color:#1a1a2e;">📋 详细排名</h2>
    <table>
    <thead><tr>
        <th>排名</th>{type_th}<th>代码</th><th>名称</th><th>行业</th>
        <th>现价</th><th>综合</th>
        <th>价值</th><th>成长</th><th>质量</th><th>动量</th><th>风险</th>
        {target_ths}
    </tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
    </table>
    """


# ============================================================
# 新增图表: 资产类型分布
# ============================================================

def _plot_asset_type_pie(scored_df, top_df):
    """资产类型分布饼图"""
    type_map = {'stock': '股票', 'etf': 'ETF', 'lof': 'LOF'}
    type_colors = {'stock': '#3498db', 'etf': '#e67e22', 'lof': '#9b59b6'}

    # 全部证券
    all_counts = scored_df['asset_type'].value_counts()
    all_labels = [type_map.get(t, t) for t in all_counts.index]

    # TOP N
    top_counts = top_df['asset_type'].value_counts()
    top_labels = [type_map.get(t, t) for t in top_counts.index]

    fig = make_subplots(rows=1, cols=2, specs=[[{'type': 'pie'}, {'type': 'pie'}]])

    fig.add_trace(go.Pie(
        labels=all_labels, values=all_counts.values,
        name='全部', hole=0.4,
        marker=dict(colors=[type_colors.get(t, '#95a5a6') for t in all_counts.index]),
    ), row=1, col=1)

    fig.add_trace(go.Pie(
        labels=top_labels, values=top_counts.values,
        name='TOP N', hole=0.4,
        marker=dict(colors=[type_colors.get(t, '#95a5a6') for t in top_counts.index]),
    ), row=1, col=2)

    fig.update_layout(
        height=300, margin=dict(t=20, b=20, l=20, r=20),
        annotations=[
            dict(text='全部', x=0.22, y=0.5, font_size=14, showarrow=False),
            dict(text='TOP N', x=0.78, y=0.5, font_size=14, showarrow=False),
        ],
        font=dict(size=12),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


# ============================================================
# 新增图表: 估值分位数分布
# ============================================================

def _plot_valuation_distribution(scored_df, top_df):
    """估值分位数分布直方图"""
    if 'pe_percentile' not in scored_df.columns:
        return '<p style="color:#999;text-align:center;padding:40px;">无估值数据</p>'

    pe_all = scored_df['pe_percentile'].dropna()
    pe_top = top_df['pe_percentile'].dropna()

    if pe_all.empty:
        return '<p style="color:#999;text-align:center;padding:40px;">无有效估值分位数据</p>'

    fig = go.Figure()
    fig.add_trace(go.Histogram(
        x=pe_all, nbinsx=20, name='全部',
        marker=dict(color='rgba(52,152,219,0.35)'),
    ))
    fig.add_trace(go.Histogram(
        x=pe_top, nbinsx=10, name='TOP 入选',
        marker=dict(color='rgba(231,76,60,0.7)'),
    ))

    # 添加区域标注
    fig.add_vrect(x0=0, x1=30, fillcolor='rgba(39,174,96,0.1)', line_width=0,
                  annotation_text='低估', annotation_position='top left')
    fig.add_vrect(x0=70, x1=100, fillcolor='rgba(231,76,60,0.1)', line_width=0,
                  annotation_text='高估', annotation_position='top right')

    fig.update_layout(
        barmode='overlay', height=300,
        margin=dict(t=20, b=40, l=50, r=20),
        xaxis=dict(title='PE/PB历史百分位 (0%=最低, 100%=最高)', gridcolor='#f0f0f0'),
        yaxis=dict(title='数量', gridcolor='#f0f0f0'),
        legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
        font=dict(size=12),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False)


# ============================================================
# 新增: 回测摘要表格
# ============================================================

def _build_backtest_summary_table(result):
    """回测结果摘要HTML表格"""
    # 处理dict结构 (from run_swing_backtest)
    if isinstance(result, dict):
        stats = result.get('stats', {})
        trades = result.get('trades', [])
        n_trades = len([t for t in trades if t.direction == 'SELL'])

        rows_html = [f"""<tr>
            <td><strong>回测结果</strong></td>
            <td>{n_trades}</td>
            <td style="font-weight:600;">{stats.get('win_rate', 0):.1%}</td>
            <td>{stats.get('avg_return', 0):+.2%}</td>
            <td>{stats.get('total_return', 0):+.2%}</td>
            <td>{stats.get('sharpe', 0):.2f}</td>
            <td style="color:#e74c3c;">{stats.get('max_drawdown', 0):.1%}</td>
            <td>-</td>
            <td>-</td>
            <td>-</td>
        </tr>"""]
    else:
        # 原有的BacktestResult对象处理
        rows_html = []
        for hp in sorted(result.periods.keys()):
            ps = result.periods[hp]
            bench = result.benchmark_periods.get(hp)

            bench_avg = '-'
            bench_wr = '-'
            excess = '-'
            if bench and bench.total_count > 0:
                bench_avg = f'{bench.avg_return:+.2%}'
                bench_wr = f'{bench.win_rate:.1%}'
                excess_val = ps.avg_return - bench.avg_return
                excess_css = 'color:#27ae60;' if excess_val > 0 else 'color:#e74c3c;'
                excess = f'<span style="{excess_css}font-weight:600;">{excess_val:+.2%}</span>'

            rows_html.append(f"""<tr>
                <td><strong>{hp}日</strong></td>
                <td>{result.n_rebalances}</td>
                <td style="font-weight:600;">{ps.win_rate:.1%}</td>
                <td>{ps.avg_return:+.2%}</td>
                <td>{ps.median_return:+.2%}</td>
                <td>{ps.sharpe:.2f}</td>
                <td style="color:#e74c3c;">{ps.max_drawdown:.1%}</td>
                <td>{bench_wr}</td>
                <td>{bench_avg}</td>
                <td>{excess}</td>
            </tr>""")

    return f"""
    <table style="margin-bottom:20px;">
    <thead><tr>
        <th>持有周期</th><th>再平衡次数</th><th>策略胜率</th><th>策略均收益</th>
        <th>策略中位数</th><th>Sharpe</th><th>最大回撤</th>
        <th>基准胜率</th><th>基准均收益</th><th>超额收益</th>
    </tr></thead>
    <tbody>{''.join(rows_html)}</tbody>
    </table>
    """

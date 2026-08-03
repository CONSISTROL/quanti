"""
A股多因子量化选股 - 报告生成模块
终端格式化表格 + Plotly交互HTML报告
"""

import os
from datetime import datetime

import pandas as pd
import numpy as np
from factor_model import FACTOR_GROUPS
from report_echarts import echarts_script, UP, DOWN, GRID, TEXT, BLUE, ORANGE, GRAY


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
    """生成交互式HTML报告（ECharts图表）"""
    # 处理scored_df为None的情况 (个股回测模式)
    if scored_df is None:
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
            <div class="card-value">{f"{stats['profit_loss_ratio']:.2f}" if stats['avg_loss'] != 0 else '∞'}</div></div>
        <div class="card"><div class="card-label">最终资金</div>
            <div class="card-value" style="{ret_css}">¥{stats['final_value']:,.0f}</div></div>
    </div>""")

        # 收益曲线
        if equity_chart_html:
            html_parts.append(f"""
    <div class="chart-container" style="margin-bottom:20px;">
        {equity_chart_html}
    </div>""")

        # 操作记录表 (系统买卖信号: 价格/盈亏按信号日信号价口径)
        # 累计盈亏 = 信号日净值/初始资金-1 (与终端 print_trade_summary 同口径)
        nav_map = {}
        try:
            initial_cap = trade_result['initial_capital']
            for d, v in trade_result.get('equity_curve', []):
                nav_map[pd.Timestamp(d).strftime('%Y-%m-%d')] = v
        except Exception:
            initial_cap = 0
        if trades:
            html_parts.append("""
    <h2 style="margin:24px 0 12px;color:#1a1a2e;">📋 系统买卖信号记录</h2>
    <table style="margin-bottom:20px;">
    <thead><tr>
        <th>信号日</th><th>方向</th><th>代码</th><th>名称</th>
        <th>信号价</th><th>数量</th><th>金额</th><th>盈亏</th><th>累计盈亏</th><th>原因</th>
    </tr></thead>
    <tbody>""")
            for t in trades:
                dir_css = 'color:#27ae60;font-weight:600;' if t.direction == 'BUY' else 'color:#e74c3c;font-weight:600;'
                dir_label = '买入' if t.direction == 'BUY' else '卖出'
                # 信号口径: 价格 = 信号日收盘价, 盈亏 = 按信号价计算的收益 (非延迟成交时与执行口径相同)
                sig_price = getattr(t, 'signal_price', None) or t.price
                pnl_sys = getattr(t, 'pnl_signal', None)
                if pnl_sys is None:
                    pnl_sys = t.pnl_pct
                pnl_str = f'{pnl_sys:+.1%}' if t.direction == 'SELL' else '-'
                pnl_css = ''
                if t.direction == 'SELL':
                    pnl_css = 'color:#27ae60;' if pnl_sys > 0 else 'color:#e74c3c;'
                # 累计盈亏: 按信号日取净值 (原教旨回测中信号日=成交日, 即当日收盘净值)
                sig_date_str = t.signal_date.strftime('%Y-%m-%d')
                cum_nav = nav_map.get(sig_date_str) if initial_cap else None
                if cum_nav is None:
                    cum_str = '-'
                    cum_css = ''
                else:
                    cum_val = cum_nav / initial_cap - 1
                    cum_str = f'{cum_val:+.1%}'
                    cum_css = 'color:#27ae60;' if cum_val > 0 else 'color:#e74c3c;'
                html_parts.append(f"""<tr>
                    <td>{sig_date_str}</td>
                    <td style="{dir_css}">{dir_label}</td>
                    <td><strong>{t.code}</strong></td>
                    <td>{t.name}</td>
                    <td>{sig_price:.2f}</td>
                    <td>{t.shares}</td>
                    <td>¥{sig_price * t.shares:,.0f}</td>
                    <td style="{pnl_css}font-weight:600;">{pnl_str}</td>
                    <td style="{cum_css}font-weight:600;">{cum_str}</td>
                    <td>{t.reason}</td>
                </tr>""")
            html_parts.append("""
    </tbody></table>
    <p style="color:#999;font-size:12px;margin-top:-12px;">本表 = 系统买卖信号记录 — 价格/盈亏按<strong>信号日信号价口径</strong>(信号当日收盘价成交的收益, 与成交模式无关); 用户A按次日执行价成交的价格/盈亏见自选池轮动分析报告"用户A操作记录"</p>""")

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

    # === 表格列筛选 JS (行数>=10的表格自动启用) ===
    html_parts.append("""
<script>
/* 表格列筛选: 点击表头▾打开面板 (数值列=范围筛选, 枚举列=多选, 长文本列=包含搜索) */
(function () {
  'use strict';
  function normText(s) { return (s == null ? '' : String(s)).trim(); }
  function numVal(s) {
    if (s == null) return null;
    var t = String(s).replace(/[,¥\\s]/g, '');
    if (t === '' || t === '-') return null;
    if (t.charAt(t.length - 1) === '%') t = t.slice(0, -1);
    if (!/^[+-]?\\d*\\.?\\d+$/.test(t)) return null;
    return parseFloat(t);
  }
  function esc(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  document.querySelectorAll('table').forEach(function (tbl) {
    var thead = tbl.querySelector('thead'), tbody = tbl.querySelector('tbody');
    if (!thead || !tbody || tbody.rows.length < 10) return;
    var ths = Array.prototype.slice.call(thead.rows[0].cells);
    var rows = Array.prototype.slice.call(tbody.rows);
    var nCols = ths.length, states = [], allVals = [], c;

    /* 1. 列类型判定: 含单位(¥/%)且可数值 → 数值列; 唯一值<=40 → 多选; 纯数字 → 数值列; 其余 → 文本包含 */
    for (c = 0; c < nCols; c++) {
      var numCnt = 0, unitCnt = 0, tot = 0, seen = {}, vals = [];
      rows.forEach(function (r) {
        var td = r.cells[c]; if (!td) return;
        var t = normText(td.textContent);
        tot++;
        if (numVal(t) !== null) numCnt++;
        if (/[¥%]/.test(t)) unitCnt++;
        if (!(t in seen)) { seen[t] = 1; vals.push(t); }
      });
      var st;
      if (unitCnt > 0 && numCnt > 0 && numCnt >= tot * 0.4) {
        st = { type: 'num', min: null, max: null };
      } else if (vals.length <= 40) {
        st = { type: 'check', selected: new Set(vals) };
      } else if (numCnt >= tot * 0.7) {
        st = { type: 'num', min: null, max: null };
      } else {
        st = { type: 'text', keyword: '' };
      }
      st.col = c; st.active = false;
      states.push(st);
      allVals.push(vals);
    }

    /* 2. 表格上方状态条 + 下方分页控件 (行数>30自动分页) */
    var bar = document.createElement('div');
    bar.className = 'filter-bar';
    tbl.parentNode.insertBefore(bar, tbl);
    var pageSize = 30, page = 0, pager = null;
    if (rows.length > 30) {
      pager = document.createElement('div');
      pager.className = 'filter-pager';
      tbl.parentNode.insertBefore(pager, tbl.nextSibling);
    }
    function renderRows(visible) {
      var start = page * pageSize;
      var end = Math.min(start + pageSize, visible.length);
      rows.forEach(function (r) { r.style.display = 'none'; });
      for (var i = start; i < end; i++) visible[i].style.display = '';
      if (!pager) return;
      var totalPages = Math.max(1, Math.ceil(visible.length / pageSize));
      pager.innerHTML = '';
      var info = document.createElement('span');
      info.className = 'pager-info';
      info.textContent = (visible.length === 0 ? 0 : start + 1) + '-' + end
        + ' / ' + visible.length + ' 行 · 第 ' + (page + 1) + '/' + totalPages + ' 页';
      pager.appendChild(info);
      var prev = document.createElement('button');
      prev.textContent = '‹'; prev.disabled = page <= 0;
      prev.onclick = function () { page--; apply(); };
      pager.appendChild(prev);
      var next = document.createElement('button');
      next.textContent = '›'; next.disabled = page >= totalPages - 1;
      next.onclick = function () { page++; apply(); };
      pager.appendChild(next);
      var goto = document.createElement('span');
      goto.className = 'pager-goto';
      var input = document.createElement('input');
      input.type = 'number'; input.min = 1; input.max = totalPages;
      input.value = page + 1; input.title = '跳转到页码 (回车确认)';
      input.onkeydown = function (e) {
        if (e.key !== 'Enter') return;
        var v = parseInt(input.value, 10);
        if (!isNaN(v)) { page = Math.min(totalPages, Math.max(1, v)) - 1; apply(); }
      };
      goto.appendChild(input);
      var jbtn = document.createElement('button');
      jbtn.textContent = '跳转';
      jbtn.onclick = function () {
        var v = parseInt(input.value, 10);
        if (!isNaN(v)) { page = Math.min(totalPages, Math.max(1, v)) - 1; apply(); }
      };
      goto.appendChild(jbtn);
      pager.appendChild(goto);
      var sel = document.createElement('select');
      [10, 30, 50, 100].forEach(function (s) {
        var op = document.createElement('option');
        op.value = s; op.textContent = s + ' 行/页';
        if (s === pageSize) op.selected = true;
        sel.appendChild(op);
      });
      sel.onchange = function () { pageSize = parseInt(sel.value, 10); page = 0; apply(); };
      pager.appendChild(sel);
    }
    function rowShown(r) {
      for (var c2 = 0; c2 < nCols; c2++) {
        var st = states[c2]; if (!st.active) continue;
        var td = r.cells[c2], t = normText(td ? td.textContent : '');
        if (st.type === 'num') {
          var n = numVal(t);
          if (n === null) return false;
          if (st.min != null && n < st.min) return false;
          if (st.max != null && n > st.max) return false;
        } else if (st.type === 'check') {
          if (!st.selected.has(t)) return false;
        } else {
          if (st.keyword && t.indexOf(st.keyword) < 0) return false;
        }
      }
      return true;
    }
    function apply() {
      var visible = rows.filter(rowShown);
      if (pager) {
        var tp = Math.max(1, Math.ceil(visible.length / pageSize));
        if (page >= tp) page = tp - 1;
      }
      renderRows(visible);
      var act = states.filter(function (s) { return s.active; }).length;
      ths.forEach(function (th, idx) { th.classList.toggle('filter-active', states[idx].active); });
      if (act) {
        bar.innerHTML = '<span>已筛选 <b>' + visible.length + '</b> / ' + rows.length + ' 行</span>'
          + '<span class="filter-clear" title="恢复全部">清除全部筛选</span>';
        bar.style.display = 'flex';
        bar.querySelector('.filter-clear').onclick = function () {
          states.forEach(function (s) {
            s.active = false; s.min = null; s.max = null; s.keyword = '';
            if (s.type === 'check') s.selected = new Set(allVals[s.col]);
          });
          if (activeCol != null) closeFloat();
          apply();
        };
      } else { bar.style.display = 'none'; }
    }

    /* 3. 浮层面板 (fixed定位, 避开table的overflow裁剪) */
    var floatPanel = null, activeCol = null;
    function closeFloat() {
      if (floatPanel) { floatPanel.classList.remove('open'); floatPanel.innerHTML = ''; }
      activeCol = null;
    }
    function buildPanel(c) {
      var st = states[c], html = '';
      if (st.type === 'num') {
        html += '<div class="filter-num">最小值 <input class="mi" type="number" step="any" value="'
          + (st.min == null ? '' : st.min) + '"></div>'
          + '<div class="filter-num">最大值 <input class="ma" type="number" step="any" value="'
          + (st.max == null ? '' : st.max) + '"></div>';
      } else if (st.type === 'check') {
        html += '<input class="filter-input fq" placeholder="搜索值..."><div class="filter-scroll"></div>';
      } else {
        html += '<input class="filter-input ft" placeholder="包含文本..." value="' + esc(st.keyword || '') + '">';
      }
      html += '<div class="filter-actions"><button class="clr">清除本列</button></div>';
      floatPanel.innerHTML = html;

      if (st.type === 'num') {
        var mi = floatPanel.querySelector('.mi'), ma = floatPanel.querySelector('.ma');
        mi.oninput = function () {
          st.min = mi.value === '' ? null : parseFloat(mi.value);
          st.active = st.min != null || st.max != null; apply();
        };
        ma.oninput = function () {
          st.max = ma.value === '' ? null : parseFloat(ma.value);
          st.active = st.min != null || st.max != null; apply();
        };
      } else if (st.type === 'check') {
        var fq = floatPanel.querySelector('.fq');
        fq.oninput = function () { renderCheck(c, fq.value); };
        renderCheck(c, '');
      } else {
        var ft = floatPanel.querySelector('.ft');
        ft.oninput = function () { st.keyword = ft.value; st.active = !!st.keyword; apply(); };
      }
      floatPanel.querySelector('.clr').onclick = function () {
        st.active = false; st.min = null; st.max = null; st.keyword = '';
        if (st.type === 'check') st.selected = new Set(allVals[c]);
        buildPanel(c); apply(); closeFloat();
      };
    }
    function renderCheck(c, kw) {
      var st = states[c], box = floatPanel.querySelector('.filter-scroll');
      var html = '', i;
      allVals[c].forEach(function (v) {
        if (kw && v.indexOf(kw) < 0) return;
        html += '<label class="filter-opt"><input type="checkbox" data-v="' + esc(v) + '"'
          + (st.selected.has(v) ? ' checked' : '') + '>' + esc(v) + '</label>';
      });
      box.innerHTML = html;
      var chks = box.querySelectorAll('input');
      for (i = 0; i < chks.length; i++) {
        chks[i].onchange = function () {
          var v = this.getAttribute('data-v');
          if (this.checked) st.selected.add(v); else st.selected.delete(v);
          st.active = true;
          apply();
        };
      }
    }

    /* 4. 表头: ▾按钮 + 点击打开面板 */
    ths.forEach(function (th, c) {
      var btn = document.createElement('span');
      btn.className = 'filter-btn'; btn.textContent = '▾'; btn.title = '筛选此列';
      th.appendChild(btn);
      th.onclick = function (e) {
        e.stopPropagation();
        if (activeCol === c && floatPanel && floatPanel.classList.contains('open')) { closeFloat(); return; }
        if (!floatPanel) {
          floatPanel = document.createElement('div');
          floatPanel.className = 'filter-panel';
          document.body.appendChild(floatPanel);
        }
        activeCol = c;
        buildPanel(c);
        var r = btn.getBoundingClientRect();
        var pw = Math.min(220, window.innerWidth - 16);
        floatPanel.style.left = Math.max(4, Math.min(r.right - pw + 8, window.innerWidth - pw - 4)) + 'px';
        floatPanel.style.top = (r.bottom + 4) + 'px';
        floatPanel.classList.add('open');
      };
    });

    /* 5. 初始渲染 (分页显示第一页) */
    apply();

    document.addEventListener('click', function (e) {
      if (floatPanel && activeCol != null && !floatPanel.contains(e.target)) closeFloat();
    });
    window.addEventListener('scroll', function () { if (activeCol != null) closeFloat(); }, true);
    window.addEventListener('resize', function () { if (activeCol != null) closeFloat(); });
  });
})();
</script>
""")

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
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
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
  /* ---- 表格列筛选 ---- */
  .filter-btn {{
    cursor: pointer; user-select: none; font-size: 11px; margin-left: 4px; opacity: .45;
  }}
  th:hover .filter-btn, .filter-btn:hover, .filter-active .filter-btn {{ opacity: 1; }}
  .filter-active {{ background: #3a3a6e; }}
  .filter-panel {{
    position: fixed; min-width: 210px; max-width: 300px;
    background: #fff; color: #333; border: 1px solid #d5d9e2; border-radius: 8px;
    box-shadow: 0 6px 20px rgba(0,0,0,.18); z-index: 9999; padding: 8px;
    text-align: left; font-size: 12px; display: none;
  }}
  .filter-panel.open {{ display: block; }}
  .filter-scroll {{ max-height: 220px; overflow-y: auto; margin-top: 4px; }}
  .filter-opt {{ display: block; padding: 3px 6px; cursor: pointer; border-radius: 4px; white-space: nowrap; }}
  .filter-opt:hover {{ background: #f0f2f7; }}
  .filter-opt input {{ margin-right: 6px; vertical-align: middle; }}
  .filter-input {{
    width: 100%; font-size: 12px; padding: 4px 6px; border: 1px solid #c8cdd6;
    border-radius: 4px; margin-bottom: 4px; box-sizing: border-box;
  }}
  .filter-num {{
    display: flex; gap: 6px; align-items: center; font-size: 12px; margin-bottom: 4px;
  }}
  .filter-num input {{ width: 80px; font-size: 12px; padding: 3px 5px; border: 1px solid #c8cdd6; border-radius: 4px; }}
  .filter-actions {{ display: flex; gap: 6px; margin-top: 6px; }}
  .filter-actions button {{
    flex: 1; font-size: 11px; padding: 3px 0; cursor: pointer; border: 1px solid #c8cdd6;
    border-radius: 4px; background: #fff;
  }}
  .filter-actions button:hover {{ background: #f0f2f7; }}
  .filter-bar {{
    font-size: 12px; color: #555; margin: 6px 0; align-items: center; gap: 10px;
  }}
  .filter-clear {{
    cursor: pointer; color: #e74c3c; border: 1px solid #e74c3c; border-radius: 4px;
    padding: 1px 8px; font-size: 11px;
  }}
  .filter-clear:hover {{ background: #fdecea; }}
  .filter-pager {{
    display: flex; align-items: center; gap: 8px; margin: 8px 0 4px;
    font-size: 12px; color: #666;
  }}
  .filter-pager button {{
    min-width: 28px; height: 24px; cursor: pointer; border: 1px solid #c8cdd6;
    border-radius: 4px; background: #fff; font-size: 12px; color: #333;
  }}
  .filter-pager button:disabled {{ opacity: .4; cursor: default; }}
  .filter-pager button:not(:disabled):hover {{ background: #f0f2f7; }}
  .filter-pager select {{ font-size: 12px; padding: 2px 4px; border: 1px solid #c8cdd6; border-radius: 4px; }}
  .pager-goto input {{ width: 3.2em; font-size: 12px; padding: 2px 4px; border: 1px solid #c8cdd6; border-radius: 4px; margin-left: 6px; }}
  .pager-info {{ margin-right: 4px; }}
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
    """TOP N 平均因子雷达图 (ECharts)"""
    if 'composite_score' not in top_df.columns or not any(f'{g}_score' in top_df.columns for g in FACTOR_GROUPS):
        return '<p style="color:#999;text-align:center;padding:40px;">无因子数据</p>'
    top10 = top_df.head(10)
    categories = list(FACTOR_GROUPS.values())
    r_values = []
    for g in FACTOR_GROUPS:
        col = f'{g}_score'
        val = top10[col].mean() if col in top10.columns else 0
        r_values.append(round(val, 3) if pd.notna(val) else 0)

    option = {
        'tooltip': {'trigger': 'item', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                    'textStyle': {'color': '#1f2329'}},
        'radar': {
            'indicator': [{'name': c['label'], 'max': 1.0} for c in categories],
            'splitLine': {'lineStyle': {'color': GRID}},
            'axisLine': {'lineStyle': {'color': '#d9dde3'}},
            'axisLabel': {'color': TEXT},
        },
        'series': [{
            'type': 'radar',
            'data': [{
                'value': r_values,
                'name': 'TOP10 平均因子',
                'areaStyle': {'color': 'rgba(44,111,187,0.15)'},
                'lineStyle': {'color': BLUE, 'width': 2},
                'itemStyle': {'color': BLUE},
                'symbolSize': 6,
            }],
        }],
    }
    return echarts_script('radar_chart', option, 350)


def _plot_sector(top_df):
    """行业分布图 (ECharts横向条形)"""
    if 'sector' not in top_df.columns:
        return '<p style="color:#999;text-align:center;padding:40px;">无行业数据</p>'

    sector_counts = (top_df['sector']
                     .replace('未知', np.nan)
                     .dropna()
                     .value_counts()
                     .head(12))
    if sector_counts.empty:
        return '<p style="color:#999;text-align:center;padding:40px;">无有效行业分类</p>'

    option = {
        'tooltip': {'trigger': 'axis', 'axisPointer': {'type': 'shadow'},
                    'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                    'textStyle': {'color': '#1f2329'}},
        'grid': {'left': 110, 'right': 40, 'top': 20, 'bottom': 30},
        'xAxis': {'type': 'value', 'splitLine': {'lineStyle': {'color': GRID}},
                  'axisLabel': {'color': TEXT}},
        'yAxis': {'type': 'category', 'data': sector_counts.index.tolist(),
                  'axisLabel': {'color': TEXT, 'fontSize': 12}},
        'series': [{
            'type': 'bar', 'data': sector_counts.values.tolist(),
            'barMaxWidth': 20,
            'itemStyle': {'color': BLUE, 'borderRadius': [0, 4, 4, 0]},
            'label': {'show': True, 'position': 'right', 'color': TEXT, 'fontSize': 12},
        }],
    }
    return echarts_script('sector_chart', option, 350)


def _plot_score_distribution(scored_df, top_df):
    """综合得分分布直方图 (ECharts)"""
    if 'composite_score' not in scored_df.columns:
        return '<p style="color:#999;text-align:center;padding:40px;">无打分数据</p>'
    scores = scored_df['composite_score'].dropna()
    top_scores = top_df['composite_score'].dropna()

    bins = list(np.arange(0, 1.6, 0.03))
    hist_all, edges = np.histogram(scores, bins=bins)
    hist_top, _ = np.histogram(top_scores, bins=bins)
    labels = [f'{edges[i]:.2f}' for i in range(len(edges) - 1)]

    option = {
        'tooltip': {'trigger': 'axis', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                    'textStyle': {'color': '#1f2329'}},
        'legend': {'top': 0, 'textStyle': {'color': TEXT}},
        'grid': {'left': 50, 'right': 20, 'top': 36, 'bottom': 40},
        'xAxis': {'type': 'category', 'data': labels, 'axisLabel': {'color': TEXT, 'rotate': 45, 'fontSize': 10}},
        'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'color': GRID}},
                  'axisLabel': {'color': TEXT}},
        'series': [
            {'name': '全部股票', 'type': 'bar', 'data': hist_all.tolist(),
             'itemStyle': {'color': 'rgba(44,111,187,0.35)'}, 'barWidth': '98%'},
            {'name': 'TOP 入选', 'type': 'bar', 'data': hist_top.tolist(),
             'itemStyle': {'color': 'rgba(232,64,58,0.7)'}, 'barWidth': '98%'},
        ],
    }
    return echarts_script('dist_chart', option, 350)


def _plot_factor_scatter(scored_df, top_df, colors):
    """因子空间散点图: 价值 x 质量 (ECharts)"""
    if 'value_score' not in scored_df.columns or 'quality_score' not in scored_df.columns:
        return '<p style="color:#999;text-align:center;padding:40px;">无因子数据</p>'
    x = scored_df['value_score'].dropna()
    y = scored_df.loc[x.index, 'quality_score']
    top_codes = set(top_df['code'].astype(str))

    all_pts = [{'v': round(vx, 3), 'q': round(vy, 3), 'code': str(c)}
               for c, vx, vy in zip(x.index, x, y) if pd.notna(vy)]
    opt = {
        'tooltip': {'trigger': 'item', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                    'textStyle': {'color': '#1f2329'},
                    'formatter': "function(p){var d=p.data;return d.code+'<br/>价值: '+d.v+'<br/>质量: '+d.q;}"},
        'legend': {'top': 0, 'textStyle': {'color': TEXT}},
        'grid': {'left': 50, 'right': 20, 'top': 36, 'bottom': 40},
        'xAxis': {'type': 'value', 'name': '价值分', 'splitLine': {'lineStyle': {'color': GRID}},
                  'axisLabel': {'color': TEXT}},
        'yAxis': {'type': 'value', 'name': '质量分', 'splitLine': {'lineStyle': {'color': GRID}},
                  'axisLabel': {'color': TEXT}},
        'series': [
            {'name': '全部', 'type': 'scatter', 'data': [[p['v'], p['q']] for p in all_pts if p['code'] not in top_codes],
             'symbolSize': 7, 'itemStyle': {'color': 'rgba(44,111,187,0.4)'}},
            {'name': 'TOP 入选', 'type': 'scatter', 'data': [[p['v'], p['q']] for p in all_pts if p['code'] in top_codes],
             'symbolSize': 11, 'itemStyle': {'color': 'rgba(232,64,58,0.85)'}},
        ],
    }
    return echarts_script('scatter_chart', opt, 350)


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
    """资产类型分布饼图 (ECharts)"""
    if 'asset_type' not in scored_df.columns:
        return '<p style="color:#999;text-align:center;padding:40px;">无资产类型数据</p>'

    type_colors = {'stock': '#2c6fbb', 'etf': '#1ba27a', 'lof': '#f5a623'}
    labels = {'stock': '股票', 'etf': 'ETF', 'lof': 'LOF'}

    def pie_series(df, name, center):
        counts = df['asset_type'].value_counts()
        data = [{'name': labels.get(t, t), 'value': int(v),
                 'itemStyle': {'color': type_colors.get(t, GRAY)}}
                for t, v in counts.items()]
        return {'name': name, 'type': 'pie', 'radius': ['40%', '68%'], 'center': center,
                'data': data, 'label': {'color': TEXT}}

    option = {
        'tooltip': {'trigger': 'item', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                    'textStyle': {'color': '#1f2329'}},
        'legend': {'top': 0, 'textStyle': {'color': TEXT}},
        'series': [pie_series(scored_df, '全部', ['25%', '55%']),
                   pie_series(top_df, 'TOP N', ['75%', '55%'])],
    }
    return echarts_script('type_pie', option, 300)


def _plot_valuation_distribution(scored_df, top_df):
    """估值分位数分布直方图 (ECharts)"""
    if 'pe_percentile' not in scored_df.columns:
        return '<p style="color:#999;text-align:center;padding:40px;">无估值数据</p>'

    pe_all = scored_df['pe_percentile'].dropna()
    pe_top = top_df['pe_percentile'].dropna()
    if pe_all.empty:
        return '<p style="color:#999;text-align:center;padding:40px;">无有效估值分位数据</p>'

    bins = list(range(0, 101, 5))
    hist_all, _ = np.histogram(pe_all, bins=bins)
    hist_top, _ = np.histogram(pe_top, bins=bins)
    labels = [f'{bins[i]}-{bins[i+1]}' for i in range(len(bins) - 1)]

    option = {
        'tooltip': {'trigger': 'axis', 'backgroundColor': '#fff', 'borderColor': '#e5e8ec',
                    'textStyle': {'color': '#1f2329'}},
        'legend': {'top': 0, 'textStyle': {'color': TEXT}},
        'grid': {'left': 50, 'right': 20, 'top': 36, 'bottom': 40},
        'xAxis': {'type': 'category', 'data': labels, 'axisLabel': {'color': TEXT, 'rotate': 45, 'fontSize': 10}},
        'yAxis': {'type': 'value', 'splitLine': {'lineStyle': {'color': GRID}},
                  'axisLabel': {'color': TEXT}},
        'series': [
            {'name': '全部', 'type': 'bar', 'data': hist_all.tolist(),
             'itemStyle': {'color': 'rgba(44,111,187,0.35)'}},
            {'name': 'TOP 入选', 'type': 'bar', 'data': hist_top.tolist(),
             'itemStyle': {'color': 'rgba(232,64,58,0.7)'}},
        ],
    }
    return echarts_script('val_dist_chart', option, 300)


def _build_backtest_summary_table(result):
    """回测结果摘要HTML表格"""
    # 处理dict结构 (from run_swing_backtest)
    if isinstance(result, dict):
        stats = result.get('stats', {})
        trades = result.get('trades', [])
        sell_trades = [t for t in trades if t.direction == 'SELL']
        n_trades = len(sell_trades)
        avg_return = stats.get('avg_return')
        if avg_return is None:
            avg_return = np.mean([t.pnl_pct for t in sell_trades]) if sell_trades else 0.0

        rows_html = [f"""<tr>
            <td><strong>回测结果</strong></td>
            <td>{n_trades}</td>
            <td style="font-weight:600;">{stats.get('win_rate', 0):.1%}</td>
            <td>{avg_return:+.2%}</td>
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

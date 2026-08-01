"""
自选池轮动回测 — config.json watchlist 指定的自选股 (可含ETF/LOF)
策略: watchlist (强弱评分+单持仓满仓+卖弱买强)

用法: python run_test.py --module watchlist_backtest
"""
import os
import sys
import pickle

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

WATCH_NAMES = {
    '601857': '中国石油', '159381': '创业板AI ETF', '588170': '科创半导体ETF',
    '600547': '山东黄金', '513580': '恒生科技ETF',
}


def main(config=None):
    from main import load_config
    config = config or load_config()

    watchlist = [str(c).zfill(6) for c in config.get('watchlist', [])]
    if not watchlist:
        print('  ✗ config.json 未配置 watchlist')
        return 1

    # ---- 1. 加载数据: 股票 + ETF ----
    hist = pickle.load(open('cache/hist_batch_20260801.pkl', 'rb'))
    etf_path = 'cache/watchlist_etf.pkl'
    if os.path.exists(etf_path):
        etf = pickle.load(open(etf_path, 'rb'))
        for k, v in etf.items():
            hist[k] = v
        print(f'  ✅ 合并ETF数据: {list(etf.keys())}')

    # ---- 2. 预计算指标 (仅自选池) ----
    from data_fetcher import _code_pure
    from indicator_cache import _incremental_indicators
    from strategies import strategy_label

    precomputed = {}
    names = {}
    for code in watchlist:
        sina = None
        for k in hist:
            if _code_pure(k) == code:
                sina = k
                break
        if sina is None:
            print(f'  ⚠ {code} 无历史数据, 跳过')
            continue
        df = hist[sina]
        dates_arr = df['date'].values
        closes = df['close'].values.astype(float)
        volumes = df['volume'].values.astype(float) if 'volume' in df.columns else None
        highs = df['high'].values.astype(float) if 'high' in df.columns else None
        lows = df['low'].values.astype(float) if 'low' in df.columns else None
        if highs is None:
            highs = closes.copy()
        if lows is None:
            lows = closes.copy()
        all_dates = set(pd.Timestamp(d).strftime('%Y-%m-%d') for d in dates_arr)
        precomputed[code] = _incremental_indicators(closes, volumes, highs, lows, dates_arr, all_dates)
        names[code] = WATCH_NAMES.get(code, code)

    pool_desc = ', '.join(f'{c} {names.get(c, "")}' for c in precomputed)
    print(f'  自选池: {pool_desc}')
    print(f'  策略: {strategy_label("watchlist")}')

    # ---- 3. 构造scored_df (显示名称) ----
    scored_df = pd.DataFrame([
        {'code': c, 'name': names[c], 'rank': i + 1, 'composite_score': 1.0}
        for i, c in enumerate(precomputed)
    ])

    # ---- 4. 回测 ----
    tr_cfg = dict(config['trading'])
    tr_cfg['strategy'] = 'watchlist'
    tr_cfg['watchlist'] = list(precomputed.keys())
    tr_cfg['max_positions'] = 1
    tr_cfg['position_pct'] = 1.0
    tr_cfg['kelly_mode'] = False

    from trading_engine import run_swing_backtest, print_trade_summary
    start = config.get('backtest', {}).get('start_date', '2025-01-01')
    end = config.get('backtest', {}).get('end_date', '2026-08-02')

    result = run_swing_backtest(
        hist, scored_df, tr_cfg,
        start_date_str=start, end_date_str=end,
        precomputed=precomputed,
    )
    if result:
        print_trade_summary(result)

        # ---- 5. 每只个股单独回测 (独立满仓, 同一策略) ----
        import numpy as np
        per_stock = {}
        print('\n' + '═' * 90)
        print('  每只个股单独回测 (独立满仓, 同一策略)')
        print('═' * 90)
        print(f"  {'代码':<8} {'名称':<12} {'总收益':>9} {'年化':>8} {'Sharpe':>7} {'最大回撤':>8} {'交易':>4} {'胜率':>6}")
        print('  ' + '─' * 76)
        for code in precomputed:
            single_cfg = dict(tr_cfg)
            single_cfg['watchlist'] = [code]
            single_cfg['max_positions'] = 1
            single_cfg['position_pct'] = 1.0
            single_df = scored_df[scored_df['code'] == code]
            r = run_swing_backtest(
                hist, single_df, single_cfg,
                start_date_str=start, end_date_str=end,
                precomputed={code: precomputed[code]},
            )
            if r is None:
                print(f"  {code:<8} {names.get(code, ''):<12} 无数据")
                continue
            st = r['stats']
            per_stock[code] = r
            print(f"  {code:<8} {names.get(code, ''):<12} {st['total_return']:>+9.2%} "
                  f"{st['annual_return']:>+8.2%} {st['sharpe']:>7.2f} "
                  f"{st['max_drawdown']:>8.1%} {st['total_trades']:>4d} {st['win_rate']:>6.0%}")

        # ---- 6. 组合 vs 个股净值对比图 ----
        try:
            import plotly.graph_objects as go
            fig = go.Figure()
            # 组合净值
            eq = result.get('equity_curve', [])
            if eq:
                dates = [pd.Timestamp(e[0]) for e in eq]
                nav = [e[1] / result['initial_capital'] for e in eq]
                fig.add_trace(go.Scatter(x=dates, y=nav, name='组合轮动',
                                         line=dict(color='#e74c3c', width=3)))
            # 每只个股净值
            colors = ['#3498db', '#2ecc71', '#f39c12', '#9b59b6', '#1abc9c', '#e67e22']
            for i, (code, r) in enumerate(per_stock.items()):
                eq = r.get('equity_curve', [])
                if not eq:
                    continue
                dates = [pd.Timestamp(e[0]) for e in eq]
                nav = [e[1] / r['initial_capital'] for e in eq]
                fig.add_trace(go.Scatter(x=dates, y=nav, name=f'{code} {names.get(code, "")}',
                                         line=dict(color=colors[i % len(colors)], width=1.5)))
            fig.add_hline(y=1.0, line_dash='dash', line_color='#94a3b8', line_width=1)
            fig.update_layout(
                title='自选池: 组合轮动 vs 每只个股独立满仓净值对比',
                xaxis=dict(title='日期'), yaxis=dict(title='净值 (初始=1)'),
                height=450, margin=dict(t=50, b=30, l=50, r=20),
                legend=dict(orientation='h', y=1.12, x=0.5, xanchor='center'),
                font=dict(size=12),
            )
            from datetime import datetime
            perf_path = f'report_watchlist_perf_{datetime.now().strftime("%Y%m%d")}.html'
            fig.write_html(perf_path)
            print(f'\n  ✅ 对比图: {os.path.abspath(perf_path)}')
        except Exception as e:
            print(f'\n  ⚠ 对比图生成失败: {e}')

        # 生成HTML报告
        from report_generator import generate_html_report
        from datetime import datetime
        output_path = f'report_watchlist_{datetime.now().strftime("%Y%m%d")}.html'
        try:
            actual_path = generate_html_report(
                scored_df, 5, {}, output_path,
                backtest_result=result, trade_result=result,
            )
            print(f'\n  ✅ HTML报告: {os.path.abspath(actual_path)}')
        except Exception as e:
            print(f'\n  ⚠ HTML报告生成失败: {e}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

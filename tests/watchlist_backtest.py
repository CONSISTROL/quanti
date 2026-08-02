"""
自选池轮动回测 — config.json watchlist 指定的自选股 (可含ETF/LOF)
策略: watchlist (强弱评分+单持仓满仓+卖弱买强)

用法: python run_test.py --module watchlist_backtest
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

WATCH_NAMES = {
    '601857': '中国石油',
    '159381': '创业板AI ETF',
    '588170': '科创半导体ETF',
    '600547': '山东黄金',
    '513580': '恒生科技ETF',
    '518880': '黄金ETF华安',
    '159941': '纳指ETF广发',
    '160723': '嘉实原油LOF'
}


def main(config=None):
    from main import load_config
    config = config or load_config()

    watchlist = [str(c).zfill(6) for c in config.get('watchlist', [])]
    if not watchlist:
        print('  ✗ config.json 未配置 watchlist')
        return 1

    # ---- 1. 加载数据: 按 config data.source 选择数据源 ----
    data_cfg = config.get('data', {})
    from data_sources import get_data_source, data_source_label
    source_name = data_cfg.get('source', 'quantdash')
    ds = get_data_source(source_name)
    print(f'  数据源: {data_source_label(source_name)}')
    hist = ds.fetch_watchlist_data(
        watchlist, cache_dir='cache',
        use_cache=True, max_bars=data_cfg.get('hist_days', 1200))
    if not hist:
        print(f'  ✗ 数据源 {source_name} 拉取失败')
        return 1

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
    print(f'  策略: {strategy_label(config.get("trading", {}).get("strategy", "watchlist"))}')

    # ---- 3. 构造scored_df (显示名称) ----
    scored_df = pd.DataFrame([
        {'code': c, 'name': names[c], 'rank': i + 1, 'composite_score': 1.0}
        for i, c in enumerate(precomputed)
    ])

    # ---- 4. 回测 ----
    tr_cfg = dict(config['trading'])
    tr_cfg['strategy'] = tr_cfg.get('strategy', 'watchlist')  # 用config策略 (watchlist/watchlist_weekly)
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

        # ---- 6. ECharts可视化报告 (净值对比+收益柱状+K线含买卖点) ----
        from data_fetcher import _code_pure
        sina_map = {_code_pure(k): k for k in hist}
        try:
            from report_echarts import generate_echarts_report
            from datetime import datetime
            combo = {
                'eq': result.get('equity_curve', []),
                'initial': result['initial_capital'],
                'stats': result['stats'],
                'trades': result['trades'],
                'name': '组合轮动',
            }
            per = {}
            for code, r in per_stock.items():
                sina = sina_map.get(code)
                per[code] = {
                    'name': names.get(code, code),
                    'eq': r.get('equity_curve', []),
                    'initial': r['initial_capital'],
                    'stats': r['stats'],
                    'trades': r['trades'],
                    'df': hist.get(sina),
                    'precomputed': precomputed,
                }
            echarts_path = f'report_watchlist_echarts_{datetime.now().strftime("%Y%m%d")}.html'
            generate_echarts_report(combo, per, echarts_path)
            print(f'\n  ✅ ECharts报告: {os.path.abspath(echarts_path)}')
        except Exception as e:
            import traceback
            print(f'\n  ⚠ ECharts报告生成失败: {e}')
            traceback.print_exc()

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

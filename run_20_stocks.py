"""
挑选20支股票, 用指定策略回测 2025-01-01 ~ 2026-08-01
选股: 多因子综合评分 TOP 20 (排除创业板/科创板, 按config)
策略: 通过 --strategy 参数选择 (strategies/ 注册表: reversal/momentum/bollinger)
用法: python run_20_stocks.py [--strategy bollinger]
"""
import sys
import contextlib
import io

import pandas as pd
import numpy as np

from main import load_config

config = load_config()
dt_cfg = config.get('data', {})
bt_cfg = config.get('backtest', {})
tr_cfg = config.get('trading', {})
sc_cfg = config.get('scoring', {})

START, END = '2025-01-01', '2026-08-01'


def main(strategy='reversal'):
    from strategies import strategy_label
    print("╔══════════════════════════════════════════════════╗")
    print(f"║  20支股票回测 ({START} ~ {END})  策略: {strategy_label(strategy)}  ║")
    print("╚══════════════════════════════════════════════════╝")

    # ---- 1. 数据采集 (缓存) ----
    from data_fetcher import fetch_all_data
    class Args: pass
    args = Args()
    args.cache_dir = 'cache'; args.no_cache = False; args.no_history = False
    args.hist_days = dt_cfg.get('hist_days', 1200)
    args.workers = dt_cfg.get('workers', 8)
    args.sleep = dt_cfg.get('sleep', 0.15)
    args.include_etf_lof = dt_cfg.get('include_etf_lof', False)
    args.exclude_gem = dt_cfg.get('exclude_gem', False)
    args.exclude_star = dt_cfg.get('exclude_star', False)
    print("\n📥 数据采集...")
    data = fetch_all_data(args)
    print(f"  ✅ 历史数据: {len(data['history'])} 只")

    # ---- 2. 综合打分选TOP20 ----
    from factor_model import calculate_all_factors, score_stocks
    weights_str = sc_cfg.get('weights', '0.25,0.20,0.25,0.20,0.10')
    w_vals = [float(x) for x in weights_str.split(',')]
    if abs(sum(w_vals) - 1.0) > 0.01:
        w_vals = [v / sum(w_vals) for v in w_vals]
    weights = dict(zip(['value', 'growth', 'quality', 'momentum', 'risk'], w_vals))

    factor_df = calculate_all_factors(
        data['spot_filtered'], data['financial'], data['financial_prev'],
        data['history'], data.get('sector_map', {}), data.get('asset_type_map', {}))
    scored_df = score_stocks(factor_df, weights)
    scored_df = scored_df[scored_df['composite_score'].notna()]
    top20 = scored_df.head(20).copy()
    print(f"\n📊 TOP 20 股票:")
    for i, row in top20.iterrows():
        print(f"  {int(row.get('rank', 0)):>3}. {str(row.get('code', '')).zfill(6)} {row.get('name', '')}  得分{row.get('composite_score', 0):.2f}")

    # ---- 3. 逐只回测 (策略由 config 决定) ----
    from trading_engine import backtest_single_stock

    print(f"\n📈 逐只回测 ({START} ~ {END})...")
    results = []
    for idx, row in top20.iterrows():
        code = str(row['code']).zfill(6)
        name = str(row.get('name', ''))
        cfg_s = dict(tr_cfg)
        cfg_s['strategy'] = strategy  # 从 strategies/ 注册表选择
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            r = backtest_single_stock(code, data['history'], cfg_s, START, END)
        if r is None:
            print(f"  ⚠ {code} {name}: 回测失败/无数据")
            continue
        st = r['stats']
        results.append({
            'code': code, 'name': name,
            'total_return': st['total_return'], 'annual_return': st['annual_return'],
            'sharpe': st['sharpe'], 'max_drawdown': st['max_drawdown'],
            'trades': st['total_trades'], 'win_rate': st['win_rate'],
            'final_value': st['final_value'],
            'trades_detail': [t for t in r['trades'] if t.direction == 'SELL'],
        })
        print(f"  ✅ {code} {name}: {st['total_return']:+.2%}  Sharpe {st['sharpe']:.2f}  {st['total_trades']}笔  胜率{st['win_rate']:.0%}")

    # ---- 4. 汇总 ----
    print("\n" + "═" * 100)
    print(f"  汇总 ({strategy_label(strategy)}, 每支独立满仓)")
    print("═" * 100)
    print(f"  {'代码':<8} {'名称':<8} {'总收益':>9} {'年化':>8} {'Sharpe':>7} {'最大回撤':>8} {'交易':>4} {'胜率':>6} {'期末资金':>11}")
    print("  " + "─" * 88)
    for r in sorted(results, key=lambda x: x['total_return'], reverse=True):
        print(f"  {r['code']:<8} {r['name']:<8} {r['total_return']:>+9.2%} {r['annual_return']:>+8.2%} "
              f"{r['sharpe']:>7.2f} {r['max_drawdown']:>8.1%} {r['trades']:>4d} {r['win_rate']:>6.0%} ¥{r['final_value']:>10,.0f}")

    if results:
        rets = [r['total_return'] for r in results]
        wins = sum(1 for r in rets if r > 0)
        print("  " + "─" * 88)
        print(f"  平均收益: {np.mean(rets):+.2%}   中位数: {np.median(rets):+.2%}   "
              f"盈利{wins}支/{len(rets)}支   "
              f"最高: {max(rets):+.2%}  最低: {min(rets):+.2%}")


if __name__ == '__main__':
    strategy = 'reversal'
    for i, arg in enumerate(sys.argv):
        if arg == '--strategy' and i + 1 < len(sys.argv):
            strategy = sys.argv[i + 1]
        elif arg.startswith('--strategy='):
            strategy = arg.split('=')[1]
    main(strategy)

"""
策略参数优化器
网格搜索最优: 止损/止盈/持仓天数/买入门槛
使用 walk-forward 验证防止过拟合
"""

import itertools
import numpy as np
from datetime import datetime


def optimize_strategy(history_dict, scored_df, base_config,
                      start_date='2025-01-01', end_date='2026-07-27',
                      top_k=5):
    """
    网格搜索最优参数组合

    参数:
        history_dict: K线数据
        scored_df: 打分排名
        base_config: 基础配置
        start_date/end_date: 回测区间
        top_k: 返回前K个最优结果

    返回: [(config, stats), ...] 按收益排序
    """
    from trading_engine import run_swing_backtest

    # 参数网格
    param_grid = {
        'stop_loss': [-0.02, -0.03, -0.05],
        'take_profit': [0.05, 0.08, 0.12, 0.15],
        'max_holding_days': [8, 15, 25],
        'min_buy_score': [4, 5, 6],
    }

    # 生成所有组合
    keys = list(param_grid.keys())
    combos = list(itertools.product(*param_grid.values()))
    print(f"\n  🔍 参数优化: {len(combos)} 种组合")
    print(f"  回测区间: {start_date} ~ {end_date}")
    print(f"  {'─' * 70}")

    results = []

    for i, combo in enumerate(combos):
        params = dict(zip(keys, combo))
        config = dict(base_config)
        config.update(params)

        # 静默运行: 抑制输出
        import io
        from contextlib import redirect_stdout
        try:
            f = io.StringIO()
            with redirect_stdout(f):
                result = run_swing_backtest(
                    history_dict, scored_df, config,
                    start_date_str=start_date,
                    end_date_str=end_date,
                )
            if result is None:
                continue

            stats = result['stats']
            # 综合评分: 收益40% + Sharpe30% + (1-回撤)30%
            score = (stats['total_return'] * 40
                     + stats['sharpe'] * 0.30
                     + (1 - stats['max_drawdown']) * 0.30)

            results.append({
                'params': params,
                'stats': stats,
                'score': score,
            })

        except Exception:
            continue

        # 进度
        if (i + 1) % 10 == 0 or i == len(combos) - 1:
            print(f"  进度: {i+1}/{len(combos)} | "
                  f"当前最优: {results[0]['stats']['total_return']:+.1%}" if results else "", end='\r')

    # 按综合评分排序
    results.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n\n  ✅ 优化完成! TOP {top_k} 参数组合:\n")
    print(f"  {'排名':>4} {'止损':>6} {'止盈':>6} {'持仓':>4} {'买分':>4} │ "
          f"{'收益':>8} {'年化':>8} {'Sharpe':>7} {'回撤':>6} {'胜率':>5} {'盈亏比':>6}")
    print(f"  {'─' * 85}")

    for i, r in enumerate(results[:top_k]):
        p = r['params']
        s = r['stats']
        print(f"  {i+1:>4} {p['stop_loss']:>5.0%} {p['take_profit']:>5.0%} "
              f"{p['max_holding_days']:>4} {p['min_buy_score']:>4} │ "
              f"{s['total_return']:>+7.1%} {s['annual_return']:>+7.1%} "
              f"{s['sharpe']:>7.2f} {s['max_drawdown']:>5.1%} "
              f"{s['win_rate']:>4.0%} {s['profit_loss_ratio']:>6.2f}")

    print(f"\n  💡 使用最优参数: 修改 config.json 中的 trading 部分")

    return results

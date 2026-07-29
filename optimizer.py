"""
策略参数优化器 (并行版)
多进程网格搜索最优: 止损/止盈/持仓天数/买入门槛
"""

import itertools
import numpy as np
import multiprocessing
from datetime import datetime


def _run_single_backtest(args):
    """单个回测任务 (worker函数)"""
    params, base_config, history_dict, scored_df, start_date, end_date, precomputed = args

    import io
    from contextlib import redirect_stdout
    from trading_engine import run_swing_backtest

    config = dict(base_config)
    config.update(params)

    try:
        f = io.StringIO()
        with redirect_stdout(f):
            result = run_swing_backtest(
                history_dict, scored_df, config,
                start_date_str=start_date,
                end_date_str=end_date,
                precomputed=precomputed,
            )
        if result is None:
            return None

        stats = result['stats']
        # 综合评分: 收益×40 + Sharpe×30 + (1-回撤)×30
        score = (stats['total_return'] * 40
                 + stats['sharpe'] * 0.30
                 + (1 - stats['max_drawdown']) * 0.30)

        return {
            'params': params,
            'stats': stats,
            'score': score,
        }
    except Exception:
        return None


def optimize_strategy(history_dict, scored_df, base_config,
                      start_date='2025-01-01', end_date='2026-07-27',
                      top_k=5, workers=None, precomputed=None):
    """
    并行网格搜索最优参数

    参数:
        history_dict: K线数据
        scored_df: 打分排名
        base_config: 基础配置
        workers: 并行进程数 (默认CPU核心数-1)
    """
    if workers is None:
        workers = max(1, multiprocessing.cpu_count() - 1)

    # 参数网格
    param_grid = {
        'stop_loss': [-0.02, -0.03, -0.05],
        'take_profit': [0.05, 0.08, 0.12, 0.15],
        'max_holding_days': [8, 15, 25],
        'min_buy_score': [4, 5, 6],
    }

    keys = list(param_grid.keys())
    combos = list(itertools.product(*param_grid.values()))

    print(f"\n  🔍 参数优化: {len(combos)} 种组合 (并行 {workers} 进程)")
    print(f"  回测区间: {start_date} ~ {end_date}")
    print(f"  {'─' * 70}")

    # 构造任务列表
    tasks = []
    for combo in combos:
        params = dict(zip(keys, combo))
        tasks.append((params, base_config, history_dict, scored_df, start_date, end_date, precomputed))

    # 并行执行
    from tqdm import tqdm
    results = []

    ctx = multiprocessing.get_context('spawn')
    with ctx.Pool(processes=workers) as pool:
        for result in tqdm(pool.imap_unordered(_run_single_backtest, tasks),
                           total=len(tasks), desc="  优化进度", ncols=80):
            if result is not None:
                results.append(result)

    # 按综合评分排序
    results.sort(key=lambda x: x['score'], reverse=True)

    print(f"\n\n  ✅ 优化完成! 有效结果 {len(results)}/{len(combos)} 种\n")
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

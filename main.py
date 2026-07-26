#!/usr/bin/env python3
"""
A股多因子量化选股系统 v3.0
========================================
基于五大因子（价值/成长/质量/动量/风险）的量化选股模型
支持: 个股 + ETF/LOF | 买卖参考价 | 策略回测
数据源: AkShare (免费)  |  输出: 终端报告 + HTML可视化

用法:
    python main.py --no_history              # 快速模式: 仅价值+成长+质量
    python main.py                           # 完整模式: 全5因子
    python main.py --include_etf_lof         # 含ETF/LOF
    python main.py --price_targets           # 含买卖参考价
    python main.py --backtest                # 含策略回测
    python main.py --include_etf_lof --price_targets --backtest  # 全功能

免责声明: 本工具仅供学习研究，不构成投资建议。
"""

import argparse
import sys
import os
import json
from datetime import datetime


def load_config():
    """
    从 config.json 加载配置

    优先级: 命令行参数 > config.json > 默认值
    config.json 中的 ai 配置会自动写入环境变量
    """
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    if not os.path.exists(config_path):
        return {}

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        print(f"  ⚠ 读取 config.json 失败: {e}")
        return {}

    # AI 配置 → 环境变量 (供 backtest.py 读取)
    ai = config.get('ai', {})
    if ai.get('api_key'):
        os.environ.setdefault('ANTHROPIC_AUTH_TOKEN', ai['api_key'])
    if ai.get('base_url'):
        os.environ.setdefault('ANTHROPIC_BASE_URL', ai['base_url'])
    if ai.get('model'):
        os.environ.setdefault('ANTHROPIC_MODEL', ai['model'])

    return config


def parse_args(config=None):
    """解析命令行参数 (config.json 的值作为默认值)"""
    if config is None:
        config = {}

    bt_cfg = config.get('backtest', {})
    sc_cfg = config.get('scoring', {})
    dt_cfg = config.get('data', {})
    parser = argparse.ArgumentParser(
        description='A股多因子量化选股系统 v3.0',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --no_history                           # 快速模式 (1-2分钟)
  python main.py                                        # 完整模式 (含动量/风险)
  python main.py --include_etf_lof --no_history         # 含ETF/LOF
  python main.py --price_targets --no_history            # 含买卖参考价
  python main.py --backtest --no_history                 # 含策略回测
  python main.py --top 50 --min_score 0.5               # TOP50, 最低得分0.5
  python main.py --weights 0.3,0.15,0.3,0.15,0.1        # 自定义权重
  python main.py --include_etf_lof --price_targets --backtest --top 50  # 全功能

过滤规则: 自动排除ST、停牌、北交所、亏损股

⚠️  免责声明: 本工具仅供学习研究，不构成投资建议。
        """
    )

    # ---- 基础参数 ----
    parser.add_argument('--top', type=int, default=sc_cfg.get('top', 30),
                        help=f'展示前N只证券 (默认: {sc_cfg.get("top", 30)})')
    parser.add_argument('--min_score', type=float, default=sc_cfg.get('min_score', 0),
                        help=f'最低综合得分 (默认: {sc_cfg.get("min_score", 0)})')
    parser.add_argument('--weights', type=str, default=sc_cfg.get('weights', '0.25,0.20,0.25,0.20,0.10'),
                        help=f'因子权重 (默认: {sc_cfg.get("weights", "0.25,0.20,0.25,0.20,0.10")})')
    parser.add_argument('--output', type=str, default=None,
                        help='HTML报告输出路径 (默认: report_YYYYMMDD.html)')
    parser.add_argument('--no_html', action='store_true',
                        help='不生成HTML报告，仅终端输出')
    parser.add_argument('--no_cache', action='store_true',
                        help='不使用缓存，强制重新获取数据')
    parser.add_argument('--no_history', action='store_true',
                        help='跳过历史K线获取 (快速模式: 仅价值+成长+质量因子)')
    parser.add_argument('--workers', type=int, default=dt_cfg.get('workers', 8),
                        help=f'历史数据并发进程数 (默认: {dt_cfg.get("workers", 8)})')
    parser.add_argument('--sleep', type=float, default=dt_cfg.get('sleep', 0.15),
                        help=f'API请求间隔秒数 (默认: {dt_cfg.get("sleep", 0.15)})')
    parser.add_argument('--cache_dir', type=str, default='cache',
                        help='缓存目录 (默认: cache)')

    # ---- ETF/LOF ----
    parser.add_argument('--include_etf_lof', action='store_true',
                        default=dt_cfg.get('include_etf_lof', False),
                        help='将ETF和LOF纳入选股池')

    # ---- 买卖参考价 ----
    parser.add_argument('--price_targets', action='store_true',
                        help='计算买卖参考价 (基于估值分位数+技术面)')

    # ---- 回测 ----
    parser.add_argument('--backtest', action='store_true',
                        help='运行策略回测 (Walk-forward)')
    parser.add_argument('--backtest_top', type=int, default=bt_cfg.get('top_n', 30),
                        help=f'回测每周期选股数 (默认: {bt_cfg.get("top_n", 30)})')
    parser.add_argument('--backtest_periods', type=str,
                        default=bt_cfg.get('holding_periods', '5,10,20,60'),
                        help=f'回测持有周期 (默认: {bt_cfg.get("holding_periods", "5,10,20,60")})')
    parser.add_argument('--backtest_rebalance', type=int,
                        default=bt_cfg.get('rebalance_days', 60),
                        help=f'回测再平衡间隔天数 (默认: {bt_cfg.get("rebalance_days", 60)})')
    parser.add_argument('--strategy', type=str,
                        default=bt_cfg.get('strategy', 'composite'),
                        choices=['momentum', 'reversion', 'trend', 'low_vol', 'composite'],
                        help=f'回测策略 (默认: {bt_cfg.get("strategy", "composite")})')
    parser.add_argument('--compare_strategies', action='store_true',
                        help='运行全部内置策略并对比')
    parser.add_argument('--no_regime_filter', action='store_true',
                        default=not bt_cfg.get('use_regime_filter', True),
                        help='关闭市场趋势过滤')
    parser.add_argument('--ai_key', type=str, default=None,
                        help='AI API Key (也可在config.json中配置)')

    # ---- 历史数据天数 ----
    parser.add_argument('--hist_days', type=int, default=dt_cfg.get('hist_days', 300),
                        help=f'历史K线天数 (默认: {dt_cfg.get("hist_days", 300)})')

    # ---- 指定日期回测 ----
    parser.add_argument('--backtest_date', type=str, default=None,
                        help='模拟指定日期的选股推荐 (格式: YYYY-MM-DD 或 YYYYMMDD)')

    return parser.parse_args()


def validate_weights(weight_str, no_history=False):
    """解析并验证因子权重参数"""
    keys = ['value', 'growth', 'quality', 'momentum', 'risk']
    try:
        values = [float(x) for x in weight_str.split(',')]
        if len(values) != 5:
            raise ValueError("需要恰好5个权重值")
        total = sum(values)
        if abs(total - 1.0) > 0.01:
            print(f"  ⚠ 权重之和 = {total:.2f} (将按比例归一化)")
            values = [v / total for v in values]
        weights = dict(zip(keys, values))

        # 快速模式: 将动量/风险权重重新分配到价值+质量
        if no_history and (weights['momentum'] > 0 or weights['risk'] > 0):
            reallocated = weights['momentum'] + weights['risk']
            weights['momentum'] = 0
            weights['risk'] = 0
            # 按比例分配给价值、成长、质量
            base = weights['value'] + weights['growth'] + weights['quality']
            if base > 0:
                weights['value'] += reallocated * weights['value'] / base
                weights['growth'] += reallocated * weights['growth'] / base
                weights['quality'] += reallocated * weights['quality'] / base
            print(f"  ⚠ 快速模式: 动量/风险权重已重分配 → "
                  f"价值{weights['value']:.0%} / 成长{weights['growth']:.0%} / "
                  f"质量{weights['quality']:.0%}")

        return weights
    except ValueError as e:
        print(f"  ✗ 权重格式错误: {e}")
        print(f"    正确格式: --weights 0.25,0.20,0.25,0.20,0.10")
        sys.exit(1)


def _run_backtest_date(args, data, weights, scored_df):
    """
    模拟指定日期的选股推荐，并验证前瞻收益

    返回: (simulated_scored_df, target_date) 或 (None, None)
    """
    import numpy as np
    import pandas as pd
    from datetime import datetime as dt_cls

    # 解析日期
    date_str = args.backtest_date.replace('-', '').replace('/', '')
    try:
        target_date = dt_cls.strptime(date_str, '%Y%m%d')
    except ValueError:
        print(f"  ✗ 日期格式错误: {args.backtest_date}，请使用 YYYY-MM-DD 或 YYYYMMDD")
        return None, None, None, None

    target_ts = pd.Timestamp(target_date)
    history_dict = data['history']

    if not history_dict:
        print(f"  ✗ 无历史K线数据")
        return None, None, None, None

    print("\n" + "━" * 52)
    print(f"  🕰️  模拟选股: 假如今天是 {target_date.strftime('%Y-%m-%d')}")
    print("━" * 52)

    # ---- 截断K线到指定日期 ----
    truncated = {}
    for sina_code, hist in history_dict.items():
        if hist is None or 'date' not in hist.columns:
            continue
        mask = hist['date'] <= target_ts
        sub = hist[mask]
        if len(sub) >= 30:
            truncated[sina_code] = sub

    print(f"  截断到 {target_date.strftime('%Y-%m-%d')} 后有 {len(truncated)} 只证券有足够数据")

    if len(truncated) < args.top:
        print(f"  ✗ 数据不足")
        return None, None, None, None

    # ---- 重新计算因子 ----
    from factor_model import calculate_all_factors, score_stocks

    factor_df_sim = calculate_all_factors(
        spot_filtered=data['spot_filtered'],
        financial_df=data['financial'],
        financial_prev_df=data['financial_prev'],
        history_dict=truncated,
        sector_map=data.get('sector_map', {}),
        asset_type_map=data.get('asset_type_map', {}),
    )

    scored_sim = score_stocks(factor_df_sim, weights)
    top_picks = scored_sim.head(args.top)

    # ---- 显示推荐列表 ----
    print(f"\n  📋 {target_date.strftime('%Y-%m-%d')} 推荐买入 TOP {args.top}:\n")
    print(f"  {'排名':>4}  {'代码':<8} {'名称':<10} {'现价':>8} {'综合分':>7} "
          f"{'动量':>6} {'风险':>6} {'5日后':>8} {'10日后':>8} {'20日后':>8} {'60日后':>8}")
    print("  " + "─" * 95)

    # ---- 计算前瞻收益（验证推荐） ----
    stock_details = []
    for i, (_, row) in enumerate(top_picks.iterrows()):
        code = str(row.get('code', '')).zfill(6)
        name = str(row.get('name', ''))
        score = row.get('composite_score', 0)

        mom = row.get('momentum_score', np.nan)
        risk = row.get('risk_score', np.nan)

        # 找到这只股票的完整K线，算前瞻收益
        sina_code = None
        for s in history_dict:
            pure = s[2:] if s[:2] in ('sh', 'sz', 'bj') else s
            if pure == code:
                sina_code = s
                break

        fwd = {}
        price_at_date = np.nan
        if sina_code and sina_code in history_dict:
            full_hist = history_dict[sina_code]
            if 'date' in full_hist.columns and 'close' in full_hist.columns:
                closes = full_hist['close'].values
                dates = full_hist['date'].values
                # 找到指定日期的索引
                mask = full_hist['date'] <= target_ts
                if mask.sum() > 0:
                    buy_idx = mask.sum() - 1
                    price_at_date = closes[buy_idx]

                    for days in [5, 10, 20, 60]:
                        sell_idx = buy_idx + days
                        if sell_idx < len(closes):
                            fwd[days] = (closes[sell_idx] / price_at_date - 1)

        def _fmt_fwd(d):
            v = fwd.get(d)
            if v is None:
                return '  -'
            css = '+' if v >= 0 else ''
            return f'{css}{v:.2%}'

        def _fmt_score(v):
            return f'{v:>6.2f}' if pd.notna(v) else '     -'

        price_str = f'{price_at_date:>8.2f}' if not np.isnan(price_at_date) else '       -'

        print(f"  {i+1:>4}  {code:<8} {name:<10} {price_str} {score:>7.2f} "
              f"{_fmt_score(mom)} {_fmt_score(risk)} "
              f"{_fmt_fwd(5):>8} {_fmt_fwd(10):>8} {_fmt_fwd(20):>8} {_fmt_fwd(60):>8}")

        stock_details.append({
            'rank': i + 1,
            'code': code,
            'name': name,
            'price': price_at_date,
            'score': score,
            'fwd_5': fwd.get(5),
            'fwd_10': fwd.get(10),
            'fwd_20': fwd.get(20),
            'fwd_60': fwd.get(60),
        })

    # ---- 汇总统计 ----
    print("  " + "─" * 95)

    fwd_summary = []
    for days in [5, 10, 20, 60]:
        rets = []
        for _, row in top_picks.iterrows():
            code = str(row.get('code', '')).zfill(6)
            sina_code = None
            for s in history_dict:
                pure = s[2:] if s[:2] in ('sh', 'sz', 'bj') else s
                if pure == code:
                    sina_code = s
                    break
            if sina_code and sina_code in history_dict:
                full_hist = history_dict[sina_code]
                closes = full_hist['close'].values
                mask = full_hist['date'] <= target_ts
                if mask.sum() > 0:
                    buy_idx = mask.sum() - 1
                    sell_idx = buy_idx + days
                    if sell_idx < len(closes) and closes[buy_idx] > 0:
                        rets.append(closes[sell_idx] / closes[buy_idx] - 1)

        if rets:
            avg = np.mean(rets)
            wr = sum(1 for r in rets if r > 0) / len(rets)
            win_n = sum(1 for r in rets if r > 0)
            print(f"  {days:>2}日后汇总: 平均收益 {avg:+.2%} | 胜率 {wr:.0%} ({win_n}/{len(rets)})")
            fwd_summary.append({
                'days': days,
                'avg_return': avg,
                'win_rate': wr,
                'win_count': win_n,
                'total_count': len(rets),
            })

    print(f"\n  ⚠️  以上基于历史数据模拟，不构成投资建议。")
    print()

    return scored_sim, target_date, fwd_summary, stock_details


def main():
    config = load_config()
    args = parse_args(config)

    # 回测、买卖参考价、指定日期选股 都需要历史数据
    needs_history = args.backtest or args.price_targets or args.backtest_date
    if needs_history:
        if args.no_history:
            print("  ⚠ --backtest/--price_targets/--backtest_date 需要历史K线，已自动关闭 --no_history")
            args.no_history = False
        # 回测/估值需要更长的历史
        if args.hist_days < 500:
            args.hist_days = 1200
            print(f"  ⚠ 已自动将历史天数调整为 {args.hist_days} 天")

    weights = validate_weights(args.weights, args.no_history)

    # 解析回测周期
    backtest_periods = [int(x) for x in args.backtest_periods.split(',')]

    # 默认输出路径
    if args.output is None:
        args.output = f"report_{datetime.now().strftime('%Y%m%d')}.html"

    # 功能开关
    features = []
    if args.include_etf_lof:
        features.append('ETF/LOF')
    if args.price_targets:
        features.append('买卖参考价')
    if args.backtest:
        if args.compare_strategies:
            features.append('多策略对比')
        else:
            from backtest import STRATEGIES
            s_label = STRATEGIES.get(args.strategy, {}).get('label', args.strategy)
            features.append(f'回测({s_label})')
    features_str = ' + '.join(features) if features else '仅个股'

    mode = "快速 (3因子)" if args.no_history else "完整 (5因子)"
    print("╔══════════════════════════════════════════════════╗")
    print("║       A股多因子量化选股系统 v3.0                 ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if config:
        ai_cfg = config.get('ai', {})
        has_ai = bool(ai_cfg.get('api_key'))
        model_name = ai_cfg.get('model', '-')
        print(f"  配置: config.json ✓ | AI: {'✓ ' + model_name if has_ai else '✗'}")
    print(f"  模式: {mode} | TOP={args.top} | 功能: {features_str}")
    print(f"  权重: 价值{weights['value']:.0%} / 成长{weights['growth']:.0%} / "
          f"质量{weights['quality']:.0%} / 动量{weights['momentum']:.0%} / "
          f"风险{weights['risk']:.0%}")
    print(f"  缓存: {'关闭' if args.no_cache else '开启'} | "
          f"进程: {args.workers} | 历史: {args.hist_days}天")
    print()

    try:
        # ================================================================
        # 阶段1: 数据采集
        # ================================================================
        from data_fetcher import fetch_all_data

        total_phases = 4
        if args.price_targets:
            total_phases += 1
        if args.backtest:
            total_phases += 1

        print("━" * 52)
        print(f"  📥 阶段 1/{total_phases}: 数据采集")
        print("━" * 52)
        data = fetch_all_data(args)
        print(f"\n  ✅ 数据采集完成")

        # ================================================================
        # 阶段2: 因子计算
        # ================================================================
        from factor_model import calculate_all_factors

        print("\n" + "━" * 52)
        print(f"  🧮 阶段 2/{total_phases}: 因子计算")
        print("━" * 52)
        factor_df = calculate_all_factors(
            spot_filtered=data['spot_filtered'],
            financial_df=data['financial'],
            financial_prev_df=data['financial_prev'],
            history_dict=data['history'],
            sector_map=data.get('sector_map', {}),
            asset_type_map=data.get('asset_type_map', {}),
        )
        print(f"\n  ✅ 因子计算完成")

        # ================================================================
        # 阶段3: 标准化与打分
        # ================================================================
        from factor_model import score_stocks

        print("\n" + "━" * 52)
        print(f"  ⚖️  阶段 3/{total_phases}: 标准化与综合打分")
        print("━" * 52)
        scored_df = score_stocks(factor_df, weights)
        print(f"\n  ✅ 打分完成")

        # ================================================================
        # 阶段3.2: 指定日期选股 (可选)
        # ================================================================
        backtest_date_info = None
        fwd_summary = None
        stock_details = None
        if args.backtest_date:
            sim_scored, sim_date, sim_fwd, sim_details = _run_backtest_date(args, data, weights, scored_df)
            if sim_scored is not None:
                scored_df = sim_scored
                backtest_date_info = sim_date
                fwd_summary = sim_fwd
                stock_details = sim_details

        # ================================================================
        # 阶段3.5: 买卖参考价 (可选)
        # ================================================================
        phase_idx = 4
        backtest_result = None

        if args.price_targets:
            from price_targets import batch_calculate_targets

            print("\n" + "━" * 52)
            print(f"  💰 阶段 {phase_idx}/{total_phases}: 计算买卖参考价")
            print("━" * 52)

            # 仅对TOP N计算参考价（性能考虑）
            top_for_targets = min(args.top * 3, len(scored_df))
            scored_top = scored_df.head(top_for_targets).copy()

            scored_top = batch_calculate_targets(
                scored_df=scored_top,
                history_dict=data['history'],
                financial_df=data['financial'],
                sector_map=data.get('sector_map', {}),
                asset_type_map=data.get('asset_type_map', {}),
            )

            # 将参考价合并回scored_df
            target_cols = ['buy_low', 'buy_high', 'sell_low', 'sell_high',
                           'support', 'resistance', 'pe_percentile', 'pb_percentile']
            for col in target_cols:
                if col in scored_top.columns:
                    scored_df[col] = scored_top[col].reindex(scored_df.index)

            print(f"\n  ✅ 买卖参考价计算完成")
            phase_idx += 1

        # ================================================================
        # 阶段3.8: 策略回测 (可选)
        # ================================================================
        comparison_results = None

        if args.backtest:
            if args.compare_strategies:
                # ---- 多策略对比模式 ----
                from backtest import (run_strategy_comparison,
                                     print_strategy_comparison,
                                     ai_analyze_strategy,
                                     print_backtest_summary,
                                     STRATEGIES)

                print("\n" + "━" * 52)
                print(f"  🏆 阶段 {phase_idx}/{total_phases}: 多策略对比回测")
                print("━" * 52)

                comparison_results = run_strategy_comparison(
                    scored_df=scored_df,
                    history_dict=data['history'],
                    financial_df=data['financial'],
                    financial_prev_df=data['financial_prev'],
                    sector_map=data.get('sector_map', {}),
                    weights=weights,
                    top_n=args.backtest_top,
                    rebalance_days=args.backtest_rebalance,
                    holding_periods=backtest_periods,
                    asset_type_map=data.get('asset_type_map', {}),
                )

                best = print_strategy_comparison(comparison_results, backtest_periods)

                # 用最佳策略作为报告展示
                if best and best in comparison_results:
                    backtest_result = comparison_results[best]

                # AI策略分析
                import os as _os
                has_ai_key = args.ai_key or _os.environ.get('ANTHROPIC_API_KEY') or _os.environ.get('ANTHROPIC_AUTH_TOKEN')
                if has_ai_key:
                    print("\n  🤖 AI策略分析中...")
                    ai_result = ai_analyze_strategy(
                        comparison_results, api_key=args.ai_key,
                        holding_periods=backtest_periods,
                    )
                    if ai_result:
                        print("\n" + "═" * 70)
                        print("  🤖 AI 策略优化建议")
                        print("═" * 70)
                        print(ai_result)
                        print()

            else:
                # ---- 单策略模式 ----
                from backtest import run_backtest, print_backtest_summary, STRATEGIES

                strategy_label = STRATEGIES.get(args.strategy, {}).get('label', args.strategy)
                print("\n" + "━" * 52)
                print(f"  🔬 阶段 {phase_idx}/{total_phases}: 策略回测 ({strategy_label})")
                print("━" * 52)

                backtest_result = run_backtest(
                    scored_df=scored_df,
                    history_dict=data['history'],
                    financial_df=data['financial'],
                    financial_prev_df=data['financial_prev'],
                    sector_map=data.get('sector_map', {}),
                    weights=weights,
                    top_n=args.backtest_top,
                    rebalance_days=args.backtest_rebalance,
                    holding_periods=backtest_periods,
                    asset_type_map=data.get('asset_type_map', {}),
                    strategy=args.strategy,
                    use_regime_filter=not args.no_regime_filter,
                )

                if backtest_result.n_rebalances > 0:
                    print_backtest_summary(backtest_result)
                else:
                    print(f"\n  ⚠ 回测数据不足，无法生成有效结果")

            phase_idx += 1

        # ================================================================
        # 阶段4: 报告输出
        # ================================================================
        from report_generator import print_terminal_report, generate_html_report

        print("\n" + "━" * 52)
        print(f"  📊 阶段 {phase_idx}/{total_phases}: 生成报告")
        print("━" * 52)

        # 应用得分门槛
        if args.min_score > 0:
            scored_df = scored_df[scored_df['composite_score'] >= args.min_score]
            print(f"  得分门槛 ≥{args.min_score}: {len(scored_df)} 只通过")

        # 终端报告
        print_terminal_report(
            scored_df, args.top, weights,
            spot_filtered=data['spot_filtered'],
            backtest_result=backtest_result,
            backtest_date=backtest_date_info,
            fwd_summary=fwd_summary,
        )

        # HTML报告
        if not args.no_html:
            actual_path = generate_html_report(
                scored_df, args.top, weights, args.output,
                spot_filtered=data['spot_filtered'],
                backtest_result=backtest_result,
                backtest_date=backtest_date_info,
                fwd_summary=fwd_summary,
                stock_details=stock_details,
            )
            abs_path = os.path.abspath(actual_path)
            print(f"  ✅ HTML报告: {abs_path}")

        print("\n╔══════════════════════════════════════════════════╗")
        print("║  ✅ 运行完成！                                   ║")
        print("╚══════════════════════════════════════════════════╝")

    except KeyboardInterrupt:
        print("\n\n  ⚠️  用户中断。已获取的数据已缓存，下次运行可直接使用。")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        print("\n  💡 建议:")
        print("    1. 检查网络连接")
        print("    2. 尝试 python main.py --no_cache 清除缓存重试")
        print("    3. 尝试 python main.py --no_history 跳过历史数据")
        sys.exit(1)


if __name__ == '__main__':
    main()

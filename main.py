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
from datetime import datetime


def parse_args():
    """解析命令行参数"""
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
    parser.add_argument('--top', type=int, default=30,
                        help='展示前N只证券 (默认: 30)')
    parser.add_argument('--min_score', type=float, default=0,
                        help='最低综合得分 (默认: 0, 不设门槛)')
    parser.add_argument('--weights', type=str, default='0.25,0.20,0.25,0.20,0.10',
                        help='因子权重: 价值,成长,质量,动量,风险 (默认: 0.25,0.20,0.25,0.20,0.10)')
    parser.add_argument('--output', type=str, default=None,
                        help='HTML报告输出路径 (默认: report_YYYYMMDD.html)')
    parser.add_argument('--no_html', action='store_true',
                        help='不生成HTML报告，仅终端输出')
    parser.add_argument('--no_cache', action='store_true',
                        help='不使用缓存，强制重新获取数据')
    parser.add_argument('--no_history', action='store_true',
                        help='跳过历史K线获取 (快速模式: 仅价值+成长+质量因子)')
    parser.add_argument('--workers', type=int, default=8,
                        help='历史数据并发进程数 (默认: 8, 上限10)')
    parser.add_argument('--sleep', type=float, default=0.15,
                        help='API请求间隔秒数 (默认: 0.15)')
    parser.add_argument('--cache_dir', type=str, default='cache',
                        help='缓存目录 (默认: cache)')

    # ---- 新增: ETF/LOF ----
    parser.add_argument('--include_etf_lof', action='store_true',
                        help='将ETF和LOF纳入选股池 (默认: 仅个股)')

    # ---- 新增: 买卖参考价 ----
    parser.add_argument('--price_targets', action='store_true',
                        help='计算买卖参考价 (基于估值分位数+技术面)')

    # ---- 新增: 回测 ----
    parser.add_argument('--backtest', action='store_true',
                        help='运行策略回测 (Walk-forward)')
    parser.add_argument('--backtest_top', type=int, default=30,
                        help='回测每周期选股数 (默认: 30)')
    parser.add_argument('--backtest_periods', type=str, default='5,10,20,60',
                        help='回测持有周期,逗号分隔 (默认: 5,10,20,60)')
    parser.add_argument('--backtest_rebalance', type=int, default=60,
                        help='回测再平衡间隔天数 (默认: 60)')

    # ---- 新增: 历史数据天数 ----
    parser.add_argument('--hist_days', type=int, default=300,
                        help='历史K线天数, 估值/回测需要更长 (默认: 300)')

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


def main():
    args = parse_args()
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
        features.append('策略回测')
    features_str = ' + '.join(features) if features else '仅个股'

    mode = "快速 (3因子)" if args.no_history else "完整 (5因子)"
    print("╔══════════════════════════════════════════════════╗")
    print("║       A股多因子量化选股系统 v3.0                 ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
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
        if args.backtest:
            from backtest import run_backtest, print_backtest_summary

            print("\n" + "━" * 52)
            print(f"  🔬 阶段 {phase_idx}/{total_phases}: 策略回测")
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
            )

            # 终端输出回测结果
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
        )

        # HTML报告
        if not args.no_html:
            generate_html_report(
                scored_df, args.top, weights, args.output,
                spot_filtered=data['spot_filtered'],
                backtest_result=backtest_result,
            )
            abs_path = os.path.abspath(args.output)
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

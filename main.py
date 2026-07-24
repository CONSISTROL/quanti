#!/usr/bin/env python3
"""
A股多因子量化选股系统 v2.0
========================================
基于五大因子（价值/成长/质量/动量/风险）的量化选股模型
数据源: AkShare (免费)  |  输出: 终端报告 + HTML可视化

用法:
    python main.py                          # 默认: 全因子模型
    python main.py --no_history             # 快速模式: 仅价值+成长+质量 (无需历史K线)
    python main.py --top 50                 # 展示前50只
    python main.py --weights 0.3,0.2,0.3,0.1,0.1  # 自定义权重

免责声明: 本工具仅供学习研究，不构成投资建议。
"""

import argparse
import sys
import os
from datetime import datetime


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='A股多因子量化选股系统',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py --no_history               # 快速模式 (1-2分钟, 无动量/风险因子)
  python main.py                            # 完整模式 (需获取历史K线, 约30分钟)
  python main.py --top 50 --min_score 0.5   # TOP50, 最低得分0.5
  python main.py --weights 0.3,0.15,0.3,0.15,0.1  # 偏重价值和质量

过滤规则: 自动排除ST、停牌、北交所、亏损股

⚠️  免责声明: 本工具仅供学习研究，不构成投资建议。
        """
    )

    parser.add_argument('--top', type=int, default=30,
                        help='展示前N只股票 (默认: 30)')
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
    parser.add_argument('--workers', type=int, default=5,
                        help='历史数据并发线程数 (默认: 5, 不宜超过5)')
    parser.add_argument('--sleep', type=float, default=0.15,
                        help='API请求间隔秒数 (默认: 0.15)')
    parser.add_argument('--cache_dir', type=str, default='cache',
                        help='缓存目录 (默认: cache)')

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

    # 默认输出路径
    if args.output is None:
        args.output = f"report_{datetime.now().strftime('%Y%m%d')}.html"

    mode = "快速 (3因子)" if args.no_history else "完整 (5因子)"
    print("╔══════════════════════════════════════════════════╗")
    print("║       A股多因子量化选股系统 v2.0                 ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  模式: {mode} | TOP={args.top}")
    print(f"  权重: 价值{weights['value']:.0%} / 成长{weights['growth']:.0%} / "
          f"质量{weights['quality']:.0%} / 动量{weights['momentum']:.0%} / "
          f"风险{weights['risk']:.0%}")
    print(f"  缓存: {'关闭' if args.no_cache else '开启'} | "
          f"线程: {args.workers}")
    print()

    try:
        # ================================================================
        # 阶段1: 数据采集
        # ================================================================
        from data_fetcher import fetch_all_data

        print("━" * 52)
        print("  📥 阶段 1/4: 数据采集")
        print("━" * 52)
        data = fetch_all_data(args)
        print(f"\n  ✅ 数据采集完成")

        # ================================================================
        # 阶段2: 因子计算
        # ================================================================
        from factor_model import calculate_all_factors

        print("\n" + "━" * 52)
        print("  🧮 阶段 2/4: 因子计算")
        print("━" * 52)
        factor_df = calculate_all_factors(
            spot_filtered=data['spot_filtered'],
            financial_df=data['financial'],
            financial_prev_df=data['financial_prev'],
            history_dict=data['history'],
            sector_map=data.get('sector_map', {}),
        )
        print(f"\n  ✅ 因子计算完成")

        # ================================================================
        # 阶段3: 标准化与打分
        # ================================================================
        from factor_model import score_stocks

        print("\n" + "━" * 52)
        print("  ⚖️  阶段 3/4: 标准化与综合打分")
        print("━" * 52)
        scored_df = score_stocks(factor_df, weights)
        print(f"\n  ✅ 打分完成")

        # ================================================================
        # 阶段4: 报告输出
        # ================================================================
        from report_generator import print_terminal_report, generate_html_report

        print("\n" + "━" * 52)
        print("  📊 阶段 4/4: 生成报告")
        print("━" * 52)

        # 应用得分门槛
        if args.min_score > 0:
            scored_df = scored_df[scored_df['composite_score'] >= args.min_score]
            print(f"  得分门槛 ≥{args.min_score}: {len(scored_df)} 只通过")

        # 终端报告
        print_terminal_report(
            scored_df, args.top, weights,
            spot_filtered=data['spot_filtered']
        )

        # HTML报告
        if not args.no_html:
            generate_html_report(
                scored_df, args.top, weights, args.output,
                spot_filtered=data['spot_filtered']
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

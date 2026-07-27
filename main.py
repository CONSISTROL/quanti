#!/usr/bin/env python3
"""
A股波段交易系统 v4.0
=====================
运行: python main.py (所有参数通过 config.json 配置)

交易体系:
  选股: 多因子打分 (基本面+技术面)
  买入: MACD金叉 + 趋势向上 + 量价配合
  卖出: 止损/止盈/MACD死叉/跌破均线/SKDJ超买
  仓位: 最多N只, 等权分配

免责声明: 本工具仅供学习研究，不构成投资建议。
"""

import sys
import os
import json
from datetime import datetime


def load_config():
    """加载配置文件"""
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    if not os.path.exists(config_path):
        print("  ✗ 未找到 config.json，请创建配置文件")
        sys.exit(1)
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        print(f"  ✗ 读取 config.json 失败: {e}")
        sys.exit(1)

    # AI配置 → 环境变量
    ai = config.get('ai', {})
    if ai.get('api_key'):
        os.environ.setdefault('ANTHROPIC_AUTH_TOKEN', ai['api_key'])
    if ai.get('base_url'):
        os.environ.setdefault('ANTHROPIC_BASE_URL', ai['base_url'])
    if ai.get('model'):
        os.environ.setdefault('ANTHROPIC_MODEL', ai['model'])

    return config


def main():
    config = load_config()

    # 读取配置
    dt_cfg = config.get('data', {})
    sc_cfg = config.get('scoring', {})
    tr_cfg = config.get('trading', {})
    bt_cfg = config.get('backtest', {})

    print("╔══════════════════════════════════════════════════╗")
    print("║        A股波段交易系统 v4.0                      ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  配置: config.json ✓ | AI: {'✓ ' + config.get('ai', {}).get('model', '') if config.get('ai', {}).get('api_key') else '✗'}")
    print(f"  资金: ¥{tr_cfg.get('initial_capital', 1000000):,.0f} | "
          f"最大持仓: {tr_cfg.get('max_positions', 4)} 只 | "
          f"止损: {tr_cfg.get('stop_loss', -0.08):.0%} | "
          f"止盈: {tr_cfg.get('take_profit', 0.20):.0%}")
    print(f"  回测: {bt_cfg.get('start_date', '2026-01-01')} ~ {bt_cfg.get('end_date', '2026-07-27')}")
    if dt_cfg.get('exclude_gem'):
        print(f"  过滤: 排除创业板{' + 科创板' if dt_cfg.get('exclude_star') else ''}")
    print()

    try:
        # ================================================================
        # 阶段1: 数据采集
        # ================================================================
        from data_fetcher import fetch_all_data

        # 构造 args 对象 (兼容现有接口)
        class Args:
            pass
        args = Args()
        args.cache_dir = 'cache'
        args.no_cache = False
        args.no_history = False
        args.hist_days = dt_cfg.get('hist_days', 1200)
        args.workers = dt_cfg.get('workers', 8)
        args.sleep = dt_cfg.get('sleep', 0.15)
        args.include_etf_lof = dt_cfg.get('include_etf_lof', False)
        args.exclude_gem = dt_cfg.get('exclude_gem', False)
        args.exclude_star = dt_cfg.get('exclude_star', False)

        print("━" * 52)
        print("  📥 阶段 1/4: 数据采集")
        print("━" * 52)
        data = fetch_all_data(args)
        print(f"\n  ✅ 数据采集完成")

        # ================================================================
        # 阶段2: 因子计算 + 打分
        # ================================================================
        from factor_model import calculate_all_factors, score_stocks

        print("\n" + "━" * 52)
        print("  🧮 阶段 2/4: 因子计算 + 打分")
        print("━" * 52)

        weights_str = sc_cfg.get('weights', '0.25,0.20,0.25,0.20,0.10')
        w_vals = [float(x) for x in weights_str.split(',')]
        if abs(sum(w_vals) - 1.0) > 0.01:
            w_vals = [v / sum(w_vals) for v in w_vals]
        keys = ['value', 'growth', 'quality', 'momentum', 'risk']
        weights = dict(zip(keys, w_vals))

        factor_df = calculate_all_factors(
            spot_filtered=data['spot_filtered'],
            financial_df=data['financial'],
            financial_prev_df=data['financial_prev'],
            history_dict=data['history'],
            sector_map=data.get('sector_map', {}),
            asset_type_map=data.get('asset_type_map', {}),
        )
        scored_df = score_stocks(factor_df, weights)
        print(f"\n  ✅ 打分完成: {len(scored_df)} 只证券")

        # ================================================================
        # 阶段3: 波段交易回测
        # ================================================================
        from trading_engine import run_swing_backtest, print_trade_summary, generate_equity_chart

        print("\n" + "━" * 52)
        print("  📈 阶段 3/4: 波段交易回测")
        print("━" * 52)

        result = run_swing_backtest(
            history_dict=data['history'],
            scored_df=scored_df,
            config=tr_cfg,
            start_date_str=bt_cfg.get('start_date', '2026-01-01'),
            end_date_str=bt_cfg.get('end_date', '2026-07-27'),
        )

        if result:
            print_trade_summary(result)

        # ================================================================
        # 阶段4: 生成报告
        # ================================================================
        from report_generator import generate_html_report

        print("\n" + "━" * 52)
        print("  📊 阶段 4/4: 生成报告")
        print("━" * 52)

        top_n = sc_cfg.get('top', 30)
        output_path = f"report_{datetime.now().strftime('%Y%m%d')}.html"

        actual_path = generate_html_report(
            scored_df, top_n, weights, output_path,
            spot_filtered=data['spot_filtered'],
            trade_result=result,
        )
        print(f"  ✅ HTML报告: {os.path.abspath(actual_path)}")

        # 今日建议
        if result and result['final_positions']:
            print(f"\n  💡 当前持仓建议:")
            for pos in result['final_positions']:
                print(f"    {pos.code} {pos.name} | 成本 ¥{pos.entry_price:.2f} | "
                      f"{pos.shares}股 | 投入 ¥{pos.capital:,.0f}")
        elif result:
            print(f"\n  💡 当前空仓，等待买入信号")

        print("\n╔══════════════════════════════════════════════════╗")
        print("║  ✅ 运行完成！                                   ║")
        print("╚══════════════════════════════════════════════════╝")

    except KeyboardInterrupt:
        print("\n\n  ⚠️  用户中断。")
        sys.exit(0)
    except Exception as e:
        print(f"\n  ❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()

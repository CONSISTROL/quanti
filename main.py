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
import pandas as pd
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


def apply_strategy_override(tr_cfg, strat_name):
    """应用 --strategy 覆盖 + 策略推荐参数 (运行时覆盖, 不写 config.json)"""
    from strategies import get_strategy, strategy_label
    st = get_strategy(strat_name)  # 校验: 未知策略直接抛错
    tr_cfg = dict(tr_cfg)
    tr_cfg['strategy'] = strat_name
    rec = getattr(st, 'recommended', None)
    if rec:
        for k, v in rec.items():
            tr_cfg[k] = v
        print(f"  📋 已应用 {strategy_label(strat_name)} 推荐参数: "
              + ", ".join(f"{k}={v}" for k, v in rec.items()))
    return tr_cfg


def run_optimization(config):
    """运行参数优化"""
    dt_cfg = config.get('data', {})
    bt_cfg = config.get('backtest', {})
    tr_cfg = config.get('trading', {})

    print("╔══════════════════════════════════════════════════╗")
    print("║        A股波段交易系统 — 参数优化模式            ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  回测区间: {bt_cfg.get('start_date', '2025-01-01')} ~ {bt_cfg.get('end_date', '2026-07-27')}")
    print()

    # 数据采集
    from data_fetcher import fetch_all_data
    class Args: pass
    args = Args()
    args.cache_dir = 'cache'; args.no_cache = False; args.no_history = False
    args.hist_days = dt_cfg.get('hist_days', 1200); args.workers = dt_cfg.get('workers', 8)
    args.sleep = dt_cfg.get('sleep', 0.15); args.include_etf_lof = dt_cfg.get('include_etf_lof', False)
    args.exclude_gem = dt_cfg.get('exclude_gem', False); args.exclude_star = dt_cfg.get('exclude_star', False)

    print("━" * 52)
    print("  📥 数据采集")
    print("━" * 52)
    data = fetch_all_data(args)

    # 打分
    from factor_model import calculate_all_factors, score_stocks
    sc_cfg = config.get('scoring', {})
    weights_str = sc_cfg.get('weights', '0.25,0.20,0.25,0.20,0.10')
    w_vals = [float(x) for x in weights_str.split(',')]
    if abs(sum(w_vals) - 1.0) > 0.01:
        w_vals = [v / sum(w_vals) for v in w_vals]
    weights = dict(zip(['value','growth','quality','momentum','risk'], w_vals))

    factor_df = calculate_all_factors(
        data['spot_filtered'], data['financial'], data['financial_prev'],
        data['history'], data.get('sector_map',{}), data.get('asset_type_map',{}))
    scored_df = score_stocks(factor_df, weights)

    # 优化
    from optimizer import optimize_strategy
    from indicator_cache import precompute_all_indicators
    from data_fetcher import _code_pure

    # 预计算指标
    pure_to_sina = {}
    for s in data['history']:
        pure_to_sina[_code_pure(s)] = s

    start_dt = pd.Timestamp(bt_cfg.get('start_date', '2025-01-01'))
    end_dt = pd.Timestamp(bt_cfg.get('end_date', '2026-07-27'))
    all_dates = set()
    for hist in data['history'].values():
        if hist is not None and 'date' in hist.columns:
            for d in hist['date'].values:
                ts = pd.Timestamp(d)
                if start_dt <= ts <= end_dt:
                    all_dates.add(ts)
    trading_dates = sorted(all_dates)

    precomputed = precompute_all_indicators(
        data['history'], pure_to_sina, trading_dates,
        start_date=bt_cfg.get('start_date', '2025-01-01'),
        end_date=bt_cfg.get('end_date', '2026-07-27'),
    )

    results = optimize_strategy(
        data['history'], scored_df, tr_cfg,
        start_date=bt_cfg.get('start_date', '2025-01-01'),
        end_date=bt_cfg.get('end_date', '2026-07-27'),
        precomputed=precomputed,
    )

    # 用最优参数运行一次完整回测
    if results:
        best = results[0]
        print(f"\n  🏆 使用最优参数运行完整回测...")
        best_config = dict(tr_cfg)
        best_config.update(best['params'])

        from trading_engine import run_swing_backtest, print_trade_summary
        result = run_swing_backtest(
            data['history'], scored_df, best_config,
            start_date_str=bt_cfg.get('start_date', '2025-01-01'),
            end_date_str=bt_cfg.get('end_date', '2026-07-27'),
        )
        if result:
            print_trade_summary(result)

            print(f"\n  📝 最优参数 (请更新 config.json):")
            print(f'    "stop_loss": {best["params"]["stop_loss"]},')
            print(f'    "take_profit": {best["params"]["take_profit"]},')
            print(f'    "max_holding_days": {best["params"]["max_holding_days"]},')
            print(f'    "min_buy_score": {best["params"]["min_buy_score"]}')


def run_single_stock(stock_code, config, strat_override=None):
    """运行个股回测"""
    dt_cfg = config.get('data', {})
    tr_cfg = config.get('trading', {})
    if strat_override:
        tr_cfg = apply_strategy_override(tr_cfg, strat_override)
    bt_cfg = config.get('backtest', {})

    print("╔══════════════════════════════════════════════════╗")
    print("║        A股波段交易系统 — 个股回测模式            ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  股票: {stock_code}")
    from strategies import strategy_label
    print(f"  策略: {strategy_label(tr_cfg.get('strategy', 'reversal'))}")
    print(f"  区间: {bt_cfg.get('start_date', '2025-01-01')} ~ {bt_cfg.get('end_date', '2026-07-27')}")
    print()

    # 数据采集
    from data_fetcher import fetch_all_data
    class Args: pass
    args = Args()
    args.cache_dir = 'cache'; args.no_cache = False; args.no_history = False
    args.hist_days = dt_cfg.get('hist_days', 1200); args.workers = dt_cfg.get('workers', 8)
    args.sleep = dt_cfg.get('sleep', 0.15); args.include_etf_lof = dt_cfg.get('include_etf_lof', False)
    args.exclude_gem = False; args.exclude_star = False  # 个股回测不过滤

    print("━" * 52)
    print("  📥 数据采集")
    print("━" * 52)
    data = fetch_all_data(args)

    # 运行个股回测
    from trading_engine import backtest_single_stock, print_trade_summary

    print("\n" + "━" * 52)
    print("  📈 个股回测")
    print("━" * 52)

    result = backtest_single_stock(
        stock_code, data['history'], tr_cfg,
        bt_cfg.get('start_date', '2025-01-01'),
        bt_cfg.get('end_date', '2026-07-27'),
    )

    if result:
        print_trade_summary(result)

        # 生成HTML报告
        from report_generator import generate_html_report
        output_path = f"report_{stock_code}_{datetime.now().strftime('%Y%m%d')}.html"
        try:
            actual_path = generate_html_report(
                None, 30, {}, output_path,
                backtest_result=result,
                trade_result=result,
            )
            print(f"\n  ✅ HTML报告: {os.path.abspath(actual_path)}")
        except Exception as e:
            print(f"\n  ⚠ HTML报告生成失败: {e}")

    print("\n╔══════════════════════════════════════════════════╗")
    print("║  ✅ 个股回测完成！                               ║")
    print("╚══════════════════════════════════════════════════╝")


def main():
    config = load_config()

    # --strategy 覆盖 config trading.strategy (实验策略如 gap_open 无需改 config.json)
    strat_override = None
    for i, arg in enumerate(sys.argv):
        if arg == '--strategy' and i + 1 < len(sys.argv):
            strat_override = sys.argv[i + 1]
            break
        elif arg.startswith('--strategy='):
            strat_override = arg.split('=')[1]
            break

    # 检查是否运行参数优化
    if '--optimize' in sys.argv:
        run_optimization(config)
        return

    # 检查是否运行个股回测
    stock_code = None
    for i, arg in enumerate(sys.argv):
        if arg == '--stock' and i + 1 < len(sys.argv):
            stock_code = sys.argv[i + 1]
            break
        elif arg.startswith('--stock='):
            stock_code = arg.split('=')[1]
            break

    if stock_code:
        # 清洗股票代码: 去sh/sz/bj前缀 + 补零到6位
        raw = stock_code.strip()
        for prefix in ('sh', 'sz', 'bj'):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        raw = raw.zfill(6)
        run_single_stock(raw, config, strat_override)
        return

    # 读取配置
    dt_cfg = config.get('data', {})
    sc_cfg = config.get('scoring', {})
    tr_cfg = config.get('trading', {})
    if strat_override:
        tr_cfg = apply_strategy_override(tr_cfg, strat_override)
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
        from indicator_cache import precompute_all_indicators

        print("\n" + "━" * 52)
        print("  📈 阶段 3/4: 波段交易回测")
        print("━" * 52)

        # 预计算指标 (首次计算后缓存, 后续秒加载)
        from data_fetcher import _code_pure
        pure_to_sina = {}
        for sina_code in data['history']:
            pure = _code_pure(sina_code)
            pure_to_sina[pure] = sina_code

        # 获取交易日列表
        start_dt = pd.Timestamp(bt_cfg.get('start_date', '2025-01-01'))
        end_dt = pd.Timestamp(bt_cfg.get('end_date', '2026-07-27'))
        all_dates = set()
        for hist in data['history'].values():
            if hist is not None and 'date' in hist.columns:
                for d in hist['date'].values:
                    ts = pd.Timestamp(d)
                    if start_dt <= ts <= end_dt:
                        all_dates.add(ts)
        trading_dates = sorted(all_dates)

        if tr_cfg.get('strategy') in ('gap_open', 'gap_open_open'):
            # 跳空高开策略: 信号由引擎向量化预筛 (收盘涨幅/开盘跳空), 无需2GB指标缓存
            precomputed = None
            print('  ⚡ 跳空高开策略: 跳过指标预计算 (信号向量化预筛, 无2GB缓存加载)')
        else:
            precomputed = precompute_all_indicators(
                data['history'], pure_to_sina, trading_dates,
                cache_dir='cache',
                start_date=bt_cfg.get('start_date', '2025-01-01'),
                end_date=bt_cfg.get('end_date', '2026-07-27'),
            )

        # 主报告负责"系统买卖信号记录" — 引擎同一次回测同时产出两种口径:
        #   信号账户 (signal_stats) = 信号日按信号价成交 → 主报告统计/系统买卖信号记录
        #   执行账户 (stats)        = config 成交模式 (exec_next_close 次日尾盘价) → 用户A操作记录(见自选池报告)
        if tr_cfg.get('strategy') == 'gap_open':
            # 跳空高开策略: 信号当日收盘价买入, 次日收盘卖出 (报告复刻)
            print('  成交模式: 信号当日收盘价买入, 次日收盘卖出 (跳空高开隔日轮动, 报告复刻)')
        elif tr_cfg.get('strategy') == 'gap_open_open':
            # 米筐模板开盘口径: 开盘集合竞价决策 → 当日开盘价买入, 次日尾盘收盘价卖出
            print('  成交模式: 开盘集合竞价决策 → 当日开盘价买入, 次日尾盘收盘价卖出 (米筐模板开盘口径)')
        else:
            exec_mode = ('次日尾盘价成交' if tr_cfg.get('exec_next_close') else
                         '次日开盘价成交' if tr_cfg.get('exec_next_open') else
                         '信号当日收盘价成交')
            print(f'  成交模式: {exec_mode} — 主报告统计=系统买卖信号口径 (信号日按信号价成交)')

        result = run_swing_backtest(
            history_dict=data['history'],
            scored_df=scored_df,
            config=tr_cfg,
            start_date_str=bt_cfg.get('start_date', '2026-01-01'),
            end_date_str=bt_cfg.get('end_date', '2026-07-27'),
            precomputed=precomputed,
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
        if result:
            # 回测生成两份报告: 信号口径(信号日按信号价成交) + 用户操作执行口径(实际成交价)
            report_specs = (('signal', 'signal', '信号口径'), ('exec', 'exec', '用户操作执行口径'))
        else:
            report_specs = (('signal', '', ''),)  # 无回测结果: 仅默认主报告
        for scope, tag, label in report_specs:
            output_path = f"report_{tag}_{datetime.now().strftime('%Y%m%d')}.html".replace('__', '_')
            actual_path = generate_html_report(
                scored_df, top_n, weights, output_path,
                spot_filtered=data['spot_filtered'],
                trade_result=result,
                trade_scope=scope,
            )
            print(f"  ✅ {label}HTML报告: {os.path.abspath(actual_path)}")

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

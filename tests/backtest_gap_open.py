"""
跳空高开隔日轮动回测 — GapOpenStrategy (全市场扫描)

参考米筐(Ricequant)模板: 问财选股(跳空高开>3%+主力净流入+低估值+市值<200亿)
→ 开盘买入 → 次日收盘卖出
本地近似: 跳空高开>3% + 放量确认(vol_ratio>=1.5, 主力净流入的代理);
主力净流入/低估值/市值无历史数据, 跳过 (避免最新财报回测历史的前视偏差)

用法:
  python tests/backtest_gap_open.py              # config backtest 全区间 (约15-25分钟)
  python tests/backtest_gap_open.py --days 400   # 只回测最近400个交易日 (快速验证)
  python tests/backtest_gap_open.py --limit 300  # 只扫前300只股票 (开发调试)
"""
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd


def _load_history_local(hist_file=''):
    """优先加载本地 hist 缓存 pickle (跳过 fetch_all_data 的全市场网络抓取)
    --hist-file 指定历史快照 (复现历史报告场景, 如 hist_batch_20260802.pkl)
    """
    import glob
    import pickle
    if hist_file:
        p = hist_file if os.path.isabs(hist_file) else os.path.join('cache', hist_file)
        if not os.path.exists(p):
            print(f'  ✗ 指定的历史快照不存在: {p}')
            return None
    else:
        files = sorted(glob.glob(os.path.join('cache', 'hist_batch_*.pkl')))
        if not files:
            return None
        p = files[-1]
    print(f'  (历史缓存: {os.path.basename(p)})')
    with open(p, 'rb') as f:
        hist = pickle.load(f)
    if isinstance(hist, dict):
        hist.pop('_date', None)
    return hist


def main(config=None, days=0, limit=0, hist_file='', next_close=False, max_rise=0.0):
    from main import load_config
    config = config or load_config()

    tr_cfg = dict(config['trading'])
    tr_cfg['strategy'] = 'gap_open'
    if next_close:
        tr_cfg['buy_next_close'] = True  # 信号次日尾盘(收盘价)买入, 卖出仍按持有到期当日尾盘
    if max_rise > 0:
        tr_cfg['gap_max'] = max_rise  # 排除收盘涨幅>=该阈值的股票 (如0.099=排除涨停收盘, 涨停买不进)
    from strategies import GapOpenStrategy, strategy_label
    tr_cfg.update(GapOpenStrategy.recommended)
    print(f'  策略: {strategy_label("gap_open")}')

    # ---- 1. 加载数据 (优先本地缓存, 回退 fetch_all_data) ----
    data = None
    hist = _load_history_local(hist_file)
    if hist is None:
        print('  (无本地历史缓存, 走 fetch_all_data 全市场抓取)')
        dt_cfg = config.get('data', {})
        from data_fetcher import fetch_all_data
        class Args:
            pass
        args = Args()
        args.cache_dir = 'cache'; args.no_cache = False; args.no_history = False
        args.hist_days = dt_cfg.get('hist_days', 1200)
        args.workers = dt_cfg.get('workers', 8)
        args.sleep = dt_cfg.get('sleep', 0.15)
        args.include_etf_lof = dt_cfg.get('include_etf_lof', False)
        args.exclude_gem = dt_cfg.get('exclude_gem', False)
        args.exclude_star = dt_cfg.get('exclude_star', False)
        data = fetch_all_data(args)
        hist = data['history']

    # ---- 2. 回测区间 ----
    bt_cfg = config.get('backtest', {})
    start = bt_cfg.get('start_date', '2015-01-01')
    end = bt_cfg.get('end_date', '2026-08-02')
    if days:
        all_dates = set()
        for h in hist.values():
            if h is not None and 'date' in h.columns:
                for d in h['date'].values:
                    ts = pd.Timestamp(d)
                    if pd.Timestamp(start) <= ts <= pd.Timestamp(end):
                        all_dates.add(ts)
        tds = sorted(all_dates)
        if len(tds) > days:
            start = tds[-days].strftime('%Y-%m-%d')
        print(f'  (快速验证: 只回测最近 {min(days, len(tds))} 个交易日)')

    # ---- 3. 指标预计算 (仅候选池) ----
    from data_fetcher import _code_pure
    from indicator_cache import precompute_all_indicators
    if limit:
        # 截断 hist: 引擎候选池从 history_dict 构建, 限制扫描范围 (调试用)
        sub = {}
        for k in hist:
            if len(sub) >= limit:
                break
            if hist[k] is not None and 'date' in hist[k].columns:
                sub[k] = hist[k]
        hist = sub
        print(f'  (调试: 只扫前 {limit} 只)')
    pure_to_sina = {_code_pure(s): s for s in hist}
    cands = [c for c in pure_to_sina if hist.get(pure_to_sina[c]) is not None
             and 'date' in hist[pure_to_sina[c]].columns]
    print(f'  候选: {len(cands)} 只 (全市场扫描)')

    # 交易日 (全市场对齐, 供 precomputed 覆盖引擎实际交易日)
    all_dates = set()
    for h in hist.values():
        if h is not None and 'date' in h.columns:
            for d in h['date'].values:
                ts = pd.Timestamp(d)
                if pd.Timestamp(start) <= ts <= pd.Timestamp(end):
                    all_dates.add(ts)
    trading_dates = sorted(all_dates)

    cand_map = {c: pure_to_sina[c] for c in cands}
    if tr_cfg.get('strategy') == 'gap_open':
        precomputed = None  # 跳空策略: 信号由引擎向量化预筛, 无需指标缓存
        print('  ⚡ 跳过指标预计算 (信号向量化预筛)')
    elif limit:
        # 调试模式: 直接计算子集指标 (不读写缓存文件, 避免覆盖全市场缓存)
        from indicator_cache import _incremental_indicators
        target_dates = {pd.Timestamp(d).strftime('%Y-%m-%d') for d in trading_dates}
        precomputed = {}
        for code, sina in cand_map.items():
            df = hist[sina]
            precomputed[code] = _incremental_indicators(
                df['close'].values.astype(float),
                df['volume'].values.astype(float) if 'volume' in df.columns else None,
                df['high'].values.astype(float) if 'high' in df.columns else df['close'].values.astype(float),
                df['low'].values.astype(float) if 'low' in df.columns else df['close'].values.astype(float),
                df['date'].values, target_dates,
            )
    else:
        precomputed = precompute_all_indicators(
            hist, cand_map, trading_dates,
            cache_dir='cache', start_date=start, end_date=end,
        )

    # ---- 4. 回测 ----
    # 股票名称: 从最新 spot 缓存构建最小 scored_df (code/name 列), 引擎据此填充交易记录名称
    spot_df = None
    import glob
    spot_files = sorted(glob.glob(os.path.join('cache', 'spot_*.pkl')))
    if spot_files:
        try:
            with open(spot_files[-1], 'rb') as f:
                spot_df = pickle.load(f)
        except Exception:
            spot_df = None
    name_df = None
    if spot_df is not None and '代码' in spot_df.columns and '名称' in spot_df.columns:
        from data_fetcher import _code_pure
        rows = []
        for _, r in spot_df.iterrows():
            try:
                rows.append({'code': _code_pure(str(r['代码'])), 'name': str(r['名称'])})
            except Exception:
                continue
        if rows:
            name_df = pd.DataFrame(rows)
            print(f'  (股票名称: 取自 {os.path.basename(spot_files[-1])} 共 {len(rows)} 只)')

    from trading_engine import run_swing_backtest, print_trade_summary
    result = run_swing_backtest(
        hist, name_df, tr_cfg,
        start_date_str=start, end_date_str=end,
        precomputed=precomputed,
    )
    if not result:
        print('  ✗ 回测无结果')
        return 1

    st = result['stats']
    print('\n' + '═' * 90)
    mode = '信号日收盘买入, 次日收盘卖出' if not next_close else '信号次日尾盘(收盘价)买入, 再持有1个交易日尾盘卖出'
    print(f'  🚀 跳空高开隔日轮动 ({mode})')
    print('═' * 90)
    print(f"    总收益率: {st['total_return']:+.2%} | 年化: {st['annual_return']:+.2%} "
          f"| Sharpe: {st['sharpe']:.2f} | 最大回撤: {st['max_drawdown']:.1%} "
          f"| 交易: {st['total_trades']}笔 | 胜率: {st['win_rate']:.0%}")
    print_trade_summary(result)

    # ---- 5. HTML报告 (两份: 信号口径 + 用户操作执行口径) ----
    from report_generator import generate_html_report
    from datetime import datetime
    stamp = datetime.now().strftime('%Y%m%d')
    for scope, tag, label in (('signal', 'signal', '信号口径'),
                              ('exec', 'exec', '用户操作执行口径')):
        output_path = f'report_gap_open_{tag}_{stamp}.html'
        try:
            actual_path = generate_html_report(
                None, 30, {}, output_path,
                backtest_result=result, trade_result=result,
                trade_scope=scope,
            )
            print(f'  ✅ {label}HTML报告: {os.path.abspath(actual_path)}')
        except Exception as e:
            print(f'\n  ⚠ {label}HTML报告生成失败: {e}')
    return 0


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--days', type=int, default=0)
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--hist-file', default='', help='指定历史快照 (如 hist_batch_20260802.pkl, 复现历史报告场景)')
    p.add_argument('--next-close', action='store_true', help='信号次日尾盘买入变体: T日收盘涨幅>=7%信号 → T+1尾盘(收盘价)买入 → T+2尾盘卖出')
    p.add_argument('--max-rise', type=float, default=0.0, help='收盘涨幅上限 (排除涨停收盘买不进的股票, 如 0.099)')
    a = p.parse_args()
    sys.exit(main(days=a.days, limit=a.limit, hist_file=a.hist_file, next_close=a.next_close, max_rise=a.max_rise))

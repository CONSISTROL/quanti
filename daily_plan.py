"""
次日操作计划 — 每日收盘后运行, 提前一天给出手动操作策略 (卖出/切标)

每晚收盘数据出来后运行, 回答三个问题:
  1. 当前持仓明天收盘到什么价格该卖? (跌破趋势/止损/高位死叉临界 → 持有区间)
  2. 卖出之后切标买谁? (按今日引擎同款打分排序)
  3. 候选标的明天收盘到什么价格触发买入? (追涨临界/超跌临界)

持仓来源:
  - 默认取回测模拟的最终持仓 (与报告一致)
  - 也可在 config.json trading.positions 指定真实持仓 (手动交易与模拟有偏差时):
      "trading": { ..., "positions": [{"code": "159941", "entry": 1.42}] }

用法: 由 tests/watchlist_backtest.py 第5.6节自动输出, 也可独立使用:
    from daily_plan import build_nextday_plan, print_nextday_plan
    plan = build_nextday_plan(hist, precomputed, names, config, strategy, scored_df, positions)
    print_nextday_plan(plan)
"""
from data_fetcher import _code_pure
from sell_price_forecast import next_sell_prices, next_buy_prices
from trading_engine import fmt_px


def build_nextday_plan(hist, precomputed, names, config, strategy, scored_df, positions):
    """构造次日操作计划

    参数:
        hist: {sina_code: K线DataFrame}
        precomputed: {code: {date_str: 指标dict}} (引擎预计算指标)
        names: {code: 中文名}
        config: config.json 完整配置
        strategy: 策略实例
        scored_df: 引擎用的打分排名 (龙头TOP10加分依赖它)
        positions: [Position] 当前持仓 (回测最终持仓或 config trading.positions 构造)
    返回:
        dict: {last_date, holds: [{code,name,entry,forecast,今日卖出,卖出原因}],
               buys: [{code,name,score,reason,bonus,buy}], source}
    """
    tr = config.get('trading', {})
    min_buy_score = tr.get('min_buy_score', 4)
    pri = tr.get('watchlist_priority', {})
    sina_map = {_code_pure(k): k for k in hist}

    # 最后交易日 (precomputed 各代码日期键的并集最大值)
    last_date = max(max(pd.keys()) for pd in precomputed.values())

    def today_state(code):
        """复刻引擎今日收盘判定: buy_signal + 龙头TOP10加分 + 优先级加分"""
        pd_ = precomputed.get(code, {}).get(last_date)
        if not pd_:
            return None, '', 0
        is_buy, score, reason = strategy.buy_signal(pd_)
        bonus = 0
        if scored_df is not None and 'code' in scored_df.columns:
            m = scored_df[scored_df['code'].astype(str).str.zfill(6) == code]
            if not m.empty and int(m.iloc[0].get('rank', 999)) <= 10:
                score += 2
                bonus += 2
        pb = pri.get(code) or pri.get(str(code).zfill(6))
        if pb:
            score += int(pb)
            bonus += int(pb)
        return float(score), reason, bonus

    # ---- 1. 持仓卖出计划 ----
    holds = []
    for pos in positions:
        sina = sina_map.get(pos.code)
        if not sina:
            continue
        fp = next_sell_prices(hist[sina], pos.entry_price, strategy)
        ind_today = precomputed.get(pos.code, {}).get(last_date, {})
        is_sell_today, sell_reason = strategy.sell_signal(ind_today, pos.entry_price, 9999, 0)
        holds.append({
            'code': pos.code, 'name': getattr(pos, 'name', '') or names.get(pos.code, pos.code),
            'entry': pos.entry_price, 'forecast': fp,
            '今日卖出': bool(is_sell_today), '卖出原因': sell_reason,
        })

    # ---- 2. 买入/切标候选 (非持仓代码, 按今日得分排序前4) ----
    held_codes = {p.code for p in positions}
    cands = []
    for code in precomputed:
        if code in held_codes:
            continue
        score, reason, bonus = today_state(code)
        if score is None:
            continue
        cands.append((score, code, reason, bonus))
    cands.sort(reverse=True)

    buys = []
    for score, code, reason, bonus in cands[:4]:
        sina = sina_map.get(code)
        if not sina:
            continue
        bp = next_buy_prices(hist[sina], strategy, bonus=bonus, min_buy_score=min_buy_score)
        buys.append({'code': code, 'name': names.get(code, code),
                     'score': score, 'reason': reason, 'bonus': bonus, 'buy': bp})

    return {'last_date': last_date, 'holds': holds, 'buys': buys}


def print_nextday_plan(plan):
    """终端输出次日操作计划"""
    last_date = plan['last_date']
    holds = plan['holds']
    buys = plan['buys']
    print('\n' + '═' * 90)
    print(f'  📌 次日操作计划 (数据截至 {last_date} 收盘) — 提前一天给出手动操作策略')
    print('═' * 90)

    if holds:
        for h in holds:
            fp = h['forecast']
            pnl = (fp['现价'] / h['entry'] - 1) if h['entry'] > 0 else 0
            print(f"\n  📦 持仓 {h['code']} {h['name']}  成本 {fmt_px(h['entry'])}  "
                  f"现价 {fmt_px(fp['现价'])}  ({pnl:+.1%})")
            if h['今日卖出']:
                print(f"    🚨 今日收盘已触发卖出 ({h['卖出原因']}) → 明日开盘优先卖出")
                continue
            if fp['趋势价'] is not None:
                print(f"    · 收盘 ≤ {fmt_px(fp['趋势价'])} → 卖出 (跌破MA20趋势转弱)")
            print(f"    · 收盘 ≤ {fmt_px(fp['止损价'])} → 卖出 (成本-5%止损)")
            if fp['死叉价'] is not None:
                print(f"    · 收盘 ≥ {fmt_px(fp['死叉价'])} → 卖出 (SKDJ高位死叉)")
            if fp['安全低'] is not None and fp['安全高'] is not None:
                print(f"    ✅ 持有区间: {fmt_px(fp['安全低'])} ~ {fmt_px(fp['安全高'])} 之间收盘 → 继续持有")
            elif fp['安全低'] is not None:
                print(f"    ✅ 持有区间: 收盘 > {fmt_px(fp['安全低'])} → 继续持有")
    else:
        print('\n  📦 当前无持仓 (空仓) — 等待候选标的买入信号')

    if buys:
        print(f"\n  🎯 切标/买入候选 (今日收盘强弱分排序):")
        for i, b in enumerate(buys, 1):
            bp = b['buy']
            tag = f" (引擎加分+{b['bonus']}: 龙头TOP10+2+优先级)" if b['bonus'] else ''
            print(f"    {i}️⃣ {b['code']} {b['name']}  得分 {b['score']:.1f}{tag}")
            if bp['已触发']:
                print(f"       ✅ 今日收盘已满足买入条件 → 明日开盘价买入即可")
            elif bp['方向'] == 'above':
                print(f"       ⚠ 明日收盘 ≥ {fmt_px(bp['触发价'])} → 触发买入 (追涨)")
            elif bp['方向'] == 'below':
                print(f"       ⚠ 明日收盘 ≤ {fmt_px(bp['触发价'])} → 触发买入 (超跌)")
            elif bp['方向'] == 'both':
                print(f"       ⚠ 明日收盘 ≥ {fmt_px(bp['触发价'])} 或超跌 → 触发买入")
            else:
                print(f"       ➖ 明日 ±20% 内无触发价, 继续观察")
    else:
        print('\n  🎯 无买入候选 (全部不满足条件, 空仓等待)')

    print('\n  ── 注: 触发价为"明日收盘价"的模拟临界; 盘中接近/突破临界亦可按同一逻辑操作 ──')

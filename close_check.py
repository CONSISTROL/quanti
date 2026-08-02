#!/usr/bin/env python3
"""
收盘前检查 — 15:57 拉取实时价格, 计算今日买卖信号, 有信号推送飞书机器人

用法:
    python close_check.py                # 立即用当前实时价检查
    python close_check.py --wait         # 等到交易日 15:57 再检查 (可挂后台/任务计划)
    python close_check.py --dry-run      # 只打印不推送 (调试)

原理:
    1. 拉取自选池历史K线 (config data.source, 前复权) + 腾讯实时行情
       (qt.gtimg.cn 免费批量接口, 含股票/ETF/LOF)
    2. 用实时价构造"今日bar" (close=最新价, open/high/low/vol=今日实际值)
       追加到历史 → compute_indicators 重算完整技术指标
    3. 持仓 (config trading.positions, 真实手动持仓):
         sell_signal → 今日触发卖出? (止损/跌破MA20/高位死叉)
       非持仓:
         buy_signal + 引擎加分 ≥ min_buy_score → 今日触发买入? (复刻引擎, 含优先级加分)
    4. 有任一买卖信号 → POST 飞书 webhook (config alert.feishu_webhook)

注意: 15:57 最新价≈收盘价但尾盘仍有波动, 推送后请确认尾盘价格再执行次日操作.
"""

import os
for _pv in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
            'all_proxy', 'ALL_PROXY'):
    os.environ.pop(_pv, None)
os.environ['NO_PROXY'] = '*'

import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import requests

from data_fetcher import _code_pure
from trading_engine import compute_indicators, Position
from strategies import get_strategy

# Windows GBK 控制台保护: ✓/⚠ 等 Unicode 符号直接 print 会报错
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

_TENCENT_QT_URL = 'https://qt.gtimg.cn/q='
_TAIL_BARS = 400  # 与 sell_price_forecast 一致, 400根指标完全收敛


def _to_tx_symbol(code):
    """纯6位 → 腾讯代码 (sh/sz前缀: 5/6/9开头→sh, 其余→sz)"""
    c = str(code).zfill(6)
    pref = 'sh' if c[0] in ('5', '6', '9') else 'sz'
    return f'{pref}{c}'


def fetch_tencent_realtime(codes):
    """批量拉取腾讯实时行情 (一次请求)

    返回: {纯6位: {name, price, open, high, low, volume(股), ts(YYYYMMDDHHMMSS)}}
    字段: 1=名称, 3=最新价, 5=今开, 6=成交量(手), 30=时间, 33=最高, 34=最低
    """
    out = {}
    syms = [_to_tx_symbol(c) for c in codes]
    r = requests.get(_TENCENT_QT_URL + ','.join(syms), timeout=10)
    r.encoding = 'gbk'
    for line in r.text.strip().split(';'):
        line = line.strip()
        if '="' not in line:
            continue
        key, val = line.split('=', 1)
        pure = key[2:]  # 'v_sh601857' → 'sh601857'
        for pref in ('sh', 'sz', 'bj'):
            if pure.startswith(pref):
                pure = pure[len(pref):]
                break
        f = val.strip('";').split('~')
        if len(f) < 35:
            continue
        try:
            price = float(f[3])
            if price <= 0:
                continue  # 停牌/未上市
            out[pure] = {
                'name': f[1],
                'price': price,
                'open': float(f[5]),
                'high': float(f[33]),
                'low': float(f[34]),
                'volume': float(f[6]) * 100.0,  # 手 → 股 (与历史K线口径一致)
                'ts': f[30],
            }
        except (ValueError, IndexError):
            continue
    return out


def build_today_indicators(hist_df, real):
    """历史(剔除今日bar) + 实时bar → 今日完整指标

    hist_df: 历史日线DataFrame (前复权)
    real: fetch_tencent_realtime 的单只dict
    返回: (ind, df) — ind=今日指标dict, df=拼接后的完整K线
    """
    df = hist_df.tail(_TAIL_BARS).reset_index(drop=True).copy()
    today = datetime.now().date()
    # 历史缓存可能已含今日未收盘bar, 统一剔除后以实时数据重建
    df = df[pd.to_datetime(df['date']).dt.date != today]
    row = pd.DataFrame([{
        'date': pd.Timestamp(today),
        'open': real['open'], 'high': real['high'],
        'low': real['low'], 'close': real['price'],
        'volume': real['volume'],
    }])
    df = pd.concat([df, row], ignore_index=True)
    closes = df['close'].values.astype(float)
    highs = df['high'].values.astype(float)
    lows = df['low'].values.astype(float)
    vols = df['volume'].values.astype(float) if 'volume' in df.columns else None
    return compute_indicators(closes, vols, highs, lows), df


def wait_until_1557():
    """等待到 15:57 (已过则立即执行)"""
    now = datetime.now()
    target = now.replace(hour=15, minute=57, second=0, microsecond=0)
    if now >= target:
        return
    print(f'  当前 {now:%H:%M:%S}, 等待到 15:57 拉取实时价 (Ctrl+C 中断)...')
    time.sleep((target - now).total_seconds())


def run_check(config, dry_run=False):
    """收盘检查主流程. 返回飞书消息文本 (无信号返回 None)"""
    tr = config.get('trading', {})
    watchlist = [str(c).zfill(6) for c in config.get('watchlist', [])]
    min_buy_score = tr.get('min_buy_score', 4)
    pri = tr.get('watchlist_priority', {})

    if not watchlist:
        print('  ✗ config.json 未配置 watchlist')
        return None

    print('═' * 60)
    print('  📌 收盘前检查 (15:57 实时价 ≈ 今日收盘价)')
    print('═' * 60)

    # ---- 1. 历史K线 (config data.source) ----
    from data_sources import get_data_source, data_source_label
    source_name = config.get('data', {}).get('source', 'quantdash')
    print(f'  数据源: {data_source_label(source_name)}')
    hist = get_data_source(source_name).fetch_watchlist_data(
        watchlist, cache_dir='cache', use_cache=True, max_bars=_TAIL_BARS)
    sina_map = {_code_pure(k): k for k in hist}

    # ---- 2. 腾讯实时行情 ----
    print('  → 腾讯实时行情...')
    real = fetch_tencent_realtime(watchlist)
    missing = [c for c in watchlist if c not in real]
    if missing:
        print(f'  ⚠ 无实时数据: {", ".join(missing)} (停牌/代码无效, 跳过)')

    # 非交易时段判定: 全部无今日成交时间戳 (盘前/休市/停牌时腾讯返回上一交易日数据)
    today = datetime.now().strftime('%Y%m%d')
    traded = [c for c, r in real.items() if r['ts'][:8] == today]
    if not traded:
        last_ts = max((r['ts'] for r in real.values()), default='')
        last_date = (f'{last_ts[:4]}-{last_ts[4:6]}-{last_ts[6:8]}'
                     if len(last_ts) >= 8 else '未知')
        print(f'  ✗ 当前无今日实时行情 — 最新行情日期 {last_date} (盘前/休市/停牌)')
        print(f'    (15:57 收盘前运行可获当日行情; --wait 自动等待, 非交易日当天不会推送)')
        return None
    for c in real:
        if c not in traded:
            print(f'  ⚠ {c} {real[c]["name"]} 非今日成交 (停牌), 跳过')

    # ---- 3. 持仓卖出判定 (config trading.positions 真实持仓) ----
    strat = get_strategy(tr.get('strategy', 'watchlist'))
    pos_cfg = tr.get('positions', [])
    positions = []
    for pc in pos_cfg:
        cc = str(pc.get('code', '')).zfill(6)
        if cc not in real or cc not in sina_map:
            print(f'  ⚠ 持仓 {cc} 无实时/历史数据, 跳过')
            continue
        positions.append(Position(cc, real[cc]['name'], float(pc.get('entry', 0)),
                                  pd.Timestamp(datetime.now()), 0, 0, 'manual'))
    held = {p.code for p in positions}

    sells = []      # 触发卖出的持仓
    holds_info = []  # 全部持仓状态 (终端展示)
    for pos in positions:
        ind, _ = build_today_indicators(hist[sina_map[pos.code]], real[pos.code])
        is_sell, reason = strat.sell_signal(ind, pos.entry_price, 9999, 0)
        pnl = real[pos.code]['price'] / pos.entry_price - 1 if pos.entry_price > 0 else 0
        st = {'code': pos.code, 'name': pos.name, 'price': real[pos.code]['price'],
              'entry': pos.entry_price, 'pnl': pnl, 'reason': reason, '触发': bool(is_sell)}
        holds_info.append(st)
        if is_sell:
            sells.append(st)

    # ---- 4. 买入候选判定 (非持仓, 复刻引擎加分: 龙头+2 + 优先级加分) ----
    # 自选池内排名≤10 → 全部+2 (与 daily_plan/tests 构造一致)
    cands = []
    for code in watchlist:
        if code in held:
            continue
        if code not in real or code not in sina_map:
            continue
        ind, _ = build_today_indicators(hist[sina_map[code]], real[code])
        is_buy, score, reason = strat.buy_signal(ind)
        bonus = 2  # 龙头TOP10加分 (自选池内rank≤10全部满足)
        pb = pri.get(code) or pri.get(str(code).zfill(6))
        if pb:
            bonus += int(pb)
        score = float(score) + bonus
        cands.append({'code': code, 'name': real[code]['name'], 'price': real[code]['price'],
                      'score': score, 'reason': reason, '触发': score >= min_buy_score})
    cands.sort(key=lambda x: -x['score'])
    buys = [c for c in cands if c['触发']]

    # ---- 5. 终端输出 ----
    print()
    print('  📦 持仓:')
    if holds_info:
        for s in holds_info:
            pnl = f'{s["pnl"]:+.1%}'
            if s['触发']:
                print(f"    ⛔ {s['code']} {s['name']} 现价 {s['price']:.3f} 成本 "
                      f"{s['entry']:.3f} ({pnl}) → 触发卖出: {s['reason']}")
            else:
                print(f"    ✅ {s['code']} {s['name']} 现价 {s['price']:.3f} 成本 "
                      f"{s['entry']:.3f} ({pnl}) → 未触发卖出")
    else:
        print('    (未配置 trading.positions 真实持仓 — 仅检查买入候选)')
    print()
    print('  🎯 买入候选:')
    if cands:
        for c in cands:
            tag = '✅ 触发' if c['触发'] else '➖ 未满足'
            print(f"    {tag} {c['code']} {c['name']} 现价 {c['price']:.3f} "
                  f"得分 {c['score']:.1f}")
    else:
        print('    (无候选)')

    # ---- 6. 组装飞书消息 (有信号才推送) ----
    if not sells and not buys:
        print('\n  ✅ 今日无买卖信号, 不推送飞书')
        return None

    lines = [f'📌 A股收盘前检查 {datetime.now():%m-%d %H:%M}', '']
    if sells:
        lines.append('📦 卖出信号:')
        for s in sells:
            lines.append(f'  ⛔ {s["code"]} {s["name"]} 现价 {s["price"]:.3f} '
                         f'成本 {s["entry"]:.3f} ({s["pnl"]:+.1%})')
            lines.append(f'     {s["reason"]} → 明日开盘优先卖出')
        lines.append('')
    if buys:
        lines.append('🎯 买入信号:')
        for b in buys:
            lines.append(f'  ✅ {b["code"]} {b["name"]} 现价 {b["price"]:.3f} '
                         f'得分 {b["score"]:.1f}')
        lines.append('    已满足买入条件 → 明日开盘可买入')
        lines.append('')
    lines.append('(15:57实时价≈收盘价判定, 尾盘波动请确认)')
    return '\n'.join(lines)


def push_feishu(webhook, text):
    """推送飞书机器人 webhook"""
    r = requests.post(webhook, json={'msg_type': 'text', 'content': {'text': text}},
                      timeout=10)
    return r.status_code, r.text[:200]


def main():
    from main import load_config
    wait = '--wait' in sys.argv
    dry_run = '--dry-run' in sys.argv

    config = load_config()
    webhook = config.get('alert', {}).get('feishu_webhook', '')

    if wait:
        wait_until_1557()
    text = run_check(config, dry_run)
    if not text:
        return 0

    print()
    print('  ── 飞书消息 ──')
    print(text)

    if dry_run:
        print('\n  (--dry-run 调试模式, 不推送)')
    elif webhook:
        code, body = push_feishu(webhook, text)
        if code == 200:
            print('\n  ✅ 已推送到飞书')
        else:
            print(f'\n  ✗ 飞书推送失败: HTTP {code} {body}')
    else:
        print('\n  ⚠ 未配置 alert.feishu_webhook (config.json), 仅打印不推送')
    return 0


if __name__ == '__main__':
    sys.exit(main())

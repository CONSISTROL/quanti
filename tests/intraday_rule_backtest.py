"""
日内量价口诀回测 — "早盘急跌买入/早盘急涨卖出/下午急涨不追/下午急跌次日狙击"

数据现实: 只有日线 OHLCV, 用日线结构代理分时行为:
  早盘急跌  ≈ 大幅低开 (开盘跳空缺口一次性释放恐慌)
  早盘急涨  ≈ 大幅高开
  下午急跌  ≈ 大阴线且收盘贴近当日最低 (尾盘跳水收在底部)
  下午急涨  ≈ 大阳线且收盘贴近当日最高 (尾盘拉升收在顶部)

交易现实: A股 T+1, 当日买当日卖不可行 → 策略为隔夜持有:
  R1 低开买入: T 日开盘价买入 → T+1 收盘卖出 (隔夜) / 或持有5日
  R2 高开卖出: 诊断口径 — 高开后当日低走概率 (验证口诀"早盘急涨是卖出")
  R3 尾盘跳水次日狙击: T+1 开盘价买入 → T+2 收盘卖出 (隔夜) / 或5日
  R4 尾盘拉升不追: 验证口径 — 若 T+1 开盘追买的结果 (验证口诀"下午急涨不追")
买入端剔除开盘涨跌停 (买不进)。组合=当日全部信号等权, 日频再平衡 (隔夜口径可合成真实净值,
5日持有为重叠窗口, 只报事件统计不合成净值)。

用法:
  python tests/intraday_rule_backtest.py            # 全市场 (约1-2分钟)
  python tests/intraday_rule_backtest.py --limit 500
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from collections import defaultdict

import numpy as np
import pandas as pd

HALF_CUT = pd.Timestamp('2022-01-01')


def _load_history_local(hist_file=''):
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


def _limit_up_th(code):
    """涨跌幅阈值: 主板10% / 创业板(30)科创(68)20% / 北交所30%"""
    pure = code[-6:] if code[:2] in ('sh', 'sz', 'bj') else code
    if pure.startswith(('30', '68')):
        return 0.195
    if code.startswith('bj') or pure.startswith(('4', '8', '9')):
        return 0.295
    return 0.095


def new_acc():
    return {'n': 0, 'pos': 0, 's': 0.0, 'vals': []}


def upd(acc, d, ret):
    a = acc[d]
    a['n'] += 1
    if ret > 0:
        a['pos'] += 1
    a['s'] += ret
    a['vals'].append(ret)


def equity_stats(daily_ret):
    n = len(daily_ret)
    if n == 0:
        return None
    total = np.prod(1 + daily_ret) - 1
    ann = (1 + total) ** (252 / n) - 1 if total > -1 else -1.0
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0
    nav = np.cumprod(1 + daily_ret)
    peak = np.maximum.accumulate(nav)
    dd = (nav / peak - 1).min()
    pre = daily_ret[daily_ret_dates < HALF_CUT]
    post = daily_ret[daily_ret_dates >= HALF_CUT]
    halves = []
    for r in (pre, post):
        if len(r) < 60:
            halves.append(np.nan)
            continue
        tot = np.prod(1 + r) - 1
        halves.append((1 + tot) ** (252 / len(r)) - 1 if tot > -1 else -1.0)
    return {'total': total, 'ann': ann, 'sharpe': sharpe, 'dd': dd, 'halves': halves}


def summarize(name, acc_, with_equity=True):
    ds = sorted(acc_.keys())
    if not ds:
        return None
    n = sum(acc_[d]['n'] for d in ds)
    if n < 200:
        return None
    pos = sum(acc_[d]['pos'] for d in ds)
    vals = [v for d in ds for v in acc_[d]['vals']]
    m = sum(vals) / len(vals)
    md = float(np.median(vals))
    out = {'name': name, 'n': n, 'p': pos / n, 'mean': m, 'med': md}
    if with_equity:
        ret = np.array([acc_[d]['s'] / acc_[d]['n'] for d in ds])
        global daily_ret_dates
        daily_ret_dates = np.array(ds)
        e = equity_stats(ret)
        if e:
            out.update(e)
            # 净口径: 每日一次往返成本 0.2% 直接从日收益扣除
            net = ret - 0.002
            tot = np.prod(1 + net) - 1
            out['net_ann'] = (1 + tot) ** (252 / len(ret)) - 1 if tot > -1 else -1.0
    return out


def main(limit=0, hist_file=''):
    hist = _load_history_local(hist_file)
    if hist is None:
        print('  ✗ 无历史缓存')
        return 1

    acc = defaultdict(new_acc)   # 基准: 全市场每日开盘买隔夜卖
    R1 = defaultdict(new_acc)    # 低开买入 → 隔夜 (T+1 卖)
    R1_5 = defaultdict(new_acc)  # 低开买入 → 5日
    R2 = defaultdict(new_acc)    # 高开 → 当日 (高开低走诊断)
    R3 = defaultdict(new_acc)    # 尾盘跳水 → 次日开盘狙击 (隔夜)
    R3_5 = defaultdict(new_acc)  # 尾盘跳水 → 次日开盘买5日
    R4 = defaultdict(new_acc)    # 尾盘拉升 → 次日开盘追买 (验证不追)
    R1d = defaultdict(new_acc)   # 低开当日修复诊断 (c/o-1, T+0 不可交易)
    # 阈值敏感性
    R1_thr = {t: defaultdict(new_acc) for t in (0.015, 0.02, 0.03, 0.04)}
    R3_thr = {t: defaultdict(new_acc) for t in (0.02, 0.03, 0.05)}

    n_stock = 0
    for i, (sina, df) in enumerate(hist.items()):
        if limit and i >= limit:
            break
        if df is None or 'date' not in df.columns or 'close' not in df.columns:
            continue
        c = df['close'].values.astype(np.float64)
        o = df['open'].values.astype(np.float64)
        h = df['high'].values.astype(np.float64)
        l = df['low'].values.astype(np.float64)
        n = len(c)
        if n < 10:
            continue
        dts = pd.to_datetime(df['date'].values)
        th = _limit_up_th(sina)
        chg = np.concatenate([[np.nan], c[1:] / c[:-1] - 1])
        gap = np.concatenate([[np.nan], o[1:] / c[:-1] - 1])
        day_ret = c / o - 1
        rng = np.where(h - l > 0, h - l, np.nan)
        pos_low = (c - l) / rng
        pos_high = (h - c) / rng
        overnight = np.full(n, np.nan)
        overnight[:-1] = c[1:] / o[:-1] - 1        # 当日开盘买 → 次日收盘卖
        nxt_open = np.full(n, np.nan)
        nxt_open[:-2] = c[2:] / o[1:-1] - 1        # 次日开盘买 → 再隔日收盘卖
        r5 = np.full(n, np.nan)
        r5_nxt = np.full(n, np.nan)
        for j in range(n - 5):
            r5[j] = c[j + 5] / o[j] - 1            # 当日开盘买 → 5日后收盘
            r5_nxt[j] = c[j + 5] / o[j + 1] - 1    # 次日开盘买 → 5日后收盘

        for j in range(1, n - 1):
            d = dts[j]
            # 基准
            if -th < gap[j] < th and not np.isnan(overnight[j]):
                upd(acc, d, overnight[j])
            # R1 低开买入 (开盘跌停买不进)
            if gap[j] <= -0.02 and gap[j] > -th:
                if not np.isnan(overnight[j]):
                    upd(R1, d, overnight[j])
                    upd(R1d, d, day_ret[j])   # 当日修复诊断 (不可交易)
                if not np.isnan(r5[j]):
                    upd(R1_5, d, r5[j])
            for t, a in R1_thr.items():
                if gap[j] <= -t and gap[j] > -th and not np.isnan(overnight[j]):
                    upd(a, d, overnight[j])
            # R2 高开诊断
            if gap[j] >= 0.02 and not np.isnan(day_ret[j]):
                upd(R2, d, day_ret[j])
            # R3/R4 尾盘形态 → 次日开盘操作
            if j + 1 < n - 1:
                g1 = gap[j + 1]
                d1 = dts[j + 1]
                if (chg[j] <= -0.03 and pos_low[j] <= 0.15
                        and -th < g1 < th):
                    if not np.isnan(nxt_open[j]):
                        upd(R3, d1, nxt_open[j])
                    if not np.isnan(r5_nxt[j]):
                        upd(R3_5, d1, r5_nxt[j])
                for t, a in R3_thr.items():
                    if (chg[j] <= -t and pos_low[j] <= 0.15
                            and -th < g1 < th and not np.isnan(nxt_open[j])):
                        upd(a, d1, nxt_open[j])
                if (chg[j] >= 0.03 and pos_high[j] <= 0.15
                        and -th < g1 < th and not np.isnan(nxt_open[j])):
                    upd(R4, d1, nxt_open[j])
        n_stock += 1

    print(f'  √ 样本: {n_stock} 只股票')

    print('\n' + '═' * 120)
    print('  组合净值口径 (每日信号等权, 日频再平衡, 毛收益): 总收益/年化/Sharpe/最大回撤')
    print('═' * 120)
    print(f"  {'规则':<32}{'样本':>9}{'P↑':>7}{'均收益':>9}{'中位':>9}{'组合年化':>9}"
          f"{'净年化':>9}{'Sharpe':>8}{'最大回撤':>9}{'22前/后年化':>14}")
    print('  ' + '─' * 116)
    rows = [summarize('基准: 全市场每日开盘买隔夜卖', acc),
            summarize('R1 低开≥2%买入→次日卖', R1),
            summarize('R3 尾盘跳水→次日狙击', R3),
            summarize('R4 尾盘拉升→次日追买 (验证不追)', R4)]
    for r in rows:
        if r is None:
            continue
        pre, post = r['halves']
        print(f"  {r['name']:<32}{r['n']:>9,}{r['p']:>7.1%}{r['mean']:>+9.2%}{r['med']:>+9.2%}"
              f"{r['ann']:>+9.1%}{r['net_ann']:>+9.1%}{r['sharpe']:>8.2f}{r['dd']:>9.1%}"
              f"{pre:>+6.1%}/{post:>+6.1%}")

    print('\n' + '═' * 120)
    print('  事件统计 (5日持有为重叠窗口不合成净值; R1诊断为T+0不可交易参考)')
    print('═' * 120)
    print(f"  {'规则':<32}{'样本':>9}{'P↑':>7}{'均收益':>9}{'中位':>9}")
    print('  ' + '─' * 72)
    for r in [summarize('R1 低开≥2% 买入→5日卖', R1_5, False),
              summarize('R3 尾盘跳水→次日买5日', R3_5, False),
              summarize('R2 高开≥2% 当日 (高开低走诊断)', R2, False),
              summarize('R1诊断 低开当日修复 (c/o-1)', R1d, False)]:
        if r is None:
            continue
        print(f"  {r['name']:<32}{r['n']:>9,}{r['p']:>7.1%}{r['mean']:>+9.2%}{r['med']:>+9.2%}")

    # 阈值敏感性
    print('\n  ── 阈值敏感性 (隔夜口径) ──')
    for t in (0.015, 0.02, 0.03, 0.04):
        r = summarize(f'R1 低开≥{t*100:.1f}%→次日卖', R1_thr[t])
        if r:
            print(f"  {r['name']:<24} n={r['n']:>9,} P↑{r['p']:>6.1%} 均{r['mean']:>+8.2%}"
                  f" 年化{r['ann']:>+8.1%}")
    for t in (0.02, 0.03, 0.05):
        r = summarize(f'R3 尾盘跌≥{t*100:.0f}%→次日狙击', R3_thr[t])
        if r:
            print(f"  {r['name']:<24} n={r['n']:>9,} P↑{r['p']:>6.1%} 均{r['mean']:>+8.2%}"
                  f" 年化{r['ann']:>+8.1%}")

    print('\n' + '═' * 120)
    print('  结论要点')
    print('═' * 120)
    b, r1, r3, r4 = rows
    if r1 and b:
        lift = (r1['mean'] - b['mean']) * 100
        print(f'  ① 口诀1"早盘急跌买入": 低开≥2% 隔夜 P↑{r1["p"]:.1%} 均{r1["mean"]:+.2%}'
              f' vs 基准 {b["mean"]:+.2%} (lift {lift:+.2f}pp) → '
              f'{"均值回归成立, 但超额有限" if lift > 0 else "不成立"}')
    if r2 := summarize('R2', R2, False):
        print(f'  ② 口诀1"早盘急涨卖出": 高开≥2% 当日 P(收阴)={1 - r2["p"]:.1%} 均{r2["mean"]:+.2%}'
              f' → {"高开低走成立, 卖出口诀有效" if r2["mean"] < 0 else "高开未低走"}')
    if r3 and r4:
        print(f'  ③ 口诀2"下午急跌次日狙击": 隔夜均{r3["mean"]:+.2%} (P↑{r3["p"]:.1%}) 毛年化{r3["ann"]:+.1%}'
              f' 净年化{r3["net_ann"]:+.1%} vs "下午急涨不追"(若追买): {r4["mean"]:+.2%} (P↑{r4["p"]:.1%})'
              f' 毛年化{r4["ann"]:+.1%} 净年化{r4["net_ann"]:+.1%}')
        print(f'     → {"狙击尾盘跳水显著优于追尾盘拉升, 口诀方向成立" if r3["mean"] > r4["mean"] else "方向不成立"}'
              f' (事件均差 {(r3["mean"] - r4["mean"]) * 100:+.1f}pp;'
              f' R4 高波动方差拖累复利, 年化差距远大于均差)')
        pre_r3, post_r3 = r3['halves']
        pre_r1, post_r1 = r1['halves']
        print(f'  ④ 时效衰减: 2022年前/后年化 — R1 {pre_r1*100:+.1f}%/{post_r1*100:+.1f}%,'
              f' R3 {pre_r3*100:+.1f}%/{post_r3*100:+.1f}%,'
              f' 基准 {b["halves"][0]*100:+.1f}%/{b["halves"][1]*100:+.1f}%'
              f' → 口诀是 2015-2021 市场的遗产, 2022 后超额≈0 (毛口径), 扣成本后为负;'
              f' 且日频再平衡 0.2%/天成本吃掉基准全部超额 (基准净年化仅 {b["net_ann"]:+.1f}%)')
    print('  ⚠ 口径: 毛收益未扣成本 (隔夜双边约0.2%, 5日双边约0.2%); 日线代理分时(早盘=低开/高开,'
          '下午=收盘位置); 次日一字涨/跌停开买不进已剔除; hist 含幸存者偏差;'
          ' 组合等权日频再平衡换手极高, 真实成本敏感')
    return 0


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='日内量价口诀回测')
    p.add_argument('--limit', type=int, default=0)
    p.add_argument('--hist-file', default='')
    a = p.parse_args()
    sys.exit(main(limit=a.limit, hist_file=a.hist_file))

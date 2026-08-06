"""
成交信息 → 未来上涨概率 实证研究 (三色主力量价代理)

问题: 能否根据成交信息(短线活跃资金是否介入)预测股票未来上涨的概率?

数据局限: 历史缓存只有 OHLCV (无逐笔/大单/资金流数据), 因此用"量比 × 涨跌"
构建三色状态代理主力资金行为 (与通达信"主力状态三色"同思路, 但基于公开量价):
  红 = 放量上涨 (vol_ratio>=1.5 且涨)  → 短线活跃资金介入
  黄 = 平量/缩量 (观望)
  绿 = 放量下跌 (vol_ratio>=1.5 且跌)  → 资金出逃
  涨停/跌停日单独拆出 (涨停买不进, 跌停卖不出)

统计: 每状态 未来 1/3/5/10 日 上涨概率 vs 全市场无条件基准率, lift 倍数,
      2022 年前/后样本稳定性, 以及"次日开盘买入"可交易口径 (高开会吃掉部分收益).

用法:
  python tests/volume_prob.py                  # 全部股票 (约1分钟)
  python tests/volume_prob.py --limit 500      # 只扫前500只 (快速验证)
  python tests/volume_prob.py --hist-file hist_batch_20260802.pkl  # 指定历史快照
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # 统一 UTF-8 输出 (Windows 终端 GBK 会乱码)
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import numpy as np
import pandas as pd

# 状态编码
ST_LABELS = ['红·放量涨', '红·涨停(买不进)', '绿·放量跌', '绿·跌停',
             '黄·缩量涨', '黄·缩量跌', '黄·平量']
N_ST = len(ST_LABELS)
HORIZONS = ['fwd1', 'fwd3', 'fwd5', 'fwd10']
HORIZONS_O = ['fwd1o', 'fwd3o', 'fwd5o', 'fwd10o']  # 次日开盘买入口径
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


def main(limit=0, hist_file=''):
    hist = _load_history_local(hist_file)
    if hist is None:
        print('  ✗ 无本地历史缓存 (需要 cache/hist_batch_*.pkl)')
        return 1

    # 聚合桶: {hz -> (st, half) -> [n, pos, s, s2]}
    from collections import defaultdict
    acc = defaultdict(lambda: np.zeros((N_ST, 2, 4), dtype=np.float64))

    n_used = 0
    n_rows = 0
    for i, (code, df) in enumerate(hist.items()):
        if limit and i >= limit:
            break
        if df is None or 'volume' not in df.columns or 'date' not in df.columns:
            continue
        if len(df) < 60:
            continue
        s = pd.DataFrame({
            'c': df['close'].values.astype(float),
            'o': df['open'].values.astype(float),
            'v': df['volume'].values.astype(float),
            'd': pd.to_datetime(df['date'].values),
        })
        s = s[(s['v'] > 0) & (s['c'] > 0)].copy()
        if len(s) < 60:
            continue
        s['pc'] = s['c'].shift(1)
        s['chg'] = s['c'] / s['pc'] - 1
        s['ma5v'] = s['v'].rolling(5).mean()
        s['vr'] = s['v'] / s['ma5v']
        for k, hz in zip((1, 3, 5, 10), HORIZONS):
            s[hz] = s['c'].shift(-k) / s['c'] - 1          # 当日收盘买入, 持有k日收盘卖出
            s[hz + 'o'] = s['c'].shift(-k) / s['o'].shift(-1) - 1  # 次日开盘买入

        # ---- 三色状态 (量比 × 涨跌) ----
        lu = s['chg'] >= _limit_up_th(code)
        ld = s['chg'] <= -_limit_up_th(code)
        up = s['chg'] > 0.005
        dn = s['chg'] < -0.005
        vr = s['vr'].values
        st = np.full(len(s), 6, dtype=np.int8)  # 默认 黄·平量
        st[(vr >= 1.5) & up.values & ~lu.values] = 0  # 红·放量涨
        st[(vr >= 1.5) & up.values & lu.values] = 1   # 红·涨停
        st[(vr >= 1.5) & dn.values & ~ld.values] = 2  # 绿·放量跌
        st[(vr >= 1.5) & dn.values & ld.values] = 3   # 绿·跌停
        st[(vr <= 0.7) & up.values] = 4               # 黄·缩量涨
        st[(vr <= 0.7) & dn.values] = 5               # 黄·缩量跌
        half = (s['d'].values >= HALF_CUT).astype(np.int8)

        ok = s['fwd10o'].notna().values & s['vr'].notna().values & s['chg'].notna().values
        if not ok.any():
            continue
        n_used += 1
        n_rows += int(ok.sum())
        for hz in HORIZONS + HORIZONS_O:
            vals = s[hz].values
            for j in range(N_ST):
                m = ok & (st == j)
                if not m.any():
                    continue
                v = vals[m]
                h = half[m]
                for hh in (0, 1):
                    mm = h == hh
                    if not mm.any():
                        continue
                    w = v[mm]
                    acc[hz][j, hh, 0] += len(w)
                    acc[hz][j, hh, 1] += (w > 0).sum()
                    acc[hz][j, hh, 2] += w.sum()
                    acc[hz][j, hh, 3] += (w * w).sum()

    if n_rows == 0:
        print('  ✗ 无有效样本')
        return 1
    print(f'  √ 样本: {n_used} 只股票, {n_rows:,} 个交易日观察 '
          f'({HALF_CUT.year}年前/后分半检验)')
    print(f'  √ 三色口径: 红=放量涨(活跃资金介入) | 黄=平量/缩量(观望) | 绿=放量跌(资金出逃)')

    # ---- 基准率 ----
    print('\n' + '═' * 96)
    print('  全市场无条件基准率 (买入任意股票的胜率, 无成交信息时)')
    print('═' * 96)
    print(f"  {'口径':<14} {'P(1日涨)':>9} {'P(3日涨)':>9} {'P(5日涨)':>9} {'P(10日涨)':>10} "
          f"{'均收益5日':>10} {'均收益10日':>11}")
    print('  ' + '─' * 76)
    for hz, hzo, lbl in zip(HORIZONS, HORIZONS_O,
                            ['收盘买入', '次日开盘买入']):
        base = acc[hz][:, :, :]
        tot_n = base[:, :, 0].sum()
        tot_pos = base[:, :, 1].sum()
        tot_s = base[:, :, 2].sum()
        b5 = acc['fwd5']
        b10 = acc['fwd10']
        p5 = b5[:, :, 1].sum() / max(b5[:, :, 0].sum(), 1)
        p10 = b10[:, :, 1].sum() / max(b10[:, :, 0].sum(), 1)
        print(f"  {lbl:<14} {tot_pos / tot_n:>9.1%} "
              f"{acc['fwd3'][:, :, 1].sum() / max(acc['fwd3'][:, :, 0].sum(), 1):>9.1%} "
              f"{p5:>9.1%} {p10:>10.1%} "
              f"{b5[:, :, 2].sum() / max(b5[:, :, 0].sum(), 1):>+10.2%} "
              f"{b10[:, :, 2].sum() / max(b10[:, :, 0].sum(), 1):>+11.2%}")

    # ---- 三色状态条件概率表 ----
    def _row(st_j, hz):
        a = acc[hz][st_j]
        n = a[:, 0].sum()
        pos = a[:, 1].sum()
        s = a[:, 2].sum()
        return n, pos / n if n else np.nan, s / n if n else np.nan

    print('\n' + '═' * 96)
    print('  三色状态 → 未来上涨概率 (收盘买入口径, 与信号账户一致)')
    print('═' * 96)
    print(f"  {'状态':<16} {'样本':>10} {'占比':>6} {'P↑1日':>7} {'P↑3日':>7} {'P↑5日':>7} "
          f"{'P↑10日':>8} {'均收益5日':>9} {'均收益10日':>10} {'lift5':>6}")
    print('  ' + '─' * 86)
    base5 = _row(None, 'fwd5')[1]  # placeholder
    # 基准P↑5日
    b = acc['fwd5']
    base5 = b[:, :, 1].sum() / max(b[:, :, 0].sum(), 1)
    b10 = acc['fwd10']
    base10 = b10[:, :, 1].sum() / max(b10[:, :, 0].sum(), 1)
    tot = b[:, :, 0].sum()
    for j in range(N_ST):
        n1, p1, _ = _row(j, 'fwd1')
        n3, p3, _ = _row(j, 'fwd3')
        n5, p5, a5 = _row(j, 'fwd5')
        n10, p10, a10 = _row(j, 'fwd10')
        lift = p5 / base5 if base5 else np.nan
        print(f"  {ST_LABELS[j]:<16} {n1:>10,} {n1 / tot:>6.1%} {p1:>7.1%} {p3:>7.1%} "
              f"{p5:>7.1%} {p10:>8.1%} {a5:>+9.2%} {a10:>+10.2%} {lift:>6.2f}")

    # ---- 可交易口径 (次日开盘买入) ----
    print('\n' + '═' * 96)
    print('  可交易口径 (信号次日开盘价买入 → 持有N日收盘卖出, 高开会吃掉部分收益)')
    print('═' * 96)
    print(f"  {'状态':<16} {'P↑1日':>7} {'P↑5日':>7} {'P↑10日':>8} {'均收益1日':>9} "
          f"{'均收益5日':>9} {'均收益10日':>10}")
    print('  ' + '─' * 76)
    for j in range(N_ST):
        n1o, p1o, a1o = _row(j, 'fwd1o')
        n5o, p5o, a5o = _row(j, 'fwd5o')
        n10o, p10o, a10o = _row(j, 'fwd10o')
        print(f"  {ST_LABELS[j]:<16} {p1o:>7.1%} {p5o:>7.1%} {p10o:>8.1%} {a1o:>+9.2%} "
              f"{a5o:>+9.2%} {a10o:>+10.2%}")

    # ---- 稳定性 (2022年前/后) ----
    print('\n' + '═' * 96)
    print(f'  样本稳定性: {HALF_CUT.year}年前 vs 后 (同一状态在两个时期是否都有效)')
    print('═' * 96)
    print(f"  {'状态':<16} {'前P↑5日':>8} {'后P↑5日':>8} {'前均5日':>9} {'后均5日':>9} "
          f"{'前样本':>9} {'后样本':>9}")
    print('  ' + '─' * 70)
    for j in range(N_ST):
        a = acc['fwd5'][j]
        n0, pos0, s0 = a[0, 0], a[0, 1], a[0, 2]
        n1, pos1, s1 = a[1, 0], a[1, 1], a[1, 2]
        p0 = pos0 / n0 if n0 else np.nan
        p1 = pos1 / n1 if n1 else np.nan
        m0 = s0 / n0 if n0 else np.nan
        m1 = s1 / n1 if n1 else np.nan
        print(f"  {ST_LABELS[j]:<16} {p0:>8.1%} {p1:>8.1%} {m0:>+9.2%} {m1:>+9.2%} "
              f"{n0:>9,} {n1:>9,}")

    # ---- 结论 ----
    print('\n' + '═' * 96)
    print('  结论摘要 (状态显著强于基准: 前后段 P↑5日 均 > 基准, 且样本>1000)')
    print('═' * 96)
    b0 = acc['fwd5'][:, 0, 1].sum() / max(acc['fwd5'][:, 0, 0].sum(), 1)
    b1 = acc['fwd5'][:, 1, 1].sum() / max(acc['fwd5'][:, 1, 0].sum(), 1)
    print(f"  基准 P↑5日: {b0:.1%} ({HALF_CUT.year}年前) / {b1:.1%} ({HALF_CUT.year}年后)")
    for j in range(N_ST):
        a = acc['fwd5'][j]
        n0, pos0 = a[0, 0], a[0, 1]
        n1, pos1 = a[1, 0], a[1, 1]
        ok_all = n0 > 1000 and n1 > 1000
        sig0 = pos0 / n0 > b0 if n0 else False
        sig1 = pos1 / n1 > b1 if n1 else False
        mark = '● 稳定有效' if ok_all and sig0 and sig1 else \
               ('◇ 仅一段有效' if (sig0 or sig1) else '× 无预测力')
        print(f"  {ST_LABELS[j]:<16} {pos0 / n0:>7.1%} vs {b0:.1%} | {pos1 / n1:>7.1%} vs "
              f"{b1:.1%}  → {mark}")
    return 0


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='成交信息 → 未来上涨概率 实证研究')
    p.add_argument('--limit', type=int, default=0, help='只扫前N只股票 (快速验证)')
    p.add_argument('--hist-file', default='', help='指定历史快照 (如 hist_batch_20260802.pkl)')
    a = p.parse_args()
    sys.exit(main(limit=a.limit, hist_file=a.hist_file))

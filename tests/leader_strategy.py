"""
龙头战法实证 — 能否在更早的时机捕获板块龙头 (涨停识别 + 板块共振 + 成交额排名)

龙头战法 (A股游资体系) 核心 = 在板块启动日识别"最先涨停/成交额最大"的那只龙头,
首板次日买入吃连板主升。本项目数据只有日线 OHLCV (无封单/资金流/L2),
用三个可观测代理:
  龙头辨识度   = 当日同行业涨停股中 成交额排名第1 (板块龙头) / 全市场涨停股成交额 Top5
  板块启动     = 当日同行业涨停股 >= 3 (板块共振)
  连板高度     = 连续涨停天数 (首板/2板/3板+)

买点: 涨停次日 (T+1) 开盘价买入 — 比"二板后追高"更早; 一字板 (T+1 开盘即涨停) 买不进, 剔除。
持有: 当日收盘 (1日) / 第5个交易日收盘 (5日)。晋级率 = T+1 继续涨停的比例。

统计分组:
  B 首板次日开盘可买 (更早时机)
  C 连板次日开盘可买 (2板/3板+, 追高时机)
  D 板块龙头 (行业涨停>=2 且成交额第1)  vs  E 板块跟风
  F 全市场辨识度 Top5                    vs  G 其余涨停
  H 板块启动龙头 (行业涨停>=3 且成交额第1) vs  I 无板块共振

用法:
  python tests/leader_strategy.py            # 全市场 (约1-2分钟)
  python tests/leader_strategy.py --limit 500
"""
import glob
import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:  # 统一 UTF-8 输出 (Windows 终端 GBK 会乱码)
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

import numpy as np
import pandas as pd


def _limit_up_th(code):
    """涨跌幅阈值: 主板10% / 创业板(30)科创(68)20% / 北交所30%"""
    pure = code[-6:] if code[:2] in ('sh', 'sz', 'bj') else code
    if pure.startswith(('30', '68')):
        return 0.195
    if code.startswith('bj') or pure.startswith(('4', '8', '9')):
        return 0.295
    return 0.095


def _load_history_local(hist_file=''):
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


def _load_financial():
    files = [f for f in sorted(glob.glob(os.path.join('cache', 'financial_*.pkl')))
             if '_prev' not in f and '_period' not in f]
    if not files:
        return {}
    with open(files[-1], 'rb') as f:
        fin = pickle.load(f)
    ind_map = {}
    if fin is not None and '股票代码' in fin.columns:
        for _, r in fin.iterrows():
            try:
                ind_map[str(r['股票代码']).zfill(6)] = str(r.get('所处行业', ''))
            except Exception:
                continue
    return ind_map


def main(limit=0, hist_file=''):
    from quantlab.data_fetcher import _code_pure
    hist = _load_history_local(hist_file)
    if hist is None:
        print('  ✗ 无历史缓存')
        return 1
    ind_map = _load_financial()
    print(f'  (行业映射: {len(ind_map)} 只)')

    rows = []
    n_stock = 0
    for i, (sina, df) in enumerate(hist.items()):
        if limit and i >= limit:
            break
        if df is None or 'date' not in df.columns or 'close' not in df.columns:
            continue
        c = df['close'].values.astype(np.float64)
        n = len(c)
        if n < 40:
            continue
        o = df['open'].values.astype(np.float64)
        a = df['amount'].values.astype(np.float64) if 'amount' in df.columns else c * df['volume'].values.astype(np.float64)
        dts = pd.to_datetime(df['date'].values)
        chg = np.concatenate([[np.nan], c[1:] / c[:-1] - 1])
        th = _limit_up_th(sina)
        lu = chg >= th
        # 连板天数 (连续涨停, 含今日)
        streak = np.zeros(n, dtype=np.int16)
        for j in range(1, n):
            if lu[j]:
                streak[j] = streak[j - 1] + 1
        code = _code_pure(sina)
        ind = ind_map.get(code, '')
        for j in np.where(lu)[0]:
            if j + 1 >= n:
                continue
            gap = o[j + 1] / c[j] - 1                     # T+1 跳空幅度
            open_gap = o[j] / c[j - 1] - 1 if j >= 1 else np.nan  # 涨停日开盘跳空 (一字板打不进)
            tradable = gap < th                           # T+1 一字板买不进
            r1 = c[j + 1] / o[j + 1] - 1 if tradable else np.nan
            r5 = c[min(j + 5, n - 1)] / o[j + 1] - 1 if tradable else np.nan
            lb1 = c[j + 1] / c[j] - 1                     # 打板口径: 涨停日收盘价买入, 次日收盘卖
            lb5 = c[min(j + 5, n - 1)] / c[j] - 1         # 打板口径: 持有5日
            rows.append({
                'date': dts[j], 'code': code, 'ind': ind, 'amount': a[j],
                'streak': int(streak[j]), 'gap': gap, 'open_gap': open_gap, 'th': th,
                'tradable': tradable,
                'lu_next': bool(lu[j + 1]), 'r1': r1, 'r5': r5,
                'lb1': lb1, 'lb5': lb5,
            })
        n_stock += 1

    if not rows:
        print('  ✗ 无涨停事件')
        return 1
    ev = pd.DataFrame(rows)
    print(f'  √ 样本: {n_stock} 只股票, {len(ev):,} 个涨停日事件')

    # 板块共振与龙头排名
    ev['first'] = ev['streak'] == 1
    ev['sector_n'] = ev.groupby(['date', 'ind'])['code'].transform('size')
    ev['rank_sector'] = ev.groupby(['date', 'ind'])['amount'].rank(ascending=False, method='first')
    ev['rank_mkt'] = ev.groupby('date')['amount'].rank(ascending=False, method='first')
    # 市场情绪温度 (当日全市场涨停率, 尺度无关)
    lu_ratio = ev.groupby('date').size() / n_stock
    ev['lu_ratio'] = ev['date'].map(lu_ratio)
    ev['mood'] = np.where(ev['lu_ratio'] >= 0.012, '高热(涨停率≥1.2%)',
                          np.where(ev['lu_ratio'] <= 0.004, '低温(≤0.4%)', '中温'))

    def stats(sub):
        t = sub[sub['tradable']]
        if len(t) < 50:
            return None
        r1 = t['r1'].dropna()
        r5 = t['r5'].dropna()
        return {
            'n': len(sub), 'n_t': len(t), '一字率': 1 - len(t) / len(sub),
            '晋级率': float(sub['lu_next'].mean()),
            '1日P↑': float((r1 > 0).mean()), '1日均': float(r1.mean()),
            '1日中位': float(r1.median()),
            '5日P↑': float((r5 > 0).mean()), '5日均': float(r5.mean()),
            '5日中位': float(r5.median()),
        }

    groups = [
        ('B 首板次日 (更早时机)', ev[ev['first']]),
        ('C 连板次日 (追高时机)', ev[~ev['first']]),
        ('D 板块龙头 (行业≥2只, 成交额第1)', ev[(ev['sector_n'] >= 2) & (ev['rank_sector'] == 1)]),
        ('E 板块跟风 (行业≥2只, 非第1)', ev[(ev['sector_n'] >= 2) & (ev['rank_sector'] > 1)]),
        ('F 全市场辨识度 Top5', ev[ev['rank_mkt'] <= 5]),
        ('G 其余涨停 (辨识度 Top5 以外)', ev[ev['rank_mkt'] > 5]),
        ('H 板块启动龙头 (行业≥3只, 成交额第1)', ev[(ev['sector_n'] >= 3) & (ev['rank_sector'] == 1)]),
        ('I 无板块共振 (行业<2只)', ev[(ev['sector_n'] < 2) | (ev['ind'] == '')]),
    ]

    print('\n' + '═' * 110)
    print('  龙头战法近似回测: 涨停日事件 → T+1 开盘买入 (一字板剔除) → 持有1日/5日')
    print('  晋级率 = T+1 继续涨停的比例; 收益为毛收益 (未扣双边0.2%成本)')
    print('═' * 110)
    print(f"  {'分组':<38}{'事件':>7}{'可买':>7}{'一字率':>7}{'晋级率':>7}"
          f"{'1日P↑':>7}{'1日均':>8}{'1日中位':>8}{'5日P↑':>7}{'5日均':>8}{'5日中位':>8}")
    print('  ' + '─' * 108)
    base5 = None
    for lbl, sub in groups:
        s = stats(sub)
        if s is None:
            print(f'  {lbl:<38} 样本不足')
            continue
        if lbl.startswith('B'):
            base5 = s
        print(f"  {lbl:<38}{s['n']:>7,}{s['n_t']:>7,}{s['一字率']:>7.1%}{s['晋级率']:>7.1%}"
              f"{s['1日P↑']:>7.1%}{s['1日均']:>+8.2%}{s['1日中位']:>+8.2%}"
              f"{s['5日P↑']:>7.1%}{s['5日均']:>+8.2%}{s['5日中位']:>+8.2%}")

    # 首板内部的板块结构: 首板+板块启动 vs 首板+无共振
    print('\n  ── 首板 (更早时机) 内部细分 ──')
    sub = ev[ev['first']]
    for lbl, m in [
        ('首板·板块启动龙头 (行业≥3, 成交额第1)', (sub['sector_n'] >= 3) & (sub['rank_sector'] == 1)),
        ('首板·板块龙头 (行业≥2, 成交额第1)', (sub['sector_n'] >= 2) & (sub['rank_sector'] == 1)),
        ('首板·板块跟风', (sub['sector_n'] >= 2) & (sub['rank_sector'] > 1)),
        ('首板·无共振', (sub['sector_n'] < 2) | (sub['ind'] == '')),
    ]:
        s = stats(sub[m])
        if s is None:
            print(f'  {lbl:<40} 样本不足')
            continue
        print(f"  {lbl:<40}{s['n']:>7,}{s['n_t']:>7,}{s['一字率']:>7.1%}{s['晋级率']:>7.1%}"
              f"{s['1日P↑']:>7.1%}{s['1日均']:>+8.2%}{s['1日中位']:>+8.2%}"
              f"{s['5日P↑']:>7.1%}{s['5日均']:>+8.2%}{s['5日中位']:>+8.2%}")

    # 连板高度: 首板后晋级 vs 2板后晋级
    print('\n  ── 连板高度 (晋级率递减假设检验) ──')
    for k in (1, 2, 3, 4):
        s = stats(ev[ev['streak'] == k])
        if s is None:
            continue
        tag = {1: '首板', 2: '2板', 3: '3板', 4: '4板+'}[k] if k < 4 else '4板+'
        print(f"  {tag:<6} 次日(晋级)率 {s['晋级率']:>7.1%} | 5日P↑ {s['5日P↑']:>7.1%}"
              f" 5日均 {s['5日均']:>+8.2%} 中位 {s['5日中位']:>+8.2%} (n={s['n']:,})")

    # 打板口径 (游资实际买点: 涨停日收盘排队买入, 靠次日晋级)
    print('\n' + '═' * 110)
    print('  打板口径 (涨停日收盘价买入 → 次日/5日收盘卖出)')
    print('  剔除: 涨停日一字板(打不进) / 次日一字跌停开与跌停收盘(卖不出); 次日一字涨停开可卖出保留')
    print('═' * 110)
    print(f"  {'分组':<38}{'事件':>7}{'隔日P↑':>7}{'隔日均':>8}{'隔日中位':>8}"
          f"{'5日P↑':>7}{'5日均':>8}{'5日中位':>8}")
    print('  ' + '─' * 100)

    def stats_lb(sub):
        # 打板现实约束: ① 涨停日一字板打不进 (开盘已涨停, open_gap<th) ② 次日一字跌停开卖不出
        # ③ 次日收盘跌停卖不出 (lb1<=-th); 一字涨停开可卖出保留 (大赚样本)
        t = sub[(sub['gap'] > -sub['th']) & (sub['open_gap'] < sub['th'] * 0.98)
                & (sub['lb1'] > -sub['th'] * 0.98)]
        if len(t) < 50:
            return None
        lb1 = t['lb1'].dropna()
        lb5 = t['lb5'].dropna()
        return {
            'n': len(t), 'p1': float((lb1 > 0).mean()), 'm1': float(lb1.mean()),
            'md1': float(lb1.median()),
            'p5': float((lb5 > 0).mean()), 'm5': float(lb5.mean()), 'md5': float(lb5.median()),
        }

    for lbl, m in [
        ('打板·首板 (全部)', ev['first']),
        ('打板·板块启动龙头 (首板+行业≥3)', ev['first'] & (ev['sector_n'] >= 3) & (ev['rank_sector'] == 1)),
        ('打板·连板 (2板+)', ~ev['first']),
        ('打板·4板+', ev['streak'] >= 4),
        ('打板·首板·高热期', ev['first'] & (ev['mood'] == '高热(涨停率≥1.2%)')),
        ('打板·首板·低温期', ev['first'] & (ev['mood'] == '低温(≤0.4%)')),
    ]:
        s = stats_lb(ev[m])
        if s is None:
            print(f'  {lbl:<38} 样本不足')
            continue
        print(f"  {lbl:<38}{s['n']:>7,}{s['p1']:>7.1%}{s['m1']:>+8.2%}{s['md1']:>+8.2%}"
              f"{s['p5']:>7.1%}{s['m5']:>+8.2%}{s['md5']:>+8.2%}")

    # 情绪周期条件: 龙头战法的命门
    print('\n  ── 情绪周期条件 (首板次日开盘买入, 5日持有) ──')
    sub = ev[ev['first']]
    for mood in ('高热(涨停率≥1.2%)', '中温', '低温(≤0.4%)'):
        s = stats(sub[sub['mood'] == mood])
        if s is None:
            continue
        print(f"  {mood:<22} n={s['n_t']:>7,} 晋级率 {s['晋级率']:>6.1%}"
              f" | 5日P↑ {s['5日P↑']:>6.1%} 5日均 {s['5日均']:>+8.2%} 中位 {s['5日中位']:>+8.2%}")
    s_hi = stats(sub[(sub['mood'] == '高热(涨停率≥1.2%)') & (sub['sector_n'] >= 3)
                     & (sub['rank_sector'] == 1)])
    if s_hi:
        print(f"  {'高热+板块启动龙头':<22} n={s_hi['n_t']:>7,} 晋级率 {s_hi['晋级率']:>6.1%}"
              f" | 5日P↑ {s_hi['5日P↑']:>6.1%} 5日均 {s_hi['5日均']:>+8.2%} 中位 {s_hi['5日中位']:>+8.2%}")

    # 结论要点
    print('\n' + '═' * 110)
    print('  结论要点')
    print('═' * 110)
    if base5:
        b = stats(ev[ev['first']])
        d = stats(ev[(ev['sector_n'] >= 2) & (ev['rank_sector'] == 1) & ev['first']])
        h = stats(ev[(ev['sector_n'] >= 3) & (ev['rank_sector'] == 1) & ev['first']])
        print(f'  ① 首板次日买入: 1日 P↑{b["1日P↑"]:.1%} 均{b["1日均"]:+.2%}'
              f' | 5日 P↑{b["5日P↑"]:.1%} 均{b["5日均"]:+.2%} 中位{b["5日中位"]:+.2%}')
        if d:
            print(f'  ② 板块龙头(首板): 5日 P↑{d["5日P↑"]:.1%} 均{d["5日均"]:+.2%}'
                  f'  vs 首板整体 {b["5日均"]:+.2%} → '
                  f'{"龙头显著占优" if d["5日均"] > b["5日均"] + 0.005 else "无明显龙头溢价"}')
        if h:
            print(f'  ③ 板块启动龙头(首板+行业≥3): 5日 P↑{h["5日P↑"]:.1%} 均{h["5日均"]:+.2%}'
                  f' 中位{h["5日中位"]:+.2%} (n={h["n_t"]:,})')
        print('  ⚠ 口径: 毛收益未扣成本; hist 含幸存者偏差; 重叠窗口 (同一只股票的连板事件) 非独立;')
        print('    一字板买不进已剔除 (打板者以涨停价排队才能成交, 实际成本更高)')
    return 0


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='龙头战法实证')
    p.add_argument('--limit', type=int, default=0, help='只扫前N只股票 (快速验证)')
    p.add_argument('--hist-file', default='', help='指定历史快照')
    a = p.parse_args()
    sys.exit(main(limit=a.limit, hist_file=a.hist_file))

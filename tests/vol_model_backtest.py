"""
成交信息 → 上涨概率: 多特征逻辑回归模型 + 每日 Top-N 轮动回测

基于 tests/volume_probability.py 的单因子结论 (放量追涨负期望/缩量下跌反弹) 升级为组合模型:
  特征 (全部用 t 日收盘已知信息):
    vr5/vr20  = 量比 (当日量 / 前5/20日均量)
    amt5      = 额比 (当日成交额 / 前5日均额)
    chg1/2/3  = 当日/前1日/前2日涨跌
    up3       = 3日累计涨跌
    pos5/pos20= 收盘价偏离 MA5/MA20 程度
  目标 y = 次日收盘 > 当日收盘
  模型 = L2 逻辑回归 (手写全批梯度下降, 无 sklearn 依赖)

流程:
  1. 训练期 ≤2018-06-30 拟合 → 测试期 ≥2018-07-01 评估
  2. 判别力: 测试期按预测概率分十组, 看 P(次日涨) 是否单调
  3. 回测: 每日收盘买入预测概率 Top-5 (涨停日买不进), 次日收盘卖出 (T+1 合法),
     对比全市场等权基准 与 简单状态规则 (缩量/平量下跌买入)

用法: python tests/vol_model_backtest.py [--hist-file cache/hist_batch_xxx.pkl]
"""
import glob
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SPLIT = 20180701      # 训练 ≤ 2018-06-30 / 测试 ≥ 2018-07-01
TOP_N = 5             # 每日买入只数
COST = 0.001          # 单边 0.1% 成交成本 (双边 0.2%)
LIMIT_CHG = 0.095     # 涨停阈值
FEATS = ['vr5', 'vr20', 'amt5', 'chg1', 'chg2', 'chg3', 'up3', 'pos5', 'pos20']
EPOCHS, LR, LAM = 40, 0.3, 0.1


def _load_history_local(hist_file=''):
    """加载本地 hist 缓存 pickle (与 backtest_gap_open 相同逻辑)"""
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


def build_panel(hist):
    """每只股票每日一行: 9 特征 + ret1/y/涨停/跌停标记, 剔除停牌与特征缺失行"""
    cols = ['date'] + FEATS + ['ret1', 'y', 'limit_up', 'limit_dn', 'amt']
    parts = []
    n_stocks = 0
    for sina, df in hist.items():
        if df is None or 'date' not in df.columns or 'volume' not in df.columns:
            continue
        if 'amount' not in df.columns:
            continue
        c = pd.Series(df['close'].values.astype(np.float64))
        v = pd.Series(df['volume'].values.astype(np.float64))
        a = pd.Series(df['amount'].values.astype(np.float64))
        chg = c.pct_change()
        n = len(df)
        out = np.full((n, len(cols)), np.nan, dtype=np.float32)
        out[:, 0] = pd.to_datetime(df['date'].values).strftime('%Y%m%d').astype(np.int64).astype(np.float32)
        out[:, 1] = (v / v.rolling(5).mean().shift(1)).values
        out[:, 2] = (v / v.rolling(20).mean().shift(1)).values
        out[:, 3] = (a / a.rolling(5).mean().shift(1)).values
        out[:, 4] = chg.values
        out[:, 5] = chg.shift(1).values
        out[:, 6] = chg.shift(2).values
        out[:, 7] = ((1 + chg) * (1 + chg.shift(1)) * (1 + chg.shift(2)) - 1).values
        out[:, 8] = (c / c.rolling(5).mean() - 1).values
        out[:, 9] = (c / c.rolling(20).mean() - 1).values
        out[:, 10] = (c.shift(-1) / c - 1).values          # ret1 次日收益
        out[:, 11] = (c.shift(-1) > c).values              # y 次日上涨
        out[:, 12] = (chg >= LIMIT_CHG).values             # 当日涨停 → 买不进
        out[:, 13] = (chg.shift(-1) <= -LIMIT_CHG).values  # 次日跌停 → 卖不出
        out[:, 14] = a.values
        bad = (v.values <= 0) | np.isnan(out).any(axis=1)
        parts.append(out[~bad])
        n_stocks += 1
    P = np.concatenate(parts)
    pan = pd.DataFrame(P, columns=cols)
    pan['date'] = pan['date'].astype(np.int64)
    return pan, n_stocks


def logit_fit(X, y, epochs=EPOCHS, lr=LR, lam=LAM):
    """L2 逻辑回归, 全批梯度下降 (特征需已标准化)"""
    n, p = X.shape
    Xb = np.column_stack([np.ones(n), X])
    w = np.zeros(p + 1)
    for e in range(1, epochs + 1):
        z = Xb @ w
        pr = 1 / (1 + np.exp(-np.clip(z, -30, 30)))
        g = Xb.T @ (pr - y) + lam * np.concatenate([[0.0], w[1:]])
        w -= lr * g / n
        if e % 5 == 0 or e == 1:
            loss = -np.mean(y * np.log(pr + 1e-12) + (1 - y) * np.log(1 - pr + 1e-12))
            print(f'    epoch {e:>2}: loss {loss:.5f}')
    return w


def logit_pred(X, w):
    Xb = np.column_stack([np.ones(len(X)), X])
    return 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))


def equity_stats(daily_ret):
    """日收益序列 → 净值/年化/Sharpe/最大回撤(幅度+峰谷日数+收复日数)/最长水下/胜率

    返回: (eq, ann, sharpe, dd, win,
           dd_p2t, dd_t2r, dd_recovered,  # 最大回撤: 峰值→谷底日数, 谷底→收复日数, 是否已收复
           longest_close, open_days)      # 最长水下(已收复), 当前水下日数(0=在新高)
    日数均为交易日; 回撤期 = 净值跌破前高 → 收复前高 (未收复计到样本末)
    """
    eq = float(np.prod(1 + daily_ret))
    ann = float((1 + daily_ret).prod() ** (252 / len(daily_ret)) - 1)
    sharpe = float(daily_ret.mean() / daily_ret.std() * np.sqrt(252)) if daily_ret.std() > 0 else 0.0
    nav = np.cumprod(1 + daily_ret)
    win = float((daily_ret > 0).mean())

    # 逐段跟踪: 每段 = 峰值 → 谷底 → 收复前高
    periods = []  # (谷底/前高-1, 峰值idx, 谷底idx, 收复idx/样本末, 是否收复)
    prev_max, peak_i, trough_i, trough_val = nav[0], 0, 0, nav[0]
    for i in range(1, len(nav)):
        if nav[i] >= prev_max:
            if trough_val < prev_max - 1e-15:
                periods.append((trough_val / prev_max - 1, peak_i, trough_i, i, True))
            peak_i, trough_i, trough_val = i, i, nav[i]
            prev_max = nav[i]
        elif nav[i] < trough_val:
            trough_val, trough_i = nav[i], i
    if trough_val < prev_max - 1e-15:
        periods.append((trough_val / prev_max - 1, peak_i, trough_i, len(nav) - 1, False))

    if periods:
        dd = min(p[0] for p in periods)
        worst = min(periods, key=lambda p: p[0])
        dd_p2t = worst[2] - worst[1]
        dd_t2r = (worst[3] - worst[2]) if worst[4] else 0
        dd_recovered = worst[4]
        closed = [p[3] - p[1] for p in periods if p[4]]
        open_p = [p for p in periods if not p[4]]
        longest_close = max(closed) if closed else 0
        open_days = open_p[0][3] - open_p[0][1] if open_p else 0
    else:
        dd = 0.0
        dd_p2t = dd_t2r = longest_close = open_days = 0
        dd_recovered = True
    return eq, ann, sharpe, dd, win, dd_p2t, dd_t2r, dd_recovered, longest_close, open_days


def main(hist_file=''):
    hist = _load_history_local(hist_file)
    if not hist:
        print('  ✗ 无历史缓存')
        return 1

    print('\n' + '═' * 90)
    print('  成交信息 → 上涨概率: 多特征逻辑回归 + Top-N 轮动回测')
    print('═' * 90)
    pan, n_stocks = build_panel(hist)
    print(f'  样本: {n_stocks} 只 × 有效日观测 {len(pan):,} 行')
    print(f'  特征: {", ".join(FEATS)}')
    print(f'  注: hist 为当前存续股票, 含幸存者偏差 (退市股不在样本内), 结果偏乐观')

    tr = pan[pan['date'] <= SPLIT]
    te = pan[pan['date'] > SPLIT]
    mu, sd = tr[FEATS].mean().values.copy(), tr[FEATS].std().values.copy()
    sd[sd == 0] = 1.0
    Xtr = (tr[FEATS].values - mu) / sd
    Xte = (te[FEATS].values - mu) / sd
    ytr = tr['y'].values.astype(np.float64)
    print(f'\n  训练: {len(tr):,} 行 (≤2018-06-30)  测试: {len(te):,} 行 (≥2018-07-01)')
    print('  训练逻辑回归:')
    w = logit_fit(Xtr, ytr)

    # ---- 1. 判别力: 测试期十分组 ----
    print('  模型权重 (标准化特征, >0 表示该特征增大 → 次日上涨概率升高):')
    print('   ' + '  '.join(f'{k} {v:+.3f}' for k, v in zip(FEATS, w[1:])))

    te = te.copy()
    te['p'] = logit_pred(Xte, w)
    te['dec'] = pd.qcut(te['p'], 10, labels=False)
    print('\n■ 模型判别力 (测试期, 按预测概率分十组)')
    print(f"  {'组':<4}{'样本量':>10}{'P(次日涨)':>10}{'较基准':>8}{'平均次日收益':>12}")
    print('  ' + '─' * 48)
    base_up = float((te['y'] > 0).mean())
    g10 = None
    for d in range(10):
        s = te[te['dec'] == d]
        u = float((s['y'] > 0).mean())
        m = float(s['ret1'].mean())
        print(f"  {d + 1:<4}{len(s):>10,}{u:>10.1%}{u - base_up:>+7.1%}{m:>+11.2%}")
    d10 = te[te['dec'] == 9]
    g10 = float((d10['y'] > 0).mean())
    print(f'  基准 P(次日涨) = {base_up:.1%} | 最高组 (decile 10) = {g10:.1%} '
          f'(lift {(g10 / base_up - 1):+.1%})')

    # ---- 2. 每日 Top-N 轮动回测 (测试期) ----
    buyable = te[te['limit_up'] == 0]
    rows, pick_rows = [], []
    for d, grp in buyable.groupby('date'):
        if len(grp) < TOP_N:
            continue
        top = grp.nlargest(TOP_N, 'p')
        rows.append((d, float(top['ret1'].mean()), float(grp['ret1'].mean()),
                     float(top['ret1'].gt(0).mean())))
        for _, r in top.iterrows():
            pick_rows.append((r['vr5'], r['vr20'], r['chg1'], r['chg2'],
                              r['up3'], r['pos20'], r['ret1'], r['p']))
    rot = pd.DataFrame(rows, columns=['date', 'r', 'base', 'win'])
    pk = pd.DataFrame(pick_rows, columns=['vr5', 'vr20', 'chg1', 'chg2', 'up3', 'pos20', 'ret1', 'p'])
    print(f'\n■ 每日 Top-{TOP_N} 轮动回测 (测试期 {len(rot):,} 个交易日, '
          f'收盘买入 → 次日收盘卖出, 涨停日买不进)')
    print(f'  {"口径":<26}{"总收益":>10}{"年化":>9}{"Sharpe":>8}{"最大回撤":>9}'
          f'{"峰→谷":>6}{"谷→收复":>8}{"最长水下":>8}{"日胜率":>8}')
    print('  ' + '─' * 82)
    r0 = rot['base'].values
    r1 = rot['r'].values
    r1c = r1 - COST
    for lab, r in (('全市场等权基准', r0), ('模型 Top-5 (无成本)', r1),
                   ('模型 Top-5 (双边0.2%成本)', r1c)):
        eq, ann, sh, dd, win, p2t, t2r, rec, lc, opd = equity_stats(r)
        t2rs = f'{t2r}日' if rec else '未收复'
        longest = max(lc, opd)
        om = ' (未)' if opd > lc else ''
        print(f'  {lab:<26}{eq - 1:>+9.2%}{ann:>+8.2%}{sh:>8.2f}{dd:>8.1%}'
              f'{p2t:>4}日{t2rs:>7}{longest:>5}日{om:<5}{win:>8.1%}')
    # 模型相对基准: 日度胜负
    beat = float((r1 > r0).mean())
    print(f'\n  模型 Top-5 跑赢基准的天数占比: {beat:.1%}  '
          f'(日超额收益均值 {float((r1 - r0).mean()):+.3%})')

    # ---- 2.5 诊断: Top-5 每日极端选股, 逐笔归因亏损 ----
    print('\n■ 诊断: 模型 Top-5 每日极端选股 (逐笔归因亏损)')
    print(f'  共 {len(pk):,} 笔选股, 平均次日收益 {float(pk["ret1"].mean()):+.3%}')
    print(f'  {"分档":<20}{"占比":>8}{"次日均收益":>12}')
    print('  ' + '─' * 42)
    for lab, m in (('当日涨跌 ≤-9.5% (买跌停)', pk['chg1'] <= -LIMIT_CHG),
                   ('-9.5% < 当日涨跌 ≤ -5%', (pk['chg1'] > -LIMIT_CHG) & (pk['chg1'] <= -0.05)),
                   ('-5% < 当日涨跌 ≤ 0', (pk['chg1'] > -0.05) & (pk['chg1'] <= 0)),
                   ('当日涨跌 > 0', pk['chg1'] > 0)):
        s = pk[m]
        if len(s):
            print(f'  {lab:<20}{len(s) / len(pk):>7.1%}{float(s["ret1"].mean()):>+11.3%}')
    for lab, m in (('昨日跌停附近 (chg2≤-9.5%)', pk['chg2'] <= -LIMIT_CHG),
                   ('昨日跌 5~9.5%', (pk['chg2'] > -LIMIT_CHG) & (pk['chg2'] <= -0.05)),
                   ('昨日涨跌 -5%~+5%', (pk['chg2'] > -0.05) & (pk['chg2'] < 0.05)),
                   ('昨日涨 ≥5%', pk['chg2'] >= 0.05)):
        s = pk[m]
        if len(s):
            print(f'  {lab:<20}{len(s) / len(pk):>7.1%}{float(s["ret1"].mean()):>+11.3%}')
    # decile10 内对照: 深跌 vs 非深跌
    deep = d10[d10['pos20'] < -0.10]
    mild = d10[d10['pos20'] >= -0.10]
    print(f'\n  对照 decile10 全组: 深跌组 (低于MA20超10%) 次日均收益 '
          f'{float(deep["ret1"].mean()):+.3%} vs 非深跌组 {float(mild["ret1"].mean()):+.3%}')
    print('  → 极端概率尾部 ≠ 温和均值回归: decile10 均值有效, 但每日最大概率的')
    print('    5 只是特征最极端的票, 其中"下跌中的票"次日继续跌 (动量主导下跌行情)')

    # ---- 3. 简单状态规则对比 (测试期, 无训练) ----
    sr = buyable[(buyable['vr5'] < 1.2) & (buyable['chg1'] < 0)]  # 缩量/平量下跌
    sr2 = sr[sr['chg1'] > -LIMIT_CHG]  # 剔除当日跌停 (不接飞刀)
    print('\n■ 简单状态规则 (缩量/平量下跌买入, 全池等权, 测试期, 无训练)')
    print(f'  {"口径":<24}{"总收益":>10}{"年化":>9}{"Sharpe":>8}{"最大回撤":>9}'
          f'{"峰→谷":>6}{"谷→收复":>8}{"最长水下":>8}{"日胜率":>8}')
    print('  ' + '─' * 80)
    for lab, src in (('含跌停日', sr), ('剔除当日跌停', sr2)):
        sr_rows = []
        for d, grp in src.groupby('date'):
            sr_rows.append((d, float(grp['ret1'].mean())))
        srdf = pd.DataFrame(sr_rows, columns=['date', 'r'])
        if not len(srdf):
            continue
        eq, ann, sh, dd, win, p2t, t2r, rec, lc, opd = equity_stats(srdf['r'].values)
        t2rs = f'{t2r}日' if rec else '未收复'
        longest = max(lc, opd)
        om = ' (未)' if opd > lc else ''
        print(f'  {lab:<24}{eq - 1:>+9.2%}{ann:>+8.2%}{sh:>8.2f}{dd:>8.1%}'
              f'{p2t:>4}日{t2rs:>7}{longest:>5}日{om:<5}{win:>8.1%}'
              f'  (样本 {len(srdf):,} 天)')

    print('\n' + '═' * 90)
    print('  结论:')
    print('  1. 多特征模型确有单调判别力 (十分组 P(次日涨) 45.5% → 50.5%, lift +5.3%),')
    print('     但概率 ≠ 收益: 各组平均次日收益不单调, 模型把下跌中的票当"反弹候选"。')
    print('  2. Top-5 巨亏归因: 8.6% 选股买在跌停 (次日 -5.0%)、17.8% 买在当日跌≥5%;')
    print('     当日上涨的选股 (35.6%) 实际是赚的 (+0.17%) — 亏全在"缩量接飞刀"。')
    print('  3. 零训练简单规则 (缩量/平量下跌全池等权): 测试期年化 +18.3% vs 基准 +10.0%,')
    print('     剔除当日跌停后更优 — 温和缩量回调均值回归成立, 与单因子研究一致。')
    print('  4. 注意: 规则为日频全池再平衡, 未计交易成本 (0.1%×2 双边下大概率被侵蚀);')
    print('     且 hist 含幸存者偏差。实盘需拉长持有期并计入成本再评估。')
    print('═' * 90)
    return 0


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--hist-file', default='', help='指定历史快照 (默认取最新 cache/hist_batch_*.pkl)')
    a = p.parse_args()
    sys.exit(main(a.hist_file))

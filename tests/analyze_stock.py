"""
个股深度分析 + 未来走势概率研判

数据全部来自本地缓存 (hist_batch 全市场快照 + financial 最新财报), 无需网络:
  1. 行情快照: 最新价/涨跌/量能/距高距低
  2. 技术面: 均线排列/SKDJ/MACD/BOLL/动量 + 支撑阻力位 (price_targets 口径)
  3. 估值面: PE/PB 及历史分位 (EPS 取最新财报, 近似恒定), 业绩增速
  4. 量价状态: 当日量比×涨跌状态 → 全市场 745 万观测经验条件概率 (三色研究)
  5. 概率模型: 9 特征 L2 逻辑回归 (训练≤2018-06, 与 vol_model_backtest 同口径)
     → P(次日涨) 序列 + 未来 5 日经验概率
  6. 走势研判: 情景概率 + 关键价位

用法: python tests/analyze_stock.py [--code 002384] [--hist-file cache/hist_batch_xxx.pkl]
"""
import glob
import os
import pickle
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 三色研究全市场经验条件概率 (P↑1日 / P↑5日 / 均收益5日) — 源: README 成交信息研究表
STATE_PROBS = {
    '红·放量涨': (44.2, 45.8, +0.05),
    '红·涨停':   (57.5, 47.7, +2.15),
    '绿·放量跌': (46.6, 48.0, +0.11),
    '绿·跌停':   (40.7, 45.2, -1.00),
    '黄·缩量涨': (51.0, 52.3, +0.94),
    '黄·缩量跌': (50.5, 49.4, +0.25),
    '黄·平量':   (48.9, 49.4, +0.27),
}
BASE_P1, BASE_P5 = 48.8, 49.3  # 全市场基准


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
        return None
    with open(files[-1], 'rb') as f:
        return pickle.load(f)


def _pctile(x, v):
    return float((x < v).mean() * 100)


def main(code='002384', hist_file=''):
    from data_fetcher import _code_pure
    hist = _load_history_local(hist_file)
    if not hist:
        print('  ✗ 无历史缓存')
        return 1
    sina = next((k for k in hist if _code_pure(k) == code), None)
    if not sina:
        print(f'  ✗ {code} 不在历史缓存中')
        return 1
    df = hist[sina]
    c = df['close'].values.astype(np.float64)
    v = df['volume'].values.astype(np.float64)
    a = df['amount'].values.astype(np.float64) if 'amount' in df.columns else None
    dts = pd.to_datetime(df['date'].values)
    n = len(df)
    chg = c[1:] / c[:-1] - 1

    # ---- 财务 ----
    fin = _load_financial()
    f = {k: np.nan for k in ('eps', 'bvps', 'np', 'np_yoy', 'rev_yoy', 'roe', 'margin')}
    name, ind = code, ''
    if fin is not None and '股票代码' in fin.columns:
        r = fin[fin['股票代码'].astype(str).str.zfill(6) == code]
        if len(r):
            row = r.iloc[0]
            name = str(row.get('股票简称', code))
            ind = str(row.get('所处行业', ''))
            for col, key in (('每股收益', 'eps'), ('每股净资产', 'bvps'), ('净利润-净利润', 'np'),
                             ('净利润-同比增长', 'np_yoy'), ('营业总收入-同比增长', 'rev_yoy'),
                             ('净资产收益率', 'roe'), ('销售毛利率', 'margin')):
                val = row.get(col)
                if val is not None:
                    f[key] = float(val)

    print('\n' + '═' * 90)
    print(f'  📊 个股深度分析: {code} {name}  (数据截至 {dts[-1].strftime("%Y-%m-%d")})')
    print('═' * 90)

    # ---- 1. 行情快照 ----
    close, prev_close = c[-1], c[-2]
    d_chg = close / prev_close - 1
    vol5 = v[-6:-1].mean()
    vr5 = v[-1] / vol5
    hi250, lo250 = c[-250:].max() if n >= 250 else c.max(), c[-250:].min() if n >= 250 else c.min()
    hi_all, lo_all = c.max(), c.min()
    print('\n■ 行情快照')
    print(f'  最新收盘: {close:.2f}  当日涨跌: {d_chg:+.2%}  前收: {prev_close:.2f}')
    print(f'  量比(当日/5日均量): {vr5:.2f}  当日成交额: {a[-1] / 1e8:.1f}亿')
    print(f'  250日区间: {lo250:.2f} ~ {hi250:.2f}  (现价处于 {_pctile(c[-250:], close):.0f}% 分位)')
    print(f'  历史区间(上市以来): {lo_all:.2f} ~ {hi_all:.2f}  (距历史最高 {hi_all / close - 1:.1%})')
    lim_dn = int((chg[-10:] <= -0.095).sum())
    lim_up = int((chg[-10:] >= 0.095).sum())
    print(f'  近10日: {lim_dn} 次跌停 / {lim_up} 次涨停')

    # ---- 2. 技术面 ----
    from indicator_cache import _incremental_indicators
    all_dates = {pd.Timestamp(d).strftime('%Y-%m-%d') for d in dts}
    pre = _incremental_indicators(
        c, v, df['high'].values.astype(float) if 'high' in df.columns else c,
        df['low'].values.astype(float) if 'low' in df.columns else c,
        df['date'].values, all_dates)
    last = pre[dts[-1].strftime('%Y-%m-%d')]
    from price_targets import compute_technical_levels
    lv = compute_technical_levels(df, close)
    ma5, ma20, ma60 = last['ma5'], last['ma20'], last['ma60']
    ma120, ma250 = last['ma120'], lv.get('ma250', np.nan)
    m = []
    if ma5 > ma20 > ma60:
        m.append('多头排列(MA5>MA20>MA60)')
    elif ma5 < ma20 < ma60:
        m.append('空头排列(MA5<MA20<MA60)')
    else:
        m.append('均线纠缠')
    m.append('价站上MA20' if close > ma20 else '价跌破MA20')
    m.append('价站上MA60' if close > ma60 else '价跌破MA60')
    skdj = f"SKDJ日K={last['skdj_k']:.0f} D={last['skdj_d']:.0f} ({'金叉' if last['skdj_k'] > last['skdj_d'] else '死叉'})"
    wk = f"周K={last['skdj_weekly_k']:.0f} D={last['skdj_weekly_d']:.0f} ({'金叉' if last['skdj_weekly_k'] > last['skdj_weekly_d'] else '死叉'})"
    macd = f"MACD DIF={last['dif']:.2f} DEA={last['dea']:.2f} 柱={'红' if last['macd_hist'] > 0 else '绿'}"
    print('\n■ 技术面')
    if not np.isnan(ma250):
        print(f'  MA5={ma5:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}  '
              f'MA120={ma120:.2f}  MA250={ma250:.2f}')
    else:
        print(f'  MA5={ma5:.2f}  MA20={ma20:.2f}  MA60={ma60:.2f}  MA120={ma120:.2f}')
    print(f'  趋势: {" / ".join(m)}')
    print(f'  动量: 5日 {last["ret_5d"]:+.1%} | 60日 {last["ret_60d"]:+.1%}')
    print(f'  {skdj} | {wk} | {macd}')
    print(f'  BOLL下轨 {lv["boll_lower"]:.2f} / 上轨 {lv["boll_upper"]:.2f}')
    print(f'  支撑位 {lv["support"]:.2f} | 阻力位 {lv["resistance"]:.2f} '
          f'(120日高 {lv["high_120d"]:.2f} / 低 {lv["low_120d"]:.2f})')

    # ---- 3. 估值面 ----
    print(f'\n■ 估值面 (行业: {ind})')
    eps, bvps, np_, np_yoy, rev_yoy, roe, margin = (f['eps'], f['bvps'], f['np'],
                                                    f['np_yoy'], f['rev_yoy'], f['roe'], f['margin'])
    if not np.isnan(eps) and eps > 0:
        pe = close / eps
        pe_hist = c / eps
        pe_pct = _pctile(pe_hist, pe)
        mktcap = np_ / eps * close / 1e8 if np_ > 0 else np.nan
        print(f'  每股收益(EPS) {eps:.2f} | 净利 {np_ / 1e8:.1f}亿 同比{np_yoy:+.0f}% '
              f'| 营收同比{rev_yoy:+.0f}% | ROE {roe:.1f}% | 毛利率 {margin:.1f}%')
        print(f'  PE {pe:.0f}x (EPS口径), 处于自身历史 {pe_pct:.0f}% 分位')
        print(f'  年化PE(Q1×4) {close / (eps * 4):.0f}x  |  PB {close / bvps:.1f}x  '
              f'|  推算市值 {mktcap:.0f}亿')
        print(f'  EPS 取最新财报近似恒定 (与项目回测口径一致); 历史PE分位为粗略代理')
    else:
        print('  (无财报数据)')

    # ---- 4. 量价状态 → 全市场条件概率 ----
    if vr5 >= 1.5:
        st = '红·涨停' if d_chg >= 0.095 else '红·放量涨'
    elif vr5 < 0.8:
        st = '黄·缩量涨' if d_chg > 0 else '黄·缩量跌'
    else:
        st = '黄·平量' if abs(d_chg) < 0.005 else ('绿·跌停' if d_chg <= -0.095
                                                   else ('黄·缩量涨' if d_chg > 0 else '黄·缩量跌'))
    p1, p5, m5 = STATE_PROBS.get(st, (BASE_P1, BASE_P5, 0.0))
    print('\n■ 量价状态 → 全市场历史经验概率 (745万日观测)')
    print(f'  当日状态: {st}  (量比 {vr5:.2f}, 涨跌 {d_chg:+.2%})')
    print(f'  该状态下 P(次日涨)={p1 / 100:.1%} (基准 {BASE_P1 / 100:.1%})  '
          f'P(5日涨)={p5 / 100:.1%} (基准 {BASE_P5 / 100:.1%})  均收益5日 {m5 / 100:+.2%}')

    # ---- 5. 概率模型 (9特征逻辑回归, 训练≤2018-06, 测试期外推) ----
    print('\n■ 概率模型 (9特征L2逻辑回归, 与 vol_model_backtest 同口径, 训练≤2018-06)')
    try:
        from vol_model_backtest import build_panel, logit_fit, logit_pred, FEATS, SPLIT
        pan, _ = build_panel(hist)
        tr = pan[pan['date'] <= SPLIT]
        mu, sd = tr[FEATS].mean().values.copy(), tr[FEATS].std().values.copy()
        sd[sd == 0] = 1.0
        ytr = tr['y'].values.astype(np.float64)
        Xtr = (tr[FEATS].values - mu) / sd
        w = logit_fit(Xtr, ytr, epochs=20)
        # 该股最近10个交易日的特征 + 预测
        rows = []
        for i in range(n - 10, n):
            if i < 22:
                continue
            vr5i = v[i] / v[i - 5:i].mean()
            vr20i = v[i] / v[i - 20:i].mean()
            amt5i = a[i] / a[i - 5:i].mean()
            ch1 = c[i] / c[i - 1] - 1
            ch2 = c[i - 1] / c[i - 2] - 1
            ch3 = c[i - 2] / c[i - 3] - 1
            up3 = (1 + ch1) * (1 + ch2) * (1 + ch3) - 1
            pos5 = c[i] / c[i - 5:i].mean() - 1
            pos20 = c[i] / c[i - 20:i].mean() - 1
            x = np.array([[vr5i, vr20i, amt5i, ch1, ch2, ch3, up3, pos5, pos20]])
            p = float(logit_pred((x - mu) / sd, w)[0])
            rows.append((dts[i].strftime('%m-%d'), c[i], ch1, p))
        print(f"  {'日期':<8}{'收盘':>9}{'当日涨跌':>9}{'P(次日涨)':>10}")
        print('  ' + '─' * 38)
        for d0, px, ch0, p0 in rows:
            print(f'  {d0:<8}{px:>9.2f}{ch0:>+8.2%}{p0:>9.1%}')
        p_now = rows[-1][3]
    except Exception as e:
        print(f'  ⚠ 模型预测失败: {e}')
        p_now = np.nan

    # ---- 6. 走势研判 ----
    print('\n' + '═' * 90)
    print('  🔮 未来走势研判 (概率情景, 非确定性预测)')
    print('═' * 90)
    trend_desc = []
    if close > lv['ma250']:
        trend_desc.append('中期趋势站上年线')
    else:
        trend_desc.append('中期趋势跌破年线')
    if last['ret_60d'] > 0:
        trend_desc.append('60日动量为正')
    else:
        trend_desc.append('60日动量转负')
    if close > ma20:
        trend_desc.append('短期站稳MA20')
    else:
        trend_desc.append('短期失守MA20')
    print(f"  当前状态: {' / '.join(trend_desc)}")
    # 近端关键位 (方向感知): 上方第一压力 = 价格上方最近的均线; 下方支撑 = 近20日低点/BOLL下轨
    lows20 = df['low'].values[-20:].astype(float).min()
    mas = [('MA5', ma5), ('MA20', ma20), ('MA60', ma60), ('MA120', ma120)]
    above = sorted(((k, v) for k, v in mas if v > close), key=lambda x: x[1])
    res1 = above[0] if above else ('MA250', ma250 if not np.isnan(ma250) else hi_all)
    print(f'  近端价位: 上方第一压力 {res1[0]} {res1[1]:.2f}'
          f' (其后 MA20 {ma20:.2f}); 下方第一支撑 近20日低 {lows20:.2f}'
          f' (其后 BOLL下轨 {lv["boll_lower"]:.2f})')
    if not np.isnan(p_now):
        print(f'\n  次日 (1日) : 模型 P(上涨) = {p_now:.1%} '
              f'vs 全市场基准 {BASE_P1 / 100:.1%}'
              f' | 量价状态经验值 {p1 / 100:.1%}')
    print(f'  近5日 (5日): 量价状态经验 P(5日涨) = {p5 / 100:.1%} (基准 {BASE_P5 / 100:.1%}), '
          f'均收益 {m5 / 100:+.2%}')
    base_pp = BASE_P1 / 100
    pp = max(p_now, p1 / 100) if not np.isnan(p_now) else p5 / 100
    if pp > base_pp:
        print(f'  → 短期概率略偏多 ({(pp - base_pp) * 100:+.1f}pp), 但幅度小, 不具备高确定性')
    else:
        print(f'  → 短期概率略偏空 ({(pp - base_pp) * 100:+.1f}pp), 但幅度小, 不具备高确定性')
    print('\n  情景推演 (基于统计与结构, 非保证):')
    print(f'    基准情形: 于近20日低 {lows20:.2f} ~ 压力 {res1[1]:.2f} 区间拉锯'
          f' (压力 {res1[1] / close - 1:+.1%} / 支撑 {close / lows20 - 1:+.1%})')
    print(f'    乐观情形: 放量收复 {res1[1]:.2f} → 修复至 MA20 {ma20:.2f}'
          f' ({ma20 / close - 1:+.1%})')
    print(f'    悲观情形: 失守 {lows20:.2f} → 下探 BOLL下轨 {lv["boll_lower"]:.2f}'
          f' ({lv["boll_lower"] / close - 1:+.1%}); 若再破, 120日低 {lv["low_120d"]:.2f} 前无险可守')
    if not np.isnan(eps) and eps > 0:
        print(f'\n  估值约束: PE(TTM) {pe:.0f}x 处于历史 {pe_pct:.0f}% 分位'
              f' {"(偏高, 上涨空间受估值压制)" if pe_pct > 70 else "(中性偏低)"}'
              f' | 净利同比 {np_yoy:+.0f}% 高增长是对高PE的主要支撑')
    print('\n  ⚠ 本报告为数据统计与规则推演, 不构成投资建议; 历史条件概率 lift 仅几个百分点,')
    print('    任何单日预测的不确定性都很高, 建议以关键价位 + 量价状态做纪律化应对。')
    print('═' * 90)
    return 0


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--code', default='002384')
    p.add_argument('--hist-file', default='')
    a = p.parse_args()
    sys.exit(main(a.code, a.hist_file))

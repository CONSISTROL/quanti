"""
G2/G3 验证: vnpy_quanti 回测 JSON vs legacy 参考 JSON。

G2 信号对比: vnpy decisions(决策日) vs legacy trades(date==signal_date, immediate 口径)
  要求方向+日期集合完全一致。
G3 绩效对比: 同公式统计表 + 差异成因提示（成交时点: legacy immediate 收盘 vs vnpy 次bar开盘）。
"""
from __future__ import annotations

import argparse
import json
import sys


def load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt_stat(v, pct=False):
    if v is None:
        return "-"
    if pct:
        return f"{v:+.2%}" if isinstance(v, float) else str(v)
    return f"{v:,.2f}" if isinstance(v, float) else str(v)


def compare(new: dict, legacy: dict) -> int:
    codeful = False
    if new.get("decisions") and legacy.get("trades"):
        codeful = ("code" in new["decisions"][0]) and ("code" in legacy["trades"][0])

    print("=" * 72)
    if codeful:
        print("G2 — 信号（决策日 × 标的 × 方向）逐条对比")
    else:
        print("G2 — 信号（决策日 × 方向）逐条对比")
    print("=" * 72)

    nm = new["meta"]
    lm = legacy["meta"]
    fmt_code = (" code=" + str(nm.get("code", ""))) if not codeful else ""
    print(f"  vnpy  : {nm.get('code', '')}{fmt_code} {nm['strategy']} "
          f"决策={len(new['decisions'])}")
    print(f"  legacy: {lm.get('code', '')} {lm['strategy']} "
          f"成交={len(legacy['trades'])} (immediate: 成交日==信号日)")

    def _key3(t):
        return (str(t.get("code", "")), t["signal_date"], t["direction"])

    def _key2(t):
        return (t["signal_date"], t["direction"])

    def _dk3(d):
        return (str(d.get("code", "")), d["date"], d["direction"])

    def _dk2(d):
        return (d["date"], d["direction"])

    key_l = _key3 if codeful else _key2
    key_d = _dk3 if codeful else _dk2

    leg = {key_l(t) for t in legacy["trades"]}
    dec = {key_d(d) for d in new["decisions"]}

    only_legacy = sorted(leg - dec)
    only_new = sorted(dec - leg)
    common = leg & dec
    print(f"  一致: {len(common)}  |  仅 legacy: {len(only_legacy)}  |  仅 vnpy: {len(only_new)}")

    exit_code = 0
    if only_legacy or only_new:
        exit_code = 1
        reasons_map = {}
        for t in legacy["trades"]:
            reasons_map.setdefault(key_l(t), []).append(t["reason"])
        for d in only_legacy:
            cd = f"{d[0]} " if codeful else ""
            lbl = d[2] if codeful else d[1]
            print(f"  ✗ 仅legacy {cd}{lbl:<4} {str(reasons_map.get(d, ['']))[:60]}")
        new_reason = {}
        for dd in new["decisions"]:
            new_reason[key_d(dd)] = dd
        for d in only_new:
            r = new_reason.get(d, {})
            cd = f"{d[0]} " if codeful else ""
            lbl = d[2] if codeful else d[1]
            print(f"  ✗ 仅vnpy  {cd}{lbl:<4} {str(r.get('reason'))[:60]}")

    # 方向一致率（按 legacy 信号为基准）
    if leg:
        hit = len(leg & dec) / len(leg)
        print(f"  信号命中率(基准=legacy): {hit:.1%}")
        if hit < 1.0:
            exit_code = 1

    print()
    print("=" * 72)
    print("G3 — 绩效对比（统计公式同源）")
    print("=" * 72)

    fill = (new.get("meta") or {}).get("fill", "close")
    if fill == "close":
        note = ("vnpy fill=close（QuantiBacktestEngine 信号日收盘成交）与 legacy immediate 同为"
                "信号日收盘口径，数字应分毫不差；剩余差异=费用/统计浮点/双口径。")
    else:
        note = ("vnpy fill=native（次bar开盘撮合）vs legacy 信号日收盘成交："
                "收益差异属成交时点执行口径，G2 信号层一致即通过。")
    print(f"  口径: {note}")

    ns, ls = new["stats"], legacy["stats"]
    rows = [
        ("初始资金", "initial_capital", None),
        ("最终净值", "final_value", None),
        ("总收益率", "total_return", "pct"),
        ("年化收益率", "annual_return", "pct"),
        ("Sharpe", "sharpe", None),
        ("最大回撤", "max_drawdown", "pct"),
        ("回撤天数", "max_drawdown_days", None),
        ("交易笔数", "total_trades", None),
        ("胜率", "win_rate", "pct"),
    ]
    for label, key, kind in rows:
        a, b = ns.get(key), ls.get(key)
        if kind == "pct":
            print(f"  {label:<10}  vnpy {fmt_stat(a, True):>12}  "
                  f"legacy {fmt_stat(b, True):>12}")
        else:
            print(f"  {label:<10}  vnpy {fmt_stat(a):>12}  legacy {fmt_stat(b):>12}")

    print()
    if "signal_stats" in new and "signal_stats" in legacy:
        print("= " * 36)
        print("信号账户(信号日/信号价口径) 对比")
        print("= " * 36)
        sns, sls = new["signal_stats"], legacy["signal_stats"]
        for label, key, kind in [("最终净值", "final_value", None),
                                 ("总收益率", "total_return", "pct"),
                                 ("Sharpe", "sharpe", None),
                                 ("最大回撤", "max_drawdown", "pct"),
                                 ("卖出笔数", "total_trades", None),
                                 ("胜率", "win_rate", "pct")]:
            a, b = sns.get(key), sls.get(key)
            if kind == "pct":
                print(f"  {label:<8}  vnpy {fmt_stat(a, True):>12}  "
                      f"legacy {fmt_stat(b, True):>12}")
            else:
                print(f"  {label:<8}  vnpy {fmt_stat(a):>12}  legacy {fmt_stat(b):>12}")
        print()
    print("=" * 72)
    print("G2 结果:", "PASS ✅" if exit_code == 0 else "FAIL ❌ (见上方差异)")
    return exit_code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--new", required=True, help="vnpy_quanti 结果 JSON")
    ap.add_argument("--legacy", required=True, help="legacy 参考 JSON")
    a = ap.parse_args(argv)
    return compare(load(a.new), load(a.legacy))


if __name__ == "__main__":
    sys.exit(main())

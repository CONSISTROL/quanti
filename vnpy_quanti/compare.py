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
    print("=" * 72)
    print("G2 — 信号（决策日 × 方向）逐条对比")
    print("=" * 72)

    nm = new["meta"]
    lm = legacy["meta"]
    print(f"  vnpy  : {nm['code']} {nm['strategy']} rows={nm['history_rows']} "
          f"决策={len(new['decisions'])}")
    print(f"  legacy: {lm['code']} {lm['strategy']} rows={lm['history_rows']} "
          f"成交={len(legacy['trades'])} (immediate: 成交日==信号日)")

    leg = {(t["signal_date"], t["direction"]) for t in legacy["trades"]}
    dec = {(d["date"], d["direction"]) for d in new["decisions"]}

    only_legacy = sorted(leg - dec)
    only_new = sorted(dec - leg)
    common = leg & dec
    print(f"  一致: {len(common)}  |  仅 legacy: {len(only_legacy)}  |  仅 vnpy: {len(only_new)}")

    exit_code = 0
    if only_legacy or only_new:
        exit_code = 1
        for d in only_legacy:
            reasons = [t["reason"] for t in legacy["trades"]
                       if (t["signal_date"], t["direction"]) == d]
            print(f"  ✗ 仅legacy {d[0]} {d[1]:<4} {reasons[:1]}")
        new_reason = {dd["date"]: dd for dd in new["decisions"]}
        for d in only_new:
            r = new_reason.get(d[0], {})
            print(f"  ✗ 仅vnpy  {d[0]} {d[1]:<4} {str(r.get('reason'))[:60]}")

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

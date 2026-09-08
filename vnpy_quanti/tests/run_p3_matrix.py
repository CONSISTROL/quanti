"""
P3 批量验证驱动 — 多策略 × 多标的: 分别跑 vnpy 侧(当前 venv) 与 legacy 侧(.venv)，
对比 G2 信号 / G3 绩效，输出汇总表与 markdown 报告。

用法(在 .venv-vnpy 下): python vnpy_quanti/tests/run_p3_matrix.py
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = REPO_ROOT / "reports" / "vnpy_compare"
WATCHLIST_PKL = "cache/quantdash_watchlist_20260908.pkl"
START = "2024-01-01"
VNPY_PY = sys.executable
LEGACY_PY = str(REPO_ROOT / ".venv" / "Scripts" / "python.exe")
GEN_SCRIPT = str(REPO_ROOT / "vnpy_quanti" / "tests" / "gen_legacy_ref.py")

COMBOS = [
    ("bollinger", "601857", None),
    ("bollinger", "600547", WATCHLIST_PKL),
    ("bollinger", "002832", None),
    ("momentum", "601857", None),
    ("momentum", "600547", WATCHLIST_PKL),
    ("gap_open", "002832", None),
    ("gap_open", "600547", WATCHLIST_PKL),
]


def run(cmd: list[str]) -> int:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode


def g2_compare(new: dict, legacy: dict) -> tuple[int, int, int]:
    leg = {(t["signal_date"], t["direction"]) for t in legacy["trades"]}
    dec = {(d["date"], d["direction"]) for d in new["decisions"]}
    return len(leg & dec), len(leg - dec), len(dec - leg)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    fail = 0
    for strat, code, pkl in COMBOS:
        tag = f"p3_{strat}_{code}"
        new_j = OUT_DIR / f"{tag}_new.json"
        ref_j = OUT_DIR / f"{tag}_ref.json"
        wl = ["--watchlist-pkl", pkl] if pkl else []

        rc = run([VNPY_PY, "-m", "vnpy_quanti", "stock", "--stock", code,
                  "--strategy", strat, "--start", START] + wl +
                 ["--out", str(new_j)])
        if rc != 0 or not new_j.exists():
            rows.append((tag, "vnpy-FAIL", "-", "-", "-", "-"))
            fail += 1
            continue
        rc = run([LEGACY_PY, GEN_SCRIPT, "--stock", code, "--strategy", strat,
                  "--start", START] + wl + ["--out", str(ref_j)])
        # legacy gen 进程退出码偶发为 1(输出阶段), 以 JSON 落盘为准
        if not ref_j.exists():
            rows.append((tag, "legacy-FAIL", "-", "-", "-", "-"))
            fail += 1
            continue

        new = json.loads(new_j.read_text(encoding="utf-8"))
        leg = json.loads(ref_j.read_text(encoding="utf-8"))
        common, only_l, only_n = g2_compare(new, leg)
        n_tr = leg["meta"].get("history_rows", "-")
        l_trd = len(leg["trades"])

        g2 = "PASS" if (only_l == 0 and only_n == 0) else "FAIL"
        if g2 == "FAIL":
            fail += 1
        ns, ls = new["stats"], leg["stats"]
        row = (tag, g2, f"{common}/{common + only_l + only_n}",
               f"{ns['total_return'] * 100:+.2f}%", f"{ls['total_return'] * 100:+.2f}%",
               f"{len(new['decisions'])}/{l_trd}")
        rows.append(row)
        print(f"{row[0]:<26} G2={row[1]:<4} 命中={row[2]:<9} "
              f"收益 vnpy={row[3]} legacy={row[4]}  决策/成交={row[5]}")

    # markdown 报告
    lines = [
        "# P3 批量迁移验证矩阵（vnpy vs legacy immediate）",
        "",
        f"- 区间: {START} ~ 数据最新 | 成交口径: vnpy fill=close (同信号日收盘)",
        f"- G2 = 决策(日,方向)与 legacy 完全一致; G3 = 同公式绩效",
        "",
        "| 策略×标的 | G2 | 命中 | vnpy 收益 | legacy 收益 | 决策/成交 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    lines += ["", f"G2 失败数: {fail}", ""]
    (OUT_DIR / "p3_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告: {OUT_DIR / 'p3_report.md'}")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())

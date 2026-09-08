"""
P4 组合引擎 vnpy 侧运行器 — 用 vnpy_quanti.PortfolioEngine 跑用例(p4_cases.json)并落 JSON。

用法(.venv-vnpy): python vnpy_quanti/tests/run_p4_vnpy.py --out-dir reports/vnpy_compare
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CASES_PATH = Path(__file__).resolve().parent / "p4_cases.json"
sys.path.insert(0, str(REPO_ROOT))

from vnpy_quanti import adapters  # noqa: E402
from vnpy_quanti.portfolio import PortfolioEngine  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "reports" / "vnpy_compare"))
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cases_json = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    pkl = str(REPO_ROOT / cases_json["watchlist_pkl"])
    frames = adapters.load_pkl_df(pkl)

    for case in cases_json["cases"]:
        name = case["name"]
        codes = [c.zfill(6) for c in case["codes"]]
        dfs = {}
        for c in codes:
            pure = c.zfill(6)
            hit = None
            for k, v in frames.items():
                if k.strip().lower().lstrip("shszbj").zfill(6) == pure:
                    hit = v
                    break
            if hit is None:
                raise FileNotFoundError(f"{pure} 不在 {pkl}")
            dfs[pure] = hit

        engine = PortfolioEngine(
            strategy=case["strategy"],
            initial_capital=float(case["initial_capital"]),
            max_positions=int(case["max_positions"]),
            position_pct=float(case["position_pct"]),
            full_position=bool(case["full_position"]),
            min_buy_score=float(case["min_buy_score"]),
            max_holding_days=int(case.get("max_holding_days", 0)),
            watchlist_priority=case.get("watchlist_priority") or {},
            ranks=case.get("ranks") or {},
        )
        engine.load(dfs, case["start"], case.get("end", ""))
        res = engine.run(verbose=False)
        out = {
            "meta": {
                "engine": "vnpy_quanti PortfolioEngine (immediate close)",
                "strategy": case["strategy"], "codes": codes,
                "start": case["start"],
                "end": case.get("end", "") or dfs[codes[0]]["date"].max().strftime("%Y-%m-%d"),
                "initial_capital": float(case["initial_capital"]),
                "max_positions": int(case["max_positions"]),
                "position_pct": float(case["position_pct"]),
                "full_position": bool(case["full_position"]),
                "window_days": len(res["equity_curve"]),
            },
            "decisions": res["decisions"],
            "trades": res["trades"],
            "equity_curve": res["equity_curve"],
            "stats": res["stats"],
        }
        f = out_dir / f"p4_{name}_new.json"
        f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[vnpy-p4] {name}: {len(res['trades'])} 笔 -> {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

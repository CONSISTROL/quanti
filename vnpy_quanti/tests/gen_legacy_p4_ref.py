"""
在 legacy venv(.venv) 下运行：用旧引擎 run_swing_backtest 对 P4 组合用例生成参考 JSON。
用例参数共用 vnpy_quanti/tests/p4_cases.json（两引擎读取同一份，杜绝口径漂移）。

用法: .venv\\Scripts\\python.exe vnpy_quanti/tests/gen_legacy_p4_ref.py --out-dir reports/vnpy_compare
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CASES_PATH = Path(__file__).resolve().parent / "p4_cases.json"


def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"])
    for col in ("open", "high", "low", "close", "volume", "amount"):
        if col not in out.columns:
            out[col] = 0.0
        else:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["close"])
    out = out[out["close"] > 0]
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    return out.reset_index(drop=True)


def load_all(path: str, codes: list[str]) -> dict[str, pd.DataFrame]:
    with open(path, "rb") as f:
        obj = pickle.load(f)
    frames = {str(k): v for k, v in obj.items() if isinstance(v, pd.DataFrame)}
    out = {}
    for c in codes:
        pure = c.zfill(6)
        hit = None
        for k, v in frames.items():
            if k.strip().lower().lstrip("shszbj").zfill(6) == pure:
                hit = v
                break
        if hit is None:
            raise FileNotFoundError(f"{pure} 不在 {path}")
        out[pure] = normalize_df(hit)
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "reports" / "vnpy_compare"))
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cases_json = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    pkl = cases_json["_comment"] and cases_json.get("watchlist_pkl")
    pkl = str(REPO_ROOT / pkl)

    from quantlab.trading_engine import run_swing_backtest

    for case in cases_json["cases"]:
        name = case["name"]
        codes = [c.zfill(6) for c in case["codes"]]
        dfs = load_all(pkl, codes)
        pure_to_sina = {}
        for k, v in dfs.items():
            prefix = "sh" if k.startswith(("6", "5", "9")) else "sz"
            pure_to_sina[k] = f"{prefix}{k}"
        history = {pure_to_sina[c]: dfs[c] for c in codes}
        sina_of = {c: pure_to_sina[c] for c in codes}

        start = case["start"]
        end = case["end"] or dfs[codes[0]]["date"].max().strftime("%Y-%m-%d")

        # per-code precomputed (per-code window target dates)
        from quantlab.indicator_cache import _incremental_indicators
        precomputed = {}
        for c in codes:
            df = dfs[c]
            dates_arr = df["date"].values
            target = set()
            for d in dates_arr:
                ts = pd.Timestamp(d)
                if pd.Timestamp(start) <= ts <= pd.Timestamp(end):
                    target.add(ts.strftime("%Y-%m-%d"))
            precomputed[c] = _incremental_indicators(
                df["close"].values.astype(float),
                df["volume"].values.astype(float) if "volume" in df else None,
                df["high"].values.astype(float) if "high" in df else None,
                df["low"].values.astype(float) if "low" in df else None,
                dates_arr, target,
            )

        tr = {
            "initial_capital": float(case["initial_capital"]),
            "max_positions": int(case["max_positions"]),
            "position_pct": float(case["position_pct"]),
            "full_position": bool(case["full_position"]),
            "min_buy_score": float(case["min_buy_score"]),
            "max_holding_days": int(case.get("max_holding_days", 0)),
            "strategy": case["strategy"],
            "watchlist": [c for c in codes],
            "watchlist_priority": {str(k): int(v)
                                   for k, v in case.get("watchlist_priority", {}).items()},
            "kelly_mode": False,
            "exec_next_open": False, "exec_next_close": False,
            "buy_next_open": False, "buy_next_close": False,
        }
        scored = pd.DataFrame([{
            "code": c, "name": c, "rank": 999,
            "momentum_score": 0, "composite_score": 1.0,
        } for c in codes])

        print(f"[legacy-p4] case={name} codes={codes} start={start} end={end}")
        result = run_swing_backtest(
            history, scored, tr, start, end, precomputed=precomputed)

        def _d(x):
            return x.strftime("%Y-%m-%d") if hasattr(x, "strftime") else str(x)

        out = {
            "meta": {
                "engine": "legacy run_swing_backtest (immediate close)",
                "strategy": case["strategy"], "codes": codes,
                "start": start, "end": end,
                "initial_capital": tr["initial_capital"],
                "max_positions": tr["max_positions"],
                "position_pct": tr["position_pct"],
                "full_position": tr["full_position"],
            },
            "trades": [{
                "date": _d(t.date), "signal_date": _d(t.signal_date),
                "code": str(t.code), "direction": t.direction,
                "price": round(float(t.price), 4), "shares": int(t.shares),
                "amount": round(float(t.amount), 2), "reason": t.reason,
            } for t in result["trades"]],
            "equity_curve": [[_d(d), round(float(v), 2)]
                             for d, v in result["equity_curve"]],
            "stats": {k: (round(float(v), 8) if isinstance(v, (int, float)) else v)
                      for k, v in result["stats"].items()},
        }
        f = out_dir / f"p4_{name}_ref.json"
        f.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[legacy-p4] {name}: {len(result['trades'])} 笔 -> {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

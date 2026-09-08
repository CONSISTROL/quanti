"""
在 legacy venv(.venv) 下运行：用旧引擎 quantlab.trading_engine.backtest_single_stock
对同一份 cache 历史生成参考 JSON（供 vnpy_quanti 侧 G2/G3 对比）。

用法:
    .venv\\Scripts\\python.exe vnpy_quanti/tests/gen_legacy_ref.py \
        --stock 601857 --strategy reversal --watchlist-pkl cache/quantdash_watchlist_20260908.pkl \
        --start 2025-01-01 --out ref_601857.json
注意: 本脚本不 import vnpy/vnpy_quanti，仅依赖 legacy 栈。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def normalize_df(df):
    """与 vnpy_quanti/adapters.normalize_df 相同的规范化（保证 G1 输入同源）。"""
    import pandas as pd
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


def load_df(path: str, code6: str):
    """从 pkl 中取出某 6 位代码的规范化 df，返回 (df, sina_key)。"""
    import pandas as pd
    with open(path, "rb") as f:
        obj = pickle.load(f)
    pure = code6.zfill(6)
    if isinstance(obj, dict) and set(obj.keys()) == {"saved_at", "df"}:
        frames = {"df": obj["df"]}
    elif isinstance(obj, dict):
        frames = obj
    else:
        raise TypeError("无法识别的 pkl")
    for k, v in frames.items():
        if not isinstance(v, pd.DataFrame):
            continue
        key = str(k).strip().lower()
        if key == "df":
            # 单股 pkl: df 就是目标
            return normalize_df(v), "sh" + pure if pure.startswith("6") else "sz" + pure
        if key.lstrip("shszbj").zfill(6) == pure:
            return normalize_df(v), key
    raise FileNotFoundError(f"{pure} not in {path}")


def fingerprint(df) -> str:
    h = hashlib.sha1()
    h.update(str(len(df)).encode())
    h.update(df["date"].iloc[0].strftime("%Y-%m-%d").encode())
    h.update(df["date"].iloc[-1].strftime("%Y-%m-%d").encode())
    h.update(df["close"].tail(50).round(6).to_csv(index=False).encode())
    return h.hexdigest()[:16]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", required=True)
    ap.add_argument("--strategy", default="reversal")
    ap.add_argument("--watchlist-pkl", default=None)
    ap.add_argument("--cache-dir", default="cache")
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--out", default="ref_legacy.json")
    a = ap.parse_args()

    code = a.stock.strip().lower()
    for pre in ("sh", "sz", "bj"):
        if code.startswith(pre):
            code = code[len(pre):]
            break
    code = code.zfill(6)

    # 1) 数据: 与 vnpy 侧同 pkl / 同规范化
    pkl = a.watchlist_pkl
    if not pkl:
        single = Path(a.cache_dir) / f"kline_1d_{code}.pkl"
        if single.exists():
            pkl = str(single)
        else:
            cands = sorted(Path(a.cache_dir).glob("*watchlist_*.pkl"), reverse=True)
            if not cands:
                raise SystemExit("找不到缓存 pkl")
            pkl = str(cands[0])
    df, sina_key = load_df(pkl, code)
    print(f"[legacy-ref] {code} from {pkl}  sina={sina_key}  rows={len(df)}  "
          f"{df['date'].iloc[0].date()} ~ {df['date'].iloc[-1].date()}")

    # 2) 旧引擎跑个股回测（immediate 成交: 决策日==成交日）
    import json as _json
    cfg_path = REPO_ROOT / "config.json"
    if cfg_path.exists():
        cfg = _json.load(open(cfg_path, encoding="utf-8"))
    else:
        cfg = {}
    tr = dict(cfg.get("trading", {}) or {})
    tr.update({
        "strategy": a.strategy,
        "exec_next_open": False, "exec_next_close": False,
        "buy_next_open": False, "buy_next_close": False,
    })
    end = a.end or df["date"].max().strftime("%Y-%m-%d")

    from quantlab.trading_engine import backtest_single_stock
    result = backtest_single_stock(code, {sina_key: df}, tr, a.start, end)
    if result is None:
        raise SystemExit("旧引擎回测失败")

    def _d(d):
        return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

    trades = [{
        "date": _d(t.date), "signal_date": _d(t.signal_date),
        "direction": t.direction, "price": round(float(t.price), 4),
        "shares": int(t.shares), "amount": round(float(t.amount), 2),
        "reason": t.reason, "pnl_pct": round(float(t.pnl_pct), 6),
        "pnl_signal": round(float(t.pnl_signal), 6),
    } for t in result["trades"]]

    def _stats(d):
        return {k: (round(float(v), 8) if isinstance(v, (int, float)) else v)
                for k, v in d.items()}

    out = {
        "meta": {
            "engine": "legacy quantlab.trading_engine (immediate close fills)",
            "code": code, "sina_code": sina_key, "strategy": a.strategy,
            "start": a.start, "end": end,
            "history_rows": int(len(df)),
            "df_fingerprint": fingerprint(df),
        },
        "trades": trades,
        "equity_curve": [[_d(d), round(float(v), 2)] for d, v in result["equity_curve"]],
        "signal_equity_curve": [[_d(d), round(float(v), 2)]
                                for d, v in result["signal_equity_curve"]],
        "stats": _stats(result["stats"]),
        "signal_stats": _stats(result["signal_stats"]),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[legacy-ref] 决策/成交 {len(trades)} 笔 → {os.path.abspath(a.out)}")
    st = out["stats"]
    print(f"  总收益率 {st['total_return']:+.2%}  年化 {st['annual_return']:+.2%}  "
          f"Sharpe {st['sharpe']:.2f}  最大回撤 {st['max_drawdown']:.2%}  "
          f"交易 {st['total_trades']} 胜率 {st['win_rate']:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

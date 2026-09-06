"""Realtime watchlist intraday-T (做T) monitor.

While the Quanti web service is running, this module polls Tencent 1-minute
K-line data for every code in ``config.json -> watchlist`` and applies the same
intraday-T rules used by ``backend/intraday_t_decision.py``:

- per-session minute MACD (12,26,9)
- gap direction decides whether the first action is B->S or S->B
- early-volume ratio is included in the message as risk context

Newly appearing buy/sell signals are pushed to the Feishu webhook configured at
``config.json -> alert.feishu_webhook``. To avoid spamming, each code/date/time/side
is sent at most once per process lifetime.
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.intraday_t_decision import _session_macd  # noqa: E402

LOW_GAP = -0.005
HIGH_GAP = 0.005
EARLY_BARS = 6
WARMUP_BARS = 10

# A-share continuous auction sessions: 09:30-11:30 and 13:00-15:00.
SESSION_RANGES = (
    (dtime(9, 30), dtime(11, 30)),
    (dtime(13, 0), dtime(15, 0)),
)

_NAME_CACHE: dict[str, str] = {}

CN_TZ = timezone(timedelta(hours=8))


def _now_cn() -> datetime:
    return datetime.now(CN_TZ)


def _load_config() -> dict:
    with open(ROOT / "config.json", encoding="utf-8") as f:
        return json.load(f)


def _is_trading_time(now: datetime | None = None) -> bool:
    now = now or _now_cn()
    if now.weekday() >= 5:
        return False
    t = now.time()
    return any(start <= t <= end for start, end in SESSION_RANGES)


def _latest_session(df: pd.DataFrame) -> tuple[pd.DataFrame | None, object]:
    """Split a 1-minute DataFrame and return the latest trading day's rows."""
    if df is None or len(df) == 0:
        return None, None
    df = df.copy()
    df["dt"] = pd.to_datetime(df["date"])
    df["day"] = df["dt"].dt.date
    latest_day = df["day"].max()
    grp = df[df["day"] == latest_day].sort_values("dt").reset_index(drop=True)
    return grp, latest_day


def _previous_close(daily: pd.DataFrame, today) -> float | None:
    """Return the previous trading day close before ``today``."""
    if daily is None or len(daily) == 0:
        return None
    daily = daily.copy()
    daily["date"] = pd.to_datetime(daily["date"]).dt.date
    daily = daily.sort_values("date").reset_index(drop=True)
    past = daily[daily["date"] < today]
    if len(past) > 0:
        return float(past["close"].iloc[-1])
    # If the daily feed only contains today's bar (rare), fall back to its close.
    return float(daily["close"].iloc[-1])


def _signal_reason(acts: np.ndarray, dif, dea, hist, idx: int) -> str:
    if int(acts[idx]) == 1:
        return "DIF低位金叉（0轴下方上穿DEA）"
    death_cross = (
        idx > 0
        and dif[idx] < dea[idx]
        and dif[idx - 1] >= dea[idx - 1]
    )
    red_shrink = (
        idx >= 2
        and hist[idx - 1] > 0
        and hist[idx] < hist[idx - 1]
        and hist[idx - 1] >= hist[idx - 2]
    )
    if death_cross:
        return "DIF死叉（动能转弱）"
    if red_shrink:
        return "红柱缩短（动能减弱）"
    return "卖出信号"


def _build_intraday_events(closes, gap_pct) -> tuple[list[dict], np.ndarray, str | None]:
    """Build stable intraday-T events for one session.

    Returns (events, acts, mode). An event is only emitted when it is part of the
    rule's first B->S or S->B round, so early unrelated MACD actions are ignored.
    """
    acts = _session_macd(closes)
    event_indices = [i for i, a in enumerate(acts) if a != 0]
    if not event_indices:
        return [], acts, None

    if gap_pct <= LOW_GAP:
        mode = "B->S"
    elif gap_pct >= HIGH_GAP:
        mode = "S->B"
    else:
        mode = "B->S" if int(acts[event_indices[0]]) == 1 else "S->B"

    events: list[dict] = []
    if mode == "B->S":
        buy_idx: int | None = None
        for i in event_indices:
            if buy_idx is None:
                if int(acts[i]) == 1:
                    buy_idx = i
                    events.append({"side": "buy", "bar_index": i, "price": float(closes[i])})
            elif int(acts[i]) == -1 and closes[i] > closes[buy_idx]:
                events.append({"side": "sell", "bar_index": i, "price": float(closes[i])})
                break
    else:
        sell_idx: int | None = None
        for i in event_indices:
            if sell_idx is None:
                if int(acts[i]) == -1:
                    sell_idx = i
                    events.append({"side": "sell", "bar_index": i, "price": float(closes[i])})
            elif int(acts[i]) == 1 and closes[i] < closes[sell_idx]:
                events.append({"side": "buy", "bar_index": i, "price": float(closes[i])})
                break

    return events, acts, mode


def _evaluate_code(code: str, config: dict) -> dict | None:
    """Fetch one code's realtime data and return actionable events for today."""
    from backend.kline import _fetch_daily_df, _fetch_tencent_1m
    from backend.stock_name import get_stock_name

    code = str(code).zfill(6)
    daily = _fetch_daily_df(code, config.get("data", {}))
    minute = _fetch_tencent_1m(code, max_bars=320, refresh=True)

    grp, latest_day = _latest_session(minute)
    if grp is None or len(grp) < WARMUP_BARS + 1:
        return None

    # During trading hours we only care about the current session. When the API
    # still returns only the previous session, skip until today's bars appear.
    if _is_trading_time():
        today = _now_cn().date()
        if latest_day != today:
            return None

    opens = grp["open"].values.astype(float)
    closes = grp["close"].values.astype(float)
    volumes = grp["volume"].values.astype(float)
    times = [ts.strftime("%H:%M") for ts in grp["dt"]]

    prev_close = _previous_close(daily, latest_day)
    if prev_close is None or prev_close <= 0:
        return None
    gap_pct = float(opens[0] / prev_close - 1)

    events, acts, mode = _build_intraday_events(closes, gap_pct)

    # Compute MACD arrays for human-readable signal reasons.
    s = pd.Series(closes)
    ema_fast = s.ewm(span=12, adjust=False).mean()
    ema_slow = s.ewm(span=26, adjust=False).mean()
    dif = (ema_fast - ema_slow).values
    dea = pd.Series(dif).ewm(span=9, adjust=False).mean().values
    hist = dif - dea

    enriched_events = []
    for ev in events:
        idx = ev["bar_index"]
        enriched_events.append({
            "side": ev["side"],
            "time": times[idx],
            "price": round(float(ev["price"]), 3),
            "reason": _signal_reason(acts, dif, dea, hist, idx),
        })

    early_vol = float(np.sum(volumes[:EARLY_BARS])) if len(volumes) >= EARLY_BARS else 0.0
    avg_bar_vol = float(np.mean(volumes)) if len(volumes) else 0.0
    early_ratio = early_vol / (avg_bar_vol * EARLY_BARS) if avg_bar_vol * EARLY_BARS > 0 else 0.0

    if code not in _NAME_CACHE:
        try:
            _NAME_CACHE[code] = get_stock_name(code)["name"]
        except Exception:
            _NAME_CACHE[code] = code
    name = _NAME_CACHE[code]

    return {
        "code": code,
        "name": name,
        "date": str(latest_day),
        "mode": mode or "watch",
        "gap_pct": gap_pct,
        "early_volume_ratio": round(float(early_ratio), 3),
        "events": enriched_events,
    }


def _push_feishu(webhook: str, text: str) -> bool:
    try:
        resp = requests.post(
            webhook,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception:
        return False


def _build_feishu_text(result: dict, event: dict) -> str:
    action = "📈 T买入信号" if event["side"] == "buy" else "📉 T卖出信号"
    action_tip = "建议先买入底仓，等红柱缩短/死叉后高抛" if result["mode"] == "B->S" else "建议先卖出持仓，等0轴下方低位金叉再低吸买回"
    mode_label = "先B后S" if result["mode"] == "B->S" else "先S后B"
    return (
        f"{action} · {result['name']} {result['code']}\n"
        f"时间：{result['date']} {event['time']}\n"
        f"价格：{event['price']:.3f}\n"
        f"模式：{mode_label}（{action_tip}）\n"
        f"触发：{event['reason']}\n"
        f"开盘跳空：{result['gap_pct'] * 100:+.2f}%\n"
        f"早盘量能：{result['early_volume_ratio']:.2f}x\n"
        "\n仅供研究参考，不构成投资建议。"
    )


class IntradayTMonitor:
    """A lightweight daemon-thread monitor used by the FastAPI process."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._sent_keys: set[tuple[str, str, str, str]] = set()
        self._baselined_days: set[tuple[str, str]] = set()
        self._status = {
            "running": False,
            "enabled": True,
            "started_at": None,
            "last_scan_at": None,
            "last_scan_error": None,
            "total_scans": 0,
            "total_signals_pushed": 0,
            "last_signals": [],
        }

    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="intraday-t-monitor",
                daemon=True,
            )
            self._thread.start()
            self._status["running"] = True
            self._status["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._status["last_scan_error"] = None
            return True

    def stop(self) -> bool:
        with self._lock:
            was_running = self._thread is not None and self._thread.is_alive()
            self._stop_event.set()
            thread = self._thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=3)
            self._thread = None
            self._status["running"] = False
            return was_running

    def status(self) -> dict:
        with self._lock:
            return dict(self._status)

    def _poll_interval(self) -> int:
        try:
            cfg = _load_config()
            return int(cfg.get("intraday_t", {}).get("poll_interval_seconds", 60))
        except Exception:
            return 60

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.scan_once()
            except Exception as exc:  # keep the loop alive on unexpected errors
                with self._lock:
                    self._status["last_scan_error"] = f"{type(exc).__name__}: {exc}"
                    self._status["last_scan_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                traceback.print_exc()
            if self._stop_event.wait(self._poll_interval()):
                break

    def scan_once(self) -> None:
        config = _load_config()
        monitor_cfg = config.get("intraday_t", {})
        if not monitor_cfg.get("enabled", True):
            with self._lock:
                self._status["enabled"] = False
            return

        webhook = (config.get("alert") or {}).get("feishu_webhook", "")
        if not webhook:
            with self._lock:
                self._status["enabled"] = True
                self._status["last_scan_error"] = "未配置 alert.feishu_webhook，仅分析不推送"
            return

        if not _is_trading_time():
            # Keep status clean when the service is running outside market hours.
            return

        codes = [str(c).zfill(6) for c in (config.get("watchlist") or [])]
        pushed_this_scan = []
        scan_errors: list[str] = []
        push_error: str | None = None

        for code in codes:
            try:
                result = _evaluate_code(code, config)
            except Exception as exc:
                # Individual symbol errors should not block the rest of watchlist.
                scan_errors.append(f"{code} 获取/分析失败: {exc}")
                continue
            if not result:
                continue

            day_key = (code, result["date"])
            new_events: list[tuple[dict, tuple[str, str, str, str]]] = []
            with self._lock:
                if day_key not in self._baselined_days:
                    # First observation of a session: treat every already-visible
                    # signal as baseline so a restart does not replay old signals.
                    self._baselined_days.add(day_key)
                    for ev in result["events"]:
                        self._sent_keys.add((code, result["date"], ev["time"], ev["side"]))
                else:
                    for ev in result["events"]:
                        key = (code, result["date"], ev["time"], ev["side"])
                        if key in self._sent_keys:
                            continue
                        new_events.append((ev, key))
                        # A session normally has at most one newly formed event per poll.
                        break

            # Push outside the lock so a slow webhook cannot block monitor status.
            for ev, key in new_events:
                text = _build_feishu_text(result, ev)
                ok = _push_feishu(webhook, text)
                with self._lock:
                    if ok:
                        self._sent_keys.add(key)
                        self._status["total_signals_pushed"] += 1
                        pushed_this_scan.append({
                            "code": code,
                            "name": result["name"],
                            "date": result["date"],
                            "time": ev["time"],
                            "side": ev["side"],
                            "price": ev["price"],
                            "reason": ev["reason"],
                        })
                    else:
                        # Do not mark sent; retry on next poll.
                        push_error = "飞书推送失败，将在下一轮重试"

        with self._lock:
            self._status["enabled"] = True
            self._status["last_scan_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._status["last_scan_error"] = push_error or ("; ".join(scan_errors[:3]) or None)
            self._status["total_scans"] += 1
            if pushed_this_scan:
                self._status["last_signals"] = pushed_this_scan[-10:]


intraday_monitor = IntradayTMonitor()

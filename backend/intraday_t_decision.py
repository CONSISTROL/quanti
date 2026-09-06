"""Intraday T (做T) decision engine.



Rules:

  - Focus on high win rate

  - Use per-session minute MACD

  - Do T mainly when early volume is strong

  - Big low open  -> B then S

  - Big high open -> S then B

  - Otherwise follow the first intraday MACD cross

"""

from __future__ import annotations



import json

import os

import sys



import numpy as np

import pandas as pd



ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if ROOT not in sys.path:

    sys.path.insert(0, ROOT)



LOW_GAP = -0.005

HIGH_GAP = 0.005

EARLY_BARS = 6





def _session_macd(closes):

    """Per-session MACD acts.

    1分钟 K 线计算 MACD(12,26,9)，开盘前 10 分钟（09:30-09:39）先跳过：

    - 买入：DIF 在 0 轴下方上穿 DEA（低位金叉），避免 0 轴上方的普通金叉追高
    - 卖出：DIF 下穿 DEA（死叉）；或红柱从峰值第一次缩短（红柱缩短预警）

    """

    s = pd.Series(closes)

    ema_fast = s.ewm(span=12, adjust=False).mean()

    ema_slow = s.ewm(span=26, adjust=False).mean()

    dif = ema_fast - ema_slow

    dea = dif.ewm(span=9, adjust=False).mean()

    hist = (dif - dea).values
    acts = np.zeros(len(closes), dtype=int)
    warmup = 10  # 开盘前10根1分钟K线（09:30-09:39）MACD 未稳定，先不产生信号
    for i in range(warmup, len(closes)):
        dif_i = float(dif.iloc[i])
        dea_i = float(dea.iloc[i])
        dif_prev = float(dif.iloc[i - 1])
        dea_prev = float(dea.iloc[i - 1])

        # 低位金叉：DIF 上穿 DEA，且 DIF 在 0 轴下方（价格仍在低位区）-> 买入
        # 这能过滤 09:33/10:00 这类 0 轴上方的普通金叉，只等 10:23 附近的低位金叉。
        if dif_i > dea_i and dif_prev <= dea_prev and dif_i <= 0:
            acts[i] = 1
            continue

        # 死叉：DIF 下穿 DEA -> 卖出（例如 09:48）
        if dif_i < dea_i and dif_prev >= dea_prev:
            acts[i] = -1
            continue

        # 红柱缩短：红柱从峰值第一次收窄（例如 09:40）也作为卖出预警
        if hist[i - 1] > 0 and hist[i] < hist[i - 1] and hist[i - 1] >= hist[i - 2]:
            acts[i] = -1
    return acts





def _simulate_day(closes, volumes, gap_pct):

    """Simulate one intraday T round based on gap and per-session MACD."""

    acts = _session_macd(closes)

    early_vol = float(np.sum(volumes[:EARLY_BARS])) if len(volumes) >= EARLY_BARS else 0.0

    avg_bar_vol = float(np.mean(volumes)) if len(volumes) else 0.0

    early_ratio = early_vol / (avg_bar_vol * EARLY_BARS) if avg_bar_vol * EARLY_BARS > 0 else 0.0



    events = [i for i, a in enumerate(acts) if a != 0]



    mode = None

    if gap_pct <= LOW_GAP:

        mode = "B->S"

    elif gap_pct >= HIGH_GAP:

        mode = "S->B"

    elif events:
        mode = "B->S" if acts[events[0]] == 1 else "S->B"



    buy_idx = sell_idx = None

    if mode == "B->S":

        for i in events:

            if buy_idx is None:

                if acts[i] == 1:

                    buy_idx = i

            elif acts[i] == -1 and closes[i] > closes[buy_idx]:

                # 先买后卖：只有卖出价高于买入价才算完成一次高抛低吸
                sell_idx = i

                break

    elif mode == "S->B":

        for i in events:

            if sell_idx is None:

                if acts[i] == -1:

                    sell_idx = i

            elif acts[i] == 1 and closes[i] < closes[sell_idx]:

                # 先卖后买：只有买回价低于卖出价才算低吸成功，避免 10:00 追高买回
                buy_idx = i

                break



    if buy_idx is not None and sell_idx is not None and sell_idx != buy_idx:

        pnl = closes[sell_idx] / closes[buy_idx] - 1 if closes[buy_idx] > 0 else 0.0

        return {

            "mode": mode,

            "gap_pct": float(round(gap_pct, 4)),

            "early_volume_ratio": round(early_ratio, 3),

            "buy_time": f"{buy_idx}",

            "sell_time": f"{sell_idx}",

            "buy_price": round(float(closes[buy_idx]), 3),

            "sell_price": round(float(closes[sell_idx]), 3),

            "pnl": round(float(pnl), 4),

        }

    return None





def intraday_t_decision(code: str, max_days: int = 30) -> dict:

    code = str(code).zfill(6)

    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:

        config = json.load(f)



    from backend.kline import _fetch_daily_df, _fetch_tencent_1m

    daily = _fetch_daily_df(code, config.get("data", {})).copy()

    minute = _fetch_tencent_1m(code, max_bars=320).copy()



    if minute is None or len(minute) < 100:

        raise ValueError(f"{code} 分钟数据不足，无法分析日内做T")



    daily["date"] = pd.to_datetime(daily["date"]).dt.date

    daily = daily.sort_values("date").reset_index(drop=True)

    daily["prev_close"] = daily["close"].shift(1)

    prev_map = dict(zip(daily["date"], daily["prev_close"]))



    minute["dt"] = pd.to_datetime(minute["date"])

    minute["day"] = minute["dt"].dt.date

    minute = minute.sort_values("dt").reset_index(drop=True)



    results = []

    for day, grp in minute.groupby("day"):

        prev_close = prev_map.get(day)

        if prev_close is None or prev_close <= 0:

            continue

        grp = grp.sort_values("dt")

        opens = grp["open"].values.astype(float)

        closes = grp["close"].values.astype(float)

        volumes = grp["volume"].values.astype(float)

        if len(closes) < 20:

            continue

        gap_pct = opens[0] / prev_close - 1

        res = _simulate_day(closes, volumes, gap_pct)

        if res:

            res["date"] = str(day)

            results.append(res)



    results = results[-max_days:]

    completed = [r for r in results if r["pnl"] is not None]

    if completed:

        pnls = [r["pnl"] for r in completed]

        wins = [p for p in pnls if p > 0]

        losses = [p for p in pnls if p <= 0]

        stats = {

            "days": len(results),

            "completed": len(completed),

            "win_rate": round(len(wins) / len(completed), 4),

            "avg_pnl": round(float(np.mean(pnls)), 4),

            "avg_win": round(float(np.mean(wins)), 4) if wins else 0.0,

            "avg_loss": round(float(np.mean(losses)), 4) if losses else 0.0,

        }

    else:

        stats = {"days": len(results), "completed": 0, "win_rate": 0, "avg_pnl": 0, "avg_win": 0, "avg_loss": 0}



    latest = results[-1] if results else None

    decision = _build_decision(latest) if latest else {"mode": "观望", "reason": "最近交易日无可用MACD配对信号"}



    from backend.stock_name import get_stock_name

    try:

        name = get_stock_name(code)["name"]

    except Exception:

        name = code



    return {

        "mode": "intraday_t",

        "code": code,

        "name": name,

        "decision": decision,

        "stats": stats,

        "recent": results[-10:][::-1],

    }





def intraday_day_detail(code: str, date: str) -> dict:

    """Return minute bars/MACD/trade points for one trading date."""

    code = str(code).zfill(6)

    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:

        config = json.load(f)



    from backend.kline import _fetch_daily_df, _fetch_tencent_1m

    daily = _fetch_daily_df(code, config.get("data", {})).copy()

    minute = _fetch_tencent_1m(code, max_bars=320).copy()



    daily["date"] = pd.to_datetime(daily["date"]).dt.date

    daily = daily.sort_values("date").reset_index(drop=True)

    daily["prev_close"] = daily["close"].shift(1)

    prev_map = dict(zip(daily["date"], daily["prev_close"]))



    minute["dt"] = pd.to_datetime(minute["date"])

    minute["day"] = minute["dt"].dt.date

    target = pd.Timestamp(date).date()

    grp = minute[minute["day"] == target].sort_values("dt").reset_index(drop=True)

    if len(grp) < 10:

        raise ValueError(f"{code} 在 {date} 没有足够的分钟数据")



    closes = grp["close"].values.astype(float)

    opens = grp["open"].values.astype(float)

    highs = grp["high"].values.astype(float)

    lows = grp["low"].values.astype(float)

    volumes = grp["volume"].values.astype(float)

    prev_close = prev_map.get(target)

    gap_pct = float(opens[0] / prev_close - 1) if prev_close else 0.0



    acts = _session_macd(closes)

    sim = _simulate_day(closes, volumes, gap_pct)



    times = [ts.strftime("%H:%M") for ts in grp["dt"]]

    bars = []

    for i in range(len(closes)):

        bars.append({

            "time": times[i],

            "open": round(float(opens[i]), 3),

            "close": round(float(closes[i]), 3),

            "low": round(float(lows[i]), 3),

            "high": round(float(highs[i]), 3),

            "volume": round(float(volumes[i]), 2),

        })



    # Session MACD values for charting
    s_macd = pd.Series(closes)
    ema_f = s_macd.ewm(span=12, adjust=False).mean()
    ema_s = s_macd.ewm(span=26, adjust=False).mean()
    dif_v = (ema_f - ema_s).values
    dea_v = dif_v if False else pd.Series(dif_v).ewm(span=9, adjust=False).mean().values
    hist_v = dif_v - dea_v
    macd_series = [
        {"dif": round(float(dif_v[i]), 4), "dea": round(float(dea_v[i]), 4), "hist": round(float(hist_v[i]), 4)}
        for i in range(len(closes))
    ]

    buy_points = []

    sell_points = []

    if sim and sim.get("buy_time") is not None and sim.get("sell_time") is not None:

        b_idx = int(sim["buy_time"])

        s_idx = int(sim["sell_time"])

        if b_idx < len(times):

            buy_points.append({
                "time": times[b_idx],
                "price": sim["buy_price"],
                "reason": "DIF低位金叉买入（0轴下方上穿DEA）",
            })

        if s_idx < len(times):
            # 用实际触发条件给卖出点写清理由，而不是笼统叫“卖出”
            death_cross = s_idx > 0 and dif_v[s_idx] < dea_v[s_idx] and dif_v[s_idx - 1] >= dea_v[s_idx - 1]
            red_shrink = (s_idx >= 2 and hist_v[s_idx - 1] > 0 and hist_v[s_idx] < hist_v[s_idx - 1]
                          and hist_v[s_idx - 1] >= hist_v[s_idx - 2])
            sell_reason = "DIF死叉卖出" if death_cross else ("红柱缩短卖出" if red_shrink else "卖出信号")
            sell_points.append({
                "time": times[s_idx],
                "price": sim["sell_price"],
                "reason": sell_reason,
            })



    return {

        "code": code,

        "date": date,

        "gap_pct": gap_pct,

        "bars": bars,
        "macd_series": macd_series,

        "buy_points": buy_points,

        "sell_points": sell_points,

        "mode": sim["mode"] if sim else None,

        "pnl": sim["pnl"] if sim else None,

    }





def _build_decision(day):

    mode = day["mode"]

    if day.get("early_volume_ratio", 0) >= 1.2:

        volume_note = f"早盘量能较强（{day['early_volume_ratio']:.2f}倍日均）"

    else:

        volume_note = f"早盘量能一般（{day['early_volume_ratio']:.2f}倍日均），可降低仓位"

    if mode == "B->S":

        reason = f"{volume_note}；开盘跳空 {day['gap_pct']*100:+.2f}%，策略建议先B后S：0轴下方低位金叉先买入，红柱缩短/死叉后卖出"

    else:

        reason = f"{volume_note}；开盘跳空 {day['gap_pct']*100:+.2f}%，策略建议先S后B：红柱缩短/死叉先卖出，等0轴下方低位金叉再买回"

    return {

        "mode": mode,

        "reason": reason,

        "date": day.get("date"),

        "gap_pct": float(day["gap_pct"]),

        "early_volume_ratio": float(day["early_volume_ratio"]),

    }


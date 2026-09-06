"""K-line / overview data service for the self-hosted Super K-Line page.







Implements an approximation of the public "Super K-Line" overview:



  - daily / weekly / monthly OHLC



  - standard indicators (MA/MACD/SKDJ/volume ratio)



  - swing-pivot trend rays (support/resistance)



  - VPVR volume profile (POC / VAH / VAL)



"""



from __future__ import annotations







import json



import os



import pickle



import sys



import time



from datetime import datetime



from typing import Any







import requests



import numpy as np



import pandas as pd







ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



if ROOT not in sys.path:



    sys.path.insert(0, ROOT)







from backend.runners import WATCH_NAMES  # noqa: E402







INTERVAL_RULES = {



    "1d": (None, "日K"),



    "1w": ("W-FRI", "周K"),



    "1M": ("ME", "月K"),



}



MINUTE_INTERVALS = {



    "5m": (5, "5分钟"),



    "15m": (15, "15分钟"),



    "30m": (30, "30分钟"),



    "60m": (60, "60分钟"),



}



MINUTE_STRATEGIES = {



    "vwap": "VWAP回归",



    "macd": "MACD金叉死叉",



    "boll": "布林反转",
    "contrarian": "追涨杀跌反指",



}




MINUTE_SIGNAL_REASONS = {
    "vwap": {"buy": "跌破VWAP下轨买入", "sell": "上穿VWAP上轨卖出"},
    "macd": {"buy": "DIF上穿DEA（MACD金叉）", "sell": "DIF下穿DEA（MACD死叉）"},
    "boll": {"buy": "收盘跌破布林下轨", "sell": "收盘突破布林上轨"},
    "contrarian": {"buy": "杀跌信号触发反指买入（短期超跌）", "sell": "追涨信号触发反指卖出（短期冲高）"},
}

SINA_KLINE_URL = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"



                  "CN_MarketData.getKLineData")







CACHE_DIR = os.path.join(ROOT, "cache")











def _cache_path(code: str, key: str) -> str:



    return os.path.join(CACHE_DIR, f"kline_{key}_{code}.pkl")











def _read_df_cache(code: str, key: str, ttl_seconds: float):



    """Read a cached DataFrame if it is still within TTL."""



    path = _cache_path(code, key)



    if not os.path.exists(path):



        return None



    try:



        with open(path, "rb") as f:



            payload = pickle.load(f)



        saved_at = payload.get("saved_at")



        df = payload.get("df")



        if df is None:



            return None



        age = (datetime.now() - saved_at).total_seconds()



        if age <= ttl_seconds:



            return df



    except Exception:



        pass



    return None











def _write_df_cache(code: str, key: str, df) -> None:



    os.makedirs(CACHE_DIR, exist_ok=True)



    with open(_cache_path(code, key), "wb") as f:



        pickle.dump({"saved_at": datetime.now(), "df": df}, f)











def _load_config() -> dict:



    path = os.path.join(ROOT, "config.json")



    with open(path, "r", encoding="utf-8") as f:



        return json.load(f)











def _round(v, digits=4):



    try:



        if v is None or (isinstance(v, float) and v != v):  # NaN



            return None



        return round(float(v), digits)



    except (TypeError, ValueError):



        return None











def _performance_from_sells(sell_signals):



    """Compute simple compounded return stats from sell signals."""



    pnls = [s.get("pnl_pct") for s in (sell_signals or []) if s.get("pnl_pct") is not None]



    if not pnls:



        return {



            "total_return": 0.0,



            "trade_count": 0,



            "win_rate": 0.0,



            "avg_win": 0.0,



            "avg_loss": 0.0,



            "profit_loss_ratio": None,



        }



    ret = 1.0



    for p in pnls:



        ret *= (1 + p)



    wins = [p for p in pnls if p > 0]



    losses = [p for p in pnls if p < 0]



    avg_win = sum(wins) / len(wins) if wins else 0.0



    avg_loss = sum(losses) / len(losses) if losses else 0.0



    return {



        "total_return": ret - 1,



        "trade_count": len(pnls),



        "win_rate": len(wins) / len(pnls) if pnls else 0.0,



        "avg_win": avg_win,



        "avg_loss": avg_loss,



        "profit_loss_ratio": abs(avg_win / avg_loss) if avg_loss else None,



    }











def _envelope(closes, period=20, pct=0.02):



    """StockSharp-style Envelope: MA(period) offset by pct%."""



    s = pd.Series(closes)



    mid = s.rolling(period).mean()



    upper = [_round(v * (1 + pct)) if not pd.isna(v) else None for v in mid]



    lower = [_round(v * (1 - pct)) if not pd.isna(v) else None for v in mid]



    return upper, lower











def _resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:



    """Resample daily OHLCV to weekly/monthly. Returns date/OHLCV DataFrame."""



    if rule is None:



        return df.reset_index(drop=True)



    df = df.copy()



    df["date"] = pd.to_datetime(df["date"])



    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}



    if "volume" in df.columns:



        agg["volume"] = "sum"



    if "amount" in df.columns:



        agg["amount"] = "sum"



    out = (



        df.set_index("date")



        .resample(rule, label="right", closed="right")



        .agg(agg)



        .dropna(subset=["open"])



        .reset_index()



    )



    out["date"] = out["date"].dt.strftime("%Y-%m-%d")



    return out











def _sina_symbol(code: str) -> str:



    return f"sh{code}" if code[0] in ("5", "6", "9") else f"sz{code}"











_MINUTE_TTL = {5: 120, 15: 300, 30: 600, 60: 1800}











def _fetch_sina_minute(code: str, scale: int, refresh: bool = False, max_bars: int = 1023) -> pd.DataFrame:



    """Fetch minute bars from Sina with retry and a short TTL cache."""



    ttl = _MINUTE_TTL.get(scale, 120)


    requested = max(1023, min(int(max_bars), 5000))
    if not refresh:


        requested = max(1023, min(int(max_bars), 5000))
        cached = _read_df_cache(code, f"min{scale}", ttl)



        if cached is not None and len(cached) >= requested:



            return cached







    last_error: Exception | None = None



    for attempt in range(3):



        try:



            resp = requests.get(



                SINA_KLINE_URL,



                params={



                    "symbol": _sina_symbol(code),



                    "scale": str(scale),



                    "ma": "no",



                    "datalen": str(requested),



                },



                timeout=20,



            )



            resp.raise_for_status()



            j = resp.json()



            if not j:



                raise ValueError(f"{code} 无 {scale} 分钟数据")



            df = pd.DataFrame(j)



            df["date"] = pd.to_datetime(df["day"])



            for col in ("open", "high", "low", "close", "volume"):



                df[col] = pd.to_numeric(df[col], errors="coerce")



            df = df.dropna(subset=["open", "high", "low", "close"])



            df = df[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)



            if len(df) < 30:



                raise ValueError(f"{code} {scale}分钟数据不足")



            _write_df_cache(code, f"min{scale}", df)



            return df



        except Exception as e:  # noqa: BLE001 - Sina may reset connections



            last_error = e



            time.sleep(0.8 * (attempt + 1))



    raise RuntimeError(f"拉取 {code} {scale}分钟数据失败: {last_error}")











def _detect_pivots(highs, lows, order=5):



    """Return pivot points: list of dict(index, kind, price)."""



    pivots = []



    n = len(highs)



    for i in range(order, n - order):



        window_high = highs[i - order:i + order + 1]



        window_low = lows[i - order:i + order + 1]



        # Strict local high: greater than left/right neighbours (allow equal boundary)



        if highs[i] == window_high.max() and highs[i] > window_high[order - 1] and highs[i] >= window_high[order + 1]:



            pivots.append({"index": i, "kind": "high", "price": float(highs[i])})



        if lows[i] == window_low.min() and lows[i] < window_low[order - 1] and lows[i] <= window_low[order + 1]:



            pivots.append({"index": i, "kind": "low", "price": float(lows[i])})



    return pivots











def _build_trend_rays(dates, highs, lows, order=5, max_per_kind=120):



    """Build many pivot-fan support/resistance rays.







    Instead of only connecting consecutive pivots, this mirrors the original



    site's structure: several recent pivot anchors each collect rays from many



    earlier pivots, producing a dense fan of trend lines.



    """



    n = len(dates)



    if n < order * 2 + 2:



        return []



    highs_f = np.asarray(highs, dtype=float)



    lows_f = np.asarray(lows, dtype=float)



    pivots = _detect_pivots(highs_f, lows_f, order=order)







    anchor_counts = {"support": 5, "resistance": 20}



    rays_by_kind = {"support": [], "resistance": []}







    for kind, pivot_kind in (("support", "low"), ("resistance", "high")):



        same = [p for p in pivots if p["kind"] == pivot_kind]



        same.sort(key=lambda p: p["index"])



        if len(same) < 2:



            continue







        anchors = same[-anchor_counts[kind]:]



        seen = set()



        for anchor in anchors:



            for p in same:



                span = anchor["index"] - p["index"]



                if span < max(6, order * 2):



                    continue



                key = (p["index"], anchor["index"])



                if key in seen:



                    continue



                seen.add(key)







                start_idx = p["index"]



                start_price = p["price"]



                anchor_idx = anchor["index"]



                anchor_price = anchor["price"]



                slope = (anchor_price - start_price) / span



                # Extend the ray from the anchor pivot to the latest bar.



                ext_price = anchor_price + slope * (n - 1 - anchor_idx)



                if not np.isfinite(ext_price):



                    continue







                # Count touches after the anchor pivot.



                touches = 0



                for i in range(anchor_idx, n):



                    line_price = anchor_price + slope * (i - anchor_idx)



                    tol = max(abs(line_price) * 0.003, 0.01)



                    if kind == "support":



                        if lows_f[i] <= line_price + tol and highs_f[i] >= line_price - tol:



                            touches += 1



                    else:



                        if highs_f[i] >= line_price - tol and lows_f[i] <= line_price + tol:



                            touches += 1







                rays_by_kind[kind].append({



                    "kind": kind,



                    "start_date": dates[start_idx],



                    "end_date": dates[n - 1],



                    "anchor_date": dates[anchor_idx],



                    "start_price": _round(start_price, 4),



                    "end_price": _round(ext_price, 4),



                    "slope": _round(slope, 6),



                    "test_count": int(touches),



                    "score": _round(touches * max(1.0, abs(slope)) * 100, 2),



                    "source_type": "pivot_pair",



                    "lifecycle_state": "confirmed" if touches >= 3 else "tested" if touches > 0 else "forming",



                    "is_family_representative": False,



                })







        # Keep a bounded but still dense set, newest anchors first.



        kind_rays = rays_by_kind[kind]



        kind_rays.sort(key=lambda r: r["anchor_date"], reverse=True)



        rays_by_kind[kind] = kind_rays[:max_per_kind]







        # Mark one representative per anchor (the highest tested ray).



        best_by_anchor: dict[str, dict] = {}



        for r in kind_rays[:max_per_kind]:



            anchor = r["anchor_date"]



            if anchor not in best_by_anchor or r["test_count"] > best_by_anchor[anchor]["test_count"]:



                best_by_anchor[anchor] = r



        for r in best_by_anchor.values():



            r["is_family_representative"] = True







    return rays_by_kind["support"] + rays_by_kind["resistance"]











def _compute_vpvr(highs, lows, volumes, bins=40):



    """Standard Volume Profile approximation: bins, POC, 70% value area."""



    if volumes is None or len(volumes) < 5:



        return None



    highs = np.asarray(highs, dtype=float)



    lows = np.asarray(lows, dtype=float)



    vols = np.asarray(volumes, dtype=float)



    valid = np.isfinite(highs) & np.isfinite(lows) & np.isfinite(vols) & (highs >= lows)



    if not valid.any():



        return None



    highs, lows, vols = highs[valid], lows[valid], vols[valid]







    lo = float(np.min(lows))



    hi = float(np.max(highs))



    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:



        return None



    edges = np.linspace(lo, hi, bins + 1)



    hist = np.zeros(bins, dtype=float)







    for h, l, v in zip(highs, lows, vols):



        if h == l:



            idx = int(min(bins - 1, max(0, np.searchsorted(edges, h, side="right") - 1)))



            hist[idx] += v



            continue



        # Volume is distributed proportionally to the overlap between candle range and each bin.



        overlaps = np.maximum(0.0, np.minimum(h, edges[1:]) - np.maximum(l, edges[:-1]))



        total = overlaps.sum()



        if total > 0:



            hist += v * overlaps / total







    total_vol = hist.sum()



    if total_vol <= 0:



        return None



    shares = hist / total_vol



    poc_idx = int(np.argmax(hist))







    # 70% value area around POC.



    target = total_vol * 0.70



    included = {poc_idx}



    acc = hist[poc_idx]



    left, right = poc_idx, poc_idx



    while acc < target and (left > 0 or right < bins - 1):



        if left == 0:



            right += 1



            included.add(right)



            acc += hist[right]



        elif right == bins - 1:



            left -= 1



            included.add(left)



            acc += hist[left]



        elif hist[left - 1] >= hist[right + 1]:



            left -= 1



            included.add(left)



            acc += hist[left]



        else:



            right += 1



            included.add(right)



            acc += hist[right]







    bins_out = []



    for i in range(bins):



        bins_out.append({



            "price_low": _round(edges[i], 4),



            "price_high": _round(edges[i + 1], 4),



            "volume": _round(hist[i], 2),



            "volume_share": _round(shares[i], 6),



            "in_value_area": bool(i in included),



            "is_poc": bool(i == poc_idx),



        })



    return {



        "poc": _round((edges[poc_idx] + edges[poc_idx + 1]) / 2, 4),



        "vah": _round(edges[right + 1], 4),



        "val": _round(edges[left], 4),



        "bins": bins_out,



    }











def _minute_signal_acts(name: str, closes, highs, lows, volumes, vwap, dif, dea):



    """Return +1 buy / -1 sell / 0 none for an intraday signal."""



    n = len(closes)



    acts = np.zeros(n, dtype=int)



    if name == "vwap":



        threshold = 0.002



        for i in range(n):



            if vwap[i] is None:



                continue



            if closes[i] < vwap[i] * (1 - threshold):



                acts[i] = 1



            elif closes[i] > vwap[i] * (1 + threshold):



                acts[i] = -1



    elif name == "macd":



        for i in range(1, n):



            if dif[i] is None or dea[i] is None:



                continue



            if dif[i] > dea[i] and dif[i - 1] <= dea[i - 1]:



                acts[i] = 1



            elif dif[i] < dea[i] and dif[i - 1] >= dea[i - 1]:



                acts[i] = -1



    elif name == "boll":



        s = pd.Series(closes)



        mid = s.rolling(20).mean()



        sd = s.rolling(20).std()



        for i in range(1, n):



            if pd.isna(mid.iloc[i]) or pd.isna(sd.iloc[i]):



                continue



            if closes[i] < mid.iloc[i] - 2 * sd.iloc[i]:



                acts[i] = 1



            elif closes[i] > mid.iloc[i] + 2 * sd.iloc[i]:



                acts[i] = -1



    elif name == "contrarian":
        lookback = 12
        threshold = 0.01
        for i in range(lookback, n):
            prev = closes[i - lookback]
            if prev <= 0:
                continue
            ret = closes[i] / prev - 1
            # 预模拟“杀跌” -> 反指买入；预模拟“追涨” -> 反指卖出
            if ret < -threshold:
                acts[i] = 1
            elif ret > threshold:
                acts[i] = -1

    return acts











def _get_minute_kline_data(code: str, interval: str, max_bars: int,



                           strategy: str | None = None,



                           refresh: bool = False) -> dict:



    """Fetch intraday minute bars from Sina and build a K-line payload.







    Intraday bars cannot use the daily indicator engine because multiple bars



    share the same calendar date; this path computes lightweight rolling



    indicators (MA/VWAP/MACD) directly from arrays.



    """



    scale, interval_label = MINUTE_INTERVALS[interval]



    config = _load_config()



    trading_cfg = config.get("trading", {})



    minute_strategy = strategy if strategy in MINUTE_STRATEGIES else "vwap"



    strategy_name = minute_strategy



    df = _fetch_sina_minute(code, scale, refresh=refresh, max_bars=max_bars)







    n = len(df)



    start_idx = max(0, n - int(max_bars))



    df = df.iloc[start_idx:].reset_index(drop=True)



    n = len(df)







    closes = df["close"].values.astype(float)



    opens = df["open"].values.astype(float)



    highs = df["high"].values.astype(float)



    lows = df["low"].values.astype(float)



    volumes = df["volume"].values.astype(float)







    dates = [pd.Timestamp(d).strftime("%Y-%m-%d %H:%M") for d in df["date"]]



    kline = [[_round(o), _round(c), _round(l), _round(h)]



             for o, c, l, h in zip(opens, closes, lows, highs)]







    # Rolling MAs



    s = pd.Series(closes)



    ma5 = [_round(v) if not pd.isna(v) else None for v in s.rolling(5).mean()]



    ma20 = [_round(v) if not pd.isna(v) else None for v in s.rolling(20).mean()]



    ma60 = [_round(v) if not pd.isna(v) else None for v in s.rolling(60).mean()]



    ma120 = [_round(v) if not pd.isna(v) else None for v in s.rolling(120).mean()]



    envelope_upper = [_round(v * 1.02) if v is not None else None for v in ma20]



    envelope_lower = [_round(v * 0.98) if v is not None else None for v in ma20]







    # VWAP: reset at the start of each trading day (session VWAP).



    tp = (highs + lows + closes) / 3.0



    vwap_raw = np.empty_like(tp)



    cum_tpv = 0.0



    cum_vol = 0.0



    prev_day = None



    for i, ts in enumerate(df["date"]):



        day = ts.date()



        if day != prev_day:



            cum_tpv = 0.0



            cum_vol = 0.0



            prev_day = day



        cum_tpv += tp[i] * volumes[i]



        cum_vol += volumes[i]



        vwap_raw[i] = cum_tpv / cum_vol if cum_vol > 0 else tp[i]



    vwap = [_round(v) for v in vwap_raw]







    # MACD



    ema_fast = pd.Series(closes).ewm(span=12, adjust=False).mean()



    ema_slow = pd.Series(closes).ewm(span=26, adjust=False).mean()



    dif = (ema_fast - ema_slow)



    dea = dif.ewm(span=9, adjust=False).mean()



    hist = (dif - dea) * 2  # A股习惯放大柱体，也可不改



    dif = [_round(v) for v in dif]



    dea = [_round(v) for v in dea]



    macd_hist = [_round(v) for v in hist]







    # Intraday buy/sell markers using the selected minute strategy.



    # Each trading day starts flat; any position left open at day end is closed



    # at the last bar (VWAP/BOLL are intraday concepts, not overnight holds).



    acts = _minute_signal_acts(minute_strategy, closes, highs, lows, volumes,



                               vwap, dif, dea)



    buy_signals: list[dict] = []



    sell_signals: list[dict] = []



    i = 0



    n_bars = len(dates)



    while i < n_bars:



        day = dates[i][:10]



        holding = False



        entry_price = 0.0



        while i < n_bars and dates[i][:10] == day:



            act = int(acts[i])



            if not holding and act == 1:



                buy_signals.append({



                    "date": dates[i],



                    "price": _round(closes[i]),



                    "reason": MINUTE_SIGNAL_REASONS[minute_strategy]["buy"],



                })



                holding = True



                entry_price = closes[i]



            elif holding and act == -1:



                sell_signals.append({



                    "date": dates[i],



                    "price": _round(closes[i]),



                    "pnl_pct": _round(closes[i] / entry_price - 1, 6) if entry_price else None,



                    "reason": MINUTE_SIGNAL_REASONS[minute_strategy]["sell"],



                })



                holding = False



            i += 1



        # Force close at the last bar of the day if still holding.



        if holding and i > 0:



            last_i = i - 1



            sell_signals.append({



                "date": dates[last_i],



                "price": _round(closes[last_i]),



                "pnl_pct": _round(closes[last_i] / entry_price - 1, 6) if entry_price else None,



                "reason": MINUTE_SIGNAL_REASONS[minute_strategy]["sell"] + "（日内平仓）",



            })







    vpvr = _compute_vpvr(highs, lows, volumes, bins=40)



    last = closes[-1]



    prev = closes[-2] if n > 1 else last



    change_pct = last / prev - 1 if prev else 0.0



    name = WATCH_NAMES.get(code, code)







    return {



        "code": code,



        "name": name,



        "strategy": strategy_name,



        "source": "sina",



        "interval": interval,



        "interval_label": interval_label,



        "dates": dates,



        "kline": kline,



        "volumes": [_round(v) for v in volumes],



        "ma5": ma5,



        "ma20": ma20,



        "ma60": ma60,



        "ma120": ma120,



        "vwap": vwap,



        "skdj_k": [],



        "skdj_d": [],



        "skdj_j": [],



        "dif": dif,



        "dea": dea,



        "macd_hist": macd_hist,



        "vol_ratio": [],



        "envelope_upper": envelope_upper,



        "envelope_lower": envelope_lower,



        "buy_signals": buy_signals,



        "sell_signals": sell_signals,



        "performance": _performance_from_sells(sell_signals),



        "trends": [],



        "vpvr": vpvr,



        "overview": {



            "date": dates[-1],



            "close": _round(last),



            "prev_close": _round(prev),



            "change_pct": _round(change_pct, 6),



            "high": _round(highs[-1]),



            "low": _round(lows[-1]),



            "volume": _round(volumes[-1]),



            "vol_ratio": None,



            "ma5": ma5[-1],



            "ma20": ma20[-1],



            "ma60": ma60[-1],



            "ma120": ma120[-1],



            "vwap": vwap[-1],



            "skdj_k": None,



            "skdj_d": None,



            "skdj_j": None,



            "dif": dif[-1],



            "dea": dea[-1],



            "macd_hist": macd_hist[-1],



            "ret_5d": None,



            "ret_60d": None,



        },



    }











def _fetch_daily_df(code: str, data_cfg: dict, refresh: bool = False) -> pd.DataFrame:



    """Fetch daily bars and cache them for 1 hour to avoid re-pulling on restarts."""



    if not refresh:



        cached = _read_df_cache(code, "1d", ttl_seconds=3600)



        if cached is not None:



            return cached







    from quantlab.data_sources import get_data_source



    ds = get_data_source(data_cfg.get("source", "quantdash"))



    hist = ds.fetch_watchlist_data(



        [code],



        cache_dir=data_cfg.get("cache_dir", "cache"),



        use_cache=False if refresh else not data_cfg.get("no_cache", False),



        max_bars=data_cfg.get("hist_days", 1200),



    )







    from quantlab.data_fetcher import _code_pure



    sina_key = None



    for k in hist:



        if _code_pure(k) == code:



            sina_key = k



            break



    if sina_key is None:



        raise ValueError(f"未获取到 {code} 的 K 线数据")







    df = hist[sina_key]



    if df is None or len(df) < 120 or "date" not in df.columns:



        raise ValueError(f"{code} 数据不足，无法计算指标（至少需要 120 根 K 线）")



    _write_df_cache(code, "1d", df)



    return df











def get_kline_data(code: str, strategy: str | None = None,



                   max_bars: int = 500, interval: str = "1d",



                   refresh: bool = False) -> dict:



    """Fetch one symbol's OHLC plus indicators, trend rays and VPVR."""



    code = str(code).zfill(6)



    if interval in MINUTE_INTERVALS:



        return _get_minute_kline_data(code, interval, max_bars, strategy, refresh=refresh)







    config = _load_config()



    data_cfg = config.get("data", {})



    trading_cfg = config.get("trading", {})







    rule, interval_label = INTERVAL_RULES.get(interval, (None, "日K"))



    if interval not in INTERVAL_RULES:



        interval = "1d"



        rule, interval_label = INTERVAL_RULES[interval]







    df = _fetch_daily_df(code, data_cfg, refresh=refresh)







    daily_df = df



    df = _resample_ohlcv(daily_df, rule)



    if len(df) < 120:



        # Weekly/monthly too short for the full indicator engine; fall back to daily.



        if interval != "1d":



            df = _resample_ohlcv(daily_df, None)



        else:



            raise ValueError(f"{code} 数据不足，无法计算指标")







    dates_arr = df["date"].values



    closes = df["close"].values.astype(float)



    volumes = df["volume"].values.astype(float) if "volume" in df.columns else None



    highs = df["high"].values.astype(float) if "high" in df.columns else None



    lows = df["low"].values.astype(float) if "low" in df.columns else None



    opens = df["open"].values.astype(float) if "open" in df.columns else None



    if highs is None:



        highs = closes.copy()



    if lows is None:



        lows = closes.copy()



    if opens is None:



        opens = closes.copy()







    all_dates = set(pd.Timestamp(d).strftime("%Y-%m-%d") for d in dates_arr)



    from quantlab.indicator_cache import _incremental_indicators



    ind_map = _incremental_indicators(closes, volumes, highs, lows, dates_arr, all_dates)



    if not ind_map:



        raise ValueError(f"{code} 指标计算失败")







    # Limit returned history.



    n = len(df)



    start_idx = max(0, n - int(max_bars))



    sliced = df.iloc[start_idx:]







    strategy_name = strategy or trading_cfg.get("strategy", "reversal")



    if strategy_name in ("envelope", "contrarian"):



        strat = None  # envelope/contrarian custom decisions



    else:



        from quantlab.strategies import get_strategy



        strat = get_strategy(strategy_name)







    dates: list[str] = []



    kline: list[list[float]] = []



    volumes_out: list[float] = []



    ma5: list[float | None] = []



    ma20: list[float | None] = []



    ma60: list[float | None] = []



    ma120: list[float | None] = []



    skdj_k: list[float | None] = []



    skdj_d: list[float | None] = []



    skdj_j: list[float | None] = []



    dif: list[float | None] = []



    dea: list[float | None] = []



    macd_hist: list[float | None] = []



    vol_ratio: list[float | None] = []



    envelope_upper: list[float | None] = []



    envelope_lower: list[float | None] = []



    buy_signals: list[dict] = []



    sell_signals: list[dict] = []



    # Simple single-position simulation so the chart can show both buy and sell.



    position: dict | None = None



    sold_today = False



    tr_cfg = config.get("trading", {})



    stop_loss = tr_cfg.get("stop_loss")



    take_profit = tr_cfg.get("take_profit")



    max_holding_days = int(tr_cfg.get("max_holding_days", 0) or 0)







    for bar_idx, (_, row) in enumerate(sliced.iterrows()):



        d = pd.Timestamp(row["date"]).strftime("%Y-%m-%d")



        ind = ind_map.get(d)



        if ind is None:



            continue



        close_price = float(row["close"])



        dates.append(d)



        kline.append([_round(row["open"]), _round(close_price),



                      _round(row["low"]), _round(row["high"])])



        volumes_out.append(_round(row["volume"]) if "volume" in row and pd.notna(row["volume"]) else None)



        ma5.append(_round(ind.get("ma5")) if ind.get("ma5") else None)



        ma20.append(_round(ind.get("ma20")) if ind.get("ma20") else None)



        env_ma20 = ind.get("ma20")



        envelope_upper.append(_round(env_ma20 * 1.02) if env_ma20 else None)



        envelope_lower.append(_round(env_ma20 * 0.98) if env_ma20 else None)



        ma60.append(_round(ind.get("ma60")) if ind.get("ma60") else None)



        ma120.append(_round(ind.get("ma120")) if ind.get("ma120") else None)



        skdj_k.append(_round(ind.get("skdj_k")))



        skdj_d.append(_round(ind.get("skdj_d")))



        skdj_j.append(_round(ind.get("skdj_j")))



        dif.append(_round(ind.get("dif")))



        dea.append(_round(ind.get("dea")))



        macd_hist.append(_round(ind.get("macd_hist")))



        vol_ratio.append(_round(ind.get("vol_ratio")))







        sold_today = False



        sell_reason = None







        # 1) Check exit when holding.



        if position is not None:



            entry_price = position["entry_price"]



            holding_days = bar_idx - position["entry_idx"]



            current_pnl = close_price / entry_price - 1 if entry_price else 0.0



            position["max_profit"] = max(position["max_profit"], current_pnl)



            if stop_loss is not None and current_pnl <= float(stop_loss):



                sell_reason = f"止损({current_pnl:.1%})"



            elif take_profit is not None and current_pnl >= float(take_profit):



                sell_reason = f"止盈({current_pnl:.1%})"



            elif max_holding_days > 0 and holding_days >= max_holding_days:



                sell_reason = f"持有{holding_days}天到期"



            else:



                if strat is None:



                    if strategy_name == "envelope":
                        if env_ma20 and close_price >= env_ma20 * 1.02:
                            sell_reason = "上穿包络上轨卖出"
                    elif strategy_name == "contrarian":
                        ret5 = float(ind.get("ret_5d") or 0)
                        # 追涨信号触发反指卖出
                        if env_ma20 and ret5 > 0.02 and close_price > env_ma20:
                            sell_reason = "追涨信号触发反指卖出（强势冲高）"



                else:



                    try:



                        is_sell, reason = strat.sell_signal(



                            ind, entry_price, holding_days, position["max_profit"])



                        if is_sell:



                            sell_reason = reason



                    except Exception:



                        pass



            if sell_reason:



                sell_signals.append({



                    "date": d,



                    "price": _round(close_price),



                    "pnl_pct": _round(current_pnl, 6),



                    "reason": str(sell_reason),



                })



                position = None



                sold_today = True







        # 2) Check entry when flat.



        if position is None and not sold_today:



            if strat is None:



                if strategy_name == "envelope":
                    if env_ma20 and close_price <= env_ma20 * 0.98:
                        buy_signals.append({
                            "date": d,
                            "price": _round(close_price),
                            "score": None,
                            "reason": "跌破包络下轨买入",
                        })
                        position = {
                            "entry_price": close_price,
                            "entry_idx": bar_idx,
                            "max_profit": 0.0,
                        }
                elif strategy_name == "contrarian":
                    ret5 = float(ind.get("ret_5d") or 0)
                    # 杀跌信号触发反指买入
                    if env_ma20 and ret5 < -0.02 and close_price < env_ma20:
                        buy_signals.append({
                            "date": d,
                            "price": _round(close_price),
                            "score": None,
                            "reason": "杀跌信号触发反指买入（超跌回落）",
                        })
                        position = {
                            "entry_price": close_price,
                            "entry_idx": bar_idx,
                            "max_profit": 0.0,
                        }


            else:



                try:



                    is_buy, score, reason = strat.buy_signal(ind)



                    if is_buy:



                        buy_signals.append({



                            "date": d,



                            "price": _round(close_price),



                            "score": _round(score),



                            "reason": str(reason),



                        })



                        position = {



                            "entry_price": close_price,



                            "entry_idx": bar_idx,



                            "max_profit": 0.0,



                        }



                except Exception:



                    pass







    if not dates:



        raise ValueError(f"{code} 没有可展示的数据")







    # Trend rays and VPVR are computed on the visible window only, so overlays



    # correspond to the currently displayed history rather than ancient pivots.



    trends = _build_trend_rays(dates, highs[start_idx:], lows[start_idx:], order=5)



    vpvr = _compute_vpvr(highs[start_idx:], lows[start_idx:],



                         volumes[start_idx:] if volumes is not None else None,



                         bins=40)












    # Latest overview metrics.



    last = ind_map.get(dates[-1], {})



    prev = ind_map.get(dates[-2], {}) if len(dates) > 1 else {}



    last_close = float(last.get("close", closes[-1]))



    prev_close = float(prev.get("close", last.get("prev_close", last_close)))



    change_pct = (last_close / prev_close - 1) if prev_close else 0.0







    name = WATCH_NAMES.get(code, code)



    return {



        "code": code,



        "name": name,



        "strategy": strategy_name,



        "source": data_cfg.get("source", "quantdash"),



        "interval": interval,



        "interval_label": interval_label if interval in INTERVAL_RULES else "日K",



        "dates": dates,



        "kline": kline,



        "volumes": volumes_out,



        "ma5": ma5,



        "ma20": ma20,



        "ma60": ma60,



        "ma120": ma120,



        "skdj_k": skdj_k,



        "skdj_d": skdj_d,



        "skdj_j": skdj_j,



        "dif": dif,



        "dea": dea,



        "macd_hist": macd_hist,



        "vol_ratio": vol_ratio,



        "envelope_upper": envelope_upper,



        "envelope_lower": envelope_lower,



        "buy_signals": buy_signals,



        "sell_signals": sell_signals,



        "performance": _performance_from_sells(sell_signals),



        "trends": trends,



        "vpvr": vpvr,



        "overview": {



            "date": dates[-1],



            "close": _round(last_close),



            "prev_close": _round(prev_close),



            "change_pct": _round(change_pct, 6),



            "high": _round(float(row["high"])),



            "low": _round(float(row["low"])),



            "volume": volumes_out[-1],



            "vol_ratio": _round(last.get("vol_ratio")),



            "ma5": _round(last.get("ma5")),



            "ma20": _round(last.get("ma20")),



            "ma60": _round(last.get("ma60")),



            "ma120": _round(last.get("ma120")),



            "skdj_k": _round(last.get("skdj_k")),



            "skdj_d": _round(last.get("skdj_d")),



            "skdj_j": _round(last.get("skdj_j")),



            "dif": _round(last.get("dif")),



            "dea": _round(last.get("dea")),



            "macd_hist": _round(last.get("macd_hist")),



            "ret_5d": _round(last.get("ret_5d"), 6),



            "ret_60d": _round(last.get("ret_60d"), 6),



        },



    }




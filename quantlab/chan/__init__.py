"""缠论分析入口 —— 用《教你炒股票108课》的走势结构方法分析一段K线。

规则全部来自原文(出处见各模块 docstring,课文在 D:\\Code\\chzhshch-108-plus\\108\\),
输出契约供 Web 前端直接消费。

用法:
    from quantlab.chan import analyze
    result = analyze(df, interval="1d", higher_zhongshus=quick_zhongshus(week_df))
"""
from __future__ import annotations

from . import narrative, structure, zhongshu

__all__ = ["analyze", "quick_zhongshus", "macd", "LEVELS"]

LEVELS = ("1d", "1w", "1M")
LEVEL_LABELS = {"1d": "日线", "1w": "周线", "1M": "月线"}


def _r(v, digits: int = 4):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:
        return None
    return round(f, digits)


def _prepare(df):
    """归一化:按日期升序、去重、剔除价格缺失的行。

    前端的 category 轴靠这份 dates 定位笔与中枢,重复或乱序会让坐标匹配错位;价格缺失
    的行会让分型/笔算在错误的位置上,这里直接剔除并如实报错,而不是带病往下算。
    """
    import pandas as pd
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    for col in ("open", "high", "low", "close"):
        d[col] = pd.to_numeric(d[col], errors="coerce")
    d["volume"] = pd.to_numeric(d.get("volume", 0), errors="coerce").fillna(0.0)
    d = d.dropna(subset=["date", "open", "high", "low", "close"])
    d = d.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    if len(d) < 10:
        raise ValueError(f"有效K线只有 {len(d)} 根,无法做缠论结构分析")
    return d


def _structure(d):
    """跑一遍包含处理 → 分型 → 笔 → 线段,并把日期挂到笔和线段上。"""
    dates = [ts.strftime("%Y-%m-%d") for ts in d["date"]]
    bars = structure.merge_inclusive(d["high"].values, d["low"].values)
    fractals = structure.find_fractals(bars)
    bis = structure.build_bis(fractals)
    for b in bis:
        b["start_date"] = dates[b["start_bar"]]
        b["end_date"] = dates[b["end_bar"]]
    segs = structure.build_segments(bis)
    for s in segs:
        s["start_date"] = dates[s["start_bar"]]
        s["end_date"] = dates[s["end_bar"]]
    return dates, bars, fractals, bis, segs


def _pending_bi(d, bis, dates) -> dict | None:
    """未完成的那一笔(待确认)。

    缠论只画端点分型已确认的笔,所以最新一段走势在图上会是断的 —— 从最后一个笔的端点
    走到当前极值的这一笔还没走完。这里按"当前极值"给出一个估计值,方向与最后一笔相反。

    它随时可能被改写(行情继续创新极值就会延长,转向则会被否掉),所以单独放在
    `pending_bi` 而不是混进 `bis`,前端用虚线画、并明确标注"未完成"。
    """
    if not bis:
        return None
    last = bis[-1]
    start_bar = last["end_bar"]
    if start_bar >= len(d) - 1:
        return None
    want = "down" if last["direction"] == "up" else "up"
    highs = d["high"].values[start_bar:]
    lows = d["low"].values[start_bar:]
    if want == "down":
        off = int(lows.argmin())
        end_price = float(lows[off])
    else:
        off = int(highs.argmax())
        end_price = float(highs[off])
    # 必须真的朝反方向走出来了,否则谈不上"未完成的一笔"
    if want == "down" and end_price >= last["end_price"]:
        return None
    if want == "up" and end_price <= last["end_price"]:
        return None
    end_bar = start_bar + off
    if off < 1:  # 极值就落在起始那根上,没有实际走势,不成其为「一笔」
        return None
    return {
        "direction": want,
        "start_date": last["end_date"],
        "start_price": _r(last["end_price"], 2),
        "end_date": dates[end_bar],
        "end_price": _r(end_price, 2),
        "bars": end_bar - start_bar,
        "at_latest": end_bar == len(d) - 1,
        "note": ("从最后一个已确认笔的端点走到当前极值;尚无反向分型确认,"
                 "随时可能被延长或否掉,只作参考"),
        "lesson": "077",
    }


def quick_zhongshus(df) -> list[dict]:
    """只算到中枢为止(含日期),供跨级别的中阴判定使用。"""
    bis = _structure(_prepare(df))[3]
    zs = zhongshu.find_zhongshus(bis)
    for z in zs:
        z["start_date"] = bis[z["bi_from"]]["start_date"]
        z["end_date"] = bis[z["bi_to"]]["end_date"]
    return zs


def analyze(df, interval: str = "1d", higher_zhongshus: list[dict] | None = None) -> dict:
    """完整缠论分析。

    df 需含 date/open/high/low/close/volume 列。higher_zhongshus 为高一级别中枢列表
    (由 quick_zhongshus 得到),用于第089课的中阴阶段判定。
    """
    if interval not in LEVELS:
        raise ValueError(f"interval 必须是 {LEVELS} 之一")
    d = _prepare(df)

    dates, bars, fractals, bis, segs = _structure(d)
    closes = d["close"].values
    dif, dea, hist = zhongshu.macd(closes)

    zs = zhongshu.find_zhongshus(bis)
    for i, z in enumerate(zs):
        z["start_date"] = bis[z["bi_from"]]["start_date"]
        z["end_date"] = bis[z["bi_to"]]["end_date"]
        z["is_current"] = i == len(zs) - 1
        z["leave_date"] = bis[z["leave_bi"]]["end_date"] if z["leave_bi"] is not None else None

    trend = zhongshu.classify_trend(zs)
    divs = zhongshu.detect_divergence(bis, hist, zs)
    sigs = zhongshu.find_signals(bis, zs, divs)

    mas = narrative.ma_series(closes)
    ma_cat = narrative.ma_category(closes, mas)
    bot = narrative.bottom_zone(bars, fractals, closes, interval)
    zy = zhongshu.zhongyin_state(divs, higher_zhongshus or [])

    kline = [[_r(o, 2), _r(c, 2), _r(l, 2), _r(h, 2)]
             for o, c, l, h in zip(d["open"], d["close"], d["low"], d["high"])]
    volume = [_r(v, 2) for v in d["volume"]]

    summary = narrative.build_summary({
        "interval": interval, "trend": trend, "zhongshus": zs, "divergences": divs,
        "signals": sigs, "ma_category": ma_cat, "zhongyin": zy, "bis": bis,
        "close": float(closes[-1]),
    })

    payload = {
        "interval": interval,
        "level": LEVEL_LABELS[interval],
        "as_of": dates[-1],
        "dates": dates,
        "kline": kline,
        "volume": volume,
        "macd_hist": [_r(v, 6) for v in hist],
        "dif": [_r(v, 6) for v in dif],
        "dea": [_r(v, 6) for v in dea],
        "mas": mas,
        "bis": [{
            "bi_index": i, "direction": b["direction"],
            "start_date": b["start_date"], "start_price": _r(b["start_price"], 2),
            "end_date": b["end_date"], "end_price": _r(b["end_price"], 2),
            "start_fx": b["start_fx"], "end_fx": b["end_fx"],
        } for i, b in enumerate(bis)],
        "segments": [{
            "start_bi": s["start_bi"], "end_bi": s["end_bi"], "bi_count": s["bi_count"],
            "direction": s["direction"],
            "start_date": s["start_date"], "start_price": _r(s["start_price"], 2),
            "end_date": s["end_date"], "end_price": _r(s["end_price"], 2),
        } for s in segs],
        "pending_bi": _pending_bi(d, bis, dates),
        "zhongshus": [{
            "bi_from": z["bi_from"], "bi_to": z["bi_to"], "bi_count": z["bi_count"],
            "z_count": z["z_count"], "direction": z["direction"],
            "start_date": z["start_date"], "end_date": z["end_date"],
            "zg": _r(z["zg"], 2), "zd": _r(z["zd"], 2),
            "gg": _r(z["gg"], 2), "dd": _r(z["dd"], 2),
            "is_extended": z["is_extended"], "is_current": z["is_current"],
            "leave_date": z["leave_date"],
            "leave_reason": z.get("leave_reason"),
        } for z in zs],
        "signals": [{
            "date": s["date"], "price": _r(s["price"], 2), "kind": s["kind"],
            "label": s["label"], "note": s["note"], "lesson": s["lesson"],
            "direction": "buy" if s["kind"].startswith("buy") else "sell",
        } for s in sigs],
        "divergences": [{
            "date": x["date"], "price": _r(x["price"], 2), "kind": x["kind"],
            "scope": x["scope"], "ratio": _r(x["ratio"], 3),
            "ratio_per_bar": _r(x.get("ratio_per_bar"), 3),
            "area_prev": _r(x["area_prev"], 2), "area_curr": _r(x["area_curr"], 2),
            "bars_prev": x.get("bars_prev"), "bars_curr": x.get("bars_curr"),
            "note": x["note"], "lesson": x["lesson"],
        } for x in divs],
        "trend": {
            "kind": trend["kind"], "label": trend["label"],
            "note": trend["note"], "lesson": trend.get("lesson"),
        },
        "ma_category": ma_cat,
        "bottom_zone": bot,
        "zhongyin": zy,
        "counts": {
            "bars": len(d), "fractals": len(fractals), "bis": len(bis),
            "segments": len(segs), "zhongshus": len(zs),
            "capped_zhongshus": sum(1 for z in zs if z["is_extended"]),
            "divergences": len(divs), "signals": len(sigs),
        },
        "summary": summary,
        "basis": {
            "zhongshu_unit": "笔中枢:以「笔」作为中枢的次级别单位,线段不参与中枢计算",
            "rules": "规则来自《教你炒股票》原文,出处课号见各条 note;"
                     "课文未给出可量化标准的部分不做二元判断,只报告原始事实",
            "source": "D:/Code/chzhshch-108-plus/108",
            "min_bi_gap": structure.MIN_BI_GAP,
            "ambiguity": ("最短笔的原文口径有张力:第077课要求「顶底之间至少有一根K线不属于"
                          f"顶分型与底分型」(合并K线上相隔 ≥{structure.MIN_BI_GAP} 根),"
                          "第106课则说一笔至少延伸 6 个基本K线单位。本实现取第077课 —— "
                          "它在日/周/月三个级别上都能给出合理结构,而按第106课口径收紧后"
                          "月线笔数会从数十根塌到个位数,无法支撑第069、108课的月线分段。"),
        },
    }
    return payload


macd = zhongshu.macd

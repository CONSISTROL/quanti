"""均线系统九分类、底部区间与本地规则研判文字。

课文出处:D:\\Code\\chzhshch-108-plus\\108\\
所有结论都由前面算出的结构事实拼装而成,每一段都标注出处课号。课文未给出可量化
标准的地方(均线的「缠绕」与「有效跌破」、分型的「长阳/长阴」、背驰的「0轴附近」)
一律不做二元判断,只报告事实。
"""
from __future__ import annotations

MA_PERIODS = (5, 13, 21, 34, 55, 89, 144, 233)  # 第106课的均线系统


def ma_series(closes) -> dict[str, list[float | None]]:
    """八条均线(第106课)。不足周期的位置补 None,避免前端把短线数组从索引 0 起画。"""
    import pandas as pd
    s = pd.Series([float(c) for c in closes], dtype="float64")
    out: dict[str, list[float | None]] = {}
    for p in MA_PERIODS:
        if len(s) >= p:
            vals: list = s.rolling(p).mean().tolist()
        else:
            vals = [None] * len(s)
        # 既要挡住滚动窗口开头的 NaN,也要挡住整条 None(历史K线短于该均线周期)
        out[f"ma{p}"] = [None if v is None or v != v else round(float(v), 4) for v in vals]
    return out


def ma_category(closes, mas: dict) -> dict:
    """均线九分类(第106课)。

    分类原则是「本次反弹目前为止未曾攻克的最小周期均线」,8 条均线分成 9 类,完全
    在所有均线下为最弱的一类(第1类),全部攻克为第9类。

    这里按**最新收盘价**在均线系统中的位置判断:自最小周期起找第一条尚未站上的均线,
    类别号 = 1 + 该均线在 8 条中的序号。简化为按最新收盘判断,而非逐笔回溯「本次反弹」
    的起点 —— 第106课未给出「本次反弹」起点的可量化定义。

    历史K线短于均线周期时(例如科创50 的月线不足 233 根),那条均线不可用;此时只按
    可得的均线分类并标记 partial,否则会把「数据不足」误报成最强的第 9 类。
    """
    close = float(closes[-1])
    if close != close:
        return {"category": None, "note": "最新价缺失", "lesson": "106"}
    usable = [p for p in MA_PERIODS if (mas.get(f"ma{p}") or [None])[-1] is not None]
    if not usable:
        return {"category": None, "partial": True,
                "note": "历史K线少于最短均线周期,均线系统不可用,第106课的分类无法计算",
                "lesson": "106"}
    conquered, unconquered = [], None
    for p in usable:
        if close > mas[f"ma{p}"][-1]:
            conquered.append(p)
        elif unconquered is None:
            unconquered = p
    partial = len(usable) < len(MA_PERIODS)
    if unconquered is None:
        cat = 1 + len(usable)
        note = (f"已站上全部 {len(usable)} 条可计算均线,"
                f"按第106课九分类属第{cat}类" + ("(均线系统不完整,类号偏保守)" if partial else ""))
    else:
        cat = 1 + MA_PERIODS.index(unconquered)
        note = (f"自最小周期起尚未攻克的最小周期均线是 MA{unconquered},"
                f"第106课九分类中的第{cat}类(共9类,越强类号越大)")
    if partial:
        note += f";注意 MA{[p for p in MA_PERIODS if p not in usable]} 因历史K线不足而不可用"
    return {
        "category": cat,
        "conquered": [f"ma{p}" for p in conquered],
        "weakest_unconquered_ma": None if unconquered is None else f"ma{unconquered}",
        "usable_mas": [f"ma{p}" for p in usable],
        "partial": partial,
        "close": round(close, 4),
        "note": note,
        "lesson": "106",
    }


def bottom_zone(bars, fractals, closes, level: str) -> dict:
    """底部区间(第108课)。

    第108课:底部就是底分型的区间;跌破最低点即失败,有效站住区间上沿即成功且至少
    有一笔向上。买点在「区间下探失败时」买,不在区间上买。

    下沿取该底分型的极值低点,上沿取该底分型三根K线的最高点。
    """
    bots = [f for f in fractals if f.kind == "bottom"]
    if not bots:
        return {"status": "none", "note": "尚无底分型,底部区间未成形", "lesson": "108"}
    last = bots[-1]
    m = last.m_idx
    if m <= 0 or m + 1 >= len(bars):
        return {"status": "none", "note": "底分型不完整", "lesson": "108"}
    lo = min(bars[m - 1].lo, bars[m].lo, bars[m + 1].lo)
    hi = max(bars[m - 1].hi, bars[m].hi, bars[m + 1].hi)
    close = float(closes[-1])
    if close < lo:
        status, label = "failed", "已跌破底分型最低点,按第108课判为失败"
    elif close >= hi:
        status, label = "holding", "已站住底分型区间上沿,按第108课为成功且至少有一笔向上"
    else:
        status, label = "inside", "价格仍在底分型区间内,底部尚未确认"
    return {
        "status": status,
        "zone_low": round(lo, 4),
        "zone_high": round(hi, 4),
        "level": level,
        "note": f"底分型区间 [{lo:.4f}, {hi:.4f}];{label}(第108课)",
        "lesson": "108",
    }


def _level_name(interval: str) -> str:
    return {"1d": "日线", "1w": "周线", "1M": "月线"}.get(interval, interval)


def build_summary(ctx: dict) -> dict:
    """本地规则研判文字。每一条都由已算出的结构事实拼装,并标注出处课号。"""
    interval = ctx["interval"]
    lv = _level_name(interval)
    trend = ctx["trend"]
    zs = ctx["zhongshus"]
    divs = ctx["divergences"]
    sigs = ctx["signals"]
    ma_cat = ctx["ma_category"]
    zy = ctx.get("zhongyin") or {}
    bis = ctx["bis"]
    cur = bis[-1] if bis else None

    bullets: list[dict] = []
    lessons: list[str] = []

    def add(text: str, lesson: str) -> None:
        bullets.append({"text": text, "lesson": lesson})
        for x in lesson.replace("/", " ").split():
            if x not in lessons:
                lessons.append(x)

    # 1) 走势类型与级别
    add(f"{lv}级别当前是{trend['label']}。{trend['note']}", trend.get("lesson", "017"))

    # 2) 当前中枢位置
    if zs:
        z = zs[-1]
        close = ctx.get("close")
        if z["leave_bi"] is None:
            # 还没有出现完整离开区间的笔。此时最新价可能已经越过边沿(那笔尚未走完),
            # 不能一律说成「仍在区间内震荡」。
            if close is None:
                pos = "尚未出现完整离开该中枢的笔"
            elif close > z["zg"]:
                pos = (f"最新价 {close:.2f} 已越过中枢上沿 ZG,但还没有形成完整离开区间的笔")
            elif close < z["zd"]:
                pos = (f"最新价 {close:.2f} 已落到中枢下沿 ZD 之下,"
                       f"但离开中枢的那一笔尚未走完")
            else:
                pos = "价格仍在该中枢区间内震荡"
        elif cur and cur["direction"] == "down" and cur["end_price"] < z["zd"]:
            pos = "价格已跌破该中枢的下沿 ZD"
        elif cur and cur["direction"] == "up" and cur["end_price"] > z["zg"]:
            pos = "价格已升破该中枢的上沿 ZG"
        else:
            pos = "价格已离开该中枢区间"
        add(f"最近一个{lv}中枢是 [{z['zd']:.2f}, {z['zg']:.2f}]"
            f"(GG {z['gg']:.2f} / DD {z['dd']:.2f},由 {z['bi_count']} 笔构成"
            f"{',已延伸至上限、按第020课应视为升级' if z['is_extended'] else ''});{pos}。"
            f"第073课:获利机会只有中枢上移与中枢震荡两类。", "020/073")

    # 3) 当前笔
    if cur:
        add(f"最新一笔为{('向上' if cur['direction'] == 'up' else '向下')}"
            f"({cur['start_date']} {cur['start_price']:.2f} → "
            f"{cur['end_date']} {cur['end_price']:.2f})。"
            f"第106课:一笔至少延伸 6 个基本K线单位,5日线都碰不到的反弹不成笔。", "106")

    # 4) 背驰
    if divs:
        d = divs[-1]
        kind = "顶背驰" if d["kind"] == "top" else "底背驰"
        scope = "趋势背驰" if d["scope"] == "trend" else "盘整背驰"
        add(f"最近一次背驰是 {d['date']} 的{scope}({kind}):{d['note']}。"
            f"第024课:背驰后至少回到最后一个中枢。", d.get("lesson", "024"))
    else:
        add("在所取数据范围内未检出背驰信号;第037课「没有趋势,没有背驰」,"
            "没有形成趋势的震荡不产生趋势背驰。", "037")

    # 5) 三类买卖点
    if sigs:
        s = sigs[-1]
        add(f"最近一个买卖点是 {s['date']} 的{s['label']}({s['note']})。", s.get("lesson", "021"))
    else:
        add("数据范围内未出现三类买卖点。", "021")

    # 6) 均线分类
    if ma_cat.get("category"):
        add(f"第106课均线系统:{ma_cat['note']};"
            f"本次已攻克 {len(ma_cat.get('conquered') or [])} 条均线。", "106")

    # 7) 中阴阶段
    if zy.get("active"):
        add(f"中阴阶段:{zy['note']}", zy.get("lesson", "089"))

    # 8) 操作纪律
    add("操作纪律(第041课):买点买、卖点卖,30分钟级别进出、最小不低于5分钟;"
        "市场给三次改错机会,连错三次即停手。", "041")
    add("资金管理(第031课):成本降到 0 之前只补同量、绝不加码;"
        "中枢上移满仓、中枢震荡上减下增、三卖后不回补、背驰清仓(第049课)。", "031/049")

    return {
        "bullets": bullets,
        "lessons": sorted(lessons, key=lambda x: int(x)),
        "level": lv,
        "interval": interval,
    }

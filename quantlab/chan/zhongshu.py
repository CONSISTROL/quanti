"""缠论中枢、趋势、背驰、三类买卖点与中阴阶段。

课文出处:D:\\Code\\chzhshch-108-plus\\108\\  函数 docstring 标注课号。
本模块以**笔**作为中枢的次级别单位(笔中枢),这是缠论实战的通行做法,也是缠师
自己图解里画的那个级别。线段不参与中枢计算。
"""
from __future__ import annotations

MAX_ZHONGSHU_BI = 8  # 020/033:中枢自 3 段起、延伸不超过 5 段(共 8 段);再延伸即升级


def macd(closes, fast: int = 12, slow: int = 26, signal: int = 9):
    """标准 MACD(第024课用它辅助判断背驰)。柱值按 2*(DIF-DEA) 计。"""
    import pandas as pd
    s = pd.Series([float(c) for c in closes], dtype="float64")
    dif = s.ewm(span=fast, adjust=False).mean() - s.ewm(span=slow, adjust=False).mean()
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2.0
    return dif.tolist(), dea.tolist(), hist.tolist()


def _overlap(lo_a, hi_a, lo_b, hi_b) -> bool:
    return max(lo_a, lo_b) < min(hi_a, hi_b)


def find_zhongshus(bis: list[dict]) -> list[dict]:
    """中枢识别(第020课)。

    中枢由前三个连续次级别走势类型(此处为笔)的重叠部分确定。与中枢形成方向一致的
    笔称 Z走势段,`ZG=min(g1,g2)`、`ZD=max(d1,d2)` 取前两个 Z走势段的高/低点;
    `GG=max(gn)`、`DD=min(dn)` 遍历中枢内全部 Z走势段。

    延伸与结束用中心定理一:某笔与 [ZD,ZG] 有重叠即仍在延伸;**第一笔与中枢区间完全
    没有重叠的笔**标志着中枢结束,该笔即「离开中枢」的笔 —— 三类买卖点就出现在它的
    位置(第020课)。这个判据对向上、向下离开是对称的,不会像只检查同向笔那样在市场
    向反方向破位时把中枢无限拉长。

    第020/033课:中枢自 3 段起、延伸不超过 5 段,合共 8 笔;再延伸即升级,此处封顶
    并标记 is_extended。
    """
    zs: list[dict] = []
    n = len(bis)
    i = 0
    while i + 2 < n:
        a, b, c = bis[i], bis[i + 1], bis[i + 2]
        zdir = a["direction"]  # 第020课:回升形成的中枢,A、C 与中枢同向
        first_z = [a, c]
        zg = min(z["hi"] for z in first_z)
        zd = max(z["lo"] for z in first_z)
        if zd >= zg:
            i += 1
            continue
        j = i + 3
        while j < n and j - i < MAX_ZHONGSHU_BI:
            if not _overlap(bis[j]["lo"], bis[j]["hi"], zd, zg):
                break
            j += 1
        zsegs = [bis[k] for k in range(i, j) if bis[k]["direction"] == zdir]
        zs.append({
            "bi_from": i,
            "bi_to": j - 1,
            "bi_count": j - i,
            "direction": zdir,
            "zg": zg,
            "zd": zd,
            "gg": max(z["hi"] for z in zsegs),
            "dd": min(z["lo"] for z in zsegs),
            "z_count": len(zsegs),
            "is_extended": (j - i) >= MAX_ZHONGSHU_BI,
            "leave_bi": j if j < n else None,
            "start_bar": bis[i]["start_bar"],
            "end_bar": bis[j - 1]["end_bar"],
        })
        i = j
    return zs


def classify_trend(zs: list[dict]) -> dict:
    """走势类型(第017、020课)。

    盘整 = 恰含一个中枢;趋势 = 至少两个依次同向、波动**不重叠**的中枢。
    第020课中心定理二:`后DD > 前GG` 等价于上涨及延续;`后GG < 前DD` 等价于下跌及
    延续;其余情形(波动区间有重叠)则形成更大级别的中枢。
    """
    if not zs:
        return {"kind": "undefined", "label": "未成形", "note": "尚无中枢,走势类型未成形", "lesson": "017"}
    if len(zs) == 1:
        z = zs[-1]
        return {
            "kind": "consolidation", "label": "盘整", "zhongshu": z,
            "note": f"恰含一个中枢 [{z['zd']:.2f}, {z['zg']:.2f}],第017课:盘整即只有一个中枢的走势",
            "lesson": "017",
        }
    a, b = zs[-2], zs[-1]
    if b["dd"] > a["gg"]:
        kind, label = "uptrend", "上涨"
        note = f"后中枢 DD {b['dd']:.2f} > 前中枢 GG {a['gg']:.2f},第020课中心定理二判为上涨及延续"
    elif b["gg"] < a["dd"]:
        kind, label = "downtrend", "下跌"
        note = f"后中枢 GG {b['gg']:.2f} < 前中枢 DD {a['dd']:.2f},第020课中心定理二判为下跌及延续"
    else:
        kind, label = "consolidation", "盘整(中枢扩展)"
        note = ("前后两中枢的波动区间有重叠,第020课定理二:不构成趋势,"
                "而是形成更大级别的中枢")
    return {"kind": kind, "label": label, "zhongshu": b, "prev": a, "note": note, "lesson": "020"}


def _area(hist: list[float], lo_bar: int, hi_bar: int, sign: int) -> float:
    """区间内同色 MACD 柱面积(第024课用面积比较力度)。sign=1 取红柱、-1 取绿柱。"""
    seg = hist[max(0, lo_bar):hi_bar + 1]
    if sign > 0:
        return float(sum(v for v in seg if v > 0))
    return float(sum(-v for v in seg if v < 0))


def _nearest(bis: list[dict], start: int, step: int, direction: str, limit: int) -> int | None:
    """从 start 出发按 step 方向找最近的、方向为 direction 的笔(笔严格交替,通常一两步内命中)。"""
    k = start
    while 0 <= k < len(bis) and abs(k - start) <= limit:
        if bis[k]["direction"] == direction:
            return k
        k += step
    return None


def detect_divergence(bis: list[dict], hist: list[float], zs: list[dict]) -> list[dict]:
    """背驰判定(第024、027、037、043课)。

    趋势背驰:结构须为 a+A+b+B+c,A、B 同级别且构成趋势。b 段取 B 之前、与趋势同向
    的最后一笔,c 段取 B 之后、与趋势同向并在其上方(下方)创新高(新低)的第一笔;
    `area(c) < area(b)` 判为背驰。这实现了第024课「B 使 MACD 回拉0轴、area(C)<area(A)」
    的力度比较,以及第037课「没有趋势没有背驰」—— 两中枢不构成趋势时不产生趋势背驰。

    盘整背驰(第024、027课):单个中枢内比较第一段与第三段,第三段常破第一段极值。

    诚实声明:「0轴附近」「面积明显小于」课文未给出可量化标准,此处以严格小于为判据,
    并把双方面积与比值一并输出,供使用者自行判断力度差异,不假装原文给了阈值。
    """
    out: list[dict] = []

    for k in range(len(zs) - 1):
        A, B = zs[k], zs[k + 1]
        if B["dd"] > A["gg"]:
            trend, sign = "up", 1
        elif B["gg"] < A["dd"]:
            trend, sign = "down", -1
        else:
            continue
        b_i = _nearest(bis, B["bi_from"] - 1, -1, trend, 4)
        if b_i is None:
            continue
        # c 段:中枢 B 之后、与趋势同向且创新高/新低的笔
        c_i = None
        for m in range(B["bi_to"] + 1, len(bis)):
            if bis[m]["direction"] != trend:
                continue
            if trend == "up" and bis[m]["end_price"] > bis[b_i]["end_price"]:
                c_i = m
                break
            if trend == "down" and bis[m]["end_price"] < bis[b_i]["end_price"]:
                c_i = m
                break
            if m - B["bi_to"] > 4:
                break
        if c_i is None:
            continue
        b_bi, c_bi = bis[b_i], bis[c_i]
        area_b = _area(hist, b_bi["start_bar"], b_bi["end_bar"], sign)
        area_c = _area(hist, c_bi["start_bar"], c_bi["end_bar"], sign)
        # 两段都必须有同色柱才能比较。c 段整段落在零轴另一侧时面积为 0,
        # 那不是「力度衰竭」而是无可比性,否则会凭空判出背驰。
        if area_b <= 0 or area_c <= 0 or area_c >= area_b:
            continue
        bars_b = b_bi["end_bar"] - b_bi["start_bar"] + 1
        bars_c = c_bi["end_bar"] - c_bi["start_bar"] + 1
        # 面积会同时被「力度」和「时长」影响。面积比值很小时,长度差可能才是主因,
        # 故一并给出按单位K线归一化后的比值,便于判断背驰是否只是「c 段更短」。
        dens_b, dens_c = area_b / bars_b, area_c / bars_c
        out.append({
            "kind": "top" if trend == "up" else "bottom",
            "scope": "trend",
            "date": c_bi["end_date"], "price": c_bi["end_price"], "bar": c_bi["end_bar"],
            "bi_index": c_i,
            "area_prev": area_b, "area_curr": area_c, "ratio": area_c / area_b,
            "bars_prev": bars_b, "bars_curr": bars_c,
            "ratio_per_bar": dens_c / dens_b if dens_b > 0 else None,
            "note": (f"{'上涨' if trend == 'up' else '下跌'}趋势背驰:c 段面积 {area_c:.4g}"
                     f"({bars_c} 根K线) < b 段面积 {area_b:.4g}({bars_b} 根K线),"
                     f"比值 {area_c / area_b:.2f};按单位K线归一化后比值 "
                     f"{dens_c / dens_b:.2f};第024课力度比较,"
                     f"c 段已{'创新高' if trend == 'up' else '创新低'}"),
            "lesson": "024/037",
        })

    for z in zs:
        a_i, c_i = z["bi_from"], z["bi_from"] + 2
        if c_i >= len(bis):
            continue
        a_bi, c_bi = bis[a_i], bis[c_i]
        sign = 1 if z["direction"] == "up" else -1
        area_a = _area(hist, a_bi["start_bar"], a_bi["end_bar"], sign)
        area_c = _area(hist, c_bi["start_bar"], c_bi["end_bar"], sign)
        if area_a <= 0 or area_c <= 0 or area_c >= area_a:
            continue
        if z["direction"] == "up" and c_bi["end_price"] <= a_bi["end_price"]:
            continue
        if z["direction"] == "down" and c_bi["end_price"] >= a_bi["end_price"]:
            continue
        bars_a = a_bi["end_bar"] - a_bi["start_bar"] + 1
        bars_c = c_bi["end_bar"] - c_bi["start_bar"] + 1
        out.append({
            "kind": "top" if z["direction"] == "up" else "bottom",
            "scope": "consolidation",
            "date": c_bi["end_date"], "price": c_bi["end_price"], "bar": c_bi["end_bar"],
            "bi_index": c_i,
            "area_prev": area_a, "area_curr": area_c, "ratio": area_c / area_a,
            "bars_prev": bars_a, "bars_curr": bars_c,
            "ratio_per_bar": ((area_c / bars_c) / (area_a / bars_a)) if area_a > 0 else None,
            "note": (f"盘整背驰:中枢内第三段面积 {area_c:.4g} < 第一段面积 {area_a:.4g}"
                     f"(比值 {area_c / area_a:.2f},按单位K线归一化后 "
                     f"{(area_c / bars_c) / (area_a / bars_a):.2f});第024/027课"),
            "lesson": "024/027",
        })
    out.sort(key=lambda d: d["bar"])
    return out


def find_signals(bis: list[dict], zs: list[dict], divs: list[dict]) -> list[dict]:
    """三类买卖点(第020、021、053课)。

    一买 = 该级别的背驰点(第053课「第一类买卖点,就是该级别的背驰点」)。
    二买 = 一买后次级别向上再向下回调而**不创新低**的低点(第053课)。
    三买 = 向上离开中枢后以次级别回试、低点**不跌破 ZG**;三卖对称,高点不升破 ZD
           (第020课)。

    诚实声明:第020课原文肯定三买「必须是第一次」,第036课有网友提出相反说法而缠师
    未澄清;此处依第020课原文,只取第一次。
    """
    out: list[dict] = []

    for d in divs:
        if d["scope"] != "trend":
            continue
        if d["kind"] == "bottom":
            out.append({"date": d["date"], "price": d["price"], "bar": d["bar"],
                        "bi_index": d["bi_index"], "kind": "buy1", "label": "一买",
                        "note": "下跌趋势背驰点;第053课「第一类买卖点就是该级别的背驰点」",
                        "lesson": "053"})
        else:
            out.append({"date": d["date"], "price": d["price"], "bar": d["bar"],
                        "bi_index": d["bi_index"], "kind": "sell1", "label": "一卖",
                        "note": "上涨趋势背驰点;第053课", "lesson": "053"})

    for s in list(out):
        i = s["bi_index"]
        if i + 2 >= len(bis):
            continue
        first, pull = bis[i + 1], bis[i + 2]
        if s["kind"] == "buy1" and first["direction"] == "up" and pull["direction"] == "down":
            if pull["end_price"] > s["price"]:
                out.append({"date": pull["end_date"], "price": pull["end_price"], "bar": pull["end_bar"],
                            "bi_index": i + 2, "kind": "buy2", "label": "二买",
                            "note": ("一买后次级别向上、再向下回调未创新低;第053课"
                                     "「高点一次级别向下后一次级别向上,买点的情况反过来」"),
                            "lesson": "053"})
        if s["kind"] == "sell1" and first["direction"] == "down" and pull["direction"] == "up":
            if pull["end_price"] < s["price"]:
                out.append({"date": pull["end_date"], "price": pull["end_price"], "bar": pull["end_bar"],
                            "bi_index": i + 2, "kind": "sell2", "label": "二卖",
                            "note": "一卖后反弹未创新高;第053课", "lesson": "053"})

    # 三买/三卖:中枢结束后第一笔与中枢区间完全无重叠。该笔可能在区间**上方**或**下方**;
    # 若它本身已经是那次回试/回抽,信号就落在它身上;若它本身就是跳空式离开的那一笔,
    # 则回试/回抽在下一笔 —— 两种情形都要判,否则跳空离开会漏掉信号(第020课)。
    for z in zs:
        j = z["leave_bi"]
        if j is None:
            continue
        lv = bis[j]
        nxt = bis[j + 1] if j + 1 < len(bis) else None
        above = lv["lo"] >= z["zg"]
        below = lv["hi"] <= z["zd"]
        if above:
            if lv["direction"] == "down":
                out.append({"date": lv["end_date"], "price": lv["end_price"], "bar": lv["end_bar"],
                            "bi_index": j, "kind": "buy3", "label": "三买",
                            "note": (f"向上离开中枢(ZD {z['zd']:.2f} / ZG {z['zg']:.2f})后回试,"
                                     f"低点 {lv['lo']:.2f} 未跌破 ZG;第020课三买"),
                            "lesson": "020"})
            elif nxt is not None and nxt["direction"] == "down" and nxt["lo"] > z["zg"]:
                out.append({"date": nxt["end_date"], "price": nxt["end_price"], "bar": nxt["end_bar"],
                            "bi_index": j + 1, "kind": "buy3", "label": "三买",
                            "note": (f"向上跳空离开中枢(ZD {z['zd']:.2f} / ZG {z['zg']:.2f})后回试,"
                                     f"低点 {nxt['lo']:.2f} 未跌破 ZG;第020课三买"),
                            "lesson": "020"})
        elif below:
            if lv["direction"] == "up":
                out.append({"date": lv["end_date"], "price": lv["end_price"], "bar": lv["end_bar"],
                            "bi_index": j, "kind": "sell3", "label": "三卖",
                            "note": (f"向下离开中枢(ZD {z['zd']:.2f} / ZG {z['zg']:.2f})后回抽,"
                                     f"高点 {lv['hi']:.2f} 未升破 ZD;第020课三卖"),
                            "lesson": "020"})
            elif nxt is not None and nxt["direction"] == "up" and nxt["hi"] < z["zd"]:
                out.append({"date": nxt["end_date"], "price": nxt["end_price"], "bar": nxt["end_bar"],
                            "bi_index": j + 1, "kind": "sell3", "label": "三卖",
                            "note": (f"向下跳空离开中枢(ZD {z['zd']:.2f} / ZG {z['zg']:.2f})后回抽,"
                                     f"高点 {nxt['hi']:.2f} 未升破 ZD;第020课三卖"),
                            "lesson": "020"})
    out.sort(key=lambda s: s["bar"])
    return out


def zhongyin_state(divs: list[dict], higher_zhongshus: list[dict]) -> dict:
    """中阴阶段(第089课)。

    第089课:某级别背驰后,其后**必先形成高一级别的中枢**(1分钟背驰后必有5分钟
    中枢),该中枢必面临延伸或三类买卖点;这个 100% 成立的结构就是操作的依据。

    实现:低级别最近出现背驰,而该背驰之后高级别尚未形成新中枢 → 处于中阴阶段。
    跨级别只按日期比较(两个级别的笔/K线下标不可通约)。
    """
    if not divs:
        return {"active": False, "note": "无背驰,未进入中阴阶段", "lesson": "089"}
    last = divs[-1]
    # 第089课要的是「其后必先出现一个高一级别的中枢」,即一个**新**中枢;一个在背驰之前
    # 就已开始、只是延续到背驰之后的中枢并不满足,不能据此认定中阴阶段已结束。
    after = [z for z in higher_zhongshus
             if (z.get("start_date") or "") >= last["date"] and (z.get("end_date") or "") > last["date"]]
    if after:
        z = after[0]
        return {
            "active": False,
            "higher_zhongshu": {"zd": z["zd"], "zg": z["zg"],
                                "start_date": z.get("start_date"), "end_date": z.get("end_date")},
            "note": (f"{last['date']} 低级别背驰后,高一级别中枢 "
                     f"[{z['zd']:.2f}, {z['zg']:.2f}] 已形成"
                     f"({z.get('start_date')} ~ {z.get('end_date')});"
                     "第089课的中阴阶段已由该中枢接续"),
            "lesson": "089",
        }
    return {
        "active": True,
        "from_date": last["date"],
        "note": (f"{last['date']} 出现背驰;按第089课「其后必先形成高一级别的中枢」,"
                 "而高一级别的新中枢尚未走完 —— 处于中阴阶段。该中枢最终要么延伸、"
                 "要么以三类买卖点结束,这是第089课给出的 100% 操作依据"),
        "lesson": "089",
    }

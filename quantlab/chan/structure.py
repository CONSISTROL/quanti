"""缠论结构层:包含关系处理 → 分型 → 笔 → 线段。

所有判别标准均来自《教你炒股票》原文,函数 docstring 标注课号。
课文出处:D:\\Code\\chzhshch-108-plus\\108\\

MIN_BI_GAP 说明:第077课的字面标准要求「顶和底之间至少有一个K线不属于顶分型与底
分型」。顶分型占其中心K线前后各一根,故两分型中心K线在**合并**K线上至少相隔 4 根,
中间那根才不属于任何一个分型。而第106课说「一笔必须至少延伸 6 个基本K线单位」,
按跨度算对应相隔 3 根。两处对最短笔的要求相差一根K线,原文未给出统一口径;这里取
第077课 —— 它是专门、且更晚给出的笔划分算法,并明确把「不属于分型」写进了条件。
"""
from __future__ import annotations

MIN_BI_GAP = 4  # 合并K线上两分型中心的最小间隔,见模块 docstring


class Bar:
    """合并后的K线。

    hi/lo 为合并后的区间;hi_bar/lo_bar 分别是取到该最高/最低价的**原始K线下标**。
    极值可能落在合并组内的任意一根上,而 bar_idx(组的最后一根)只用于回查区间边界。
    """

    __slots__ = ("bar_idx", "hi", "lo", "hi_bar", "lo_bar")

    def __init__(self, bar_idx: int, hi: float, lo: float, hi_bar: int = 0, lo_bar: int = 0):
        self.bar_idx = bar_idx
        self.hi = hi
        self.lo = lo
        self.hi_bar = hi_bar
        self.lo_bar = lo_bar


class Fractal:
    """分型。

    m_idx 为合并后的K线下标 —— 第077课「顶底之间至少一根独立K线」要在合并K线上
    度量,用原始K线下标会恒不成立。bar_idx 为该分型**极值实际发生**的那根原始K线,
    用于回查日期,使笔的顶点精确落在极值K线上(极值未必在合并组的最后一根上)。
    """

    __slots__ = ("m_idx", "bar_idx", "kind", "price", "hi", "lo")

    def __init__(self, m_idx: int, bar_idx: int, kind: str, price: float, hi: float, lo: float):
        self.m_idx = m_idx
        self.bar_idx = bar_idx
        self.kind = kind
        self.price = price
        self.hi = hi
        self.lo = lo


def merge_inclusive(highs, lows) -> list[Bar]:
    """包含关系合并(第062、065课)。

    一K线的高低点全在另一K线范围内即为包含。合并规则:向上(gn >= gn-1)取两K
    高点的高者与两K低点的较高者;向下(dn <= dn-1)取两K低点的低者与两K高点的
    较低者。必须**顺序合成** —— 用新合并出的K线继续与下一根比较(第065课)。

    第065课未给出「首组包含关系的方向来源」的可量化标准,此处按向上处理并在
    结果中保留 bar_idx 以便核对。
    """
    n = len(highs)
    if n == 0:
        return []
    bars = [Bar(0, float(highs[0]), float(lows[0]), 0, 0)]
    direction = None
    for i in range(1, n):
        h, l = float(highs[i]), float(lows[i])
        prev = bars[-1]
        included = (h <= prev.hi and l >= prev.lo) or (h >= prev.hi and l <= prev.lo)
        if not included:
            bars.append(Bar(i, h, l, i, i))
            continue
        if len(bars) >= 2:
            direction = "up" if bars[-1].hi >= bars[-2].hi else "down"
        elif direction is None:
            direction = "up"
        if direction == "up":
            if h >= prev.hi:
                prev.hi, prev.hi_bar = h, i
            if l >= prev.lo:
                prev.lo, prev.lo_bar = l, i
        else:
            if h <= prev.hi:
                prev.hi, prev.hi_bar = h, i
            if l <= prev.lo:
                prev.lo, prev.lo_bar = l, i
        prev.bar_idx = i
    return bars


def find_fractals(bars: list[Bar]) -> list[Fractal]:
    """分型识别(第062课)。

    顶分型:相邻三根(已无包含关系的)K线中,第二根的高点是三根中最高,且其低点
    也是三根中最高;底分型反之。这里只产出候选分型,**相邻分型不得共用K线的
    结合律**留到 build_bis 中与笔的两个条件一并校验(第077课)。
    """
    out: list[Fractal] = []
    for i in range(1, len(bars) - 1):
        a, b, c = bars[i - 1], bars[i], bars[i + 1]
        if b.hi > a.hi and b.hi > c.hi and b.lo > a.lo and b.lo > c.lo:
            # 端点日期取极值实际发生的那根原始K线,顶点才会正好落在极值K线上
            out.append(Fractal(i, b.hi_bar, "top", b.hi, b.hi, b.lo))
        elif b.lo < a.lo and b.lo < c.lo and b.hi < a.hi and b.hi < c.hi:
            out.append(Fractal(i, b.lo_bar, "bottom", b.lo, b.hi, b.lo))
    return out


def _bi_conditions(a: Fractal, b: Fractal) -> bool:
    """第077课笔的两个条件(a、b 为一顶一底的两个分型)。

    条件一:顶和底之间至少有一个K线不属于顶分型与底分型。顶分型占其中心K线前后
    各一根、共三根,底分型同理;故两分型中心K线在合并K线上至少相隔 MIN_BI_GAP 根,
    中间那根才不属于任何一个分型。这是笔的主要约束。

    条件二:顶分型中最高那K线的区间至少要有一部分高于底分型中最低那K线的区间,即
    顶K线的高 > 底K线的低。第077课把不满足的情形描述为「顶都在低的范围内或顶比底
    还低」,并称这条是「还有一个最显然的」—— 它是一句几乎恒成立的合理性检查,不是
    主过滤器;不要在这里额外加重条件,否则会大面积吃掉本应成立的笔。
    """
    if a.kind == b.kind:
        return False
    first, second = (a, b) if a.m_idx <= b.m_idx else (b, a)
    top, bottom = (first, second) if first.kind == "top" else (second, first)
    # 间隔要在合并K线上取两个分型的先后距离:向上笔是底在前、顶在后。
    if second.m_idx - first.m_idx < MIN_BI_GAP:
        return False
    return top.hi > bottom.lo


def build_bis(fractals: list[Fractal]) -> list[dict]:
    """笔的划分(第077课三步唯一算法)。

    一、确定所有符合标准的分型。
    二、前后两分型同性质时,顶留高者、底留低者(相等先保留)。
    三、相邻顶底连成一笔;连续同性质时取**最先一个**连向新出现的反向分型。

    第 2 步必须**回头修正上一笔的端点**,而不是只比较相邻分型:第077课的原话是
    「如果前面的底高于后面的底,那么前面的划分显然是错误的,因为按这种划分,该笔是
    没有完成的」—— 在出现有效顶之前先出了更低的底,说明那一笔还没走完。若不做这个
    修正,锚点会停在旧的极值上、后续同性质分型全被当成「取最先一个」跳过,笔的划分
    会在中途彻底停住(实测会把一年的行情整段丢掉)。

    修正上一笔的端点不会破坏笔首尾相接:被修正的端点同时也是下一笔的起点。
    """
    bis: list[dict] = []
    anchor: Fractal | None = None
    for fx in fractals:
        if anchor is None:
            anchor = fx
            continue
        if fx.kind == anchor.kind:
            more_extreme = (fx.hi > anchor.hi) if fx.kind == "top" else (fx.lo < anchor.lo)
            if more_extreme:
                if (bis and bis[-1]["end_bar"] == anchor.bar_idx
                        and bis[-1]["end_fx"] == anchor.kind):
                    prev = bis[-1]
                    prev["end_bar"] = fx.bar_idx
                    prev["end_price"] = fx.price
                    prev["lo"] = min(prev["start_price"], fx.price)
                    prev["hi"] = max(prev["start_price"], fx.price)
                anchor = fx
            continue
        if not _bi_conditions(anchor, fx):
            continue
        start, end = anchor, fx
        bis.append({
            "start_bar": start.bar_idx,
            "end_bar": end.bar_idx,
            "start_price": start.price,
            "end_price": end.price,
            "direction": "up" if start.kind == "bottom" else "down",
            "start_fx": start.kind,
            "end_fx": end.kind,
            "lo": min(start.price, end.price),
            "hi": max(start.price, end.price),
            "start_date": None,
            "end_date": None,
        })
        anchor = fx
    return bis


def _overlap(lo_a: float, hi_a: float, lo_b: float, hi_b: float) -> bool:
    return max(lo_a, lo_b) < min(hi_a, hi_b)


def _merge_fx_seq(elems: list[list[float]], direction: str) -> list[list[float]]:
    """对特征序列做包含处理(第067课)。第一种情况禁做、第二种情况必做,见 build_segments。"""
    out: list[list[float]] = []
    for el in elems:
        if not out:
            out.append(list(el))
            continue
        ph, pl = out[-1][1], out[-1][0]
        h, l = el[1], el[0]
        if (h <= ph and l >= pl) or (h >= ph and l <= pl):
            if direction == "up":
                out[-1] = [max(pl, l), max(ph, h)]
            else:
                out[-1] = [min(pl, l), min(ph, h)]
        else:
            out.append(list(el))
    return out


def _fx_top(elems: list[list[float]]) -> int | None:
    """特征序列里的顶分型(第067课:向上线段只考察顶分型)。返回中心元素下标。"""
    for i in range(1, len(elems) - 1):
        if elems[i][1] > elems[i - 1][1] and elems[i][1] > elems[i + 1][1]:
            return i
    return None


def _fx_bottom(elems: list[list[float]]) -> int | None:
    """特征序列里的底分型(第067课:向下线段只考察底分型)。"""
    for i in range(1, len(elems) - 1):
        if elems[i][0] < elems[i - 1][0] and elems[i][0] < elems[i + 1][0]:
            return i
    return None


def _segment_end(bis: list[dict], start: int, direction: str) -> int | None:
    """找线段终点,返回**下一线段起始笔**的下标;None 表示线段尚未被破坏。

    特征序列:向上线段取向下笔序列、只考察顶分型(第067课)。特征序列出现顶分型,
    说明该反向笔的**起点**(即前一笔的终点)是线段的最高点,线段至此结束,下一线段
    从该反向笔开始。

    第一种情况:第1、2元素间无缺口,该分型即线段终点。
    第二种情况:有缺口,须从该极值点起的笔序列的特征序列出现反向分型才确认
    (第067、071课),且第071课要求第二种情况必须严格做包含处理。
    """
    want = "down" if direction == "up" else "up"
    idxs = [i for i in range(start, len(bis)) if bis[i]["direction"] == want]
    if len(idxs) < 3:
        return None
    raw = [[bis[i]["lo"], bis[i]["hi"]] for i in idxs]
    finder = _fx_top if direction == "up" else _fx_bottom
    merged = _merge_fx_seq(raw, direction)
    pos = finder(merged)
    if pos is None:
        return None
    gap = pos >= 2 and not _overlap(merged[pos - 1][0], merged[pos - 1][1],
                                   merged[pos - 2][0], merged[pos - 2][1])
    if gap:
        tail_start = idxs[pos] + 2
        tail = [i for i in range(tail_start, len(bis)) if bis[i]["direction"] == direction]
        if len(tail) < 3:
            return None
        tail_elems = [[bis[i]["lo"], bis[i]["hi"]] for i in tail]
        tail_merged = _merge_fx_seq(tail_elems, want)
        rev = _fx_bottom(tail_merged) if direction == "up" else _fx_top(tail_merged)
        if rev is None:
            return None
    next_start = idxs[pos]
    return next_start if next_start > start else None


def build_segments(bis: list[dict]) -> list[dict]:
    """线段划分(第062、065、067、071、078课)。

    线段至少三笔且**前三笔必须有重合**;含笔数为单数,不同于笔可以从顶到顶或底到
    底;线段必须被反向线段破坏才能确定其完成(第062、065课),破坏的判别用特征序列
    分型(第067课)。

    诚实声明:第067课的第二种情况(特征序列有缺口)依赖对后续特征序列的递归判断,
    课文对边界情形的描述本身留有解释空间;线段仅用于描述更大级别结构、**不参与中枢
    计算**,因此不影响本页的核心结论。
    """
    segs: list[dict] = []
    n = len(bis)
    if n < 3:
        return segs
    s = 0
    while s + 2 < n:
        a, b, c = bis[s], bis[s + 1], bis[s + 2]
        if max(a["lo"], b["lo"], c["lo"]) >= min(a["hi"], b["hi"], c["hi"]):
            s += 1
            continue
        direction = a["direction"]
        next_s = _segment_end(bis, s, direction)
        if next_s is None:
            break
        count = next_s - s
        if count < 3 or count % 2 == 0:
            break
        segs.append({
            "start_bi": s,
            "end_bi": next_s - 1,
            "start_bar": bis[s]["start_bar"],
            "end_bar": bis[next_s - 1]["end_bar"],
            "start_price": bis[s]["start_price"],
            "end_price": bis[next_s - 1]["end_price"],
            "direction": direction,
            "bi_count": count,
        })
        s = next_s
    return segs

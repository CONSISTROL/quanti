"""缠论买卖点驱动的交易模拟。

## 为什么必须逐根重算

缠论的结构全是"回头才能确认"的:一个分型要等它后面那根K线,一笔要等反向分型,中枢
要等离开的那一笔,背驰要等中枢走完。所以**直接用整段历史算出来的信号去回测,含严重
的未来函数** —— 等于在最低点买入、最高点卖出,收益会被系统性高估。

这里按K线逐根推进:第 t 根**收盘后**只用 [0..t] 的数据重算一次结构,那一刻才出现的
信号记在 t 这根上(此前不可知),按约定在第 t+1 根的**开盘**成交。信号是稀疏事件
(上证日线1200根约10个),逐根重算约 3s,所以结果做了缓存。

## 规则出处

- 买点买、卖点卖:第041课;买点后持股至卖点:第045课
- 背驰清仓:第049课「中枢上移满仓,中枢震荡上减下增,三卖后不回补,背驰清仓」
- 三个赢利买卖点的完备性:第021课(赢利买卖点只有一、二、三类)
- 三买最安全:第053课「一个上涨的股票,如果是日线级别的,最晚就是在第三类买点介入」
"""
from __future__ import annotations

LEAN_MIN_BARS = 120   # 结构分析需要的最少K线(默认,日线口径)

# 逐根推进要求先有一段历史才能析出笔/中枢。这个预热长度必须按级别给:统一用 120 根
# 对月线等于要求 10 年历史,会把一大段可交易的行情白白丢掉。
MIN_BARS_BY_LEVEL = {"1d": 120, "1w": 90, "1M": 60}

# 买点/卖点分类
BUY_KINDS = ("buy1", "buy2", "buy3")
SELL_KINDS = ("sell1", "sell2", "sell3")

# 绩效函数按「年化 = (1+总收益)^(252/交易日数) - 1」计算,夏普按 sqrt(252) 年化,
# 所以喂给它的必须是**交易日数**与**每年的周期数**,不能是K线根数。
PERIODS_PER_YEAR = {"1d": 252, "1w": 52, "1M": 12}


def trading_days_of(dates: list[str], interval: str) -> int:
    """把曲线长度换算成交易日数。

    日线的每根K线就是一个交易日,直接用根数最准;周线/月线按**实际日历跨度**换算 ——
    月线 342 根是 28 年,当成 342 个交易日年化会把结果放大 25 倍以上。
    """
    import datetime as dt

    if interval == "1d" or len(dates) < 2:
        return max(1, len(dates))
    try:
        d0 = dt.datetime.strptime(dates[0], "%Y-%m-%d")
        d1 = dt.datetime.strptime(dates[-1], "%Y-%m-%d")
        return max(1, round((d1 - d0).days / 365.25 * 252))
    except Exception:
        factor = 5 if interval == "1w" else 21
        return max(1, len(dates) * factor)


def _sharpe(curve: list, periods_per_year: int) -> float:
    """按曲线自身的周期数年化夏普。

    vnpy_quanti.metrics 里那份固定用 sqrt(252),对月线曲线会高估 4.6 倍
    (sqrt(252/12)),所以这里自己算一遍覆盖掉。
    """
    import numpy as np

    vals = [float(v) for _, v in curve]
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals)) if vals[i - 1] > 0]
    if len(rets) < 2:
        return 0.0
    sd = float(np.std(rets))
    if sd <= 0:
        return 0.0
    return float(np.mean(rets) / sd * np.sqrt(periods_per_year))


def _lean(sub):
    """只算回测需要的部分:分型→笔→中枢→背驰→买卖点 + 当前走势类型。

    刻意不算 8 条均线(1200 根上占了大头),单次从 40ms 降到 5ms。
    """
    from . import structure, zhongshu

    dates = [str(x)[:10] for x in sub["date"]]
    bars = structure.merge_inclusive(sub["high"].values, sub["low"].values)
    bis = structure.build_bis(structure.find_fractals(bars))
    for b in bis:
        b["start_date"] = dates[b["start_bar"]]
        b["end_date"] = dates[b["end_bar"]]
    _, _, hist = zhongshu.macd(sub["close"].values)
    zs = zhongshu.find_zhongshus(bis)
    divs = zhongshu.detect_divergence(bis, hist, zs)
    sigs = zhongshu.find_signals(bis, zs, divs)
    trend = zhongshu.classify_trend(zs)
    return sigs, divs, trend, len(zs)


def walk_forward_events(df, interval: str = "1d", min_bars: int | None = None) -> list[dict]:
    """逐根推进,返回**事件流**。每个事件带首次可判定与执行的那根K线。

    新出现的信号/背驰才会成为事件;被后续K线修正掉的信号不做处理 —— 它在出现的那
    一刻确实是可判定的,真实交易者当时就会按它下单,不能因为事后被改写就当它没发生。
    """
    if min_bars is None:
        min_bars = MIN_BARS_BY_LEVEL.get(interval, LEAN_MIN_BARS)
    n = len(df)
    if n <= min_bars:
        return []
    dates = [str(x)[:10] for x in df["date"]]
    opens = [float(v) for v in df["open"].values]
    seen: set = set()
    events: list[dict] = []
    for t in range(min_bars - 1, n):
        try:
            sigs, divs, trend, zcount = _lean(df.iloc[:t + 1])
        except Exception:
            continue
        exec_index = min(t + 1, n - 1)
        for s in sigs:
            key = ("sig", s["kind"], s["date"])
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "kind": s["kind"], "label": s["label"], "signal_date": s["date"],
                "signal_price": s["price"], "note": s["note"], "lesson": s["lesson"],
                "index": t, "exec_index": exec_index,
                "exec_date": dates[exec_index], "exec_price": opens[exec_index],
                # 信号标在极值点上,而那时它还没被确认。这里把间隔拆成两段:
                # confirm_lag 是「极值那根 -> 结构上能被判定为分型/笔端点」用的根数,
                # 它是第062课分型定义(三根K线)的必然结果,不是额外规则;
                # 再加 1 根是按约定在次日开盘成交。
                "signal_index": s["bar"],
                "confirm_lag": t - s["bar"],
                "lag_bars": exec_index - s["bar"],
                "trend_kind": trend["kind"], "zhongshu_count": zcount,
            })
        for d in divs:
            if d["kind"] != "top":
                continue          # 第049课说的是背驰清仓(顶部);底背驰的买点已由一买覆盖
            key = ("div", d["date"])
            if key in seen:
                continue
            seen.add(key)
            events.append({
                "kind": "clear_top_div", "label": "顶背驰清仓", "signal_date": d["date"],
                "signal_price": d["price"], "note": d["note"], "lesson": d["lesson"],
                "index": t, "exec_index": exec_index,
                "exec_date": dates[exec_index], "exec_price": opens[exec_index],
                "signal_index": d.get("bar"),
                "confirm_lag": t - d.get("bar", t),
                "lag_bars": exec_index - d.get("bar", exec_index),
                "trend_kind": trend["kind"], "zhongshu_count": zcount,
            })
    events.sort(key=lambda e: (e["exec_index"], e["index"]))
    return events


def _position_fraction(ev, mode: str) -> float:
    """建仓比例。

    full    —— 每次满仓(与 config.json 的 full_position 一致)
    rule049 —— 第049课「中枢上移满仓,中枢震荡上减下增」:建仓时是上涨趋势就满仓,
                否则(盘整/下跌)半仓
    """
    if mode != "rule049":
        return 1.0
    return 1.0 if ev.get("trend_kind") == "uptrend" else 0.5


def simulate(df, events, mode: str, capital: float = 100000.0,
             start_index: int = 0) -> dict:
    """按事件流模拟成交。

    成交价用事件 exec_index 那根的开盘价;净值按每日收盘价逐根记录,这样和基准
    (买入持有)可以严格放在同一时间轴上比较。不含手续费与滑点。
    """
    n = len(df)
    dates = [str(x)[:10] for x in df["date"]]
    opens = [float(v) for v in df["open"].values]
    closes = [float(v) for v in df["close"].values]

    by_exec: dict[int, list[dict]] = {}
    for ev in events:
        by_exec.setdefault(ev["exec_index"], []).append(ev)

    cash = float(capital)
    shares = 0.0
    cost_basis = 0.0
    trades: list[dict] = []
    curve: list[list] = []
    no_rebuy = False          # rule049:三卖后不回补,等一买/三买再说
    skipped: list[dict] = []
    hold_days = 0             # 有持仓的交易日数,用来报告仓位暴露度

    for i in range(n):
        for ev in by_exec.get(i, []):
            price = opens[i]
            if price <= 0:
                continue
            kind = ev["kind"]
            if kind in BUY_KINDS:
                if shares > 0:
                    continue          # 已持仓,不叠加(第031课:成本0前只补同量,这里按单笔持仓)
                frac = _position_fraction(ev, mode)
                if mode == "rule049" and no_rebuy and kind == "buy2":
                    skipped.append({**ev, "skip_reason": "第049课:三卖后不回补,跳过二买"})
                    continue
                amount = cash * frac
                if amount <= 0:
                    continue
                vol = amount / price
                trades.append({
                    "date": ev["exec_date"], "direction": "BUY", "price": round(price, 4),
                    "volume": round(vol, 4), "amount": round(amount, 2),
                    "kind": kind, "label": ev["label"],
                    "signal_date": ev["signal_date"], "signal_price": ev["signal_price"],
                    "fraction": frac, "trend_kind": ev.get("trend_kind"),
                    "lag_bars": ev.get("lag_bars"), "confirm_lag": ev.get("confirm_lag"),
                    "reason": ev["note"], "lesson": ev["lesson"],
                    "pnl_pct": None,
                })
                cost_basis = price
                shares = vol
                cash -= amount
                no_rebuy = False
            else:                      # sell1/2/3 或 顶背驰清仓
                if shares <= 0:
                    continue
                amount = shares * price
                trades.append({
                    "date": ev["exec_date"], "direction": "SELL", "price": round(price, 4),
                    "volume": round(shares, 4), "amount": round(amount, 2),
                    "kind": kind, "label": ev["label"],
                    "signal_date": ev["signal_date"], "signal_price": ev["signal_price"],
                    "fraction": None, "trend_kind": ev.get("trend_kind"),
                    "lag_bars": ev.get("lag_bars"), "confirm_lag": ev.get("confirm_lag"),
                    "reason": ev["note"], "lesson": ev["lesson"],
                    "pnl_pct": round(price / cost_basis - 1, 6) if cost_basis > 0 else None,
                })
                cash += amount
                shares = 0.0
                if mode == "rule049" and kind == "sell3":
                    no_rebuy = True
        if i >= start_index:
            if shares > 0:
                hold_days += 1
            curve.append([dates[i], round(cash + shares * closes[i], 2)])

    return {
        "mode": mode, "curve": curve, "trades": trades, "skipped": skipped,
        "final_value": round(cash + shares * closes[-1], 2),
        "open_position": shares > 0,
        # 仓位暴露度:策略大部分时间空仓时,单看总收益和"满仓拿到底"的基准比是不公平的
        "exposure": round(hold_days / max(1, len(curve)), 4),
    }


def benchmark(df, capital: float = 100000.0, start_index: int = 0) -> dict:
    """买入持有基准:在 start_index 那根的开盘价一次性买入并持有到结束。

    刻意不补一笔虚拟的期末卖出 —— 那样会凭空造出一个"交易"并影响胜率口径;基准只
    报告由净值曲线算得出的指标(收益/年化/回撤/夏普),胜率类指标留空。
    """
    n = len(df)
    dates = [str(x)[:10] for x in df["date"]]
    opens = [float(v) for v in df["open"].values]
    closes = [float(v) for v in df["close"].values]
    entry = opens[start_index]
    vol = capital / entry if entry > 0 else 0.0
    curve = [[dates[i], round(vol * closes[i], 2)] for i in range(start_index, n)]
    return {"mode": "benchmark", "curve": curve, "trades": [], "skipped": [],
            "final_value": round(vol * closes[-1], 2), "open_position": True,
            "exposure": 1.0}


def run_all(df, interval: str = "1d", capital: float = 100000.0) -> dict:
    """两种仓位策略 + 买入持有基准,三者放在同一时间轴上比较。"""
    from vnpy_quanti.metrics import calc_legacy_style_stats

    events = walk_forward_events(df, interval=interval)
    if not events:
        raise ValueError("K线太短或没有出现任何缠论买卖点,无法模拟")
    start_index = min(e["exec_index"] for e in events)
    # 基准也从同一根开始,保证三条曲线覆盖完全相同的区间
    start_index = max(0, start_index - 1)

    ppy = PERIODS_PER_YEAR.get(interval, 252)

    def _stats(curve, trades):
        td = trading_days_of([d for d, _ in curve], interval)
        st = calc_legacy_style_stats(curve, capital, td, trades)
        if st:
            st["sharpe"] = _sharpe(curve, ppy)
        return st

    strategies = []
    for mode, name in (("full", "三类买卖点 · 每次满仓"),
                       ("rule049", "三类买卖点 · 第049课分仓")):
        r = simulate(df, events, mode, capital=capital, start_index=start_index)
        strategies.append({
            "key": mode, "name": name,
            "curve": r["curve"], "trades": r["trades"], "skipped": r["skipped"],
            "stats": _stats(r["curve"], r["trades"]),
            "open_position": r["open_position"], "exposure": r["exposure"],
        })

    bm = benchmark(df, capital=capital, start_index=start_index)
    bm_curve = bm["curve"]
    bm_stats = _stats(bm_curve, [])
    bm_stats.update({"win_rate": None, "profit_loss_ratio": None,
                     "total_trades": None, "avg_win": None, "avg_loss": None})
    bars = len(bm_curve)
    tdays = trading_days_of([d for d, _ in bm_curve], interval)

    return {
        "capital": capital,
        "interval": interval,
        "min_bars": MIN_BARS_BY_LEVEL.get(interval, LEAN_MIN_BARS),
        "events": len(events),
        "start_date": bm_curve[0][0] if bm_curve else None,
        "end_date": bm_curve[-1][0] if bm_curve else None,
        "bars": bars,
        "trading_days": tdays,
        "years": round(tdays / 252, 2),
        "strategies": strategies,
        "benchmark": {"key": "benchmark", "name": "买入持有(基准)",
                      "curve": bm_curve, "stats": bm_stats, "trades": [],
                      "exposure": 1.0},
        "rules": {
            "signals": "一买/二买/三买买入,一卖/二卖/三卖卖出;另按第049课「背驰清仓」,"
                       "出现顶背驰亦清仓",
            "execution": (f"严格逐根重算:第 t 根收盘后才用 [0..t] 的数据重算结构,那一刻才出现的"
                          f"信号在 t+1 开盘价成交,消除未来函数。预热 {MIN_BARS_BY_LEVEL.get(interval, LEAN_MIN_BARS)} 根"
                          f"K线才开始(结构需要足够历史才能析出笔与中枢)"),
            "annualize": (f"年化按真实交易日数折算(本区间 {tdays} 个交易日 ≈ {round(tdays/252, 2)} 年),"
                          f"夏普按每年 {ppy} 个周期年化 —— 周线、月线不能用K线根数当交易日数"),
            "lag_note": ("交易记录里的「结构确认 N 根」不是原文的规则,原文没有规定任何成交时点。"
                         "它由两部分组成:①结构确认的根数 —— 这是第062课分型定义(三根K线)"
                         "的必然结果,极值那根要等后续K线走完才能被判定为分型/笔端点,"
                         "多数情况只要 1 根,但行情持续创新极值时可能拖到几十根;"
                         "②次日开盘 1 根 —— 纯粹是本页约定的成交方式"),
            "strategy_a": "每次满仓(与 config.json 的 full_position 一致)",
            "strategy_b": ("第049课「中枢上移满仓,中枢震荡上减下增,三卖后不回补」:"
                           "建仓时为上涨趋势满仓、盘整则半仓;三卖清仓后跳过紧接着的二买"),
            "reading_note": ("第049课原文只说「三卖后不回补」,没有给出回补的判定条件;"
                             "这里的操作化解读是「三卖后的下一个二买跳过,等一买或三买再介入」"
                             "——这是实现口径,不是原文原话"),
            "cost": "不含手续费与滑点;按份额连续计算,不按整手取整",
            "lesson": "021/041/045/049/053",
        },
    }

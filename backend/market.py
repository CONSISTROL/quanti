"""指数数据通路 —— 「缠论看盘」专用。

现有的 quantlab/data_sources 按代码首位数字推断沪深市场(000001 会被当作平安银行),
不适用于指数;且指数无需复权,因此这里单独走一条通路,把腾讯符号显式写死,不去改动
选股/回测那条已有的符号映射链。
"""
from __future__ import annotations

import re

import pandas as pd
import requests

# 宽基指数注册表。code 为页面/接口使用的 6 位代码,sym 为腾讯行情符号。
INDICES = (
    {"code": "000001", "sym": "sh000001", "name": "上证指数"},
    {"code": "399001", "sym": "sz399001", "name": "深证成指"},
    {"code": "399006", "sym": "sz399006", "name": "创业板指"},
    {"code": "000688", "sym": "sh000688", "name": "科创50"},
    {"code": "000300", "sym": "sh000300", "name": "沪深300"},
    {"code": "000905", "sym": "sh000905", "name": "中证500"},
    {"code": "000016", "sym": "sh000016", "name": "上证50"},
)

_BY_CODE = {i["code"]: i for i in INDICES}

PERIOD = {"1d": "day", "1w": "week", "1M": "month"}
MAX_BARS = {"1d": 1200, "1w": 800, "1M": 500}
# 日线盘中会变,缓存短一些;周/月线一天一换足够。
TTL = {"1d": 1800, "1w": 21600, "1M": 21600}

_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def get_index(code: str) -> dict:
    """按 6 位代码取指数元信息。"""
    item = _BY_CODE.get(str(code).zfill(6))
    if item is None:
        known = "、".join(f"{i['code']}({i['name']})" for i in INDICES)
        raise ValueError(f"未知指数代码 {code},可用:{known}")
    return item


def list_indices() -> list[dict]:
    return [dict(i) for i in INDICES]


def is_index(code: str) -> bool:
    return str(code).zfill(6) in _BY_CODE


def resolve(code: str) -> tuple[str, dict]:
    """判断代码是指数还是个股/ETF,返回 (kind, 元信息)。

    指数代码与个股代码会撞车(000001 既可以是上证指数、也可以是平安银行),所以指数
    必须在注册表里显式命中;命中不了的一律按个股处理,走项目已有的前复权通路。
    """
    pure = str(code).zfill(6)
    if pure in _BY_CODE:
        return "index", dict(_BY_CODE[pure])
    if not re.fullmatch(r"\d{6}", pure):
        raise ValueError(f"代码必须是 6 位数字:{code}")
    return "stock", {"code": pure, "name": _stock_name_cached(pure), "sym": None}


_NAME_TTL = 21600  # 名称基本不变,缓存 6 小时;否则每个个股请求都要多一次行情往返
_name_cache: dict = {}


def _stock_name_cached(code: str) -> str:
    import time
    hit = _name_cache.get(code)
    now = time.time()
    if hit and now - hit[0] < _NAME_TTL:
        return hit[1]
    try:
        from backend.stock_name import get_stock_name
        raw = get_stock_name(code).get("name") or code
        # 行情接口的名称会带当日除权除息标记(如 XD中国石油),那是一天的临时状态,
        # 不该当成标的的名字显示。ST/*ST 是真实风险标记,保留。
        name = re.sub(r"^(XD|XR|DR)", "", raw).strip() or raw
    except Exception:
        # 取不到就退化成用代码显示,而且**不缓存**,下次还能重试
        return code
    _name_cache[code] = (now, name)
    return name


def fetch_stock_df(code: str, interval: str = "1d", refresh: bool = False) -> pd.DataFrame:
    """个股/ETF 的K线,复用已有的 QuantDash 前复权通路。

    除权跳空如果没复权,会被当成真实的K线缺口和分型,所以这里必须走前复权;
    日线以上用项目既有的 INTERVAL_RULES 重采样,不另起一套。
    """
    from backend.kline import INTERVAL_RULES, _fetch_daily_df, _resample_ohlcv

    cfg = _load_data_cfg()
    rule, _ = INTERVAL_RULES.get(interval, (None, "日K"))
    df = _fetch_daily_df(str(code).zfill(6), cfg, refresh=refresh)
    if df is None or len(df) == 0:
        raise ValueError(f"{code} 无K线数据")
    if rule:
        df = _resample_ohlcv(df, rule)
    df = df[["date", "open", "high", "low", "close", "volume"]].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    df = df.dropna(subset=["date", "open", "high", "low", "close"])
    df = (df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
            .reset_index(drop=True))
    # 截到最后 N 根:个股日线可以有 4000 根,全量送回前端会让载荷和笔的数量都失控
    n = MAX_BARS[interval]
    if len(df) > n:
        df = df.iloc[-n:].reset_index(drop=True)
    if len(df) < 10:
        raise ValueError(f"{code} 的{interval}K线不足 10 根,无法做结构分析")
    return df


def _load_data_cfg() -> dict:
    try:
        from quantlab.cli.main import load_config
        return load_config().get("data", {}) or {}
    except Exception:
        return {}


def fetch_target_df(kind: str, code: str, interval: str = "1d",
                    refresh: bool = False) -> pd.DataFrame:
    if kind == "index":
        return fetch_index_df(code, interval, refresh=refresh)
    return fetch_stock_df(code, interval, refresh=refresh)


def fetch_index_df(code: str, interval: str = "1d", max_bars: int | None = None,
                   refresh: bool = False) -> pd.DataFrame:
    """抓取指数K线。返回 date/open/high/low/close/volume,已按日期升序去重。

    去重与升序是硬要求:前端用 category 轴按日期定位笔与中枢,重复或乱序会让坐标错位。
    """
    if interval not in PERIOD:
        raise ValueError(f"interval 必须是 {sorted(PERIOD)} 之一")
    item = get_index(code)
    sym, period = item["sym"], PERIOD[interval]
    n = int(max_bars or MAX_BARS[interval])
    cache_code = f"idx_{sym}"

    from backend.kline import _read_df_cache, _write_df_cache  # 懒导入,避免拖慢启动
    if not refresh:
        cached = _read_df_cache(cache_code, interval, TTL[interval])
        if cached is not None and len(cached) >= 60:
            return cached

    resp = requests.get(
        _KLINE_URL,
        params={"param": f"{sym},{period},,,{n},qfq"},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data")
    if not isinstance(data, dict) or sym not in data:
        raise ValueError(f"指数 {item['name']} 无数据返回")
    node = data[sym]
    rows = node.get(f"qfq{period}") or node.get(period)
    if not rows:
        raise ValueError(f"指数 {item['name']} 无{interval}K线数据")

    df = pd.DataFrame([{
        "date": pd.to_datetime(r[0]),
        "open": float(r[1]),
        "close": float(r[2]),
        "high": float(r[3]),
        "low": float(r[4]),
        "volume": float(r[5]) if len(r) > 5 and r[5] not in (None, "") else 0.0,
    } for r in rows])
    df = (df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
            .reset_index(drop=True))
    if len(df) < 10:
        raise ValueError(f"指数 {item['name']} 的{interval}K线不足 10 根,无法做结构分析")
    _write_df_cache(cache_code, interval, df)
    return df


def higher_interval(interval: str) -> str | None:
    """高一级别。月线之上不再取,中阴阶段只做日→周、周→月两级。"""
    return {"1d": "1w", "1w": "1M", "1M": None}.get(interval)


def _last_zhongshu_summary(zs: list[dict]) -> dict | None:
    if not zs:
        return None
    z = zs[-1]
    return {"start_date": z["start_date"], "end_date": z["end_date"],
            "zg": z["zg"], "zd": z["zd"], "bi_count": z["bi_count"],
            "is_extended": z["is_extended"], "is_current": z["is_current"]}


def analyze_target(code: str, interval: str = "1d", refresh: bool = False) -> dict:
    """单个标的(宽基指数或个股/ETF)单级别的完整缠论分析。

    含高一级别,供第089课的中阴阶段判定。个股与指数走同一条分析路径,区别只在取数。
    """
    from quantlab.chan import analyze, quick_zhongshus

    kind, item = resolve(code)
    df = fetch_target_df(kind, item["code"], interval, refresh=refresh)
    hi = higher_interval(interval)
    higher_zs: list[dict] = []
    higher_note = None
    if hi:
        try:
            hdf = fetch_target_df(kind, item["code"], hi, refresh=refresh)
            higher_zs = quick_zhongshus(hdf)
        except Exception as e:  # 高一级别取不到时中阴阶段无法判定,如实说明而不是假装没有
            higher_note = f"高一级别({hi})数据不可用,第089课的中阴阶段未能判定:{e}"

    res = analyze(df, interval=interval, higher_zhongshus=higher_zs)
    res["code"] = item["code"]
    res["name"] = item["name"]
    res["kind"] = kind
    res["sym"] = item.get("sym")
    res["higher_interval"] = hi
    if higher_note:
        res["zhongyin"] = {"active": None, "note": higher_note, "lesson": "089"}
    closes = df["close"].tolist()
    prev = closes[-2] if len(closes) > 1 else closes[-1]
    res["quote"] = {
        "close": round(float(closes[-1]), 2),
        "change": round(float(closes[-1] - prev), 2),
        "change_pct": round(float((closes[-1] / prev - 1) * 100), 2) if prev else 0.0,
    }
    res["last_zhongshu"] = _last_zhongshu_summary(res["zhongshus"])
    return res


_SIM_TTL = 1800          # 模拟结果缓存:逐根重算一次约 2~3s,不必每次点开都重算
_sim_cache: dict = {}


def simulate_target(code: str, interval: str = "1d", capital: float = 100000.0,
                    refresh: bool = False) -> dict:
    """缠论买卖点驱动的交易模拟(两种仓位策略 + 买入持有基准)。

    严格逐根重算以消除未来函数,代价是一次 2~3s,所以结果按 (标的, 级别, 本金) 缓存。
    """
    import time

    kind, item = resolve(code)
    key = (item["code"], interval, round(float(capital), 2))
    now = time.time()
    hit = _sim_cache.get(key)
    if hit and not refresh and now - hit[0] < _SIM_TTL:
        return hit[1]

    from quantlab.chan.simulate import run_all
    df = fetch_target_df(kind, item["code"], interval, refresh=refresh)
    res = run_all(df, interval=interval, capital=float(capital))
    res["code"] = item["code"]
    res["name"] = item["name"]
    res["kind"] = kind
    _sim_cache[key] = (now, res)
    return res


def watchlist() -> list[dict]:
    """config.json 里的自选股,供页面快捷选择。名称查询并行 + 走缓存。"""
    from concurrent.futures import ThreadPoolExecutor

    try:
        from quantlab.cli.main import load_config
        codes = load_config().get("watchlist") or []
    except Exception:
        codes = []
    pure: list[str] = []
    for c in codes:
        p = str(c).zfill(6)
        if p in _BY_CODE or p in pure:
            continue
        if re.fullmatch(r"\d{6}", p):
            pure.append(p)
    if not pure:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(pure))) as ex:
        named = list(ex.map(_stock_name_cached, pure))
    return [{"code": p, "name": n} for p, n in zip(pure, named)]


def _warm_cache(keys: list[tuple[str, str]]) -> None:
    """并行预热指数K线缓存。概览要取 7 个指数 × 3 个级别,首次访问串行拉会太慢。"""
    from concurrent.futures import ThreadPoolExecutor

    def one(kv):
        try:
            fetch_index_df(kv[0], kv[1])
        except Exception:
            return None
        return None

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(one, keys))


def market_overview(intervals=("1d", "1w", "1M"), refresh: bool = False) -> dict:
    """全部宽基指数 × 日/周/月 的结构摘要,用于强弱对比与总研判(第106课)。"""
    keys = [(i["code"], iv) for i in INDICES for iv in intervals]
    if refresh:
        _warm_cache(keys)

    rows = []
    for item in INDICES:
        levels: dict[str, dict] = {}
        for iv in intervals:
            try:
                r = analyze_target(item["code"], iv, refresh=False)
            except Exception as e:
                levels[iv] = {"error": str(e)}
                continue
            last_sig = r["signals"][-1] if r["signals"] else None
            levels[iv] = {
                "as_of": r["as_of"],
                "trend": r["trend"]["label"],
                "trend_kind": r["trend"]["kind"],
                "ma_category": r["ma_category"]["category"],
                "bi_direction": r["bis"][-1]["direction"] if r["bis"] else None,
                "bi_count": r["counts"]["bis"],
                "zhongshu": r["last_zhongshu"],
                "last_signal": ({"label": last_sig["label"], "date": last_sig["date"],
                                 "direction": last_sig["direction"]} if last_sig else None),
                "zhongyin": r["zhongyin"]["active"],
                "bottom_zone": r["bottom_zone"],
                "divergence": (r["divergences"][-1] if r["divergences"] else None),
            }
        rows.append({"code": item["code"], "name": item["name"], "sym": item["sym"],
                     "levels": levels})

    # 第106课:板块强弱指标 = 类别数的平均值
    strength = {}
    for iv in intervals:
        vals = [r["levels"][iv].get("ma_category") for r in rows
                if isinstance(r["levels"].get(iv), dict) and r["levels"][iv].get("ma_category")]
        strength[iv] = round(sum(vals) / len(vals), 2) if vals else None

    rows.sort(key=lambda r: (r["levels"].get("1d") or {}).get("ma_category") or 0, reverse=True)
    return {
        "as_of": rows[0]["levels"]["1d"].get("as_of") if rows else None,
        "indices": rows,
        "strength": strength,
        "strength_note": ("第106课:缠中说禅板块强弱指标 = 各指数均线类别数的平均值,"
                          "越大越强;均线系统为 5/13/21/34/55/89/144/233,共九类"),
        "basis": {
            "zhongshu_unit": "笔中枢",
            "source": "D:/Code/chzhshch-108-plus/108",
        },
    }

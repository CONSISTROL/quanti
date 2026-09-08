"""
代码 / 交易所 / vt_symbol 映射。

vt_symbol 规范（vnpy）："601857.SSE" = symbol.exchange。
legacy quanti 缓存 key 形如 "sh601857" / "sz159941" / "bj430047"。
"""
from __future__ import annotations

from vnpy.trader.constant import Exchange


def exchange_of_pure(code6: str) -> Exchange:
    """按 6 位代码推断交易所（A股惯例，够用即可）。"""
    c = code6.zfill(6)
    if c.startswith(("60", "68", "9")):
        return Exchange.SSE
    if c.startswith(("5", "58")):
        return Exchange.SSE        # 沪市ETF/LOF 5xxxxx、科创ETF 588
    if c.startswith(("00", "30", "12", "15", "16", "18", "2")):
        return Exchange.SZSE
    if c.startswith(("4", "8")):
        return Exchange.BSE
    return Exchange.SSE if c.startswith("6") else Exchange.SZSE


_PREFIX_EXCHANGE = {
    "sh": Exchange.SSE,
    "sz": Exchange.SZSE,
    "bj": Exchange.BSE,
}


def sina_to_parts(sina_code: str) -> tuple[str, Exchange]:
    """'sh601857' -> ('601857', Exchange.SSE)。"""
    s = sina_code.strip().lower()
    for prefix, ex in _PREFIX_EXCHANGE.items():
        if s.startswith(prefix):
            return s[len(prefix):].zfill(6), ex
    return s.zfill(6), exchange_of_pure(s.zfill(6))


def sina_to_vt(sina_code: str) -> str:
    code, ex = sina_to_parts(sina_code)
    return f"{code}.{ex.value}"


def vt_to_parts(vt_symbol: str) -> tuple[str, Exchange]:
    code, ex = vt_symbol.split(".")
    return code, Exchange(ex)


def sina_of_pure(code6: str) -> str:
    """6 位纯代码 → legacy sina key（sh/sz/bj + code）。"""
    code = code6.zfill(6)
    ex = exchange_of_pure(code)
    prefix = {Exchange.SSE: "sh", Exchange.SZSE: "sz", Exchange.BSE: "bj"}.get(ex, "sh")
    return f"{prefix}{code}"


def vt_to_sina(vt_symbol: str) -> str:
    code, ex = vt_to_parts(vt_symbol)
    prefix = {Exchange.SSE: "sh", Exchange.SZSE: "sz", Exchange.BSE: "bj"}.get(ex, "sh")
    return f"{prefix}{code}"

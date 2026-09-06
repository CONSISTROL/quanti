"""Lightweight stock/ETF name lookup used by the frontend autofill."""
from __future__ import annotations

import re
import requests

from quantlab.data_sources import tencent  # reuse code_pure helper


def _symbol(code: str) -> str:
    code = tencent.code_pure(code)
    if code[0] in ("5", "6", "9"):
        return f"sh{code}"
    if code[0] in ("4", "8"):
        return f"bj{code}"
    return f"sz{code}"


def get_stock_name(code: str) -> dict:
    """Return {'code': '601857', 'name': '中国石油'} from Tencent quote API."""
    pure = str(code).zfill(6)
    sym = _symbol(pure)
    resp = requests.get(
        "https://qt.gtimg.cn/q=" + sym,
        headers={"Referer": "https://gu.qq.com/"},
        timeout=8,
    )
    resp.raise_for_status()
    text = resp.text
    m = re.search(r'="([^"]*)"', text)
    if not m:
        raise ValueError(f"未获取到 {pure} 的名称")
    fields = m.group(1).split("~")
    if len(fields) < 2 or not fields[1]:
        raise ValueError(f"未获取到 {pure} 的名称")
    return {"code": pure, "name": fields[1]}

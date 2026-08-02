"""
腾讯数据源 — LOF 历史K线回退 (QuantDash 不覆盖 LOF, 新浪也无 LOF 接口)

接口: web.ifzq.gtimg.cn fqkline (前复权, 单次上限 640 根)
"""
import pandas as pd
import requests

_TENCENT_KLINE_URL = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'


def code_pure(code):
    """从带前缀的代码中提取纯6位数字"""
    code = str(code)
    for prefix in ('sh', 'sz', 'bj', 'SH', 'SZ', 'BJ'):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code.zfill(6)


def fetch_lof_history(code, max_bars=1200):
    """腾讯 LOF 日线 (前复权, 单次上限640根)

    返回: DataFrame(date/open/high/low/close/volume) 或 None
    """
    code = code_pure(code)
    pref = 'sh' if code[0] in ('5', '6', '9') else 'sz'
    symbol = f'{pref}{code}'
    days = min(max_bars * 2, 640)  # 腾讯单次上限 640 根, 按自然日留余量

    r = requests.get(_TENCENT_KLINE_URL, params={
        'param': f'{symbol},day,,,{days},qfq'}, timeout=15)
    r.raise_for_status()
    d = r.json()
    data = d.get('data', {})
    if not isinstance(data, dict):
        return None
    data = data.get(symbol, {})
    if not isinstance(data, dict):
        return None
    kline = data.get('qfqday') or data.get('day')
    if not kline:
        return None

    out = pd.DataFrame(
        [row[:6] for row in kline],
        columns=['date', 'open', 'close', 'high', 'low', 'volume'])
    out['date'] = pd.to_datetime(out['date'])
    for col in ['open', 'close', 'high', 'low', 'volume']:
        out[col] = pd.to_numeric(out[col], errors='coerce')
    return out.sort_values('date').tail(max_bars).reset_index(drop=True)

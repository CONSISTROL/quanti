"""
QuantDash 数据源 — 个股/ETF 前复权K线 (官方API, 免费单只拉取)

覆盖:
  - 个股 + ETF: QuantDash API (adjust=forward, 单次上限1200根)
  - LOF (160xxx): 腾讯 fqkline 回退 (QuantDash 不覆盖 LOF)

API key 获取: https://quantdash.net/dashboard
配置: 环境变量 QUANTDASH_API_KEY 或 config.json data.quantdash_key
"""
import os
import time
import pickle
import sys
from datetime import datetime

import pandas as pd

from .base import BaseDataSource
from . import tencent

# Windows GBK 控制台保护: ✓/⚠ 等 Unicode 符号直接 print 会报错
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def _is_lof(code):
    """LOF判定: 16xxxx (深市LOF) 走腾讯回退"""
    return tencent.code_pure(code).startswith('16')


def _to_qd_symbol(code):
    """6位纯代码 → QuantDash符号 (601857 → 601857.SH, 159941 → 159941.SZ)"""
    code = tencent.code_pure(code)
    if code[0] in ('5', '6', '9'):
        return f'{code}.SH'
    return f'{code}.SZ'


def _qd_client(api_key=None):
    """创建 QuantDash 客户端 (懒加载)

    优先: 显式传入 → 环境变量 QUANTDASH_API_KEY → config.json data.quantdash_key
    """
    import quantdash as qd
    if api_key is None:
        api_key = os.environ.get('QUANTDASH_API_KEY')
    if api_key is None:
        try:
            from main import load_config
            cfg = load_config()
            api_key = cfg.get('data', {}).get('quantdash_key')
        except Exception:
            api_key = None
    if not api_key:
        raise ValueError(
            'QuantDash API key 未配置: 设置环境变量 QUANTDASH_API_KEY '
            '或在 config.json data.quantdash_key 中配置')
    return qd.QuantDash(api_key=api_key)


class QuantDashDataSource(BaseDataSource):
    name = 'quantdash'
    label = 'QuantDash (个股/ETF前复权 + 腾讯LOF回退)'

    def fetch_watchlist_data(self, codes, cache_dir='cache',
                             use_cache=True, max_bars=1200, sleep=0.2):
        codes = [tencent.code_pure(c) for c in codes]

        # 当日缓存 (cache/quantdash_watchlist_YYYYMMDD.pkl)
        cached = None
        if use_cache:
            today = datetime.now().strftime('%Y%m%d')
            path = os.path.join(cache_dir, f'quantdash_watchlist_{today}.pkl')
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    cached = pickle.load(f)
        if cached is not None:
            have = set(cached.keys())
            need = [c for c in codes
                    if f'sh{c}' not in have and f'sz{c}' not in have]
            if not need:
                print(f'  ✓ 从缓存加载 {len(codes)} 只自选数据')
                return cached
            result = dict(cached)
            print(f'  缓存缺失 {len(need)} 只, 增量拉取...')
        else:
            result = {}
            need = codes

        # 分类: LOF走腾讯, 其余走QuantDash
        lofs = [c for c in need if _is_lof(c)]
        qds = [c for c in need if not _is_lof(c)]

        if qds:
            print(f'  → QuantDash 拉取 {len(qds)} 只 (个股/ETF)...')
            client = _qd_client()
            for code in qds:
                sym = _to_qd_symbol(code)
                try:
                    df = _fetch_quantdash_kline(client, code, max_bars)
                    if df is not None and len(df) >= 30:
                        pref = 'sh' if code[0] in ('5', '6', '9') else 'sz'
                        result[f'{pref}{code}'] = df
                        print(f'    ✓ {code}: {len(df)} bars')
                    else:
                        print(f'    ⚠ {code}: 数据不足')
                except Exception as e:
                    print(f'    ⚠ {code}: {type(e).__name__}: {str(e)[:80]}')
                time.sleep(sleep)

        if lofs:
            print(f'  → 腾讯 拉取 {len(lofs)} 只 (LOF)...')
            for code in lofs:
                try:
                    df = tencent.fetch_lof_history(code, max_bars)
                    if df is not None and len(df) >= 30:
                        pref = 'sh' if code[0] in ('5', '6', '9') else 'sz'
                        result[f'{pref}{code}'] = df
                        print(f'    ✓ {code}: {len(df)} bars')
                    else:
                        print(f'    ⚠ {code}: 数据不足')
                except Exception as e:
                    print(f'    ⚠ {code}: {type(e).__name__}: {str(e)[:80]}')
                time.sleep(sleep)

        if use_cache and result:
            os.makedirs(cache_dir, exist_ok=True)
            today = datetime.now().strftime('%Y%m%d')
            path = os.path.join(cache_dir, f'quantdash_watchlist_{today}.pkl')
            with open(path, 'wb') as f:
                pickle.dump(result, f)
        return result


def _fetch_quantdash_kline(client, code, max_bars=1200):
    """单只个股/ETF 前复权日线 → 统一格式DataFrame"""
    sym = _to_qd_symbol(code)
    df = client.klines.get(
        sym, period='1d', count=min(max_bars, 1200),
        adjust='forward', to_dataframe=True)
    if df is None or len(df) == 0:
        return None

    out = pd.DataFrame({
        'date': pd.to_datetime(df['trade_date']),
        'open': df['open'].astype(float),
        'high': df['high'].astype(float),
        'low': df['low'].astype(float),
        'close': df['close'].astype(float),
        'volume': df['volume'].astype(float) * 100.0,  # 手 → 股 (与新浪缓存一致)
        'amount': df['amount'].astype(float),
    })
    return out.sort_values('date').reset_index(drop=True)


if __name__ == '__main__':
    import sys
    codes = sys.argv[1:] or ['601857', '600547', '518880', '159941', '160723']
    ds = QuantDashDataSource()
    hist = ds.fetch_watchlist_data(codes)
    for k, df in hist.items():
        print(f'{k}: {len(df)} bars, {df["date"].iloc[0].date()} ~ '
              f'{df["date"].iloc[-1].date()}, last close={df["close"].iloc[-1]}')

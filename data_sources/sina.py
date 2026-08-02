"""
新浪数据源 — AkShare/新浪 (个股V8前复权 + 新浪ETF + 腾讯LOF)

覆盖:
  - 个股: 新浪 V8 解密接口 (data_fetcher._fast_fetch_stock_history, 前复权)
  - ETF: ak.fund_etf_hist_sina (新浪基金, 全历史不复权)
  - LOF: 腾讯 fqkline 回退

注意: 新浪 ETF 数据不做份额折算复权 (如纳指ETF 2022-03-07 1:4折算后价格断层),
      指标会失真 — 自选池回测建议用 quantdash 数据源 (config data.source).
"""
import os
import sys
import time
import pickle
from datetime import datetime

import pandas as pd

from .base import BaseDataSource
from . import tencent

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def _sina_symbol(code):
    """6位纯代码 → Sina格式 (sh601857 / sz000001 / sz159941)"""
    code = tencent.code_pure(code)
    return f'{"sh" if code[0] in ("5", "6", "9") else "sz"}{code}'


def _fetch_sina_etf(code, max_bars=1200):
    """新浪 ETF 日线 (ak.fund_etf_hist_sina, 全历史不复权)"""
    import akshare as ak
    df = ak.fund_etf_hist_sina(symbol=_sina_symbol(code))
    if df is None or df.empty or len(df) < 30:
        return None
    df['date'] = pd.to_datetime(df['date'])
    for col in ['open', 'high', 'low', 'close', 'volume']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    return df.sort_values('date').tail(max_bars).reset_index(drop=True)


class SinaDataSource(BaseDataSource):
    name = 'sina'
    label = '新浪/AkShare (个股V8 + 新浪ETF + 腾讯LOF)'
    supports_scan = True

    def fetch_watchlist_data(self, codes, cache_dir='cache',
                             use_cache=True, max_bars=1200, sleep=0.2):
        codes = [tencent.code_pure(c) for c in codes]

        # 当日缓存 (cache/sina_watchlist_YYYYMMDD.pkl)
        cached = None
        if use_cache:
            today = datetime.now().strftime('%Y%m%d')
            path = os.path.join(cache_dir, f'sina_watchlist_{today}.pkl')
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

        # 分类: LOF走腾讯, ETF走新浪, 个股走新浪V8
        lofs = [c for c in need if tencent.code_pure(c).startswith('16')]
        others = [c for c in need if c not in lofs]

        if others:
            print(f'  → 新浪 拉取 {len(others)} 只 (个股/ETF)...')
            # 初始化 V8 + Session (单进程下默认未初始化)
            from data_fetcher import _fast_fetch_stock_history, _init_worker
            try:
                _init_worker()
            except Exception:
                pass
            for code in others:
                pref = 'sh' if code[0] in ('5', '6', '9') else 'sz'
                key = f'{pref}{code}'
                try:
                    df = None
                    if code[0] in ('5', '6'):  # 上海ETF/个股都用V8? 个股才走V8
                        df = _fast_fetch_stock_history(key)
                    if df is None or len(df) < 30:
                        df = _fetch_sina_etf(code, max_bars)
                    if df is not None and len(df) >= 30:
                        result[key] = df
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
            path = os.path.join(cache_dir, f'sina_watchlist_{today}.pkl')
            with open(path, 'wb') as f:
                pickle.dump(result, f)
        return result


if __name__ == '__main__':
    import sys
    codes = sys.argv[1:] or ['601857', '600547', '518880', '159941', '160723']
    ds = SinaDataSource()
    hist = ds.fetch_watchlist_data(codes)
    for k, df in hist.items():
        print(f'{k}: {len(df)} bars, {df["date"].iloc[0].date()} ~ '
              f'{df["date"].iloc[-1].date()}, last close={df["close"].iloc[-1]}')

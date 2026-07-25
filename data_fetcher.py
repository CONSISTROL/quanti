"""
A股多因子量化选股 - 数据采集模块
使用 AkShare (Sina数据源) 获取实时行情、财务报表、历史K线数据
支持本地缓存和API重试机制
"""

import os
import time
import pickle
from datetime import datetime, timedelta

# 绕过系统代理，避免AkShare请求失败
for _proxy_var in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'):
    os.environ.pop(_proxy_var, None)
os.environ['NO_PROXY'] = '*'

import akshare as ak
import pandas as pd
import numpy as np
from tqdm import tqdm
import multiprocessing


# ============================================================
# 工具函数
# ============================================================

def retry_api_call(func, max_retries=3, base_delay=1.0):
    """带指数退避的API调用重试"""
    for attempt in range(max_retries):
        try:
            result = func()
            if result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
                return result
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"  ✗ API调用失败 (重试{max_retries}次): {e}")
                return None
            delay = base_delay * (2 ** attempt)
            print(f"  ⚠ 重试中 ({attempt+1}/{max_retries})，等待{delay:.0f}秒...")
            time.sleep(delay)
    return None


def _find_column(df, candidates):
    """在DataFrame中灵活匹配列名（支持多种AkShare版本）"""
    for name in candidates:
        if name in df.columns:
            return name
    for name in candidates:
        matches = [c for c in df.columns if name in str(c)]
        if matches:
            return matches[0]
    return None


def _code_to_sina(code):
    """
    将纯数字股票代码转换为Sina格式 (带交易所前缀)
    6开头 → sh (上海)
    0/3开头 → sz (深圳)
    4/8开头 → bj (北交所)
    """
    code = str(code).zfill(6)
    if code.startswith('6'):
        return f'sh{code}'
    elif code.startswith(('0', '3')):
        return f'sz{code}'
    elif code.startswith(('4', '8')):
        return f'bj{code}'
    return f'sh{code}'


def _code_pure(code):
    """从带前缀的代码中提取纯6位数字"""
    code = str(code)
    for prefix in ('sh', 'sz', 'bj', 'SH', 'SZ', 'BJ'):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code.zfill(6)


# ============================================================
# 缓存管理
# ============================================================

def save_cache(data, name, cache_dir):
    """保存数据到本地缓存"""
    os.makedirs(cache_dir, exist_ok=True)
    today = datetime.now().strftime('%Y%m%d')
    filepath = os.path.join(cache_dir, f"{name}_{today}.pkl")
    with open(filepath, 'wb') as f:
        pickle.dump(data, f)


def load_cache(name, cache_dir):
    """加载当日缓存数据（仅当日有效）"""
    today = datetime.now().strftime('%Y%m%d')
    filepath = os.path.join(cache_dir, f"{name}_{today}.pkl")
    if os.path.exists(filepath):
        with open(filepath, 'rb') as f:
            return pickle.load(f)
    return None


# ============================================================
# 实时行情数据 (Sina数据源)
# ============================================================

def fetch_spot_data(cache_dir='cache', use_cache=True):
    """
    获取全A股实时行情快照 (Sina数据源)
    列: 代码(sh/sz/bj+6位), 名称, 最新价, 涨跌额, 涨跌幅, 成交量, 成交额
    注意: 不含PE/PB/市值, 需通过财报数据推导
    """
    if use_cache:
        cached = load_cache('spot', cache_dir)
        if cached is not None:
            print("  ✓ 从缓存加载实时行情")
            return cached

    df = retry_api_call(lambda: ak.stock_zh_a_spot())
    if df is not None and not df.empty:
        print(f"  ✓ 获取 {len(df)} 只股票实时行情")
        save_cache(df, 'spot', cache_dir)
    return df


# ============================================================
# 财务报表数据 (东方财富数据源 — 可用)
# ============================================================

def fetch_financial_data(cache_dir='cache', use_cache=True):
    """
    获取最新一期财务报表数据
    列: 股票代码, 股票简称, 每股收益, 每股净资产, 营业总收入, 净利润,
        ROE, 毛利率, 所处行业 等
    """
    if use_cache:
        cached = load_cache('financial', cache_dir)
        if cached is not None:
            print("  ✓ 从缓存加载财务数据")
            return cached

    now = datetime.now()
    year = now.year
    month = now.month

    # 根据当前月份推断最新可用报告期（财报发布有滞后）
    if month >= 11:
        periods = [f"{year}0930", f"{year}0630", f"{year}0331", f"{year-1}1231"]
    elif month >= 9:
        periods = [f"{year}0630", f"{year}0331", f"{year-1}1231", f"{year-1}0930"]
    elif month >= 5:
        periods = [f"{year}0331", f"{year-1}1231", f"{year-1}0930", f"{year-1}0630"]
    else:
        periods = [f"{year-1}0930", f"{year-1}0630", f"{year-1}1231", f"{year-2}1231"]

    df = None
    for period in periods:
        print(f"  尝试获取 {period} 期财报...")
        df = retry_api_call(lambda p=period: ak.stock_yjbb_em(date=p))
        if df is not None and not df.empty:
            print(f"  ✓ 成功获取 {period} 期财报 ({len(df)} 条)")
            save_cache(df, 'financial', cache_dir)
            save_cache(period, 'financial_period', cache_dir)
            break
        time.sleep(0.5)

    return df


def fetch_prev_financial_data(current_period, cache_dir='cache', use_cache=True):
    """获取去年同期财报（用于计算同比增长率）"""
    if use_cache:
        cached = load_cache('financial_prev', cache_dir)
        if cached is not None:
            print("  ✓ 从缓存加载上期财务数据")
            return cached

    if not current_period or len(str(current_period)) < 8:
        return None

    current_period = str(current_period)
    year_part = current_period[:4]
    date_part = current_period[4:]
    prev_period = f"{int(year_part) - 1}{date_part}"

    print(f"  获取去年同期财报 {prev_period}...")
    df = retry_api_call(lambda: ak.stock_yjbb_em(date=prev_period))
    if df is not None and not df.empty:
        print(f"  ✓ 获取上期财报 ({len(df)} 条)")
        save_cache(df, 'financial_prev', cache_dir)
    return df


# ============================================================
# 个股历史K线数据 (Sina数据源)
# ============================================================

def fetch_single_stock_history(sina_symbol):
    """
    获取单只股票的历史日K线（Sina数据源, 前复权）
    sina_symbol: Sina格式代码, 如 'sh600519', 'sz000001'
    """
    try:
        df = ak.stock_zh_a_daily(symbol=sina_symbol, adjust="qfq")
        if df is not None and not df.empty:
            # stock_zh_a_daily 返回列: date, open, high, low, close, volume, ...
            # 已经是英文列名，不需要重命名
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
            return df
    except Exception:
        pass
    return None


def _fetch_history_worker(sym):
    """
    多进程worker: 获取单只股票历史K线 (模块级函数, 可被pickle序列化)
    py_mini_racer (V8) 不是线程安全的, 必须用多进程隔离
    """
    # 子进程也需要清除代理 (spawn模式下不继承父进程的 os.environ 修改)
    for _pv in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'):
        os.environ.pop(_pv, None)
    os.environ['NO_PROXY'] = '*'

    df = fetch_single_stock_history(sym)
    if df is not None and len(df) >= 30:
        return (sym, df.tail(300).reset_index(drop=True))
    return (sym, None)


def fetch_history_batch(symbols, cache_dir='cache', sleep_time=0.15,
                        use_cache=True, workers=5):
    """
    批量获取个股历史K线数据 (多进程并发)
    symbols: Sina格式代码列表 (如 ['sh600519', 'sz000001', ...])
    workers: 并发进程数 (默认5)
    返回: {sina_symbol: DataFrame, ...}

    注意: 使用多进程而非多线程, 因为 AkShare 内部的 py_mini_racer (V8)
    不是线程安全的, 多线程会导致 V8 引擎崩溃
    """
    # 加载已有缓存
    hist_cache = {}
    if use_cache:
        cached = load_cache('hist_batch', cache_dir)
        if cached and isinstance(cached, dict):
            today = datetime.now().strftime('%Y%m%d')
            if cached.get('_date') == today:
                hist_cache = cached
                cached_count = len([k for k in hist_cache if k != '_date'])
                print(f"  ✓ 从缓存加载 {cached_count} 只股票的历史数据")

    # 找出需要新获取的股票
    to_fetch = [s for s in symbols if s not in hist_cache]

    if to_fetch:
        est_time = len(to_fetch) * 0.6 / workers / 60  # 估算: 0.6s/stock with processes
        print(f"  需要获取 {len(to_fetch)} 只股票的历史K线 "
              f"(并发{workers}进程, 约{est_time:.0f}分钟)...")

        success_count = 0
        fail_count = 0
        save_interval = max(100, len(to_fetch) // 5)  # 每20%保存一次

        # 使用多进程: 每个进程有独立的 V8 实例, 避免 py_mini_racer 线程安全问题
        # spawn 模式确保子进程是全新的 Python 解释器, 不会继承父进程的 V8 状态
        ctx = multiprocessing.get_context('spawn')
        with ctx.Pool(processes=workers) as pool:
            with tqdm(total=len(to_fetch), desc="历史数据", ncols=80) as pbar:
                for sym, df in pool.imap_unordered(_fetch_history_worker, to_fetch):
                    if df is not None:
                        hist_cache[sym] = df
                        success_count += 1
                    else:
                        fail_count += 1
                    pbar.update(1)

                    # 定期保存缓存 (防止中断丢失)
                    total_done = success_count + fail_count
                    if total_done % save_interval == 0:
                        hist_cache['_date'] = datetime.now().strftime('%Y%m%d')
                        save_cache(hist_cache, 'hist_batch', cache_dir)

        print(f"  成功: {success_count} | 失败/数据不足: {fail_count}")

        # 最终保存缓存
        hist_cache['_date'] = datetime.now().strftime('%Y%m%d')
        save_cache(hist_cache, 'hist_batch', cache_dir)

    # 返回不含 _date 键的纯数据字典
    return {k: v for k, v in hist_cache.items() if k != '_date'}


# ============================================================
# 股票池过滤
# ============================================================

def filter_universe(spot_df, financial_df=None):
    """
    过滤股票池:
    - 排除 ST / *ST 股票
    - 排除北交所 (bj前缀)
    - 排除停牌股（成交量=0）
    - 排除亏损股（EPS ≤ 0，来自财报数据）
    """
    df = spot_df.copy()
    initial_count = len(df)

    code_col = _find_column(df, ['代码'])
    name_col = _find_column(df, ['名称'])
    if not code_col or not name_col:
        print("  ✗ 无法识别代码/名称列")
        return df

    df[code_col] = df[code_col].astype(str)

    # 排除ST
    mask_st = df[name_col].str.contains('ST', case=False, na=False)
    df = df[~mask_st]

    # 排除北交所 (bj前缀)
    mask_bj = df[code_col].str.startswith('bj', na=False)
    df = df[~mask_bj]

    # 数值列转换
    vol_col = _find_column(df, ['成交量'])
    price_col = _find_column(df, ['最新价'])

    for col in [vol_col, price_col]:
        if col and col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 排除停牌
    suspended = 0
    if vol_col:
        suspended = (df[vol_col] <= 0).sum()
        df = df[df[vol_col] > 0]

    # 排除价格为0
    if price_col:
        df = df[df[price_col] > 0]

    # 如果有财报数据，排除亏损股 (EPS <= 0)
    eps_excluded = 0
    if financial_df is not None and not financial_df.empty:
        fin_code_col = _find_column(financial_df, ['股票代码'])
        eps_col = _find_column(financial_df, ['每股收益'])
        if fin_code_col and eps_col:
            # 构建 纯代码 → EPS 映射
            fin_df = financial_df[[fin_code_col, eps_col]].copy()
            fin_df[fin_code_col] = fin_df[fin_code_col].astype(str).str.zfill(6)
            fin_df[eps_col] = pd.to_numeric(fin_df[eps_col], errors='coerce')
            eps_map = dict(zip(fin_df[fin_code_col], fin_df[eps_col]))

            # 从spot代码中提取纯数字代码
            pure_codes = df[code_col].apply(_code_pure)
            eps_values = pure_codes.map(eps_map)

            # 排除 EPS <= 0 的股票 (有财报且EPS<=0)
            mask_loss = eps_values <= 0
            eps_excluded = mask_loss.sum()
            df = df[~mask_loss]

    print(f"  过滤: {initial_count} → {len(df)} 只股票")
    print(f"    排除: ST {mask_st.sum()} | 北交所 {mask_bj.sum()} | "
          f"停牌 {suspended} | 亏损 {eps_excluded}")

    return df


# ============================================================
# 构建行业映射 (从财报数据中提取)
# ============================================================

def build_sector_map(financial_df):
    """从财报数据中提取 纯6位代码 → 行业 映射"""
    if financial_df is None or financial_df.empty:
        return {}

    code_col = _find_column(financial_df, ['股票代码'])
    sector_col = _find_column(financial_df, ['所处行业'])
    if not code_col or not sector_col:
        return {}

    sector_map = {}
    for _, row in financial_df.iterrows():
        code = str(row[code_col]).zfill(6)
        sector = row[sector_col]
        if pd.notna(sector) and str(sector).strip():
            sector_map[code] = str(sector).strip()

    print(f"  ✓ 从财报获取 {len(sector_map)} 只股票的行业分类")
    return sector_map


# ============================================================
# 主入口: 获取所有数据
# ============================================================

def fetch_all_data(args):
    """
    获取所有需要的数据，返回结构化字典
    """
    cache_dir = args.cache_dir
    use_cache = not args.no_cache

    # ---- Step 1: 实时行情 (Sina) ----
    print("[1/4] 获取全A股实时行情 (Sina)...")
    spot_df = fetch_spot_data(cache_dir, use_cache)
    if spot_df is None or spot_df.empty:
        raise RuntimeError("无法获取实时行情数据，请检查网络连接")

    # ---- Step 2: 财务报表 (含行业分类) ----
    print("\n[2/4] 获取财务报表数据...")
    financial_df = fetch_financial_data(cache_dir, use_cache)

    # 获取上期财报（同比计算用）
    prev_financial_df = None
    if financial_df is not None:
        current_period = load_cache('financial_period', cache_dir)
        if current_period:
            prev_financial_df = fetch_prev_financial_data(current_period, cache_dir, use_cache)

    # ---- Step 3: 过滤股票池 (使用财报EPS过滤亏损股) ----
    print(f"\n[3/4] 过滤股票池...")
    filtered_df = filter_universe(spot_df, financial_df)

    # 提取Sina格式代码列表
    code_col = _find_column(filtered_df, ['代码'])
    sina_symbols = filtered_df[code_col].astype(str).tolist()

    # ---- Step 4: 历史K线 (Sina) ----
    history_dict = {}
    skip_history = getattr(args, 'no_history', False)
    workers = getattr(args, 'workers', 5)

    if skip_history:
        print(f"\n[4/4] 跳过历史K线获取 (--no_history 快速模式)")
        print(f"  ⚠ 动量和风险因子将不可用")
    else:
        print(f"\n[4/4] 获取 {len(sina_symbols)} 只股票的历史K线 (Sina, {workers}线程)...")
        history_dict = fetch_history_batch(
            sina_symbols, cache_dir, args.sleep, use_cache, workers
        )

    # ---- 行业分类 (从财报中提取, 无需额外API调用) ----
    print("\n[附加] 构建行业分类...")
    sector_map = build_sector_map(financial_df)

    return {
        'spot': spot_df,
        'spot_filtered': filtered_df,
        'financial': financial_df,
        'financial_prev': prev_financial_df,
        'history': history_dict,
        'sector_map': sector_map,
        'symbols': sina_symbols,
    }

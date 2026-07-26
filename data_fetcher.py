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
import requests as _requests_lib
import pandas as pd
import numpy as np
from tqdm import tqdm
import multiprocessing
import random


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


def _code_to_fund_sina(code):
    """
    将ETF/LOF的6位代码转换为Sina格式 (带交易所前缀)
    5开头 → sh (上海: 510xxx, 512xxx, 518xxx, 588xxx 等)
    1开头 → sz (深圳: 159xxx, 161xxx 等)
    0开头 → sz (深圳: 部分LOF)
    """
    code = str(code).zfill(6)
    if code.startswith('5'):
        return f'sh{code}'
    else:
        return f'sz{code}'


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


def load_latest_cache(name, cache_dir, max_age_days=7):
    """
    加载最近的缓存文件（跨天有效）
    适用于变化不频繁的数据（如财务报表, 每季度才更新一次）
    max_age_days: 缓存最大有效天数, 默认7天
    """
    if not os.path.exists(cache_dir):
        return None, None

    # 扫描缓存目录, 找最新的匹配文件
    prefix = f"{name}_"
    suffix = ".pkl"
    candidates = []
    for f in os.listdir(cache_dir):
        if f.startswith(prefix) and f.endswith(suffix):
            date_str = f[len(prefix):-len(suffix)]
            try:
                cache_date = datetime.strptime(date_str, '%Y%m%d')
                age = (datetime.now() - cache_date).days
                if age <= max_age_days:
                    candidates.append((age, date_str, f))
            except ValueError:
                continue

    if not candidates:
        return None, None

    # 取最新的
    candidates.sort(key=lambda x: x[0])
    age, date_str, filename = candidates[0]
    filepath = os.path.join(cache_dir, filename)
    with open(filepath, 'rb') as f:
        data = pickle.load(f)
    return data, date_str


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

    print(f"  正在从 Sina 获取全A股实时行情 (约5000+只, 请稍候)...")
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
        # 财报每季度才更新一次, 跨天复用缓存 (7天内有效)
        cached, cache_date = load_latest_cache('financial', cache_dir, max_age_days=7)
        if cached is not None:
            print(f"  ✓ 从缓存加载财务数据 ({cache_date})")
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
        # 上期财报同样是季度数据, 跨天复用
        cached, cache_date = load_latest_cache('financial_prev', cache_dir, max_age_days=7)
        if cached is not None:
            print(f"  ✓ 从缓存加载上期财务数据 ({cache_date})")
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
# 个股历史K线数据 (Sina数据源) — 优化版
# ============================================================

# --- 模块级全局变量 (每个 worker 进程各持有一份) ---
_WORKER_V8 = None       # py_mini_racer V8 实例 (进程内复用, 避免每只股票重建)
_WORKER_SESSION = None  # requests.Session (连接池复用, 减少 TCP 握手)
_JS_DECODE = None       # Sina 行情解密 JS 脚本 (只加载一次)


def _init_worker():
    """
    多进程 worker 初始化 (Pool initializer, 每个 worker 进程只调用一次)
    - 创建 V8 实例并预加载解密脚本 (避免每只股票重建, 省 ~1-2s/只)
    - 创建 requests.Session 并禁用代理 (连接池复用)
    - 清除代理环境变量
    """
    global _WORKER_V8, _WORKER_SESSION, _JS_DECODE

    # 清除代理 (spawn 模式不继承父进程的环境变量修改)
    for _pv in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'all_proxy', 'ALL_PROXY'):
        os.environ.pop(_pv, None)
    os.environ['NO_PROXY'] = '*'

    # V8 引擎: 每个进程一个实例, 进程内所有股票共享
    import py_mini_racer
    from akshare.stock.stock_zh_a_sina import hk_js_decode
    _WORKER_V8 = py_mini_racer.MiniRacer()
    _WORKER_V8.eval(hk_js_decode)
    _JS_DECODE = hk_js_decode

    # HTTP Session: 连接池复用, trust_env=False 绕过系统代理
    _WORKER_SESSION = _requests_lib.Session()
    _WORKER_SESSION.trust_env = False


def _fast_fetch_stock_history(sina_symbol):
    """
    快速获取单只股票历史K线 (优化版, 替代 ak.stock_zh_a_daily)

    优化点:
    1. 复用 V8 实例 — 省去每只股票 ~1-2s 的 V8 初始化
    2. 跳过 share amount 请求 — 省 1 次 HTTP (我们不需要流通股本/换手率)
    3. Session 连接池 — 复用 TCP 连接, 减少握手开销
    4. HTTP 超时 15s — 防止请求无限挂起

    返回: DataFrame (date, open, high, low, close, volume) 或 None
    """
    from akshare.stock.stock_zh_a_sina import (
        zh_sina_a_stock_hist_url, zh_sina_a_stock_qfq_url
    )

    session = _WORKER_SESSION
    try:
        # --- 请求 1/2: 获取加密K线数据, 用 V8 解密 ---
        r = session.get(zh_sina_a_stock_hist_url.format(sina_symbol), timeout=15)
        dict_list = _WORKER_V8.call(
            "d", r.text.split("=")[1].split(";")[0].replace('"', "")
        )
        data_df = pd.DataFrame(dict_list)
        data_df.index = pd.to_datetime(data_df["date"], errors="coerce").dt.date
        del data_df["date"]
        for col in ("prevclose", "postVol", "postAmt"):
            if col in data_df.columns:
                del data_df[col]
        data_df = data_df.astype("float")

        # --- 请求 2/2: 获取前复权因子并应用 ---
        r2 = session.get(zh_sina_a_stock_qfq_url.format(sina_symbol), timeout=15)
        qfq_factor_df = pd.DataFrame(
            eval(r2.text.split("=")[1].split("\n")[0])["data"]
        )
        if qfq_factor_df.shape[0] > 0:
            qfq_factor_df.columns = ["date", "qfq_factor"]
            qfq_factor_df["qfq_factor"] = qfq_factor_df["qfq_factor"].astype(float)
            qfq_factor_df.index = pd.to_datetime(qfq_factor_df.date)
            del qfq_factor_df["date"]
            temp_df = pd.merge(
                data_df, qfq_factor_df,
                left_index=True, right_index=True, how="left"
            )
            temp_df = temp_df.ffill()
            for col in ("open", "high", "close", "low"):
                temp_df[col] = temp_df[col] / temp_df["qfq_factor"]
            temp_df = temp_df.drop(columns=["qfq_factor"])
            data_df = temp_df

        data_df = data_df.reset_index()
        # reset_index 可能生成 'index' 列 (日期索引无名时)
        if "date" not in data_df.columns and "index" in data_df.columns:
            data_df = data_df.rename(columns={"index": "date"})
        if 'date' in data_df.columns:
            data_df['date'] = pd.to_datetime(data_df['date'])
        return data_df

    except Exception:
        return None


def _fetch_history_worker(args):
    """
    多进程worker: 获取单只股票历史K线
    V8 和 Session 由 _init_worker 初始化, 进程内复用
    args: (sym, sleep_time, max_bars) 元组
    """
    if len(args) == 3:
        sym, sleep_time, max_bars = args
    else:
        sym, sleep_time = args
        max_bars = 300

    if sleep_time > 0:
        time.sleep(sleep_time * random.uniform(0.5, 1.5))

    df = _fast_fetch_stock_history(sym)
    if df is not None and len(df) >= 30:
        return (sym, df.tail(max_bars).reset_index(drop=True))
    return (sym, None)


def fetch_history_batch(symbols, cache_dir='cache', sleep_time=0.15,
                        use_cache=True, workers=5, max_bars=300):
    """
    批量获取个股历史K线数据 (多进程并发, V8 进程内复用)
    symbols: Sina格式代码列表 (如 ['sh600519', 'sz000001', ...])
    workers: 并发进程数 (默认8)
    max_bars: 每只股票保留的最大K线条数 (默认300, 估值/回测模式可传1200)
    返回: {sina_symbol: DataFrame, ...}

    优化策略:
    - 每个 worker 进程持有独立的 V8 实例 + HTTP Session (Pool initializer)
    - V8 不再每只股票重建, 省 ~1-2s/只
    - 跳过不需要的 share amount 请求, 省 1 次 HTTP/只
    - Session 连接池复用 TCP 连接
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
        workers = min(workers, 10)  # 上限 10, 避免触发 Sina IP 封禁
        est_time = len(to_fetch) * 0.3 / workers / 60  # 优化后约 0.3s/stock
        print(f"  需要获取 {len(to_fetch)} 只股票的历史K线 "
              f"(并发{workers}进程, 约{est_time:.0f}分钟)...")

        # 构造 (sym, sleep_time, max_bars) 元组列表
        fetch_args = [(s, sleep_time, max_bars) for s in to_fetch]

        success_count = 0
        fail_count = 0
        save_interval = max(100, len(to_fetch) // 5)  # 每20%保存一次

        # 多进程 + initializer: V8 和 Session 在 worker 启动时创建一次
        ctx = multiprocessing.get_context('spawn')
        with ctx.Pool(processes=workers, initializer=_init_worker) as pool:
            with tqdm(total=len(to_fetch), desc="历史数据", ncols=80) as pbar:
                for sym, df in pool.imap_unordered(_fetch_history_worker, fetch_args):
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
# ETF / LOF 数据采集
# ============================================================

def fetch_etf_spot_data(cache_dir='cache', use_cache=True):
    """
    获取全市场ETF实时行情 (东方财富数据源)
    返回对齐后的DataFrame，列名与stock spot兼容
    特殊列: IOPV实时估值, 基金折价率, 最新份额
    """
    if use_cache:
        cached = load_cache('etf_spot', cache_dir)
        if cached is not None:
            print("  ✓ 从缓存加载ETF行情")
            return cached

    print(f"  正在获取全市场ETF实时行情 (约1500+只)...")
    df = retry_api_call(lambda: ak.fund_etf_spot_em())
    if df is not None and not df.empty:
        # 代码列转为str并添加Sina前缀
        code_col = _find_column(df, ['代码'])
        if code_col:
            df[code_col] = df[code_col].astype(str).str.zfill(6).apply(_code_to_fund_sina)
        print(f"  ✓ 获取 {len(df)} 只ETF实时行情")
        save_cache(df, 'etf_spot', cache_dir)
    return df


def fetch_lof_spot_data(cache_dir='cache', use_cache=True):
    """
    获取全市场LOF实时行情 (东方财富数据源)
    返回对齐后的DataFrame，列名与stock spot兼容
    """
    if use_cache:
        cached = load_cache('lof_spot', cache_dir)
        if cached is not None:
            print("  ✓ 从缓存加载LOF行情")
            return cached

    print(f"  正在获取全市场LOF实时行情...")
    df = retry_api_call(lambda: ak.fund_lof_spot_em())
    if df is not None and not df.empty:
        code_col = _find_column(df, ['代码'])
        if code_col:
            df[code_col] = df[code_col].astype(str).str.zfill(6).apply(_code_to_fund_sina)
        print(f"  ✓ 获取 {len(df)} 只LOF实时行情")
        save_cache(df, 'lof_spot', cache_dir)
    return df


def filter_fund_universe(fund_df, fund_type='etf'):
    """
    过滤ETF/LOF:
    - 排除成交量=0（停牌/未上市）
    - 排除价格≤0
    - 排除规模过小（流通市值 < 1亿）
    """
    if fund_df is None or fund_df.empty:
        return fund_df

    df = fund_df.copy()
    initial_count = len(df)
    type_label = 'ETF' if fund_type == 'etf' else 'LOF'

    vol_col = _find_column(df, ['成交量'])
    price_col = _find_column(df, ['最新价'])
    mcap_col = _find_column(df, ['流通市值'])

    for col in [vol_col, price_col, mcap_col]:
        if col and col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # 排除停牌
    suspended = 0
    if vol_col:
        suspended = (df[vol_col] <= 0).sum()
        df = df[df[vol_col] > 0]

    # 排除价格异常
    if price_col:
        df = df[df[price_col] > 0]

    # 排除规模过小 (< 1亿)
    small_excluded = 0
    if mcap_col:
        small_excluded = (df[mcap_col] < 1e8).sum()
        df = df[df[mcap_col] >= 1e8]

    print(f"  {type_label}过滤: {initial_count} → {len(df)} 只")
    print(f"    排除: 停牌 {suspended} | 规模过小 {small_excluded}")

    return df


def _fetch_fund_hist_worker(args):
    """
    多进程worker: 获取单只ETF/LOF历史K线 (东方财富数据源)
    args: (code, sleep_time, fund_type, max_bars) 元组
    code: 纯6位数字代码
    fund_type: 'etf' 或 'lof'
    """
    code, sleep_time, fund_type, max_bars = args

    if sleep_time > 0:
        time.sleep(sleep_time * random.uniform(0.5, 1.5))

    try:
        # 计算日期范围
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=int(max_bars * 1.8))).strftime('%Y%m%d')

        if fund_type == 'etf':
            df = ak.fund_etf_hist_em(
                symbol=str(code).zfill(6), period='daily',
                start_date=start_date, end_date=end_date, adjust='qfq'
            )
        else:
            df = ak.fund_lof_hist_em(
                symbol=str(code).zfill(6), period='daily',
                start_date=start_date, end_date=end_date, adjust='qfq'
            )

        if df is None or df.empty or len(df) < 30:
            return (code, None)

        # 列名映射: 中文 → 英文 (与stock K线格式对齐)
        col_map = {
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume',
        }
        df = df.rename(columns=col_map)

        # 确保关键列存在
        needed = ['date', 'open', 'high', 'low', 'close', 'volume']
        for col in needed:
            if col not in df.columns:
                return (code, None)

        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')

        df = df.sort_values('date').tail(max_bars).reset_index(drop=True)
        return (code, df)

    except Exception:
        return (code, None)


def fetch_fund_history_batch(codes, fund_type='etf', cache_dir='cache',
                             sleep_time=0.15, use_cache=True, workers=5, max_bars=300):
    """
    批量获取ETF/LOF历史K线 (多进程并发, 东方财富数据源)
    codes: 纯6位数字代码列表
    fund_type: 'etf' 或 'lof'
    max_bars: 每只保留的最大K线条数
    返回: {sina_symbol: DataFrame, ...}  (key带sh/sz前缀)
    """
    type_label = 'ETF' if fund_type == 'etf' else 'LOF'
    cache_name = f'hist_{fund_type}'

    # 加载缓存
    hist_cache = {}
    if use_cache:
        cached = load_cache(cache_name, cache_dir)
        if cached and isinstance(cached, dict):
            today = datetime.now().strftime('%Y%m%d')
            if cached.get('_date') == today:
                hist_cache = cached
                cached_count = len([k for k in hist_cache if k != '_date'])
                print(f"  ✓ 从缓存加载 {cached_count} 只{type_label}的历史数据")

    # 转为Sina格式key
    code_to_sina = {_code_pure(c): _code_to_fund_sina(_code_pure(c)) for c in codes}

    # 找出需要获取的
    to_fetch = [c for c in codes if _code_to_fund_sina(str(c).zfill(6)) not in hist_cache]

    if to_fetch:
        workers = min(workers, 8)
        est_time = len(to_fetch) * 0.5 / workers / 60
        print(f"  需要获取 {len(to_fetch)} 只{type_label}的历史K线 "
              f"(并发{workers}进程, 约{est_time:.0f}分钟)...")

        fetch_args = [(str(c).zfill(6), sleep_time, fund_type, max_bars) for c in to_fetch]

        success_count = 0
        fail_count = 0
        save_interval = max(50, len(to_fetch) // 5)

        ctx = multiprocessing.get_context('spawn')
        with ctx.Pool(processes=workers) as pool:
            with tqdm(total=len(to_fetch), desc=f"{type_label}历史", ncols=80) as pbar:
                for code, df in pool.imap_unordered(_fetch_fund_hist_worker, fetch_args):
                    if df is not None:
                        sina_key = _code_to_fund_sina(code)
                        hist_cache[sina_key] = df
                        success_count += 1
                    else:
                        fail_count += 1
                    pbar.update(1)

                    total_done = success_count + fail_count
                    if total_done % save_interval == 0:
                        hist_cache['_date'] = datetime.now().strftime('%Y%m%d')
                        save_cache(hist_cache, cache_name, cache_dir)

        print(f"  {type_label}历史: 成功 {success_count} | 失败 {fail_count}")

        hist_cache['_date'] = datetime.now().strftime('%Y%m%d')
        save_cache(hist_cache, cache_name, cache_dir)

    return {k: v for k, v in hist_cache.items() if k != '_date'}


# ============================================================
# 股票池过滤
# ============================================================

def filter_universe(spot_df, financial_df=None, exclude_gem=False, exclude_star=False):
    """
    过滤股票池:
    - 排除 ST / *ST 股票
    - 排除北交所 (bj前缀)
    - 可选排除创业板 (300xxx)、科创板 (688xxx)
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

    # 排除创业板 (300xxx)
    mask_gem = 0
    if exclude_gem:
        pure_codes = df[code_col].apply(_code_pure)
        mask_gem_s = pure_codes.str.startswith('300')
        mask_gem = mask_gem_s.sum()
        df = df[~mask_gem_s]

    # 排除科创板 (688xxx)
    mask_star = 0
    if exclude_star:
        pure_codes = df[code_col].apply(_code_pure)
        mask_star_s = pure_codes.str.startswith('688')
        mask_star = mask_star_s.sum()
        df = df[~mask_star_s]

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
          f"停牌 {suspended} | 亏损 {eps_excluded}", end='')
    if exclude_gem:
        print(f" | 创业板 {mask_gem}", end='')
    if exclude_star:
        print(f" | 科创板 {mask_star}", end='')
    print()

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
    支持股票 + ETF/LOF混合选股
    """
    cache_dir = args.cache_dir
    use_cache = not args.no_cache
    include_etf_lof = getattr(args, 'include_etf_lof', False)
    max_bars = getattr(args, 'hist_days', 300)

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
    filtered_df = filter_universe(spot_df, financial_df,
                                  exclude_gem=getattr(args, 'exclude_gem', False),
                                  exclude_star=getattr(args, 'exclude_star', False))
    # 标记资产类型
    filtered_df['_asset_type'] = 'stock'

    # ---- ETF/LOF ----
    etf_spot_df = None
    lof_spot_df = None
    if include_etf_lof:
        print(f"\n[3/4+] 获取ETF/LOF行情...")

        # ETF
        etf_raw = fetch_etf_spot_data(cache_dir, use_cache)
        if etf_raw is not None and not etf_raw.empty:
            etf_spot_df = filter_fund_universe(etf_raw, 'etf')
            etf_spot_df['_asset_type'] = 'etf'

        # LOF
        lof_raw = fetch_lof_spot_data(cache_dir, use_cache)
        if lof_raw is not None and not lof_raw.empty:
            lof_spot_df = filter_fund_universe(lof_raw, 'lof')
            lof_spot_df['_asset_type'] = 'lof'

        # 对齐列名并合并
        common_cols = ['代码', '名称', '最新价', '涨跌额', '涨跌幅', '成交量', '成交额', '_asset_type']

        def _align_columns(df, cols):
            """只保留公共列，确保可以合并"""
            available = [c for c in cols if c in df.columns]
            return df[available].copy()

        parts = [_align_columns(filtered_df, common_cols)]
        if etf_spot_df is not None:
            parts.append(_align_columns(etf_spot_df, common_cols))
        if lof_spot_df is not None:
            parts.append(_align_columns(lof_spot_df, common_cols))

        if len(parts) > 1:
            filtered_df = pd.concat(parts, ignore_index=True)
            print(f"  ✓ 合并: 股票 + ETF + LOF = {len(filtered_df)} 只")

    # 提取Sina格式代码列表
    code_col = _find_column(filtered_df, ['代码'])
    sina_symbols = filtered_df[code_col].astype(str).tolist()

    # 构建资产类型映射
    asset_type_map = {}
    if '_asset_type' in filtered_df.columns:
        for _, row in filtered_df.iterrows():
            code = str(row[code_col])
            asset_type_map[code] = row['_asset_type']

    # ---- Step 4: 历史K线 ----
    history_dict = {}
    skip_history = getattr(args, 'no_history', False)
    workers = getattr(args, 'workers', 5)

    if skip_history:
        print(f"\n[4/4] 跳过历史K线获取 (--no_history 快速模式)")
        print(f"  ⚠ 动量和风险因子将不可用")
    else:
        # 分离股票和基金的代码
        stock_symbols = [s for s in sina_symbols if asset_type_map.get(s, 'stock') == 'stock']
        etf_symbols_pure = [_code_pure(s) for s in sina_symbols if asset_type_map.get(s) == 'etf']
        lof_symbols_pure = [_code_pure(s) for s in sina_symbols if asset_type_map.get(s) == 'lof']

        # 股票历史K线 (Sina)
        if stock_symbols:
            print(f"\n[4/4] 获取 {len(stock_symbols)} 只股票的历史K线 (Sina, {workers}进程)...")
            history_dict = fetch_history_batch(
                stock_symbols, cache_dir, args.sleep, use_cache, workers, max_bars
            )

        # ETF历史K线 (东方财富)
        if etf_symbols_pure:
            print(f"\n[4/4+] 获取 {len(etf_symbols_pure)} 只ETF的历史K线 (东方财富)...")
            etf_hist = fetch_fund_history_batch(
                etf_symbols_pure, 'etf', cache_dir, args.sleep, use_cache, workers, max_bars
            )
            history_dict.update(etf_hist)

        # LOF历史K线 (东方财富)
        if lof_symbols_pure:
            print(f"\n[4/4+] 获取 {len(lof_symbols_pure)} 只LOF的历史K线 (东方财富)...")
            lof_hist = fetch_fund_history_batch(
                lof_symbols_pure, 'lof', cache_dir, args.sleep, use_cache, workers, max_bars
            )
            history_dict.update(lof_hist)

    # ---- 行业分类 (从财报中提取, 无需额外API调用) ----
    print("\n[附加] 构建行业分类...")
    sector_map = build_sector_map(financial_df)

    # ETF/LOF行业标记为"基金"
    for sym in sina_symbols:
        pure = _code_pure(sym)
        atype = asset_type_map.get(sym, 'stock')
        if atype in ('etf', 'lof') and pure not in sector_map:
            sector_map[pure] = 'ETF/LOF基金'

    return {
        'spot': spot_df,
        'spot_filtered': filtered_df,
        'financial': financial_df,
        'financial_prev': prev_financial_df,
        'history': history_dict,
        'sector_map': sector_map,
        'symbols': sina_symbols,
        'asset_type_map': asset_type_map,
        'etf_spot': etf_spot_df,
        'lof_spot': lof_spot_df,
    }

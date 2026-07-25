"""
A股多因子量化选股 - 因子计算与打分模块
实现五大类因子: 价值、成长、质量、动量、风险
支持Z-score标准化、加权综合打分
"""

import numpy as np
import pandas as pd


# ============================================================
# 因子分组定义
# ============================================================

FACTOR_GROUPS = {
    'value':    {'label': '价值', 'factors': ['ep', 'bp', 'sp']},
    'growth':   {'label': '成长', 'factors': ['rev_growth', 'profit_growth']},
    'quality':  {'label': '质量', 'factors': ['roe', 'gross_margin']},
    'momentum': {'label': '动量', 'factors': ['ret_1m', 'ret_3m', 'ret_6m']},
    'risk':     {'label': '风险', 'factors': ['vol_inv', 'drawdown_inv']},
}


def _find_column(df, candidates):
    """灵活匹配列名"""
    for name in candidates:
        if name in df.columns:
            return name
    for name in candidates:
        matches = [c for c in df.columns if name in str(c)]
        if matches:
            return matches[0]
    return None


def _safe_float(val):
    """安全转换为浮点数"""
    if val is None:
        return np.nan
    try:
        result = float(val)
        return result if not np.isnan(result) and not np.isinf(result) else np.nan
    except (ValueError, TypeError):
        return np.nan


def _code_pure(code):
    """从带前缀的代码中提取纯6位数字"""
    code = str(code)
    for prefix in ('sh', 'sz', 'bj', 'SH', 'SZ', 'BJ'):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code.zfill(6)


# ============================================================
# 构建财报查找表 (一次性, 避免逐行查找的性能问题)
# ============================================================

def _build_financial_lookup(financial_df):
    """
    从财报DataFrame构建 {纯6位代码: {字段: 值}} 的查找字典
    大幅提升因子计算性能
    """
    if financial_df is None or financial_df.empty:
        return {}

    code_col = _find_column(financial_df, ['股票代码'])
    if not code_col:
        return {}

    # 需要提取的字段及其候选列名
    field_map = {
        'eps':          ['每股收益'],
        'bvps':         ['每股净资产'],
        'roe':          ['净资产收益率'],
        'gross_margin': ['销售毛利率'],
        'revenue':      ['营业总收入-营业总收入'],
        'net_profit':   ['净利润-净利润'],
        'rev_growth':   ['营业总收入-同比增长'],
        'profit_growth':['净利润-同比增长'],
    }

    # 解析每个字段对应的实际列名
    col_map = {}
    for field, candidates in field_map.items():
        col = _find_column(financial_df, candidates)
        if col:
            col_map[field] = col

    lookup = {}
    for _, row in financial_df.iterrows():
        pure = str(row[code_col]).zfill(6)
        entry = {}
        for field, col in col_map.items():
            entry[field] = _safe_float(row[col])
        lookup[pure] = entry

    return lookup


# ============================================================
# 原始因子计算
# ============================================================

def calculate_all_factors(spot_filtered, financial_df, financial_prev_df,
                          history_dict, sector_map, asset_type_map=None):
    """
    计算所有因子，返回包含全部因子值的DataFrame

    参数:
        spot_filtered: 过滤后的实时行情 (Sina格式代码: sh/sz+6位)
        financial_df: 最新财报数据
        financial_prev_df: 去年同期财报 (备用)
        history_dict: {Sina代码: 历史K线DataFrame}
        sector_map: {纯6位代码: 行业名称}
        asset_type_map: {Sina代码: 'stock'/'etf'/'lof'} (可选)
    """
    code_col = _find_column(spot_filtered, ['代码'])
    name_col = _find_column(spot_filtered, ['名称'])
    price_col = _find_column(spot_filtered, ['最新价'])

    if asset_type_map is None:
        asset_type_map = {}

    # 构建财报查找表 (O(1) 查找)
    fin_lookup = _build_financial_lookup(financial_df)
    print(f"  财报查找表: {len(fin_lookup)} 只股票")

    records = []
    for _, row in spot_filtered.iterrows():
        sina_code = str(row[code_col])
        pure_code = _code_pure(sina_code)
        price = _safe_float(row.get(price_col, np.nan))
        asset_type = asset_type_map.get(sina_code, 'stock')

        # 获取财报数据 (仅股票有)
        fin = fin_lookup.get(pure_code, {}) if asset_type == 'stock' else {}

        rec = {
            'code': pure_code,
            'name': row[name_col],
            'price': price if price else np.nan,
            'sector': sector_map.get(pure_code, '未知'),
            'asset_type': asset_type,
        }

        # ---- 价值因子 (仅股票) ----
        if asset_type == 'stock':
            eps = fin.get('eps', np.nan)
            bvps = fin.get('bvps', np.nan)
            revenue = fin.get('revenue', np.nan)
            net_profit = fin.get('net_profit', np.nan)

            # EP = 1/PE = EPS/Price (越高越便宜)
            if price and price > 0 and eps and not np.isnan(eps) and eps > 0:
                rec['ep'] = eps / price
            else:
                rec['ep'] = np.nan

            # BP = 1/PB = BVPS/Price (越高越便宜)
            if price and price > 0 and bvps and not np.isnan(bvps) and bvps > 0:
                rec['bp'] = bvps / price
            else:
                rec['bp'] = np.nan

            # SP = 1/PS = Revenue/MarketCap (越高越便宜)
            if (price and price > 0 and eps and not np.isnan(eps) and eps > 0
                    and net_profit and not np.isnan(net_profit) and net_profit > 0
                    and revenue and not np.isnan(revenue) and revenue > 0):
                total_shares = net_profit / eps
                est_market_cap = price * total_shares
                rec['sp'] = revenue / est_market_cap
            else:
                rec['sp'] = np.nan

            # ---- 质量因子 (仅股票) ----
            rec['roe'] = fin.get('roe', np.nan)
            rec['gross_margin'] = fin.get('gross_margin', np.nan)

            # ---- 成长因子 (仅股票) ----
            rev_g = fin.get('rev_growth', np.nan)
            profit_g = fin.get('profit_growth', np.nan)
            rec['rev_growth'] = np.clip(rev_g, -1.0, 5.0) if not np.isnan(rev_g) else np.nan
            rec['profit_growth'] = np.clip(profit_g, -1.0, 5.0) if not np.isnan(profit_g) else np.nan
        else:
            # ETF/LOF: 无财务因子
            rec['ep'] = rec['bp'] = rec['sp'] = np.nan
            rec['roe'] = rec['gross_margin'] = np.nan
            rec['rev_growth'] = rec['profit_growth'] = np.nan

        # ---- 动量 & 风险因子 (来自历史K线, 股票和ETF/LOF通用) ----
        hist = history_dict.get(sina_code)
        if hist is not None and len(hist) >= 30 and 'close' in hist.columns:
            closes = hist['close'].values.astype(float)
            n = len(closes)

            # 动量: 区间收益率
            rec['ret_1m'] = _period_return(closes, n, 20)
            rec['ret_3m'] = _period_return(closes, n, 60)
            rec['ret_6m'] = _period_return(closes, n, 120)

            # 风险: 年化波动率
            log_returns = np.diff(np.log(closes))
            if len(log_returns) >= 60:
                vol = np.std(log_returns[-60:]) * np.sqrt(252)
                rec['vol_inv'] = -vol if not np.isnan(vol) else np.nan
            else:
                rec['vol_inv'] = np.nan

            # 风险: 最大回撤 (最近120个交易日)
            lookback = min(120, n)
            recent = closes[-lookback:]
            peaks = np.maximum.accumulate(recent)
            dd = (peaks - recent) / peaks
            max_dd = np.max(dd) if len(dd) > 0 else np.nan
            rec['drawdown_inv'] = -max_dd if not np.isnan(max_dd) else np.nan
        else:
            rec['ret_1m'] = rec['ret_3m'] = rec['ret_6m'] = np.nan
            rec['vol_inv'] = rec['drawdown_inv'] = np.nan

        records.append(rec)

    df = pd.DataFrame(records)
    n_factors = sum(1 for k in df.columns if k not in ['code', 'name', 'price', 'sector', 'asset_type'])
    print(f"  计算完成: {len(df)} 只证券, {n_factors} 个因子")

    # 统计各因子的有效数据比例
    for group_key, group in FACTOR_GROUPS.items():
        valid_rates = []
        for f in group['factors']:
            if f in df.columns:
                rate = df[f].notna().mean() * 100
                valid_rates.append(f"{rate:.0f}%")
        print(f"    {group['label']}: 有效率 {' / '.join(valid_rates)}")

    return df


def _period_return(closes, n, period):
    """计算指定周期的收益率"""
    if n > period:
        return (closes[-1] / closes[-(period + 1)]) - 1.0
    return np.nan


# ============================================================
# 因子标准化
# ============================================================

def winsorize(series, lower=0.05, upper=0.95):
    """缩尾处理: 按分位数截断极端值"""
    valid = series.dropna()
    if len(valid) < 10:
        return series
    lo = valid.quantile(lower)
    hi = valid.quantile(upper)
    return series.clip(lo, hi)


def zscore_normalize(series):
    """Z-score标准化"""
    valid = series.dropna()
    if len(valid) < 10:
        return series * np.nan
    mean = valid.mean()
    std = valid.std()
    if std < 1e-10:
        return pd.Series(0.0, index=series.index)
    return (series - mean) / std


# ============================================================
# 综合打分
# ============================================================

def score_stocks(factor_df, weights):
    """
    对所有股票进行因子标准化 + 加权综合打分

    参数:
        factor_df: calculate_all_factors() 的返回值
        weights: dict, 因子组名→权重 (value/growth/quality/momentum/risk)

    返回:
        新增 composite_score 和各组子得分列的 DataFrame, 按得分降序排列
    """
    df = factor_df.copy()

    # ---- 1. 缩尾 + Z-score 标准化 ----
    zscored = {}
    for group_key, group_info in FACTOR_GROUPS.items():
        for factor_name in group_info['factors']:
            if factor_name not in df.columns:
                continue
            col = df[factor_name].copy()
            col = winsorize(col)
            col = zscore_normalize(col)
            zscored[factor_name] = col

    # ---- 2. 计算各组子得分 & 综合得分 ----
    df['composite_score'] = 0.0
    df['_available'] = 0.0
    df['_total'] = 0.0

    # ETF/LOF仅预期有动量+风险因子，股票预期全部5组
    _fund_expected_groups = {'momentum', 'risk'}

    for group_key, group_info in FACTOR_GROUPS.items():
        weight = weights.get(group_key, 0.2)
        factors = [f for f in group_info['factors'] if f in zscored]
        if not factors:
            continue

        factor_matrix = pd.DataFrame({f: zscored[f] for f in factors})
        sub_score = factor_matrix.mean(axis=1)

        df[f'{group_key}_score'] = sub_score
        df['composite_score'] += weight * sub_score.fillna(0)

        available = factor_matrix.notna().sum(axis=1)

        # 根据资产类型调整预期因子数
        if 'asset_type' in df.columns:
            is_fund = df['asset_type'].isin(['etf', 'lof'])
            # 基金类型: 仅动量+风险算入_total
            if group_key in _fund_expected_groups:
                df.loc[is_fund, '_available'] += available[is_fund]
                df.loc[is_fund, '_total'] += len(factors)
            # 股票类型: 所有组都算入
            df.loc[~is_fund, '_available'] += available[~is_fund]
            df.loc[~is_fund, '_total'] += len(factors)
        else:
            df['_available'] += available
            df['_total'] += len(factors)

    # ---- 3. 缺失因子惩罚 ----
    # ETF/LOF: 仅对动量+风险的缺失做惩罚
    # 股票: 对所有5组的缺失做惩罚
    ratio = (df['_available'] / df['_total'].replace(0, np.nan)).fillna(0.5)
    df['composite_score'] *= np.sqrt(ratio)

    # 对于ETF/LOF，重新归一化权重（仅使用动量+风险的权重）
    if 'asset_type' in df.columns:
        fund_weight_total = weights.get('momentum', 0) + weights.get('risk', 0)
        if fund_weight_total > 0 and fund_weight_total < 1.0:
            scale = 1.0 / fund_weight_total
            is_fund = df['asset_type'].isin(['etf', 'lof'])
            # 对基金的综合得分做权重归一化（使其与股票可比）
            df.loc[is_fund, 'composite_score'] *= scale

    # ---- 4. 排名 ----
    df['rank'] = df['composite_score'].rank(ascending=False, method='min').astype(int)

    df = df.drop(columns=['_available', '_total'], errors='ignore')
    df = df.sort_values('composite_score', ascending=False).reset_index(drop=True)

    # 统计
    n_stock = (df['asset_type'] == 'stock').sum() if 'asset_type' in df.columns else len(df)
    n_fund = (df['asset_type'].isin(['etf', 'lof'])).sum() if 'asset_type' in df.columns else 0
    print(f"  打分完成: {len(df)} 只证券参与排名 (股票 {n_stock} + 基金 {n_fund})")
    return df

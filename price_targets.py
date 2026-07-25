"""
A股多因子量化选股 - 买卖参考价计算模块
基于估值分位数（自身历史 + 行业对比）和技术面支撑阻力位
给出买入/卖出建议价格区间
"""

import numpy as np
import pandas as pd
from scipy import stats


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


def _code_pure(code):
    """从带前缀的代码中提取纯6位数字"""
    code = str(code)
    for prefix in ('sh', 'sz', 'bj', 'SH', 'SZ', 'BJ'):
        if code.startswith(prefix):
            return code[len(prefix):]
    return code.zfill(6)


# ============================================================
# 技术面支撑阻力位
# ============================================================

def compute_technical_levels(hist_df, current_price):
    """
    技术面支撑阻力位计算

    返回: {
        'support': float,      # 综合支撑位
        'resistance': float,   # 综合阻力位
        'ma20': float,         # 20日均线
        'ma60': float,         # 60日均线
        'ma120': float,        # 120日均线
        'ma250': float,        # 250日均线 (如数据足够)
        'boll_upper': float,   # 布林上轨
        'boll_lower': float,   # 布林下轨
        'high_120d': float,    # 120日最高
        'low_120d': float,     # 120日最低
    }
    """
    if hist_df is None or len(hist_df) < 20 or 'close' not in hist_df.columns:
        return {k: np.nan for k in ['support', 'resistance', 'ma20', 'ma60',
                                     'ma120', 'ma250', 'boll_upper', 'boll_lower',
                                     'high_120d', 'low_120d']}

    closes = hist_df['close'].values.astype(float)
    highs = hist_df['high'].values.astype(float) if 'high' in hist_df.columns else closes
    lows = hist_df['low'].values.astype(float) if 'low' in hist_df.columns else closes
    n = len(closes)

    # 均线
    ma20 = np.mean(closes[-20:]) if n >= 20 else np.nan
    ma60 = np.mean(closes[-60:]) if n >= 60 else np.nan
    ma120 = np.mean(closes[-120:]) if n >= 120 else np.nan
    ma250 = np.mean(closes[-250:]) if n >= 250 else np.nan

    # 布林带 (20日, 2倍标准差)
    if n >= 20:
        boll_std = np.std(closes[-20:])
        boll_upper = ma20 + 2 * boll_std
        boll_lower = ma20 - 2 * boll_std
    else:
        boll_upper = boll_lower = np.nan

    # 120日最高/最低
    lookback = min(120, n)
    high_120d = np.max(highs[-lookback:])
    low_120d = np.min(lows[-lookback:])

    # 综合支撑位: 取多个支撑信号中的最高值（最保守）
    support_candidates = []
    if not np.isnan(ma120):
        support_candidates.append(ma120)
    if not np.isnan(boll_lower):
        support_candidates.append(boll_lower)
    if low_120d > 0:
        support_candidates.append(low_120d * 1.02)  # 低点上方2%

    support = max(support_candidates) if support_candidates else low_120d

    # 综合阻力位: 取多个阻力信号中的最低值（最保守）
    resistance_candidates = []
    if not np.isnan(boll_upper):
        resistance_candidates.append(boll_upper)
    if high_120d > 0:
        resistance_candidates.append(high_120d * 0.98)  # 高点下方2%

    resistance = min(resistance_candidates) if resistance_candidates else high_120d

    return {
        'support': round(support, 3),
        'resistance': round(resistance, 3),
        'ma20': round(ma20, 3) if not np.isnan(ma20) else np.nan,
        'ma60': round(ma60, 3) if not np.isnan(ma60) else np.nan,
        'ma120': round(ma120, 3) if not np.isnan(ma120) else np.nan,
        'ma250': round(ma250, 3) if not np.isnan(ma250) else np.nan,
        'boll_upper': round(boll_upper, 3) if not np.isnan(boll_upper) else np.nan,
        'boll_lower': round(boll_lower, 3) if not np.isnan(boll_lower) else np.nan,
        'high_120d': round(high_120d, 3),
        'low_120d': round(low_120d, 3),
    }


# ============================================================
# 估值法目标价 (股票专用)
# ============================================================

def compute_valuation_targets(price, eps, bvps, pe_hist, pb_hist, sector_pe_median):
    """
    基于PE/PB历史分位数的估值法目标价

    参数:
        price: 当前价格
        eps: 每股收益
        bvps: 每股净资产
        pe_hist: 历史PE序列 (numpy array)
        pb_hist: 历史PB序列 (numpy array)
        sector_pe_median: 同行业PE中位数

    返回: {
        'buy_low': float,     # 激进买入 (PE 20%分位)
        'buy_high': float,    # 保守买入 (PE 40%分位)
        'sell_low': float,    # 保守卖出 (PE 60%分位)
        'sell_high': float,   # 激进卖出 (PE 80%分位)
        'pe_percentile': float,  # 当前PE百分位 (0-100)
        'pb_percentile': float,  # 当前PB百分位 (0-100)
    }
    """
    result = {k: np.nan for k in ['buy_low', 'buy_high', 'sell_low', 'sell_high',
                                   'pe_percentile', 'pb_percentile']}

    if price <= 0 or np.isnan(price):
        return result

    # ---- PE法 ----
    pe_targets = {}
    current_pe = price / eps if eps and eps > 0 else np.nan

    if not np.isnan(current_pe) and pe_hist is not None and len(pe_hist) >= 60:
        pe_valid = pe_hist[~np.isnan(pe_hist) & (pe_hist > 0) & (pe_hist < 500)]
        if len(pe_valid) >= 30:
            # 当前PE百分位
            result['pe_percentile'] = round(stats.percentileofscore(pe_valid, current_pe), 1)

            # 各分位对应的价格
            pe_20 = np.percentile(pe_valid, 20)
            pe_40 = np.percentile(pe_valid, 40)
            pe_60 = np.percentile(pe_valid, 60)
            pe_80 = np.percentile(pe_valid, 80)

            pe_targets['buy_low'] = eps * pe_20
            pe_targets['buy_high'] = eps * pe_40
            pe_targets['sell_low'] = eps * pe_60
            pe_targets['sell_high'] = eps * pe_80

    # ---- PB法 ----
    pb_targets = {}
    current_pb = price / bvps if bvps and bvps > 0 else np.nan

    if not np.isnan(current_pb) and pb_hist is not None and len(pb_hist) >= 60:
        pb_valid = pb_hist[~np.isnan(pb_hist) & (pb_hist > 0) & (pb_hist < 100)]
        if len(pb_valid) >= 30:
            result['pb_percentile'] = round(stats.percentileofscore(pb_valid, current_pb), 1)

            pb_20 = np.percentile(pb_valid, 20)
            pb_40 = np.percentile(pb_valid, 40)
            pb_60 = np.percentile(pb_valid, 60)
            pb_80 = np.percentile(pb_valid, 80)

            pb_targets['buy_low'] = bvps * pb_20
            pb_targets['buy_high'] = bvps * pb_40
            pb_targets['sell_low'] = bvps * pb_60
            pb_targets['sell_high'] = bvps * pb_80

    # ---- 综合 (PE法和PB法取均值) ----
    for key in ['buy_low', 'buy_high', 'sell_low', 'sell_high']:
        vals = []
        if key in pe_targets and not np.isnan(pe_targets[key]):
            vals.append(pe_targets[key])
        if key in pb_targets and not np.isnan(pb_targets[key]):
            vals.append(pb_targets[key])
        if vals:
            result[key] = round(np.mean(vals), 2)

    # ---- 行业修正 ----
    if not np.isnan(current_pe) and sector_pe_median and sector_pe_median > 0:
        deviation = (current_pe - sector_pe_median) / sector_pe_median
        if abs(deviation) > 0.3:
            # 偏差>30%时，目标价向行业均值靠拢10%
            correction = 0.1
            sector_pe_target = sector_pe_median * (1 - deviation * correction)
            for key in ['buy_low', 'buy_high', 'sell_low', 'sell_high']:
                if not np.isnan(result.get(key, np.nan)) and eps and eps > 0:
                    original = result[key]
                    adjusted = eps * sector_pe_target
                    # 仅微调，不完全替换
                    ratio_map = {'buy_low': 0.6, 'buy_high': 0.8,
                                 'sell_low': 1.2, 'sell_high': 1.4}
                    result[key] = round(original * (1 - correction) + adjusted * ratio_map.get(key, 1.0) * correction, 2)

    return result


# ============================================================
# 单只证券买卖参考价
# ============================================================

def calculate_price_targets(code, sina_code, price, hist_df, fin_data,
                            sector_map, sector_pe_medians):
    """
    计算单只股票的买卖参考价

    参数:
        code: 纯6位代码
        sina_code: Sina格式代码 (sh/sz+6位)
        price: 当前价格
        hist_df: 历史K线DataFrame
        fin_data: 财报数据字典 {eps, bvps, ...}
        sector_map: {code: sector_name}
        sector_pe_medians: {sector_name: pe_median}

    返回: dict with buy_low, buy_high, sell_low, sell_high, support, resistance, etc.
    """
    result = {
        'buy_low': np.nan, 'buy_high': np.nan,
        'sell_low': np.nan, 'sell_high': np.nan,
        'support': np.nan, 'resistance': np.nan,
        'pe_percentile': np.nan, 'pb_percentile': np.nan,
    }

    if price <= 0 or np.isnan(price):
        return result

    # 技术面
    tech = compute_technical_levels(hist_df, price)
    result['support'] = tech['support']
    result['resistance'] = tech['resistance']

    # 估值面 (仅股票)
    eps = fin_data.get('eps', np.nan) if fin_data else np.nan
    bvps = fin_data.get('bvps', np.nan) if fin_data else np.nan

    if not np.isnan(eps) and eps > 0 and hist_df is not None and len(hist_df) >= 60:
        closes = hist_df['close'].values.astype(float)
        pe_hist = closes / eps
        pb_hist = closes / bvps if (not np.isnan(bvps) and bvps > 0) else None

        sector = sector_map.get(code, '未知')
        sector_pe = sector_pe_medians.get(sector, None)

        val = compute_valuation_targets(price, eps, bvps, pe_hist, pb_hist, sector_pe)
        for k in ['buy_low', 'buy_high', 'sell_low', 'sell_high', 'pe_percentile', 'pb_percentile']:
            if not np.isnan(val.get(k, np.nan)):
                result[k] = val[k]

    return result


def calculate_etf_price_targets(code, sina_code, price, hist_df):
    """
    ETF/LOF的买卖参考价（基于价格分位数 + MA支撑阻力）

    用价格历史分位数替代PE/PB
    """
    result = {
        'buy_low': np.nan, 'buy_high': np.nan,
        'sell_low': np.nan, 'sell_high': np.nan,
        'support': np.nan, 'resistance': np.nan,
        'pe_percentile': np.nan, 'pb_percentile': np.nan,
    }

    if price <= 0 or np.isnan(price):
        return result

    # 技术面
    tech = compute_technical_levels(hist_df, price)
    result['support'] = tech['support']
    result['resistance'] = tech['resistance']

    # 价格分位数
    if hist_df is not None and len(hist_df) >= 60 and 'close' in hist_df.columns:
        closes = hist_df['close'].values.astype(float)
        valid = closes[~np.isnan(closes) & (closes > 0)]

        if len(valid) >= 30:
            # 当前价格百分位
            price_pct = stats.percentileofscore(valid, price)
            result['pe_percentile'] = round(price_pct, 1)  # 复用字段表示价格分位

            # 各分位作为目标价
            result['buy_low'] = round(np.percentile(valid, 10), 3)   # 历史10%分位
            result['buy_high'] = round(np.percentile(valid, 30), 3)  # 历史30%分位
            result['sell_low'] = round(np.percentile(valid, 70), 3)  # 历史70%分位
            result['sell_high'] = round(np.percentile(valid, 90), 3) # 历史90%分位

    return result


# ============================================================
# 行业PE中位数计算
# ============================================================

def compute_sector_pe_medians(financial_df, spot_filtered):
    """
    计算各行业PE中位数

    返回: {sector_name: pe_median}
    """
    if financial_df is None or financial_df.empty:
        return {}

    code_col = _find_column(financial_df, ['股票代码'])
    eps_col = _find_column(financial_df, ['每股收益'])
    sector_col = _find_column(financial_df, ['所处行业'])

    if not code_col or not eps_col or not sector_col:
        return {}

    # 构建 代码→EPS 和 代码→行业 映射
    fin = financial_df.copy()
    fin[code_col] = fin[code_col].astype(str).str.zfill(6)
    fin[eps_col] = pd.to_numeric(fin[eps_col], errors='coerce')

    # 从spot获取价格
    spot_code_col = _find_column(spot_filtered, ['代码'])
    spot_price_col = _find_column(spot_filtered, ['最新价'])

    if not spot_code_col or not spot_price_col:
        return {}

    price_map = {}
    for _, row in spot_filtered.iterrows():
        pure = _code_pure(str(row[spot_code_col]))
        price_map[pure] = pd.to_numeric(row[spot_price_col], errors='coerce')

    # 计算每只股票的PE
    sector_pes = {}
    for _, row in fin.iterrows():
        code = str(row[code_col])
        eps = row[eps_col]
        sector = row[sector_col]
        price = price_map.get(code, np.nan)

        if (pd.notna(eps) and eps > 0 and pd.notna(price) and price > 0
                and pd.notna(sector)):
            pe = price / eps
            if 0 < pe < 500:  # 排除极端值
                sector_pes.setdefault(str(sector), []).append(pe)

    # 计算中位数
    result = {}
    for sector, pes in sector_pes.items():
        if len(pes) >= 5:
            result[sector] = np.median(pes)

    return result


# ============================================================
# 财报查找表 (复用factor_model的逻辑)
# ============================================================

def _build_financial_lookup(financial_df):
    """从财报构建 {纯代码: {字段: 值}} 查找字典"""
    if financial_df is None or financial_df.empty:
        return {}

    code_col = _find_column(financial_df, ['股票代码'])
    if not code_col:
        return {}

    field_map = {
        'eps': ['每股收益'],
        'bvps': ['每股净资产'],
        'roe': ['净资产收益率'],
    }

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
            val = row[col]
            try:
                v = float(val)
                entry[field] = v if not np.isnan(v) and not np.isinf(v) else np.nan
            except (ValueError, TypeError):
                entry[field] = np.nan
        lookup[pure] = entry

    return lookup


# ============================================================
# 批量计算
# ============================================================

def batch_calculate_targets(scored_df, history_dict, financial_df, sector_map, asset_type_map=None):
    """
    批量计算所有证券的买卖参考价

    参数:
        scored_df: score_stocks() 的输出
        history_dict: {sina_code: K线DataFrame}
        financial_df: 财报数据
        sector_map: {code: sector_name}
        asset_type_map: {sina_code: 'stock'/'etf'/'lof'}

    返回: scored_df 新增列 buy_low, buy_high, sell_low, sell_high,
          support, resistance, pe_pct, pb_pct
    """
    if asset_type_map is None:
        asset_type_map = {}

    # 构建财报查找表
    fin_lookup = _build_financial_lookup(financial_df)

    # 构建Sina格式代码映射
    sina_lookup = {}
    for _, row in scored_df.iterrows():
        code = str(row.get('code', '')).zfill(6)
        # 尝试找到对应的sina_code
        for s_code in history_dict.keys():
            if _code_pure(s_code) == code:
                sina_lookup[code] = s_code
                break

    # 计算行业PE中位数 (用于行业修正)
    sector_pe_medians = compute_sector_pe_medians(financial_df, scored_df)

    print(f"  计算买卖参考价: {len(scored_df)} 只证券...")

    results = []
    for idx, row in scored_df.iterrows():
        code = str(row.get('code', '')).zfill(6)
        sina_code = sina_lookup.get(code, '')
        price = row.get('price', np.nan)
        asset_type = asset_type_map.get(sina_code, row.get('asset_type', 'stock'))

        hist = history_dict.get(sina_code)
        fin = fin_lookup.get(code, {})

        if asset_type in ('etf', 'lof'):
            target = calculate_etf_price_targets(code, sina_code, price, hist)
        else:
            target = calculate_price_targets(
                code, sina_code, price, hist, fin, sector_map, sector_pe_medians
            )

        results.append(target)

    # 合并到scored_df
    target_df = pd.DataFrame(results)
    for col in target_df.columns:
        scored_df[col] = target_df[col].values

    # 统计
    n_with_targets = scored_df['buy_low'].notna().sum()
    print(f"  ✓ {n_with_targets}/{len(scored_df)} 只证券成功计算参考价")

    return scored_df

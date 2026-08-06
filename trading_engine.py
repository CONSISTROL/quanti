"""
波段交易引擎
============
核心交易体系:
  - 选股: 基本面优质 + 技术面买入信号
  - 买入: MACD金叉 + 趋势向上 + 量价配合
  - 卖出: 止损(-8%) / 止盈(+20%) / MACD死叉 / 跌破均线 / SKDJ超买
  - 仓位: 最多5只, 每只20%资金

回测输出: 操作记录 + 收益曲线 + 统计指标
"""

import numpy as np
import pandas as pd
from tqdm import tqdm
from datetime import datetime, timedelta


from strategies import get_strategy, strategy_label


def fmt_px(v):
    """价格按原始精度显示 (最多3位小数, 去尾零) — ETF/LOF 3位小数不丢失"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return '0'
    s = f'{f:.3f}'.rstrip('0').rstrip('.')
    return s


# ============================================================
# 技术指标计算
# ============================================================

def _ema(data, period):
    if len(data) < period:
        return np.full_like(data, np.nan, dtype=float)
    alpha = 2.0 / (period + 1)
    result = np.full_like(data, np.nan, dtype=float)
    result[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def compute_indicators(closes, volumes=None, highs=None, lows=None):
    """计算全部技术指标, 返回字典"""
    n = len(closes)
    ind = {}
    ind['skdj_close'] = closes[-1] if n > 0 else 0  # 当前价格

    # MA均线
    for p in [5, 10, 20, 60, 120]:
        ind[f'ma{p}'] = np.mean(closes[-p:]) if n >= p else np.nan
    # MA5前一日 (用于判断MA5拐头)
    ind['ma5_prev'] = np.mean(closes[-6:-1]) if n >= 6 else np.nan

    # MACD
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12 - ema26
    valid_dif = dif[~np.isnan(dif)]
    dea = _ema(valid_dif, 9) if len(valid_dif) >= 9 else np.array([np.nan])
    ind['dif'] = dif[-1] if len(dif) > 0 else 0
    ind['dea'] = dea[-1] if len(dea) > 0 and not np.isnan(dea[-1]) else 0
    ind['macd_hist'] = (ind['dif'] - ind['dea']) * 2

    # 动能窗口flags (与 indicator_cache 定义一致, 供 buy_signal 判断)
    # hist_rise: 当日MACD柱较前一日上升 (DIF-DEA回升)
    if len(dif) >= 2 and len(dea) >= 2 and not any(
            np.isnan(x) for x in (dif[-2], dea[-2], dif[-1], dea[-1])):
        ind['hist_rise'] = bool((dif[-1] - dea[-1]) > (dif[-2] - dea[-2]))
    else:
        ind['hist_rise'] = False
    # macd_gold_win: 近3天(含当天)内 DIF>DEA (金叉状态)
    gold = False
    for lag in range(min(3, len(dif), len(dea))):
        if not any(np.isnan(x) for x in (dif[-1 - lag], dea[-1 - lag])):
            if dif[-1 - lag] > dea[-1 - lag]:
                gold = True
                break
    ind['macd_gold_win'] = gold

    # MACD金叉/死叉 (最近3天)
    ind['macd_cross'] = 0
    if len(valid_dif) >= 4 and len(dea) >= 4:
        for lag in range(1, 4):
            if lag < len(valid_dif) and lag < len(dea):
                pd_, pe_ = valid_dif[-(lag+1)], dea[-(lag+1)]
                cd_, ce_ = valid_dif[-lag], dea[-lag]
                if not any(np.isnan(x) for x in [pd_, pe_, cd_, ce_]):
                    if pd_ <= pe_ and cd_ > ce_:
                        ind['macd_cross'] = 1  # 金叉
                        break
                    elif pd_ >= pe_ and cd_ < ce_:
                        ind['macd_cross'] = -1  # 死叉
                        break

    # SKDJ
    if highs is not None and lows is not None and n >= 18:
        k_prev, d_prev = 50.0, 50.0
        k_vals, d_vals = [], []
        for i in range(8, n):
            wh = np.max(highs[i-8:i+1])
            wl = np.min(lows[i-8:i+1])
            rsv = (closes[i] - wl) / (wh - wl) * 100 if wh != wl else 50
            k = 2/3 * k_prev + 1/3 * rsv
            d = 2/3 * d_prev + 1/3 * k
            k_vals.append(k)
            d_vals.append(d)
            k_prev, d_prev = k, d
        ind['skdj_k'] = k_vals[-1] if k_vals else 50
        ind['skdj_d'] = d_vals[-1] if d_vals else 50
        ind['skdj_j'] = 3 * ind['skdj_k'] - 2 * ind['skdj_d']
        # SKDJ交叉
        ind['skdj_cross'] = 0
        if len(k_vals) >= 4:
            for lag in range(1, 4):
                if k_vals[-(lag+1)] <= d_vals[-(lag+1)] and k_vals[-lag] > d_vals[-lag]:
                    ind['skdj_cross'] = 1; break
                elif k_vals[-(lag+1)] >= d_vals[-(lag+1)] and k_vals[-lag] < d_vals[-lag]:
                    ind['skdj_cross'] = -1; break
    else:
        ind['skdj_k'] = ind['skdj_d'] = ind['skdj_j'] = 50
        ind['skdj_cross'] = 0

    # 成交量
    if volumes is not None and n >= 20:
        ind['vol_ratio'] = np.mean(volumes[-5:]) / np.mean(volumes[-20:]) if np.mean(volumes[-20:]) > 0 else 1
        ind['vol_ma5'] = np.mean(volumes[-5:])
        ind['vol_ma20'] = np.mean(volumes[-20:])
    else:
        ind['vol_ratio'] = 1.0
        ind['vol_ma5'] = ind['vol_ma20'] = 0

    # 收益率
    ind['ret_5d'] = (closes[-1] / closes[-6] - 1) if n >= 6 else 0
    ind['ret_20d'] = (closes[-1] / closes[-21] - 1) if n >= 21 else 0
    ind['ret_60d'] = (closes[-1] / closes[-61] - 1) if n >= 61 else 0

    # 波动率
    if n >= 20:
        log_ret = np.diff(np.log(closes[-20:]))
        ind['volatility'] = np.std(log_ret) * np.sqrt(252) if len(log_ret) > 0 else 0
    else:
        ind['volatility'] = 0

    ind['close'] = closes[-1]
    ind['price'] = closes[-1]

    return ind


# ============================================================
# 交易引擎
# ============================================================

class Position:
    """持仓"""
    def __init__(self, code, name, entry_price, entry_date, shares, capital, entry_type='swing'):
        self.code = code
        self.name = name
        self.entry_price = entry_price
        self.entry_date = entry_date
        self.shares = shares
        self.capital = capital  # 投入资金
        self.entry_type = entry_type  # 'swing'=波段主仓 / 'rebound'=短线超跌反弹仓

    def pnl(self, current_price):
        return (current_price / self.entry_price - 1) if self.entry_price > 0 else 0

    def market_value(self, current_price):
        return self.shares * current_price


class TradeRecord:
    """交易记录 (同一笔交易记录两种视角: 信号日口径 + 执行日口径)

    信号日口径(系统买卖信号记录): signal_date/signal_price/pnl_signal —
      按信号当日收盘价(信号价)成交的盈亏, 即系统发出信号时点的收益
    执行日口径(用户A操作记录): date/price/pnl_pct —
      延迟成交模式下 T+1 实际执行价/执行盈亏
    """
    def __init__(self, code, name, direction, price, date, shares, amount, reason, pnl_pct=0,
                 signal_date=None, signal_price=None, pnl_signal=None):
        self.code = code
        self.name = name
        self.direction = direction  # 'BUY' or 'SELL'
        self.price = price
        self.date = date            # 执行日 (非延迟成交模式 == 信号日)
        self.shares = shares
        self.amount = amount
        self.reason = reason
        self.pnl_pct = pnl_pct  # 卖出时的盈亏比例 (执行口径)
        self.signal_date = signal_date or date  # 信号日 (exec模式: T日信号, T+1执行 → 两日分离)
        self.signal_price = signal_price if signal_price is not None else price  # 信号日收盘价(信号价)
        self.pnl_signal = pnl_signal if pnl_signal is not None else pnl_pct  # 信号口径盈亏


def run_swing_backtest(history_dict, scored_df, config, start_date_str='2026-01-01',
                       end_date_str='2026-07-27', precomputed=None, market_df=None):
    """
    波段交易回测引擎

    参数:
        history_dict: {sina_code: K线DataFrame}
        scored_df: 当前打分排名 (用于股票池筛选)
        config: 交易配置字典
        start_date_str: 回测开始日期
        end_date_str: 回测结束日期

    返回: {
        'trades': [TradeRecord, ...],
        'equity_curve': [(date, value), ...],
        'stats': dict,
        'final_positions': [Position, ...],
    }
    """
    # ---- 参数 ----
    initial_capital = config.get('initial_capital', 1000000)
    max_positions = config.get('max_positions', 4)
    position_pct = config.get('position_pct', 0.25)
    stop_loss = config.get('stop_loss', -0.08)
    take_profit = config.get('take_profit', 0.20)
    max_holding_days = config.get('max_holding_days', 40)
    min_buy_score = config.get('min_buy_score', 5)
    strategy = config.get('strategy', 'reversal')  # 从 strategies/ 注册表选择

    # 从策略注册表加载策略 (config.json trading.strategy 配置)
    strat = get_strategy(strategy)
    # config 可覆盖策略类阈值 (如 gap_open 的 gap_max: 排除涨停收盘股, 模拟涨停买不进)
    if strategy in ('gap_open', 'gap_open_open') and config.get('gap_max'):
        strat.gap_max = float(config['gap_max'])
    buy_signal_func = strat.buy_signal
    sell_signal_func = strat.sell_signal
    from strategies.base import BaseStrategy  # 判断是否覆写了rebound
    has_rebound = type(strat).rebound_signal is not BaseStrategy.rebound_signal
    if has_rebound:
        rebound_signal_func = strat.rebound_signal
    print(f"  策略: {strategy_label(strategy)}")

    start_date = pd.Timestamp(start_date_str)
    end_date = pd.Timestamp(end_date_str)

    # ---- 市场环境过滤 (market_filter) ----
    # market_df: 指数K线 (date/close), 指数收盘 < 指数MA20 时禁买 (持仓照旧)
    market_ok_cache = {}
    market_filter = config.get('market_filter', False)
    market_ma = int(config.get('market_ma', 20))
    if market_filter and market_df is not None and len(market_df) > market_ma:
        m_dates = pd.to_datetime(market_df['date']).values
        m_close = market_df['close'].values.astype(float)
        m_ma = pd.Series(m_close).rolling(market_ma).mean().values

        def market_allows(today):
            mask = m_dates <= np.datetime64(today)
            n = int(mask.sum())
            if n < market_ma:
                return True  # 指数数据不足, 不限制
            return bool(m_close[n - 1] >= m_ma[n - 1])

        market_ok_cache = market_allows
        print(f"  市场过滤: 上证指数 < MA{market_ma} 时禁买")
    else:
        market_ok_cache = lambda today: True

    # ---- 准备数据 ----
    # 构建 pure_code → sina_code 映射
    pure_to_sina = {}
    for sina_code in history_dict:
        code = str(sina_code)
        for prefix in ('sh', 'sz', 'bj'):
            if code.startswith(prefix):
                code = code[len(prefix):]
                break
        pure_to_sina[code.zfill(6)] = sina_code

    # 获取候选股票列表
    candidate_codes = []
    candidate_set = set()

    watchlist = config.get('watchlist', [])
    if strategy == 'watchlist' and watchlist:
        # 自选轮动: 只扫描自选池 (可含ETF/LOF)
        candidate_codes = []
        for c in watchlist:
            cc = str(c).zfill(6)
            if cc in pure_to_sina:
                candidate_codes.append(cc)
        candidate_set = set(candidate_codes)
        print(f"  自选轮动: {len(candidate_codes)} 只 (config watchlist)")
    elif strategy in ('reversal', 'bollinger', 'gap_open'):
        # 全市场扫描策略: 超跌/破轨/跳空高开 anywhere
        candidate_codes = list(pure_to_sina.keys())
        candidate_set = set(candidate_codes)
        print(f"  候选: 全部 {len(candidate_codes)} 只 (全市场扫描)")
    else:
        # 动量策略: 综合排名TOP30 + 动量排名TOP30
        if scored_df is not None and 'code' in scored_df.columns:
            for _, row in scored_df.head(30).iterrows():
                code = str(row['code']).zfill(6)
                if code in pure_to_sina and code not in candidate_set:
                    candidate_codes.append(code)
                    candidate_set.add(code)
            if 'momentum_score' in scored_df.columns:
                momentum_ranked = scored_df.sort_values('momentum_score', ascending=False)
                for _, row in momentum_ranked.head(30).iterrows():
                    code = str(row['code']).zfill(6)
                    if code in pure_to_sina and code not in candidate_set:
                        candidate_codes.append(code)
                        candidate_set.add(code)
        if not candidate_codes:
            candidate_codes = list(pure_to_sina.keys())[:60]
        print(f"  龙头候选: {len(candidate_codes)} 只 (综合TOP30 + 动量TOP30)")

    # 找共同交易日
    all_dates = set()
    for sina_code in [pure_to_sina[c] for c in candidate_codes if c in pure_to_sina]:
        hist = history_dict.get(sina_code)
        if hist is not None and 'date' in hist.columns:
            for d in hist['date'].values:
                all_dates.add(pd.Timestamp(d))

    trading_dates = sorted([d for d in all_dates if start_date <= d <= end_date])
    if not trading_dates:
        print(f"  ✗ 在 {start_date_str} ~ {end_date_str} 范围内无交易日")
        return None

    print(f"  交易日: {trading_dates[0].strftime('%Y-%m-%d')} ~ "
          f"{trading_dates[-1].strftime('%Y-%m-%d')} ({len(trading_dates)} 天)")

    # ---- 回测主循环 ----
    cash = initial_capital
    positions = []  # [Position, ...]
    trades = []     # [TradeRecord, ...]
    equity_curve = []
    max_profit_tracker = {}  # {code: max_profit_seen}

    # 预构建代码元数据 (rank/name) 与自选优先级映射 — 候选扫描每只股票每天查询,
    # 避免循环内 pandas 全表布尔过滤 (全市场扫描时从~1ms/只降至~1us, 提速千倍)
    code_meta = {}  # {6位code: (rank, name)}
    if scored_df is not None and 'code' in scored_df.columns:
        for _, row in scored_df.iterrows():
            code_meta[str(row['code']).zfill(6)] = (
                int(row.get('rank', 999)), str(row.get('name', '')))
    wpri_map = {}
    for k, v in config.get('watchlist_priority', {}).items():
        wpri_map[str(k).zfill(6)] = int(v)

    # 跳空高开策略: 信号完全预计算 (向量化涨幅, 逆排索引 {date_str: [code,...]})
    # 候选扫描只遍历当天有信号的股票, 避免全市场×全天逐只调用 buy_signal
    # gap_open:   收盘涨幅口径 (close/prev-1, 报告复刻)
    # gap_open_open: 开盘跳空口径 (open/prev-1, 米筐模板) + 放量确认 (5日均量, 主力净流入代理)
    sig_by_date = None
    if strategy in ('gap_open', 'gap_open_open'):
        gap_min = getattr(strat, 'gap_min', 0.07)
        gap_max = getattr(strat, 'gap_max', 1.0)  # 涨幅上限 (config gap_max 覆盖, 模拟涨停买不进)
        vol_min = getattr(strat, 'vol_min', 1.5)
        sig_by_date = {}
        for code, sina in pure_to_sina.items():
            hdf = history_dict.get(sina)
            if hdf is None or 'close' not in hdf.columns or 'date' not in hdf.columns:
                continue
            closes = hdf['close'].values.astype(float)
            dates = hdf['date'].values
            if len(closes) < 2:
                continue
            prev = np.empty_like(closes)
            prev[0] = 0.0
            prev[1:] = closes[:-1]
            if strategy == 'gap_open_open':
                # 开盘跳空: 开盘价/昨收-1 (集合竞价定盘价)
                if 'open' not in hdf.columns:
                    continue
                base = hdf['open'].values.astype(float)
            else:
                base = closes
            mask = (base > 0) & (prev > 0)
            if not mask.any():
                continue
            rise = np.zeros_like(closes)
            rise[mask] = base[mask] / prev[mask] - 1
            mask &= rise >= gap_min
            mask &= rise < gap_max
            if strategy == 'gap_open_open' and 'volume' in hdf.columns:
                # 放量确认: 当日量/5日均量 >= vol_min (主力净流入代理)
                vols = hdf['volume'].values.astype(float)
                cs = np.cumsum(vols)
                avg5 = np.empty_like(vols)
                avg5[0] = vols[0]
                if len(vols) > 1:
                    avg5[1:min(5, len(vols))] = cs[1:min(5, len(vols))] / np.arange(2, min(5, len(vols)) + 1)
                if len(vols) >= 6:
                    avg5[5:] = (cs[5:] - cs[:-5]) / 5.0
                vr = np.zeros_like(vols)
                okv = avg5 > 0
                vr[okv] = vols[okv] / avg5[okv]
                mask &= vr >= vol_min
            if mask.any():
                for i in np.nonzero(mask)[0]:
                    dstr = pd.Timestamp(dates[i]).strftime('%Y-%m-%d')
                    sig_by_date.setdefault(dstr, []).append(code)
        n_sig = sum(len(v) for v in sig_by_date.values())
        print(f'  信号预筛: {n_sig} 个买入信号, 覆盖 {len(sig_by_date)} 个交易日 (逆排索引)')

    # 凯利公式参数
    kelly_mode = config.get('kelly_mode', False)
    kelly_wins = []
    kelly_losses = []

    def calc_kelly_fraction():
        """根据已有交易动态计算凯利值"""
        if len(kelly_wins) < 3 or len(kelly_losses) < 3:
            return position_pct  # 交易太少,用默认值
        p = len(kelly_wins) / (len(kelly_wins) + len(kelly_losses))
        q = 1 - p
        b = np.mean(kelly_wins) / abs(np.mean(kelly_losses)) if np.mean(kelly_losses) != 0 else 1
        kelly = (b * p - q) / b if b > 0 else 0
        # 限制在5%-30%之间
        return max(0.05, min(0.30, kelly))

    # ---- 成交模式: 次日开盘价 / 次日尾盘价 ----
    # exec_next_open:  T日收盘信号 → T+1早盘开盘价成交 (买卖都延迟, 真实场景)
    # buy_next_open:   仅买入按T+1开盘价, 卖出仍按信号当日收盘价 (测试买入延迟的独立影响)
    # exec_next_close: T日收盘信号 → T+1尾盘(收盘价)成交 (15:57收盘前手动交易的模拟)
    exec_next_open = config.get('exec_next_open', False)
    buy_next_open = config.get('buy_next_open', False)
    exec_next_close = config.get('exec_next_close', False)
    buy_next_close = config.get('buy_next_close', False)  # 仅买入延迟: T日信号 → T+1尾盘(收盘价)买入, 卖出仍按持有到期当日尾盘
    if strategy in ('gap_open', 'gap_open_open'):
        # 跳空策略当日成交: 开盘价延迟模式与信号口径不符 (信号日≠成交日)
        # exec_next_close 会被 config.json(手动交易模拟)继承, 回测默认禁用 (保持当日成交)
        # buy_next_close 不会从 config 继承, 仅显式传入时生效 (信号次日尾盘买入变体)
        exec_next_open = buy_next_open = exec_next_close = False
    pending_sells = []    # [(pos, reason, sig_date, sig_price)] — T日收盘卖出信号 → T+1开盘执行
    pending_reduces = []  # [(pos, reason, ratio, sig_date, sig_price)] — T日收盘减仓信号 → T+1开盘执行
    pending_buys = []     # [(code, name, score, reason, entry_type, sig_date, sig_price)] — T日收盘买入信号 → T+1开盘执行
    sys_entry = {}        # {code: 信号价买入成本} — 系统信号口径的成本 (用户A执行口径成本在Position.entry_price)

    # 降频机制 (减少信号翻转交易, 默认关闭):
    # min_holding_days: 最短持有天数 — 持有不足N天时忽略技术性卖出(死叉/趋势转弱), 止损-5%始终有效
    # death_cross_confirm: SKDJ高位死叉连续N天确认 — 连续N天死叉才卖出 (慢牛中K>70常驻, 死叉多为噪声)
    min_holding_days = int(config.get('min_holding_days', 0))
    death_cross_confirm = int(config.get('death_cross_confirm', 0))
    death_streak = {}     # {code: 连续死叉天数}
    if strategy == 'gap_open_open':
        print('  成交模式: 开盘价成交 (开盘集合竞价决策 → 当日开盘价买入, 次日尾盘收盘价卖出, 米筐模板口径)')
    elif strategy == 'gap_open':
        print('  成交模式: 收盘价成交 (当日收盘决策 → 收盘价买入, 次日收盘价卖出)')
    elif exec_next_open:
        print('  成交模式: 次日开盘价成交 (T日收盘信号 → T+1日早盘开盘价买卖)')
    elif exec_next_close:
        print('  成交模式: 次日尾盘(收盘价)成交 (T日收盘信号 → T+1日尾盘价买卖)')
    elif buy_next_close:
        print('  成交模式: 买入按次日尾盘收盘价 (T日收盘信号 → T+1尾盘买入, 卖出仍按持有到期当日尾盘)')
    elif buy_next_open:
        print('  成交模式: 买入按次日开盘价 (卖出仍按信号当日收盘价)')

    for day_idx, today in enumerate(tqdm(trading_dates, desc="  回测进度", ncols=80, bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]')):
        # ---- 0. 当日卖出集合 (卖出当天禁止买回, 避免同日卖买换手) ----
        sold_today = set()

        # ---- 0b. 执行昨日挂起的信号 (次日成交, 先卖后买) ----
        # exec_next_open:  T+1开盘价成交 / exec_next_close: T+1尾盘(收盘价)成交
        if pending_sells or pending_reduces or pending_buys:
            def _sig_close(code, sig_date):
                """信号日收盘价 (高位换仓等非标准卖出用于系统信号口径)"""
                if precomputed and code in precomputed:
                    pd_ = precomputed[code].get(sig_date.strftime('%Y-%m-%d'))
                    if pd_ and pd_.get('close'):
                        return float(pd_['close'])
                sina = pure_to_sina.get(code)
                hdf = history_dict.get(sina) if sina else None
                if hdf is not None and 'close' in hdf.columns:
                    m = hdf['date'] <= sig_date
                    if m.sum() > 0:
                        return float(hdf['close'].values[m][-1])
                return None

            need_codes = ({p.code for p, _, _, _ in pending_sells}
                          | {p.code for p, _, _, _, _ in pending_reduces}
                          | {c for c, *_ in pending_buys})
            opens = {}      # 卖出执行价 (sell_col)
            buy_opens = {}  # 买入执行价 (buy_col, buy_next_close 时独立于卖出列)
            sell_col = 'close' if exec_next_close else 'open'
            buy_col = 'close' if (exec_next_close or buy_next_close) else 'open'
            for code in need_codes:
                sina = pure_to_sina.get(code)
                hdf = history_dict.get(sina) if sina else None
                if hdf is not None and sell_col in hdf.columns:
                    m = hdf['date'] <= today
                    if m.sum() > 0:
                        opens[code] = float(hdf[sell_col].values[m][-1])
                        buy_opens[code] = float(hdf[buy_col].values[m][-1]) if buy_col in hdf.columns else opens[code]
                if code not in opens and precomputed and code in precomputed:
                    pd_ = precomputed[code].get(today.strftime('%Y-%m-%d'))
                    if pd_:
                        opens[code] = float(pd_.get(sell_col, pd_.get('close', 0)))
                        buy_opens[code] = float(pd_.get(buy_col, pd_.get('close', 0)))

            def _px(code, fallback):
                p = opens.get(code)
                return p if p is not None and p > 0 else fallback

            # 先卖出/减仓 (释放现金)
            for pos, reason, sig_date, sig_price in pending_sells:
                px = _px(pos.code, pos.entry_price)
                pnl_pct = pos.pnl(px)
                sys_cost = sys_entry.get(pos.code, pos.entry_price)
                pnl_signal = (sig_price / sys_cost - 1) if sig_price else pnl_pct
                amount = pos.shares * px
                cash += amount
                trades.append(TradeRecord(pos.code, pos.name, 'SELL', px, today,
                                          pos.shares, amount, reason, pnl_pct,
                                          signal_date=sig_date, signal_price=sig_price,
                                          pnl_signal=pnl_signal))
                positions.remove(pos)
                sys_entry.pop(pos.code, None)
                max_profit_tracker.pop(pos.code, None)
                death_streak.pop(pos.code, None)
                sold_today.add(pos.code)
                if kelly_mode:
                    if pnl_pct > 0:
                        kelly_wins.append(pnl_pct)
                    else:
                        kelly_losses.append(pnl_pct)
            for pos, reason, ratio, sig_date, sig_price in pending_reduces:
                px = _px(pos.code, pos.entry_price)
                reduce_shares = int(pos.shares * ratio / 100) * 100
                if 100 <= reduce_shares < pos.shares:
                    cash += reduce_shares * px
                    sys_cost = sys_entry.get(pos.code, pos.entry_price)
                    pnl_signal = (sig_price / sys_cost - 1) if sig_price else pos.pnl(px)
                    trades.append(TradeRecord(pos.code, pos.name, 'SELL', px, today,
                                              reduce_shares, reduce_shares * px,
                                              reason, pos.pnl(px), signal_date=sig_date,
                                              signal_price=sig_price, pnl_signal=pnl_signal))
                    pos.shares -= reduce_shares
                    if kelly_mode:
                        if pos.pnl(px) > 0:
                            kelly_wins.append(pos.pnl(px))
                        else:
                            kelly_losses.append(pos.pnl(px))
            # 再买入 (当日开盘价)
            avail = max_positions - len(positions)
            for code, name, score, reason, entry_type, sig_date, sig_price in pending_buys:
                if code in sold_today or avail <= 0:
                    continue
                px = buy_opens.get(code)
                if px is None or px <= 0:
                    continue
                if cash < px * 100:
                    # 现金不足: 卖出持仓中最高位的 (K>75) 换资金
                    sold_hp = False
                    pcom = precomputed or {}
                    for pos in sorted(positions, key=lambda p: (
                            pcom.get(p.code, {}).get(today.strftime('%Y-%m-%d'), {}).get('skdj_k', 0)),
                                      reverse=True):
                        pdata = pcom.get(pos.code, {}).get(today.strftime('%Y-%m-%d'), {})
                        if pdata.get('skdj_k', 50) > 75:
                            hp = opens.get(pos.code, pdata.get('skdj_close', pdata.get('close', 0)))
                            cash += pos.shares * hp
                            sp = _sig_close(pos.code, sig_date) or hp
                            sys_cost = sys_entry.get(pos.code, pos.entry_price)
                            trades.append(TradeRecord(pos.code, pos.name, 'SELL', hp, today,
                                                      pos.shares, pos.shares * hp, '高位换仓(K>75)',
                                                      pos.pnl(hp), signal_date=sig_date,
                                                      signal_price=sp,
                                                      pnl_signal=(sp / sys_cost - 1) if sp else pos.pnl(hp)))
                            positions.remove(pos)
                            max_profit_tracker.pop(pos.code, None)
                            death_streak.pop(pos.code, None)
                            sold_hp = True
                            break
                    if not sold_hp:
                        continue
                if kelly_mode:
                    alloc = cash * calc_kelly_fraction()
                elif config.get('full_position', False):
                    alloc = cash * position_pct
                else:
                    weight = position_pct * min(max(score, 0) / 10.0, 1.0)
                    alloc = cash * weight
                if alloc < px * 100:
                    continue
                shares = int(alloc / px / 100) * 100
                if shares <= 0:
                    continue
                amount = shares * px
                cash -= amount
                positions.append(Position(code, name, px, today, shares, amount, entry_type))
                trades.append(TradeRecord(code, name, 'BUY', px, today, shares, amount, reason,
                                          signal_date=sig_date, signal_price=sig_price))
                sys_entry[code] = sig_price
                avail -= 1
            pending_sells.clear()
            pending_reduces.clear()
            pending_buys.clear()

        # ---- 1. 检查持仓, 判断是否卖出 ----
        to_sell = []
        for pos in positions:
            sina = pure_to_sina.get(pos.code)
            if not sina:
                continue

            # 优先使用预计算数据 (包含周线SKDJ和窗口flags)
            today_str = today.strftime('%Y-%m-%d')
            ind = None
            if precomputed and pos.code in precomputed:
                pdata = precomputed[pos.code].get(today_str)
                if pdata:
                    ind = pdata

            if ind is None:
                hist = history_dict.get(sina)
                if hist is None:
                    continue

                mask = hist['date'] <= today
                if mask.sum() == 0:
                    continue
                idx = mask.sum() - 1
                closes = hist['close'].values.astype(float)[:idx+1]
                volumes_arr = hist['volume'].values.astype(float)[:idx+1] if 'volume' in hist.columns else None
                highs_arr = hist['high'].values.astype(float)[:idx+1] if 'high' in hist.columns else None
                lows_arr = hist['low'].values.astype(float)[:idx+1] if 'low' in hist.columns else None

                if len(closes) < 20:
                    continue

                ind = compute_indicators(closes, volumes_arr, highs_arr, lows_arr)

            holding_days = sum(1 for d in trading_dates if pos.entry_date < d <= today)

            # 更新最大浮盈
            current_pnl = (ind['close'] / pos.entry_price - 1) if pos.entry_price > 0 else 0
            max_profit_tracker[pos.code] = max(max_profit_tracker.get(pos.code, 0), current_pnl)

            # 最大持有天数强制卖出 (max_holding_days=0表示不限制)
            if max_holding_days > 0 and holding_days >= max_holding_days:
                to_sell.append((pos, ind['close'], f'持有{holding_days}天到期'))
                continue

            # 短线超跌反弹仓: 反弹到日线MA20 或 持有5个交易日到期
            if pos.entry_type == 'rebound':
                close_price = ind.get('skdj_close', ind.get('close', 0))
                ma20 = ind.get('ma20', 0)
                if holding_days >= 5:
                    to_sell.append((pos, ind['close'], f'反弹到期({holding_days}天)'))
                    continue
                if holding_days >= 2 and ma20 > 0 and close_price >= ma20:
                    to_sell.append((pos, ind['close'], f'反弹到MA20({ma20:.2f})'))
                    continue

            # 高位减仓 (部分卖出): 卖出50%保留底仓, 释放现金
            reduce_fn = getattr(strat, 'reduce_signal', None)
            if reduce_fn:
                is_reduce, ratio = reduce_fn(ind, pos.entry_price)
                if is_reduce and 0 < ratio < 1:
                    reduce_price = ind.get('skdj_close', ind.get('close', 0))
                    if exec_next_open or exec_next_close:
                        pending_reduces.append((pos, f'高位减仓{ratio:.0%}', ratio, today, reduce_price))
                    else:
                        reduce_shares = int(pos.shares * ratio / 100) * 100
                        if reduce_shares >= 100 and reduce_shares < pos.shares:
                            cash += reduce_shares * reduce_price
                            trades.append(TradeRecord(
                                pos.code, pos.name, 'SELL', reduce_price, today,
                                reduce_shares, reduce_shares * reduce_price,
                                f'高位减仓{ratio:.0%}', pos.pnl(reduce_price)
                            ))
                            pos.shares -= reduce_shares
                            if kelly_mode:
                                if pos.pnl(reduce_price) > 0:
                                    kelly_wins.append(pos.pnl(reduce_price))
                                else:
                                    kelly_losses.append(pos.pnl(reduce_price))

            # 死叉连续天数跟踪 (每日更新, 与卖出触发独立)
            if death_cross_confirm > 0 and hasattr(strat, 'is_death_cross'):
                death_streak[pos.code] = death_streak.get(pos.code, 0) + 1 \
                    if strat.is_death_cross(ind) else 0

            is_sell, reason = sell_signal_func(ind, pos.entry_price, holding_days,
                                               max_profit_tracker.get(pos.code, 0))
            if is_sell:
                # 最短持有期: 持有不足N天时忽略技术性卖出(死叉/趋势转弱), 止损始终有效
                if min_holding_days > 0 and holding_days < min_holding_days \
                        and '止损' not in reason:
                    is_sell = False
                # 死叉双日确认: SKDJ高位死叉需连续N天才卖 (持有期保护之上再过滤)
                elif death_cross_confirm > 0 and '死叉' in reason \
                        and death_streak.get(pos.code, 0) < death_cross_confirm:
                    is_sell = False
            if is_sell:
                to_sell.append((pos, ind['close'], reason))

        # 执行卖出 (清仓)
        if exec_next_open or exec_next_close:
            for pos, sell_price, reason in to_sell:
                pending_sells.append((pos, reason, today, sell_price))
            to_sell = []
        for pos, sell_price, reason in to_sell:
            pnl_pct = pos.pnl(sell_price)
            amount = pos.shares * sell_price
            cash += amount
            trades.append(TradeRecord(
                pos.code, pos.name, 'SELL', sell_price, today,
                pos.shares, amount, reason, pnl_pct
            ))
            positions.remove(pos)
            max_profit_tracker.pop(pos.code, None)
            death_streak.pop(pos.code, None)
            sold_today.add(pos.code)  # 当日禁买

            # 凯利公式: 记录盈亏
            if kelly_mode:
                if pnl_pct > 0:
                    kelly_wins.append(pnl_pct)
                else:
                    kelly_losses.append(pnl_pct)

        # ---- 2. 扫描买入信号 (含低位加仓 + 无现金卖高换低) ----
        available_slots = max_positions - len(positions)
        if (available_slots > 0 and cash > initial_capital * 0.05
                and market_ok_cache(today)):
            buy_candidates = []
            held_codes = {p.code for p in positions}
            add_fn = getattr(strat, 'add_position_signal', None)

            today_str = today.strftime('%Y-%m-%d')

            # 跳空高开策略: 只遍历当天有信号的股票 (逆排索引, 通常几十只 vs 全市场2732只)
            scan_codes = sig_by_date.get(today_str, []) if sig_by_date is not None else candidate_codes

            for code in scan_codes:
                if code in sold_today:
                    continue  # 当日已卖出 (当日禁买)
                if code in held_codes and add_fn is None:
                    continue  # 已持仓且策略不支持加仓
                sina = pure_to_sina.get(code)
                if not sina:
                    continue

                # 优先使用预计算数据 (包含周线SKDJ)
                ind = None
                if precomputed and code in precomputed:
                    pdata = precomputed[code].get(today_str)
                    if pdata:
                        ind = pdata

                if ind is None:
                    # 回退到实时计算
                    hist = history_dict.get(sina)
                    if hist is None:
                        continue

                    if strategy in ('gap_open', 'gap_open_open'):
                        # 跳空策略轻量构造 (避免全量指标计算)
                        # gap_open: 只需收盘价; gap_open_open: 还需开盘价(跳空口径)+量比(主力净流入代理)
                        mask = hist['date'] <= today
                        if mask.sum() < 2:
                            continue
                        idx = mask.sum() - 1
                        ind = {'close': float(hist['close'].values[idx]),
                               'prev_close': float(hist['close'].values[idx - 1])}
                        if 'open' in hist.columns:
                            ind['open'] = float(hist['open'].values[idx])
                        if 'volume' in hist.columns:
                            vols = hist['volume'].values.astype(float)[:idx+1]
                            v5 = vols[-5:].mean() if len(vols) >= 5 else vols.mean()
                            ind['vol_ratio'] = float(vols[-1] / v5) if v5 > 0 else 1.0
                    else:
                        mask = hist['date'] <= today
                        if mask.sum() < 60:
                            continue
                        idx = mask.sum() - 1
                        closes = hist['close'].values.astype(float)[:idx+1]
                        volumes_arr = hist['volume'].values.astype(float)[:idx+1] if 'volume' in hist.columns else None
                        highs_arr = hist['high'].values.astype(float)[:idx+1] if 'high' in hist.columns else None
                        lows_arr = hist['low'].values.astype(float)[:idx+1] if 'low' in hist.columns else None

                        if len(closes) < 60:
                            continue

                        ind = compute_indicators(closes, volumes_arr, highs_arr, lows_arr)

                is_buy, score, reason = buy_signal_func(ind)
                entry_type = 'swing'

                if not is_buy and has_rebound:
                    # 短线超跌反弹买点: 日线破BOLL下轨 + 急跌 + 周线超卖
                    prev_close = None
                    if precomputed and code in precomputed and day_idx > 0:
                        pv = precomputed[code].get(trading_dates[day_idx - 1].strftime('%Y-%m-%d'))
                        if pv:
                            prev_close = pv.get('close', 0)
                    is_rebound, rebound_reason = rebound_signal_func(ind, prev_close)
                    if is_rebound:
                        is_buy, score, reason, entry_type = True, 4, rebound_reason, 'rebound'

                # 龙头加分: 综合排名TOP10额外+2分 (code_meta 预构建, 避免循环内 pandas 过滤)
                meta = code_meta.get(code)
                if meta:
                    rank = meta[0]
                    if rank <= 10:
                        score += 2
                        reason += '+龙头TOP10' if reason else '龙头TOP10'

                # 自选池优先级: config watchlist_priority 指定标的评分+n (与其他自选比时优先买入)
                bonus = wpri_map.get(code)
                if bonus:
                    score += bonus
                    reason += f'+优先级{bonus}' if reason else f'优先级{bonus}'

                if score >= min_buy_score:
                    name = meta[1] if meta else ''
                    # 成交价: buy_at_open(米筐模板开盘口径)=当日开盘价, 默认=当日收盘价
                    buy_px = (ind.get('open', ind['close'])
                              if config.get('buy_at_open') else ind['close'])
                    buy_candidates.append((code, name, buy_px, score, reason, entry_type))

            # 按信号强度排序, 买入前 available_slots 个
            buy_candidates.sort(key=lambda x: x[3], reverse=True)
            picks = buy_candidates[:available_slots]

            # 无现金换仓: 有买入候选但现金不足 → 卖出持仓中最高位的 (K>75) 换资金
            # (次日成交模式: 换仓顺延到T+1执行, 见第0b步)
            if not (exec_next_open or exec_next_close or buy_next_close) and picks and cash < min(p[2] for p in picks) * 100:
                pcom = precomputed or {}
                for pos in sorted(positions, key=lambda p: (
                        pcom.get(p.code, {}).get(today_str, {}).get('skdj_k', 0)), reverse=True):
                    pdata = pcom.get(pos.code, {}).get(today_str, {})
                    if pdata.get('skdj_k', 50) > 75:
                        hp = pdata.get('skdj_close', pdata.get('close', 0))
                        cash += pos.shares * hp
                        trades.append(TradeRecord(
                            pos.code, pos.name, 'SELL', hp, today,
                            pos.shares, pos.shares * hp, '高位换仓(K>75)', pos.pnl(hp)
                        ))
                        positions.remove(pos)
                        max_profit_tracker.pop(pos.code, None)
                        death_streak.pop(pos.code, None)
                        break

            # 动态仓位: 单只上限position_pct, 按强弱分比例缩放 (行情弱→分低→轻仓)
            # 已持仓低位标的 → 加仓补足到单只上限; 新标的 → 按强弱分分配
            if exec_next_open or buy_next_open or exec_next_close or buy_next_close:
                # 延迟成交模式: 买入信号挂起, 次日成交价执行 (见第0b步)
                for code, name, buy_price, score, reason, entry_type in picks:
                    pending_buys.append((code, name, score, reason, entry_type, today, buy_price))
                picks = []
            for code, name, buy_price, score, reason, entry_type in picks:
                # 加仓: 已持仓且低位 (补足到单只上限)
                pos = next((p for p in positions if p.code == code), None)
                if pos is not None:
                    total_assets = cash + sum(p.shares * p._last_price if hasattr(p, '_last_price') else p.shares * buy_price for p in positions)
                    cur_value = pos.shares * buy_price
                    target = position_pct * total_assets
                    add_alloc = max(0, min(cash, target - cur_value))
                    if add_alloc < buy_price * 100:
                        continue
                    shares = int(add_alloc / buy_price / 100) * 100
                    if shares <= 0:
                        continue
                    amount = shares * buy_price
                    cash -= amount
                    old_cost = pos.entry_price * pos.shares
                    pos.shares += shares
                    pos.capital += amount
                    pos.entry_price = (old_cost + amount) / pos.shares  # 摊薄成本
                    trades.append(TradeRecord(
                        code, name, 'BUY', buy_price, today, shares, amount,
                        f'低位加仓:{reason}'
                    ))
                    continue

                if kelly_mode:
                    alloc = cash * calc_kelly_fraction()
                elif config.get('full_position', False):
                    alloc = cash * position_pct  # 强制满仓, 不做强弱分缩放
                else:
                    weight = position_pct * min(max(score, 0) / 10.0, 1.0)
                    alloc = cash * weight
                if alloc < buy_price * 100:  # 至少买1手
                    continue
                shares = int(alloc / buy_price / 100) * 100  # 整手
                if shares <= 0:
                    continue
                amount = shares * buy_price
                cash -= amount
                positions.append(Position(code, name, buy_price, today, shares, amount, entry_type))
                trades.append(TradeRecord(
                    code, name, 'BUY', buy_price, today, shares, amount, reason
                ))

        # ---- 2b. 动态龙头发现: 有预计算时每天扫,无预计算时3天扫一次 ----
        available_slots = max_positions - len(positions)
        scan_freq = 1 if precomputed else 3  # 有缓存天天扫,没缓存3天一次
        if strategy not in ('gap_open', 'gap_open_open') and available_slots > 0 and cash > initial_capital * 0.05 and day_idx % scan_freq == 0:
            wildcard_candidates = []
            held_codes = {p.code for p in positions}
            today_str = today.strftime('%Y-%m-%d')

            for code, sina in pure_to_sina.items():
                if code in held_codes or code in candidate_set or strategy in ('watchlist', 'watchlist_weekly'):
                    continue  # 自选轮动模式: 只做自选池, 不动态发现

                # 使用预计算数据 (快)
                if precomputed and code in precomputed:
                    pdata = precomputed[code].get(today_str)
                    if not pdata:
                        continue
                    k = pdata['k']
                    cross = pdata['cross']
                    ma60 = pdata['ma60']
                    ma120 = pdata['ma120']
                    vr = pdata['vol_ratio']
                    close = pdata['close']
                    ret5d = pdata['ret_5d']

                    # 快速预过滤
                    if ret5d > 0.02:
                        continue
                    if ma120 > 0 and close < ma120 * 0.85:
                        continue
                else:
                    # 原始计算 (慢)
                    hist = history_dict.get(sina)
                    if hist is None:
                        continue
                    mask = hist['date'] <= today
                    if mask.sum() < 120:
                        continue
                    idx = mask.sum() - 1
                    c = hist['close'].values.astype(float)[:idx+1]
                    if len(c) < 120:
                        continue
                    if len(c) >= 6 and c[-1] / c[-6] > 1.02:
                        continue
                    if len(c) >= 120 and c[-1] < np.mean(c[-120:]) * 0.85:
                        continue
                    v = hist['volume'].values.astype(float)[:idx+1] if 'volume' in hist.columns else None
                    h = hist['high'].values.astype(float)[:idx+1] if 'high' in hist.columns else None
                    l = hist['low'].values.astype(float)[:idx+1] if 'low' in hist.columns else None
                    ind = compute_indicators(c, v, h, l)
                    k = ind['skdj_k']
                    cross = ind['skdj_cross']
                    ma60 = ind.get('ma60', 0)
                    ma120 = ind.get('ma120', 0)
                    vr = ind.get('vol_ratio', 1.0)
                    close = float(c[-1])

                # 极强信号: SKDJ超卖金叉 + 均线多头 + 缩量
                if (cross == 1 and k < 30
                        and not np.isnan(ma60) and close > ma60
                        and not np.isnan(ma120) and close > ma120
                        and vr < 0.9):
                    nm = ''
                    meta = code_meta.get(code)
                    if meta:
                        nm = meta[1]
                    reason = f'动态龙头:SKDJ金叉(K={k:.0f})+缩量({vr:.1f}x)+均线多头'
                    wildcard_candidates.append((code, nm, close, 10, reason))

            for code, name, buy_price, score, reason in wildcard_candidates[:available_slots]:
                if exec_next_open or buy_next_open or exec_next_close or buy_next_close:
                    pending_buys.append((code, name, score, reason, 'swing', today, buy_price))
                    continue
                alloc = cash * (calc_kelly_fraction() if kelly_mode else position_pct)
                if alloc < buy_price * 100:
                    continue
                shares = int(alloc / buy_price / 100) * 100
                if shares <= 0:
                    continue
                amount = shares * buy_price
                cash -= amount
                positions.append(Position(code, name, buy_price, today, shares, amount))
                trades.append(TradeRecord(
                    code, name, 'BUY', buy_price, today, shares, amount, reason
                ))

        # ---- 3. 记录当日净值 ----
        portfolio_value = cash
        for pos in positions:
            sina = pure_to_sina.get(pos.code)
            if sina:
                hist = history_dict.get(sina)
                if hist is not None:
                    mask = hist['date'] <= today
                    if mask.sum() > 0:
                        idx = mask.sum() - 1
                        portfolio_value += pos.shares * hist['close'].values[idx]
                    else:
                        portfolio_value += pos.capital
            else:
                portfolio_value += pos.capital

        equity_curve.append((today, portfolio_value))

    # ---- 信号账户净值 (系统信号口径: 同一批信号按信号价即时成交) ----
    # 用户A执行账户 = equity_curve (延迟成交); 信号账户 = 信号日按信号价成交的虚拟账户.
    # 两账户共享同一批 trades (交易次数一致), 仅价格/盈亏口径不同:
    #   信号账户 → "系统买卖信号记录" (信号日/信号价/信号口径收益)
    #   执行账户 → "用户A操作记录"  (执行日/执行价/执行口径收益)
    signal_curve = []
    sig_positions = []  # 信号账户最终持仓 [{code, name, shares, cost(摊薄信号价), last_price}]
    if trades:
        sig_codes = {t.code for t in trades}
        sig_closes = {}
        for code in sig_codes:
            pd_map = {}
            # history 原始K线优先 (覆盖完整 — 回测信号可能基于此产生, 而指标缓存precomputed可能缺早期段)
            sina = pure_to_sina.get(code)
            hdf = history_dict.get(sina) if sina else None
            if hdf is not None and 'date' in hdf.columns and 'close' in hdf.columns:
                for d, c in zip(hdf['date'].values, hdf['close'].values):
                    pd_map[pd.Timestamp(d).strftime('%Y-%m-%d')] = float(c)
            # precomputed 补缺 (history 无该code时)
            if precomputed and code in precomputed:
                for dstr, v in precomputed[code].items():
                    if v and v.get('close') and dstr not in pd_map:
                        pd_map[dstr] = float(v['close'])
            sig_closes[code] = pd_map
        trades_by_day = {}
        for t in trades:  # 保持引擎顺序 (同日先卖后买)
            sd = t.signal_date.strftime('%Y-%m-%d')
            trades_by_day.setdefault(sd, []).append(t)
        # 信号账户独立资金管理: 买入按信号账户自身现金满仓 (同日多笔等分),
        # 卖出清仓 — 份额与执行账户不同, 但信号序列一致 (交易次数对得上)
        s_cash = float(initial_capital)
        s_held = {}  # {code: shares}
        s_cost = {}  # {code: 累计投入金额(信号价×份额)} — 摊薄信号成本
        s_peak = {}  # {code: (peak_close, peak_day_idx)} — 持仓期间最高收盘价及日期
        sig_last_close = {}  # {code: 最近有效收盘价} — 缺失日期(停牌/未上市)前向填充
        for day_idx, day in enumerate(trading_dates):
            ds = day.strftime('%Y-%m-%d')
            day_trades = trades_by_day.get(ds, [])
            # 先卖 (清仓, 含高位减仓等部分卖出信号 — 信号账户视作转弱清仓)
            for t in day_trades:
                if t.direction != 'SELL':
                    continue
                sig_px = getattr(t, 'signal_price', None)
                if sig_px is None:
                    sig_px = t.price
                held_shares = s_held.get(t.code, 0)
                # 回撤天数 = 持仓期间最高收盘价日 → 卖出信号日的交易日数 (卖出日创新高则0)
                pk = s_peak.get(t.code)
                if pk is None or sig_px >= pk[0]:
                    t.drawdown_days = 0
                else:
                    t.drawdown_days = max(0, day_idx - pk[1])
                s_cash += held_shares * sig_px
                s_held.pop(t.code, None)
                s_cost.pop(t.code, None)
                s_peak.pop(t.code, None)
                t.signal_shares = held_shares  # 信号账户实际卖出份额
            # 后买 (满仓, 同日多笔等分现金)
            buys = [t for t in day_trades if t.direction == 'BUY']
            if buys:
                per_alloc = s_cash / len(buys)
                for t in buys:
                    sig_px = getattr(t, 'signal_price', None)
                    if sig_px is None:
                        sig_px = t.price
                    if sig_px <= 0:
                        t.signal_shares = 0
                        continue
                    shares = int(per_alloc / sig_px / 100) * 100
                    if shares > 0:
                        s_cash -= shares * sig_px
                        s_held[t.code] = s_held.get(t.code, 0) + shares
                        s_cost[t.code] = s_cost.get(t.code, 0) + shares * sig_px
                        s_peak[t.code] = (sig_px, day_idx)  # 持仓峰值起点 = 买入信号价
                    t.signal_shares = shares
            # 当日估值 + 持仓峰值更新
            mv = 0
            for cd, sh in s_held.items():
                c = sig_closes.get(cd, {}).get(ds)
                if c is not None:
                    sig_last_close[cd] = c
                    pk = s_peak.get(cd)
                    if pk is None or c >= pk[0]:
                        s_peak[cd] = (c, day_idx)
                mv += sh * sig_last_close.get(cd, 0)
            signal_curve.append((day, s_cash + mv))
        # 信号账户最终持仓 (信号价摊薄成本 + 最新收盘价) — 供信号口径报告的"当前持仓"区块
        for code, sh in s_held.items():
            cost_avg = (s_cost.get(code, 0) / sh) if sh else 0
            nm = ''
            meta = code_meta.get(code)
            if meta:
                nm = meta[1]
            sig_positions.append({
                'code': code, 'name': nm, 'shares': sh,
                'cost': cost_avg, 'last_price': sig_last_close.get(code, 0),
            })
    else:
        signal_curve = list(equity_curve)

    # ---- 统计 ----
    final_value = equity_curve[-1][1] if equity_curve else initial_capital
    total_return = (final_value / initial_capital - 1)
    trading_days = len(trading_dates)
    annual_return = (1 + total_return) ** (252 / max(trading_days, 1)) - 1

    sell_trades = [t for t in trades if t.direction == 'SELL']
    win_trades = [t for t in sell_trades if t.pnl_pct > 0]
    win_rate = len(win_trades) / len(sell_trades) if sell_trades else 0

    avg_win = np.mean([t.pnl_pct for t in win_trades]) if win_trades else 0
    loss_trades = [t for t in sell_trades if t.pnl_pct <= 0]
    avg_loss = np.mean([t.pnl_pct for t in loss_trades]) if loss_trades else 0

    # Sharpe
    if len(equity_curve) > 1:
        daily_rets = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i-1][1]
            curr = equity_curve[i][1]
            if prev > 0:
                daily_rets.append(curr / prev - 1)
        if daily_rets and np.std(daily_rets) > 0:
            sharpe = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252)
        else:
            sharpe = 0
        # 最大回撤
        values = [e[1] for e in equity_curve]
        peaks = np.maximum.accumulate(values)
        drawdowns = (peaks - values) / peaks
        max_drawdown = np.max(drawdowns)
        # 最大回撤天数 = 净值从峰值回落的最长持续交易日数 (创新高重置)
        max_dd_days = 0
        pk_idx = 0
        for i in range(1, len(values)):
            if values[i] >= values[pk_idx]:
                pk_idx = i
            elif i - pk_idx > max_dd_days:
                max_dd_days = i - pk_idx
    else:
        sharpe = 0
        max_drawdown = 0
        max_dd_days = 0

    stats = {
        'initial_capital': initial_capital,
        'final_value': final_value,
        'total_return': total_return,
        'annual_return': annual_return,
        'sharpe': sharpe,
        'max_drawdown': max_drawdown,
        'max_drawdown_days': max_dd_days,
        'total_trades': len(sell_trades),
        'win_rate': win_rate,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'profit_loss_ratio': abs(avg_win / avg_loss) if avg_loss != 0 else 0,
        'trading_days': trading_days,
        'max_positions': max_positions,
    }

    # 信号账户统计 (口径与执行账户一致, 胜率/盈亏按信号口径 pnl_signal)
    sig_final = signal_curve[-1][1] if signal_curve else initial_capital
    sig_total = (sig_final / initial_capital - 1)
    sig_annual = (1 + sig_total) ** (252 / max(trading_days, 1)) - 1
    sig_sell_pnls = []
    for t in trades:
        if t.direction == 'SELL':
            p = getattr(t, 'pnl_signal', None)
            if p is None:
                p = t.pnl_pct
            sig_sell_pnls.append(p)
    sig_wins = [p for p in sig_sell_pnls if p > 0]
    sig_losses = [p for p in sig_sell_pnls if p <= 0]
    if len(signal_curve) > 1:
        sig_rets = []
        for i in range(1, len(signal_curve)):
            prev = signal_curve[i - 1][1]
            curr = signal_curve[i][1]
            if prev > 0:
                sig_rets.append(curr / prev - 1)
        if sig_rets and np.std(sig_rets) > 0:
            sig_sharpe = np.mean(sig_rets) / np.std(sig_rets) * np.sqrt(252)
        else:
            sig_sharpe = 0
        sig_vals = [e[1] for e in signal_curve]
        sig_peaks = np.maximum.accumulate(sig_vals)
        sig_drawdowns = (sig_peaks - sig_vals) / sig_peaks
        sig_max_dd = np.max(sig_drawdowns)
        sig_max_dd_days = 0
        sig_pk = 0
        for i in range(1, len(sig_vals)):
            if sig_vals[i] >= sig_vals[sig_pk]:
                sig_pk = i
            elif i - sig_pk > sig_max_dd_days:
                sig_max_dd_days = i - sig_pk
    else:
        sig_sharpe = 0
        sig_max_dd = 0
        sig_max_dd_days = 0
    signal_stats = {
        'initial_capital': initial_capital,
        'final_value': sig_final,
        'total_return': sig_total,
        'annual_return': sig_annual,
        'sharpe': sig_sharpe,
        'max_drawdown': sig_max_dd,
        'max_drawdown_days': sig_max_dd_days,
        'total_trades': len(sig_sell_pnls),
        'win_rate': len(sig_wins) / len(sig_sell_pnls) if sig_sell_pnls else 0,
        'avg_win': np.mean(sig_wins) if sig_wins else 0,
        'avg_loss': np.mean(sig_losses) if sig_losses else 0,
        'profit_loss_ratio': abs(np.mean(sig_wins) / np.mean(sig_losses)) if sig_wins and sig_losses and np.mean(sig_losses) != 0 else 0,
        'trading_days': trading_days,
        'max_positions': max_positions,
    }

    # 给持仓补充最新价格
    for pos in positions:
        sina = pure_to_sina.get(pos.code)
        if sina:
            hist = history_dict.get(sina)
            if hist is not None:
                last_mask = hist['date'] <= trading_dates[-1]
                if last_mask.sum() > 0:
                    pos._last_price = hist['close'].values[last_mask.sum() - 1]
                else:
                    pos._last_price = pos.entry_price
            else:
                pos._last_price = pos.entry_price
        else:
            pos._last_price = pos.entry_price

    return {
        'trades': trades,
        'equity_curve': equity_curve,
        'stats': stats,
        'signal_equity_curve': signal_curve,
        'signal_stats': signal_stats,
        'final_positions': positions,
        'signal_positions': sig_positions,
        'initial_capital': initial_capital,
    }


# ============================================================
# 输出
# ============================================================

def print_trade_summary(result):
    """打印交易摘要"""
    if result is None:
        return

    stats = result['stats']
    trades = result['trades']

    print("\n" + "═" * 70)
    print("  📊 波段交易回测结果")
    print("═" * 70)
    print(f"  初始资金:   ¥{stats['initial_capital']:>12,.0f}")
    print(f"  最终资金:   ¥{stats['final_value']:>12,.0f}")
    print(f"  总收益率:   {stats['total_return']:>+12.2%}")
    print(f"  年化收益率: {stats['annual_return']:>+12.2%}")
    print(f"  Sharpe:     {stats['sharpe']:>12.2f}")
    print(f"  最大回撤:   {stats['max_drawdown']:>12.1%}")
    print(f"  交易次数:   {stats['total_trades']:>12d} 笔")
    print(f"  胜率:       {stats['win_rate']:>12.1%}")
    print(f"  平均盈利:   {stats['avg_win']:>+12.2%}")
    if stats['avg_loss'] == 0:
        print(f"  平均亏损:   {'           -'}")
        print(f"  盈亏比:     {'           ∞'}")
    else:
        print(f"  平均亏损:   {stats['avg_loss']:>+12.2%}")
        print(f"  盈亏比:     {stats['profit_loss_ratio']:>12.2f}")
    print(f"  交易天数:   {stats['trading_days']:>12d} 天")
    print("═" * 70)

    # 操作记录 (含累计收益+总市值+仓位)
    print(f"\n  📋 操作记录 (共 {len(trades)} 笔):\n")
    print(f"  {'日期':>12} {'方向':>4} {'代码':<8} {'名称':<8} "
          f"{'价格':>8} {'数量':>6} {'金额':>10} {'盈亏':>7} {'累计':>7} {'总市值':>10} {'仓位':>6}  原因")
    print("  " + "─" * 115)

    # 构建日期→净值映射
    date_nav = {}
    initial = result['initial_capital']
    for d, v in result.get('equity_curve', []):
        date_nav[pd.Timestamp(d).strftime('%Y-%m-%d')] = v

    # 跟踪持仓数以计算仓位
    current_held = {}  # {code: (shares, entry_price)}

    for t in trades:
        dir_label = '🟢买入' if t.direction == 'BUY' else '🔴卖出'
        pnl_str = f'{t.pnl_pct:+.1%}' if t.direction == 'SELL' else ''

        # 更新持仓跟踪 (使用当前市场价格)
        if t.direction == 'BUY':
            current_held[t.code] = (t.shares, t.price)  # 买入时使用交易价格
        else:
            current_held.pop(t.code, None)

        # 当前持仓市值 (使用当前市场价格)
        # 对于买入交易，新仓位使用交易价格
        # 对于卖出交易，剩余仓位使用上一次的市场价格
        held_value = 0
        for code, (shares, last_price) in current_held.items():
            held_value += shares * last_price

        # 总市值 = equity_curve的值 (包含现金+持仓市值)
        d_str = t.date.strftime('%Y-%m-%d')
        total_value = date_nav.get(d_str, initial)

        # 仓位 = 持仓市值 / 总市值
        position_ratio = held_value / total_value if total_value > 0 else 0

        # 累计收益 = (总市值 / 初始资金) - 1
        cum_ret = (total_value / initial - 1) if initial > 0 else 0
        cum_str = f'{cum_ret:+.1%}'
        pos_str = f'{position_ratio:.0%}'

        print(f"  {d_str:>12} {dir_label:>4} {t.code:<8} {t.name:<8} "
              f"{fmt_px(t.price):>8} {t.shares:>6d} {t.amount:>10,.0f} {pnl_str:>7} {cum_str:>7} "
              f"¥{total_value:>8,.0f} {pos_str:>5}  {t.reason}")

    # 当前持仓 (含浮动收益)
    if result['final_positions']:
        print(f"\n  📦 当前持仓 ({len(result['final_positions'])} 只):")
        print(f"    {'代码':<8} {'名称':<8} {'成本':>8} {'现价':>8} {'持股':>6} {'浮动盈亏':>10} {'盈亏%':>8} {'投入':>12}")
        print("    " + "─" * 80)
        total_float_pnl = 0
        for pos in result['final_positions']:
            current_price = getattr(pos, '_last_price', pos.entry_price)
            float_pnl = (current_price - pos.entry_price) * pos.shares
            float_pct = (current_price / pos.entry_price - 1) if pos.entry_price > 0 else 0
            pnl_css = '+' if float_pct >= 0 else ''
            total_float_pnl += float_pnl
            print(f"    {pos.code:<8} {pos.name:<8} {fmt_px(pos.entry_price):>8} {fmt_px(current_price):>8} "
                  f"{pos.shares:>6d} {pnl_css}{float_pnl:>9,.0f} {pnl_css}{float_pct:>7.1%} ¥{pos.capital:>10,.0f}")
        print("    " + "─" * 80)
        total_css = '+' if total_float_pnl >= 0 else ''
        print(f"    {'合计浮动盈亏:':>34} {total_css}¥{total_float_pnl:,.0f}")

    print("\n  ⚠️  回测基于历史数据，不代表未来表现。投资有风险，入市需谨慎。")
    print()


def generate_equity_chart(result, scope='signal'):
    """生成收益曲线图 (ECharts) — 净值+回撤+买卖点
    scope='signal': 信号账户口径 (信号日/信号价成交, 系统买卖信号) — 默认
    scope='exec':   执行账户口径 (实际执行日/执行价成交, 用户A操作)
    """
    if result is None:
        return ''
    if scope == 'exec':
        eq_curve = result.get('equity_curve') or result.get('signal_equity_curve') or []
        st = result.get('stats') or result.get('signal_stats') or {}
    else:
        eq_curve = result.get('signal_equity_curve') or result.get('equity_curve') or []
        st = result.get('signal_stats') or result.get('stats') or {}
    if not eq_curve:
        return ''

    from report_echarts import echarts_script, UP, DOWN, GRID, TEXT, BLUE, GRAY

    dates = [pd.Timestamp(e[0]).strftime('%Y-%m-%d') for e in eq_curve]
    values = [e[1] for e in eq_curve]
    initial = result['initial_capital']
    stats = st

    nav = [v / initial for v in values]
    peaks = np.maximum.accumulate(nav)
    drawdowns = [-((p - n) / p * 100) for p, n in zip(peaks, nav)]

    final_ret = stats['total_return']
    line_color = UP if final_ret >= 0 else DOWN

    # 买卖点 (按口径: 信号账户标记于信号日/信号价, 执行账户标记于执行日/执行价)
    buy_pts, sell_pts = [], []
    for t in result['trades']:
        if scope == 'exec':
            d = pd.Timestamp(t.date).strftime('%Y-%m-%d')
            px = t.price
        else:
            d = pd.Timestamp(getattr(t, 'signal_date', None) or t.date).strftime('%Y-%m-%d')
            px = getattr(t, 'signal_price', None) or t.price
        if d in dates:
            idx = dates.index(d)
            if t.direction == 'BUY':
                buy_pts.append({'name': '买入', 'value': [d, round(nav[idx], 4)],
                                'code': t.code, 'price': px, 'reason': t.reason})
            else:
                if scope == 'exec':
                    pnl_sig = t.pnl_pct
                else:
                    pnl_sig = getattr(t, 'pnl_signal', None)
                    if pnl_sig is None:
                        pnl_sig = t.pnl_pct
                sell_pts.append({'name': '卖出', 'value': [d, round(nav[idx], 4)],
                                 'code': t.code, 'price': px, 'pnl': round(pnl_sig * 100, 1),
                                 'reason': t.reason})

    option = {
        'tooltip': {
            'trigger': 'axis',
            'backgroundColor': '#fff', 'borderColor': '#e5e8ec', 'textStyle': {'color': '#1f2329'},
            'formatter': "function(ps){var p=ps[0];var s='<b>'+p.axisValue+'</b>';"
                         "ps.forEach(function(x){if(x.seriesName.indexOf('净值')>=0)s+='<br/>净值: '+x.value.toFixed(3);"
                         "if(x.seriesName==='买入')s+='<br/>🟢 买入 '+x.data.code+' @'+x.data.price;"
                         "if(x.seriesName==='卖出')s+='<br/>🔴 卖出 '+x.data.code+' @'+x.data.price+' ('+x.data.pnl+'%)';});return s;}",
        },
        'legend': {'top': 0, 'textStyle': {'color': TEXT}},
        'grid': [
            {'left': 55, 'right': 20, 'top': 40, 'bottom': 90, 'height': '58%'},
            {'left': 55, 'right': 20, 'top': '78%', 'bottom': 40, 'height': '14%'},
        ],
        'xAxis': [
            {'type': 'category', 'data': dates, 'axisLine': {'lineStyle': {'color': '#d9dde3'}},
             'axisLabel': {'color': TEXT}, 'boundaryGap': False},
            {'type': 'category', 'data': dates, 'axisLine': {'lineStyle': {'color': '#d9dde3'}},
             'axisLabel': {'color': TEXT, 'show': False}, 'gridIndex': 1, 'boundaryGap': False},
        ],
        'yAxis': [
            {'type': 'value', 'scale': True, 'splitLine': {'lineStyle': {'color': GRID}},
             'axisLabel': {'color': TEXT, 'formatter': 'function(v){return v.toFixed(2);}'}},
            {'type': 'value', 'scale': True, 'splitLine': {'lineStyle': {'color': GRID}},
             'axisLabel': {'color': TEXT, 'formatter': 'function(v){return v + "%";}'}, 'gridIndex': 1},
        ],
        'dataZoom': [
            {'type': 'inside', 'xAxisIndex': [0, 1]},
            {'type': 'slider', 'xAxisIndex': [0, 1], 'height': 16, 'bottom': 10},
        ],
        'series': [
            {'name': '策略净值', 'type': 'line', 'data': nav, 'showSymbol': False, 'smooth': True,
             'lineStyle': {'color': line_color, 'width': 2.5},
             'areaStyle': {'color': 'rgba(232,64,58,0.08)' if final_ret >= 0 else 'rgba(27,162,122,0.08)'}},
            {'name': '买入', 'type': 'scatter', 'data': buy_pts, 'symbol': 'triangle', 'symbolSize': 11,
             'itemStyle': {'color': DOWN, 'borderColor': '#fff', 'borderWidth': 1}},
            {'name': '卖出', 'type': 'scatter', 'data': sell_pts, 'symbol': 'triangle', 'symbolRotate': 180,
             'symbolSize': 11, 'itemStyle': {'color': UP, 'borderColor': '#fff', 'borderWidth': 1}},
            {'name': '回撤', 'type': 'line', 'data': drawdowns, 'xAxisIndex': 1, 'yAxisIndex': 1,
             'showSymbol': False, 'lineStyle': {'color': UP, 'width': 1},
             'areaStyle': {'color': 'rgba(232,64,58,0.15)'}},
        ],
    }
    return echarts_script('equity_chart', option, 480)


def backtest_single_stock(code, history_dict, config, start_date_str='2025-01-01',
                          end_date_str='2026-07-27'):
    """
    回测单只股票 (个股专项分析)

    用法: python main.py --stock 601857  (回测中国石油)
    """
    from data_fetcher import _code_pure

    # 查找sina代码
    sina_code = None
    code = str(code).zfill(6)
    for s in history_dict:
        if _code_pure(s) == code:
            sina_code = s
            break

    if not sina_code:
        print(f"  ✗ 未找到股票 {code} 的历史数据")
        return None

    hist = history_dict[sina_code]
    if hist is None or 'date' not in hist.columns:
        print(f"  ✗ 股票 {code} 无有效K线数据")
        return None

    # 构建单只股票的precomputed
    from indicator_cache import _incremental_indicators
    import pandas as pd

    start_date = pd.Timestamp(start_date_str)
    end_date = pd.Timestamp(end_date_str)

    dates_arr = hist['date'].values
    all_dates = set()
    for d in dates_arr:
        ts = pd.Timestamp(d)
        if start_date <= ts <= end_date:
            all_dates.add(ts.strftime('%Y-%m-%d'))

    closes = hist['close'].values.astype(float)
    volumes = hist['volume'].values.astype(float) if 'volume' in hist.columns else None
    highs = hist['high'].values.astype(float) if 'high' in hist.columns else None
    lows = hist['low'].values.astype(float) if 'low' in hist.columns else None
    if highs is None: highs = closes.copy()
    if lows is None: lows = closes.copy()

    precomputed_data = _incremental_indicators(closes, volumes, highs, lows, dates_arr, all_dates)

    # 用run_swing_backtest但只传入这一只股票
    pure_to_sina = {code: sina_code}
    single_history = {sina_code: hist}

    # 构建单只股票的scored_df
    import pandas as pd
    scored_df = pd.DataFrame([{
        'code': code,
        'name': code,
        'rank': 1,
        'momentum_score': 0,
        'composite_score': 1.0,
    }])

    # 获取股票名称
    try:
        from data_fetcher import fetch_spot_data
        spot = fetch_spot_data('cache', True)
        if spot is not None:
            from data_fetcher import _find_column
            code_col = _find_column(spot, ['代码'])
            name_col = _find_column(spot, ['名称'])
            if code_col and name_col:
                for _, row in spot.iterrows():
                    if _code_pure(str(row[code_col])) == code:
                        scored_df.loc[0, 'name'] = str(row[name_col])
                        break
    except Exception:
        pass

    name = scored_df.loc[0, 'name']
    print(f"\n  📊 个股回测: {code} {name}")
    print(f"  区间: {start_date_str} ~ {end_date_str}")

    # 个股回测: 100%仓位 (关闭凯利, 个股模式满仓)
    config_single = dict(config)
    config_single['position_pct'] = 1.0
    config_single['max_positions'] = 1
    config_single['kelly_mode'] = False

    result = run_swing_backtest(
        single_history, scored_df, config_single,
        start_date_str, end_date_str,
        precomputed={code: precomputed_data},
    )

    return result


"""
vnpy BaseDatabase 适配层：直接以 cache/*.pkl 历史喂给 vnpy 回测引擎，免数据库迁移。

用法（在引擎 load_data 之前）：
    from vnpy.trader.database import database as _db_mod
    _db_mod.database = QuantCacheDatabase({vt_symbol: df})
    engine.load_data()   # 内部 get_database() 返回上面的实例
"""
from __future__ import annotations

from datetime import datetime, time

import pandas as pd
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import BarOverview, BaseDatabase, TickOverview
from vnpy.trader.object import BarData, TickData

from .adapters import bars_from_df
from .symbols import vt_to_parts


class QuantCacheDatabase(BaseDatabase):
    """内存版数据库：{vt_symbol: DataFrame} → load_bar_data。仅支持日线读。"""

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self._frames: dict[str, pd.DataFrame] = {k: v for k, v in frames.items()}

    # -- 注册到全局（免改 SETTINGS / 免 vnpy_sqlite）--
    @staticmethod
    def install(frames: dict[str, pd.DataFrame]) -> "QuantCacheDatabase":
        import vnpy.trader.database as db_mod
        inst = QuantCacheDatabase(frames)
        db_mod.database = inst
        return inst

    # -- 读接口 --
    def load_bar_data(self, symbol: str, exchange: Exchange, interval: Interval,
                      start: datetime, end: datetime) -> list[BarData]:
        if interval != Interval.DAILY:
            return []
        vt = f"{symbol}.{exchange.value}"
        df = self._frames.get(vt)
        if df is None or df.empty:
            return []

        def _day(dt: datetime):
            return dt.date()

        mask = (df["date"].dt.date >= _day(start)) & (df["date"].dt.date <= _day(end))
        sub = df.loc[mask]
        return bars_from_df(vt, sub)

    # -- 写/管理接口：内存态，save 视为成功但不持久化 --
    def save_bar_data(self, bars: list[BarData], stream: bool = False) -> bool:
        return True

    def save_tick_data(self, ticks: list[TickData], stream: bool = False) -> bool:
        return True

    def delete_bar_data(self, symbol: str, exchange: Exchange,
                        interval: Interval) -> int:
        return 0

    def delete_tick_data(self, symbol: str, exchange: Exchange) -> int:
        return 0

    def load_tick_data(self, symbol: str, exchange: Exchange,
                       start: datetime, end: datetime) -> list[TickData]:
        return []

    def get_bar_overview(self) -> list[BarOverview]:
        out = []
        for vt, df in self._frames.items():
            symbol, exchange = vt_to_parts(vt)
            if df is None or df.empty:
                continue
            d0 = df["date"].iloc[0]
            d1 = df["date"].iloc[-1]
            out.append(BarOverview(
                symbol=symbol, exchange=exchange, interval=Interval.DAILY,
                count=len(df),
                start=_to_dt(d0), end=_to_dt(d1),
            ))
        return out

    def get_tick_overview(self) -> list[TickOverview]:
        return []


def _to_dt(x) -> datetime:
    ts = pd.Timestamp(x)
    return datetime.combine(ts.date(), time.min)

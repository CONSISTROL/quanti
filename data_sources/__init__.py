"""
数据源注册表 — 通过 config.json 的 data.source 选择数据获取方式

可选数据源:
  quantdash  QuantDash API (个股/ETF前复权, 正确处理份额折算) + 腾讯LOF回退 [默认]
  sina      新浪/AkShare (个股V8 + 新浪ETF) + 腾讯LOF回退 (ETF不复权, 有折算断层)

返回格式统一: {sh601857: DataFrame(date/open/high/low/close/volume/amount), ...}
"""
from .base import BaseDataSource
from .quantdash import QuantDashDataSource
from .sina import SinaDataSource

DATA_SOURCES = {
    'quantdash': QuantDashDataSource,
    'sina': SinaDataSource,
}


def get_data_source(name=None):
    """按名称获取数据源实例"""
    if name is None:
        name = 'quantdash'
    cls = DATA_SOURCES.get(name)
    if cls is None:
        raise ValueError(f'未知数据源: {name}, 可选: {", ".join(DATA_SOURCES)}')
    return cls()


def data_source_label(name):
    """获取数据源显示名称"""
    cls = DATA_SOURCES.get(name)
    return cls.label if cls else name

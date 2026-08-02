"""
数据源基类 — 统一自选池数据获取接口

各数据源通过 config.json 的 data.source 选择:
  quantdash  QuantDash API (个股/ETF前复权) + 腾讯LOF回退 [默认]
  sina      新浪/AkShare (个股V8前复权 + 新浪ETF + 腾讯LOF)

返回格式统一: {sh601857: DataFrame(date/open/high/low/close/volume/amount), ...}
"""


class BaseDataSource:
    name = 'base'
    label = '基础数据源'
    supports_scan = False  # 是否支持全市场扫描 (仅 sina 支持)

    def fetch_watchlist_data(self, codes, cache_dir='cache',
                             use_cache=True, max_bars=1200):
        """
        拉取自选池历史数据 (统一入口)

        codes: 纯6位代码列表, 如 ['601857', '159941', '160723']
        返回: {sh601857: DataFrame(date/open/high/low/close/volume/amount), ...}
        """
        raise NotImplementedError

"""vnpy_quanti — 基于 vnpy 4.4 生态重构的 Quanti 交易内核（新包，独立 venv 运行）。

设计见仓库根目录 REFACTOR_TO_VNPY.md：
- 策略判定逻辑与指标计算单源复用 legacy 包 quantlab（仅 numpy/pandas/tqdm 依赖）；
- vnpy 承担标准策略模板/回测引擎/事件对象/数据库接口；
- 旧系统（quantlab/tests/Web）保持零改动可回退。
"""

__version__ = "0.1.0"

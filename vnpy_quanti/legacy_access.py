"""
legacy quantlab 访问桥：确保仓库根目录可 import，并复用其纯指标函数。

策略判定/指标与旧系统单源（量化一致性），仅依赖 numpy/pandas/tqdm。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_ensured = False


def ensure_legacy_importable() -> None:
    """把仓库根目录加入 sys.path（幂等），使 'quantlab.*' 可导入。"""
    global _ensured
    if _ensured:
        return
    root = os.fspath(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    _ensured = True

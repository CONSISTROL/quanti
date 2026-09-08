"""python -m vnpy_quanti — 入口分发。"""
from __future__ import annotations

import sys


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print("用法: python -m vnpy_quanti <stock|compare|genref> ...")
        print("  python -m vnpy_quanti stock --stock 601857 --strategy reversal")
        print("  python -m vnpy_quanti compare --new new.json --legacy legacy.json")
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "stock":
        from .backtest import main as m
        return m(rest)
    if cmd == "compare":
        from .compare import main as m
        return m(rest)
    if cmd == "genref":
        print("genref 需在 legacy venv(.venv) 下运行: python vnpy_quanti/tests/gen_legacy_ref.py")
        return 0
    print(f"未知子命令: {cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())

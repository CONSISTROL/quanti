"""
测试运行器 — 运行 config.json test.module 指定的测试模块

用法:
  python run_test.py                          # 运行 config 指定的测试
  python run_test.py --module run_20_stocks   # 覆盖指定测试
  python run_test.py --list                   # 列出可用测试

config.json:
  "test": {
      "module": "verify_signals",   // tests/ 下要运行的模块名
      "stock": "601857",            // 测试股票 (verify_signals 等使用)
      "strategy": "reversal"        // 测试策略 (verify_signals/run_20_stocks 使用)
  }
"""
import sys
import os
import importlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json

# 兼容 Windows GBK 控制台，避免 ▶/中文等字符触发 UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass


def list_tests():
    """列出 tests/ 下所有可用测试模块"""
    import tests
    import pkgutil
    print('可用测试模块 (tests/):')
    for m in pkgutil.iter_modules(tests.__path__):
        mod = importlib.import_module(f'tests.{m.name}')
        doc = (mod.__doc__ or '').strip().split('\n')[0]
        print(f'  {m.name:<20} {doc}')


def main():
    config = json.load(open('config.json', encoding='utf-8'))
    test_cfg = config.get('test', {})
    module = test_cfg.get('module', 'verify_signals')

    if '--list' in sys.argv:
        list_tests()
        return

    for i, arg in enumerate(sys.argv):
        if arg == '--module' and i + 1 < len(sys.argv):
            module = sys.argv[i + 1]
        elif arg.startswith('--module='):
            module = arg.split('=')[1]

    try:
        mod = importlib.import_module(f'tests.{module}')
    except ImportError as e:
        print(f'  ✗ 无法导入测试模块 tests.{module}: {e}')
        list_tests()
        sys.exit(1)

    print(f'▶ 运行测试: tests/{module}.py')
    print('─' * 60)
    rc = mod.main(config)
    print('─' * 60)
    print(f'✔ 测试完成: tests/{module}.py')
    sys.exit(rc or 0)


if __name__ == '__main__':
    main()

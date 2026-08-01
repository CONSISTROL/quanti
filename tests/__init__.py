"""
测试套件 — 通过 config.json 的 test.module 选择要运行的测试

可选测试模块 (tests/ 下):
  verify_signals    7个用户信号验证 (用指定策略在指定股票上验证买卖信号命中)
  backtest_user     用户波段策略回测 (周KDJ金叉/死叉 + 日线RSI/BOLL, 3只股票)
  grid_search_ref   参考策略参数网格搜索
  grid_search_hybrid 混合策略参数搜索
  run_20_stocks     20支股票批量回测 (按config策略)

运行方式:
  python run_test.py                    # 运行 config.json test.module 指定的测试
  python run_test.py --module run_20_stocks  # 覆盖指定测试
"""

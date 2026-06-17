#!/usr/bin/env python3
"""
查看系统学习进度
用法: python3 tools/check_learning.py
"""
import sys
import json

sys.path.insert(0, "/mnt/c/Users/朱/Desktop/hexagon_copilot/mm_tracker")

from trading.pattern_analyzer import PatternAnalyzer
from trading.meta_optimizer import MetaOptimizer

analyzer = PatternAnalyzer()
stats = analyzer.overall_stats()

print(f"\n已积累交易: {stats['total_trades']} 笔")
print(f"当前胜率: {stats['win_rate']*100:.1f}%")
print(f"均赢: {stats['avg_win_pct']:.1f}% | 均亏: {stats['avg_loss_pct']:.1f}%")
print(f"期望收益/笔: {stats['expected_value_per_trade']:.2f}%")

if stats['total_trades'] >= 10:
    print(analyzer.generate_report())

# 查看参数历史
try:
    with open("config/optimization_log.json") as f:
        log = json.load(f)
    print(f"\n已执行优化: {len(log)} 次")
    if log:
        last = log[-1]
        print(f"上次优化: {last['timestamp'][:16]}, 调整了{len(last['changes'])}个参数")
        print("调整详情:")
        for c in last['changes']:
            print(f"  - {c['type']}: {c.get('signal', c.get('old', ''))} {c.get('old', '')} -> {c.get('new', '')}")
except FileNotFoundError:
    print("\n尚未执行任何自动优化（需要20笔交易后触发）")
except Exception as e:
    print(f"\n读取优化日志失败: {e}")

# 查看当前学习统计
try:
    with open("config/strategy_params.json") as f:
        params = json.load(f)
    learning = params.get("learning_stats", {})
    if learning:
        print(f"\n【学习统计】")
        print(f"  优化次数: {learning.get('optimization_count', 0)}")
        print(f"  上次优化: {learning.get('last_optimized', 'N/A')[:16]}")
        print(f"  分析交易数: {learning.get('total_trades_analyzed', 0)}")
except:
    pass
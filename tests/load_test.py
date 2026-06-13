"""
MMTracker 压力测试脚本
用于验证系统在高并发场景下的性能与稳定性
"""

import time
import sys
import os
import argparse

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_pressure_test(tokens: int = 30, verbose: bool = False):
    """运行压力测试"""
    from scanner.universe import get_full_universe
    from scanner.deep_scan import deep_scan_batch
    from scanner.fast_filter import run_fast_filter
    from fetchers.utils import get_stats, get_health_report, health_monitor
    
    print("=" * 60)
    print("🚀 MMTracker 压力测试")
    print("=" * 60)
    print(f"测试规模: {tokens} 个代币")
    print()
    
    # 记录开始状态
    start_stats = get_stats()
    start_time = time.time()
    
    # Step 1: 获取候选列表
    print("📋 Step 1: 获取市场代币列表...")
    universe = get_full_universe()
    print(f"  市场规模: {len(universe)} 代币")
    
    # Step 2: 快速筛选
    print("\n🔍 Step 2: 快速筛选...")
    candidates = run_fast_filter(universe)
    candidates = candidates[:tokens]
    print(f"  筛选后: {len(candidates)} 代币")
    
    # Step 3: 深度扫描
    print(f"\n⚡ Step 3: 深度扫描 {len(candidates)} 个代币...")
    scan_start = time.time()
    results = deep_scan_batch(candidates)
    scan_end = time.time()
    
    # 计算结果
    elapsed = scan_end - start_time
    scan_elapsed = scan_end - scan_start
    
    # 获取最终统计
    end_stats = get_stats()
    
    print("\n" + "=" * 60)
    print("📊 测试结果")
    print("=" * 60)
    print(f"总耗时: {elapsed:.1f}秒")
    print(f"扫描耗时: {scan_elapsed:.1f}秒")
    print(f"平均每币: {scan_elapsed/len(candidates):.2f}秒")
    print(f"吞吐量: {len(candidates)/scan_elapsed:.2f} tokens/sec")
    
    print("\n📈 性能指标:")
    print(f"  总请求数: {end_stats['total_requests']}")
    print(f"  缓存命中: {end_stats['cache_hits']}")
    print(f"  缓存未命中: {end_stats['cache_misses']}")
    cache_total = end_stats['cache_hits'] + end_stats['cache_misses']
    cache_rate = end_stats['cache_hits'] / cache_total * 100 if cache_total > 0 else 0
    print(f"  缓存命中率: {cache_rate:.1f}%")
    print(f"  429限流: {end_stats['429_hits']}")
    print(f"  错误数: {end_stats['errors']}")
    print(f"  平均延迟: {end_stats['avg_latency_ms']:.1f}ms")
    print(f"  最大延迟: {end_stats['max_latency_ms']:.1f}ms")
    
    # 信号统计
    print("\n🎯 信号触发统计:")
    signal_counts = {}
    for r in results:
        score = r.get("score", {})
        count = score.get("triggered_count", 0)
        signal_counts[count] = signal_counts.get(count, 0) + 1
    
    for count in sorted(signal_counts.keys(), reverse=True)[:5]:
        print(f"  {count} 信号: {signal_counts[count]} 个代币")
    
    # Top 结果
    sorted_results = sorted(
        results, 
        key=lambda x: x.get("score", {}).get("triggered_count", 0), 
        reverse=True
    )[:5]
    
    print("\n🏆 Top 5 代币:")
    for i, r in enumerate(sorted_results, 1):
        s = r.get("score", {})
        print(f"  {i}. {r['symbol']}: {s.get('triggered_count', 0)}/11 信号, 评分 {s.get('total_score', 0):.1f}")
    
    # 健康报告
    if verbose:
        print("\n" + "=" * 60)
        print(get_health_report())
    
    # 验收判断
    print("\n" + "=" * 60)
    print("✅ 验收结果")
    print("=" * 60)
    
    success = True
    
    # 性能基线: 30 tokens / 30-60 秒
    if scan_elapsed > 60:
        print(f"❌ 性能基线未达标: {scan_elapsed:.1f}s > 60s")
        success = False
    else:
        print(f"✅ 性能基线达标: {scan_elapsed:.1f}s < 60s")
    
    # 错误率
    error_rate = end_stats['errors'] / end_stats['total_requests'] * 100 if end_stats['total_requests'] > 0 else 0
    if error_rate > 10:
        print(f"❌ 错误率过高: {error_rate:.1f}%")
        success = False
    else:
        print(f"✅ 错误率正常: {error_rate:.1f}%")
    
    # 缓存命中率
    if cache_rate < 20 and end_stats['total_requests'] > 10:
        print(f"⚠️ 缓存命中率较低: {cache_rate:.1f}%")
    else:
        print(f"✅ 缓存命中率: {cache_rate:.1f}%")
    
    return success


def run_quick_test():
    """快速测试（10个代币）"""
    print("⚡ 快速压力测试 (10 tokens)")
    return run_pressure_test(tokens=10)


def run_full_test():
    """完整测试（30个代币）"""
    print("🎯 完整压力测试 (30 tokens)")
    return run_pressure_test(tokens=30)


def main():
    parser = argparse.ArgumentParser(description="MMTracker 压力测试")
    parser.add_argument("--tokens", "-n", type=int, default=30, help="测试代币数量")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细信息")
    parser.add_argument("--quick", "-q", action="store_true", help="快速测试（10 tokens）")
    
    args = parser.parse_args()
    
    if args.quick:
        success = run_quick_test()
    else:
        success = run_pressure_test(tokens=args.tokens, verbose=args.verbose)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
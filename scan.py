#!/usr/bin/env python3
"""
MMTracker 市场全局扫描器

用法:
  python scan.py               ← 全市场扫描，输出 Top 15
  python scan.py --top 30      ← 显示 Top 30
  python scan.py --watch 600  ← 每10分钟自动刷新一次
  python scan.py --quick      ← 只做画像过滤，不运行完整7信号（更快）
  python scan.py LAB VELVET    ← 指定代币，走完整7信号分析（复用原 main.py 功能）
"""

import sys
import argparse
import time
from datetime import datetime

# 添加项目路径
sys.path.insert(0, "/mnt/c/Users/朱/Desktop/hexagon_copilot/mm_tracker")

# 尝试导入 rich（可选）
try:
    from rich.console import Console
    console = Console()
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    console = None


def run_full_scan(top_n: int = 15, quick: bool = False) -> list:
    """
    完整扫描流程
    """
    from scanner.universe import get_full_universe
    from scanner.fast_filter import run_fast_filter
    from scanner.deep_scan import deep_scan_batch
    from scanner.scan_report import render_scan_results, save_scan_report
    
    start_time = time.time()
    
    # Step 1: 获取全市场候选
    if console:
        console.print("[cyan]Step 1/4 获取全市场代币...[/cyan]")
    else:
        print("Step 1/4 获取全市场代币...")
    
    universe = get_full_universe()
    
    if not universe:
        print("❌ 无法获取代币列表")
        return []
    
    if console:
        console.print(f"[green]✓ 获取 {len(universe)} 个代币[/green]")
    else:
        print(f"✓ 获取 {len(universe)} 个代币")
    
    # Step 2: 画像过滤 + 预评分
    if console:
        console.print("[cyan]Step 2/4 画像快速筛选...[/cyan]")
    else:
        print("Step 2/4 画像快速筛选...")
    
    candidates = run_fast_filter(universe)
    
    if not candidates:
        print("❌ 无候选通过画像过滤")
        return []
    
    if quick:
        # 快速模式：只做画像过滤
        duration = time.time() - start_time
        stats = {
            "total_scanned": len(universe),
            "candidates_count": len(candidates),
            "duration": duration,
        }
        
        if console:
            console.print("[green]✓ 快速模式完成，跳过深度扫描[/green]")
            render_scan_results(candidates, show_top=top_n, stats=stats)
        else:
            print(f"✓ 快速模式完成，跳过深度扫描")
            # 简单打印
            print(f"\n{'='*50}")
            print("Top 候选代币:")
            for i, c in enumerate(candidates[:10], 1):
                print(f"  {i}. {c['symbol']}: ${c.get('price', 0):.4f}, 分数={c.get('quick_score', 0)}")
        
        return candidates
    
    # Step 3: 深度7信号扫描
    if console:
        console.print("[cyan]Step 3/4 深度7信号扫描...[/cyan]")
    else:
        print("Step 3/4 深度7信号扫描...")
    
    results = deep_scan_batch(candidates)
    
    # Step 4: 渲染输出 + 保存
    duration = time.time() - start_time
    
    stats = {
        "total_scanned": len(universe),
        "candidates_count": len(candidates),
        "duration": duration,
    }
    
    if console:
        console.print("[cyan]Step 4/4 生成报告...[/cyan]")
        render_scan_results(results, show_top=top_n, stats=stats)
    else:
        render_scan_results(results, show_top=top_n, stats=stats)
    
    # 检查是否有高置信度信号，发送桌面通知
    try:
        from fetchers.notification import notify_if_needed
        for r in results:
            score = r.get("score", {})
            grade = score.get("grade", "")
            if grade in ["ENTRY", "WATCH"]:
                signal_count = score.get("triggered_count", 0)
                # 获取触发信号名称
                signals = r.get("signals", {})
                triggered_signals = [k.replace("signal_", "s") for k, v in signals.items() if isinstance(v, dict) and v.get("triggered")]
                notify_if_needed(
                    symbol=r.get("symbol", ""),
                    signal_count=signal_count,
                    triggered_signals=triggered_signals,
                    grade=grade
                )
    except ImportError:
        pass  # 通知模块未安装
    
    # 保存报告
    try:
        save_path = save_scan_report(results, stats)
        if console:
            console.print(f"[green]报告已保存: {save_path}[/green]")
        else:
            print(f"报告已保存: {save_path}")
    except Exception as e:
        if console:
            console.print(f"[yellow]报告保存失败: {e}[/yellow]")
        else:
            print(f"报告保存失败: {e}")
    
    return results


def run_single_analysis(tickers: list) -> None:
    """
    单币分析模式（复用 main.py 功能）
    """
    from main import analyze_one
    
    for symbol in tickers:
        if console:
            console.print(f"\n[bold cyan]分析代币: {symbol}[/bold cyan]")
        else:
            print(f"\n{'='*50}")
            print(f"分析代币: {symbol}")
            print(f"{'='*50}")
        
        try:
            result = analyze_one(symbol.upper(), verbose=True)
        except Exception as e:
            if console:
                console.print(f"[red]分析失败: {e}[/red]")
            else:
                print(f"分析失败: {e}")


def main():
    parser = argparse.ArgumentParser(description="MMTracker 市场全局扫描器")
    parser.add_argument("tickers", nargs="*", help="指定代币代码（可选）")
    parser.add_argument("--top", type=int, default=15, help="显示 Top N (默认15)")
    parser.add_argument("--quick", action="store_true", help="快速模式（只做画像过滤）")
    parser.add_argument("--watch", type=int, default=0, help="自动刷新间隔（秒）")
    parser.add_argument("--health", action="store_true", help="显示健康检查报告")
    parser.add_argument("--stats", action="store_true", help="显示系统统计信息")
    
    args = parser.parse_args()
    
    # 健康检查模式
    if args.health:
        from fetchers.utils import get_health_report
        print(get_health_report())
        return
    
    # 统计信息模式
    if args.stats:
        from fetchers.utils import get_stats, health_monitor
        from fetchers.utils import thread_safe_cache
        
        stats = get_stats()
        cache_stats = thread_safe_cache.get_stats()
        
        print("=" * 50)
        print("📊 系统统计信息")
        print("=" * 50)
        print(f"请求统计:")
        print(f"  总请求数: {stats['total_requests']}")
        print(f"  429限流: {stats['429_hits']}")
        print(f"  错误数: {stats['errors']}")
        print(f"  平均延迟: {stats['avg_latency_ms']:.1f}ms")
        print(f"  最大延迟: {stats['max_latency_ms']:.1f}ms")
        print(f"\n缓存统计:")
        print(f"  命中: {cache_stats['hits']}")
        print(f"  未命中: {cache_stats['misses']}")
        print(f"  命中率: {cache_stats['hit_rate']*100:.1f}%")
        print(f"  缓存大小: {cache_stats['size']}")
        return
    
    if args.tickers:
        # 指定代币，走原来 main.py 的单币分析
        run_single_analysis(args.tickers)
        return
    
    # 全市场扫描模式
    if args.watch > 0:
        # 持续监控
        scan_count = 0
        
        while True:
            scan_count += 1
            
            if console:
                console.rule(f"[bold cyan]第 {scan_count} 次扫描[/bold cyan]")
            else:
                print(f"\n{'='*50}")
                print(f"第 {scan_count} 次扫描")
                print(f"{'='*50}")
            
            run_full_scan(top_n=args.top, quick=args.quick)
            
            wait_msg = f"等待 {args.watch} 秒后重新扫描..."
            if console:
                console.print(f"[dim]{wait_msg}[/dim]")
            else:
                print(f"\n{wait_msg}")
            
            time.sleep(args.watch)
    else:
        # 单次运行
        run_full_scan(top_n=args.top, quick=args.quick)


if __name__ == "__main__":
    main()
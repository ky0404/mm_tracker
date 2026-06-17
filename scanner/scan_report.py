"""
MMTracker Scanner - 扫描报告渲染模块
用 rich 库输出市场全局扫描结果排行榜。
"""

import json
from typing import List, Dict, Any
from datetime import datetime
from pathlib import Path

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def render_scan_results(results: List[Dict], show_top: int = 15, stats: Dict = None):
    """
    在终端渲染扫描排行榜
    """
    if not RICH_AVAILABLE:
        _render_plain_text(results, show_top, stats)
        return
    
    console = Console()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # 统计信息
    total_scanned = stats.get("total_scanned", 0) if stats else 0
    candidates_count = stats.get("candidates_count", 0) if stats else len(results)
    
    # 标题
    title = f"🔍 MMTracker 全市场庄家行为扫描 | {now}"
    subtitle = f"扫描范围: {total_scanned} 个代币 | 最终候选: {candidates_count} 个"
    if stats and "duration" in stats:
        subtitle += f" | 耗时: {stats['duration']:.1f}秒"
    
    console.print()
    console.rule(f"[bold cyan]{title}[/bold cyan]")
    console.print(f"[dim]{subtitle}[/dim]")
    
    # 数据源健康状态（如果有 stats）
    if stats and stats.get("show_health", True):
        try:
            from fetchers.utils import get_stats, health_monitor
            from config import MMTrackerConfig
            
            health_stats = get_stats()
            config = MMTrackerConfig()
            ds_status = config.datasource_switches.get_status()
            
            # 构建健康状态面板
            health_lines = []
            for source, enabled in ds_status.items():
                status_icon = "✅" if enabled else "❌"
                source_stats = health_stats.get("sources", {}).get(source, {})
                req_count = source_stats.get("requests", 0)
                err_count = source_stats.get("errors", 0)
                health_lines.append(f"  {status_icon} {source}: {req_count} 请求, {err_count} 错误")
            
            if health_lines:
                console.print("\n[bold]📡 数据源状态:[/bold]")
                for line in health_lines:
                    console.print(line)
        except Exception:
            pass
    
    # 排行榜表格
    table = Table(
        title="",
        box=None,
        show_header=True,
        header_style="bold cyan",
        row_styles=[],
    )
    
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("代币", style="bold", width=8)
    table.add_column("价格", justify="right", width=10)
    table.add_column("24H", justify="right", width=7)
    table.add_column("信号", justify="center", width=5)
    table.add_column("评分", justify="center", width=6)
    table.add_column("等级", width=8)
    table.add_column("关键信号", style="dim")
    
    # 按触发信号数排序
    sorted_results = sorted(
        results[:show_top],
        key=lambda x: (x.get("score", {}).get("triggered_count", 0), x.get("score", {}).get("total_score", 0)),
        reverse=True
    )
    
    for i, r in enumerate(sorted_results, 1):
        score_data = r.get("score", {})
        triggered = score_data.get("triggered_count", 0)
        total_score = score_data.get("total_score", 0)
        grade = score_data.get("grade", "IDLE")
        grade_label = score_data.get("grade_label", "无信号")
        
        # 颜色
        if triggered >= 5:
            row_style = "on green"
            grade_color = "green"
        elif triggered >= 3:
            row_style = "on yellow"
            grade_color = "yellow"
        else:
            row_style = ""
            grade_color = "white"
        
        # 价格和变化
        price = r.get("price", 0)
        if price > 0:
            price_str = f"${price:.4f}"
        else:
            price_str = "-"
        
        # 获取价格变化
        change = 0
        if r.get("signals"):
            # 从 signal_1 获取变化
            sig1 = r.get("signals", {}).get("signal_1_integer_consolidation", {})
            change = 0  # 简化处理
        
        # 关键信号
        key_signals = []
        for sig_name, sig_data in r.get("signals", {}).items():
            if sig_data.get("triggered"):
                sig_label = sig_name.replace("signal_", "").replace("_", " ")
                key_signals.append(sig_label)
        
        key_signals_str = ", ".join(key_signals[:3]) if key_signals else "-"
        
        table.add_row(
            str(i),
            r["symbol"],
            price_str,
            f"{change:+.1f}%",
            f"{triggered}/11",
            f"{total_score:.1f}",
            f"[{grade_color}]{grade_label}[/{grade_color}]",
            key_signals_str,
            style=row_style
        )
    
    console.print(table)
    
    # 重点关注区块 - 使用新阈值 (>=2)
    entry_coins = [r for r in results if r.get("score", {}).get("triggered_count", 0) >= 2]
    
    if entry_coins:
        console.print()
        # 区分高置信和低置信
        high_conf = [c for c in entry_coins if r.get("score", {}).get("triggered_count", 0) >= 3]
        low_conf = [c for c in entry_coins if r.get("score", {}).get("triggered_count", 0) == 2]
        
        if high_conf:
            console.print(Panel.fit(
                "[bold green]🟢 高置信 (3+信号)[/bold green]\n" + 
                "\n".join([
                    f"{c['symbol']} ${c.get('price', 0):.4f} | {c.get('score', {}).get('triggered_count', 0)}信号 | {c.get('score', {}).get('total_score', 0):.1f}分"
                    for c in high_conf[:5]
                ]),
                border_style="green",
                title="高置信入场"
            ))
        
        if low_conf:
            console.print(Panel.fit(
                "[bold yellow]🟡 低置信 (2信号)[/bold yellow]\n" + 
                "\n".join([
                    f"{c['symbol']} ${c.get('price', 0):.4f} | {c.get('score', {}).get('triggered_count', 0)}信号"
                    for c in low_conf[:5]
                ]),
                border_style="yellow",
                title="低置信观察"
            ))
    else:
        console.print()
        console.print(Panel.fit(
            "[dim]当前无代币满足2个以上信号，市场处于静默期，继续监控[/dim]",
            border_style="blue",
            title="📊 状态"
        ))


def _render_plain_text(results: List[Dict], show_top: int = 15, stats: Dict = None):
    """纯文本回退渲染"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    print(f"\n{'='*60}")
    print(f"🔍 MMTracker 全市场庄家行为扫描 | {now}")
    print(f"{'='*60}")
    
    sorted_results = sorted(
        results[:show_top],
        key=lambda x: (x.get("score", {}).get("triggered_count", 0), x.get("score", {}).get("total_score", 0)),
        reverse=True
    )
    
    print(f"\n{'#':<3} {'代币':<8} {'价格':<10} {'信号':<6} {'评分':<6} {'等级':<12}")
    print("-" * 60)
    
    for i, r in enumerate(sorted_results, 1):
        score_data = r.get("score", {})
        triggered = score_data.get("triggered_count", 0)
        total_score = score_data.get("total_score", 0)
        grade_label = score_data.get("grade_label", "无信号")
        
        price = r.get("price", 0)
        price_str = f"${price:.4f}" if price > 0 else "-"
        
        print(f"{i:<3} {r['symbol']:<8} {price_str:<10} {triggered}/7   {total_score:<6.1f} {grade_label:<12}")


def save_scan_report(results: List[Dict], stats: Dict = None, output_dir: str = "reports") -> str:
    """
    保存扫描结果为 JSON 和 Markdown 报告
    """
    now = datetime.now()
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    
    # 确保目录存在
    Path(output_dir).mkdir(exist_ok=True)
    
    # JSON 数据
    json_data = {
        "scan_time": now.isoformat(),
        "total_scanned": stats.get("total_scanned", 0) if stats else 0,
        "candidates_filtered": stats.get("candidates_count", 0) if stats else len(results),
        "duration_seconds": stats.get("duration", 0) if stats else 0,
        "results": []
    }
    
    for r in results:
        score = r.get("score", {})
        json_data["results"].append({
            "symbol": r["symbol"],
            "price": r.get("price", 0),
            "quick_score": r.get("quick_score", 0),
            "funding_rate": r.get("funding_rate", 0),
            "triggered_count": score.get("triggered_count", 0),
            "total_score": score.get("total_score", 0),
            "grade": score.get("grade", "IDLE"),
            "grade_label": score.get("grade_label", ""),
            "signals_triggered": [
                s for s, d in r.get("signals", {}).items() if d.get("triggered")
            ]
        })
    
    # 保存 JSON
    json_path = f"{output_dir}/scan_{timestamp}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, ensure_ascii=False)
    
    # 生成 Markdown
    md_lines = [
        f"# MMTracker 全市场扫描报告",
        f"",
        f"**扫描时间**: {now.strftime('%Y-%m-%d %H:%M:%S')}",
        f"**扫描范围**: {json_data['total_scanned']} 个代币",
        f"**候选数量**: {json_data['candidates_filtered']} 个",
        f"",
        f"## 排行榜",
        f"",
        f"| # | 代币 | 价格 | 信号数 | 评分 | 等级 |",
        f"|---|------|------|--------|------|------|",
    ]
    
    for i, r in enumerate(json_data["results"][:20], 1):
        md_lines.append(
            f"| {i} | {r['symbol']} | ${r['price']:.4f} | {r['triggered_count']}/9 | "
            f"{r['total_score']:.1f} | {r['grade_label']} |"
        )
    
    # 添加重点关注
    entry_coins = [r for r in json_data["results"] if r["triggered_count"] >= 5]
    if entry_coins:
        md_lines.extend([
            "",
            f"## 🎯 重点关注 ({len(entry_coins)} 个)",
            ""
        ])
        for c in entry_coins:
            md_lines.append(f"- **{c['symbol']}**: 触发 {c['triggered_count']} 个信号")
    
    md_content = "\n".join(md_lines)
    
    md_path = f"{output_dir}/scan_{timestamp}.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    
    return md_path


def load_latest_report(output_dir: str = "reports") -> Dict:
    """加载最新的扫描报告"""
    reports_dir = Path(output_dir)
    
    if not reports_dir.exists():
        return None
    
    json_files = list(reports_dir.glob("scan_*.json"))
    
    if not json_files:
        return None
    
    # 找到最新的
    latest = max(json_files, key=lambda p: p.stat().st_mtime)
    
    with open(latest, "r", encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    # 测试
    test_results = [
        {"symbol": "LAB", "price": 0.82, "quick_score": 8.5, "funding_rate": 0.001,
         "score": {"triggered_count": 5, "total_score": 7.5, "grade": "ENTRY", "grade_label": "满足入场"}},
        {"symbol": "VELVET", "price": 0.041, "quick_score": 7.5, "funding_rate": 0.0005,
         "score": {"triggered_count": 4, "total_score": 5.5, "grade": "WATCH", "grade_label": "一般关注"}},
    ]
    
    render_scan_results(test_results, show_top=15, stats={"total_scanned": 441, "candidates_count": 30, "duration": 45.2})
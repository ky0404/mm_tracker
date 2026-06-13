import json
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.live_metrics import calculate_live_metrics, calculate_period_metrics
from backtest.auto_calibrator import auto_calibrate, suggest_parameter_adjustments


def generate_live_report(
    log_file: str = "trading/live_trades.json",
    output_dir: str = "reports",
    output_format: str = "markdown",
) -> Dict[str, Any]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    metrics = calculate_live_metrics(log_file)
    period_7d = calculate_period_metrics(log_file, days=7)
    period_30d = calculate_period_metrics(log_file, days=30)
    calib = auto_calibrate(log_file)
    suggestions = suggest_parameter_adjustments(log_file)
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    md_content = f"""# MMTracker 实战周报

**生成时间**: {timestamp}

---

## 交易概览

| 指标 | 数值 |
|------|------|
| 总交易数 | {metrics['total_trades']} |
| 胜率 | {metrics['win_rate']:.1%} |
| 盈利交易 | {metrics['win_count']} |
| 亏损交易 | {metrics['loss_count']} |
| 平均盈亏 | {metrics['avg_pnl']:.2f} |
| 总盈亏 | {metrics['total_pnl']:.2f} |
| 最大回撤 | {metrics['max_drawdown']:.2f} |

---

## 近期表现

| 周期 | 交易数 | 胜率 | 平均盈亏 | 总盈亏 |
|------|--------|------|----------|--------|
| 7天 | {period_7d.get('trades', 0)} | {period_7d.get('win_rate', 0):.1%} | {period_7d.get('avg_pnl', 0):.2f} | {period_7d.get('total_pnl', 0):.2f} |
| 30天 | {period_30d.get('trades', 0)} | {period_30d.get('win_rate', 0):.1%} | {period_30d.get('avg_pnl', 0):.2f} | {period_30d.get('total_pnl', 0):.2f} |

---

## 参数校准建议

**当前最佳阈值**: {calib.get('best_threshold', 'N/A')}
**评分**: {calib.get('best_score', 0):.2f}

"""

    if suggestions["suggestions"]:
        md_content += "### 参数调整建议\n\n"
        for s in suggestions["suggestions"]:
            md_content += f"- **{s['parameter']}**: {s['current']} → {s['suggested']}\n  - 原因: {s['reason']}\n"

    md_content += "\n---\n\n## 最近交易记录\n\n"

    try:
        with open(log_file, "r", encoding="utf-8") as f:
            trades = json.load(f)
    except:
        trades = []

    recent = sorted(trades, key=lambda x: x.get("timestamp", ""), reverse=True)[:10]
    
    if recent:
        md_content += "| 时间 | 代币 | 信号数 | 入场价 | 出场价 | PnL | 胜负 |\n"
        md_content += "|------|------|--------|--------|--------|-----|------|\n"
        
        for t in recent:
            ts = t.get("timestamp", "")[:10]
            token = t.get("token", "")
            signals = t.get("signal_count", 0)
            entry = t.get("entry_price", 0)
            exit_p = t.get("exit_price", "N/A")
            pnl = t.get("pnl", "N/A")
            win = "✅" if t.get("win") else "❌" if t.get("win") is False else "⏳"
            
            entry_str = f"{entry:.4f}" if entry else "N/A"
            exit_str = f"{exit_p:.4f}" if exit_p and isinstance(exit_p, (int, float)) else "N/A"
            pnl_str = f"{pnl:.2f}" if pnl and isinstance(pnl, (int, float)) else "N/A"
            
            md_content += f"| {ts} | {token} | {signals} | {entry_str} | {exit_str} | {pnl_str} | {win} |\n"
    else:
        md_content += "*暂无交易记录*\n"

    md_content += "\n---\n\n*由 MMTracker 自动生成*\n"
    
    output_file = output_path / "live_report.md"
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(md_content)
    
    json_output = output_path / "live_report.json"
    with open(json_output, "w", encoding="utf-8") as f:
        json.dump({
            "timestamp": timestamp,
            "metrics": metrics,
            "period_7d": period_7d,
            "period_30d": period_30d,
            "calibration": calib,
            "suggestions": suggestions,
        }, f, ensure_ascii=False, indent=2)
    
    return {
        "markdown_file": str(output_file),
        "json_file": str(json_output),
        "metrics": metrics,
    }


def generate_daily_summary(
    log_file: str = "trading/live_trades.json",
) -> str:
    period = calculate_period_metrics(log_file, days=1)
    
    return f"""
📊 MMTracker 每日摘要

昨日交易: {period['trades']}
胜率: {period['win_rate']:.1%}
盈亏: {period['total_pnl']:.2f}
"""


if __name__ == "__main__":
    result = generate_live_report()
    print(f"报告已生成: {result['markdown_file']}")
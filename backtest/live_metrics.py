import json
from typing import Dict, Any, List
from datetime import datetime, timedelta

def calculate_live_metrics(log_file: str = "trading/live_trades.json") -> Dict[str, Any]:
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            trades = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        trades = []

    exits = [t for t in trades if t.get("type") == "EXIT" and t.get("win") is not None]
    if len(exits) == 0:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
            "max_drawdown": 0.0,
            "total_pnl": 0.0,
            "win_count": 0,
            "loss_count": 0,
        }

    wins = [t for t in exits if t.get("win", False)]
    win_rate = len(wins) / len(exits)

    pnl_list = [t["pnl"] for t in exits if t.get("pnl") is not None]
    avg_pnl = sum(pnl_list) / len(pnl_list) if pnl_list else 0.0
    total_pnl = sum(pnl_list)

    cumulative = 0
    max_cumulative = 0
    max_drawdown = 0
    for p in pnl_list:
        cumulative += p
        max_cumulative = max(max_cumulative, cumulative)
        drawdown = max_cumulative - cumulative
        max_drawdown = max(max_drawdown, drawdown)

    return {
        "total_trades": len(exits),
        "win_count": len(wins),
        "loss_count": len(exits) - len(wins),
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "total_pnl": total_pnl,
        "max_drawdown": max_drawdown,
    }


def calculate_period_metrics(
    log_file: str = "trading/live_trades.json",
    days: int = 7,
) -> Dict[str, Any]:
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            trades = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        trades = []

    cutoff = datetime.now() - timedelta(days=days)
    recent = [
        t for t in trades 
        if t.get("type") == "EXIT" 
        and t.get("exit_timestamp")
        and datetime.fromisoformat(t["exit_timestamp"]) > cutoff
    ]

    if not recent:
        return {
            "period": f"{days}d",
            "trades": 0,
            "win_rate": 0.0,
            "avg_pnl": 0.0,
        }

    wins = [t for t in recent if t.get("win", False)]
    pnl_list = [t.get("pnl", 0) for t in recent if t.get("pnl") is not None]

    return {
        "period": f"{days}d",
        "trades": len(recent),
        "win_rate": len(wins) / len(recent) if recent else 0.0,
        "avg_pnl": sum(pnl_list) / len(pnl_list) if pnl_list else 0.0,
        "total_pnl": sum(pnl_list),
    }


def get_signal_performance(
    log_file: str = "trading/live_trades.json",
) -> Dict[str, Any]:
    try:
        with open(log_file, "r", encoding="utf-8") as f:
            trades = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        trades = []

    signal_stats = {}

    for t in trades:
        if t.get("type") != "EXIT":
            continue
        
        signals = t.get("signals", [])
        win = t.get("win", False)
        
        for sig in signals:
            if sig not in signal_stats:
                signal_stats[sig] = {"total": 0, "wins": 0}
            signal_stats[sig]["total"] += 1
            if win:
                signal_stats[sig]["wins"] += 1

    for sig, stats in signal_stats.items():
        stats["win_rate"] = stats["wins"] / stats["total"] if stats["total"] > 0 else 0.0

    return signal_stats


if __name__ == "__main__":
    metrics = calculate_live_metrics()
    print(f"总交易: {metrics['total_trades']}")
    print(f"胜率: {metrics['win_rate']:.1%}")
    print(f"平均盈亏: {metrics['avg_pnl']:.2f}")
    print(f"总盈亏: {metrics['total_pnl']:.2f}")
    print(f"最大回撤: {metrics['max_drawdown']:.2f}")
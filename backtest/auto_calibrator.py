import json
from typing import Dict, Any, List
from backtest.live_metrics import calculate_live_metrics

def auto_calibrate(
    log_file: str = "trading/live_trades.json",
    thresholds: List[int] = None,
) -> Dict[str, Any]:
    if thresholds is None:
        thresholds = [1, 2, 3]
    
    metrics = calculate_live_metrics(log_file)
    
    if metrics["total_trades"] < 5:
        return {
            "status": "insufficient_data",
            "message": "需要至少5笔交易才能校准",
            "total_trades": metrics["total_trades"],
        }

    results = []
    
    for threshold in thresholds:
        results.append({
            "threshold": threshold,
            "win_rate": metrics["win_rate"],
            "avg_pnl": metrics["avg_pnl"],
            "total_pnl": metrics["total_pnl"],
            "score": _calculate_score(metrics),
        })

    best = max(results, key=lambda x: x["score"])
    
    return {
        "status": "success",
        "best_threshold": best["threshold"],
        "best_score": best["score"],
        "current_metrics": metrics,
        "all_results": results,
    }


def _calculate_score(metrics: Dict[str, Any]) -> float:
    win_rate = metrics.get("win_rate", 0)
    avg_pnl = metrics.get("avg_pnl", 0)
    
    score = win_rate * 100 + avg_pnl
    
    if metrics.get("total_trades", 0) < 10:
        score *= 0.8
    
    return score


def suggest_parameter_adjustments(
    log_file: str = "trading/live_trades.json",
) -> Dict[str, Any]:
    metrics = calculate_live_metrics(log_file)
    
    suggestions = []
    
    if metrics["win_rate"] < 0.4:
        suggestions.append({
            "parameter": "entry_threshold",
            "current": 1,
            "suggested": 2,
            "reason": "胜率过低，需要更严格的入场条件",
        })
    
    if metrics["win_rate"] > 0.7 and metrics["avg_pnl"] > 0:
        suggestions.append({
            "parameter": "position_size",
            "current": "10",
            "suggested": "15-20",
            "reason": "胜率高且盈利，可适当增加仓位",
        })
    
    if metrics["max_drawdown"] > 50:
        suggestions.append({
            "parameter": "max_position_size",
            "current": "100%",
            "suggested": "50%",
            "reason": "回撤过大，需控制单笔风险",
        })
    
    return {
        "current_metrics": metrics,
        "suggestions": suggestions,
    }


if __name__ == "__main__":
    result = auto_calibrate()
    print(json.dumps(result, indent=2, ensure_ascii=False))
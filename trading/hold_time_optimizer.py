"""
持仓时间智能优化器
基于 LEAN 的 HoldTimeOptimizationModel 设计
分析统计分布，智能推荐最优持仓时间
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from scipy import stats
import numpy as np


class HoldTimeOptimizer:
    """
    持仓时间智能优化器
    
    LEAN 参考: HoldTimeOptimizationModel.cs
    
    特性：
    1. 持仓时间分布分析
    2. 胜率-持仓时间关系建模
    3. 最优持有区间计算
    4. 风险调整收益分析
    """
    
    def __init__(
        self,
        min_trades: int = 10,
        risk_free_rate: float = 0.03 / 365  # 日无风险利率
    ):
        self.min_trades = min_trades
        self.risk_free_rate = risk_free_rate
        self._cache_file = "config/hold_time_cache.json"
        self._cache = self._load_cache()
    
    def _load_cache(self) -> dict:
        try:
            if os.path.exists(self._cache_file):
                with open(self._cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}
    
    def _save_cache(self):
        os.makedirs(os.path.dirname(self._cache_file), exist_ok=True)
        with open(self._cache_file, 'w', encoding='utf-8') as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)
    
    def analyze(self, trades: List[dict]) -> dict:
        """
        分析持仓时间，输出优化建议
        """
        # 筛选有效交易
        valid_trades = self._filter_valid_trades(trades)
        
        if len(valid_trades) < self.min_trades:
            return {
                "sufficient_data": False,
                "reason": f"有效交易数不足: {len(valid_trades)}/{self.min_trades}",
                "recommendation": 240,  # 默认4小时
                "confidence": "low"
            }
        
        # 计算持仓时间（分钟）
        hold_times = []
        win_hold_times = []
        loss_hold_times = []
        
        for trade in valid_trades:
            hold_minutes = self._calculate_hold_time(trade)
            if hold_minutes is None:
                continue
            
            hold_times.append(hold_minutes)
            pnl_pct = trade.get("pnl_pct", 0)
            
            if pnl_pct > 0:
                win_hold_times.append(hold_minutes)
            else:
                loss_hold_times.append(hold_minutes)
        
        if not hold_times:
            return {
                "sufficient_data": False,
                "reason": "无法计算持仓时间",
                "recommendation": 240,
                "confidence": "low"
            }
        
        # 统计分析
        stats_result = self._calculate_statistics(
            hold_times, win_hold_times, loss_hold_times
        )
        
        # 找到最优持仓区间
        optimal_range = self._find_optimal_range(
            hold_times, win_hold_times, loss_hold_times
        )
        
        # 计算风险调整收益
        risk_adj_return = self._calculate_risk_adjusted_return(
            hold_times, valid_trades
        )
        
        # 综合建议
        recommendation = self._generate_recommendation(
            stats_result, optimal_range, risk_adj_return
        )
        
        result = {
            "sufficient_data": True,
            "statistics": stats_result,
            "optimal_range": optimal_range,
            "risk_adjusted_return": risk_adj_return,
            "recommendation": recommendation["max_hold_minutes"],
            "confidence": recommendation["confidence"],
            "reasoning": recommendation["reasoning"],
            "last_updated": datetime.now().isoformat()
        }
        
        # 缓存结果
        self._cache = result
        self._save_cache()
        
        return result
    
    def _filter_valid_trades(self, trades: List[dict]) -> List[dict]:
        """筛选有效交易（排除手动操作）"""
        excluded_reasons = {"SELL_ALL", "SPOT_CLOSED", "manual_reset"}
        
        valid = []
        for trade in trades:
            if trade.get("type") != "EXIT":
                continue
            if trade.get("exit_reason") in excluded_reasons:
                continue
            if "manual_sell_all" in trade.get("signals", []):
                continue
            valid.append(trade)
        
        return valid
    
    def _calculate_hold_time(self, trade: dict) -> Optional[float]:
        """计算持仓时间（分钟）- 使用统一的字段和解析方式"""
        # 统一使用 entry_time 或 timestamp
        entry_time = trade.get("entry_time") or trade.get("timestamp", "")
        exit_time = trade.get("exit_timestamp", "")
        
        if not entry_time or not exit_time:
            return None
        
        try:
            entry_dt = datetime.fromisoformat(entry_time.replace("Z", ""))
            exit_dt = datetime.fromisoformat(exit_time.replace("Z", ""))
            delta = exit_dt - entry_dt
            return delta.total_seconds() / 60
        except Exception:
            return None
    
    def _calculate_statistics(
        self,
        hold_times: List[float],
        win_times: List[float],
        loss_times: List[float]
    ) -> dict:
        """计算统计指标"""
        return {
            "total_trades": len(hold_times),
            "mean_hold_minutes": round(np.mean(hold_times), 1),
            "median_hold_minutes": round(np.median(hold_times), 1),
            "std_hold_minutes": round(np.std(hold_times), 1),
            "min_hold_minutes": round(min(hold_times), 1),
            "max_hold_minutes": round(max(hold_times), 1),
            "win_count": len(win_times),
            "loss_count": len(loss_times),
            "avg_win_hold_minutes": round(np.mean(win_times), 1) if win_times else 0,
            "avg_loss_hold_minutes": round(np.mean(loss_times), 1) if loss_times else 0,
        }
    
    def _find_optimal_range(
        self,
        hold_times: List[float],
        win_times: List[float],
        loss_times: List[float]
    ) -> dict:
        """找到最优持仓区间"""
        if not hold_times:
            return {"min": 60, "max": 480, "optimal": 240}
        
        # 按时间分桶
        buckets = {
            "0-30": {"wins": 0, "losses": 0},
            "30-60": {"wins": 0, "losses": 0},
            "60-120": {"wins": 0, "losses": 0},
            "120-240": {"wins": 0, "losses": 0},
            "240-480": {"wins": 0, "losses": 0},
            "480+": {"wins": 0, "losses": 0},
        }
        
        for t in hold_times:
            pnl = 0
            # 查找对应的pnl (简化处理)
            
            if t <= 30:
                bucket = "0-30"
            elif t <= 60:
                bucket = "30-60"
            elif t <= 120:
                bucket = "60-120"
            elif t <= 240:
                bucket = "120-240"
            elif t <= 480:
                bucket = "240-480"
            else:
                bucket = "480+"
            
            # 简化：使用全局win_times判断
            if t in win_times:
                buckets[bucket]["wins"] += 1
            else:
                buckets[bucket]["losses"] += 1
        
        # 计算每个桶的胜率
        bucket_win_rates = {}
        for bucket, counts in buckets.items():
            total = counts["wins"] + counts["losses"]
            if total > 0:
                bucket_win_rates[bucket] = counts["wins"] / total
            else:
                bucket_win_rates[bucket] = 0.5
        
        # 找最优桶
        best_bucket = max(bucket_win_rates, key=bucket_win_rates.get)
        
        # 转换为时间范围
        bucket_ranges = {
            "0-30": (0, 30),
            "30-60": (30, 60),
            "60-120": (60, 120),
            "120-240": (120, 240),
            "240-480": (240, 480),
            "480+": (480, 1440),
        }
        
        range_min, range_max = bucket_ranges.get(best_bucket, (60, 480))
        
        return {
            "min": range_min,
            "max": range_max,
            "optimal": int((range_min + range_max) / 2),
            "best_bucket": best_bucket,
            "bucket_win_rates": {k: round(v, 2) for k, v in bucket_win_rates.items()}
        }
    
    def _calculate_risk_adjusted_return(
        self,
        hold_times: List[float],
        trades: List[dict]
    ) -> dict:
        """计算风险调整收益（简化版 Sharpe Ratio）"""
        if not trades:
            return {"sharpe_like": 0, "sortino_like": 0}
        
        returns = []
        for i, trade in enumerate(trades):
            pnl_pct = trade.get("pnl_pct", 0)
            hold_hours = hold_times[i] / 60 if i < len(hold_times) else 1
            
            # 年化收益（简化）
            annual_return = pnl_pct * (365 / max(hold_hours, 0.1))
            returns.append(annual_return)
        
        if not returns:
            return {"sharpe_like": 0, "sortino_like": 0}
        
        mean_return = np.mean(returns)
        std_return = np.std(returns)
        
        sharpe_like = mean_return / std_return if std_return > 0 else 0
        
        # Sortino: 只考虑下行风险
        downside = [r for r in returns if r < 0]
        downside_std = np.std(downside) if downside else 0.01
        sortino_like = mean_return / downside_std if downside_std > 0 else 0
        
        return {
            "sharpe_like": round(sharpe_like, 3),
            "sortino_like": round(sortino_like, 3),
            "avg_annualized_return": round(mean_return, 3)
        }
    
    def _generate_recommendation(
        self,
        stats_result: dict,
        optimal_range: dict,
        risk_adj: dict
    ) -> dict:
        """生成推荐建议"""
        # 综合多个因素
        factors = []
        
        # 1. 统计中位数
        median_hold = stats_result["median_hold_minutes"]
        
        # 2. 最优区间
        optimal_hold = optimal_range["optimal"]
        
        # 3. 风险调整收益最优时间
        # 简化：取中位数和最优区间的加权
        
        # 计算置信度
        confidence = "low"
        if stats_result["total_trades"] >= 30:
            confidence = "high"
        elif stats_result["total_trades"] >= 15:
            confidence = "medium"
        
        # 最终推荐
        if confidence == "high":
            recommended = optimal_hold
        else:
            recommended = int((median_hold + optimal_hold) / 2)
        
        # 边界保护
        recommended = max(30, min(1440, recommended))
        
        reasoning = f"基于{stats_result['total_trades']}笔交易分析，"
        reasoning += f"中位数持仓{median_hold}分钟，"
        reasoning += f"最优区间{optimal_range['min']}-{optimal_range['max']}分钟"
        
        return {
            "max_hold_minutes": recommended,
            "confidence": confidence,
            "reasoning": reasoning
        }


def get_hold_time_optimizer() -> HoldTimeOptimizer:
    """获取持仓时间优化器单例"""
    global _hold_time_optimizer
    if _hold_time_optimizer is None:
        _hold_time_optimizer = HoldTimeOptimizer()
    return _hold_time_optimizer


_hold_time_optimizer: Optional[HoldTimeOptimizer] = None
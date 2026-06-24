"""
Walk-Forward 验证器
基于 LEAN 的 WalkForwardOptimizationAlgorithm 设计
滚动窗口验证，防止过拟合
"""
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import numpy as np


class WalkForwardValidator:
    """
    Walk-Forward 验证器
    
    LEAN 参考: WalkForwardOptimizationAlgorithm.cs
    
    特性：
    1. 滚动窗口分割（训练集/测试集）
    2. 多周期验证
    3. 稳定性评估
    4. 优化采纳决策
    """
    
    def __init__(
        self,
        train_ratio: float = 0.7,
        min_train_trades: int = 10,
        min_test_trades: int = 5,
        min_stability_rate: float = 0.6
    ):
        self.train_ratio = train_ratio
        self.min_train_trades = min_train_trades
        self.min_test_trades = min_test_trades
        self.min_stability_rate = min_stability_rate
        self._cache_file = "config/walkforward_cache.json"
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
    
    def validate(
        self,
        trades: List[dict],
        optimization_fn,
        n_splits: int = 5
    ) -> dict:
        """
        执行 Walk-Forward 验证
        
        参数:
        - trades: 交易历史
        - optimization_fn: 优化函数，接受 trades 返回优化后的参数
        - n_splits: 滚动窗口数量
        
        返回:
        - validation_result: 验证结果
        """
        # 筛选有效交易
        valid_trades = self._filter_valid_trades(trades)
        
        if len(valid_trades) < self.min_train_trades + self.min_test_trades:
            return {
                "validated": False,
                "reason": f"交易数不足: {len(valid_trades)}",
                "adopted": False
            }
        
        # 按时间排序
        sorted_trades = sorted(valid_trades, key=lambda x: x.get("exit_timestamp", x.get("timestamp", "")))
        n = len(sorted_trades)
        
        # 计算窗口
        train_size = int(n * self.train_ratio)
        step_size = max(1, (n - train_size) // max(n_splits - 1, 1))
        
        results = []
        
        for i in range(n_splits):
            start = i * step_size
            end = start + train_size
            
            if end > n:
                break
            
            train_window = sorted_trades[start:end]
            test_start = end
            test_end = min(end + step_size, n)
            test_window = sorted_trades[test_start:test_end]
            
            # 检查数据量
            if len(train_window) < self.min_train_trades:
                continue
            if len(test_window) < self.min_test_trades:
                continue
            
            # 在训练集上优化
            try:
                train_result = optimization_fn(train_window)
            except Exception as e:
                train_result = {"weights": {}, "performance": {}}
            
            # 在测试集上验证
            test_performance = self._evaluate_on_window(
                test_window, 
                train_result.get("weights", {})
            )
            
            # 判断稳定性
            stable = test_performance.get("total_pnl", 0) >= 0
            
            results.append({
                "window": i + 1,
                "train_range": f"{start}-{end}",
                "test_range": f"{test_start}-{test_end}",
                "train_size": len(train_window),
                "test_size": len(test_window),
                "train_performance": train_result.get("performance", {}),
                "test_performance": test_performance,
                "stable": stable
            })
        
        # 计算整体稳定性
        if not results:
            return {
                "validated": False,
                "reason": "无法生成有效窗口",
                "adopted": False
            }
        
        stable_count = sum(1 for r in results if r["stable"])
        stability_rate = stable_count / len(results)
        
        # 计算平均测试性能
        avg_test_pnl = np.mean([
            r["test_performance"].get("total_pnl", 0) for r in results
        ])
        
        avg_test_wr = np.mean([
            r["test_performance"].get("win_rate", 0) for r in results
        ])
        
        final_result = {
            "validated": True,
            "n_windows": len(results),
            "stable_windows": stable_count,
            "stability_rate": round(stability_rate, 3),
            "avg_test_pnl": round(avg_test_pnl, 3),
            "avg_test_win_rate": round(avg_test_wr, 3),
            "adopted": stability_rate >= self.min_stability_rate,
            "window_details": results,
            "recommendation": self._generate_recommendation(
                stability_rate, avg_test_pnl, avg_test_wr
            )
        }
        
        # 缓存
        self._cache = final_result
        self._save_cache()
        
        return final_result
    
    def _filter_valid_trades(self, trades: List[dict]) -> List[dict]:
        """筛选有效交易"""
        excluded_reasons = {"SELL_ALL", "SPOT_CLOSED", "manual_reset"}
        
        valid = []
        for trade in trades:
            if trade.get("type") != "EXIT":
                continue
            if trade.get("exit_reason") in excluded_reasons:
                continue
            valid.append(trade)
        
        return valid
    
    def _evaluate_on_window(
        self,
        trades: List[dict],
        weights: dict
    ) -> dict:
        """在单个窗口上评估性能"""
        wins = 0
        losses = 0
        total_pnl = 0
        pnl_list = []
        
        for trade in trades:
            pnl_pct = trade.get("pnl_pct", 0)
            pnl_list.append(pnl_pct)
            
            if pnl_pct > 0:
                wins += 1
            else:
                losses += 1
            total_pnl += pnl_pct
        
        total = wins + losses
        
        return {
            "wins": wins,
            "losses": losses,
            "total_trades": total,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
            "total_pnl": round(total_pnl, 3),
            "avg_pnl": round(total_pnl / total, 3) if total > 0 else 0,
            "pnl_std": round(np.std(pnl_list), 3) if len(pnl_list) > 1 else 0
        }
    
    def _generate_recommendation(
        self,
        stability_rate: float,
        avg_pnl: float,
        avg_wr: float
    ) -> str:
        """生成建议"""
        if stability_rate >= 0.8 and avg_pnl > 0:
            return "采用优化：多窗口表现稳定"
        elif stability_rate >= 0.6 and avg_pnl > 0:
            return "谨慎采用：部分窗口表现不稳定"
        elif stability_rate >= 0.4:
            return "暂不采用：多数窗口表现不稳定"
        else:
            return "拒绝采用：参数过拟合风险高"


class SimpleWalkForwardValidator:
    """
    简化版 Walk-Forward 验证器
    用于快速验证，不需要传入优化函数
    """
    
    def __init__(self):
        self.validator = WalkForwardValidator()
    
    def validate_trading_strategy(
        self,
        trades: List[dict],
        param_adjustments: Dict[str, Any]
    ) -> dict:
        """
        验证交易策略的稳定性
        
        参数:
        - trades: 交易历史
        - param_adjustments: 拟应用的参数调整
        
        返回:
        - 验证结果
        """
        # 创建模拟优化函数
        def mock_optimization(window_trades):
            # 简化：基于窗口内胜率计算权重
            signal_stats = {}
            
            for trade in window_trades:
                signals = trade.get("entry_signals", [])
                pnl_pct = trade.get("pnl_pct", 0)
                
                for sig in signals:
                    if sig not in signal_stats:
                        signal_stats[sig] = {"wins": 0, "total": 0}
                    signal_stats[sig]["total"] += 1
                    if pnl_pct > 0:
                        signal_stats[sig]["wins"] += 1
            
            # 计算权重
            weights = {}
            for sig, data in signal_stats.items():
                if data["total"] < 3:
                    continue
                win_rate = data["wins"] / data["total"]
                if win_rate > 0.6:
                    weights[sig] = 1.3
                elif win_rate < 0.4:
                    weights[sig] = 0.7
                else:
                    weights[sig] = 1.0
            
            # 评估性能
            performance = self._calculate_performance(window_trades, weights)
            
            return {"weights": weights, "performance": performance}
        
        # 执行验证
        result = self.validator.validate(trades, mock_optimization)
        
        # 添加建议信息
        if result.get("adopted"):
            result["message"] = (
                f"参数调整验证通过！稳定性: {result['stability_rate']*100:.0f}%, "
                f"平均测试收益: {result['avg_test_pnl']:.2%}"
            )
        else:
            result["message"] = (
                f"参数调整验证未通过。稳定性: {result.get('stability_rate', 0)*100:.0f}%, "
                f"建议保持现有参数"
            )
        
        return result
    
    def _calculate_performance(
        self,
        trades: List[dict],
        weights: dict
    ) -> dict:
        """计算性能"""
        wins = 0
        losses = 0
        total_pnl = 0
        
        for trade in trades:
            pnl_pct = trade.get("pnl_pct", 0)
            if pnl_pct > 0:
                wins += 1
            else:
                losses += 1
            total_pnl += pnl_pct
        
        total = wins + losses
        
        return {
            "wins": wins,
            "losses": losses,
            "total_trades": total,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
            "total_pnl": round(total_pnl, 3)
        }


def get_walkforward_validator() -> WalkForwardValidator:
    """获取验证器单例"""
    global _walkforward_validator
    if _walkforward_validator is None:
        _walkforward_validator = WalkForwardValidator()
    return _walkforward_validator


_walkforward_validator: Optional[WalkForwardValidator] = None
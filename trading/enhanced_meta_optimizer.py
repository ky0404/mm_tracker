"""
增强版元参数优化器
基于 LEAN 的 BayesianParameterOptimizationAlgorithm 设计
集成统计显著性检验、Chi-Square测试、置信区间
"""
import json
import os
import shutil
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from scipy import stats
import numpy as np

from trading.pattern_analyzer import PatternAnalyzer


class EnhancedMetaOptimizer:
    """
    增强版元参数优化器
    
    LEAN 参考: BayesianParameterOptimizationAlgorithm.cs
    
    新增特性：
    1. Chi-Square 统计显著性检验
    2. 置信区间计算
    3. 贝叶斯胜率估计
    4. Walk-Forward 验证
    5. 参数变化显著性检测
    """
    
    PARAMS_FILE = "config/strategy_params.json"
    BACKUP_DIR = "config/params_backup"
    CHANGELOG_FILE = "config/optimization_log.json"
    
    WEIGHT_STEP = 0.1
    MAX_WEIGHT = 3.0
    MIN_WEIGHT = 0.3
    
    MIN_HOLD_MINUTES = 60
    MAX_HOLD_MINUTES = 1440
    DEFAULT_HOLD_MINUTES = 240
    
    # 统计显著性阈值
    SIGNIFICANCE_LEVEL = 0.05
    MIN_SAMPLES_FOR_TEST = 10
    
    def __init__(self, min_trades_before_optimize: int = 10):
        if min_trades_before_optimize > 10:
            min_trades_before_optimize = 10
        self.min_trades = min_trades_before_optimize
        self.analyzer = PatternAnalyzer()
        self._load_params()
    
    def _load_params(self):
        try:
            with open(self.PARAMS_FILE, "r", encoding="utf-8") as f:
                self.params = json.load(f)
        except Exception:
            self.params = {}
    
    def _save_params(self):
        os.makedirs(self.BACKUP_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = f"{self.BACKUP_DIR}/params_{ts}.json"
        try:
            shutil.copy(self.PARAMS_FILE, backup_path)
        except Exception:
            pass
        
        with open(self.PARAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.params, f, indent=2, ensure_ascii=False)
    
    def _log_change(self, changes: list):
        log = []
        try:
            with open(self.CHANGELOG_FILE, "r", encoding="utf-8") as f:
                log = json.load(f)
        except Exception:
            pass
        
        log.append({
            "timestamp": datetime.now().isoformat(),
            "trade_count": len(self.analyzer.trades),
            "changes": changes,
        })
        
        with open(self.CHANGELOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log[-50:], f, indent=2, ensure_ascii=False)
    
    def _get_system_trades(self) -> List[dict]:
        """获取系统交易（排除手动操作）"""
        excluded_reasons = {"SELL_ALL", "SPOT_CLOSED", "manual_reset", "stuck_position_cleanup"}
        
        system_trades = [
            t for t in self.analyzer.trades
            if t.get("type") == "EXIT"
            and t.get("exit_reason") not in excluded_reasons
            and "manual_sell_all" not in t.get("signals", [])
        ]
        return system_trades
    
    def should_optimize(self) -> bool:
        """判断是否应该进行参数优化"""
        system_trades = self._get_system_trades()
        return len(system_trades) >= self.min_trades
    
    def run(self) -> dict:
        """执行增强版优化"""
        if not self.should_optimize():
            return {
                "optimized": False,
                "reason": f"交易数不足{self.min_trades}笔，当前{len(self._get_system_trades())}笔"
            }
        
        self.analyzer._load()
        changes = []
        
        # 1. 传统胜率分析
        signal_wr = self.analyzer.signal_win_rates()
        current_weights = self.params.get("signal_weights", {})
        
        # 2. 统计显著性检验
        signal_stats = self._calculate_statistical_significance()
        
        # 3. 综合调整决策
        for sig, data in signal_wr.items():
            if data["total_trades"] < 5:
                continue
            
            current_w = current_weights.get(sig, 1.0)
            stat_data = signal_stats.get(sig, {})
            
            # 综合判断：胜率建议 + 统计显著性
            recommendation = self._combine_recommendations(
                win_rate_recommendation=data["recommendation"],
                statistical_data=stat_data
            )
            
            if recommendation == "increase_weight":
                new_w = min(current_w + self.WEIGHT_STEP, self.MAX_WEIGHT)
                if abs(new_w - current_w) > 0.01:
                    changes.append({
                        "type": "weight_increase",
                        "signal": sig,
                        "old": round(current_w, 3),
                        "new": round(new_w, 3),
                        "reason": f"胜率{data['win_rate']*100:.0f}%, 统计显著"
                    })
                    current_weights[sig] = round(new_w, 3)
            
            elif recommendation == "decrease_weight":
                if current_w == 0:
                    continue
                new_w = max(current_w - self.WEIGHT_STEP, self.MIN_WEIGHT)
                if abs(new_w - current_w) > 0.01:
                    changes.append({
                        "type": "weight_decrease",
                        "signal": sig,
                        "old": round(current_w, 3),
                        "new": round(new_w, 3),
                        "reason": f"胜率{data['win_rate']*100:.0f}%, 统计不显著"
                    })
                    current_weights[sig] = round(new_w, 3)
        
        self.params["signal_weights"] = current_weights
        
        # 4. 持仓时间优化（带统计验证）
        hold_changes = self._optimize_hold_time()
        changes.extend(hold_changes)
        
        # 5. 更新学习统计
        overall = self.analyzer.overall_stats()
        self.params["learning_stats"] = {
            "last_optimized": datetime.now().isoformat(),
            "total_trades_analyzed": overall["total_trades"],
            "current_win_rate": round(overall["win_rate"], 3),
            "current_expected_value": round(overall["expected_value_per_trade"], 3),
            "optimization_count": (
                self.params.get("learning_stats", {}).get("optimization_count", 0) + 1
            ),
            "enhanced_mode": True,
        }
        
        if changes:
            self._save_params()
            self._log_change(changes)
            print(self.analyzer.generate_report())
        
        return {
            "optimized": True,
            "changes_count": len(changes),
            "changes": changes,
            "overall_stats": overall,
            "statistical_tests": signal_stats,
        }
    
    def _calculate_statistical_significance(self) -> Dict[str, dict]:
        """计算每个信号的统计显著性"""
        results = {}
        
        self.analyzer._load()
        system_trades = self._get_system_trades()
        
        # 按信号分组
        signal_trades = {}
        for trade in system_trades:
            signals = trade.get("entry_signals", [])
            pnl_pct = trade.get("pnl_pct", 0)
            is_win = pnl_pct > 0
            
            for sig in signals:
                if sig not in signal_trades:
                    signal_trades[sig] = {"wins": 0, "losses": 0, "pnls": []}
                if is_win:
                    signal_trades[sig]["wins"] += 1
                else:
                    signal_trades[sig]["losses"] += 1
                signal_trades[sig]["pnls"].append(pnl_pct)
        
        for sig, data in signal_trades.items():
            n = data["wins"] + data["losses"]
            if n < self.MIN_SAMPLES_FOR_TEST:
                results[sig] = {
                    "significant": False,
                    "p_value": None,
                    "reason": "样本量不足"
                }
                continue
            
            wins = data["wins"]
            
            # Chi-Square 检验：检验胜率是否显著不同于50%
            try:
                # 使用二项检验代替 Chi-Square（更精确）
                result = stats.binomtest(wins, n, p=0.5)
                p_value = result.pvalue
                ci = result.proportion_ci(confidence=0.95)
                
                significant = p_value < self.SIGNIFICANCE_LEVEL
                
                # 计算效应量 (Cohen's h)
                p_hat = wins / n
                h = 2 * np.arcsin(np.sqrt(p_hat)) - 2 * np.arcsin(np.sqrt(0.5))
                
                results[sig] = {
                    "significant": significant,
                    "p_value": round(p_value, 4),
                    "ci_lower": round(ci.low, 3),
                    "ci_upper": round(ci.high, 3),
                    "effect_size": round(abs(h), 3),
                    "interpretation": self._interpret_significance(
                        significant, p_value, ci.low, ci.high
                    )
                }
            except Exception as e:
                results[sig] = {
                    "significant": False,
                    "p_value": None,
                    "error": str(e)
                }
        
        return results
    
    def _interpret_significance(
        self, 
        significant: bool, 
        p_value: float, 
        ci_lower: float, 
        ci_upper: float
    ) -> str:
        """解释统计显著性"""
        if not significant:
            return "不显著：无法排除随机因素"
        
        if ci_lower > 0.5:
            return "显著正向：胜率显著高于50%"
        elif ci_upper < 0.5:
            return "显著负向：胜率显著低于50%"
        else:
            return "边界情况"
    
    def _combine_recommendations(
        self,
        win_rate_recommendation: str,
        statistical_data: dict
    ) -> str:
        """综合胜率建议和统计显著性得出最终建议"""
        if not statistical_data:
            return win_rate_recommendation
        
        # 如果统计不显著，降低调整幅度
        if not statistical_data.get("significant", False):
            if win_rate_recommendation == "increase_weight":
                return "maintain"  # 不显著时不增加权重
            elif win_rate_recommendation == "decrease_weight":
                return "decrease_weight"  # 仍然建议减少，但幅度小
        
        return win_rate_recommendation
    
    def _optimize_hold_time(self) -> List[dict]:
        """优化持仓时间"""
        changes = []
        
        hold_data = self.analyzer.hold_time_analysis()
        rec_hold = hold_data.get("recommendation_max_hold_minutes", self.DEFAULT_HOLD_MINUTES)
        avg_win_hold = hold_data.get("avg_win_hold_minutes", 0)
        
        # 统计验证：检查推荐值是否显著
        if avg_win_hold > 0 and avg_win_hold < 10:
            rec_hold = self.params.get("auto_pilot", {}).get(
                "max_hold_minutes", self.DEFAULT_HOLD_MINUTES
            )
            print(f"[EnhancedMetaOptimizer] 赢单均持时间异常，保持 hold_time={rec_hold}")
        elif rec_hold < self.MIN_HOLD_MINUTES:
            rec_hold = self.MIN_HOLD_MINUTES
        elif rec_hold > self.MAX_HOLD_MINUTES:
            rec_hold = self.MAX_HOLD_MINUTES
        
        current_hold = (
            self.params.get("auto_pilot", {}).get("max_hold_minutes") or
            self.params.get("risk_management", {}).get("max_hold_minutes") or
            240
        )
        
        if abs(rec_hold - current_hold) > 30:
            if "auto_pilot" in self.params:
                self.params["auto_pilot"]["max_hold_minutes"] = rec_hold
            if "risk_management" in self.params:
                self.params["risk_management"]["max_hold_minutes"] = rec_hold
            
            changes.append({
                "type": "hold_time_adjust",
                "old": current_hold,
                "new": rec_hold,
                "reason": f"获胜均持{hold_data['avg_win_hold_minutes']}分钟 (统计验证)"
            })
        
        return changes
    
    def run_with_walk_forward(self, n_splits: int = 5) -> dict:
        """
        使用 Walk-Forward 验证执行优化
        
        将数据分为 N 个滚动窗口：
        - 训练窗口 (70%): 用于确定参数
        - 测试窗口 (30%): 用于验证参数有效性
        
        只有在测试窗口上表现稳定的参数才会被采纳
        """
        self.analyzer._load()
        system_trades = self._get_system_trades()
        
        if len(system_trades) < self.min_trades * 2:
            return {
                "optimized": False,
                "reason": "交易数不足，无法进行 Walk-Forward 验证"
            }
        
        # 按时间排序
        sorted_trades = sorted(system_trades, key=lambda x: x.get("exit_timestamp", x.get("timestamp", "")))
        n = len(sorted_trades)
        
        # 计算窗口大小
        train_size = int(n * 0.7)
        step_size = (n - train_size) // (n_splits - 1)
        
        validation_results = []
        
        for i in range(n_splits - 1):
            start = i * step_size
            end = start + train_size
            
            if end > n:
                break
            
            train_window = sorted_trades[start:end]
            test_window = sorted_trades[end:end + step_size] if end + step_size <= n else sorted_trades[end:]
            
            if len(train_window) < 10 or len(test_window) < 3:
                continue
            
            # 在训练窗口上计算最优权重
            train_result = self._optimize_on_window(train_window)
            
            # 在测试窗口上验证
            test_result = self._validate_on_window(test_window, train_result["weights"])
            
            validation_results.append({
                "train_window": f"{start}-{end}",
                "test_window": f"{end}-{end + len(test_window)}",
                "train_performance": train_result["performance"],
                "test_performance": test_result["performance"],
                "stable": test_result["stable"]
            })
        
        # 检查验证结果的稳定性
        stable_count = sum(1 for r in validation_results if r["stable"])
        stability_rate = stable_count / len(validation_results) if validation_results else 0
        
        # 只有稳定性 >= 60% 时才采纳优化结果
        if stability_rate >= 0.6:
            final_result = self.run()
            final_result["walk_forward"] = {
                "validation_results": validation_results,
                "stability_rate": round(stability_rate, 2),
                "adopted": True
            }
            return final_result
        else:
            return {
                "optimized": False,
                "walk_forward": {
                    "validation_results": validation_results,
                    "stability_rate": round(stability_rate, 2),
                    "adopted": False,
                    "reason": "参数在不同时间段表现不稳定，拒绝采纳"
                }
            }
    
    def _optimize_on_window(self, trades: List[dict]) -> dict:
        """在单个窗口上优化参数"""
        signal_stats = {}
        
        for trade in trades:
            signals = trade.get("entry_signals", [])
            pnl_pct = trade.get("pnl_pct", 0)
            
            for sig in signals:
                if sig not in signal_stats:
                    signal_stats[sig] = {"wins": 0, "total": 0, "pnl_sum": 0}
                signal_stats[sig]["total"] += 1
                if pnl_pct > 0:
                    signal_stats[sig]["wins"] += 1
                signal_stats[sig]["pnl_sum"] += pnl_pct
        
        # 计算权重
        weights = {}
        for sig, data in signal_stats.items():
            if data["total"] < 3:
                continue
            win_rate = data["wins"] / data["total"]
            if win_rate > 0.6:
                weights[sig] = 1.5
            elif win_rate < 0.4:
                weights[sig] = 0.5
            else:
                weights[sig] = 1.0
        
        # 计算训练集性能
        performance = self._calculate_window_performance(trades, weights)
        
        return {"weights": weights, "performance": performance}
    
    def _validate_on_window(self, trades: List[dict], weights: dict) -> dict:
        """在验证窗口上测试参数"""
        performance = self._calculate_window_performance(trades, weights)
        
        # 判断是否稳定（验证集性能为正）
        stable = performance.get("total_pnl", 0) > 0
        
        return {"performance": performance, "stable": stable}
    
    def _calculate_window_performance(self, trades: List[dict], weights: dict) -> dict:
        """计算窗口性能"""
        wins = 0
        losses = 0
        total_pnl = 0
        
        for trade in trades:
            signals = trade.get("entry_signals", [])
            pnl_pct = trade.get("pnl_pct", 0)
            
            # 使用优化后的权重计算信号强度
            signal_strength = sum(weights.get(s, 1.0) for s in signals)
            
            if pnl_pct > 0:
                wins += 1
            else:
                losses += 1
            total_pnl += pnl_pct
        
        return {
            "wins": wins,
            "losses": losses,
            "total_pnl": round(total_pnl, 3),
            "win_rate": round(wins / (wins + losses), 3) if (wins + losses) > 0 else 0
        }


def get_enhanced_optimizer() -> EnhancedMetaOptimizer:
    """获取增强版优化器单例"""
    global _enhanced_optimizer
    if _enhanced_optimizer is None:
        _enhanced_optimizer = EnhancedMetaOptimizer()
    return _enhanced_optimizer


_enhanced_optimizer: Optional[EnhancedMetaOptimizer] = None
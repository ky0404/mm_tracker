"""
增强参数优化器 - 统一入口
整合 SignalConfidenceCalculator、EnhancedMetaOptimizer、HoldTimeOptimizer、WalkForwardValidator
基于 LEAN 的 ParameterOptimizationEngine 设计
"""
import json
import os
import shutil
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable

from trading.signal_confidence_calculator import SignalConfidenceCalculator
from trading.enhanced_meta_optimizer import EnhancedMetaOptimizer
from trading.hold_time_optimizer import HoldTimeOptimizer
from trading.walkforward_validator import WalkForwardValidator


class EnhancedParameterOptimizer:
    """
    增强参数优化器 - 统一入口
    
    LEAN 参考: ParameterOptimizationEngine.cs
    
    整合所有专业优化组件：
    1. SignalConfidenceCalculator - 信号置信度计算
    2. EnhancedMetaOptimizer - 增强版元优化（含统计显著性）
    3. HoldTimeOptimizer - 持仓时间优化
    4. WalkForwardValidator - Walk-Forward验证
    """
    
    PARAMS_FILE = "config/strategy_params.json"
    BACKUP_DIR = "config/params_backup"
    OPT_LOG_FILE = "config/enhanced_optimization_log.json"
    
    def __init__(
        self,
        min_trades: int = 10,
        enable_walkforward: bool = True,
        enable_confidence: bool = True
    ):
        self.min_trades = min_trades
        self.enable_walkforward = enable_walkforward
        self.enable_confidence = enable_confidence
        
        # 初始化子优化器
        self.confidence_calc = SignalConfidenceCalculator() if enable_confidence else None
        self.meta_optimizer = EnhancedMetaOptimizer(min_trades)
        self.hold_optimizer = HoldTimeOptimizer(min_trades)
        self.walkforward_validator = WalkForwardValidator() if enable_walkforward else None
        
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
        backup_path = f"{self.BACKUP_DIR}/enhanced_params_{ts}.json"
        try:
            shutil.copy(self.PARAMS_FILE, backup_path)
        except Exception:
            pass
        
        with open(self.PARAMS_FILE, "w", encoding="utf-8") as f:
            json.dump(self.params, f, indent=2, ensure_ascii=False)
    
    def _log_optimization(self, result: dict):
        log = []
        try:
            if os.path.exists(self.OPT_LOG_FILE):
                with open(self.OPT_LOG_FILE, "r", encoding="utf-8") as f:
                    log = json.load(f)
        except Exception:
            pass
        
        log.append({
            "timestamp": datetime.now().isoformat(),
            "result": result
        })
        
        with open(self.OPT_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log[-30:], f, indent=2, ensure_ascii=False)
    
    def _get_system_trades(self) -> List[dict]:
        """获取系统交易"""
        from trading.result_logger import ResultLogger
        
        try:
            logger = ResultLogger()
            all_trades = logger.get_finished_trades()
        except Exception:
            return []
        
        excluded_reasons = {"SELL_ALL", "SPOT_CLOSED", "manual_reset", "stuck_position_cleanup"}
        
        return [
            t for t in all_trades
            if t.get("type") == "EXIT"
            and t.get("exit_reason") not in excluded_reasons
            and "manual_sell_all" not in t.get("signals", [])
        ]
    
    def run_full_optimization(self) -> dict:
        """
        执行完整优化流程
        
        流程：
        1. 加载交易数据
        2. 计算信号置信度
        3. 运行增强版元优化
        4. 优化持仓时间
        5. Walk-Forward验证（可选）
        6. 保存优化结果
        """
        # 1. 加载数据
        system_trades = self._get_system_trades()
        
        if len(system_trades) < self.min_trades:
            return {
                "success": False,
                "reason": f"交易数不足: {len(system_trades)}/{self.min_trades}",
                "phase": "data_loading"
            }
        
        results = {
            "phase": "started",
            "timestamp": datetime.now().isoformat(),
            "trades_analyzed": len(system_trades),
            "changes": []
        }
        
        # 2. 信号置信度分析
        if self.confidence_calc:
            results["phase"] = "confidence_analysis"
            try:
                confidence_results = self.confidence_calc.get_all_confidences(system_trades)
                results["confidence_analysis"] = confidence_results
                
                # 基于置信度调整权重
                weight_changes = self._apply_confidence_adjustments(confidence_results)
                results["changes"].extend(weight_changes)
            except Exception as e:
                results["confidence_error"] = str(e)
        
        # 3. 增强版元优化
        results["phase"] = "meta_optimization"
        try:
            meta_result = self.meta_optimizer.run()
            results["meta_optimization"] = meta_result
            results["changes"].extend(meta_result.get("changes", []))
        except Exception as e:
            results["meta_error"] = str(e)
        
        # 4. 持仓时间优化
        results["phase"] = "hold_time_optimization"
        try:
            hold_result = self.hold_optimizer.analyze(system_trades)
            results["hold_time_optimization"] = hold_result
            
            if hold_result.get("sufficient_data"):
                # 应用持仓时间优化
                hold_change = self._apply_hold_time_recommendation(
                    hold_result["recommendation"]
                )
                if hold_change:
                    results["changes"].append(hold_change)
        except Exception as e:
            results["hold_time_error"] = str(e)
        
        # 5. Walk-Forward验证
        if self.walkforward_validator and len(system_trades) >= 20:
            results["phase"] = "walkforward_validation"
            try:
                # 创建优化函数
                def optimization_fn(window_trades):
                    meta = EnhancedMetaOptimizer(min_trades_before_optimize=5)
                    # 临时加载窗口数据
                    from trading.pattern_analyzer import PatternAnalyzer
                    analyzer = PatternAnalyzer()
                    analyzer.trades = window_trades
                    # 简单优化逻辑
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
                    
                    weights = {}
                    for sig, data in signal_stats.items():
                        if data["total"] < 3:
                            continue
                        wr = data["wins"] / data["total"]
                        if wr > 0.6:
                            weights[sig] = 1.3
                        elif wr < 0.4:
                            weights[sig] = 0.7
                        else:
                            weights[sig] = 1.0
                    
                    return {"weights": weights, "performance": {}}
                
                wf_result = self.walkforward_validator.validate(
                    system_trades, optimization_fn
                )
                results["walkforward_validation"] = wf_result
                
                # 如果验证不通过，撤销所有变更
                if not wf_result.get("adopted"):
                    results["changes"] = []
                    results["walkforward_rejected"] = True
                    results["rejection_reason"] = wf_result.get("recommendation")
            except Exception as e:
                results["walkforward_error"] = str(e)
        
        # 6. 保存结果
        results["phase"] = "saving"
        results["total_changes"] = len(results["changes"])
        
        if results["changes"]:
            self._save_params()
            results["success"] = True
            results["message"] = f"优化完成，共{len(results['changes'])}项变更"
        else:
            results["success"] = True
            results["message"] = "无需变更，当前参数已最优"
        
        # 7. 记录日志
        results["phase"] = "completed"
        self._log_optimization(results)
        
        return results
    
    def _apply_confidence_adjustments(self, confidence_results: dict) -> List[dict]:
        """基于置信度应用权重调整"""
        changes = []
        current_weights = self.params.get("signal_weights", {})
        
        for signal, conf_data in confidence_results.items():
            recommendation = conf_data.get("recommendation", "maintain")
            base_weight = current_weights.get(signal, 1.0)
            
            if recommendation == "increase_weight":
                new_weight = min(base_weight * 1.2, 3.0)
                if abs(new_weight - base_weight) > 0.05:
                    changes.append({
                        "type": "confidence_weight_increase",
                        "signal": signal,
                        "old": round(base_weight, 3),
                        "new": round(new_weight, 3),
                        "confidence": conf_data.get("confidence", 0),
                        "reason": f"置信度{conf_data.get('confidence', 0):.1%}, 建议增加"
                    })
                    current_weights[signal] = round(new_weight, 3)
            
            elif recommendation == "reduce_weight":
                new_weight = max(base_weight * 0.7, 0.3)
                if abs(new_weight - base_weight) > 0.05:
                    changes.append({
                        "type": "confidence_weight_decrease",
                        "signal": signal,
                        "old": round(base_weight, 3),
                        "new": round(new_weight, 3),
                        "confidence": conf_data.get("confidence", 0),
                        "reason": f"置信度{conf_data.get('confidence', 0):.1%}, 建议降低"
                    })
                    current_weights[signal] = round(new_weight, 3)
            
            elif recommendation == "disable":
                if base_weight > 0:
                    changes.append({
                        "type": "signal_disabled",
                        "signal": signal,
                        "old": round(base_weight, 3),
                        "new": 0,
                        "reason": f"置信度过低: {conf_data.get('confidence', 0):.1%}"
                    })
                    current_weights[signal] = 0
        
        self.params["signal_weights"] = current_weights
        return changes
    
    def _apply_hold_time_recommendation(self, recommended: int) -> Optional[dict]:
        """应用持仓时间推荐"""
        current_hold = (
            self.params.get("auto_pilot", {}).get("max_hold_minutes") or
            self.params.get("risk_management", {}).get("max_hold_minutes") or
            240
        )
        
        if abs(recommended - current_hold) > 30:
            if "auto_pilot" not in self.params:
                self.params["auto_pilot"] = {}
            if "risk_management" not in self.params:
                self.params["risk_management"] = {}
            
            self.params["auto_pilot"]["max_hold_minutes"] = recommended
            self.params["risk_management"]["max_hold_minutes"] = recommended
            
            return {
                "type": "hold_time_optimized",
                "old": current_hold,
                "new": recommended,
                "reason": "基于持仓时间统计分析"
            }
        
        return None
    
    def run_quick_optimization(self) -> dict:
        """
        快速优化 - 仅执行基础元优化
        用于每次交易后快速反馈
        """
        system_trades = self._get_system_trades()
        
        if len(system_trades) < self.min_trades:
            return {
                "success": False,
                "reason": f"交易数不足: {len(system_trades)}/{self.min_trades}"
            }
        
        # 仅运行基础元优化
        result = self.meta_optimizer.run()
        
        if result.get("optimized") and result.get("changes_count", 0) > 0:
            self._save_params()
            return {
                "success": True,
                "changes": result.get("changes", []),
                "message": f"快速优化完成，{result['changes_count']}项变更"
            }
        
        return {
            "success": True,
            "message": "无需优化，当前参数已最优"
        }
    
    def get_confidence_report(self) -> dict:
        """获取置信度报告"""
        system_trades = self._get_system_trades()
        
        if not self.confidence_calc:
            return {"error": "置信度计算未启用"}
        
        if len(system_trades) < 10:
            return {"error": "交易数不足"}
        
        return self.confidence_calc.get_all_confidences(system_trades)
    
    def get_optimization_status(self) -> dict:
        """获取优化状态"""
        system_trades = self._get_system_trades()
        
        return {
            "total_system_trades": len(system_trades),
            "min_trades_required": self.min_trades,
            "ready_to_optimize": len(system_trades) >= self.min_trades,
            "features": {
                "confidence_enabled": self.confidence_calc is not None,
                "walkforward_enabled": self.walkforward_validator is not None,
                "hold_time_optimization": True
            }
        }


def get_enhanced_optimizer() -> EnhancedParameterOptimizer:
    """获取增强参数优化器单例"""
    global _enhanced_parameter_optimizer
    if _enhanced_parameter_optimizer is None:
        _enhanced_parameter_optimizer = EnhancedParameterOptimizer()
    return _enhanced_parameter_optimizer


_enhanced_parameter_optimizer: Optional[EnhancedParameterOptimizer] = None
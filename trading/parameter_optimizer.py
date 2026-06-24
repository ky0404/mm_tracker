"""
参数优化器 - Parameter Optimizer
功能：分析交易历史，自动调整信号权重和风控参数
"""
import json
import logging
from typing import Dict, Any
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


class ParameterOptimizer:
    """参数优化器 - 基于交易结果自动调整参数"""

    def __init__(self, result_logger, params_file: str = "config/strategy_params.json"):
        self.result_logger = result_logger
        self.params_file = params_file
        self.params = self._load_params()
        self.signal_stats = defaultdict(lambda: {"wins": 0, "total": 0})

    def _load_params(self) -> Dict[str, Any]:
        try:
            with open(self.params_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("参数文件不存在，使用默认配置")
            return {}

    def _save_params(self):
        self.params["updated_at"] = datetime.now().isoformat()
        with open(self.params_file, "w", encoding="utf-8") as f:
            json.dump(self.params, f, ensure_ascii=False, indent=2)
        logger.info(f"[参数优化] 已保存参数到 {self.params_file}")

    def analyze_trades(self) -> Dict[str, Any]:
        finished = self.result_logger.get_finished_trades()
        if not finished:
            return {"total_trades": 0, "message": "没有完成的交易"}
        
        signal_wins = defaultdict(lambda: {"wins": 0, "total": 0, "pnl": 0})
        
        for trade in finished:
            signals = trade.get("signals", [])
            win = trade.get("win", False)
            pnl = trade.get("pnl", 0)
            for sig in signals:
                signal_wins[sig]["total"] += 1
                if win:
                    signal_wins[sig]["wins"] += 1
                    signal_wins[sig]["pnl"] += pnl
        
        signal_stats = {}
        for sig, stats in signal_wins.items():
            win_rate = stats["wins"] / stats["total"] if stats["total"] > 0 else 0
            signal_stats[sig] = {
                "win_rate": win_rate,
                "total": stats["total"],
                "wins": stats["wins"],
                "losses": stats["total"] - stats["wins"],
                "avg_pnl": stats["pnl"] / stats["total"] if stats["total"] > 0 else 0,
            }
        
        total_wins = sum(s["wins"] for s in signal_stats.values())
        total_trades = sum(s["total"] for s in signal_stats.values())
        overall_win_rate = total_wins / total_trades if total_trades > 0 else 0
        
        return {
            "total_trades": len(finished),
            "overall_win_rate": overall_win_rate,
            "signal_stats": signal_stats,
        }

    def optimize(self, force: bool = False) -> Dict[str, Any]:
        finished = self.result_logger.get_finished_trades()
        opt_interval = self.params.get("auto_pilot", {}).get("optimization_interval_trades", 5)
        
        if len(finished) < opt_interval and not force:
            return {
                "optimized": False,
                "reason": f"交易数量不足 ({len(finished)}/{opt_interval})",
                "next_optimization": opt_interval - len(finished),
            }
        
        analysis = self.analyze_trades()
        if analysis.get("total_trades", 0) == 0:
            return {"optimized": False, "reason": "没有完成的交易"}
        
        signal_weights = self.params.get("signal_weights", {})
        signal_stats = analysis.get("signal_stats", {})
        adjustments = []
        
        for sig, weight in signal_weights.items():
            if sig in signal_stats:
                stats = signal_stats[sig]
                win_rate = stats["win_rate"]
                if win_rate >= 0.7:
                    new_weight = min(weight * 1.2, 2.0)
                    adjustments.append(f"{sig}: {weight:.2f} -> {new_weight:.2f} (胜率 {win_rate:.1%})")
                elif win_rate <= 0.3:
                    new_weight = max(weight * 0.7, 0.3)
                    adjustments.append(f"{sig}: {weight:.2f} -> {new_weight:.2f} (胜率 {win_rate:.1%})")
                else:
                    new_weight = weight
                signal_weights[sig] = round(new_weight, 2)
        
        overall_wr = analysis.get("overall_win_rate", 0.5)
        risk_mgmt = self.params.get("risk_management", {})
        current_size = risk_mgmt.get("default_position_size", 50)
        if overall_wr >= 0.6:
            new_size = min(current_size * 1.2, 200)
            risk_mgmt["default_position_size"] = new_size
            adjustments.append(f"仓位: {current_size} -> {new_size:.0f}U (胜率{overall_wr*100:.0f}%)")
        elif overall_wr <= 0.3:
            new_size = max(current_size * 0.7, 10)
            risk_mgmt["default_position_size"] = new_size
            adjustments.append(f"仓位: {current_size} -> {new_size:.0f}U (胜率{overall_wr*100:.0f}%)")
            adjustments.append(f"仓位: {risk_mgmt.get('default_position_size')} (减少)")
        
        self.params["signal_weights"] = signal_weights
        self.params["risk_management"] = risk_mgmt
        
        protected_params = self.params.get("protected_params", {})
        if "max_open_positions" not in protected_params:
            protected_params["max_open_positions"] = {"min": 2, "max": 5, "default": 3}
        if "max_open_positions" in self.params:
            bounds = protected_params["max_open_positions"]
            self.params["max_open_positions"] = max(bounds["min"], min(self.params["max_open_positions"], bounds["max"]))
            logger.info(f"[参数保护] max_open_positions 限制在 {bounds['min']}-{bounds['max']} 之间")
        
        if "max_hold_minutes" not in protected_params:
            protected_params["max_hold_minutes"] = {"min": 60, "max": 240, "default": 120}
        if "max_hold_minutes" in self.params:
            bounds = protected_params["max_hold_minutes"]
            self.params["max_hold_minutes"] = max(bounds["min"], min(self.params["max_hold_minutes"], bounds["max"]))
            logger.info(f"[参数保护] max_hold_minutes 限制在 {bounds['min']}-{bounds['max']} 之间")
        
        self.params["protected_params"] = protected_params
        self.params["signal_stats"]["total_trades"] = len(finished)
        self.params["signal_stats"]["win_rate_by_signal"] = signal_stats
        self.params["signal_stats"]["last_optimization"] = datetime.now().isoformat()
        
        self._save_params()
        
        return {
            "optimized": True,
            "total_trades": len(finished),
            "overall_win_rate": overall_wr,
            "adjustments": adjustments,
            "new_weights": signal_weights,
        }

    def run(self) -> Dict[str, Any]:
        """
        自动执行优化 - 优先增强版，回退到基础版
        这是 auto_pilot.py 调用的入口方法
        """
        result = self._run_enhanced()
        if result.get("optimized") and result.get("changes"):
            return result
        return self.optimize(force=True)

    def _run_enhanced(self) -> Dict[str, Any]:
        """增强版优化 - 整合置信度、持仓时间优化"""
        try:
            from trading.signal_confidence_calculator import SignalConfidenceCalculator
            from trading.hold_time_optimizer import HoldTimeOptimizer
        except ImportError as e:
            logger.warning(f"增强优化组件导入失败: {e}")
            return {"optimized": False, "reason": "组件导入失败"}
        
        try:
            all_trades = self.result_logger.get_finished_trades()
        except Exception:
            all_trades = []
        
        excluded_reasons = {"SELL_ALL", "SPOT_CLOSED", "manual_reset", "stuck_position_cleanup"}
        system_trades = [
            t for t in all_trades
            if t.get("type") == "EXIT"
            and t.get("exit_reason") not in excluded_reasons
            and "manual_sell_all" not in (t.get("signals") or [])
        ]
        
        if len(system_trades) < 10:
            return {"optimized": False, "reason": f"系统交易数不足: {len(system_trades)}/10", "enhanced": True}
        
        changes = []
        
        # 1. 信号置信度分析
        try:
            confidence_calc = SignalConfidenceCalculator()
            confidence_results = confidence_calc.get_all_confidences(system_trades)
            current_weights = self.params.get("signal_weights", {})
            for signal, conf_data in confidence_results.items():
                recommendation = conf_data.get("recommendation", "maintain")
                base_weight = current_weights.get(signal, 1.0)
                if recommendation == "increase_weight":
                    new_weight = min(base_weight * 1.2, 3.0)
                    if abs(new_weight - base_weight) > 0.05:
                        changes.append({"type": "confidence_weight_increase", "signal": signal, "old": round(base_weight, 3), "new": round(new_weight, 3), "reason": f"置信度{conf_data.get('confidence', 0):.1%}"})
                        current_weights[signal] = round(new_weight, 3)
                elif recommendation == "reduce_weight":
                    new_weight = max(base_weight * 0.7, 0.3)
                    if abs(new_weight - base_weight) > 0.05:
                        changes.append({"type": "confidence_weight_decrease", "signal": signal, "old": round(base_weight, 3), "new": round(new_weight, 3), "reason": f"置信度{conf_data.get('confidence', 0):.1%}"})
                        current_weights[signal] = round(new_weight, 3)
                elif recommendation == "disable":
                    if base_weight > 0:
                        changes.append({"type": "signal_disabled", "signal": signal, "old": round(base_weight, 3), "new": 0, "reason": "置信度过低"})
                        current_weights[signal] = 0
            self.params["signal_weights"] = current_weights
        except Exception as e:
            logger.warning(f"置信度分析失败: {e}")
        
        # 2. 持仓时间优化
        try:
            hold_optimizer = HoldTimeOptimizer(min_trades=10)
            hold_result = hold_optimizer.analyze(system_trades)
            if hold_result.get("sufficient_data"):
                recommended = hold_result.get("recommendation", 240)
                current_hold = self.params.get("auto_pilot", {}).get("max_hold_minutes", 240)
                if abs(recommended - current_hold) > 30:
                    if "auto_pilot" not in self.params:
                        self.params["auto_pilot"] = {}
                    self.params["auto_pilot"]["max_hold_minutes"] = recommended
                    changes.append({"type": "hold_time_optimized", "old": current_hold, "new": recommended, "reason": "基于统计分布"})
        except Exception as e:
            logger.warning(f"持仓时间优化失败: {e}")
        
        if changes:
            self._save_params()
            logger.info(f"[增强优化] 完成 {len(changes)} 项变更")
        
        return {"optimized": True, "enhanced": True, "changes_count": len(changes), "changes": changes, "system_trades": len(system_trades)}

    def get_current_params(self) -> Dict[str, Any]:
        return self.params

    def reload_params(self):
        self.params = self._load_params()
        logger.info("[参数优化] 已重新加载参数")

    def get_optimization_status(self) -> Dict[str, Any]:
        try:
            all_trades = self.result_logger.get_finished_trades()
            excluded_reasons = {"SELL_ALL", "SPOT_CLOSED", "manual_reset", "stuck_position_cleanup"}
            system_trades = [t for t in all_trades if t.get("type") == "EXIT" and t.get("exit_reason") not in excluded_reasons]
            return {"total_system_trades": len(system_trades), "min_trades_required": 10, "ready_to_optimize": len(system_trades) >= 10, "enhanced_mode": True}
        except Exception as e:
            return {"error": str(e)}


if __name__ == "__main__":
    from trading.result_logger import ResultLogger
    logger = ResultLogger()
    optimizer = ParameterOptimizer(logger)
    result = optimizer.run()
    print(f"优化结果: {result}")
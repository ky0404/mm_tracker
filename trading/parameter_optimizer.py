"""
参数优化器 - Parameter Optimizer
功能：分析交易历史，自动调整信号权重和风控参数
"""
import json
import logging
from typing import Dict, Any, List
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


class ParameterOptimizer:
    """参数优化器 - 基于交易结果自动调整参数"""

    def __init__(self, result_logger, params_file: str = "config/strategy_params.json"):
        self.result_logger = result_logger
        self.params_file = params_file
        self.params = self._load_params()
        
        # 统计每个信号的胜率
        self.signal_stats = defaultdict(lambda: {"wins": 0, "total": 0})

    def _load_params(self) -> Dict[str, Any]:
        """加载参数配置"""
        try:
            with open(self.params_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning(f"参数文件不存在，使用默认配置")
            return {}

    def _save_params(self):
        """保存参数配置"""
        self.params["updated_at"] = datetime.now().isoformat()
        with open(self.params_file, "w", encoding="utf-8") as f:
            json.dump(self.params, f, ensure_ascii=False, indent=2)
        logger.info(f"[参数优化] 已保存参数到 {self.params_file}")

    def analyze_trades(self) -> Dict[str, Any]:
        """
        分析已完成交易，计算各信号胜率
        :return: 统计分析结果
        """
        finished = self.result_logger.get_finished_trades()
        
        if not finished:
            return {"total_trades": 0, "message": "没有完成的交易"}
        
        # 按信号分组统计
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
        
        # 计算胜率
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
        
        # 总体统计
        total_wins = sum(s["wins"] for s in signal_stats.values())
        total_trades = sum(s["total"] for s in signal_stats.values())
        overall_win_rate = total_wins / total_trades if total_trades > 0 else 0
        
        return {
            "total_trades": len(finished),
            "overall_win_rate": overall_win_rate,
            "signal_stats": signal_stats,
        }

    def optimize(self, force: bool = False) -> Dict[str, Any]:
        """
        执行参数优化 - 修复: 启用日内杠杆模式优化
        :param force: 是否强制优化（忽略时间间隔）
        :return: 优化结果
        """
        # 检查交易数量是否足够 (修复: 不再禁用日内/动量模式)
        finished = self.result_logger.get_finished_trades()
        
        # 从 config 获取优化间隔 (默认5笔触发优化)
        opt_interval = self.params.get("auto_pilot", {}).get("optimization_interval_trades", 5)
        
        if len(finished) < opt_interval and not force:
            return {
                "optimized": False,
                "reason": f"交易数量不足 ({len(finished)}/{opt_interval})",
                "next_optimization": opt_interval - len(finished),
            }
        
        # 分析交易
        analysis = self.analyze_trades()
        
        if analysis.get("total_trades", 0) == 0:
            return {"optimized": False, "reason": "没有完成的交易"}
        
        # 调整信号权重
        signal_weights = self.params.get("signal_weights", {})
        signal_stats = analysis.get("signal_stats", {})
        
        adjustments = []
        
        for sig, weight in signal_weights.items():
            if sig in signal_stats:
                stats = signal_stats[sig]
                win_rate = stats["win_rate"]
                
                # 根据胜率调整权重
                if win_rate >= 0.7:
                    # 高胜率，增加权重
                    new_weight = min(weight * 1.2, 2.0)
                    adjustments.append(f"{sig}: {weight:.2f} -> {new_weight:.2f} (胜率 {win_rate:.1%})")
                elif win_rate <= 0.3:
                    # 低胜率，减少权重
                    new_weight = max(weight * 0.7, 0.3)
                    adjustments.append(f"{sig}: {weight:.2f} -> {new_weight:.2f} (胜率 {win_rate:.1%})")
                else:
                    new_weight = weight
                
                signal_weights[sig] = round(new_weight, 2)
        
        # 调整风控参数
        overall_wr = analysis.get("overall_win_rate", 0.5)
        risk_mgmt = self.params.get("risk_management", {})
        
        if overall_wr >= 0.6:
            # 高胜率，可以稍微增加仓位
            risk_mgmt["default_position_size"] = min(
                risk_mgmt.get("default_position_size", 10) * 1.1, 50
            )
            adjustments.append(f"仓位: {risk_mgmt.get('default_position_size')}")
        elif overall_wr <= 0.3:
            # 低胜率，减少仓位
            risk_mgmt["default_position_size"] = max(
                risk_mgmt.get("default_position_size", 10) * 0.8, 5
            )
            adjustments.append(f"仓位: {risk_mgmt.get('default_position_size')} (减少)")
        
        # 保存更新
        self.params["signal_weights"] = signal_weights
        self.params["risk_management"] = risk_mgmt
        
        # ========== 保护关键参数不被优化器随意改变 ==========
        # 设置参数边界，防止优化器做出不合理的改动
        protected_params = self.params.get("protected_params", {})
        
        # max_open_positions: 保持在 2-5 之间，不允许太低
        if "max_open_positions" not in protected_params:
            protected_params["max_open_positions"] = {"min": 2, "max": 5, "default": 3}
        
        # 应用保护边界
        if "max_open_positions" in self.params:
            bounds = protected_params["max_open_positions"]
            self.params["max_open_positions"] = max(bounds["min"], min(self.params["max_open_positions"], bounds["max"]))
            logger.info(f"[参数保护] max_open_positions 限制在 {bounds['min']}-{bounds['max']} 之间: {self.params['max_open_positions']}")
        
        # max_hold_minutes: 保持在 60-240 分钟之间
        if "max_hold_minutes" not in protected_params:
            protected_params["max_hold_minutes"] = {"min": 60, "max": 240, "default": 120}
        
        if "max_hold_minutes" in self.params:
            bounds = protected_params["max_hold_minutes"]
            self.params["max_hold_minutes"] = max(bounds["min"], min(self.params["max_hold_minutes"], bounds["max"]))
            logger.info(f"[参数保护] max_hold_minutes 限制在 {bounds['min']}-{bounds['max']} 之间: {self.params['max_hold_minutes']}")
        
        self.params["protected_params"] = protected_params
        # ========== 参数保护结束 ==========
        
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

    def get_current_params(self) -> Dict[str, Any]:
        """获取当前参数"""
        return self.params

    def reload_params(self):
        """重新加载参数"""
        self.params = self._load_params()
        logger.info("[参数优化] 已重新加载参数")


if __name__ == "__main__":
    from trading.result_logger import ResultLogger
    
    logger = ResultLogger()
    optimizer = ParameterOptimizer(logger)
    
    # 模拟一些交易
    for i in range(6):
        idx = logger.log_entry(
            token="BTC",
            signals=["signal_4_volume_spike", "signal_8_wash_test"],
            score=5.0,
            entry_price=63000.0,
            entry_signals_count=4,
            position_size=0.1,
        )
        
        # 随机胜负
        import random
        win = random.random() > 0.5
        pnl = 100 if win else -50
        
        logger.log_exit(
            trade_index=idx,
            exit_price=63000 + (63000 * pnl / 100),
            pnl=pnl,
            exit_reason="TAKE_PROFIT" if win else "STOP_LOSS",
        )
    
    # 分析
    analysis = optimizer.analyze_trades()
    print("分析结果:", json.dumps(analysis, indent=2))
    
    # 优化
    result = optimizer.optimize(force=True)
    print("优化结果:", json.dumps(result, indent=2))
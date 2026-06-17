"""
元参数优化器
用真实交易数据自动调整系统参数
每N笔交易触发一次，保守调整，避免过拟合
"""
import json
import os
import shutil
from datetime import datetime
from typing import Dict, Any

from trading.pattern_analyzer import PatternAnalyzer


class MetaOptimizer:
    PARAMS_FILE = "config/strategy_params.json"
    BACKUP_DIR = "config/params_backup"
    CHANGELOG_FILE = "config/optimization_log.json"

    WEIGHT_STEP = 0.1
    MAX_WEIGHT = 3.0
    MIN_WEIGHT = 0.3

    MIN_HOLD_MINUTES = 60
    MAX_HOLD_MINUTES = 1440

    def __init__(self, min_trades_before_optimize: int = 20):
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

    def should_optimize(self) -> bool:
        return self.analyzer.enough_data(self.min_trades)

    def run(self) -> dict:
        if not self.should_optimize():
            return {
                "optimized": False,
                "reason": f"交易数不足{self.min_trades}笔，当前{len(self.analyzer.trades)}笔"
            }

        self.analyzer._load()
        changes = []

        signal_wr = self.analyzer.signal_win_rates()
        current_weights = self.params.get("signal_weights", {})

        for sig, data in signal_wr.items():
            if data["total_trades"] < 5:
                continue

            current_w = current_weights.get(sig, 1.0)
            rec = data["recommendation"]

            if rec == "increase_weight":
                new_w = min(current_w + self.WEIGHT_STEP, self.MAX_WEIGHT)
                if abs(new_w - current_w) > 0.01:
                    changes.append({
                        "type": "weight_increase",
                        "signal": sig,
                        "old": round(current_w, 3),
                        "new": round(new_w, 3),
                        "reason": f"胜率{data['win_rate']*100:.0f}% (>65%)"
                    })
                    current_weights[sig] = round(new_w, 3)

            elif rec == "decrease_weight":
                # 如果当前权重已经是0，保持禁用状态
                if current_w == 0:
                    continue
                new_w = max(current_w - self.WEIGHT_STEP, self.MIN_WEIGHT)
                if abs(new_w - current_w) > 0.01:
                    changes.append({
                        "type": "weight_decrease",
                        "signal": sig,
                        "old": round(current_w, 3),
                        "new": round(new_w, 3),
                        "reason": f"胜率{data['win_rate']*100:.0f}% (<40%)"
                    })
                    current_weights[sig] = round(new_w, 3)

        self.params["signal_weights"] = current_weights

        hold_data = self.analyzer.hold_time_analysis()
        rec_hold = hold_data["recommendation_max_hold_minutes"]

        if rec_hold < self.MIN_HOLD_MINUTES or rec_hold > self.MAX_HOLD_MINUTES:
            print(f"[MetaOptimizer] hold_time推荐值异常({rec_hold}分钟)，跳过调整，保持默认240分钟")
            rec_hold = 240

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
                "reason": f"获胜均持{hold_data['avg_win_hold_minutes']}分钟"
            })

        overall = self.analyzer.overall_stats()
        self.params["learning_stats"] = {
            "last_optimized": datetime.now().isoformat(),
            "total_trades_analyzed": overall["total_trades"],
            "current_win_rate": round(overall["win_rate"], 3),
            "current_expected_value": round(overall["expected_value_per_trade"], 3),
            "optimization_count": (
                self.params.get("learning_stats", {}).get("optimization_count", 0) + 1
            ),
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
        }
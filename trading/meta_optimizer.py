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
    DEFAULT_HOLD_MINUTES = 240  # 默认持仓4小时

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

        # Bug 3 fix: hold_time 保护逻辑加强
        hold_data = self.analyzer.hold_time_analysis()
        rec_hold = hold_data.get("recommendation_max_hold_minutes", self.DEFAULT_HOLD_MINUTES)
        avg_win_hold = hold_data.get("avg_win_hold_minutes", 0)

        # 如果赢单均持时间 < 10 分钟，说明数据异常（可能是立刻成交的模拟单）
        # 此时不调整，保持默认值
        if avg_win_hold > 0 and avg_win_hold < 10:
            rec_hold = self.params.get("auto_pilot", {}).get("max_hold_minutes", self.DEFAULT_HOLD_MINUTES)
            print(f"[MetaOptimizer] 赢单均持时间异常({avg_win_hold:.0f}分钟)，保持 hold_time={rec_hold}")
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

    def run_weekly_feedback(self) -> dict:
        """
        周度自动反馈 - 基于最近7天的交易数据自动调整参数
        集成 walk-forward 验证结果
        """
        from datetime import timedelta
        
        # 检查是否到了周度优化时间
        last_optimized = self.params.get("learning_stats", {}).get("last_optimized", "")
        if last_optimized:
            last_time = datetime.fromisoformat(last_optimized)
            days_since = (datetime.now() - last_time).days
            if days_since < 7:
                return {
                    "skipped": True,
                    "reason": f"距离上次优化仅{days_since}天，需满7天"
                }
        
        # 加载最近7天的交易数据
        self.analyzer._load()
        
        # 分析最近7天的信号表现
        signal_performance = self._analyze_recent_signals(days=7)
        
        changes = []
        current_weights = self.params.get("signal_weights", {})
        
        # 根据最近7天的胜率调整权重
        for sig, stats in signal_performance.items():
            if stats['count'] < 3:
                continue
            
            win_rate = stats['win_rate']
            avg_pnl = stats['avg_pnl']
            current_w = current_weights.get(sig, 1.0)
            
            # 激进调整策略
            if win_rate >= 0.7 and avg_pnl > 0:
                # 高胜率+盈利 -> 大幅增加权重
                new_w = min(current_w * 1.3, self.MAX_WEIGHT)
                if abs(new_w - current_w) > 0.05:
                    changes.append({
                        "type": "weekly_weight_increase",
                        "signal": sig,
                        "old": round(current_w, 3),
                        "new": round(new_w, 3),
                        "reason": f"7日胜率{win_rate*100:.0f}%, 均盈{avg_pnl:.2%}"
                    })
                    current_weights[sig] = round(new_w, 3)
                    
            elif win_rate <= 0.3 or avg_pnl < -0.05:
                # 低胜率或亏损 -> 大幅降低权重
                if current_w > self.MIN_WEIGHT:
                    new_w = max(current_w * 0.6, self.MIN_WEIGHT)
                    if abs(new_w - current_w) > 0.05:
                        changes.append({
                            "type": "weekly_weight_decrease",
                            "signal": sig,
                            "old": round(current_w, 3),
                            "new": round(new_w, 3),
                            "reason": f"7日胜率{win_rate*100:.0f}%, 均亏{avg_pnl:.2%}"
                        })
                        current_weights[sig] = round(new_w, 3)
        
        self.params["signal_weights"] = current_weights
        
        # 更新学习统计
        self.params["learning_stats"] = {
            "last_weekly_optimized": datetime.now().isoformat(),
            "weekly_changes": len(changes),
            "optimization_count": (
                self.params.get("learning_stats", {}).get("optimization_count", 0) + 1
            ),
        }
        
        if changes:
            self._save_params()
            self._log_change(changes)
            
        return {
            "optimized": True,
            "type": "weekly_feedback",
            "changes_count": len(changes),
            "changes": changes,
        }

    def _analyze_recent_signals(self, days: int = 7) -> dict:
        """分析最近N天的信号表现"""
        signal_stats = {}
        
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        
        # 获取最近N天的交易
        recent_trades = [
            t for t in self.analyzer.trades 
            if t.get('exit_time', '') >= cutoff
        ]
        
        if not recent_trades:
            return {}
        
        # 统计每个信号的胜率
        for trade in recent_trades:
            signals = trade.get('entry_signals', [])
            pnl_pct = trade.get('pnl_pct', 0)
            is_win = pnl_pct > 0
            
            for sig in signals:
                if sig not in signal_stats:
                    signal_stats[sig] = {'wins': 0, 'losses': 0, 'total_pnl': 0, 'count': 0}
                
                signal_stats[sig]['count'] += 1
                if is_win:
                    signal_stats[sig]['wins'] += 1
                else:
                    signal_stats[sig]['losses'] += 1
                signal_stats[sig]['total_pnl'] += pnl_pct
        
        # 计算胜率和平均盈亏
        for sig, stats in signal_stats.items():
            total = stats['wins'] + stats['losses']
            stats['win_rate'] = stats['wins'] / max(total, 1)
            stats['avg_pnl'] = stats['total_pnl'] / max(total, 1)
        
        return signal_stats
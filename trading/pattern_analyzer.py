"""
交易模式分析器
读取历史交易记录，分析什么条件下赢了、什么条件下输了
每次分析后生成人类可读的洞察报告 + 机器可读的参数建议
"""
import json
from typing import Dict, List, Any, Optional
from datetime import datetime
from collections import defaultdict


class PatternAnalyzer:
    def __init__(self, log_file: str = "trading/live_trades.json"):
        self.log_file = log_file
        self.trades = []
        self._load()

    def _load(self):
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                all_trades = json.load(f)
            self.trades = [t for t in all_trades if t.get("type") == "EXIT"]
        except Exception:
            self.trades = []

    def enough_data(self, min_trades: int = 10) -> bool:
        return len(self.trades) >= min_trades

    def signal_win_rates(self) -> Dict[str, dict]:
        signal_stats = defaultdict(lambda: {"wins": 0, "total": 0})

        for trade in self.trades:
            won = trade.get("win", False)
            triggered = trade.get("signals", [])
            ctx = trade.get("market_context", {})
            if ctx.get("signals_triggered"):
                triggered = ctx["signals_triggered"]

            for sig in triggered:
                if not sig:
                    continue
                signal_stats[sig]["total"] += 1
                if won:
                    signal_stats[sig]["wins"] += 1

        result = {}
        for sig, stats in signal_stats.items():
            total = stats["total"]
            wins = stats["wins"]
            win_rate = wins / total if total > 0 else 0
            result[sig] = {
                "win_rate": win_rate,
                "total_trades": total,
                "wins": wins,
                "losses": total - wins,
                "recommendation": (
                    "increase_weight" if total >= 5 and win_rate >= 0.65 else
                    "decrease_weight" if total >= 5 and win_rate < 0.40 else
                    "neutral"
                )
            }
        return result

    def time_of_day_analysis(self) -> Dict[int, dict]:
        hour_stats = defaultdict(lambda: {"wins": 0, "total": 0, "avg_pnl": []})

        for trade in self.trades:
            hour = None
            ctx = trade.get("market_context", {})
            if ctx.get("hour_of_day") is not None:
                hour = ctx["hour_of_day"]
            else:
                try:
                    ts = trade.get("entry_time") or trade.get("timestamp", "")
                    hour = datetime.fromisoformat(ts.replace("Z", "")).hour
                except Exception:
                    continue

            hour_stats[hour]["total"] += 1
            pnl = trade.get("pnl_pct") or 0
            hour_stats[hour]["avg_pnl"].append(pnl)
            if trade.get("win", False):
                hour_stats[hour]["wins"] += 1

        result = {}
        for hour, stats in hour_stats.items():
            total = stats["total"]
            avg_pnl = sum(stats["avg_pnl"]) / len(stats["avg_pnl"]) if stats["avg_pnl"] else 0
            result[hour] = {
                "win_rate": stats["wins"] / total if total > 0 else 0,
                "total_trades": total,
                "avg_pnl_pct": round(avg_pnl, 2),
            }
        return result

    def funding_rate_analysis(self) -> dict:
        buckets = {
            "negative": {"label": "< 0%", "wins": 0, "total": 0},
            "safe":     {"label": "0%-0.05%", "wins": 0, "total": 0},
            "caution":  {"label": "0.05%-0.15%", "wins": 0, "total": 0},
            "warning":  {"label": "> 0.15%", "wins": 0, "total": 0},
        }

        for trade in self.trades:
            ctx = trade.get("market_context", {})
            fr = (ctx.get("funding_rate") or 0) * 100
            won = trade.get("win", False)

            if fr < 0:
                bucket = "negative"
            elif fr < 0.05:
                bucket = "safe"
            elif fr < 0.15:
                bucket = "caution"
            else:
                bucket = "warning"

            buckets[bucket]["total"] += 1
            if won:
                buckets[bucket]["wins"] += 1

        for k, v in buckets.items():
            v["win_rate"] = v["wins"] / v["total"] if v["total"] > 0 else None
        return buckets

    def hold_time_analysis(self) -> dict:
        wins_hold = [t.get("hold_minutes", 0) or 0 for t in self.trades if t.get("win")]
        losses_hold = [t.get("hold_minutes", 0) or 0 for t in self.trades if not t.get("win")]

        avg_win = int(sum(wins_hold) / len(wins_hold)) if wins_hold else 0
        avg_loss = int(sum(losses_hold) / len(losses_hold)) if losses_hold else 0

        rec = max(60, min(1440, int(avg_win * 1.5))) if avg_win > 0 else 240

        return {
            "avg_win_hold_minutes": avg_win,
            "avg_loss_hold_minutes": avg_loss,
            "recommendation_max_hold_minutes": rec,
        }

    def phase_accuracy(self) -> dict:
        phase_stats = defaultdict(lambda: {"wins": 0, "total": 0})
        for trade in self.trades:
            ctx = trade.get("market_context", {})
            phase = ctx.get("phase_detected", "unknown")
            phase_stats[phase]["total"] += 1
            if trade.get("win", False):
                phase_stats[phase]["wins"] += 1

        return {
            phase: {
                "win_rate": s["wins"] / s["total"] if s["total"] > 0 else 0,
                "total": s["total"]
            }
            for phase, s in phase_stats.items()
        }

    def overall_stats(self) -> dict:
        if not self.trades:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "avg_win_pct": 0,
                "avg_loss_pct": 0,
                "expected_value_per_trade": 0,
            }

        wins = [t for t in self.trades if t.get("win")]
        losses = [t for t in self.trades if not t.get("win")]
        win_pnls = [t.get("pnl_pct", 0) or 0 for t in wins]
        loss_pnls = [t.get("pnl_pct", 0) or 0 for t in losses]

        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0
        wr = len(wins) / len(self.trades)
        ev = wr * avg_win + (1 - wr) * avg_loss

        return {
            "total_trades": len(self.trades),
            "win_rate": wr,
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "expected_value_per_trade": round(ev, 2),
        }

    def generate_report(self) -> str:
        stats = self.overall_stats()
        signal_wr = self.signal_win_rates()
        hold_analysis = self.hold_time_analysis()
        fr_analysis = self.funding_rate_analysis()

        lines = [
            "",
            "=" * 55,
            f"📊 MMTracker 学习报告 — 基于 {stats['total_trades']} 笔交易",
            "=" * 55,
            "",
            "【整体表现】",
            f"  胜率: {stats['win_rate']*100:.1f}%  |  均赢: {stats['avg_win_pct']:.1f}%  |  均亏: {stats['avg_loss_pct']:.1f}%",
            f"  每笔期望收益: {stats['expected_value_per_trade']:.2f}%",
            "",
            "【哪些信号有效】",
        ]

        for sig, data in sorted(signal_wr.items(), key=lambda x: x[1]['win_rate'], reverse=True):
            if data['total_trades'] >= 3:
                rec_icon = "⬆️" if data['recommendation'] == "increase_weight" else (
                    "⬇️" if data['recommendation'] == "decrease_weight" else "➡️")
                lines.append(
                    f"  {rec_icon} {sig}: 胜率{data['win_rate']*100:.0f}%"
                    f" ({data['wins']}赢/{data['losses']}亏)"
                )

        lines += [
            "",
            "【最佳持仓时长】",
            f"  获胜交易平均持仓: {hold_analysis['avg_win_hold_minutes']}分钟",
            f"  亏损交易平均持仓: {hold_analysis['avg_loss_hold_minutes']}分钟",
            f"  建议最大持仓: {hold_analysis['recommendation_max_hold_minutes']}分钟",
            "",
            "【资金费率与胜率关系】",
        ]

        for bucket, data in fr_analysis.items():
            if data['total'] > 0 and data['win_rate'] is not None:
                lines.append(
                    f"  费率{data['label']}: 胜率{data['win_rate']*100:.0f}% ({data['total']}笔)"
                )

        lines += ["", "=" * 55, ""]
        return "\n".join(lines)
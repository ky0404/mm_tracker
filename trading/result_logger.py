import json
from datetime import datetime
from typing import List, Dict, Any, Optional


class ResultLogger:
    def __init__(self, log_file: str = "trading/live_trades.json"):
        self.log_file = log_file
        self.trades = []
        self._load()

    def _load(self):
        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                self.trades = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.trades = []

    def log_entry(
        self,
        token: str,
        signals: List[Dict[str, Any]],
        score: float,
        entry_price: float,
        entry_signals_count: int,
        position_size: float = 10.0,
        market_context: Dict[str, Any] = None,
        trade_db_id: int = None,
    ) -> int:
        now = datetime.now()
        trade = {
            "index": len(self.trades),
            "type": "ENTRY",
            "token": token,
            "timestamp": now.isoformat(),
            "entry_time": now.isoformat(),
            "signals": [s["name"] if isinstance(s, dict) else s for s in signals] if signals else [],
            "signal_count": entry_signals_count,
            "score": score,
            "entry_price": entry_price,
            "position_size": position_size,
            "market_context": market_context or {},
            "trade_db_id": trade_db_id,  # 添加 trade_db_id 字段
            "exit_price": None,
            "pnl": None,
            "pnl_pct": None,
            "hold_minutes": None,
            "win": None,
            "exit_reason": None,
            "exit_timestamp": None,
        }
        self.trades.append(trade)
        self.save()
        return len(self.trades) - 1

    def log_exit(
        self,
        trade_index: int,
        exit_price: float,
        pnl: float,
        exit_reason: str,
    ):
        if trade_index >= len(self.trades):
            return

        trade = self.trades[trade_index]
        entry_price = trade.get("entry_price", 0)
        
        # 计算持仓时长（分钟）
        hold_minutes = 0
        entry_time_str = trade.get("entry_time") or trade.get("timestamp", "")
        if entry_time_str:
            try:
                entry_dt = datetime.fromisoformat(entry_time_str.replace("Z", ""))
                hold_minutes = int((datetime.now() - entry_dt).total_seconds() / 60)
            except Exception:
                hold_minutes = 0

        # 计算百分比盈亏
        pnl_pct = 0.0
        if entry_price and entry_price > 0 and exit_price:
            pnl_pct = (exit_price - entry_price) / entry_price * 100

        trade["exit_price"] = exit_price
        trade["pnl"] = pnl
        trade["pnl_pct"] = round(pnl_pct, 4)
        trade["hold_minutes"] = hold_minutes
        trade["win"] = pnl > 0
        trade["type"] = "EXIT"
        trade["status"] = "closed"  # 标记为已关闭
        trade["exit_timestamp"] = datetime.now().isoformat()
        trade["exit_reason"] = exit_reason
        self.save()

    def log_partial_close(self, trade_index: int, close_size: float, remaining_size: float, exit_price: float, pnl: float, exit_reason: str):
        """部分平仓记录"""
        if trade_index >= len(self.trades):
            return
        trade = self.trades[trade_index]
        partial = trade.get("partial_closes", [])
        entry_price = trade.get("entry_price", exit_price)
        close_pct = close_size / (close_size + remaining_size) if (close_size + remaining_size) > 0 else 0
        pnl_pct = (pnl / (close_size * entry_price) * 100) if entry_price > 0 and close_size > 0 else 0
        partial.append({
            "timestamp": datetime.now().isoformat(),
            "close_pct": round(close_pct * 100, 2),
            "close_size": close_size,
            "remaining_size": remaining_size,
            "exit_price": exit_price,
            "pnl": pnl,
            "pnl_pct": round(pnl_pct, 4),
            "reason": exit_reason,
        })
        trade["partial_closes"] = partial
        self.save()

    def get_unfinished_trades(self) -> List[Dict[str, Any]]:
        return [t for t in self.trades if t.get("type") == "ENTRY"]

    def get_finished_trades(self) -> List[Dict[str, Any]]:
        return [t for t in self.trades if t.get("type") == "EXIT"]

    def get_trade(self, index: int) -> Optional[Dict[str, Any]]:
        if 0 <= index < len(self.trades):
            return self.trades[index]
        return None

    def force_close_all_entries(self):
        """启动时清除所有卡死的ENTRY记录"""
        count = 0
        for trade in self.trades:
            if trade.get("type") == "ENTRY":
                trade["type"] = "ABANDONED"
                trade["exit_reason"] = "auto_reset_on_startup"
                trade["exit_timestamp"] = datetime.now().isoformat()
                count += 1
        if count > 0:
            self.save()
            print(f"[ResultLogger] 已重置 {count} 个卡死仓位")

    def save(self):
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(self.trades, f, ensure_ascii=False, indent=2)

    def clear(self):
        self.trades = []
        self.save()

    def get_stats(self) -> Dict[str, Any]:
        """获取交易统计（包含所有交易）"""
        finished = self.get_finished_trades()
        if not finished:
            return {
                "total_trades": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "total_pnl": 0.0,
                "open_count": 0,
                "open_positions": [],
                "system_trades_only": False,
            }

        wins = [t for t in finished if t.get("win", False)]
        losses = [t for t in finished if not t.get("win", True)]
        pnls = [t.get("pnl", 0) for t in finished if t.get("pnl") is not None]
        
        # 计算未平仓数量
        unfinished = self.get_unfinished_trades()
        open_count = len(unfinished)
        open_positions = [t.get("token") for t in unfinished]

        return {
            "total_trades": len(finished),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": len(wins) / len(finished) if finished else 0.0,
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "total_pnl": sum(pnls),
            "open_count": open_count,
            "open_positions": open_positions,
            "system_trades_only": False,
        }
    
    def get_system_stats(self) -> Dict[str, Any]:
        """获取系统自动交易的统计（过滤手动操作）"""
        excluded_reasons = {"SELL_ALL", "SPOT_CLOSED", "manual_reset", "stuck_position_cleanup"}
        
        finished = self.get_finished_trades()
        system_trades = [
            t for t in finished
            if t.get("exit_reason") not in excluded_reasons
            and t.get("signals", []) not in [["manual_sell_all"], ["spot_position"], ["manual_sell_all", "spot_position"], ["spot_position", "manual_sell_all"]]
        ]
        
        if not system_trades:
            return {
                "system_total_trades": 0,
                "system_win_count": 0,
                "system_loss_count": 0,
                "system_win_rate": 0.0,
                "system_total_pnl": 0.0,
                "system_avg_pnl": 0.0,
                "note": "系统自动交易数量不足"
            }
        
        wins = [t for t in system_trades if t.get("win", False)]
        losses = [t for t in system_trades if not t.get("win", True)]
        pnls = [t.get("pnl", 0) for t in system_trades if t.get("pnl") is not None]
        
        return {
            "system_total_trades": len(system_trades),
            "system_win_count": len(wins),
            "system_loss_count": len(losses),
            "system_win_rate": len(wins) / len(system_trades) if system_trades else 0.0,
            "system_total_pnl": sum(pnls) if pnls else 0.0,
            "system_avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "note": "仅统计系统自动触发的交易（已过滤手动操作）"
        }
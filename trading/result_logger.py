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
    ) -> int:
        trade = {
            "index": len(self.trades),
            "type": "ENTRY",
            "token": token,
            "timestamp": datetime.now().isoformat(),
            "signals": [s["name"] for s in signals] if signals else [],
            "signal_count": entry_signals_count,
            "score": score,
            "entry_price": entry_price,
            "position_size": position_size,
            "exit_price": None,
            "pnl": None,
            "win": None,
            "exit_reason": None,
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
        trade["exit_price"] = exit_price
        trade["pnl"] = pnl
        trade["win"] = pnl > 0
        trade["type"] = "EXIT"
        trade["exit_timestamp"] = datetime.now().isoformat()
        trade["exit_reason"] = exit_reason
        self.save()

    def get_unfinished_trades(self) -> List[Dict[str, Any]]:
        return [t for t in self.trades if t.get("type") == "ENTRY"]

    def get_finished_trades(self) -> List[Dict[str, Any]]:
        return [t for t in self.trades if t.get("type") == "EXIT"]

    def get_trade(self, index: int) -> Optional[Dict[str, Any]]:
        if 0 <= index < len(self.trades):
            return self.trades[index]
        return None

    def save(self):
        with open(self.log_file, "w", encoding="utf-8") as f:
            json.dump(self.trades, f, ensure_ascii=False, indent=2)

    def clear(self):
        self.trades = []
        self.save()
    
    def get_stats(self) -> Dict[str, Any]:
        finished = self.get_finished_trades()
        if not finished:
            return {
                "total_trades": 0,
                "win_count": 0,
                "loss_count": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
            }
        
        wins = [t for t in finished if t.get("win", False)]
        losses = [t for t in finished if not t.get("win", True)]
        pnls = [t.get("pnl", 0) for t in finished if t.get("pnl") is not None]
        
        return {
            "total_trades": len(finished),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": len(wins) / len(finished) if finished else 0.0,
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "total_pnl": sum(pnls),
        }
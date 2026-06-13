"""
持仓监控器 - Position Monitor
功能：监控开仓仓位，自动执行止损/止盈
"""
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class PositionMonitor:
    """持仓监控器 - 监控仓位并自动处理 SL/TP"""

    def __init__(self, trader, result_logger, params: Dict[str, Any] = None):
        self.trader = trader
        self.result_logger = result_logger
        self.params = params or {}
        
        self.sl_pct = self.params.get("stop_loss_pct", 5.0)
        self.tp_pct = self.params.get("take_profit_pct", 10.0)
        self.max_positions = self.params.get("max_open_positions", 5)
        
        self.last_check = {}

    def check_positions(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        检查所有持仓，处理 SL/TP
        :param prices: 当前市场价格 {"LAB": 10.0, "WIF": 0.15, ...}
        :return: 触发平仓的交易列表
        """
        closed_trades = []
        unfinished = self.result_logger.get_unfinished_trades()
        
        for trade in unfinished:
            token = trade["token"]
            entry_price = trade.get("entry_price", 0)
            position_size = trade.get("position_size", 0)
            trade_index = trade["index"]
            
            if entry_price <= 0 or position_size <= 0:
                continue
            
            current_price = prices.get(token, entry_price)
            price_change_pct = (current_price - entry_price) / entry_price * 100
            
            exit_reason = None
            exit_price = current_price
            
            # 检查止盈
            if price_change_pct >= self.tp_pct:
                exit_reason = "TAKE_PROFIT"
                logger.info(f"[TP] {token} 达到止盈 {self.tp_pct}%, 当前涨幅 {price_change_pct:.2f}%")
            
            # 检查止损
            elif price_change_pct <= -self.sl_pct:
                exit_reason = "STOP_LOSS"
                logger.info(f"[SL] {token} 触发止损 {self.sl_pct}%, 当前跌幅 {price_change_pct:.2f}%")
            
            if exit_reason:
                # 使用现货格式
                symbol = f"{token}-USDT"
                result = self.trader.close_position(symbol)
                
                if result.get("code") == "0":
                    pnl = (exit_price - entry_price) * position_size
                    
                    self.result_logger.log_exit(
                        trade_index=trade_index,
                        exit_price=exit_price,
                        pnl=pnl,
                        exit_reason=exit_reason,
                    )
                    
                    closed_trades.append({
                        "token": token,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl": pnl,
                        "pnl_pct": price_change_pct,
                        "exit_reason": exit_reason,
                    })
                    
                    logger.info(f"[平仓] {token} @ {exit_price}, PnL: {pnl:.2f} ({price_change_pct:+.2f}%)")
                else:
                    logger.error(f"[平仓失败] {token}: {result.get('msg')}")
        
        return closed_trades

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """获取当前活跃仓位"""
        unfinished = self.result_logger.get_unfinished_trades()
        active = []
        
        for trade in unfinished:
            active.append({
                "token": trade["token"],
                "entry_price": trade.get("entry_price", 0),
                "position_size": trade.get("position_size", 0),
                "signals": trade.get("signals", []),
                "timestamp": trade.get("timestamp", ""),
            })
        
        return active

    def can_open_new_position(self) -> bool:
        """检查是否可以开新仓位"""
        unfinished = self.result_logger.get_unfinished_trades()
        return len(unfinished) < self.max_positions

    def update_params(self, params: Dict[str, Any]):
        """动态更新参数"""
        self.sl_pct = params.get("stop_loss_pct", self.sl_pct)
        self.tp_pct = params.get("take_profit_pct", self.tp_pct)
        self.max_positions = params.get("max_open_positions", self.max_positions)
        logger.info(f"[参数更新] SL: {self.sl_pct}%, TP: {self.tp_pct}%, MaxPos: {self.max_positions}")


if __name__ == "__main__":
    from trading.mock_trader import create_trader
    from trading.result_logger import ResultLogger
    
    trader = create_trader(sim_mode=True)
    logger = ResultLogger()
    
    monitor = PositionMonitor(trader, logger, {"stop_loss_pct": 5.0, "take_profit_pct": 10.0})
    
    # 测试开仓
    logger.log_entry(
        token="BTC",
        signals=[{"name": "signal_4_volume_spike"}],
        score=5.0,
        entry_price=63000.0,
        entry_signals_count=4,
        position_size=0.1,
    )
    
    # 测试检查（假设价格下跌 6%）
    prices = {"BTC": 59220.0}  # -6%
    closed = monitor.check_positions(prices)
    print(f"触发平仓: {closed}")
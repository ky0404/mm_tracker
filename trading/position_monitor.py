"""
持仓监控器 - Position Monitor (Freqtrade风格增强版)
功能：监控开仓仓位，自动执行止损/止盈/追踪止损/ROI/保护机制

整合自Freqtrade核心逻辑:
- freqtradebot.py: should_exit, _check_and_execute_exit
- persistence/trade_model.py: 持仓状态管理
- plugins/protections/stoploss_guard.py: 风控保护
"""
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

UTC = timezone.utc


class ExitType(Enum):
    """出场类型 (Freqtrade ExitType)"""
    EXIT_SIGNAL = "exit_signal"
    ROI = "roi"
    STOP_LOSS = "stoploss"
    TRAILING_STOP = "trailing_stop"
    PARTIAL_EXIT = "partial_exit"
    EMERGENCY_EXIT = "emergency_exit"
    MANUAL_EXIT = "manual_exit"
    MAX_HOLD_TIME = "max_hold_time"
    FUNDING_EXIT = "funding_exit"


@dataclass
class ExitConfig:
    """出场配置 (Freqtrade风格)"""
    # 止损
    stop_loss_pct: float = -0.10  # -10% (负值表示亏损)
    
    # 追踪止损
    trailing_stop: bool = True
    trailing_stop_positive: float = 0.02  # 2%启动追踪
    trailing_stop_offset: float = 0.04    # 4%锁定利润
    
    # ROI (分阶段止盈)
    minimal_roi: dict = field(default_factory=lambda: {
        "0": 0.20,      # 0分钟以上: 20%
        "30": 0.10,    # 30分钟以上: 10%
        "60": 0.05,    # 60分钟以上: 5%
    })
    
    # 持仓时间
    max_hold_minutes: int = 438  # 从meta_optimizer优化而来
    min_hold_minutes: int = 60   # 最小持仓时间
    
    # 仓位限制
    max_open_positions: int = 5
    
    # 资金费率阈值
    funding_warning_pct: float = 0.15
    funding_reduce_pct: float = 0.50
    funding_exit_pct: float = 1.0
    
    # 保护机制
    max_drawdown: float = 0.25   # 最大回撤25%
    max_cooldown_seconds: int = 300  # 冷却时间
    max_consecutive_losses: int = 5  # 连续亏损次数
    
    # 落袋为安
    partial_take_profit: bool = True
    partial_tp_pct: float = 0.30  # 30%止盈时平仓50%
    partial_trailing_pct: float = 0.05  # 回撤5%触发


class PositionMonitor:
    """
    持仓监控器 (Freqtrade风格增强版)
    核心逻辑来自Freqtrade freqtradebot.py 的 should_exit 方法
    """

    def __init__(self, trader, result_logger, params: Dict[str, Any] = None):
        self.trader = trader
        self.result_logger = result_logger
        self.params = params or {}
        
        # 使用ExitConfig
        self.config = ExitConfig(
            stop_loss_pct=self.params.get("stop_loss_pct", -0.10),
            trailing_stop=self.params.get("trailing_stop", True),
            trailing_stop_positive=self.params.get("trailing_stop_positive", 0.02),
            trailing_stop_offset=self.params.get("trailing_stop_offset", 0.04),
            minimal_roi=self.params.get("minimal_roi", {"0": 0.20, "30": 0.10, "60": 0.05}),
            max_hold_minutes=self.params.get("max_hold_minutes", 438),
            min_hold_minutes=self.params.get("min_hold_minutes", 60),
            max_open_positions=self.params.get("max_open_positions", 5),
            funding_warning_pct=self.params.get("funding_warning_pct", 0.15),
            funding_reduce_pct=self.params.get("funding_reduce_pct", 0.50),
            funding_exit_pct=self.params.get("funding_exit_pct", 1.0),
            max_drawdown=self.params.get("max_drawdown", 0.25),
            max_cooldown_seconds=self.params.get("max_cooldown_seconds", 300),
            max_consecutive_losses=self.params.get("max_consecutive_losses", 5),
            partial_take_profit=self.params.get("partial_take_profit", True),
            partial_tp_pct=self.params.get("partial_tp_pct", 0.30),
            partial_trailing_pct=self.params.get("partial_trailing_pct", 0.05),
        )
        
        # 追踪止损状态
        self.highest_price: Dict[str, float] = {}  # 最高价
        self._highest_profit: Dict[str, float] = {}  # 最高利润率
        
        # 保护机制状态
        self._cooldown_end_time: Optional[datetime] = None
        self._consecutive_losses: int = 0
        self._peak_balance: float = 0
        self._current_balance: float = 0
        
        # 落袋为安标志
        self.half_position_taken: Dict[str, bool] = {}
        
        # DCA 配置
        self.dca_enabled = self.params.get("dca_enabled", False)
        self.dca_mode = self.params.get("dca_mode", "mode_0")
        self.dca_record: Dict[str, dict] = {}

    def update_balance(self, balance: float) -> None:
        """更新余额用于回撤计算"""
        self._current_balance = balance
        if balance > self._peak_balance:
            self._peak_balance = balance
    
    @property
    def current_drawdown(self) -> float:
        """当前回撤"""
        if self._peak_balance == 0:
            return 0
        return (self._peak_balance - self._current_balance) / self._peak_balance

    def can_trade(self, current_time: datetime = None) -> tuple[bool, str]:
        """检查是否可以交易 (Freqtrade风格保护检查)"""
        current_time = current_time or datetime.now(UTC)
        
        # 1. 检查冷却期
        if self._cooldown_end_time and current_time < self._cooldown_end_time:
            remaining = (self._cooldown_end_time - current_time).total_seconds()
            return False, f"cooldown: {remaining:.0f}s"
        
        # 2. 检查回撤保护
        if self.current_drawdown > self.config.max_drawdown:
            return False, f"max_drawdown: {self.current_drawdown:.1%}"
        
        return True, ""

    def record_trade_result(self, profit: float, current_time: datetime = None) -> None:
        """记录交易结果用于保护机制"""
        current_time = current_time or datetime.now(UTC)
        
        if profit < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.config.max_consecutive_losses:
                self._cooldown_end_time = current_time + timedelta(
                    seconds=self.config.max_cooldown_seconds
                )
                logger.warning(f"[保护机制] 连续{self._consecutive_losses}笔亏损，冷却{self.config.max_cooldown_seconds}秒")
        else:
            self._consecutive_losses = 0

    def check_positions(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        检查所有持仓，处理 SL/TP/追踪止损/ROI/超时/资金费率
        核心逻辑来自Freqtrade freqtradebot.py 的 should_exit 方法
        """
        from fetchers.price_api import fetch_funding_rate_history
        
        closed_trades = []
        partial_closed = []
        unfinished = self.result_logger.get_unfinished_trades()
        
        for trade in unfinished:
            token = trade["token"]
            entry_price = trade.get("entry_price", 0)
            position_size = trade.get("position_size", 0)
            trade_index = trade["index"]
            entry_time_str = trade.get("timestamp", "")
            
            current_price = prices.get(token, entry_price)
            if current_price <= 0:
                current_price = entry_price
            
            # 计算收益率
            profit_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            
            # 更新最高价和最高利润率 (用于追踪止损)
            if token not in self.highest_price or current_price > self.highest_price[token]:
                self.highest_price[token] = current_price
            
            if token not in self._highest_profit or profit_pct > self._highest_profit[token]:
                self._highest_profit[token] = profit_pct
            
            exit_reason = None
            exit_type = None
            exit_price = current_price
            reduce_ratio = 0
            
            # 计算持仓时间
            hold_minutes = 0
            if entry_time_str:
                try:
                    entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00"))
                    hold_minutes = (datetime.now(entry_time.tzinfo) - entry_time).total_seconds() / 60
                except:
                    pass
            
            # ========== 1. 检查ROI (分阶段止盈) ==========
            roi_exit = self._check_roi(profit_pct, hold_minutes)
            if roi_exit:
                exit_reason = roi_exit["reason"]
                exit_type = ExitType.ROI
                logger.info(f"[ROI] {token} 持仓{hold_minutes:.0f}分钟, 收益率{profit_pct:.1%} >= {roi_exit['threshold']:.1%}")
            
            # ========== 2. 检查追踪止损 ==========
            elif self.config.trailing_stop:
                trailing_exit = self._check_trailing_stop(token, current_price, profit_pct)
                if trailing_exit:
                    exit_reason = trailing_exit
                    exit_type = ExitType.TRAILING_STOP
                    logger.info(f"[追踪止损] {token} 触发追踪止损")
            
            # ========== 3. 落袋为安 (部分止盈) ==========
            elif self.config.partial_take_profit and not exit_reason:
                partial_result = self._check_partial_take_profit(
                    token, entry_price, current_price, profit_pct
                )
                if partial_result:
                    exit_reason = partial_result["reason"]
                    exit_type = ExitType.PARTIAL_EXIT
                    reduce_ratio = partial_result["reduce_ratio"]
            
            # ========== 4. 止损检查 ==========
            elif profit_pct <= self.config.stop_loss_pct:
                exit_reason = "STOP_LOSS"
                exit_type = ExitType.STOP_LOSS
                logger.info(f"[SL] {token} 触发止损 {self.config.stop_loss_pct:.1%}, 当前{profit_pct:.1%}")
            
            # ========== 5. 资金费率检查 ==========
            if not exit_reason:
                funding_exit = self._check_funding_exit(token, profit_pct)
                if funding_exit:
                    exit_reason = funding_exit["reason"]
                    exit_type = ExitType.FUNDING_EXIT
                    if funding_exit.get("reduce_ratio"):
                        reduce_ratio = funding_exit["reduce_ratio"]
            
            # ========== 6. 超时检查 ==========
            if not exit_reason and hold_minutes >= self.config.max_hold_minutes:
                exit_reason = "MAX_HOLD_TIME"
                exit_type = ExitType.MAX_HOLD_TIME
                logger.info(f"[超时] {token} 持仓{hold_minutes:.0f}分钟 >= {self.config.max_hold_minutes}分钟")
            
            # ========== 7. DCA 加仓检查 ==========
            dca_action = None
            if self.dca_enabled and not exit_reason and profit_pct < 0:
                dca_action = self._check_dca(
                    token=token,
                    current_profit_pct=profit_pct,
                    current_price=current_price,
                    entry_price=entry_price,
                )
                if dca_action:
                    logger.info(f"[DCA] {token} {dca_action}")
            
            # 执行平仓
            if exit_reason or reduce_ratio > 0:
                symbol = f"{token}-USDT"
                close_size = position_size * reduce_ratio if 0 < reduce_ratio < 1 else position_size
                
                result = self.trader.close_position(symbol)
                
                if result.get("code") == "0":
                    pnl = (exit_price - entry_price) * close_size
                    
                    # 记录交易结果用于保护机制
                    self.record_trade_result(pnl)
                    
                    if 0 < reduce_ratio < 1:
                        self.result_logger.log_partial_close(
                            trade_index=trade_index,
                            close_size=close_size,
                            remaining_size=position_size - close_size,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason=exit_reason or "PARTIAL_CLOSE",
                        )
                        partial_closed.append({
                            "token": token,
                            "exit_type": exit_type.value if exit_type else "unknown",
                            "closed_size": close_size,
                            "remaining_size": position_size - close_size,
                            "exit_price": exit_price,
                            "pnl": pnl,
                            "exit_reason": exit_reason,
                        })
                    else:
                        self.result_logger.log_exit(
                            trade_index=trade_index,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason=exit_reason or "CLOSE",
                        )
                        closed_trades.append({
                            "token": token,
                            "exit_type": exit_type.value if exit_type else "unknown",
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "pnl": pnl,
                            "pnl_pct": profit_pct * 100,
                            "exit_reason": exit_reason,
                            "hold_minutes": hold_minutes,
                        })
                    
                    # 清理追踪状态
                    self.highest_price.pop(token, None)
                    self._highest_profit.pop(token, None)
                    self.half_position_taken.pop(token, None)
                    
                    logger.info(f"[平仓] {token} @ {exit_price}, PnL: {pnl:.2f} ({profit_pct:+.1%}), 类型: {exit_type.value if exit_type else 'N/A'}")
                else:
                    logger.error(f"[平仓失败] {token}: {result.get('msg')}")
        
        return closed_trades + partial_closed

    def _check_roi(self, profit_pct: float, hold_minutes: float) -> Optional[dict]:
        """
        检查ROI止盈 (Freqtrade风格)
        minimal_roi: {"0": 0.20, "30": 0.10, "60": 0.05}
        """
        for minutes_str, threshold in sorted(self.config.minimal_roi.items(), reverse=True):
            threshold_minutes = int(minutes_str)
            if hold_minutes >= threshold_minutes:
                if profit_pct >= threshold:
                    return {
                        "reason": f"ROI_{threshold:.0%}",
                        "threshold": threshold,
                        "hold_minutes": hold_minutes
                    }
        return None

    def _check_trailing_stop(self, token: str, current_price: float, current_profit: float) -> Optional[str]:
        """
        检查追踪止损 (Freqtrade风格)
        启动条件: profit >= trailing_stop_positive (2%)
        触发条件: highest_profit - current_profit >= trailing_stop_offset (4%)
        """
        if current_profit < self.config.trailing_stop_positive:
            return None
        
        highest_profit = self._highest_profit.get(token, 0)
        
        if highest_profit - current_profit >= self.config.trailing_stop_offset:
            return f"TRAILING_STOP_{self.config.trailing_stop_offset:.0%}"
        
        return None

    def _check_partial_take_profit(
        self,
        token: str,
        entry_price: float,
        current_price: float,
        profit_pct: float
    ) -> Optional[dict]:
        """落袋为安: 30%止盈时平仓50%，剩余50%跟踪止损"""
        half_taken = self.half_position_taken.get(token, False)
        
        if profit_pct >= self.config.partial_tp_pct and not half_taken:
            self.half_position_taken[token] = True
            return {
                "reason": "TAKE_PROFIT_HALF",
                "reduce_ratio": 0.5
            }
        
        if half_taken and profit_pct >= self.config.partial_tp_pct:
            highest = self.highest_price.get(token, entry_price)
            drawdown = (highest - current_price) / highest if highest > 0 else 0
            
            if drawdown >= self.config.partial_trailing_pct:
                return {
                    "reason": "TRAILING_STOP_HALF",
                    "reduce_ratio": 1.0
                }
        
        return None

    def _check_funding_exit(self, token: str, profit_pct: float) -> Optional[dict]:
        """资金费率检查"""
        try:
            funding_data = fetch_funding_rate_history(token)
            funding_rate = funding_data.get("latest_rate", 0) if funding_data else 0
            
            if funding_rate >= self.config.funding_exit_pct:
                return {
                    "reason": f"FUNDING_EXIT_{funding_rate:.3%}",
                    "reduce_ratio": 1.0
                }
            elif funding_rate >= self.config.funding_reduce_pct:
                return {
                    "reason": f"FUNDING_REDUCE_{funding_rate:.3%}",
                    "reduce_ratio": 0.3
                }
        except:
            pass
        
        return None

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
                "profit_pct": self._calculate_profit(
                    trade.get("entry_price", 0),
                    trade.get("current_price", trade.get("entry_price", 0))
                ),
                "hold_minutes": self._calculate_hold_minutes(trade.get("timestamp", "")),
            })
        
        return active

    def _calculate_profit(self, entry_price: float, current_price: float) -> float:
        """计算收益率"""
        if entry_price <= 0:
            return 0
        return (current_price - entry_price) / entry_price

    def _calculate_hold_minutes(self, timestamp: str) -> float:
        """计算持仓分钟数"""
        if not timestamp:
            return 0
        try:
            entry_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            return (datetime.now(entry_time.tzinfo) - entry_time).total_seconds() / 60
        except:
            return 0

    def can_open_new_position(self) -> bool:
        """检查是否可以开新仓位"""
        unfinished = self.result_logger.get_unfinished_trades()
        return len(unfinished) < self.config.max_open_positions

    def update_params(self, params: Dict[str, Any]):
        """动态更新参数"""
        # 更新config
        if "stop_loss_pct" in params:
            self.config.stop_loss_pct = params["stop_loss_pct"]
        if "trailing_stop" in params:
            self.config.trailing_stop = params["trailing_stop"]
        if "trailing_stop_positive" in params:
            self.config.trailing_stop_positive = params["trailing_stop_positive"]
        if "trailing_stop_offset" in params:
            self.config.trailing_stop_offset = params["trailing_stop_offset"]
        if "minimal_roi" in params:
            self.config.minimal_roi = params["minimal_roi"]
        if "max_hold_minutes" in params:
            self.config.max_hold_minutes = params["max_hold_minutes"]
        if "min_hold_minutes" in params:
            self.config.min_hold_minutes = params["min_hold_minutes"]
        if "max_open_positions" in params:
            self.config.max_open_positions = params["max_open_positions"]
        
        # 兼容旧参数名
        self.sl_pct = self.config.stop_loss_pct * 100  # 转成正数百分比
        self.tp_pct = self.config.minimal_roi.get("0", 0.20) * 100
        self.max_positions = self.config.max_open_positions
        self.max_hold_minutes = self.config.max_hold_minutes
        
        logger.info(f"[参数更新] SL: {self.config.stop_loss_pct:.1%}, TP: {self.tp_pct:.0f}%, "
                   f"MaxPos: {self.max_positions}, MaxHold: {self.max_hold_minutes}min, "
                   f"Trailing: {self.config.trailing_stop}")

    def _check_dca(self, token: str, current_profit_pct: float, current_price: float, entry_price: float) -> Optional[str]:
        """
        检查是否触发 DCA 加仓
        
        Returns:
            None 不加仓
            str 加仓描述信息
        """
        from trading.nfi_dca import NFIDCAManager
        
        if token not in self.dca_record:
            self.dca_record[token] = {"count": 0, "total_amount": 0}
        
        dca_info = self.dca_record[token]
        entry_count = dca_info["count"] + 1  # 第几次入场(首次=1, 加仓1次=2, ...)
        
        dca_manager = NFIDCAManager()
        result = dca_manager.calculate_dca(
            current_profit=current_profit_pct,
            entry_count=entry_count,
            mode=self.dca_mode,
        )
        
        if result.should_dca:
            dca_info["count"] += 1
            dca_info["total_amount"] += result.dca_amount
            
            return (f"触发DCA加仓 #{dca_info['count']}, "
                    f"亏损{current_profit_pct*100:.1f}%, "
                    f"加仓金额${result.dca_amount:.2f}, "
                    f"原因: {result.reason}")
        
        return None


if __name__ == "__main__":
    # 直接使用真实OKX API测试
    import json
    from trading.okx_testnet import OKXTestnetTrader
    
    with open("config/testnet_config.json", "r") as f:
        config = json.load(f)
    okx_cfg = config.get("okx", {})
    
    trader = OKXTestnetTrader(
        okx_cfg["api_key"], 
        okx_cfg["api_secret"], 
        okx_cfg["passphrase"], 
        testnet=True
    )
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
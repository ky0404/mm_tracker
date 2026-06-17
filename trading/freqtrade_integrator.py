"""
Freqtrade Integrator - 将Freqtrade策略框架整合到MMTracker

功能:
- 策略接口 (IStrategy) 适配
- 入场/出场信号标准化
- 多时间框架确认
- 确认钩子 (confirm_entry/exit)
- 自定义价格 (custom_entry/exit_price)
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from dataclasses import dataclass

from freqtrade_strategy import (
    IStrategy,
    MMTrackerStrategy,
    TradeInfo,
    ExitCheckTuple,
    ExitType,
    StrategyConfig,
)

logger = logging.getLogger(__name__)

UTC = timezone.utc


@dataclass
class FreqtradeIntegration:
    """Freqtrade集成器"""
    strategy: IStrategy
    protection_manager: Any = None  # ProtectionManager
    
    @classmethod
    def create(cls, params: Dict[str, Any] = None) -> "FreqtradeIntegration":
        """创建集成器"""
        params = params or {}
        
        strategy_config = StrategyConfig(
            stoploss=params.get("stoploss", -0.10),
            trailing_stop=params.get("trailing_stop", True),
            trailing_stop_positive=params.get("trailing_stop_positive", 0.02),
            trailing_stop_offset=params.get("trailing_stop_offset", 0.04),
            minimal_roi=params.get("minimal_roi", {"0": 0.20, "30": 0.10, "60": 0.05}),
            max_hold_time=params.get("max_hold_minutes", 438),
            min_hold_time=params.get("min_hold_minutes", 60),
            max_open_trades=params.get("max_open_positions", 5),
        )
        
        strategy = MMTrackerStrategy(strategy_config)
        
        return cls(strategy=strategy)
    
    def confirm_entry(
        self,
        pair: str,
        current_price: float,
        proposed_rate: float,
        amount: float,
        side: str = "long"
    ) -> tuple[bool, str]:
        """
        入场确认钩子 (Freqtrade confirm_trade_entry)
        
        检查:
        1. 策略确认
        2. 保护机制 (冷却/回撤)
        3. 资金费率
        """
        current_time = datetime.now(UTC)
        
        # 1. 策略确认
        if not self.strategy.confirm_entry(
            pair=pair,
            order_type="limit",
            amount=amount,
            rate=proposed_rate,
            time_in_force="gtc",
            current_time=current_time,
            side=side
        ):
            return False, "strategy_confirm_failed"
        
        # 2. 保护机制检查
        if self.protection_manager:
            can_trade, reason = self.protection_manager.can_trade(current_time)
            if not can_trade:
                return False, reason
        
        return True, ""
    
    def confirm_exit(
        self,
        pair: str,
        current_price: float,
        proposed_rate: float,
        amount: float,
        exit_reason: str,
        side: str = "long"
    ) -> tuple[bool, str]:
        """出场确认钩子"""
        current_time = datetime.now(UTC)
        
        return self.strategy.confirm_exit(
            pair=pair,
            order_type="market",
            amount=amount,
            rate=proposed_rate,
            time_in_force="gtc",
            current_time=current_time,
            exit_tag=exit_reason,
            side=side
        )
    
    def get_entry_price(
        self,
        pair: str,
        current_price: float,
        trade: Optional[Dict] = None,
        side: str = "long"
    ) -> float:
        """
        获取自定义入场价 (Freqtrade custom_entry_price)
        
        策略: 限价单挂单，不追高
        挂单价 = 买一价 * (1 - offset)
        """
        current_time = datetime.now(UTC)
        
        proposed_rate = current_price * 0.995  # 默认-0.5%
        
        return self.strategy.custom_entry_price(
            pair=pair,
            trade=self._convert_to_trade_info(trade) if trade else None,
            current_time=current_time,
            proposed_rate=proposed_rate,
            side=side
        )
    
    def get_exit_price(
        self,
        pair: str,
        current_price: float,
        trade: Dict,
        exit_reason: str,
        side: str = "long"
    ) -> float:
        """获取自定义出场价"""
        current_time = datetime.now(UTC)
        
        trade_info = self._convert_to_trade_info(trade)
        
        return self.strategy.custom_exit_price(
            pair=pair,
            trade=trade_info,
            current_time=current_time,
            proposed_rate=current_price,
            exit_tag=exit_reason,
            side=side
        )
    
    def should_exit(
        self,
        trade: Dict,
        current_price: float,
        current_time: datetime = None
    ) -> list[ExitCheckTuple]:
        """
        判断是否应该出场 (Freqtrade should_exit核心逻辑)
        
        检查顺序:
        1. ROI止盈
        2. 追踪止损
        3. 退出信号
        4. 最大持仓时间
        """
        current_time = current_time or datetime.now(UTC)
        
        trade_info = self._convert_to_trade_info(trade)
        trade_info._current_rate = current_price  # 更新当前价
        
        return self.strategy.should_exit(
            trade=trade_info,
            exit_rate=current_price,
            current_time=current_time,
            enter=False,
            exit_=False
        )
    
    def get_roi_threshold(self, hold_minutes: float) -> Optional[float]:
        """获取指定持仓时间的ROI阈值"""
        for minutes_str, threshold in sorted(
            self.strategy.config.minimal_roi.items(), 
            reverse=True
        ):
            if hold_minutes >= int(minutes_str):
                return threshold
        return None
    
    def get_leverage(self, pair: str, side: str = "long") -> float:
        """获取杠杆倍数"""
        current_time = datetime.now(UTC)
        return self.strategy.leverage(
            pair=pair,
            current_time=current_time,
            proposed_leverage=1.0,
            side=side
        )
    
    def _convert_to_trade_info(self, trade: Optional[Dict]) -> Optional[TradeInfo]:
        """将字典转换为TradeInfo"""
        if not trade:
            return None
        
        return TradeInfo(
            id=trade.get("index", 0),
            pair=trade.get("token", ""),
            amount=trade.get("position_size", 0),
            stake_amount=trade.get("position_size", 0) * trade.get("entry_price", 0),
            open_rate=trade.get("entry_price", 0),
            open_rate_requested=trade.get("entry_price", 0),
            open_date=datetime.fromisoformat(
                trade.get("timestamp", "").replace("Z", "+00:00")
            ) if trade.get("timestamp") else datetime.now(UTC),
        )


def create_integrator(params: Dict[str, Any] = None) -> FreqtradeIntegration:
    """工厂函数: 创建Freqtrade集成器"""
    return FreqtradeIntegration.create(params)
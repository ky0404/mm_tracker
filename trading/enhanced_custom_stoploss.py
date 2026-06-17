"""
自定义止损增强模块 - Custom Stoploss Enhancement
功能：基于PSAR的自定义动态止损 + 趋势反转紧急逃生
基于 freqtrade-strategies/CustomStoplossWithPSAR.py

用户需求：
- 不止损，大方向正确就不被强平
- 3倍杠杆不能遇到强平事件
- 只实现20%止盈

但为了防强平，需要：
- 趋势反转紧急逃生（系统级防爆仓兜底）
- 4H日线级别趋势逆转时强制平仓
"""
import logging
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StoplossDecision:
    """止损决策"""
    should_exit: bool      # 是否应该出场
    exit_reason: str       # 出场原因
    stoploss_pct: float    # 止损百分比（负数）
    emergency: bool        # 是否为紧急逃生


class EnhancedCustomStoploss:
    """
    增强型自定义止损
    
    Freqtrade custom_stoploss 逻辑：
    def custom_stoploss(self, pair, trade, current_time, current_rate, 
                        current_profit, **kwargs) -> float:
        # 返回相对于当前价格的止损 offset
        # 例如: -0.02 表示在当前价格下方2%设置止损
    
    我们的实现：
    1. 基础模式：不设止损（0%），靠大方向防强平
    2. 紧急逃生：4H趋势逆转时强制平仓
    3. PSAR动态追踪：可选，用于更精细的止损管理
    """
    
    def __init__(
        self,
        # 基础止损配置
        base_stoploss: float = 0.0,        # 0% = 不止损
        use_trailing_stop: bool = True,
        trailing_stop_trigger: float = 0.02,  # 2%利润后启动跟踪
        trailing_stop_offset: float = 0.04,    # 4%锁定利润
        
        # 紧急逃生配置
        use_emergency_exit: bool = True,
        emergency_4h_trend_reversal: bool = True,
        emergency_daily_trend_reversal: bool = True,
        
        # PSAR配置
        use_psar: bool = False,
        psar_acceleration: float = 0.02,
        psar_maximum: float = 0.2,
    ):
        # 基础止损
        self.base_stoploss = base_stoploss
        self.use_trailing_stop = use_trailing_stop
        self.trailing_stop_trigger = trailing_stop_trigger
        self.trailing_stop_offset = trailing_stop_offset
        
        # 紧急逃生
        self.use_emergency_exit = use_emergency_exit
        self.emergency_4h_trend_reversal = emergency_4h_trend_reversal
        self.emergency_daily_trend_reversal = emergency_daily_trend_reversal
        
        # PSAR
        self.use_psar = use_psar
        self.psar_acceleration = psar_acceleration
        self.psar_maximum = psar_maximum
        
        # 状态跟踪
        self.highest_prices: Dict[str, float] = {}
        self.trailing_activated: Dict[str, bool] = {}
        self.psar_values: Dict[str, float] = {}
        
        logger.info(f"[CustomSL] 基础止损: {base_stoploss}%, 跟踪止损: {use_trailing_stop}")
        logger.info(f"[CustomSL] 紧急逃生: {use_emergency_exit}, PSAR: {use_psar}")
    
    def calculate_psar(
        self,
        high: List[float],
        low: List[float],
        close: List[float],
        accel: float = 0.02,
        max_af: float = 0.2,
    ) -> float:
        """
        计算PSAR（抛物线转向指标）
        用于动态追踪止损
        """
        if len(close) < 3:
            return close[-1] if close else 0
        
        # 简化版PSAR计算
        trend = "up" if close[-1] > close[-2] else "down"
        
        if trend == "up":
            # 上升趋势：PSAR在低点
            psar = min(low[-3:])
            # 检查是否反转
            if close[-1] < psar:
                trend = "down"
                psar = max(high[-3:])
        else:
            # 下降趋势：PSAR在高点
            psar = max(high[-3:])
            # 检查是否反转
            if close[-1] > psar:
                trend = "up"
                psar = min(low[-3:])
        
        return psar
    
    def check_emergency_exit(
        self,
        token: str,
        current_price: float,
        entry_price: float,
        profit_pct: float,
        closes_4h: List[float],
        closes_1d: List[float] = None,
    ) -> Tuple[bool, str]:
        """
        检查是否需要紧急逃生
        
        触发条件：
        1. 4H趋势逆转（日线级别更严格）
        2. 价格跌破关键支撑
        3. 资金费率异常
        """
        if not self.use_emergency_exit:
            return False, ""
        
        # ===== 1. 4H趋势逆转检查 =====
        if self.emergency_4h_trend_reversal and len(closes_4h) >= 20:
            # 计算4H EMA
            ema_20 = np.mean(closes_4h[-20:])
            current_4h_price = closes_4h[-1]
            
            # 如果当前价格跌破20EMA，触发紧急逃生
            if current_4h_price < ema_20 * 0.98:  # 跌破2%
                logger.warning(f"[EmergencyExit] {token} 4H趋势逆转，跌破20EMA")
                return True, "EMERGENCY_4H_TREND_REVERSAL"
        
        # ===== 2. 日线趋势逆转检查 =====
        if self.emergency_daily_trend_reversal and closes_1d and len(closes_1d) >= 20:
            ema_20 = np.mean(closes_1d[-20:])
            current_daily_price = closes_1d[-1]
            
            if current_daily_price < ema_20 * 0.95:  # 跌破5%
                logger.warning(f"[EmergencyExit] {token} 日线趋势逆转，跌破20EMA 5%")
                return True, "EMERGENCY_DAILY_TREND_REVERSAL"
        
        # ===== 3. 大幅浮亏检查 =====
        if profit_pct < -15:  # 浮亏超过15%（3倍杠杆=45%跌幅）
            logger.warning(f"[EmergencyExit] {token} 浮亏过大: {profit_pct:.1f}%")
            return True, "EMERGENCY_LARGE_LOSS"
        
        return False, ""
    
    def calculate_stoploss(
        self,
        token: str,
        current_price: float,
        entry_price: float,
        profit_pct: float,
        closes_4h: List[float] = None,
        closes_1d: List[float] = None,
        highs: List[float] = None,
        lows: List[float] = None,
        closes: List[float] = None,
    ) -> StoplossDecision:
        """
        计算止损决策
        
        Returns:
            StoplossDecision: 包含是否出场、出场原因、止损百分比
        """
        # 1. 紧急逃生检查
        if self.use_emergency_exit:
            emergency_exit, emergency_reason = self.check_emergency_exit(
                token, current_price, entry_price, profit_pct,
                closes_4h or [], closes_1d or []
            )
            if emergency_exit:
                return StoplossDecision(
                    should_exit=True,
                    exit_reason=emergency_reason,
                    stoploss_pct=-0.01,  # 接近市价
                    emergency=True,
                )
        
        # 2. 跟踪止损检查
        if self.use_trailing_stop and profit_pct >= self.trailing_stop_trigger * 100:
            # 初始化最高价
            if token not in self.highest_prices:
                self.highest_prices[token] = current_price
                self.trailing_activated[token] = False
            
            # 更新最高价
            if current_price > self.highest_prices[token]:
                self.highest_prices[token] = current_price
                self.trailing_activated[token] = True
            
            # 如果已激活跟踪，计算止损价
            if self.trailing_activated.get(token, False):
                highest = self.highest_prices[token]
                stoploss_price = highest * (1 - self.trailing_stop_offset)
                stoploss_pct = (stoploss_price - current_price) / current_price
                
                # 如果当前价格低于跟踪止损价，触发出场
                if current_price <= stoploss_price:
                    return StoplossDecision(
                        should_exit=True,
                        exit_reason="TRAILING_STOP",
                        stoploss_pct=stoploss_pct,
                        emergency=False,
                    )
        
        # 3. PSAR止损检查（可选）
        if self.use_psar and highs and lows and closes:
            try:
                psar = self.calculate_psar(highs, lows, closes, self.psar_acceleration, self.psar_maximum)
                psar_stoploss_pct = (psar - current_price) / current_price
                
                # 如果价格跌破PSAR
                if current_price < psar:
                    return StoplossDecision(
                        should_exit=True,
                        exit_reason="PSAR_REVERSAL",
                        stoploss_pct=psar_stoploss_pct,
                        emergency=False,
                    )
            except:
                pass
        
        # 4. 默认不触发止损（用户需求：不止损）
        return StoplossDecision(
            should_exit=False,
            exit_reason="HOLD",
            stoploss_pct=self.base_stoploss,
            emergency=False,
        )
    
    def get_stoploss_pct(
        self,
        token: str,
        current_price: float,
        entry_price: float,
        profit_pct: float,
    ) -> float:
        """
        Freqtrade风格的custom_stoploss返回值
        
        返回值：
        - 负数offset：相对于当前价格的止损位置
        - 例如：-0.02 表示在当前价格下方2%止损
        """
        decision = self.calculate_stoploss(
            token=token,
            current_price=current_price,
            entry_price=entry_price,
            profit_pct=profit_pct,
        )
        
        return decision.stoploss_pct


def create_enhanced_custom_stoploss(
    base_stoploss: float = 0.0,
    use_emergency_exit: bool = True,
) -> EnhancedCustomStoploss:
    """创建增强型自定义止损"""
    return EnhancedCustomStoploss(
        base_stoploss=base_stoploss,
        use_trailing_stop=True,
        trailing_stop_trigger=0.02,
        trailing_stop_offset=0.04,
        use_emergency_exit=use_emergency_exit,
        use_psar=False,
    )


if __name__ == "__main__":
    # 测试
    stoploss = create_enhanced_custom_stoploss(base_stoploss=0.0)
    
    closes_4h = [100 + i * 0.3 for i in range(30)]
    entry_price = 100
    current_price = 95
    profit_pct = -5.0
    
    decision = stoploss.calculate_stoploss(
        token="TEST",
        current_price=current_price,
        entry_price=entry_price,
        profit_pct=profit_pct,
        closes_4h=closes_4h,
    )
    
    print(f"是否出场: {decision.should_exit}")
    print(f"出场原因: {decision.exit_reason}")
    print(f"止损比例: {decision.stoploss_pct:.2%}")
    print(f"紧急: {decision.emergency}")
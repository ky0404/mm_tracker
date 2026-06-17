"""
超级趋势策略集成 - Supertrend Trend Confirmation
功能：基于Supertrend指标确认趋势方向，作为入场时机判断
基于 freqtrade-strategies/Supertrend.py

用户需求：
- 找到正确的方向
- 3倍杠杆不能遇到强平事件
- 大方向正确就不被强平
"""
import logging
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SupertrendResult:
    """Supertrend结果"""
    trend: str          # "up" or "down"
    st_value: float     # ST值
    strength: float     # 趋势强度 0-1
    signal: str         # "buy", "sell", "neutral"


class SupertrendStrategy:
    """
    超级趋势策略
    
    使用3组Supertrend指标确认趋势：
    - buy_supertrend: 3个指标同时为"up" -> 买入信号
    - sell_supertrend: 3个指标同时为"down" -> 卖出信号
    
    这与freqtrade的Supertrend.py完全一致
    """
    
    def __init__(
        self,
        buy_m1: int = 4,
        buy_m2: int = 7,
        buy_m3: int = 1,
        buy_p1: int = 8,
        buy_p2: int = 9,
        buy_p3: int = 8,
    ):
        # Buy超参数
        self.buy_m1 = buy_m1
        self.buy_m2 = buy_m2
        self.buy_m3 = buy_m3
        self.buy_p1 = buy_p1
        self.buy_p2 = buy_p2
        self.buy_p3 = buy_p3
        
        logger.info(f"[Supertrend] Buy参数: m={buy_m1},{buy_m2},{buy_m3}, p={buy_p1},{buy_p2},{buy_p3}")
    
    def calculate_supertrend(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
        multiplier: float,
        period: int,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        计算Supertrend指标
        返回: (ST, STX)
        """
        # 1. TR和ATR
        tr = np.maximum(
            high - low,
            np.maximum(
                np.abs(high - np.roll(close, 1)),
                np.abs(low - np.roll(close, 1))
            )
        )
        atr = pd.Series(tr).rolling(period).mean().values
        
        # 2. 基础上下轨
        basic_ub = (high + low) / 2 + multiplier * atr
        basic_lb = (high + low) / 2 - multiplier * atr
        
        # 3. 最终上下轨
        final_ub = np.zeros_like(close)
        final_lb = np.zeros_like(close)
        
        for i in range(period, len(close)):
            if i == period:
                final_ub[i] = basic_ub[i]
                final_lb[i] = basic_lb[i]
            else:
                final_ub[i] = (
                    basic_ub[i] if basic_ub[i] < final_ub[i-1] or close[i-1] > final_ub[i-1]
                    else final_ub[i-1]
                )
                final_lb[i] = (
                    basic_lb[i] if basic_lb[i] > final_lb[i-1] or close[i-1] < final_lb[i-1]
                    else final_lb[i-1]
                )
        
        # 4. ST计算
        st = np.zeros_like(close)
        for i in range(period, len(close)):
            if st[i-1] == final_ub[i-1]:
                st[i] = final_ub[i] if close[i] <= final_ub[i] else final_lb[i]
            elif st[i-1] == final_lb[i-1]:
                st[i] = final_lb[i] if close[i] >= final_lb[i] else final_ub[i]
        
        # 5. STX方向
        stx = np.where(st > 0, np.where(close < st, 'down', 'up'), 'neutral')
        
        return st, stx
    
    def calculate_3_supertrends(
        self,
        high: np.ndarray,
        low: np.ndarray,
        close: np.ndarray,
    ) -> Dict[str, any]:
        """
        计算3组Supertrend指标
        """
        results = {}
        
        # Group 1 (buy_m1, buy_p1)
        st1, stx1 = self.calculate_supertrend(high, low, close, self.buy_m1, self.buy_p1)
        results["st1"] = st1[-1] if len(st1) > 0 else 0
        results["stx1"] = stx1[-1] if len(stx1) > 0 else "neutral"
        
        # Group 2 (buy_m2, buy_p2)
        st2, stx2 = self.calculate_supertrend(high, low, close, self.buy_m2, self.buy_p2)
        results["st2"] = st2[-1] if len(st2) > 0 else 0
        results["stx2"] = stx2[-1] if len(stx2) > 0 else "neutral"
        
        # Group 3 (buy_m3, buy_p3)
        st3, stx3 = self.calculate_supertrend(high, low, close, self.buy_m3, self.buy_p3)
        results["st3"] = st3[-1] if len(st3) > 0 else 0
        results["stx3"] = stx3[-1] if len(stx3) > 0 else "neutral"
        
        return results
    
    def analyze(
        self,
        high: List[float],
        low: List[float],
        close: List[float],
    ) -> SupertrendResult:
        """
        分析趋势并返回信号
        
        Returns:
            SupertrendResult: 包含trend, st_value, strength, signal
        """
        high = np.array(high)
        low = np.array(low)
        close = np.array(close)
        
        if len(close) < 50:
            return SupertrendResult("neutral", 0, 0, "neutral")
        
        # 计算3组Supertrend
        results = self.calculate_3_supertrends(high, low, close)
        
        # 判断信号：3个指标都为"up" -> buy, 都为"down" -> sell
        all_up = all([
            results.get("stx1") == "up",
            results.get("stx2") == "up",
            results.get("stx3") == "up",
        ])
        
        all_down = all([
            results.get("stx1") == "down",
            results.get("stx2") == "down",
            results.get("stx3") == "down",
        ])
        
        if all_up:
            # 计算趋势强度
            strength = 1.0
            return SupertrendResult("up", results["st1"], strength, "buy")
        
        elif all_down:
            strength = 1.0
            return SupertrendResult("down", results["st1"], strength, "sell")
        
        else:
            # 混合状态
            up_count = sum([
                results.get("stx1") == "up",
                results.get("stx2") == "up",
                results.get("stx3") == "up",
            ])
            strength = up_count / 3.0
            
            if up_count >= 2:
                return SupertrendResult("up", results["st1"], strength, "buy")
            elif up_count <= 1:
                return SupertrendResult("down", results["st1"], strength, "sell")
            else:
                return SupertrendResult("neutral", results["st1"], 0, "neutral")


def create_supertrend_strategy(
    buy_m1: int = 4,
    buy_m2: int = 7,
    buy_m3: int = 1,
    buy_p1: int = 8,
    buy_p2: int = 9,
    buy_p3: int = 8,
) -> SupertrendStrategy:
    """创建Supertrend策略"""
    return SupertrendStrategy(
        buy_m1=buy_m1,
        buy_m2=buy_m2,
        buy_m3=buy_m3,
        buy_p1=buy_p1,
        buy_p2=buy_p2,
        buy_p3=buy_p3,
    )


if __name__ == "__main__":
    # 测试
    import random
    
    # 模拟上涨趋势数据
    close = [100 + i * 0.5 + random.uniform(-0.2, 0.3) for i in range(60)]
    high = [c + random.uniform(0.5, 1.5) for c in close]
    low = [c - random.uniform(0.5, 1.5) for c in close]
    
    strategy = create_supertrend_strategy()
    result = strategy.analyze(high, low, close)
    
    print(f"趋势: {result.trend}, 信号: {result.signal}, 强度: {result.strength:.2f}")
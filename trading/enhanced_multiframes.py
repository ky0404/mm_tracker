"""
增强型多时间框架分析器 - Enhanced Multi-Timeframe Analyzer
功能：整合freqtrade @informative装饰器模式，增强4H/1H/15m多时间框架分析
基于 freqtrade-strategies/multi_tf.py 和用户的4H门卫需求

用户需求：
- 找到正确的方向以及入场时机
- 4H趋势门卫（最重要）
- 1H RSI确认
- 15m超卖反弹信号
- 不能遇到强平事件（通过大周期趋势确认防强平）
"""
import logging
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TimeFrameAnalysis:
    """时间框架分析结果"""
    timeframe: str          # "4h", "1h", "15m"
    trend: str              # "up", "down", "neutral"
    rsi: float              # RSI值
    ema_trend: str          # EMA趋势
    signal: str             # "buy", "sell", "neutral"
    confidence: float       # 置信度 0-1
    details: Dict           # 详细指标


@dataclass
class MultiTFDecision:
    """多时间框架综合决策"""
    can_entry: bool         # 是否可以入场
    trend_direction: str    # 趋势方向
    entry_signal: str       # 入场信号
    risk_level: str         # "low", "medium", "high"
    details: Dict           # 详细决策


class EnhancedMultiTimeFrameAnalyzer:
    """
    增强型多时间框架分析器
    
    Freqtrade @informative 模式：
    @informative('30m')
    @informative('1h')
    def populate_indicators_1h(self, dataframe, metadata):
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        return dataframe
    
    我们的实现：
    - 4H (门卫): 大方向确认，防强平基石
    - 1H (动量): 中期趋势确认
    - 15M (入场): 短期超卖反弹信号
    """
    
    # 时间框架优先级
    TIMEFRAME_PRIORITY = {
        "4h": 3,   # 最重要，大方向
        "1h": 2,   # 中等，动量确认
        "15m": 1,  # 基础，入场时机
    }
    
    def __init__(
        self,
        require_4h_uptrend: bool = True,
        require_1h_confirm: bool = True,
        use_supertrend: bool = True,
        use_rsi: bool = True,
    ):
        self.require_4h_uptrend = require_4h_uptrend
        self.require_1h_confirm = require_1h_confirm
        self.use_supertrend = use_supertrend
        self.use_rsi = use_rsi
        
        # RSI参数
        self.rsi_period = 14
        self.rsi_oversold = 30
        self.rsi_overbought = 70
        
        # EMA参数
        self.ema_fast = 20
        self.ema_slow = 50
        
        logger.info(f"[MultiTF] 4H门卫: {require_4h_uptrend}, 1H确认: {require_1h_confirm}")
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """计算RSI"""
        if len(prices) < period + 1:
            return 50.0
        
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        
        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_ema(self, prices: List[float], period: int) -> float:
        """计算EMA"""
        if len(prices) < period:
            return prices[-1] if prices else 0
        
        prices_arr = np.array(prices)
        ema = prices_arr[0]
        
        for price in prices_arr[1:]:
            ema = ema * (period - 1) / period + price * 2 / period
        
        return ema
    
    def analyze_timeframe(
        self,
        closes: List[float],
        highs: List[float],
        lows: List[float],
        timeframe: str,
    ) -> TimeFrameAnalysis:
        """
        分析单个时间框架
        
        Returns:
            TimeFrameAnalysis: 分析结果
        """
        if len(closes) < 50:
            return TimeFrameAnalysis(
                timeframe=timeframe,
                trend="neutral",
                rsi=50,
                ema_trend="neutral",
                signal="neutral",
                confidence=0,
                details={},
            )
        
        # 计算RSI
        rsi = self.calculate_rsi(closes, self.rsi_period)
        
        # 计算EMA
        ema_fast = self.calculate_ema(closes, self.ema_fast)
        ema_slow = self.calculate_ema(closes, self.ema_slow)
        
        # 判断EMA趋势
        if ema_fast > ema_slow:
            ema_trend = "up"
            trend = "up"
        elif ema_fast < ema_slow:
            ema_trend = "down"
            trend = "down"
        else:
            ema_trend = "neutral"
            trend = "neutral"
        
        # 判断信号
        signal = "neutral"
        confidence = 0.5
        
        if timeframe in ["4h", "1h"]:
            # 大时间框架：主要看趋势
            if trend == "up" and rsi < self.rsi_overbought:
                signal = "buy"
                confidence = 0.7
            elif trend == "down" and rsi > self.rsi_oversold:
                signal = "sell"
                confidence = 0.7
        else:
            # 15m: 找超卖反弹
            if rsi < self.rsi_oversold:
                signal = "buy"
                confidence = 0.6
            elif rsi > self.rsi_overbought:
                signal = "sell"
                confidence = 0.6
        
        return TimeFrameAnalysis(
            timeframe=timeframe,
            trend=trend,
            rsi=rsi,
            ema_trend=ema_trend,
            signal=signal,
            confidence=confidence,
            details={
                "ema_fast": ema_fast,
                "ema_slow": ema_slow,
                "current_price": closes[-1],
            },
        )
    
    def make_decision(
        self,
        analysis_4h: TimeFrameAnalysis,
        analysis_1h: TimeFrameAnalysis,
        analysis_15m: TimeFrameAnalysis,
    ) -> MultiTFDecision:
        """
        综合多时间框架分析，做出交易决策
        
        Freqtrade风格决策逻辑：
        - 4H必须是上升趋势（门卫）
        - 1H确认趋势
        - 15m给出入场时机
        """
        can_entry = False
        trend_direction = "neutral"
        entry_signal = "neutral"
        risk_level = "high"
        
        # ===== 1. 4H门卫检查 (最重要) =====
        if self.require_4h_uptrend:
            if analysis_4h.trend != "up":
                logger.info(f"[MultiTF] 4H门卫拒绝: 4H趋势={analysis_4h.trend}, 不是上升趋势")
                return MultiTFDecision(
                    can_entry=False,
                    trend_direction=analysis_4h.trend,
                    entry_signal="rejected_by_4h",
                    risk_level="high",
                    details={"reason": "4H trend not up"},
                )
        
        # ===== 2. 1H趋势确认 =====
        if self.require_1h_confirm:
            if analysis_1h.trend != "up":
                logger.info(f"[MultiTF] 1H确认拒绝: 1H趋势={analysis_1h.trend}")
                return MultiTFDecision(
                    can_entry=False,
                    trend_direction=analysis_1h.trend,
                    entry_signal="rejected_by_1h",
                    risk_level="medium",
                    details={"reason": "1H trend not confirmed"},
                )
        
        # ===== 3. 15m入场时机 =====
        if analysis_15m.signal == "buy" and analysis_15m.rsi < 40:
            can_entry = True
            entry_signal = "buy_15m_oversold"
            risk_level = "low"
        elif analysis_15m.signal == "buy" and analysis_15m.rsi < 50:
            can_entry = True
            entry_signal = "buy_15m_momentum"
            risk_level = "medium"
        else:
            # 15m没有强烈信号，但4H和1H都OK，可以尝试轻仓
            can_entry = True
            entry_signal = "buy_4h_1h_trend"
            risk_level = "medium"
        
        # 综合趋势方向
        if analysis_4h.trend == "up" and analysis_1h.trend == "up":
            trend_direction = "up"
        elif analysis_4h.trend == "down" or analysis_1h.trend == "down":
            trend_direction = "down"
        else:
            trend_direction = "neutral"
        
        return MultiTFDecision(
            can_entry=can_entry,
            trend_direction=trend_direction,
            entry_signal=entry_signal,
            risk_level=risk_level,
            details={
                "4h": {"trend": analysis_4h.trend, "rsi": analysis_4h.rsi},
                "1h": {"trend": analysis_1h.trend, "rsi": analysis_1h.rsi},
                "15m": {"signal": analysis_15m.signal, "rsi": analysis_15m.rsi},
            },
        )
    
    def analyze(
        self,
        closes_4h: List[float],
        highs_4h: List[float],
        lows_4h: List[float],
        closes_1h: List[float],
        highs_1h: List[float],
        lows_1h: List[float],
        closes_15m: List[float],
        highs_15m: List[float],
        lows_15m: List[float],
    ) -> MultiTFDecision:
        """
        综合分析多时间框架
        
        Args:
            closes_4h: 4小时K线收盘价
            closes_1h: 1小时K线收盘价
            closes_15m: 15分钟K线收盘价
        
        Returns:
            MultiTFDecision: 综合决策
        """
        # 分析各时间框架
        analysis_4h = self.analyze_timeframe(closes_4h, highs_4h, lows_4h, "4h")
        analysis_1h = self.analyze_timeframe(closes_1h, highs_1h, lows_1h, "1h")
        analysis_15m = self.analyze_timeframe(closes_15m, highs_15m, lows_15m, "15m")
        
        logger.info(f"[MultiTF] 4H: trend={analysis_4h.trend}, rsi={analysis_4h.rsi:.1f}")
        logger.info(f"[MultiTF] 1H: trend={analysis_1h.trend}, rsi={analysis_1h.rsi:.1f}")
        logger.info(f"[MultiTF] 15M: signal={analysis_15m.signal}, rsi={analysis_15m.rsi:.1f}")
        
        # 综合决策
        return self.make_decision(analysis_4h, analysis_1h, analysis_15m)


def create_enhanced_multiframes_analyzer() -> EnhancedMultiTimeFrameAnalyzer:
    """创建增强型多时间框架分析器"""
    return EnhancedMultiTimeFrameAnalyzer(
        require_4h_uptrend=True,
        require_1h_confirm=True,
        use_supertrend=True,
        use_rsi=True,
    )


if __name__ == "__main__":
    # 测试
    import random
    
    # 生成模拟数据
    closes_4h = [100 + i * 0.3 + random.uniform(-0.1, 0.2) for i in range(100)]
    highs_4h = [c + random.uniform(0.5, 1.5) for c in closes_4h]
    lows_4h = [c - random.uniform(0.5, 1.5) for c in closes_4h]
    
    closes_1h = [100 + i * 0.4 + random.uniform(-0.2, 0.3) for i in range(100)]
    highs_1h = [c + random.uniform(0.3, 1.0) for c in closes_1h]
    lows_1h = [c - random.uniform(0.3, 1.0) for c in closes_1h]
    
    closes_15m = [100 + i * 0.5 + random.uniform(-0.3, 0.4) for i in range(100)]
    highs_15m = [c + random.uniform(0.2, 0.8) for c in closes_15m]
    lows_15m = [c - random.uniform(0.2, 0.8) for c in closes_15m]
    
    analyzer = create_enhanced_multiframes_analyzer()
    decision = analyzer.analyze(
        closes_4h, highs_4h, lows_4h,
        closes_1h, highs_1h, lows_1h,
        closes_15m, highs_15m, lows_15m,
    )
    
    print(f"是否可以入场: {decision.can_entry}")
    print(f"趋势方向: {decision.trend_direction}")
    print(f"入场信号: {decision.entry_signal}")
    print(f"风险等级: {decision.risk_level}")
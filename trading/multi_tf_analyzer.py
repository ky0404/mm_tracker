"""
Freqtrade风格多时间框架分析
- 1H/4H/1D 多时间框架确认
- 多时间框架RSI/EMA一致性判断
- 类似Freqtrade的@informative装饰器逻辑
"""
import requests
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# 移除代理，使用直连


@dataclass
class MultiTimeFrameAnalysis:
    """多时间框架分析结果"""
    token: str
    # 各时间框架的指标
    m5: dict  # 5分钟
    m15: dict  # 15分钟
    h1: dict  # 1小时
    h4: dict  # 4小时
    d1: dict  # 1天
    
    # 综合信号
    entry_signal: bool = False
    exit_signal: bool = False
    confidence: float = 0.0
    signal_reasons: List[str] = None
    
    def __post_init__(self):
        if self.signal_reasons is None:
            self.signal_reasons = []


class MultiTimeFrameAnalyzer:
    """
    多时间框架分析器
    Freqtrade风格: 多个时间周期互相验证
    """
    
    TIMEFRAMES = ["5m", "15m", "1h", "4h", "1d"]
    
    def __init__(self):
        self.cache: Dict[str, dict] = {}
        
    def get_candles(self, token: str, timeframe: str, limit: int = 100) -> List[List[float]]:
        """获取K线数据"""
        url = "https://www.okx.com/api/v5/market/history-candles"
        params = {
            "instId": f"{token}-USDT",
            "bar": timeframe.upper(),  # OKX要求大写，如4H不是4h
            "limit": limit
        }
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            return resp.json()["data"]
        except Exception as e:
            logger.error(f"[K线获取失败] {token} {timeframe}: {e}")
            return []
    
    def calculate_ema(self, closes: List[float], period: int) -> float:
        """计算EMA"""
        if len(closes) < period:
            period = len(closes)
        if period <= 0:
            return 0
            
        multiplier = 2 / (period + 1)
        ema = closes[0]
        
        for price in closes[1:]:
            ema = (price - ema) * multiplier + ema
            
        return ema
    
    def calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """计算RSI"""
        if len(closes) < period + 1:
            return 50
            
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100
            
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi
    
    def calculate_bollinger(self, closes: List[float], period: int = 20) -> Dict[str, float]:
        """计算布林带"""
        if len(closes) < period:
            return {"upper": 0, "middle": 0, "lower": 0}
            
        sma = sum(closes[-period:]) / period
        variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
        std = variance ** 0.5
        
        return {
            "upper": sma + 2 * std,
            "middle": sma,
            "lower": sma - 2 * std
        }
    
    def calculate_supertrend(self, candles: List[List[float]], period: int = 10, multiplier: float = 3.0) -> str:
        """
        计算Supertrend
        Freqtrade策略中常用的指标
        返回: "up", "down", "neutral"
        """
        if len(candles) < period:
            return "neutral"
            
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        closes = [float(c[4]) for c in candles]
        
        # ATR
        trs = []
        for i in range(1, len(candles)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )
            trs.append(tr)
            
        atr = sum(trs[-period:]) / period if trs else 0
        
        # Basic bands
        hl_avg = [(highs[i] + lows[i]) / 2 for i in range(len(highs))]
        basic_upper = [hl_avg[i] + multiplier * atr for i in range(len(hl_avg))]
        basic_lower = [hl_avg[i] - multiplier * atr for i in range(len(hl_avg))]
        
        # Final bands (simplified)
        close = closes[-1]
        upper = basic_upper[-1]
        lower = basic_lower[-1]
        
        if close > upper:
            return "up"
        elif close < lower:
            return "down"
        else:
            return "neutral"
    
    def analyze_timeframe(self, token: str, timeframe: str) -> dict:
        """分析单个时间框架"""
        candles = self.get_candles(token, timeframe, 100)
        
        if not candles:
            return {}
            
        closes = [float(c[4]) for c in candles]
        highs = [float(c[2]) for c in candles]
        lows = [float(c[3]) for c in candles]
        
        # 基础指标
        rsi = self.calculate_rsi(closes)
        ema9 = self.calculate_ema(closes, 9)
        ema21 = self.calculate_ema(closes, 21)
        ema50 = self.calculate_ema(closes, 50)
        current_price = closes[-1]
        
        # 趋势判断
        trend = "up" if ema9 > ema21 else "down"
        
        # 相对位置
        bb = self.calculate_bollinger(closes)
        position_pct = (current_price - bb["lower"]) / (bb["upper"] - bb["lower"]) * 100 if bb["upper"] != bb["lower"] else 50
        
        # Supertrend
        st = self.calculate_supertrend(candles)
        
        return {
            "timeframe": timeframe,
            "price": current_price,
            "rsi": rsi,
            "ema9": ema9,
            "ema21": ema21,
            "ema50": ema50,
            "trend": trend,
            "bb_upper": bb["upper"],
            "bb_middle": bb["middle"],
            "bb_lower": bb["lower"],
            "bb_position": position_pct,
            "supertrend": st,
            "high_4h": highs[-4] if len(highs) >= 4 else highs[-1],
            "low_4h": lows[-4] if len(lows) >= 4 else lows[-1],
        }
    
    def analyze(self, token: str) -> MultiTimeFrameAnalysis:
        """
        综合多时间框架分析
        类似Freqtrade的multi_tf策略
        """
        result = MultiTimeFrameAnalysis(
            token=token,
            m5=self.analyze_timeframe(token, "5m"),
            m15=self.analyze_timeframe(token, "15m"),
            h1=self.analyze_timeframe(token, "1h"),
            h4=self.analyze_timeframe(token, "4h"),
            d1=self.analyze_timeframe(token, "1d")
        )
        
        # 4H是主时间框架（门卫）
        h4 = result.h4
        h1 = result.h1
        m15 = result.m15
        
        if not h4:
            return result
            
        signal_reasons = []
        entry_score = 0
        
        # ===== 入场条件 =====
        
        # 1. 4H EMA趋势确认 (最重要)
        if h4.get("trend") == "up":
            entry_score += 3
            signal_reasons.append("4H_UPTREND")
        else:
            # 4H下降但其他周期可能反弹
            if m15.get("rsi", 50) < 35:
                entry_score += 1
                signal_reasons.append("4H_DOWN_M15_RSI_OVERSOLD")
        
        # 2. RSI超卖反弹
        for tf in [h4, h1, m15]:
            rsi = tf.get("rsi", 50)
            if rsi < 30:
                entry_score += 2
                signal_reasons.append(f"{tf['timeframe']}_RSI_{rsi:.0f}_OVERSOLD")
                break
            elif rsi < 40:
                entry_score += 1
                signal_reasons.append(f"{tf['timeframe']}_RSI_{rsi:.0f}_LOW")
        
        # 3. Supertrend由下转上
        if h4.get("supertrend") == "up" or h1.get("supertrend") == "up":
            entry_score += 2
            signal_reasons.append("SUPERTREND_REVERSAL")
        
        # 4. 布林带位置
        for tf in [h4, h1]:
            bb_pos = tf.get("bb_position", 50)
            if bb_pos < 20:  # 接近下轨
                entry_score += 1
                signal_reasons.append(f"{tf['timeframe']}_BB_LOWER")
        
        # 5. 多时间框架一致性
        up_count = sum([
            h4.get("trend") == "up",
            h1.get("trend") == "up", 
            m15.get("trend") == "up"
        ])
        if up_count >= 2:
            entry_score += 2
            signal_reasons.append("MULTI_TF_ALIGNED")
        
        # 6. 资金费率
        # (后面单独获取)
        
        # ===== 出场条件 =====
        
        exit_score = 0
        
        # RSI超买
        for tf in [h4, h1]:
            rsi = tf.get("rsi", 50)
            if rsi > 70:
                exit_score += 2
                signal_reasons.append(f"{tf['timeframe']}_RSI_{rsi:.0f}_OVERBOUGHT")
        
        # Supertrend反转
        if h4.get("supertrend") == "down":
            exit_score += 2
            signal_reasons.append("4H_SUPERTREND_DOWN")
        
        # 设置结果
        result.signal_reasons = signal_reasons
        
        if entry_score >= 4:
            result.entry_signal = True
            result.confidence = min(entry_score / 10, 1.0)
        elif entry_score >= 2:
            result.confidence = entry_score / 10
            
        if exit_score >= 2:
            result.exit_signal = True
            
        return result
    
    def get_entry_price_recommendation(self, token: str, analysis: MultiTimeFrameAnalysis) -> dict:
        """
        基于多时间框架分析，推荐挂单价
        这是你要求学习的核心功能
        """
        if not analysis.h4:
            return {"price": 0, "method": "market"}
        
        current = analysis.h4["price"]
        bb_lower = analysis.h4.get("bb_lower", current * 0.98)
        ema21 = analysis.h4.get("ema21", current)
        
        # 方法1: 回调到EMA21附近挂单
        method1_price = ema21 * 1.005  # 比EMA21高0.5%
        
        # 方法2: 回调到布林下轨
        method2_price = bb_lower * 1.01  # 比下轨高1%
        
        # 方法3: 买一价-0.3%
        market = self._get_market_price(token)
        method3_price = market["bid"] * 0.997
        
        # 选择最佳方法（根据历史表现学习）
        recommended = method1_price  # 默认用EMA21
        
        return {
            "recommended_price": round(recommended, 5),
            "methods": {
                "ema21_approach": round(method1_price, 5),
                "bb_lower_approach": round(method2_price, 5),
                "bid_minus": round(method3_price, 5)
            },
            "current_price": current,
            "discount_pct": (current - recommended) / current * 100
        }
    
    def _get_market_price(self, token: str) -> dict:
        """获取实时市场价格"""
        url = "https://www.okx.com/api/v5/market/ticker"
        params = {"instId": f"{token}-USDT"}
        
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json()["data"][0]
        
        return {
            "bid": float(data["bidPx"]),
            "ask": float(data["askPx"]),
            "last": float(data["last"])
        }


# ==================== 单元测试 ====================
if __name__ == "__main__":
    analyzer = MultiTimeFrameAnalyzer()
    
    print("=== 多时间框架分析测试 ===\n")
    
    # 测试OP
    analysis = analyzer.analyze("OP")
    
    print(f"代币: {analysis.token}")
    print(f"4H 趋势: {analysis.h4.get('trend', 'N/A')}, RSI: {analysis.h4.get('rsi', 0):.0f}")
    print(f"1H 趋势: {analysis.h1.get('trend', 'N/A')}, RSI: {analysis.h1.get('rsi', 0):.0f}")
    print(f"信号: 入场={analysis.entry_signal}, 出场={analysis.exit_signal}")
    print(f"置信度: {analysis.confidence:.0%}")
    print(f"原因: {analysis.signal_reasons}")
    
    # 推荐挂单价
    rec = analyzer.get_entry_price_recommendation("OP", analysis)
    print(f"\n=== 挂单价推荐 ===")
    print(f"建议挂单价: ${rec['recommended_price']:.5f}")
    print(f"当前价: ${rec['current_price']:.5f}")
    print(f"折扣: {rec['discount_pct']:.2f}%")
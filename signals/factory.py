"""
信号工厂 - 统一所有量化信号
将7信号 + 多时间框架信号 统一为一套体系

工厂模式：
  SignalFactory.create('signal_name') → 返回信号实例
  SignalFactory.scan_all(token) → 返回所有信号评分
"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """信号结果"""
    name: str
    triggered: bool
    weight: float
    detail: str
    source: str  # '1h'/'4h'/'15m'/'1d'/'static'


class BaseSignal(ABC):
    """信号基类"""
    
    name: str = ""
    weight: float = 1.0
    source: str = "static"  # 1h/4h/15m/1d/static
    
    @abstractmethod
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        """评估信号"""
        pass


class SignalFactory:
    """信号工厂 - 统一创建所有信号"""
    
    _signals: Dict[str, type] = {}
    
    @classmethod
    def register(cls, name: str, signal_class: type):
        """注册信号"""
        cls._signals[name] = signal_class
    
    @classmethod
    def create(cls, name: str) -> Optional[BaseSignal]:
        """创建信号实例"""
        return cls._signals.get(name)()
    
    @classmethod
    def scan_all(cls, token: str, data: Dict[str, Any]) -> Dict[str, SignalResult]:
        """扫描所有信号"""
        results = {}
        for name, signal_class in cls._signals.items():
            try:
                signal = signal_class()
                result = signal.evaluate(data)
                results[name] = result
            except Exception as e:
                logger.debug(f"信号{name}评估失败: {e}")
        return results
    
    @classmethod
    def calculate_total_score(cls, results: Dict[str, SignalResult]) -> dict:
        """计算总分 - 支持从配置读取权重"""
        # 尝试加载配置权重
        custom_weights = {}
        try:
            import json
            with open('config/params.json') as f:
                params = json.load(f)
                custom_weights = params.get('signal_weights', {})
        except:
            pass
        
        total = 0
        triggered = []
        by_source = {"1h": 0, "4h": 0, "15m": 0, "1d": 0, "static": 0}
        
        for name, result in results.items():
            if result.triggered:
                # 使用配置权重或默认权重
                weight = custom_weights.get(name, result.weight)
                total += weight
                triggered.append(name)
                by_source[result.source] = by_source.get(result.source, 0) + weight
        
        return {
            "total_score": round(total, 2),
            "triggered_count": len(triggered),
            "triggered_signals": triggered,
            "by_source": by_source,
            "grade": "ENTRY" if len(triggered) >= 3 and total >= 5 else "WATCH"
        }


# ===== 4H层信号 =====
class Signal4H_EMABullish(BaseSignal):
    name = "4h_ema_bullish"
    weight = 2.0
    source = "4h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h4 = data.get("analysis_4h", {})
        triggered = h4.get("ema_bullish", False)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail="4H EMA金叉" if triggered else "EMA死叉",
            source=self.source
        )


class Signal4H_RSIRecovering(BaseSignal):
    name = "4h_rsi_recovering"
    weight = 1.5
    source = "4h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h4 = data.get("analysis_4h", {})
        triggered = h4.get("rsi_recovering", False)
        rsi = h4.get("current_rsi", 0)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"4H RSI回升到{rsi}" if triggered else "RSI未回升",
            source=self.source
        )


class Signal4H_VolExpanding(BaseSignal):
    name = "4h_vol_expanding"
    weight = 1.5
    source = "4h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h4 = data.get("analysis_4h", {})
        triggered = h4.get("vol_expanding", False)
        ratio = h4.get("current_vol_ratio", 0)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"4H量比{ratio}x" if triggered else "量能未放大",
            source=self.source
        )


# ===== NFI风格 4H Surface信号 =====
class Signal4H_EMATrend(BaseSignal):
    """NFI风格 - EMA趋势: 必须比3根K线前高1%以上"""
    name = "4h_ema_trend_rising"
    weight = 2.5
    source = "4h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h4 = data.get("analysis_4h", {})
        triggered = h4.get("ema50_rising", False)
        momentum = h4.get("ema50_momentum_pct", 0)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"4H EMA趋势↑ {momentum:+.1f}%" if triggered else "EMA趋势↓",
            source=self.source
        )


class Signal4H_RSIOversoldRecovery(BaseSignal):
    """NFI风格 - RSI超卖恢复"""
    name = "4h_rsi_oversold_recovery"
    weight = 2.0
    source = "4h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h4 = data.get("analysis_4h", {})
        triggered = h4.get("rsi_oversold_recovery", False)
        rsi = h4.get("current_rsi", 0)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"4H RSI超卖恢复 {rsi:.0f}" if triggered else "RSI非超卖恢复",
            source=self.source
        )


class Signal4H_GoldenCross(BaseSignal):
    """NFI风格 - EMA金叉确认"""
    name = "4h_golden_cross"
    weight = 1.5
    source = "4h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h4 = data.get("analysis_4h", {})
        triggered = h4.get("golden_cross", False)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail="4H EMA8>EMA21金叉" if triggered else "EMA死叉",
            source=self.source
        )


# ===== NFI风格 1H Surface信号 =====
class Signal1H_EMATrend(BaseSignal):
    """NFI风格 - 1H EMA趋势"""
    name = "1h_ema_trend_rising"
    weight = 2.0
    source = "1h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h1 = data.get("analysis_1h", {})
        triggered = h1.get("ema50_rising", False)
        detail = h1.get("detail", "")
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"1H {detail}" if triggered else "1H EMA趋势↓",
            source=self.source
        )


class Signal1H_RSINotOverbought(BaseSignal):
    """NFI风格 - RSI未超买"""
    name = "1h_rsi_not_overbought"
    weight = 1.5
    source = "1h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h1 = data.get("analysis_1h", {})
        triggered = h1.get("rsi_not_overbought", False)
        rsi = h1.get("current_rsi", 50)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"1H RSI {rsi:.0f} 健康" if triggered else f"1H RSI {rsi:.0f} 超买",
            source=self.source
        )


class Signal1H_EMACrossUp(BaseSignal):
    """NFI风格 - EMA20上穿EMA50"""
    name = "1h_ema_cross_up"
    weight = 1.5
    source = "1h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        h1 = data.get("analysis_1h", {})
        triggered = h1.get("ema_cross_up", False)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail="1H EMA20上穿EMA50" if triggered else "无EMA金叉",
            source=self.source
        )


# ===== NFI风格 15M Surface信号 =====
class Signal15M_EMATrend(BaseSignal):
    """NFI风格 - 15M EMA趋势"""
    name = "15m_ema_trend_up"
    weight = 1.5
    source = "15m"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        m15 = data.get("analysis_15m", {})
        triggered = m15.get("ema_trend_up", False)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail="15M EMA趋势↑" if triggered else "15M EMA趋势↓",
            source=self.source
        )


class Signal15M_RSIOversold(BaseSignal):
    """NFI风格 - 15M RSI超卖恢复"""
    name = "15m_rsi_oversold_recovery"
    weight = 2.0
    source = "15m"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        m15 = data.get("analysis_15m", {})
        triggered = m15.get("rsi_oversold_recovery", False)
        rsi = m15.get("current_rsi", 50)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"15M RSI {rsi:.0f} 超卖恢复" if triggered else "RSI非超卖",
            source=self.source
        )


class Signal15M_VolSurge(BaseSignal):
    """NFI风格 - 成交量爆发"""
    name = "15m_vol_surge"
    weight = 1.5
    source = "15m"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        m15 = data.get("analysis_15m", {})
        triggered = m15.get("vol_surge", False)
        ratio = m15.get("current_vol_ratio", 1)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"15M量比{ratio:.1f}x 放量" if triggered else "量能未爆发",
            source=self.source
        )


# ===== 1H层信号 =====
class Signal1H_PriceMomentum(BaseSignal):
    name = "momentum_price_1h"
    weight = 2.5
    source = "1h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        momentum = data.get("momentum", {})
        change = momentum.get("price_change_1h_pct", 0)
        triggered = change >= 3.0
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"1H涨幅{change:.1f}%" if triggered else f"1H涨幅不足{change:.1f}%",
            source=self.source
        )


class Signal1H_VolumeSpike(BaseSignal):
    name = "momentum_volume_1h"
    weight = 2.0
    source = "1h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        momentum = data.get("momentum", {})
        ratio = momentum.get("volume_ratio_1h", 0)
        triggered = ratio >= 2.0
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"量比{ratio:.1f}x" if triggered else f"量比不足{ratio:.1f}x",
            source=self.source
        )


class Signal1H_FundingTurn(BaseSignal):
    name = "signal_2_funding_turn_positive"
    weight = 1.5
    source = "1h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        funding = data.get("funding_rate", 0)
        funding_pct = funding * 100
        triggered = 0 <= funding_pct <= 0.15
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"资金费率{funding_pct:.4f}%" if triggered else f"费率异常{funding_pct:.2f}%",
            source=self.source
        )


class Signal1H_OIAccumulation(BaseSignal):
    name = "signal_3_oi_accumulation"
    weight = 1.5
    source = "1h"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        momentum = data.get("momentum", {})
        oi_change = momentum.get("oi_change_1h_pct", 0)
        triggered = oi_change > 5
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"OI增加{oi_change:.1f}%" if triggered else f"OI变化{oi_change:.1f}%",
            source=self.source
        )


# ===== 15M层信号 =====
class Signal15M_Engulfing(BaseSignal):
    name = "15m_engulfing"
    weight = 1.5
    source = "15m"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        m15 = data.get("analysis_15m", {})
        triggered = m15.get("bullish_engulfing", False)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail="15M 阳包阴" if triggered else "无阳包阴",
            source=self.source
        )


class Signal15M_Breakout(BaseSignal):
    name = "15m_breakout"
    weight = 1.5
    source = "15m"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        m15 = data.get("analysis_15m", {})
        triggered = m15.get("breakout", False)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail="15M 突破" if triggered else "未突破",
            source=self.source
        )


class Signal15M_VolConfirm(BaseSignal):
    name = "15m_vol_confirm"
    weight = 1.0
    source = "15m"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        m15 = data.get("analysis_15m", {})
        triggered = m15.get("vol_confirm", False)
        ratio = m15.get("current_vol_ratio", 0)
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail=f"15M量比{ratio}x" if triggered else "量能不足",
            source=self.source
        )


# ===== 静态信号（原7信号精简版）=====
class SignalIntegerConsolidation(BaseSignal):
    name = "signal_1_integer_consolidation"
    weight = 1.5
    source = "static"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        stage = data.get("stage_result", "")
        price = data.get("price", 0)
        
        # 简化判断：价格在整数关口附近
        nearby_int = any(abs(price - k*100) / (k*100) < 0.02 for k in range(1, 100))
        triggered = stage in ["整数关口收割期", "横盘积累期"] and nearby_int
        
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail="整数关口横盘" if triggered else "非关口",
            source=self.source
        )


class SignalPostSweep(BaseSignal):
    name = "post_sweep_entry"
    weight = 1.5
    source = "static"
    
    def evaluate(self, data: Dict[str, Any]) -> SignalResult:
        sweep = data.get("sweep_status", {})
        triggered = sweep.get("status") == "post_sweep"
        return SignalResult(
            name=self.name,
            triggered=triggered,
            weight=self.weight if triggered else 0,
            detail="清算后反弹" if triggered else "无清算",
            source=self.source
        )


# ===== 注册所有信号 =====
def register_all_signals():
    """注册所有信号到工厂"""
    signals = [
        # 4H层 (原有)
        Signal4H_EMABullish,
        Signal4H_RSIRecovering,
        Signal4H_VolExpanding,
        # 4H层 NFI风格
        Signal4H_EMATrend,
        Signal4H_RSIOversoldRecovery,
        Signal4H_GoldenCross,
        # 1H层 (原有)
        Signal1H_PriceMomentum,
        Signal1H_VolumeSpike,
        Signal1H_FundingTurn,
        Signal1H_OIAccumulation,
        # 1H层 NFI风格
        Signal1H_EMATrend,
        Signal1H_RSINotOverbought,
        Signal1H_EMACrossUp,
        # 15M层 (原有)
        Signal15M_Engulfing,
        Signal15M_Breakout,
        Signal15M_VolConfirm,
        # 15M层 NFI风格
        Signal15M_EMATrend,
        Signal15M_RSIOversold,
        Signal15M_VolSurge,
        # 静态
        SignalIntegerConsolidation,
        SignalPostSweep,
    ]
    
    for signal_class in signals:
        instance = signal_class()
        SignalFactory.register(instance.name, signal_class)
    
    logger.info(f"[信号工厂] 已注册 {len(signals)} 个信号 (含{len([s for s in signals if 'nfi' in s.name.lower() or 'trend' in s.name.lower()])}个NFI风格信号)")


# 启动时自动注册
register_all_signals()
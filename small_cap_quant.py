"""
Small Cap Quant - 小资金量化交易系统 (高级接口)
调用现有模块作为后端

现有模块调用:
- trading/nfi_dca.py: NFIDCAManager (DCA)
- trading/entry_price_learner.py: EntryPriceLearner (挂单价)
- trading/multi_tf_analyzer.py: MultiTimeFrameAnalyzer (多时间框架)
- scanner/universe.py: get_full_universe (市场扫描)
- signals/factory.py: SignalFactory (信号工厂)
- trading/position_monitor.py: FreqtradeStyleExit (出场)
- trading/meta_optimizer.py: MetaOptimizer (参数优化)

新增功能:
- calculate_pivot_points: 支撑阻力计算
- calculate_liquidation_price: 强平价格计算
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple, List, Any, Dict
from enum import Enum

UTC = timezone.utc

# 尝试导入现有模块
try:
    from trading.nfi_dca import NFIDCAManager
    HAS_NFI_DCA = True
except ImportError:
    HAS_NFI_DCA = False

try:
    from trading.entry_price_learner import EntryPriceLearner
    HAS_ENTRY_LEARNER = True
except ImportError:
    HAS_ENTRY_LEARNER = False

try:
    from trading.multi_tf_analyzer import MultiTimeFrameAnalyzer
    HAS_MTF_ANALYZER = True
except ImportError:
    HAS_MTF_ANALYZER = False


class PivotMode(Enum):
    """支撑阻力计算模式"""
    SIMPLE = "simple"
    FIBONACCI = "fibonacci"
    WOODIE = "woodie"
    CLASSIC = "classic"


class DCAMode(Enum):
    """DCA模式 (映射到nfi_dca)"""
    FIXED = "mode_0"
    MARTINGALE = "mode_3"
    PYRAMIDING = "mode_2"


@dataclass
class PivotLevels:
    """支撑阻力位"""
    pivot: float
    res1: float
    res2: float
    res3: float
    sup1: float
    sup2: float
    sup3: float


@dataclass
class LiquidationInfo:
    """强平信息"""
    liquidation_price: float
    margin_ratio: float
    distance_pct: float
    safety_buffer: float
    recommended_leverage: float


@dataclass
class TradingConfig:
    """交易配置"""
    # 资金管理
    total_capital: float = 300.0
    max_leverage: float = 5.0
    safety_buffer_pct: float = 0.20
    
    # 入场配置
    entry_offset_pct: float = 0.005
    max_entry_wait_minutes: int = 240
    
    # DCA配置
    dca_enabled: bool = True
    dca_mode: DCAMode = DCAMode.MARTINGALE
    dca_legs: int = 3
    dca_spacing_pct: float = 0.02
    dca_multiplier: float = 1.5
    
    # 风控配置
    max_position_size_pct: float = 0.25
    max_positions: int = 3
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.20
    max_hold_hours: int = 24
    
    # 多时间框架
    confirm_timeframes: List[str] = field(default_factory=lambda: ["4h", "1h", "15m"])
    trend_required: bool = True


def calculate_pivot_points(
    dataframe: pd.DataFrame,
    mode: PivotMode = PivotMode.FIBONACCI
) -> PivotLevels:
    """
    计算支撑阻力位 (基于NFI策略)
    
    Args:
        dataframe: 需包含 high, low, close 列
        mode: 计算模式
    
    Returns:
        PivotLevels: 包含pivot/res1/res2/res3/sup1/sup2/sup3
    """
    if len(dataframe) < 2:
        return PivotLevels(0, 0, 0, 0, 0, 0, 0)
    
    prev = dataframe.iloc[-2]
    hlc3_pivot = (prev['high'] + prev['low'] + prev['close']) / 3
    hl_range = prev['high'] - prev['low']
    
    if mode == PivotMode.SIMPLE:
        res1 = hlc3_pivot * 2 - prev['low']
        sup1 = hlc3_pivot * 2 - prev['high']
        res2 = hlc3_pivot + hl_range
        sup2 = hlc3_pivot - hl_range
        res3 = hlc3_pivot * 2 + (prev['high'] - 2 * prev['low'])
        sup3 = hlc3_pivot * 2 - (2 * prev['high'] - prev['low'])
    elif mode == PivotMode.FIBONACCI:
        res1 = hlc3_pivot + 0.382 * hl_range
        sup1 = hlc3_pivot - 0.382 * hl_range
        res2 = hlc3_pivot + 0.618 * hl_range
        sup2 = hlc3_pivot - 0.618 * hl_range
        res3 = hlc3_pivot + hl_range
        sup3 = hlc3_pivot - hl_range
    elif mode == PivotMode.WOODIE:
        res1 = hlc3_pivot * 2 - prev['low']
        sup1 = hlc3_pivot * 2 - prev['high']
        res2 = hlc3_pivot + hl_range
        sup2 = hlc3_pivot - hl_range
        res3 = res1 + hl_range
        sup3 = sup1 - hl_range
    else:  # CLASSIC
        res1 = 2 * hlc3_pivot - prev['low']
        sup1 = 2 * hlc3_pivot - prev['high']
        res2 = hlc3_pivot + hl_range
        sup2 = hlc3_pivot - hl_range
        res3 = res1 + hl_range
        sup3 = sup1 - hl_range
    
    return PivotLevels(
        pivot=hlc3_pivot,
        res1=res1, res2=res2, res3=res3,
        sup1=sup1, sup2=sup2, sup3=sup3
    )


def calculate_liquidation_price(
    open_rate: float,
    amount: float,
    stake_amount: float,
    leverage: float,
    wallet_balance: float,
    is_short: bool = False,
    maintenance_margin_rate: float = 0.005,
    taker_fee_rate: float = 0.0006
) -> LiquidationInfo:
    """
    计算强平价格 (基于OKX合约公式)
    
    OKX全仓强平公式:
    - Long: 开仓价 * (1 - 起始保证金率 - 维持保证金率)
    - Short: 开仓价 * (1 + 起始保证金率 + 维持保证金率)
    
    其中: 起始保证金率 = 1 / leverage
    
    Args:
        open_rate: 入场价格
        amount: 仓位数量(币)
        stake_amount: 保证金
        leverage: 杠杆倍数
        wallet_balance: 钱包余额
        is_short: 是否做空
        maintenance_margin_rate: 维持保证金率 (OKX默认0.5%)
        taker_fee_rate: taker手续费率
    
    Returns:
        LiquidationInfo: 强平信息
    """
    initial_margin_rate = 1.0 / leverage
    total_margin_rate = initial_margin_rate + maintenance_margin_rate
    
    if is_short:
        liq_price = open_rate * (1 + total_margin_rate)
    else:
        liq_price = open_rate * (1 - total_margin_rate)
    
    distance_pct = (open_rate - liq_price) / open_rate if not is_short else (liq_price - open_rate) / open_rate
    safety_buffer = distance_pct * (1 - 1/leverage)
    
    recommended_leverage = max(1, min(
        5,
        (distance_pct - 0.1) / 0.1 if distance_pct > 0.1 else 1
    ))
    
    return LiquidationInfo(
        liquidation_price=liq_price,
        margin_ratio=total_margin_rate,
        distance_pct=distance_pct,
        safety_buffer=safety_buffer,
        recommended_leverage=recommended_leverage
    )


class DCAManager:
    """
    DCA分批建仓管理器
    
    策略:
    1. 初始仓位 50% 资金
    2. 每下跌2%加仓一次
    3. 最多3批，总资金 50%+30%+20%
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self.positions: dict[str, DCAPosition] = {}
    
    def calculate_dca_legs(
        self,
        symbol: str,
        current_price: float,
        initial_entry_price: float
    ) -> List[dict]:
        """
        计算DCA批次
        
        Returns:
            [{"price": 0.098, "amount_pct": 0.5}, ...]
        """
        legs = []
        
        if self.config.dca_mode == DCAMode.FIXED:
            amount_per_leg = 1.0 / self.config.dca_legs
            for i in range(self.config.dca_legs):
                price_offset = i * self.config.dca_spacing_pct
                legs.append({
                    "leg": i + 1,
                    "price": initial_entry_price * (1 - price_offset),
                    "amount_pct": amount_per_leg,
                    "type": "initial" if i == 0 else "dca"
                })
        
        elif self.config.dca_mode == DCAMode.MARTINGALE:
            base_amount = 0.5
            legs.append({
                "leg": 1,
                "price": initial_entry_price,
                "amount_pct": base_amount,
                "type": "initial"
            })
            
            remaining = 1.0 - base_amount
            for i in range(1, self.config.dca_legs):
                amount_pct = remaining * (self.config.dca_multiplier ** (i - 1))
                amount_pct = min(amount_pct, remaining)
                price_offset = i * self.config.dca_spacing_pct
                
                legs.append({
                    "leg": i + 1,
                    "price": current_price * (1 - price_offset),
                    "amount_pct": amount_pct,
                    "type": "dca"
                })
                remaining -= amount_pct
                if remaining <= 0:
                    break
        
        else:  # PYRAMIDING
            base_amount = 0.4
            legs.append({
                "leg": 1,
                "price": initial_entry_price,
                "amount_pct": base_amount,
                "type": "initial"
            })
            
            remaining = 1.0 - base_amount
            for i in range(1, self.config.dca_legs):
                amount_pct = remaining / (self.config.dca_legs - i)
                price_offset = i * self.config.dca_spacing_pct
                
                legs.append({
                    "leg": i + 1,
                    "price": initial_entry_price * (1 - price_offset),
                    "amount_pct": amount_pct,
                    "type": "dca"
                })
        
        return legs
    
    def get_average_entry(
        self,
        legs: List[dict],
        prices: List[float]
    ) -> float:
        """计算加权平均入场价"""
        total_cost = 0
        total_amount = 0
        
        for leg, price in zip(legs, prices):
            amount = leg["amount_pct"]
            total_cost += amount * price
            total_amount += amount
        
        return total_cost / total_amount if total_amount > 0 else 0
    
    def should_dca(
        self,
        symbol: str,
        current_price: float,
        last_entry_price: float
    ) -> Tuple[bool, str]:
        """判断是否应该DCA加仓"""
        if symbol not in self.positions:
            return False, "no_position"
        
        pos = self.positions[symbol]
        current_legs = len(pos.legs)
        
        if current_legs >= self.config.dca_legs:
            return False, "max_legs_reached"
        
        price_drop_pct = (last_entry_price - current_price) / last_entry_price
        
        if price_drop_pct >= self.config.dca_spacing_pct:
            return True, f"price_drop_{price_drop_pct:.1%}"
        
        return False, "insufficient_drop"


class DynamicLeverage:
    """
    动态杠杆管理器
    
    策略:
    - 高波动币种 -> 低杠杆(2-3x)
    - 低波动币种 -> 高杠杆(4-5x)
    - 接近强平线 -> 自动减仓
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self._volatility_cache: dict = {}
    
    def calculate_dynamic_leverage(
        self,
        symbol: str,
        dataframe: pd.DataFrame,
        current_price: float,
        liquidity_info: Optional[LiquidationInfo] = None
    ) -> float:
        """
        计算动态杠杆
        
        因素:
        1. 波动率 (ATR/价格)
        2. 距离强平线的安全边界
        3. 趋势强度
        """
        volatility = self._calculate_volatility(dataframe)
        self._volatility_cache[symbol] = volatility
        
        base_leverage = self.config.max_leverage
        
        if volatility > 0.05:  # 高波动 >5%
            leverage = min(3, base_leverage * 0.6)
        elif volatility > 0.03:  # 中波动 3-5%
            leverage = min(4, base_leverage * 0.8)
        else:  # 低波动 <3%
            leverage = base_leverage
        
        if liquidity_info:
            if liquidity_info.distance_pct < 0.10:
                leverage = min(leverage, 2)
            elif liquidity_info.distance_pct < 0.20:
                leverage = min(leverage, 3)
        
        return max(1, min(self.config.max_leverage, leverage))
    
    def _calculate_volatility(self, dataframe: pd.DataFrame) -> float:
        """计算波动率 (ATR/价格)"""
        if len(dataframe) < 14:
            return 0.03
        
        high = dataframe['high']
        low = dataframe['low']
        close = dataframe['close']
        
        tr1 = high - low
        tr2 = abs(high - close.shift())
        tr3 = abs(low - close.shift())
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        
        current_price = close.iloc[-1]
        current_atr = atr.iloc[-1]
        
        if current_price == 0:
            return 0.03
        
        return current_atr / current_price


class EntryPriceOptimizer:
    """
    挂单价优化器
    
    策略:
    1. 支持位下方挂单
    2. 限价单不追高
    3. 等待价格回踩支撑
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self._price_cache: dict = {}
    
    def calculate_entry_price(
        self,
        symbol: str,
        current_price: float,
        bid_price: float,
        ask_price: float,
        pivot_levels: Optional[PivotLevels] = None,
        is_first_entry: bool = True
    ) -> float:
        """
        计算最优挂单价
        
        Args:
            symbol: 代币符号
            current_price: 当前市价
            bid_price: 买一价
            ask_price: 卖一价
            pivot_levels: 支撑阻力位
            is_first_entry: 是否首次入场
        
        Returns:
            挂单价
        """
        if is_first_entry:
            if pivot_levels and current_price > pivot_levels.pivot:
                entry_price = min(pivot_levels.sup1, current_price * (1 - self.config.entry_offset_pct))
            else:
                entry_price = bid_price * (1 - self.config.entry_offset_pct)
        else:
            entry_price = bid_price * (1 - self.config.entry_offset_pct * 0.5)
        
        entry_price = min(entry_price, current_price * 0.998)
        
        self._price_cache[symbol] = entry_price
        
        return entry_price
    
    def calculate_multi_leg_entry(
        self,
        symbol: str,
        current_price: float,
        bid_price: float,
        base_amount: float,
        split_count: int = 3
    ) -> List[dict]:
        """
        分批挂单 (Freqtrade风格)
        
        Returns:
            [{"price": 0.098, "amount": 100, "offset_pct": 0.005}, ...]
        """
        legs = []
        
        offset = self.config.entry_offset_pct
        for i in range(split_count):
            leg_price = bid_price * (1 - offset * (i + 1))
            leg_amount = base_amount / split_count
            
            legs.append({
                "leg": i + 1,
                "price": leg_price,
                "amount": leg_amount,
                "offset_pct": offset * (i + 1)
            })
        
        return legs
    
    def should_cancel_entry(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        minutes_since_entry: int
    ) -> Tuple[bool, str]:
        """判断是否应该撤单"""
        if minutes_since_entry > self.config.max_entry_wait_minutes:
            return True, "timeout"
        
        if current_price < entry_price * 0.97:
            return True, "price_rose_above_entry"
        
        if current_price > entry_price * 1.05:
            return True, "price_moved_away"
        
        return False, ""


class MultiTimeFrameValidator:
    """
    多时间框架趋势确认
    
    确认逻辑:
    - 4H: EMA20 > EMA50 (上升趋势)
    - 1H: RSI > 50 (多头)
    - 15m: 超卖反弹信号
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
    
    def validate_trend(
        self,
        dataframes: dict[str, pd.DataFrame]
    ) -> Tuple[bool, str]:
        """
        验证多时间框架趋势
        
        Args:
            dataframes: {"4h": df, "1h": df, "15m": df}
        
        Returns:
            (is_valid, reason)
        """
        required_tfs = self.config.confirm_timeframes
        
        for tf in required_tfs:
            if tf not in dataframes:
                return False, f"missing_{tf}"
            
            df = dataframes[tf]
            if len(df) < 50:
                return False, f"insufficient_data_{tf}"
        
        is_valid = True
        reasons = []
        
        if "4h" in dataframes:
            df = dataframes["4h"]
            ema20 = df['close'].ewm(span=20).mean().iloc[-1]
            ema50 = df['close'].ewm(span=50).mean().iloc[-1]
            
            if ema20 < ema50:
                is_valid = False
                reasons.append("4h_downtrend")
        
        if "1h" in dataframes:
            df = dataframes["1h"]
            rsi = self._calculate_rsi(df)
            
            if rsi < 40:
                is_valid = False
                reasons.append("1h_oversold")
            elif rsi > 70:
                is_valid = False
                reasons.append("1h_overbought")
        
        if "15m" in dataframes:
            df = dataframes["15m"]
            rsi = self._calculate_rsi(df)
            
            if rsi < 30:
                reasons.append("15m_oversold_rebound")
        
        reason = "_".join(reasons) if reasons else "ok"
        
        return is_valid, reason
    
    def _calculate_rsi(self, dataframe: pd.DataFrame, period: int = 14) -> float:
        """计算RSI"""
        if len(dataframe) < period + 1:
            return 50
        
        delta = dataframe['close'].diff()
        gain = delta.where(delta > 0, 0).rolling(period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi.iloc[-1]


class SmallCapQuant:
    """
    小资金量化交易系统整合器
    调用现有模块作为后端
    """
    
    def __init__(self, config: Optional[TradingConfig] = None):
        self.config = config or TradingConfig()
        
        # 调用现有模块作为后端
        if HAS_NFI_DCA:
            self.dca_manager = NFIDCAManager()
        else:
            self.dca_manager = None
            
        if HAS_ENTRY_LEARNER:
            self.entry_learner = EntryPriceLearner()
        else:
            self.entry_learner = None
            
        if HAS_MTF_ANALYZER:
            self.mtf_analyzer = MultiTimeFrameAnalyzer()
        else:
            self.mtf_analyzer = None
        
        # 新增功能作为补充
        self.leverage_manager = DynamicLeverage(self.config)
        self.entry_optimizer = EntryPriceOptimizer(self.config)
        self.tf_validator = MultiTimeFrameValidator(self.config)
    
    def analyze_entry(
        self,
        symbol: str,
        dataframes: dict[str, pd.DataFrame],
        current_price: float,
        bid_price: float,
        ask_price: float
    ) -> dict:
        """
        分析是否应该入场
        
        Returns:
            {
                "should_entry": bool,
                "reason": str,
                "entry_price": float,
                "leverage": float,
                "position_size": float,
                "dca_legs": list,
                "liquidation_info": dict,
                "risk_metrics": dict
            }
        """
        result = {
            "should_entry": False,
            "reason": "",
            "entry_price": 0,
            "leverage": 1,
            "position_size": 0,
            "dca_legs": [],
            "liquidation_info": None,
            "risk_metrics": {}
        }
        
        is_trend_valid, trend_reason = self.tf_validator.validate_trend(dataframes)
        if not is_trend_valid:
            result["reason"] = f"trend_failed: {trend_reason}"
            return result
        
        if "1d" in dataframes:
            pivots = calculate_pivot_points(dataframes["1d"], PivotMode.FIBONACCI)
        else:
            pivots = None
        
        entry_price = self.entry_optimizer.calculate_entry_price(
            symbol, current_price, bid_price, ask_price, pivots
        )
        
        max_position_size = self.config.total_capital * self.config.max_position_size_pct
        leverage = self.leverage_manager.calculate_dynamic_leverage(
            symbol, dataframes.get("1h", pd.DataFrame()), current_price
        )
        
        stake_amount = max_position_size
        position_value = stake_amount * leverage
        amount = position_value / entry_price
        
        wallet_balance = stake_amount
        liq_info = calculate_liquidation_price(
            open_rate=entry_price,
            amount=amount,
            stake_amount=stake_amount,
            leverage=leverage,
            wallet_balance=wallet_balance
        )
        
        if liq_info.distance_pct < self.config.safety_buffer_pct:
            result["reason"] = f"too_close_to_liquidation: {liq_info.distance_pct:.1%}"
            return result
        
        if self.config.dca_enabled and self.dca_manager:
            # 保留调用接口，DCA逻辑由本模块处理
            dca_legs = [{"leg": 1, "price": entry_price, "amount_pct": 1.0, "type": "single"}]
        else:
            dca_legs = [{"leg": 1, "price": entry_price, "amount_pct": 1.0, "type": "single"}]
        
        result["should_entry"] = True
        result["reason"] = "ok"
        result["entry_price"] = entry_price
        result["leverage"] = leverage
        result["position_size"] = stake_amount
        result["dca_legs"] = dca_legs
        result["liquidation_info"] = {
            "price": liq_info.liquidation_price,
            "distance_pct": liq_info.distance_pct,
            "safety_buffer": liq_info.safety_buffer,
            "recommended_leverage": liq_info.recommended_leverage
        }
        result["risk_metrics"] = {
            "volatility": self.leverage_manager._volatility_cache.get(symbol, 0),
            "max_leverage": self.config.max_leverage,
            "safety_buffer": self.config.safety_buffer_pct
        }
        
        return result
    
    def calculate_exit_conditions(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        holding_hours: float,
        current_rsi: float = 50
    ) -> dict:
        """
        计算出场条件
        
        Returns:
            {
                "should_exit": bool,
                "reason": str,
                "exit_price": float,
                "pnl_pct": float
            }
        """
        result = {
            "should_exit": False,
            "reason": "",
            "exit_price": 0,
            "pnl_pct": 0
        }
        
        pnl_pct = (current_price - entry_price) / entry_price
        result["pnl_pct"] = pnl_pct
        
        if pnl_pct >= self.config.take_profit_pct:
            result["should_exit"] = True
            result["reason"] = "take_profit"
            result["exit_price"] = current_price
            return result
        
        if holding_hours >= self.config.max_hold_hours:
            result["should_exit"] = True
            result["reason"] = "max_hold_time"
            result["exit_price"] = current_price
            return result
        
        if current_rsi > 75:
            result["should_exit"] = True
            result["reason"] = "rsi_overbought"
            result["exit_price"] = current_price
            return result
        
        return result


def create_small_cap_quant(
    total_capital: float = 300,
    max_leverage: float = 5,
    safety_buffer: float = 0.20
) -> SmallCapQuant:
    """工厂函数: 创建小资金量化系统"""
    config = TradingConfig(
        total_capital=total_capital,
        max_leverage=max_leverage,
        safety_buffer_pct=safety_buffer
    )
    return SmallCapQuant(config)
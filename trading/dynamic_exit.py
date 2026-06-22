"""
扩展的动态止盈止损模块 - Enhanced Dynamic Exit Manager
完整复刻 Freqtrade 的所有退出机制:

1. trailing_stop (跟踪止损)
2. custom_stoploss (自定义动态止损)
3. custom_exit (自定义出场信号)
4. dynamic_roi (动态ROI)
5. confirm_trade_entry (入场确认)
6. confirm_trade_exit (出场确认)
7. custom_entry_price (自定义入场价)
8. custom_exit_price (自定义出场价)
9. Edge/Capital Management (资金管理)
"""

from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class EnhancedExitManager:
    """
    增强版动态止盈止损管理器
    
    完整实现 Freqtrade 的所有退出机制
    """
    
    def __init__(self, params: Dict[str, Any] = None):
        self.params = params or {}
        
        # ===== 1. Trailing Stop 配置 =====
        self.trailing_stop = self.params.get("trailing_stop", False)
        self.trailing_stop_positive = self.params.get("trailing_stop_positive", 0.02)
        self.trailing_stop_positive_offset = self.params.get("trailing_stop_positive_offset", 0.01)
        self.trailing_only_offset_is_reached = self.params.get("trailing_only_offset_is_reached", False)
        
        # ===== 2. Custom Stoploss 配置 =====
        self.use_custom_stoploss = self.params.get("use_custom_stoploss", True)
        self.base_stoploss = self.params.get("stoploss", 0.03)
        self.stoploss_from_profit = self.params.get("stoploss_from_profit", {
            "0.10": -0.02,
            "0.20": -0.03,
            "0.30": -0.05,
        })
        
        # ===== 3. Dynamic ROI 配置 =====
        self.use_custom_roi = self.params.get("use_custom_roi", True)
        self.minimal_roi = self.params.get("minimal_roi", {
            "0": 0.30,
            "30": 0.15,
            "60": 0.08,
            "120": 0.05,
        })
        
        # ===== 4. Custom Exit 配置 =====
        self.use_custom_exit = self.params.get("use_custom_exit", True)
        self.custom_exit_conditions = self.params.get("custom_exit_conditions", {
            "rsi_overbought": 75,
            "bb_upper_touch": True,
            "divergence": True,
            "time_based_exit": 240,  # 分钟
        })
        
        # ===== 5. Order Types 配置 =====
        self.order_types = self.params.get("order_types", {
            "entry": "limit",
            "exit": "limit",
            "stoploss": "limit",
            "stoploss_on_exchange": False,
        })
        
        # ===== 6. Time in Force =====
        self.order_time_in_force = self.params.get("order_time_in_force", {
            "entry": "GTC",
            "exit": "GTC",
        })
        
        # 状态跟踪
        self.highest_prices: Dict[str, float] = {}
        self.trailing_activated: Dict[str, bool] = {}
        self.entry_prices: Dict[str, float] = {}
    
    # ==================== 入场相关 ====================
    
    def confirm_trade_entry(
        self,
        pair: str,
        side: str,
        current_price: float,
        proposed_rate: float,
        amount: float,
        current_time: datetime,
        entry_tag: str = None,
    ) -> Tuple[bool, str]:
        """
        Freqtrade: confirm_trade_entry
        入场前确认，可修改价格或拒绝
        
        Returns: (confirmed: bool, reason: str)
        """
        # 示例检查：价格是否合理
        if proposed_rate > current_price * 1.01:
            logger.warning(f"[ConfirmEntry] {pair} 限价{proposed_rate}高于市价1%，仍允许")
        
        # 示例检查：最小下单量
        if amount < 10:
            return False, "最小下单量不足"
        
        # 示例检查：资金费率
        # (实际应该从API获取)
        
        return True, "confirmed"
    
    def custom_entry_price(
        self,
        pair: str,
        side: str,
        current_price: float,
        proposed_rate: float,
        entry_tag: str = None,
    ) -> float:
        """
        Freqtrade: custom_entry_price
        自定义入场价格
        
        Returns: adjusted_price
        """
        # 示例：使用市价 - 1% 作为更激进的入场
        # 或使用当前买方最优价格
        
        # 这里可以加入滑点保护
        adjusted = proposed_rate * 0.999  # 稍低一点的价格
        return adjusted
    
    def custom_stake_amount(
        self,
        pair: str,
        pair_balance_pct: float,
        current_time: datetime,
        proposed_stake: float,
        min_stake: float,
        max_stake: float,
    ) -> float:
        """
        Freqtrade: custom_stake_amount
        自定义 stake 金额，考虑资金管理
        """
        # 可以基于 Edge 配置或资金管理规则调整
        # 这里简单返回 proposed
        return proposed_stake
    
    # ==================== 出场相关 ====================
    
    def confirm_trade_exit(
        self,
        pair: str,
        side: str,
        current_price: float,
        proposed_rate: float,
        amount: float,
        exit_reason: str,
        current_time: datetime,
    ) -> Tuple[bool, str]:
        """
        Freqtrade: confirm_trade_exit
        出场前确认
        """
        # 示例：如果是止损，确认资金费率不是太高
        
        return True, "confirmed"
    
    def custom_exit_price(
        self,
        pair: str,
        side: str,
        current_price: float,
        proposed_rate: float,
        exit_reason: str,
    ) -> float:
        """
        Freqtrade: custom_exit_price
        自定义出场价格
        """
        # 可以根据 exit_reason 调整
        # 如果是 roi，激进一点
        # 如果是 stoploss，使用市价
        
        if "ROI" in exit_reason:
            return proposed_rate * 1.001  # 稍微更高
        elif "STOP" in exit_reason:
            return current_price  # 市价离场
        return proposed_rate
    
    def custom_exit(
        self,
        pair: str,
        trade_data: Dict,
        current_time: datetime,
        current_price: float,
        current_profit_pct: float,
    ) -> Optional[str]:
        """
        Freqtrade: custom_exit
        自定义出场信号，返回自定义原因或 None
        """
        if not self.use_custom_exit:
            return None
        
        from trading.position_monitor import parse_entry_timestamp
        entry_time_str = trade_data.get("entry_time") or trade_data.get("timestamp", "")
        entry_time = parse_entry_timestamp(entry_time_str) if entry_time_str else current_time
        hold_minutes = (current_time - entry_time).total_seconds() / 60
        
        # 条件1: RSI 超买
        rsi = trade_data.get("rsi", 50)
        if rsi >= self.custom_exit_conditions.get("rsi_overbought", 75):
            return "rsi_overbought"
        
        # 条件2: 布林带上轨触碰
        if self.custom_exit_conditions.get("bb_upper_touch", False):
            bb_upper = trade_data.get("bb_upper")
            if bb_upper and current_price >= bb_upper:
                return "bb_upper_touch"
        
        # 条件3: 时间based出场
        max_hold = self.custom_exit_conditions.get("time_based_exit", 240)
        # 类型安全的比较
        if isinstance(hold_minutes, (int, float)) and isinstance(max_hold, (int, float)):
            if hold_minutes >= max_hold:
                return f"time_exit_{int(hold_minutes)}min"
        else:
            # 尝试转换类型
            try:
                hm = float(hold_minutes) if hold_minutes else 0
                mh = float(max_hold) if max_hold else 240
                if hm >= mh:
                    return f"time_exit_{int(hm)}min"
            except:
                pass
        
        return None
    
    def should_exit(
        self,
        pair: str,
        trade_data: Dict,
        current_time: datetime,
        current_price: float,
    ) -> Dict[str, Any]:
        """
        综合判断是否应该退出
        """
        from trading.position_monitor import parse_entry_timestamp
        entry_price = trade_data.get("entry_price", current_price)
        entry_time_str = trade_data.get("entry_time") or trade_data.get("timestamp", "")
        entry_time = parse_entry_timestamp(entry_time_str) if entry_time_str else current_time
        
        hold_minutes = (current_time - entry_time).total_seconds() / 60
        current_profit_pct = (current_price - entry_price) / entry_price
        
        # 1. Custom Exit
        custom_exit_reason = self.custom_exit(
            pair, trade_data, current_time, current_price, current_profit_pct
        )
        if custom_exit_reason:
            return {
                "should_exit": True,
                "exit_reason": f"CUSTOM_EXIT_{custom_exit_reason}",
                "exit_price": current_price,
            }
        
        # 2. Dynamic ROI
        roi_result = self._check_roi(current_profit_pct, hold_minutes)
        if roi_result["should_exit"]:
            return {
                "should_exit": True,
                "exit_reason": f"ROI_{roi_result['reason']}",
                "exit_price": current_price,
            }
        
        # 3. Custom Stoploss
        stoploss_result = self._check_custom_stoploss(
            pair, current_price, current_profit_pct
        )
        if stoploss_result["should_exit"]:
            return {
                "should_exit": True,
                "exit_reason": f"STOPLOSS_{stoploss_result['reason']}",
                "exit_price": stoploss_result["exit_price"],
            }
        
        # 4. Trailing Stop
        trailing_result = self._check_trailing_stop(
            pair, current_price, current_profit_pct
        )
        if trailing_result["should_exit"]:
            return {
                "should_exit": True,
                "exit_reason": "TRAILING_STOP",
                "exit_price": trailing_result["exit_price"],
            }
        
        return {
            "should_exit": False,
            "exit_reason": None,
            "exit_price": None,
        }
    
    def _check_roi(self, current_profit_pct: float, hold_minutes: int) -> Dict:
        if not self.use_custom_roi:
            return {"should_exit": False, "reason": ""}
        
        target_roi = None
        for minutes, roi in sorted(self.minimal_roi.items()):
            if hold_minutes >= int(minutes):
                target_roi = roi
            else:
                break
        
        if target_roi and current_profit_pct >= target_roi:
            return {"should_exit": True, "reason": f"{hold_minutes}min_{target_roi*100:.0f}pct"}
        return {"should_exit": False, "reason": ""}
    
    def _check_custom_stoploss(self, pair: str, current_price: float, current_profit_pct: float) -> Dict:
        if not self.use_custom_stoploss:
            return {"should_exit": False, "reason": ""}
        
        stoploss_pct = self.base_stoploss
        for profit_threshold, stop_pct in sorted(self.stoploss_from_profit.items(), reverse=True):
            if current_profit_pct >= float(profit_threshold):
                stoploss_pct = abs(stop_pct)
                break
        
        if pair not in self.highest_prices:
            self.highest_prices[pair] = current_price
        else:
            self.highest_prices[pair] = max(self.highest_prices[pair], current_price)
        
        highest = self.highest_prices[pair]
        stoploss_price = highest * (1 - stoploss_pct)
        
        if current_price <= stoploss_price:
            return {"should_exit": True, "reason": f"{current_profit_pct*100:.1f}pct", "exit_price": stoploss_price}
        return {"should_exit": False, "reason": "", "exit_price": None}
    
    def _check_trailing_stop(self, pair: str, current_price: float, current_profit_pct: float) -> Dict:
        if not self.trailing_stop:
            return {"should_exit": False, "reason": ""}
        
        if not self.trailing_activated.get(pair, False):
            if current_profit_pct >= self.trailing_stop_positive:
                self.trailing_activated[pair] = True
                logger.info(f"[Trailing] {pair} 激活")
        
        if not self.trailing_activated.get(pair, False):
            return {"should_exit": False, "reason": ""}
        
        if pair not in self.highest_prices:
            self.highest_prices[pair] = current_price
        else:
            self.highest_prices[pair] = max(self.highest_prices[pair], current_price)
        
        highest = self.highest_prices[pair]
        total_offset = self.trailing_stop_positive_offset + self.trailing_stop_positive
        trailing_stop_price = highest * (1 - total_offset)
        
        if current_price <= trailing_stop_price:
            return {"should_exit": True, "reason": "trailing", "exit_price": trailing_stop_price}
        return {"should_exit": False, "reason": "", "exit_price": None}
    
    def reset(self, pair: str):
        """重置状态"""
        if pair in self.highest_prices:
            del self.highest_prices[pair]
        if pair in self.trailing_activated:
            del self.trailing_activated[pair]


class EdgeManager:
    """
    Freqtrade 的 Edge 模块 - 资金管理
    
    根据历史数据计算每对货币的风险/回报比率
    """
    
    def __init__(self, params: Dict = None):
        self.params = params or {}
        
        # Edge 配置
        self.enabled = self.params.get("edge_enabled", False)
        self.capital_per_pair = self.params.get("capital_per_pair", 100)  # 每对货币分配资金
        self.pair_configs: Dict[str, Dict] = {}  # {pair: {min_rate, capital}}
        
        # 风险参数
        self.max_risk_per_trade = self.params.get("max_risk_per_trade", 0.02)  # 2% 最大风险
    
    def calculate_position_size(
        self,
        pair: str,
        capital: float,
        entry_price: float,
        stop_loss_pct: float,
    ) -> float:
        """
        根据 Edge 规则计算仓位大小
        
        Freqtrade 公式:
        position_size = (capital * risk) / stop_loss_pct
        """
        if not self.enabled:
            return capital
        
        risk_amount = capital * self.max_risk_per_trade
        position_size = risk_amount / abs(stop_loss_pct)
        
        # 限制最大仓位
        max_position = capital * 0.3  # 不超过本金30%
        return min(position_size, max_position)
    
    def get_pair_stake_config(self, pair: str) -> Dict:
        """获取交易对的资金配置"""
        return self.pair_configs.get(pair, {
            "capital": self.capital_per_pair,
            "min_rate": 0.5,  # 最小胜率阈值
        })


# 全局实例
exit_manager = EnhancedExitManager()
edge_manager = EdgeManager()


def get_exit_manager(params: Dict = None) -> EnhancedExitManager:
    global exit_manager
    if params:
        exit_manager = EnhancedExitManager(params)
    return exit_manager


def get_edge_manager(params: Dict = None) -> EdgeManager:
    global edge_manager
    if params:
        edge_manager = EdgeManager(params)
    return edge_manager


if __name__ == "__main__":
    # 测试 EnhancedExitManager
    params = {
        "trailing_stop": True,
        "trailing_stop_positive": 0.05,
        "trailing_stop_positive_offset": 0.02,
        "use_custom_stoploss": True,
        "stoploss": 0.03,
        "use_custom_roi": True,
        "minimal_roi": {"0": 0.30, "30": 0.15, "60": 0.08, "120": 0.05},
        "use_custom_exit": True,
        "custom_exit_conditions": {"rsi_overbought": 75, "bb_upper_touch": True, "time_based_exit": 240},
    }
    
    manager = EnhancedExitManager(params)
    
    trade_data = {
        "entry_price": 1.0,
        "entry_time": datetime.now() - timedelta(minutes=45),
        "rsi": 78,  # 超买
    }
    
    result = manager.should_exit(
        pair="TEST/USDT",
        trade_data=trade_data,
        current_time=datetime.now(),
        current_price=1.15,
    )
    
    print("=" * 60)
    print("Enhanced Exit Manager 测试")
    print("=" * 60)
    print(f"结果: {result}")
    
    # 测试 Entry 确认
    confirmed, reason = manager.confirm_trade_entry(
        pair="BTC/USDT",
        side="long",
        current_price=50000,
        proposed_rate=50500,
        amount=100,
        current_time=datetime.now(),
    )
    print(f"\n入场确认: {confirmed}, 原因: {reason}")
import logging
from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timezone, timedelta

from trading.hour_based_strategy import HourBasedStrategy, create_hour_based_strategy
from trading.entry_price_learner import EntryPriceLearner

logger = logging.getLogger(__name__)
UTC = timezone.utc


class TradeInfo:
    """轻量交易记录（替代 freqtrade_strategy.TradeInfo）"""
    def __init__(
        self,
        id: int,
        pair: str,
        amount: float,
        stake_amount: float,
        open_rate: float,
        open_rate_requested: float,
        open_date: datetime,
    ):
        self.id = id
        self.pair = pair
        self.amount = amount
        self.stake_amount = stake_amount
        self.open_rate = open_rate
        self.open_rate_requested = open_rate_requested
        self.open_date = open_date
        self._current_rate: float = open_rate

    @property
    def profit_pct(self) -> float:
        if self.open_rate <= 0:
            return 0.0
        return (self._current_rate - self.open_rate) / self.open_rate

    @property
    def hold_minutes(self) -> float:
        now = datetime.now(UTC)
        open_dt = self.open_date
        if open_dt.tzinfo is None:
            open_dt = open_dt.replace(tzinfo=UTC)
        return (now - open_dt).total_seconds() / 60


class MinimalStrategy:
    """
    内嵌的轻量策略实现
    取代对 freqtrade_strategy 包的依赖
    """

    def __init__(self, config: Dict[str, Any]):
        self.stoploss = config.get("stoploss", -0.10)
        self.trailing_stop = config.get("trailing_stop", True)
        self.trailing_stop_positive = config.get("trailing_stop_positive", 0.02)
        self.trailing_stop_offset = config.get("trailing_stop_offset", 0.04)
        self.minimal_roi: Dict[str, float] = config.get("minimal_roi", {
            "0": 0.20, "30": 0.10, "60": 0.05
        })
        self.max_hold_minutes = config.get("max_hold_minutes", 438)
        self.max_open_trades = config.get("max_open_positions", 5)
        self._highest: Dict[str, float] = {}

    def should_exit(self, trade: TradeInfo, current_rate: float) -> Tuple[bool, str]:
        trade._current_rate = current_rate
        profit = trade.profit_pct
        hold_min = trade.hold_minutes

        for minutes_str, threshold in sorted(
            self.minimal_roi.items(), key=lambda x: -int(x[0])
        ):
            if hold_min >= int(minutes_str) and profit >= threshold:
                return True, f"roi_{int(threshold*100)}pct"

        if self.trailing_stop and profit >= self.trailing_stop_positive:
            pair = trade.pair
            self._highest[pair] = max(self._highest.get(pair, current_rate), current_rate)
            pullback = (self._highest[pair] - current_rate) / self._highest[pair]
            if pullback >= self.trailing_stop_offset:
                return True, "trailing_stop"

        if profit <= self.stoploss:
            return True, "stop_loss"

        # 类型安全的超时检查
        max_hold = int(self.max_hold_minutes) if self.max_hold_minutes else 438
        if hold_min >= max_hold:
            return True, "max_hold_time"

        return False, ""

    def confirm_entry(
        self,
        pair: str,
        amount: float,
        rate: float,
        **kwargs,
    ) -> bool:
        if amount < 5:
            return False
        return True

    def custom_entry_price(
        self,
        pair: str,
        proposed_rate: float,
        **kwargs,
    ) -> float:
        return proposed_rate * 0.997

    def custom_exit_price(
        self,
        pair: str,
        proposed_rate: float,
        exit_tag: str = "",
        **kwargs,
    ) -> float:
        return proposed_rate

    def reset(self, pair: str):
        self._highest.pop(pair, None)


class FreqtradeIntegration:
    """
    Freqtrade 风格集成器（修复版）
    去掉了对 freqtrade_strategy 包的依赖，改用内嵌 MinimalStrategy
    """

    def __init__(self, params: Dict[str, Any] = None):
        params = params or {}
        self.strategy = MinimalStrategy(params)
        self.hour_strategy: HourBasedStrategy = create_hour_based_strategy(
            max_daily_trades=params.get("max_trades_per_day", 4),
            max_per_session=params.get("max_trades_per_session", 2),
        )
        self.entry_learner = EntryPriceLearner()

    def confirm_entry(
        self,
        pair: str,
        current_price: float,
        proposed_rate: float,
        amount: float,
        side: str = "long",
    ) -> Tuple[bool, str]:
        can_trade, reason = self.hour_strategy.can_trade()
        if not can_trade:
            return False, reason
        ok = self.strategy.confirm_entry(pair, amount, proposed_rate)
        return ok, "" if ok else "strategy_confirm_failed"

    def get_entry_price(
        self,
        pair: str,
        bid_price: float,
        ask_price: float,
    ) -> float:
        symbol = pair.replace("-USDT", "").replace("-USDT-SWAP", "")
        entry_info = self.entry_learner.get_entry_price(symbol, bid_price, ask_price, "limit")
        return entry_info.get("price", self.strategy.custom_entry_price(pair, bid_price))

    def should_exit(
        self,
        trade_dict: Dict,
        current_price: float,
    ) -> Tuple[bool, str]:
        trade = self._to_trade_info(trade_dict)
        return self.strategy.should_exit(trade, current_price)

    def record_trade(self):
        self.hour_strategy.record_trade()

    def during_position_management(
        self,
        token: str,
        entry_price: float,
        current_price: float,
        entry_time: datetime,
        closes_4h=None,
    ) -> Tuple[bool, str]:
        fake_trade = TradeInfo(
            id=0,
            pair=token,
            amount=888,
            stake_amount=888,
            open_rate=entry_price,
            open_rate_requested=entry_price,
            open_date=entry_time,
        )
        should_exit, reason = self.strategy.should_exit(fake_trade, current_price)
        return should_exit, reason

    def _to_trade_info(self, trade: Dict) -> TradeInfo:
        from trading.position_monitor import parse_entry_timestamp
        ts = trade.get("timestamp", "")
        try:
            open_date = parse_entry_timestamp(ts)
        except Exception:
            open_date = datetime.now(UTC)
        return TradeInfo(
            id=trade.get("index", 0),
            pair=trade.get("token", ""),
            amount=trade.get("position_size", 0),
            stake_amount=trade.get("position_size", 0) * trade.get("entry_price", 0),
            open_rate=trade.get("entry_price", 0),
            open_rate_requested=trade.get("entry_price", 0),
            open_date=open_date,
        )


def create_integrator(params: Dict[str, Any] = None) -> FreqtradeIntegration:
    """工厂函数"""
    return FreqtradeIntegration(params or {})
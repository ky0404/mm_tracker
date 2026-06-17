"""
时间段交易控制器 - HourBasedStrategy Integration
功能：控制每日交易时段和频率，确保3-4次交易分散在早中晚
基于 freqtrade-strategies/HourBasedStrategy.py

用户需求：
- 一天交易三四次，从早到晚
- 找到正确的方向以及入场时机
- 3倍杠杆每个代币开888u
"""
import logging
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TradeSession:
    """交易时段"""
    name: str           # 早/中/晚
    start_hour: int     # 开始小时
    end_hour: int       # 结束小时
    max_trades: int     # 该时段最大交易数


class HourBasedStrategy:
    """
    时间段交易策略控制器
    
    将一天分为三个时段：
    - 早盘 (00:00-08:00): 最多1-2次交易
    - 中盘 (08:00-16:00): 最多1-2次交易  
    - 晚盘 (16:00-24:00): 最多1-2次交易
    
    每日总交易上限: 3-4次
    """
    
    # 默认时段配置
    DEFAULT_SESSIONS = [
        TradeSession("早盘", 0, 8, 1),
        TradeSession("中盘", 8, 16, 1),
        TradeSession("晚盘", 16, 24, 2),
    ]
    
    def __init__(
        self,
        max_daily_trades: int = 4,
        max_per_session: int = 2,
        sessions: List[TradeSession] = None,
    ):
        self.max_daily_trades = max_daily_trades
        self.max_per_session = max_per_session
        self.sessions = sessions or self.DEFAULT_SESSIONS
        
        # 每日统计
        self.daily_trades: Dict[str, int] = {}  # {日期: 交易次数}
        self.session_trades: Dict[str, int] = {}  # {日期_时段: 交易次数}
        
        # 最后一次交易时间
        self.last_trade_time: Optional[datetime] = None
        self.cooldown_seconds: int = 300  # 5分钟冷却
        
        logger.info(f"[HourBased] 每日上限: {max_daily_trades}次, 时段上限: {max_per_session}次")
    
    def get_current_session(self) -> TradeSession:
        """获取当前时段"""
        current_hour = datetime.now().hour
        
        for session in self.sessions:
            if session.start_hour <= current_hour < session.end_hour:
                return session
        
        # 默认晚盘
        return self.sessions[-1]
    
    def get_date_key(self) -> str:
        """获取日期key"""
        return datetime.now().strftime("%Y-%m-%d")
    
    def can_trade(self) -> Tuple[bool, str]:
        """
        检查是否可以交易
        Returns: (can_trade: bool, reason: str)
        """
        now = datetime.now()
        date_key = self.get_date_key()
        current_session = self.get_current_session()
        session_key = f"{date_key}_{current_session.name}"
        
        # 1. 检查每日总次数
        daily_count = self.daily_trades.get(date_key, 0)
        if daily_count >= self.max_daily_trades:
            logger.warning(f"[HourBased] 每日交易次数已达上限: {daily_count}/{self.max_daily_trades}")
            return False, f"每日上限已达: {daily_count}/{self.max_daily_trades}"
        
        # 2. 检查当前时段次数
        session_count = self.session_trades.get(session_key, 0)
        if session_count >= self.max_per_session:
            logger.warning(f"[HourBased] 当前时段交易次数已达上限: {session_count}/{self.max_per_session}")
            return False, f"时段{current_session.name}上限已达: {session_count}/{self.max_per_session}"
        
        # 3. 检查冷却时间
        if self.last_trade_time:
            cooldown_seconds = (now - self.last_trade_time).total_seconds()
            if cooldown_seconds < self.cooldown_seconds:
                remaining = self.cooldown_seconds - cooldown_seconds
                logger.warning(f"[HourBased] 冷却中，还需{remaining:.0f}秒")
                return False, f"冷却中，还需{remaining:.0f}秒"
        
        return True, "允许交易"
    
    def record_trade(self) -> None:
        """记录一次交易"""
        now = datetime.now()
        date_key = self.get_date_key()
        current_session = self.get_current_session()
        session_key = f"{date_key}_{current_session.name}"
        
        # 更新统计
        self.daily_trades[date_key] = self.daily_trades.get(date_key, 0) + 1
        self.session_trades[session_key] = self.session_trades.get(session_key, 0) + 1
        self.last_trade_time = now
        
        logger.info(f"[HourBased] 交易记录: 今日{self.daily_trades[date_key]}次, "
                   f"时段{current_session.name}{self.session_trades[session_key]}次")
    
    def get_trade_status(self) -> Dict[str, any]:
        """获取交易状态"""
        date_key = self.get_date_key()
        current_session = self.get_current_session()
        session_key = f"{date_key}_{current_session.name}"
        
        return {
            "date": date_key,
            "current_session": current_session.name,
            "daily_trades": self.daily_trades.get(date_key, 0),
            "daily_limit": self.max_daily_trades,
            "session_trades": self.session_trades.get(session_key, 0),
            "session_limit": self.max_per_session,
            "can_trade": self.can_trade()[0],
        }
    
    def reset_daily(self) -> None:
        """重置每日统计（每天0点自动调用）"""
        date_key = self.get_date_key()
        self.daily_trades[date_key] = 0
        for key in list(self.session_trades.keys()):
            if key.startswith(date_key):
                self.session_trades[key] = 0
        logger.info(f"[HourBased] 每日统计已重置")


def create_hour_based_strategy(
    max_daily_trades: int = 4,
    max_per_session: int = 2,
) -> HourBasedStrategy:
    """创建时间段交易策略"""
    return HourBasedStrategy(
        max_daily_trades=max_daily_trades,
        max_per_session=max_per_session,
    )


if __name__ == "__main__":
    # 测试
    strategy = create_hour_based_strategy()
    can_trade, reason = strategy.can_trade()
    print(f"是否可以交易: {can_trade}, 原因: {reason}")
    print(f"交易状态: {strategy.get_trade_status()}")
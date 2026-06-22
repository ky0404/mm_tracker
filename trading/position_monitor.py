"""
持仓监控器 - Position Monitor (Freqtrade风格增强版)
功能：监控开仓仓位，自动执行止损/止盈/追踪止损/ROI/保护机制

整合自Freqtrade核心逻辑:
- freqtradebot.py: should_exit, _check_and_execute_exit
- persistence/trade_model.py: 持仓状态管理
- plugins/protections/stoploss_guard.py: 风控保护
"""
import time
import logging
import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from enum import Enum

# 导入交易数据库
try:
    from trading.trade_db import TradeDB
    _TRADE_DB_AVAILABLE = True
except ImportError:
    _TRADE_DB_AVAILABLE = False

logger = logging.getLogger(__name__)

UTC = timezone.utc


def parse_entry_timestamp(ts) -> datetime:
    """
    正确解析入场时间戳，处理各种格式
    支持: 
      - 2026-06-12T23:47:20.759562
      - 2026-06-12T23:47:20.759562Z
      - 2026-06-12T23:47:20
      - 1781674818564 (毫秒时间戳)
      - 1781674818564 (13位毫秒)
      - datetime对象
    """
    if not ts:
        return datetime.now(UTC)
    
    # 如果已经是datetime对象，确保有时区信息
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=UTC)
        return ts
    
    ts = str(ts).strip()
    
    # 尝试解析毫秒时间戳 (13位数字)
    if ts.isdigit() and len(ts) >= 13:
        try:
            ms = int(ts)
            return datetime.fromtimestamp(ms / 1000, UTC)
        except:
            pass
    
    # 统一处理Z后缀
    if ts.endswith('Z'):
        ts = ts[:-1] + '+00:00'
    
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        # 尝试只取日期时间部分
        ts = ts.split('.')[0]
        dt = datetime.fromisoformat(ts)
    
    # 确保有时区信息
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    
    return dt


class ExitType(Enum):
    """出场类型 (Freqtrade ExitType)"""
    EXIT_SIGNAL = "exit_signal"
    ROI = "roi"
    STOP_LOSS = "stoploss"
    TRAILING_STOP = "trailing_stop"
    PARTIAL_EXIT = "partial_exit"
    EMERGENCY_EXIT = "emergency_exit"
    MANUAL_EXIT = "manual_exit"
    MAX_HOLD_TIME = "max_hold_time"
    FUNDING_EXIT = "funding_exit"


@dataclass
class ExitConfig:
    """出场配置 (Freqtrade风格)"""
    # 止损
    stop_loss_pct: float = 0.0  # 硬止损禁用，改为软止损
    soft_stop_loss_pct: float = -0.15  # 软止损-15%才强制平仓
    
    # 强平预警 (新增)
    liquidation_warning_pct: float = 0.15  # 距强平15%时警告
    liquidation_danger_pct: float = 0.10   # 距强平10%时减仓
    liquidation_buffer_pct: float = 0.15   # 预留buffer
    
    # 追踪止损
    trailing_stop: bool = True
    trailing_stop_positive: float = 0.02  # 2%启动追踪
    trailing_stop_offset: float = 0.04    # 4%锁定利润
    
    # ROI (分阶段止盈)
    minimal_roi: dict = field(default_factory=lambda: {
        "0": 0.20,      # 0分钟以上: 20%
        "30": 0.10,    # 30分钟以上: 10%
        "60": 0.05,    # 60分钟以上: 5%
    })
    
    # 持仓时间
    max_hold_minutes: int = 438  # 从meta_optimizer优化而来
    min_hold_minutes: int = 60   # 最小持仓时间
    
    # 仓位限制
    max_open_positions: int = 5
    
    # 资金费率阈值
    funding_warning_pct: float = 0.15
    funding_reduce_pct: float = 0.50
    funding_exit_pct: float = 1.0
    
    # 保护机制
    max_drawdown: float = 0.25   # 最大回撤25%
    max_cooldown_seconds: int = 300  # 冷却时间
    max_consecutive_losses: int = 5  # 连续亏损次数
    
    # ===== 动态止盈配置 (根据信号数量) =====
    # 信号越多，目标涨幅越大
    # 格式: {信号数量阈值: 止盈目标}
    # 例如: {7: 0.50} 表示7个以上信号时，50%止盈
    dynamic_tp_by_signals: Dict[int, float] = field(default_factory=lambda: {
        7: 0.50,   # 7+信号: 看涨50%，止盈目标50%
        5: 0.15,   # 5-6信号: 看涨15%，止盈目标15%
        3: 0.08,   # 3-4信号: 看涨8%，止盈目标8%
        0: 0.05,   # <3信号: 保守5%
    })
    
    # 落袋为安
    partial_take_profit: bool = True
    partial_tp_pct: float = 0.05  # 5%时平50%保本
    partial_trailing_pct: float = 0.03  # 回撤3%触发


class PositionMonitor:
    """
    持仓监控器 (Freqtrade风格增强版)
    核心逻辑来自Freqtrade freqtradebot.py 的 should_exit 方法
    """

    def __init__(self, trader, result_logger, params: Dict[str, Any] = None):
        self.trader = trader
        self.result_logger = result_logger
        self.params = params or {}
        
        # 冷却机制：记录最近平仓的token，防止OKX数据延迟导致重复平仓
        self._recently_closed = {}  # {token: timestamp}
        self._close_cooldown_seconds = 600  # 10分钟内不重复平仓
        
        # 已确认平仓的持仓列表（持久化，防止重启后重复处理）
        self._closed_positions = set()  # 已成功平仓的 token 集合
        self._load_closed_positions()  # 从文件加载历史平仓记录
        
        # 优先从OKX Testnet API获取实时持仓
        open_positions = []
        
        # 方法1: 从OKX API获取实时持仓（最准确）
        if hasattr(self.trader, 'get_balance'):
            try:
                balance = self.trader.get_balance()
                if balance and 'details' in balance:
                    for d in balance['details']:
                        ccy = d.get('ccy')
                        # 只使用可用余额判断是否有实际持仓
                        avail_bal = float(d.get('availBal', 0))
                        frozen_bal = float(d.get('frozenBal', 0))
                        total = avail_bal + frozen_bal
                        
                        # 只有可用余额 > 0.001 才视为有效持仓
                        if avail_bal > 0.001 and ccy != 'USDT':
                            avg_px = float(d.get('accAvgPx', 0))
                            # 获取当前价格
                            current_price = 0
                            if hasattr(self.trader, 'get_current_price'):
                                try:
                                    current_price = self.trader.get_current_price(f"{ccy}-USDT")
                                except:
                                    pass
                            open_positions.append({
                                'token': ccy,
                                'entry_price': avg_px if avg_px > 0 else current_price,
                                'quantity': avail_bal,  # 使用可用余额
                                'current_price': current_price,
                                'created_at': datetime.now().isoformat(),
                                'source': 'OKX_API'
                            })
                    logger.info(f"[PositionMonitor] 从OKX API加载 {len(open_positions)} 个实时持仓")
            except Exception as e:
                logger.warning(f"[PositionMonitor] OKX API加载失败: {e}")
        
        # 方法2: 回退到数据库
        if not open_positions:
            self.trade_db = TradeDB() if _TRADE_DB_AVAILABLE else None
            if self.trade_db:
                open_positions = self.trade_db.get_open_positions()
                logger.info(f"[PositionMonitor] 回退到数据库加载 {len(open_positions)} 个持仓")
        
        # 同步到result_logger
        if self.result_logger and open_positions:
            for pos in open_positions:
                # 检查是否已有任何记录（ENTRY或EXIT）
                existing = [t for t in self.result_logger.trades 
                           if t.get("token") == pos["token"]]
                
                # 只添加没有记录的token
                if not existing:
                    self.result_logger.trades.append({
                        "index": len(self.result_logger.trades),
                        "type": "ENTRY",
                        "token": pos["token"],
                        "entry_price": pos["entry_price"],
                        "position_size": pos["quantity"],
                        "timestamp": pos.get("created_at", datetime.now().isoformat()),
                        "entry_price_original": pos["entry_price"],
                        "source": "OKX_API",
                        "status": "open"  # 明确设置为open状态
                    })
                    logger.info(f"[PositionMonitor] 新增持仓: {pos['token']} = {pos['quantity']} @ ${pos['entry_price']}")
                else:
                    # 修复已有的记录：确保 type=ENTRY 且 status=open
                    # 🔧 BUG修复: 只修复那些没有exit_reason的持仓
                    for t in existing:
                        updated = False
                        # 只有当持仓还没有退出理由时，才认为是活跃持仓
                        if not t.get('exit_reason'):
                            if t.get('type') != 'ENTRY':
                                t['type'] = 'ENTRY'
                                updated = True
                            if t.get('status') != 'open':
                                t['status'] = 'open'
                                updated = True
                            if t.get('position_size', 0) != pos['quantity']:
                                t['position_size'] = pos['quantity']
                                updated = True
                            if updated:
                                logger.info(f"[PositionMonitor] 修复持仓记录: {pos['token']} → type=ENTRY, status=open")
            
            self.result_logger.save()
            logger.info(f"[PositionMonitor] 同步完成")
        
        # 从strategy_params.json加载默认参数
        default_params = {}
        import os
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_params.json')
        if os.path.exists(config_path):
            import json
            with open(config_path) as f:
                strategy_config = json.load(f)
                exit_params = strategy_config.get('exit_params', {})
                default_params = {
                    'stop_loss_pct': exit_params.get('stop_loss_pct', -0.08),
                    'take_profit_pct': exit_params.get('take_profit_pct', 0.25),
                    'max_hold_minutes': exit_params.get('max_hold_minutes', 438),
                    'trailing_stop': exit_params.get('trailing_stop', True),
                    'minimal_roi': exit_params.get('minimal_roi', {'0': 0.25, '30': 0.15, '60': 0.08}),
                }
        
        # 合并参数: params优先，否则用默认配置
        merged_params = {**default_params, **self.params}
        
        # ===== 动态止盈: 根据信号数量 + 用户target_return =====
        # 读取用户配置的target_return (如 15% -> 0.15)
        user_target = merged_params.get("target_return", 0.15)
        user_stop_loss = merged_params.get("stop_loss", 0.03)
        
        # 动态止盈配置: 信号越多，止盈越高
        # 用户target是基础值，信号多时往上加
        signal_count = merged_params.get("entry_signal_count", 0)
        
        if signal_count >= 5:
            # 5+信号: 激进止盈 = user_target * 1.5
            dynamic_tp = user_target * 1.5
        elif signal_count >= 3:
            # 3-4信号: 正常止盈 = user_target
            dynamic_tp = user_target
        else:
            # <3信号: 保守止盈 = user_target * 0.8
            dynamic_tp = user_target * 0.8
        
        # 构建动态ROI: 时间越久，止盈越低(防止太贪)
        dynamic_roi = {
            "0": dynamic_tp,           # 0分钟以上: 动态止盈
            "30": dynamic_tp * 0.7,   # 30分钟以上: 70%
            "60": dynamic_tp * 0.5,   # 60分钟以上: 50%
        }
        
        logger.info(f"[动态止盈] 用户目标: {user_target*100}%, 信号数: {signal_count}, 动态止盈: {dynamic_tp*100:.1f}%")
        
        # 使用ExitConfig
        self.config = ExitConfig(
            stop_loss_pct=merged_params.get("stop_loss_pct", -user_stop_loss),
            soft_stop_loss_pct=merged_params.get("soft_stop_loss_pct", -user_stop_loss * 3),
            liquidation_warning_pct=merged_params.get("liquidation_warning_pct", 0.15),
            liquidation_danger_pct=merged_params.get("liquidation_danger_pct", 0.10),
            liquidation_buffer_pct=merged_params.get("liquidation_buffer_pct", 0.15),
            trailing_stop=merged_params.get("trailing_stop", True),
            trailing_stop_positive=merged_params.get("trailing_stop_positive", 0.02),
            trailing_stop_offset=merged_params.get("trailing_stop_offset", 0.04),
            minimal_roi=dynamic_roi,  # 使用动态止盈
            max_hold_minutes=merged_params.get("max_hold_minutes", 120),
            min_hold_minutes=merged_params.get("min_hold_minutes", 30),
            max_open_positions=merged_params.get("max_open_positions", 3),
            funding_warning_pct=merged_params.get("funding_warning_pct", 0.15),
            funding_reduce_pct=merged_params.get("funding_reduce_pct", 0.50),
            funding_exit_pct=merged_params.get("funding_exit_pct", 1.0),
            max_drawdown=merged_params.get("max_drawdown", 0.25),
            max_cooldown_seconds=merged_params.get("max_cooldown_seconds", 300),
            max_consecutive_losses=merged_params.get("max_consecutive_losses", 5),
            partial_take_profit=merged_params.get("partial_take_profit", True),
            partial_tp_pct=merged_params.get("partial_tp_pct", 0.05),  # 5%时平50%保本
            partial_trailing_pct=merged_params.get("partial_trailing_pct", 0.03),  # 3%追踪保护
        )
        
        # 保存用户配置供后续使用
        self.user_target_return = user_target
        self.user_stop_loss = user_stop_loss
        
        # 追踪止损状态
        self.highest_price: Dict[str, float] = {}  # 最高价
        self._highest_profit: Dict[str, float] = {}  # 最高利润率
        
        # 保护机制状态
        self._cooldown_end_time: Optional[datetime] = None
        self._consecutive_losses: int = 0
        self._peak_balance: float = 0
        self._current_balance: float = 0
        
        # 落袋为安标志
        self.half_position_taken: Dict[str, bool] = {}
        
        # DCA 配置
        self.dca_enabled = self.params.get("dca_enabled", False)
        self.dca_mode = self.params.get("dca_mode", "mode_0")
        self.dca_record: Dict[str, dict] = {}
    
    def _load_closed_positions(self):
        """从文件加载历史平仓记录"""
        import os
        closed_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'closed_positions.json')
        if os.path.exists(closed_file):
            try:
                with open(closed_file, 'r') as f:
                    data = json.load(f)
                    self._closed_positions = set(data.get('closed_tokens', []))
                    logger.info(f"[PositionMonitor] 从文件加载 {len(self._closed_positions)} 个历史平仓记录")
            except Exception as e:
                logger.warning(f"[PositionMonitor] 加载平仓记录失败: {e}")
    
    def _save_closed_positions(self):
        """保存平仓记录到文件"""
        import os
        data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
        os.makedirs(data_dir, exist_ok=True)
        closed_file = os.path.join(data_dir, 'closed_positions.json')
        try:
            with open(closed_file, 'w') as f:
                json.dump({'closed_tokens': list(self._closed_positions)}, f)
        except Exception as e:
            logger.warning(f"[PositionMonitor] 保存平仓记录失败: {e}")

    def update_balance(self, balance: float) -> None:
        """更新余额用于回撤计算"""
        self._current_balance = balance
        if balance > self._peak_balance:
            self._peak_balance = balance
    
    @property
    def current_drawdown(self) -> float:
        """当前回撤"""
        if self._peak_balance == 0:
            return 0
        return (self._peak_balance - self._current_balance) / self._peak_balance

    def can_trade(self, current_time: datetime = None) -> tuple[bool, str]:
        """检查是否可以交易 (Freqtrade风格保护检查)"""
        current_time = current_time or datetime.now(UTC)
        
        # 1. 检查冷却期
        if self._cooldown_end_time and current_time < self._cooldown_end_time:
            remaining = (self._cooldown_end_time - current_time).total_seconds()
            return False, f"cooldown: {remaining:.0f}s"
        
        # 2. 检查回撤保护
        if self.current_drawdown > self.config.max_drawdown:
            return False, f"max_drawdown: {self.current_drawdown:.1%}"
        
        return True, ""

    def record_trade_result(self, profit: float, current_time: datetime = None) -> None:
        """记录交易结果用于保护机制"""
        current_time = current_time or datetime.now(UTC)
        
        if profit < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.config.max_consecutive_losses:
                self._cooldown_end_time = current_time + timedelta(
                    seconds=self.config.max_cooldown_seconds
                )
                logger.warning(f"[保护机制] 连续{self._consecutive_losses}笔亏损，冷却{self.config.max_cooldown_seconds}秒")
        else:
            self._consecutive_losses = 0

    def check_positions(self, prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        检查所有持仓，处理 SL/TP/追踪止损/ROI/超时/资金费率/强平预警
        核心逻辑来自Freqtrade freqtradebot.py 的 should_exit 方法
        """
        from fetchers.price_api import fetch_funding_rate_history
        
        closed_trades = []
        partial_closed = []
        
        # 获取 unfinished trades 和已确认平仓的token
        unfinished = self.result_logger.get_unfinished_trades()
        
        # ========== 0. 过滤掉已确认平仓的持仓 ==========
        # 防止 OKX API 数据延迟导致的重复平仓问题
        filtered_unfinished = []
        for trade in unfinished:
            token = trade["token"]
            if token in self._closed_positions:
                logger.debug(f"[已平仓过滤] {token} 已在_closed_positions中，跳过检查")
                continue
            filtered_unfinished.append(trade)
        
        unfinished = filtered_unfinished
        
        for trade in unfinished:
            token = trade["token"]
            entry_price = trade.get("entry_price", 0)
            position_size = trade.get("position_size", 0)
            # 兼容旧记录：没有index则用token作为唯一标识
            trade_index = trade.get("index") or trade.get("trade_index") or token
            entry_time_str = trade.get("timestamp", "") or trade.get("entry_timestamp", "")
            
            # 获取入场时的信号数量（用于动态止盈）
            entry_signals_count = trade.get("signal_count", trade.get("entry_signals_count", 0))
            
            # 如果config中没有信号数量，从当前持仓更新
            if not hasattr(self, '_signal_counts'):
                self._signal_counts = {}
            if token not in self._signal_counts:
                self._signal_counts[token] = entry_signals_count
            
            current_price = prices.get(token, entry_price)
            if current_price <= 0:
                current_price = entry_price
            
            # 计算收益率
            profit_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0
            
            # ===== 新增: 获取market_context中的策略参数 =====
            market_context = trade.get("market_context", {})
            hold_style = market_context.get("hold_style", "altcoin")  # 默认山寨币策略
            is_mainstream = market_context.get("is_mainstream", False)
            custom_target = market_context.get("target_pct", 20)  # 默认20%
            custom_stop = market_context.get("stop_loss_pct", 2.5)  # 默认2.5%
            
            # 根据主流/山寨调整持仓时间
            if is_mainstream:
                max_hold = self.config.max_hold_minutes * 2  # 主流币可以持更久
            else:
                max_hold = self.config.max_hold_minutes  # 山寨币更早退出
            
            logger.debug(f"[动态退场] {token} 风格={hold_style}, 目标={custom_target}%, 止损={custom_stop}%, 最大持仓={max_hold}分钟")
            
            # ========== 0. 强平预警检查 (修复) ==========
            # 检查距离强平线的距离，3-5倍杠杆需要特别关注
            exit_reason = None
            exit_type = None
            exit_price = current_price
            reduce_ratio = 0
            
            leverage = self.params.get("leverage", 3.0)
            if leverage > 1:
                # 3x杠杆: 强平线约在 -33%, 5x杠杆: 强平线约在 -20%
                liquidation_ratio = 1 / leverage
                warning_pct = self.config.liquidation_warning_pct
                danger_pct = self.config.liquidation_danger_pct
                
                # 当前保证金率: 1 + profit_pct
                # 3x杠杆开多: 价格跌33%强平 -> 利润率 -33%
                # 距离强平: margin_ratio - liquidation_ratio
                current_margin = 1 + profit_pct
                margin_distance = current_margin - liquidation_ratio
                
                if margin_distance <= danger_pct:
                    # 危险! 距强平10%以内，强制全部平仓
                    logger.critical(f"[强平预警] 🚨 {token} 距强平仅剩{margin_distance*100:.1f}%! 强制全部平仓!")
                    reduce_ratio = 1.0  # 全部平仓
                    exit_reason = "LIQUIDATION_DANGER"
                    exit_type = ExitType.EMERGENCY_EXIT
                elif margin_distance <= warning_pct:
                    # 警告! 距强平15%以内，强制减仓50%
                    logger.warning(f"[强平预警] ⚠️ {token} 距强平仅剩{margin_distance*100:.1f}%! 强制减仓50%")
                    reduce_ratio = 0.5
                    exit_reason = "LIQUIDATION_WARNING"
                    exit_type = ExitType.EMERGENCY_EXIT
            
            # 如果已经触发强平预警，直接执行平仓，跳过其他检查
            if exit_reason:
                # 记录用于日志
                logger.critical(f"[强平执行] {token} 执行{exit_reason}, reduce_ratio={reduce_ratio}")
                
                # 直接跳到执行平仓部分
                symbol = f"{token}-USDT"
                close_size = position_size * reduce_ratio if 0 < reduce_ratio < 1 else position_size
                
                result = self.trader.close_position(symbol)
                
                if result.get("code") == "0":
                    pnl = (exit_price - entry_price) / entry_price * close_size if entry_price > 0 else 0
                    self.record_trade_result(pnl)
                    
                    # 记录平仓
                    self.result_logger.log_exit(
                        trade_index=trade_index,
                        exit_price=exit_price,
                        pnl=pnl,
                        exit_reason=exit_reason,
                    )
                    
                    # 标记已平仓
                    self._recently_closed[token] = datetime.now().timestamp()
                    self._closed_positions.add(token)
                    self._save_closed_positions()  # 持久化保存
                    
                    # 清理追踪状态
                    self.highest_price.pop(token, None)
                    self._highest_profit.pop(token, None)
                    
                    closed_trades.append({
                        "token": token,
                        "exit_type": exit_type.value if exit_type else "emergency_exit",
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl": pnl,
                        "pnl_pct": profit_pct * 100,
                        "exit_reason": exit_reason,
                    })
                    logger.info(f"[强平平仓] {token} @ {exit_price}, PnL: {pnl:.2f}")
                    continue
            
            # 更新最高价和最高利润率 (用于追踪止损)
            if token not in self.highest_price or current_price > self.highest_price[token]:
                self.highest_price[token] = current_price
            
            if token not in self._highest_profit or profit_pct > self._highest_profit[token]:
                self._highest_profit[token] = profit_pct
            
            # 计算持仓时间
            hold_minutes = 0
            if entry_time_str:
                try:
                    entry_time = parse_entry_timestamp(entry_time_str)
                    now_time = datetime.now(UTC)
                    hold_minutes = (now_time - entry_time).total_seconds() / 60
                except Exception as e:
                    logger.warning(f"[持仓时间解析失败] {token}: {e}")
            
            # ========== 1. 检查ROI (分阶段止盈) ==========
            # ROI是止盈条件，只有盈利时才检查
            if profit_pct > 0:
                roi_exit = self._check_roi(profit_pct, hold_minutes, custom_target)
                if roi_exit:
                    exit_reason = roi_exit["reason"]
                    exit_type = ExitType.ROI
                    logger.info(f"[ROI] {token} 持仓{hold_minutes:.0f}分钟, 收益率{profit_pct:.1%} >= {roi_exit['threshold']:.1%}")
            
            # ========== 2. 检查追踪止损 ==========
            # 追踪止损只在盈利时启动
            if not exit_reason and profit_pct > 0 and self.config.trailing_stop:
                trailing_exit = self._check_trailing_stop(token, current_price, profit_pct)
                if trailing_exit:
                    exit_reason = trailing_exit
                    exit_type = ExitType.TRAILING_STOP
                    logger.info(f"[追踪止损] {token} 触发追踪止损")
            
            # ========== 3. 落袋为安 (部分止盈) ==========
            # 部分止盈只在盈利时触发
            # 获取入场时的信号数量 (从trade记录中)
            entry_signals_count = trade.get("entry_signals_count", 0)
            
            if not exit_reason and profit_pct > 0 and self.config.partial_take_profit:
                partial_result = self._check_partial_take_profit(
                    token, entry_price, current_price, profit_pct,
                    entry_signals_count=entry_signals_count
                )
                if partial_result:
                    exit_reason = partial_result["reason"]
                    exit_type = ExitType.PARTIAL_EXIT
                    reduce_ratio = partial_result["reduce_ratio"]
            
            # ========== 4. 软止损检查 (替代硬止损) ==========
            # 用户要求"不想止损"，所以改用软止损机制：
            # - 亏损超过 soft_stop_loss_pct 才强制平仓 (默认-15%)
            # - 否则启用追踪止损保护利润
            soft_sl_pct = self.params.get("soft_stop_loss_pct", -0.15)
            if not exit_reason and profit_pct <= soft_sl_pct:
                exit_reason = "SOFT_STOP_LOSS"
                exit_type = ExitType.STOP_LOSS
                logger.info(f"[软止损] {token} 亏损{profit_pct:.1%} <= {soft_sl_pct:.1%}，触发强制平仓")
            elif not exit_reason and profit_pct < 0:
                # 亏损在0到-15%之间时，启用加强版追踪止损
                logger.info(f"[软止损保护] {token} 亏损{profit_pct:.1%}，启用保护模式")
                # 设置更紧的追踪止损
                self._highest_profit[token] = min(self._highest_profit.get(token, 0), profit_pct)
            
            # ========== 5. 资金费率检查 ==========
            if not exit_reason:
                funding_exit = self._check_funding_exit(token, profit_pct)
                if funding_exit:
                    exit_reason = funding_exit["reason"]
                    exit_type = ExitType.FUNDING_EXIT
                    if funding_exit.get("reduce_ratio"):
                        reduce_ratio = funding_exit["reduce_ratio"]
            
            # ========== 6. 超时检查 ==========
            if not exit_reason and hold_minutes >= max_hold:
                # 确保类型安全
                if isinstance(hold_minutes, (int, float)) and isinstance(self.config.max_hold_minutes, (int, float)):
                    exit_reason = "MAX_HOLD_TIME"
                    exit_type = ExitType.MAX_HOLD_TIME
                    logger.info(f"[超时] {token} 持仓{hold_minutes:.0f}分钟 >= {max_hold}分钟 (风格:{hold_style})")
                else:
                    # 类型不匹配时跳过或使用默认值
                    logger.warning(f"[超时检查跳过] {token}: hold_minutes类型={type(hold_minutes)}, max类型={type(self.config.max_hold_minutes)}")
            
            # ========== 7. DCA 加仓检查 ==========
            dca_action = None
            if self.dca_enabled and not exit_reason and profit_pct < 0:
                dca_action = self._check_dca(
                    token=token,
                    current_profit_pct=profit_pct,
                    current_price=current_price,
                    entry_price=entry_price,
                )
                if dca_action:
                    logger.info(f"[DCA] {token} {dca_action}")
            
            # 执行平仓
            if exit_reason or reduce_ratio > 0:
                # 检查冷却：最近2分钟是否已经尝试过平仓
                # 但如果是超时/止损等紧急退出，则忽略冷却
                now = datetime.now().timestamp()
                last_close = self._recently_closed.get(token, 0)
                in_cooldown = (now - last_close < self._close_cooldown_seconds)
                
                # 紧急退出原因，忽略冷却
                critical_exit = exit_reason in (
                    "MAX_HOLD_TIME", 
                    "STOPLOSS", 
                    "SOFT_STOPLOSS",
                    "TRAILING_STOP"
                )
                
                if in_cooldown and not critical_exit:
                    logger.info(f"[平仓冷却] {token} 刚平仓不久({int(now-last_close)}秒)，跳过")
                    continue
                
                # 如果是紧急退出且在冷却中，给出警告但仍然执行
                if in_cooldown and critical_exit:
                    logger.warning(f"[紧急平仓] {token} 虽在冷却中但因{exit_reason}强制平仓")
                
                # 检查是否已经平仓（防止重复平仓）
                # 只检查同 token 的且有实际 exit_price 的 EXIT 记录
                existing_exits = [t for t in self.result_logger.trades 
                                 if t.get('type') == 'EXIT' and t.get('token') == token
                                 and t.get('exit_price') is not None]
                if existing_exits:
                    logger.warning(f"[重复平仓防护] {token} 已有平仓记录(exit_price={existing_exits[0].get('exit_price')}), 跳过")
                    continue
                
                symbol = f"{token}-USDT"
                close_size = position_size * reduce_ratio if 0 < reduce_ratio < 1 else position_size
                
                result = self.trader.close_position(symbol)
                
                if result.get("code") == "0":
                    # 修复PnL计算: close_size是USDT金额，需要转换为代币数量
                    # PnL = (exit_price - entry_price) / entry_price * close_size
                    if entry_price > 0:
                        pnl = (exit_price - entry_price) / entry_price * close_size
                    else:
                        pnl = 0
                    
                    # 记录交易结果用于保护机制
                    self.record_trade_result(pnl)
                    
                    if 0 < reduce_ratio < 1:
                        self.result_logger.log_partial_close(
                            trade_index=trade_index,
                            close_size=close_size,
                            remaining_size=position_size - close_size,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason=exit_reason or "PARTIAL_CLOSE",
                        )
                        partial_closed.append({
                            "token": token,
                            "exit_type": exit_type.value if exit_type else "unknown",
                            "closed_size": close_size,
                            "remaining_size": position_size - close_size,
                            "exit_price": exit_price,
                            "pnl": pnl,
                            "exit_reason": exit_reason,
                        })
                    else:
                        # 确保exit_reason不为空
                        final_exit_reason = exit_reason if exit_reason else "AUTO_CLOSE"
                        self.result_logger.log_exit(
                            trade_index=trade_index,
                            exit_price=exit_price,
                            pnl=pnl,
                            exit_reason=final_exit_reason,
                        )
                        
                        # 记录平仓时间，用于冷却机制
                        self._recently_closed[token] = datetime.now().timestamp()
                        logger.info(f"[平仓记录] {token} 已平仓，记录冷却时间")
                        
                        # 添加到已确认平仓集合，防止重复平仓
                        self._closed_positions.add(token)
                        self._save_closed_positions()  # 持久化保存
                        logger.info(f"[已平仓标记] {token} 添加到_closed_positions")
                        
                        # 同步到 TradeDB
                        if _TRADE_DB_AVAILABLE:
                            try:
                                # 查找对应的trade - 使用token名称查找，使用 trade_db_id
                                trade = None
                                for t in self.result_logger.trades:
                                    if t.get('token') == token and t.get('type') == 'ENTRY':
                                        trade = t
                                        break
                                
                                if trade:
                                    # 使用正确的 TradeDB ID 而不是 index
                                    trade_db_id = trade.get('trade_db_id')
                                    if trade_db_id is not None:
                                        TradeDB.record_exit(
                                            trade_id=trade_db_id,
                                            exit_price=exit_price,
                                            exit_reason=exit_reason,
                                        )
                                        logger.info(f"[TradeDB] 平仓同步成功, trade_id={trade_db_id}")
                                    else:
                                        logger.warning(f"[TradeDB] {token} 无 trade_db_id，跳过")
                                else:
                                    logger.warning(f"[TradeDB] {token} 找不到对应入场记录")
                            except Exception as e:
                                logger.warning(f"[TradeDB] 同步失败: {e}")
                        
                        closed_trades.append({
                            "token": token,
                            "exit_type": exit_type.value if exit_type else "unknown",
                            "entry_price": entry_price,
                            "exit_price": exit_price,
                            "pnl": pnl,
                            "pnl_pct": profit_pct * 100,
                            "exit_reason": exit_reason,
                            "hold_minutes": hold_minutes,
                        })
                    
                    # 清理追踪状态
                    self.highest_price.pop(token, None)
                    self._highest_profit.pop(token, None)
                    self.half_position_taken.pop(token, None)
                    
                    logger.info(f"[平仓] {token} @ {exit_price}, PnL: {pnl:.2f} ({profit_pct:+.1%}), 类型: {exit_type.value if exit_type else 'N/A'}")
                else:
                    logger.error(f"[平仓失败] {token}: {result.get('msg')}")
        
        return closed_trades + partial_closed

    def _check_roi(self, profit_pct: float, hold_minutes: float, custom_target: float = None) -> Optional[dict]:
        """
        检查ROI止盈 (Freqtrade风格 + 自定义目标)
        minimal_roi: {"0": 0.20, "30": 0.10, "60": 0.05}
        
        如果传入了 custom_target，则优先使用自定义目标
        """
        # ===== 新增: 使用自定义目标 =====
        if custom_target and custom_target > 0:
            # 转换为小数格式
            target_threshold = custom_target / 100.0
            if profit_pct >= target_threshold:
                return {
                    "reason": f"ROI_CUSTOM_{custom_target}%",
                    "threshold": target_threshold,
                    "hold_minutes": hold_minutes
                }
            return None
        
        # 原有逻辑
        for minutes_str, threshold in sorted(self.config.minimal_roi.items(), reverse=True):
            threshold_minutes = int(minutes_str)
            if hold_minutes >= threshold_minutes:
                if profit_pct >= threshold:
                    return {
                        "reason": f"ROI_{threshold:.0%}",
                        "threshold": threshold,
                        "hold_minutes": hold_minutes
                    }
        return None

    def _check_trailing_stop(self, token: str, current_price: float, current_profit: float) -> Optional[str]:
        """
        检查追踪止损 (Freqtrade风格)
        启动条件: profit >= trailing_stop_positive (2%)
        触发条件: highest_profit - current_profit >= trailing_stop_offset (4%)
        """
        if current_profit < self.config.trailing_stop_positive:
            return None
        
        highest_profit = self._highest_profit.get(token, 0)
        
        if highest_profit - current_profit >= self.config.trailing_stop_offset:
            return f"TRAILING_STOP_{self.config.trailing_stop_offset:.0%}"
        
        return None

    def _check_partial_take_profit(
        self,
        token: str,
        entry_price: float,
        current_price: float,
        profit_pct: float,
        entry_signals_count: int = 0
    ) -> Optional[dict]:
        """
        动态止盈 - 根据信号数量决定止盈目标
        
        核心逻辑:
        - 入场时有N个信号，决定了上涨潜力
        - 7+信号: 可能涨50%，目标50%止盈
        - 5-6信号: 可能涨15%，目标15%止盈
        - <5信号: 保守8%，目标8%止盈
        - 5%时先平50%保本
        """
        half_taken = self.half_position_taken.get(token, False)
        
        # 获取该信号数量对应的止盈目标
        target_tp = self._get_dynamic_tp_target(entry_signals_count)
        
        # 第一阶段: 5%时平50%保本 (无论信号多少)
        if profit_pct >= self.config.partial_tp_pct and not half_taken:
            self.half_position_taken[token] = True
            logger.info(f"[动态止盈] {token} 盈利{profit_pct:.1%}, 入场信号{entry_signals_count}个, 目标{target_tp:.0%}, 平50%保本")
            return {
                "reason": f"TAKE_PROFIT_HALF_TARGET_{target_tp:.0%}",
                "reduce_ratio": 0.5,
                "target_tp": target_tp
            }
        
        # 第二阶段: 达到目标止盈且已过半仓
        if half_taken and profit_pct >= target_tp:
            highest = self.highest_price.get(token, entry_price)
            drawdown = (highest - current_price) / highest if highest > 0 else 0
            
            # 达到目标后，从高点回撤超过3%才全部平仓
            if drawdown >= 0.03:
                logger.info(f"[动态止盈] {token} 达到目标{target_tp:.0%}，从高点回撤{drawdown:.1%}，全部平仓")
                return {
                    "reason": f"DYNAMIC_TP_TARGET_{target_tp:.0%}",
                    "reduce_ratio": 1.0
                }
        
        # 第三阶段: 趋势反转检查 (只针对高信号币)
        if entry_signals_count >= 5 and half_taken:
            # 7+信号的币，让利润奔跑，回撤8%才走
            # 5-6信号的币，回撤5%就走
            reversal_threshold = 0.08 if entry_signals_count >= 7 else 0.05
            
            highest = self.highest_price.get(token, entry_price)
            if highest > 0:
                drawdown = (highest - current_price) / highest
                
                if drawdown >= reversal_threshold:
                    logger.info(f"[动态止盈] {token} 信号{entry_signals_count}个，趋势反转回撤{drawdown:.1%}，全部平仓")
                    return {
                        "reason": f"TREND_REVERSAL_{entry_signals_count}SIG",
                        "reduce_ratio": 1.0
                    }
        
        return None
    
    def _get_dynamic_tp_target(self, entry_signals_count: int) -> float:
        """
        根据入场信号数量获取止盈目标
        """
        dynamic_tp = self.config.dynamic_tp_by_signals
        
        if entry_signals_count >= 7:
            return dynamic_tp.get(7, 0.50)
        elif entry_signals_count >= 5:
            return dynamic_tp.get(5, 0.15)
        elif entry_signals_count >= 3:
            return dynamic_tp.get(3, 0.08)
        else:
            return dynamic_tp.get(0, 0.05)
    
    def _check_trend_take_profit(
        self,
        token: str,
        profit_pct: float,
        hold_minutes: float,
        current_price: float,
        entry_price: float
    ) -> Optional[dict]:
        """
        智能趋势止盈 - 让利润奔跑！
        核心逻辑：
        - 5%时平50%保本（已在partial_take_profit处理）
        - 剩余50%让利润奔跑，只在趋势反转时退出
        - 趋势判断：价格创新高+RSI<70+趋势向上 → 继续持有
        """
        half_taken = self.half_position_taken.get(token, False)
        
        # 如果还没到5%，不进行趋势追踪
        if profit_pct < 0.05:
            return None
        
        # 只有50%仓位已落袋，才启用趋势追踪
        if not half_taken:
            return None
        
        # 获取当前最高利润
        highest_profit = self._highest_profit.get(token, profit_pct)
        
        # 趋势保护：利润从最高点回撤超过5%才考虑退出
        if highest_profit > 0.05:
            drawdown = highest_profit - profit_pct
            # 回撤超过5%且持仓超过30分钟，触发趋势反转检查
            if drawdown >= 0.05 and hold_minutes >= 30:
                # 获取趋势信号判断
                trend_reversing = self._check_trend_reversal(token, current_price)
                if trend_reversing:
                    return {
                        "reason": "TREND_REVERSAL",
                        "reduce_ratio": 1.0  # 全部平仓
                    }
        
        # 继续持有：利润在扩大或保持高位
        return None
    
    def _check_trend_reversal(self, token: str, current_price: float) -> bool:
        """
        检查趋势是否反转（简化版）
        实际应该调用技术指标 API
        """
        # 简化逻辑：最近2根K线是否下跌
        # 实际应该用: CTI<0, EWO<0, RSI>70 等信号
        
        # 这里先用简单的价格判断
        highest = self.highest_price.get(token, current_price)
        if highest > 0:
            price_drop_pct = (highest - current_price) / highest
            # 价格从高点下跌超过8%，认为趋势可能反转
            if price_drop_pct >= 0.08:
                return True
        
        return False

    def _check_funding_exit(self, token: str, profit_pct: float) -> Optional[dict]:
        """资金费率检查"""
        try:
            funding_data = fetch_funding_rate_history(token)
            funding_rate = funding_data.get("latest_rate", 0) if funding_data else 0
            
            if funding_rate >= self.config.funding_exit_pct:
                return {
                    "reason": f"FUNDING_EXIT_{funding_rate:.3%}",
                    "reduce_ratio": 1.0
                }
            elif funding_rate >= self.config.funding_reduce_pct:
                return {
                    "reason": f"FUNDING_REDUCE_{funding_rate:.3%}",
                    "reduce_ratio": 0.3
                }
        except:
            pass
        
        return None

    def get_active_positions(self) -> List[Dict[str, Any]]:
        """获取当前活跃仓位"""
        unfinished = self.result_logger.get_unfinished_trades()
        active = []
        
        for trade in unfinished:
            active.append({
                "token": trade["token"],
                "entry_price": trade.get("entry_price", 0),
                "position_size": trade.get("position_size", 0),
                "signals": trade.get("signals", []),
                "timestamp": trade.get("timestamp", ""),
                "profit_pct": self._calculate_profit(
                    trade.get("entry_price", 0),
                    trade.get("current_price", trade.get("entry_price", 0))
                ),
                "hold_minutes": self._calculate_hold_minutes(trade.get("timestamp", "")),
            })
        
        return active

    def _calculate_profit(self, entry_price: float, current_price: float) -> float:
        """计算收益率"""
        if entry_price <= 0:
            return 0
        return (current_price - entry_price) / entry_price

    def _calculate_hold_minutes(self, timestamp: str) -> float:
        """计算持仓分钟数"""
        if not timestamp:
            return 0
        try:
            entry_time = parse_entry_timestamp(timestamp)
            now_time = datetime.now(UTC)
            return (now_time - entry_time).total_seconds() / 60
        except:
            return 0

    def can_open_new_position(self) -> bool:
        """检查是否可以开新仓位"""
        # 获取未完成的交易，但需要过滤掉已确认平仓的持仓
        unfinished = self.result_logger.get_unfinished_trades()
        
        # 过滤掉已平仓的持仓（防止 OKX API 延迟导致的问题）
        active_unfinished = [
            t for t in unfinished 
            if t.get("token") not in self._closed_positions
        ]
        
        available = self.config.max_open_positions - len(active_unfinished)
        logger.info(f"[持仓检查] 最大: {self.config.max_open_positions}, 活跃: {len(active_unfinished)}, 可用: {available}")
        
        return available > 0

    def update_params(self, params: Dict[str, Any]):
        """动态更新参数"""
        # 更新self.params，这样其他地方引用self.params时能获取最新值
        self.params.update(params)
        
        # 更新config
        if "stop_loss_pct" in params:
            self.config.stop_loss_pct = params["stop_loss_pct"]
        if "trailing_stop" in params:
            self.config.trailing_stop = params["trailing_stop"]
        if "trailing_stop_positive" in params:
            self.config.trailing_stop_positive = params["trailing_stop_positive"]
        if "trailing_stop_offset" in params:
            self.config.trailing_stop_offset = params["trailing_stop_offset"]
        if "minimal_roi" in params:
            self.config.minimal_roi = params["minimal_roi"]
        if "max_hold_minutes" in params:
            self.config.max_hold_minutes = params["max_hold_minutes"]
        if "min_hold_minutes" in params:
            self.config.min_hold_minutes = params["min_hold_minutes"]
        if "max_open_positions" in params:
            self.config.max_open_positions = params["max_open_positions"]
        
        # ========== 参数边界验证 ==========
        # 确保关键参数在合理范围内
        self.config.max_open_positions = max(2, min(self.config.max_open_positions, 5))
        self.config.max_hold_minutes = max(60, min(self.config.max_hold_minutes, 240))
        self.config.min_hold_minutes = max(15, min(self.config.min_hold_minutes, 60))
        
        if params.get("max_open_positions") and params["max_open_positions"] != self.config.max_open_positions:
            logger.warning(f"[参数修正] max_open_positions 从 {params['max_open_positions']} 调整为 {self.config.max_open_positions}")
        # ========== 验证结束 ==========
        
        # 兼容旧参数名
        sl_val = self.config.stop_loss_pct
        if abs(sl_val) > 1:
            sl_val = sl_val / 100
        self.sl_pct = abs(sl_val) * 100
        
        tp_val = self.config.minimal_roi.get("0", 0.20)
        if tp_val > 1:
            tp_val = tp_val / 100
        self.tp_pct = tp_val * 100
        
        logger.info(f"[参数更新] SL: -{self.sl_pct:.1f}%, TP: {self.tp_pct:.0f}%, "
                   f"MaxPos: {self.config.max_open_positions}, MaxHold: {self.config.max_hold_minutes}min, "
                   f"Trailing: {self.config.trailing_stop}")

    def _check_dca(self, token: str, current_profit_pct: float, current_price: float, entry_price: float) -> Optional[str]:
        """
        检查是否触发 DCA 加仓
        
        Returns:
            None 不加仓
            str 加仓描述信息
        """
        from trading.nfi_dca import NFIDCAManager
        
        if token not in self.dca_record:
            self.dca_record[token] = {"count": 0, "total_amount": 0}
        
        dca_info = self.dca_record[token]
        entry_count = dca_info["count"] + 1  # 第几次入场(首次=1, 加仓1次=2, ...)
        
        dca_manager = NFIDCAManager()
        result = dca_manager.calculate_dca(
            current_profit=current_profit_pct,
            entry_count=entry_count,
            mode=self.dca_mode,
        )
        
        if result.should_dca:
            dca_info["count"] += 1
            dca_info["total_amount"] += result.dca_amount
            
            return (f"触发DCA加仓 #{dca_info['count']}, "
                    f"亏损{current_profit_pct*100:.1f}%, "
                    f"加仓金额${result.dca_amount:.2f}, "
                    f"原因: {result.reason}")
        
        return None


if __name__ == "__main__":
    # 直接使用真实OKX API测试
    import json
    from trading.okx_testnet import OKXTestnetTrader
    
    with open("config/testnet_config.json", "r") as f:
        config = json.load(f)
    okx_cfg = config.get("okx", {})
    
    trader = OKXTestnetTrader(
        okx_cfg["api_key"], 
        okx_cfg["api_secret"], 
        okx_cfg["passphrase"], 
        testnet=True
    )
    logger = ResultLogger()
    
    monitor = PositionMonitor(trader, logger, {"stop_loss_pct": 5.0, "take_profit_pct": 10.0})
    
    # 测试开仓
    logger.log_entry(
        token="BTC",
        signals=[{"name": "signal_4_volume_spike"}],
        score=5.0,
        entry_price=63000.0,
        entry_signals_count=4,
        position_size=0.1,
    )
    
    # 测试检查（假设价格下跌 6%）
    prices = {"BTC": 59220.0}  # -6%
    closed = monitor.check_positions(prices)
    print(f"触发平仓: {closed}")
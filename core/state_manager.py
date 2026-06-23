"""
MMTracker CentralStateManager - 中央状态管理器
单一数据源管理持仓、价格、配置，解决多数据源不同步问题

设计原则:
- 单例模式 + 线程锁，保证并发安全
- SQLite 持久化，启动时自动恢复持仓
- OKX 定期同步，确保与交易所状态一致
- 统一配置读取，使用 utils.config_loader
"""

import threading
import time
import json
import os
import sqlite3
from dataclasses import dataclass, asdict
from typing import Dict, Optional, List
from pathlib import Path


@dataclass
class Position:
    token: str
    entry_price: float
    size_usd: float
    side: str  # 'long' or 'short'
    entry_time: float  # unix timestamp
    trade_id: str
    signals: list
    score: float
    take_profit_pct: float = 20.0
    stop_loss_pct: float = 5.0
    
    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "entry_price": self.entry_price,
            "size_usd": self.size_usd,
            "side": self.side,
            "entry_time": self.entry_time,
            "trade_id": self.trade_id,
            "signals": self.signals,
            "score": self.score,
            "take_profit_pct": self.take_profit_pct,
            "stop_loss_pct": self.stop_loss_pct,
        }


class CentralStateManager:
    """中央状态管理器 - 单例模式"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self._state_lock = threading.RLock()
        
        # 核心状态
        self.open_positions: Dict[str, Position] = {}
        self.price_cache: Dict[str, float] = {}
        self.price_cache_time: Dict[str, float] = {}
        
        # 配置 - 使用统一配置加载器
        self._config = self._load_config()
        
        # 数据库路径
        base_dir = Path(__file__).parent.parent
        self._db_path = base_dir / "trading" / "state.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # SQLite 持久化
        self._db = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False
        )
        self._init_db()
        
        # 启动时从 DB 恢复持仓
        self._restore_from_db()
        
        print(f"[StateManager] 初始化完成，开放持仓: {len(self.open_positions)}")
    
    def _load_config(self) -> dict:
        """加载配置 - 使用统一配置加载器"""
        try:
            from utils.config_loader import get_config as load_config
            return load_config()
        except ImportError:
            # 回退到直接读取
            base_dir = Path(__file__).parent.parent
            config_path = base_dir / "config" / "strategy_params.json"
            if config_path.exists():
                with open(config_path, 'r') as f:
                    return json.load(f)
            return {}
    
    def _init_db(self):
        """初始化数据库表"""
        self._db.execute('''
            CREATE TABLE IF NOT EXISTS positions (
                trade_id TEXT PRIMARY KEY,
                token TEXT,
                entry_price REAL,
                size_usd REAL,
                side TEXT,
                entry_time REAL,
                signals TEXT,
                score REAL,
                status TEXT DEFAULT 'open',
                exit_price REAL,
                exit_time REAL,
                exit_reason TEXT,
                pnl REAL,
                take_profit_pct REAL DEFAULT 20.0,
                stop_loss_pct REAL DEFAULT 5.0
            )
        ''')
        
        # 如果表已存在，尝试添加新列
        try:
            self._db.execute('ALTER TABLE positions ADD COLUMN take_profit_pct REAL DEFAULT 20.0')
            self._db.execute('ALTER TABLE positions ADD COLUMN stop_loss_pct REAL DEFAULT 5.0')
            self._db.commit()
        except:
            pass
        
        # 价格缓存表
        self._db.execute('''
            CREATE TABLE IF NOT EXISTS price_cache (
                token TEXT PRIMARY KEY,
                price REAL,
                update_time REAL
            )
        ''')
        
        self._db.commit()
    
    def _restore_from_db(self):
        """从数据库恢复持仓"""
        cursor = self._db.execute(
            "SELECT * FROM positions WHERE status='open'"
        )
        for row in cursor.fetchall():
            try:
                signals = json.loads(row[6]) if row[6] else []
            except:
                signals = []
            
            pos = Position(
                trade_id=row[0],
                token=row[1],
                entry_price=row[2],
                size_usd=row[3],
                side=row[4],
                entry_time=row[5],
                signals=signals,
                score=row[7]
            )
            self.open_positions[pos.token] = pos
        
        if self.open_positions:
            print(f"[StateManager] 从DB恢复 {len(self.open_positions)} 个持仓")
    
    # ── 持仓操作 ──────────────────────────────────────
    
    def get_position(self, token: str) -> Optional[Position]:
        """获取指定代币的持仓"""
        with self._state_lock:
            return self.open_positions.get(token.upper())
    
    def has_position(self, token: str) -> bool:
        """检查是否有持仓"""
        return token.upper() in self.open_positions
    
    def get_all_positions(self) -> Dict[str, Position]:
        """获取所有持仓"""
        with self._state_lock:
            return dict(self.open_positions)
    
    def get_position_count(self) -> int:
        """获取持仓数量"""
        return len(self.open_positions)
    
    def can_open_position(self) -> bool:
        """是否可以开新仓"""
        max_pos = self._config.get(
            'risk_management', {}
        ).get('max_open_positions', 3)
        return self.get_position_count() < max_pos
    
    def open_position(
        self,
        token: str,
        entry_price: float,
        size_usd: float,
        side: str = 'long',
        signals: list = None,
        score: float = 0,
        trade_id: str = None
    ) -> str:
        """
        开仓 - 记录到内存和数据库
        """
        with self._state_lock:
            token = token.upper()
            
            if self.has_position(token):
                raise ValueError(f"{token} 已有持仓")
            
            if not self.can_open_position():
                raise ValueError(f"已达最大持仓数 {self._config.get('risk_management', {}).get('max_open_positions', 3)}")
            
            if trade_id is None:
                trade_id = f"{token}_{int(time.time())}"
            
            if signals is None:
                signals = []
            
            # 动态止盈止损（基于信号分数）
            if score >= 7:
                take_profit_pct = 25.0
                stop_loss_pct = 8.0
            elif score >= 5:
                take_profit_pct = 20.0
                stop_loss_pct = 5.0
            elif score >= 3:
                take_profit_pct = 15.0
                stop_loss_pct = 4.0
            else:
                take_profit_pct = 12.0
                stop_loss_pct = 3.0
            
            pos = Position(
                token=token,
                entry_price=entry_price,
                size_usd=size_usd,
                side=side,
                entry_time=time.time(),
                trade_id=trade_id,
                signals=signals,
                score=score,
                take_profit_pct=take_profit_pct,
                stop_loss_pct=stop_loss_pct,
            )
            
            self.open_positions[token] = pos
            
            # 写入数据库
            self._db.execute('''
                INSERT INTO positions 
                (trade_id, token, entry_price, size_usd, side, 
                 entry_time, signals, score, status, take_profit_pct, stop_loss_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            ''', (
                trade_id, token, entry_price, size_usd, side,
                pos.entry_time, json.dumps(signals), score,
                take_profit_pct, stop_loss_pct
            ))
            self._db.commit()
            
            print(f"[StateManager] 开仓: {token} {side} @ ${entry_price} 金额:${size_usd}")
            return trade_id
    
    def close_position(
        self,
        token: str,
        exit_price: float,
        exit_reason: str
    ) -> Optional[dict]:
        """
        平仓 - 更新数据库，计算盈亏
        """
        with self._state_lock:
            token = token.upper()
            pos = self.open_positions.get(token)
            
            if pos is None:
                print(f"[StateManager] 平仓失败: {token} 无持仓")
                return None
            
            # 计算盈亏
            if pos.side == 'long':
                pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
            else:
                pnl_pct = (pos.entry_price - exit_price) / pos.entry_price * 100
            
            pnl_usd = pos.size_usd * pnl_pct / 100
            
            # 更新数据库
            self._db.execute('''
                UPDATE positions SET
                    status='closed',
                    exit_price=?,
                    exit_time=?,
                    exit_reason=?,
                    pnl=?
                WHERE trade_id=?
            ''', (
                exit_price, time.time(),
                exit_reason, pnl_usd,
                pos.trade_id
            ))
            self._db.commit()
            
            del self.open_positions[token]
            
            result = {
                "token": token,
                "trade_id": pos.trade_id,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "pnl_pct": round(pnl_pct, 2),
                "pnl_usd": round(pnl_usd, 2),
                "exit_reason": exit_reason,
                "side": pos.side,
                "signals": pos.signals,
                "score": pos.score,
            }
            
            print(f"[StateManager] 平仓: {token} {exit_reason} "
                  f"PnL={pnl_usd:+.2f}U ({pnl_pct:+.1f}%)")
            
            return result
    
    def close_all_positions(self, exit_price: float = 0, exit_reason: str = "force_close") -> List[dict]:
        """强制平所有仓"""
        with self._state_lock:
            results = []
            tokens = list(self.open_positions.keys())
            
            for token in tokens:
                result = self.close_position(token, exit_price, exit_reason)
                if result:
                    results.append(result)
            
            return results
    
    # ── 价格缓存 ──────────────────────────────────────
    
    def update_price(self, token: str, price: float):
        """更新价格缓存"""
        with self._state_lock:
            self.price_cache[token.upper()] = price
            self.price_cache_time[token.upper()] = time.time()
    
    def get_price(self, token: str, max_age: float = 60) -> Optional[float]:
        """获取缓存价格"""
        token = token.upper()
        with self._state_lock:
            if token not in self.price_cache:
                return None
            age = time.time() - self.price_cache_time.get(token, 0)
            if age > max_age:
                return None
            return self.price_cache[token]
    
    def get_or_fetch_price(self, token: str, trader, max_age: float = 60) -> Optional[float]:
        """获取价格，无缓存则从OKX获取"""
        token = token.upper()
        
        # 先从缓存取
        cached_price = self.get_price(token, max_age)
        if cached_price:
            return cached_price
        
        # 缓存过期，从OKX获取
        if trader and hasattr(trader, 'get_current_price'):
            try:
                price = trader.get_current_price(f"{token}-USDT")
                if price > 0:
                    self.update_price(token, price)
                    return price
            except Exception as e:
                print(f"[StateManager] 获取价格失败 {token}: {e}")
        
        return None
    
    # ── OKX 同步 ──────────────────────────────────────
    
    def sync_from_okx(self, trader) -> dict:
        """
        从 OKX 同步真实持仓到内存状态
        trader: OKXTestnetTrader 实例
        """
        with self._state_lock:
            try:
                balance = trader.get_balance()
                if not balance or 'details' not in balance:
                    return {"synced": False, "reason": "无法获取余额"}
                
                okx_tokens = {}
                for d in balance.get('details', []):
                    ccy = d.get('ccy', '')
                    eq = float(d.get('eq', 0))
                    if ccy != 'USDT' and eq > 0.001:
                        # 获取价格
                        try:
                            price = trader.get_current_price(f"{ccy}-USDT")
                        except:
                            price = 0
                        okx_tokens[ccy] = {"eq": eq, "price": price}
                        
                        if price > 0:
                            self.update_price(ccy, price)
                
                # 检查内存里有但 OKX 没有的持仓（可能已被手动平仓）
                stale = [
                    t for t in list(self.open_positions.keys())
                    if t not in okx_tokens
                ]
                for token in stale:
                    print(f"[StateManager] 检测到 {token} 已不在OKX持仓，同步平仓")
                    self.close_position(token, 0, "sync_closed")
                
                return {
                    "synced": True,
                    "okx_positions": list(okx_tokens.keys()),
                    "memory_positions": list(self.open_positions.keys()),
                    "stale_positions": stale
                }
            except Exception as e:
                return {"synced": False, "reason": str(e)}
    
    # ── 配置管理 ──────────────────────────────────────
    
    def get_config(self, key: str, default=None):
        """获取配置 - 支持点分隔的嵌套key"""
        keys = key.split('.')
        val = self._config
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k, default)
            else:
                return default
        return val
    
    def reload_config(self):
        """重新加载配置"""
        try:
            from utils.config_loader import reload_config
            self._config = reload_config()
            print("[StateManager] 配置已重载")
        except ImportError:
            print("[StateManager] 配置重载失败")
    
    # ── 统计 ──────────────────────────────────────────
    
    def get_stats(self) -> dict:
        """获取交易统计"""
        cursor = self._db.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(pnl) as total_pnl,
                AVG(pnl) as avg_pnl
            FROM positions WHERE status='closed'
        ''')
        row = cursor.fetchone()
        total = row[0] or 0
        wins = row[1] or 0
        
        return {
            "total_trades": total,
            "win_rate": round(wins / total, 3) if total > 0 else 0,
            "total_pnl": round(row[2] or 0, 2),
            "avg_pnl": round(row[3] or 0, 2),
            "open_positions": list(self.open_positions.keys()),
            "open_count": len(self.open_positions)
        }
    
    def get_position_detail(self, token: str) -> Optional[dict]:
        """获取持仓详情"""
        pos = self.get_position(token)
        if not pos:
            return None
        
        current_price = self.get_price(token, max_age=300)
        
        # 计算浮动盈亏
        if current_price and pos.side == 'long':
            pnl_pct = (current_price - pos.entry_price) / pos.entry_price * 100
        elif current_price and pos.side == 'short':
            pnl_pct = (pos.entry_price - current_price) / pos.entry_price * 100
        else:
            pnl_pct = 0
        
        return {
            "token": pos.token,
            "entry_price": pos.entry_price,
            "current_price": current_price,
            "size_usd": pos.size_usd,
            "side": pos.side,
            "pnl_pct": round(pnl_pct, 2),
            "entry_time": pos.entry_time,
            "signals": pos.signals,
            "score": pos.score,
            "hold_minutes": (time.time() - pos.entry_time) / 60
        }
    
    # ── 调试 ──────────────────────────────────────────
    
    def debug_dump(self) -> dict:
        """调试: 输出完整状态"""
        return {
            "open_positions": {
                k: v.to_dict() for k, v in self.open_positions.items()
            },
            "price_cache": dict(self.price_cache),
            "stats": self.get_stats()
        }
    
    def close(self):
        """关闭数据库连接"""
        if hasattr(self, '_db') and self._db:
            self._db.close()
            print("[StateManager] 数据库连接已关闭")


# ── 全局单例访问点 ───────────────────────────────────

_state: Optional[CentralStateManager] = None


def get_state() -> CentralStateManager:
    """获取中央状态管理器单例"""
    global _state
    if _state is None:
        _state = CentralStateManager()
    return _state


def reset_state():
    """重置单例 - 仅用于测试"""
    global _state
    if _state:
        _state.close()
    _state = None
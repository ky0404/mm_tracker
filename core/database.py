#!/usr/bin/env python3
"""
MMTracker SQLite 数据库层
替换 live_trades.json，实现结构化数据存储
"""
import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager


class Database:
    """SQLite 数据库管理"""
    
    def __init__(self, db_path: str = "data/mmtracker.db"):
        self.db_path = db_path
        self._ensure_dir()
        self._init_tables()
    
    def _ensure_dir(self):
        """确保目录存在"""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
    
    @contextmanager
    def _get_conn(self):
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()
    
    def _init_tables(self):
        """初始化表结构"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            
            # 交易记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    amount REAL,
                    side TEXT,
                    entry_time TEXT,
                    exit_time TEXT,
                    pnl_pct REAL,
                    pnl_usd REAL,
                    reason TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 信号记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token TEXT NOT NULL,
                    signal_type TEXT,
                    confidence REAL,
                    price REAL,
                    stage TEXT,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 优化历史表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS optimizations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    param_name TEXT,
                    old_value REAL,
                    new_value REAL,
                    reason TEXT,
                    pnl_before REAL,
                    pnl_after REAL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 机器人状态表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 创建索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_token ON trades(token)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_token ON signals(token)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_signals_created ON signals(created_at)")
    
    # ==================== 交易记录 ====================
    
    def add_trade(self, token: str, entry_price: float, amount: float, 
                  side: str = "long", reason: str = "") -> int:
        """添加交易记录"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades (token, entry_price, amount, side, reason, status)
                VALUES (?, ?, ?, ?, ?, 'open')
            """, (token, entry_price, amount, side, reason))
            return cursor.lastrowid
    
    def close_trade(self, trade_id: int, exit_price: float, 
                    reason: str = "", pnl_pct: float = 0, pnl_usd: float = 0):
        """平仓交易"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE trades 
                SET exit_price = ?, exit_time = ?, reason = ?, 
                    pnl_pct = ?, pnl_usd = ?, status = 'closed',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (exit_price, datetime.now().isoformat(), reason, 
                  pnl_pct, pnl_usd, trade_id))
    
    def get_open_trades(self) -> List[Dict]:
        """获取开仓中交易"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trades WHERE status = 'open' ORDER BY created_at DESC
            """)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_all_trades(self, limit: int = 100) -> List[Dict]:
        """获取所有交易"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trades ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_trade_stats(self) -> Dict[str, Any]:
        """获取交易统计"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            
            # 总交易数
            cursor.execute("SELECT COUNT(*) as total FROM trades")
            total = cursor.fetchone()['total']
            
            # 胜率
            cursor.execute("SELECT COUNT(*) as wins FROM trades WHERE pnl_usd > 0 AND status = 'closed'")
            wins = cursor.fetchone()['wins']
            
            # 总盈亏
            cursor.execute("SELECT SUM(pnl_usd) as total_pnl FROM trades WHERE status = 'closed'")
            total_pnl = cursor.fetchone()['total_pnl'] or 0
            
            # 平均持仓时间
            cursor.execute("""
                SELECT AVG(
                    (julianday(exit_time) - julianday(entry_time)) * 24 * 60
                ) as avg_minutes FROM trades WHERE status = 'closed' AND exit_time IS NOT NULL
            """)
            avg_hold = cursor.fetchone()['avg_minutes'] or 0
            
            return {
                'total_trades': total,
                'wins': wins,
                'win_rate': wins / total if total > 0 else 0,
                'total_pnl': total_pnl,
                'avg_hold_minutes': avg_hold
            }
    
    # ==================== 信号记录 ====================
    
    def add_signal(self, token: str, signal_type: str, confidence: float,
                   price: float, stage: str = "", metadata: dict = None):
        """添加信号记录"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO signals (token, signal_type, confidence, price, stage, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (token, signal_type, confidence, price, stage, 
                  json.dumps(metadata) if metadata else None))
    
    def get_recent_signals(self, hours: int = 24, limit: int = 50) -> List[Dict]:
        """获取最近信号"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM signals 
                WHERE created_at >= datetime('now', '-' || ? || ' hours')
                ORDER BY confidence DESC LIMIT ?
            """, (hours, limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get('metadata'):
                    d['metadata'] = json.loads(d['metadata'])
                result.append(d)
            return result
    
    # ==================== 优化记录 ====================
    
    def add_optimization(self, param_name: str, old_value: float, new_value: float,
                         reason: str = "", pnl_before: float = 0, pnl_after: float = 0):
        """添加优化记录"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO optimizations (param_name, old_value, new_value, reason, pnl_before, pnl_after)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (param_name, old_value, new_value, reason, pnl_before, pnl_after))
    
    def get_recent_optimizations(self, limit: int = 20) -> List[Dict]:
        """获取最近优化"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM optimizations ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    
    # ==================== 机器人状态 ====================
    
    def set_state(self, key: str, value: Any):
        """设置状态"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO bot_state (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, json.dumps(value) if isinstance(value, (dict, list)) else str(value)))
    
    def get_state(self, key: str, default: Any = None) -> Any:
        """获取状态"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM bot_state WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                try:
                    return json.loads(row['value'])
                except:
                    return row['value']
            return default
    
    def get_all_state(self) -> Dict[str, Any]:
        """获取所有状态"""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM bot_state")
            result = {}
            for row in cursor.fetchall():
                try:
                    result[row['key']] = json.loads(row['value'])
                except:
                    result[row['key']] = row['value']
            return result


# 全局数据库实例
_db_instance: Optional[Database] = None


def get_db(db_path: str = "data/mmtracker.db") -> Database:
    """获取数据库实例"""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
    return _db_instance


def init_db(db_path: str = "data/mmtracker.db") -> Database:
    """初始化数据库"""
    global _db_instance
    _db_instance = Database(db_path)
    return _db_instance
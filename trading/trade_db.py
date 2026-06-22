"""
MMTracker 交易闭环数据库
=======================
完整的交易记录系统：从入场到平仓的全流程追踪

Tables:
- trades: 所有交易记录
- positions: 当前持仓
- predictions: 预测/推荐历史
- daily_stats: 每日统计
"""

import sqlite3
import json
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from pathlib import Path

DB_PATH = "trading/trade_db.sqlite"


def get_connection():
    """获取数据库连接"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """初始化数据库表"""
    conn = get_connection()
    cursor = conn.cursor()
    
    # 交易记录表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL,
            side TEXT NOT NULL,  -- buy/sell
            entry_price REAL,
            exit_price REAL,
            quantity REAL,
            entry_time TEXT,
            exit_time TEXT,
            pnl_usd REAL,
            pnl_pct REAL,
            fee_usd REAL,
            status TEXT DEFAULT 'open',  -- open/closed
            signal_name TEXT,
            signal_score REAL,
            entry_order_id TEXT,
            exit_order_id TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 预测推荐表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time TEXT NOT NULL,
            token TEXT NOT NULL,
            price REAL,
            change_24h_pct REAL,
            volume_usd REAL,
            tech_score REAL,
            stage TEXT,
            confidence REAL,
            signal_count INTEGER,
            actual_entry BOOLEAN,
            actual_pnl REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 每日统计表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            total_trades INTEGER DEFAULT 0,
            closed_trades INTEGER DEFAULT 0,
            winning_trades INTEGER DEFAULT 0,
            losing_trades INTEGER DEFAULT 0,
            total_pnl_usd REAL DEFAULT 0,
            total_volume_usd REAL DEFAULT 0,
            best_trade TEXT,
            worst_trade TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 信号表现表
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS signal_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_name TEXT NOT NULL,
            total_triggers INTEGER DEFAULT 0,
            winning_triggers INTEGER DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            avg_pnl REAL,
            win_rate REAL,
            last_triggered TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    conn.close()
    print(f"[TradeDB] ✅ 数据库初始化完成: {DB_PATH}")


class TradeDB:
    """交易数据库操作类"""
    
    @staticmethod
    def record_entry(token: str, side: str, price: float, quantity: float, 
                     signal_name: str = None, signal_score: float = 0,
                     order_id: str = None) -> int:
        """
        记录入场交易
        返回: trade_id
        """
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO trades (token, side, entry_price, quantity, 
                               signal_name, signal_score, entry_order_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
        """, (token, side, price, quantity, signal_name, signal_score, order_id))
        
        trade_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        print(f"[TradeDB] ✅ 入场记录: {token} @ ${price}, qty={quantity}")
        return trade_id
    
    @staticmethod
    def record_exit(trade_id: int, exit_price: float, exit_reason: str = None, order_id: str = None):
        """
        记录平仓交易并计算PnL
        """
        conn = get_connection()
        cursor = conn.cursor()
        
        # 获取入场记录
        cursor.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
        trade = cursor.fetchone()
        
        if not trade:
            print(f"[TradeDB] ❌ 找不到交易ID: {trade_id}")
            conn.close()
            return
        
        entry_price = trade['entry_price']
        quantity = trade['quantity']
        token = trade['token']
        
        # 计算PnL
        if trade['side'] == 'buy':
            pnl_usd = (exit_price - entry_price) * quantity
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:  # sell/short
            pnl_usd = (entry_price - exit_price) * quantity
            pnl_pct = (entry_price - exit_price) / entry_price * 100
        
        # 估算手续费 (0.1% per side)
        fee_usd = (entry_price + exit_price) * quantity * 0.001
        pnl_usd -= fee_usd
        
        # 更新记录 (添加notes字段记录退出原因)
        cursor.execute("""
            UPDATE trades 
            SET exit_price = ?, exit_time = ?, pnl_usd = ?, pnl_pct = ?,
                fee_usd = ?, status = 'closed', exit_order_id = ?,
                notes = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (exit_price, datetime.now().isoformat(), pnl_usd, pnl_pct, 
              fee_usd, order_id, exit_reason, trade_id))
        
        conn.commit()
        conn.close()
        
        print(f"[TradeDB] ✅ 平仓记录: {token} @ ${exit_price}, PnL: ${pnl_usd:+.2f} ({pnl_pct:+.2f}%)")
        
        # 更新每日统计
        TradeDB.update_daily_stats(token, pnl_usd)
        
        # 更新信号表现
        if trade['signal_name']:
            TradeDB.update_signal_performance(trade['signal_name'], pnl_usd)
    
    @staticmethod
    def update_daily_stats(token: str, pnl: float):
        """更新每日统计"""
        today = datetime.now().strftime("%Y-%m-%d")
        
        conn = get_connection()
        cursor = conn.cursor()
        
        # 检查今天的记录是否存在
        cursor.execute("SELECT * FROM daily_stats WHERE date = ?", (today,))
        existing = cursor.fetchone()
        
        if existing:
            # 更新
            cursor.execute("""
                UPDATE daily_stats 
                SET total_trades = total_trades + 1,
                    closed_trades = closed_trades + 1,
                    total_pnl_usd = total_pnl_usd + ?,
                    winning_trades = winning_trades + ?,
                    losing_trades = losing_trades + ?
                WHERE date = ?
            """, (pnl, 1 if pnl > 0 else 0, 1 if pnl <= 0 else 0, today))
        else:
            # 插入新记录
            cursor.execute("""
                INSERT INTO daily_stats (date, total_trades, closed_trades, 
                                        winning_trades, losing_trades, total_pnl_usd)
                VALUES (?, 1, 1, ?, ?, ?)
            """, (today, 1 if pnl > 0 else 0, 1 if pnl <= 0 else 0, pnl))
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def update_signal_performance(signal_name: str, pnl: float):
        """更新信号表现"""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM signal_performance WHERE signal_name = ?", 
                      (signal_name,))
        existing = cursor.fetchone()
        
        if existing:
            new_wins = existing['winning_triggers'] + (1 if pnl > 0 else 0)
            new_total = existing['total_triggers'] + 1
            cursor.execute("""
                UPDATE signal_performance
                SET total_triggers = ?,
                    winning_triggers = ?,
                    total_pnl = total_pnl + ?,
                    avg_pnl = (total_pnl + ?) / ?,
                    win_rate = ? * 100.0 / ?,
                    last_triggered = CURRENT_TIMESTAMP
                WHERE signal_name = ?
            """, (new_total, new_wins, pnl, pnl, new_total, 
                  new_wins, new_total, signal_name))
        else:
            cursor.execute("""
                INSERT INTO signal_performance (signal_name, total_triggers, 
                                                winning_triggers, total_pnl, 
                                                avg_pnl, win_rate)
                VALUES (?, 1, ?, ?, ?, ?)
            """, (signal_name, 1 if pnl > 0 else 0, pnl, pnl, 
                  100 if pnl > 0 else 0))
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def record_prediction(scan_time: str, token: str, price: float, 
                          change_24h: float, volume: float, tech_score: float,
                          stage: str, confidence: float, signal_count: int):
        """记录预测推荐"""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO predictions (scan_time, token, price, change_24h_pct,
                                    volume_usd, tech_score, stage, confidence,
                                    signal_count, actual_entry, actual_pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """, (scan_time, token, price, change_24h, volume, tech_score, 
              stage, confidence, signal_count))
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def mark_prediction_entered(token: str, scan_time: str, pnl: float):
        """标记预测是否入场及实际盈亏"""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            UPDATE predictions 
            SET actual_entry = 1, actual_pnl = ?
            WHERE token = ? AND scan_time LIKE ?
        """, (pnl, token, f"{scan_time[:10]}%"))
        
        conn.commit()
        conn.close()
    
    @staticmethod
    def get_open_positions() -> List[Dict]:
        """获取所有未平仓交易"""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM trades WHERE status = 'open'")
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    @staticmethod
    def cleanup_stale_positions(max_hold_hours: int = 24) -> Dict:
        """
        清理超时的异常持仓
        
        问题：如果程序异常退出，持仓可能长期处于'open'状态
        解决：检查持仓时间，超过max_hold_hours小时且已有平仓记录的视为异常
        
        Returns: {'cleaned': 数量, 'kept': 数量}
        """
        conn = get_connection()
        cursor = conn.cursor()
        
        # 计算超时阈值
        threshold = datetime.now() - timedelta(hours=max_hold_hours)
        
        # 找出超时的持仓
        cursor.execute("""
            SELECT id, token, created_at 
            FROM trades 
            WHERE status = 'open' 
            AND created_at < ?
        """, (threshold.isoformat(),))
        
        stale_positions = cursor.fetchall()
        
        cleaned_count = 0
        kept_count = 0
        
        for pos in stale_positions:
            pos_id = pos['id']
            token = pos['token']
            created = pos['created_at']
            
            # 检查是否真的还在持仓（通过OKX API或模拟检查）
            # 如果无法确认，标记为异常并强制平仓
            # 这里简化处理：超过24小时且状态为open的，标记为'ghost'并忽略
            
            cursor.execute("""
                UPDATE trades 
                SET status = 'ghost', notes = '系统清理: 超时持仓' 
                WHERE id = ?
            """, (pos_id,))
            
            print(f"[TradeDB] 🧹 清理异常持仓: {token} (ID: {pos_id}, 创建于 {created})")
            cleaned_count += 1
        
        conn.commit()
        
        # 统计保留的正常持仓
        cursor.execute("SELECT COUNT(*) as cnt FROM trades WHERE status = 'open'")
        kept_count = cursor.fetchone()['cnt']
        
        conn.close()
        
        result = {'cleaned': cleaned_count, 'kept': kept_count}
        print(f"[TradeDB] ✅ 持仓清理完成: 清理{cleaned_count}个异常, 保留{kept_count}个正常")
        
        return result
    
    @staticmethod
    def get_closed_trades(days: int = 30) -> List[Dict]:
        """获取已平仓交易"""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM trades 
            WHERE status = 'closed' 
            AND exit_time >= datetime('now', '-' || ? || ' days')
            ORDER BY exit_time DESC
        """, (days,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    @staticmethod
    def get_total_pnl(days: int = 30) -> Dict:
        """获取总盈亏统计"""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_usd <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(pnl_usd) as total_pnl,
                AVG(pnl_usd) as avg_pnl,
                AVG(CASE WHEN pnl_usd > 0 THEN pnl_usd END) as avg_win,
                AVG(CASE WHEN pnl_usd <= 0 THEN pnl_usd END) as avg_loss
            FROM trades
            WHERE status = 'closed'
            AND exit_time >= datetime('now', '-' || ? || ' days')
        """, (days,))
        
        row = cursor.fetchone()
        conn.close()
        
        if row and row['total']:
            total = row['total']
            return {
                'total_trades': total,
                'wins': row['wins'] or 0,
                'losses': row['losses'] or 0,
                'win_rate': (row['wins'] or 0) / total * 100,
                'total_pnl': row['total_pnl'] or 0,
                'avg_pnl': row['avg_pnl'] or 0,
                'avg_win': row['avg_win'] or 0,
                'avg_loss': row['avg_loss'] or 0,
            }
        return {'total_trades': 0, 'wins': 0, 'losses': 0, 'win_rate': 0, 
                'total_pnl': 0, 'avg_pnl': 0, 'avg_win': 0, 'avg_loss': 0}
    
    @staticmethod
    def get_signal_performance() -> List[Dict]:
        """获取信号表现排名"""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM signal_performance
            ORDER BY win_rate DESC, total_pnl DESC
        """)
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    @staticmethod
    def get_daily_stats(days: int = 30) -> List[Dict]:
        """获取每日统计"""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT * FROM daily_stats
            WHERE date >= datetime('now', '-' || ? || ' days')
            ORDER BY date DESC
        """, (days,))
        
        rows = cursor.fetchall()
        conn.close()
        
        return [dict(row) for row in rows]
    
    @staticmethod
    def generate_report() -> str:
        """生成完整交易报告"""
        stats = TradeDB.get_total_pnl(30)
        signals = TradeDB.get_signal_performance()
        daily = TradeDB.get_daily_stats(30)
        
        report = []
        report.append("="*70)
        report.append("📊 MMTRADER 交易闭环报告 (30天)")
        report.append("="*70)
        report.append("")
        report.append("【整体表现】")
        report.append(f"  总交易数: {stats['total_trades']}")
        report.append(f"  盈利: {stats['wins']} 笔 | 亏损: {stats['losses']} 笔")
        report.append(f"  胜率: {stats['win_rate']:.1f}%")
        report.append(f"  总盈亏: ${stats['total_pnl']:+.2f}")
        report.append(f"  平均每笔: ${stats['avg_pnl']:+.2f}")
        report.append(f"  平均盈利: ${stats['avg_win']:+.2f}")
        report.append(f"  平均亏损: ${stats['avg_loss']:+.2f}")
        report.append("")
        report.append("【信号表现 Top 10】")
        
        for i, s in enumerate(signals[:10], 1):
            emoji = "✅" if s['win_rate'] > 50 else "❌"
            report.append(f"  {i}. {emoji} {s['signal_name']}: 触发{s['total_triggers']}次, "
                         f"胜率{s['win_rate']:.0f}%, PnL:${s['total_pnl']:+.2f}")
        
        report.append("")
        report.append("【每日统计】")
        for d in daily[:7]:
            report.append(f"  {d['date']}: 交易{d['closed_trades']}笔, "
                         f"PnL:${d['total_pnl_usd']:+.2f}, 胜率{d['winning_trades']*100/max(d['closed_trades'],1):.0f}%")
        
        report.append("="*70)
        
        return "\n".join(report)


def sync_with_okx():
    """
    同步OKX实际持仓到数据库
    每次运行机器人时调用
    """
    try:
        from trading.okx_simulator import OKXSimulator
        trader = OKXSimulator()
        
        # 获取实际持仓
        positions = trader.get_positions()
        
        conn = get_connection()
        cursor = conn.cursor()
        
        # 获取数据库中的open仓位移除已平仓的
        db_positions = TradeDB.get_open_positions()
        db_tokens = {t['token'] for t in db_positions}
        
        okx_tokens = set()
        for p in positions:
            symbol = p.get('instId', '').replace('-USDT-SWAP', '')
            if symbol:
                okx_tokens.add(symbol)
        
        # 找出已平仓但未记录的
        closed_tokens = db_tokens - okx_tokens
        for token in closed_tokens:
            # 查找最新入场记录
            cursor.execute("""
                SELECT * FROM trades 
                WHERE token = ? AND status = 'open'
                ORDER BY entry_time DESC LIMIT 1
            """, (token,))
            trade = cursor.fetchone()
            
            if trade:
                # 获取当前价格计算PnL
                from fetchers.momentum import get_okx_price
                current_price = get_okx_price(token)
                
                if current_price and current_price > 0:
                    TradeDB.record_exit(trade['id'], current_price)
                    print(f"[Sync] ✅ 自动平仓记录: {token}")
        
        conn.close()
        
    except Exception as e:
        print(f"[Sync] ⚠️ OKX同步失败: {e}")


# 初始化数据库
if __name__ == "__main__":
    init_db()
    
    # 测试
    print("\n" + "="*50)
    print("📊 交易报告")
    print("="*50)
    print(TradeDB.generate_report())
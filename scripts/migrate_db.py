#!/usr/bin/env python3
"""
数据迁移工具: live_trades.json → SQLite
"""
import json
import os
from datetime import datetime
from core.database import get_db


def migrate_live_trades():
    """迁移 live_trades.json 到 SQLite"""
    
    trades_file = "trading/live_trades.json"
    if not os.path.exists(trades_file):
        print("❌ live_trades.json 不存在，无需迁移")
        return
    
    with open(trades_file, 'r') as f:
        trades = json.load(f)
    
    if not trades:
        print("⚠️ live_trades.json 为空，无需迁移")
        return
    
    print(f"📦 发现 {len(trades)} 条历史交易记录")
    
    db = get_db()
    migrated = 0
    
    for trade in trades:
        token = trade.get('token', '')
        entry_price = trade.get('entry_price', 0)
        exit_price = trade.get('exit_price', 0)
        amount = trade.get('amount', 0)
        side = trade.get('side', 'long')
        entry_time = trade.get('entry_time', '')
        exit_time = trade.get('exit_time', '')
        pnl_pct = trade.get('pnl_pct', 0)
        pnl_usd = trade.get('pnl_usd', 0)
        reason = trade.get('reason', '')
        status = trade.get('status', 'closed')
        
        if status == 'open':
            # 开仓中交易，添加但不平仓
            db.add_trade(token, entry_price, amount, side, reason)
        else:
            # 已平仓交易
            trade_id = db.add_trade(token, entry_price, amount, side, reason)
            db.close_trade(trade_id, exit_price, reason, pnl_pct, pnl_usd)
        
        migrated += 1
    
    print(f"✅ 迁移完成: {migrated} 条记录")
    
    # 验证
    stats = db.get_trade_stats()
    print(f"📊 SQLite 统计: {stats}")
    
    # 备份原文件
    backup_file = f"trading/live_trades.json.backup.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    os.rename(trades_file, backup_file)
    print(f"📁 已备份原文件到: {backup_file}")


if __name__ == "__main__":
    migrate_live_trades()
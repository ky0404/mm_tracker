"""
NFI 回测 - 模拟数据版本
用于开发和测试 NFI 量化因子
"""

import random
from typing import List, Dict


def generate_mock_candles(symbol: str, trend: str = "up", days: int = 30) -> list:
    """生成模拟K线数据"""
    candles = []
    base_price = 100.0
    
    for i in range(days * 24):
        change = random.uniform(-0.02, 0.025) if trend == "up" else random.uniform(-0.025, 0.02)
        base_price *= (1 + change)
        
        candles.append({
            "timestamp": i * 3600,
            "open": base_price * 0.998,
            "high": base_price * 1.01,
            "low": base_price * 0.99,
            "close": base_price,
            "volume": random.uniform(1000000, 5000000),
        })
    
    return candles


def mock_trades():
    """模拟交易记录"""
    return [
        {"token": "BTC", "entry_price": 64000, "exit_price": 65600, "pnl": 1600},
        {"token": "ETH", "entry_price": 3400, "exit_price": 3500, "pnl": 100},
    ]


if __name__ == "__main__":
    trades = mock_trades()
    print(f"NFI回测: {len(trades)} 笔交易")
    
    for t in trades:
        print(f"  {t['token']}: 入{t['entry_price']} 出{t['exit_price']} PnL{t['pnl']}")
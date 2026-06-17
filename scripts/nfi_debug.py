"""
NFI回测 - 调试版本
"""

import json
import requests
import pandas as pd
import sys, os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import calculate_nfi_indicators
import json


def load_params():
    with open("config/params.json") as f:
        return json.load(f)


def fetch_okx_candles(symbol: str, bar: str = "1H", limit: int = 200):
    url = "https://www.okx.com/api/v5/market/history-candles"
    params = {"instId": f"{symbol}-USDT-SWAP", "bar": bar, "limit": limit}
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get("code") != "0" or not data.get("data"):
            return None
        candles = []
        for c in data["data"]:
            candles.append({
                "timestamp": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
            })
        return candles[::-1]
    except Exception as e:
        return None


def main():
    with open("trading/live_trades.json") as f:
        trades = json.load(f)
    completed = [t for t in trades if t.get("type") == "EXIT" and t.get("exit_reason") != "manual_reset"]
    
    params = load_params()
    nfi_params = params.get("nfi_protection_params", {})
    
    # 只测试第一笔
    trade = completed[0]
    token = trade.get("token")
    print(f"测试 {token}...")
    
    candles = fetch_okx_candles(token, "1H", 200)
    if not candles:
        print("获取K线失败")
        return
    
    print(f"K线数量: {len(candles)}")
    
    indicators = calculate_nfi_indicators(candles, nfi_params)
    df = pd.DataFrame(indicators)
    
    print(f"\nDataFrame形状: {df.shape}")
    print(f"\n最后5行 EMA50趋势:")
    print(df[["ema_50", "ema_trend_rising", "rsi_14", "rsi_recovering"]].tail(10))
    
    # 看所有时刻的ema_trend_rising
    ema_true_count = df["ema_trend_rising"].sum()
    print(f"\nEMA趋势True的数量: {ema_true_count} / {len(df)}")
    print(f"EMA趋势True的比例: {ema_true_count/len(df)*100:.1f}%")


if __name__ == "__main__":
    main()
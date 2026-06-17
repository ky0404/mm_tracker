"""
NFI回测 - 更真实的波动模拟 + 参数验证
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import calculate_nfi_indicators
import json


def load_params():
    with open("config/params.json") as f:
        return json.load(f)


def gen_realistic_candles(trend: str, volatility: float = 0.03, days: int = 14):
    """生成更真实的K线: 趋势+震荡+随机波动"""
    n = days * 24
    base = 1.0
    
    prices = []
    price = base
    for i in range(n):
        # 趋势成分
        if trend == "up":
            trend_factor = 0.0005
        elif trend == "down":
            trend_factor = -0.0005
        else:
            trend_factor = 0
        
        # 震荡成分
        cycle = 0.01 * np.sin(i / 12)  # 12小时周期
        
        # 随机波动
        noise = volatility * np.random.randn()
        
        price = price * (1 + trend_factor + cycle + noise)
        prices.append(price)
    
    candles = []
    base_time = datetime.now() - timedelta(hours=n)
    for i, close in enumerate(prices):
        o = close * (1 - 0.005 * np.random.random())
        h = close * (1 + 0.015 * np.random.random())
        l = close * (1 - 0.015 * np.random.random())
        candles.append({
            "timestamp": int((base_time + timedelta(hours=i)).timestamp() * 1000),
            "open": o, "high": h, "low": l,
            "close": close,
            "volume": 1e6 * (1 + 0.3 * np.random.random())
        })
    return candles


def main():
    print("=" * 60)
    print("NFI参数验证回测")
    print("=" * 60)
    
    params = load_params()
    nfi = params["nfi_protection_params"]
    
    print(f"\n当前阈值配置:")
    print(f"  safe_dips: {nfi['safe_dips']}")
    print(f"  safe_pump: {nfi['safe_pump']}")
    print(f"  lookback: {nfi['lookback_periods']}")
    
    # 测试不同场景
    scenarios = [
        ("上涨趋势(波动大)", "up", 0.04),
        ("上涨趋势(波动小)", "up", 0.02),
        ("下跌趋势(波动大)", "down", 0.04),
        ("下跌趋势(波动小)", "down", 0.02),
        ("震荡市场", "sideways", 0.03),
    ]
    
    results = []
    
    for name, trend, vol in scenarios:
        candles = gen_realistic_candles(trend, vol, days=14)
        
        indicators = calculate_nfi_indicators(candles, nfi)
        df = pd.DataFrame(indicators)
        
        if len(df) < 50:
            print(f"{name}: 数据不足")
            continue
        
        latest = df.iloc[-1]
        
        result = {
            "scenario": name,
            "ema_trend": bool(latest.get("ema_trend_rising", False)),
            "rsi_recover": bool(latest.get("rsi_recovering", False)),
            "safe_dips": bool(latest.get("safe_dips", False)),
            "safe_pump": bool(latest.get("safe_pump", False)),
        }
        result["passed"] = (
            result["ema_trend"] and result["rsi_recover"] and 
            (result["safe_dips"] or result["safe_pump"])
        )
        results.append(result)
        
        status = "✓" if result["passed"] else "✗"
        print(f"\n{name}: {status}")
        print(f"  EMA趋势: {result['ema_trend']}, RSI恢复: {result['rsi_recover']}")
        print(f"  安全回调: {result['safe_dips']}, 安全涨幅: {result['safe_pump']}")
    
    print("\n" + "=" * 60)
    print("阈值敏感性分析")
    print("=" * 60)
    
    # 测试不同阈值
    test_thresholds = [
        {"0": 0.032, "2": 0.09, "12": 0.24},  # 原值
        {"0": 0.05, "2": 0.12, "12": 0.30},   # 当前
        {"0": 0.08, "2": 0.18, "12": 0.40},   # 宽松
        {"0": 0.10, "2": 0.25, "12": 0.50},   # 很宽松
    ]
    
    candles = gen_realistic_candles("up", 0.03, 14)
    indicators = calculate_nfi_indicators(candles, nfi)
    df = pd.DataFrame(indicators)
    latest = df.iloc[-1]
    
    print(f"\n上涨趋势测试 (波动0.03):")
    for i, th in enumerate(test_thresholds):
        test_nfi = {
            **nfi,
            "safe_dips": {
                "threshold_0": th["0"],
                "threshold_2": th["2"],
                "threshold_12": th["12"],
            }
        }
        ind = calculate_nfi_indicators(candles, test_nfi)
        d = pd.DataFrame(ind).iloc[-1]
        dips = bool(d.get("safe_dips", False))
        print(f"  阈值{i+1}: {th['0']}/{th['2']}/{th['12']} → safe_dips={dips}")
    
    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)
    
    passed = sum(1 for r in results if r["passed"])
    print(f"\n场景通过率: {passed}/{len(results)} = {passed/len(results)*100:.0f}%")
    
    if passed >= 3:
        print("→ 阈值适中，大部分有效场景可入场")
    elif passed == 0:
        print("→ 阈值过严，建议放宽 safe_dips")
    else:
        print("→ 阈值偏严，可适当放宽")


if __name__ == "__main__":
    main()
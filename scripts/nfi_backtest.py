"""
NFI风格回测脚本
用实盘交易历史 + Binance历史K线验证策略效果
"""

import json
import pandas as pd
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import run_nfi_surface_backtest, calculate_nfi_indicators
import json


def load_params() -> dict:
    with open("config/params.json") as f:
        return json.load(f)


def fetch_ohlcv(symbol: str, interval: str = "1h", limit: int = 200) -> List[Dict]:
    """从Binance获取K线数据"""
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": f"{symbol.upper()}USDT",
        "interval": interval,
        "limit": min(limit, 1000)
    }
    try:
        data = requests.get(url, params=params, timeout=10).json()
        if not data or not isinstance(data, list):
            return []
        
        candles = []
        for item in data:
            candles.append({
                "timestamp": item[0],
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
                "close_time": item[6]
            })
        return candles
    except Exception as e:
        print(f"获取{symbol} K线失败: {e}")
        return []


def load_live_trades() -> List[Dict]:
    """加载实盘交易记录"""
    with open("trading/live_trades.json") as f:
        return json.load(f)


def analyze_trade_with_nfi(trade: Dict, candles: List[Dict]) -> Dict:
    """用NFI面分析审查一笔交易"""
    if len(candles) < 50:
        return {"error": "数据不足"}
    
    params = load_params()
    nfi_params = params.get("nfi_protection_params", {
        "ema_periods": [8, 20, 50, 200],
        "rsi_periods": [4, 14, 84],
        "lookback_periods": {"ema_trend": 3, "rsi_recovery": 2},
        "safe_dips": {"threshold_0": 0.032, "threshold_2": 0.09, "threshold_12": 0.24},
        "safe_pump": {"threshold_24h": 0.75}
    })
    
    try:
        indicators = calculate_nfi_indicators(candles, nfi_params)
        df = pd.DataFrame(indicators)
        
        if len(df) < 10:
            return {"error": "DataFrame太短"}
        
        latest = df.iloc[-1]
        
        result = {
            "symbol": trade.get("token"),
            "entry_time": trade.get("timestamp"),
            "exit_time": trade.get("exit_timestamp"),
            "win": trade.get("win", False),
            "pnl": trade.get("pnl", 0),
            "nfi_checks": {
                "ema_trend_rising": bool(latest.get("ema_trend_rising", False)),
                "rsi_recovering": bool(latest.get("rsi_recovering", False)),
                "safe_dips": bool(latest.get("safe_dips", False)),
                "safe_pump": bool(latest.get("safe_pump", False)),
            }
        }
        
        # 综合判断
        protections = result["nfi_checks"]["safe_dips"] or result["nfi_checks"]["safe_pump"]
        result["nfi_passed"] = (
            result["nfi_checks"]["ema_trend_rising"] and 
            result["nfi_checks"]["rsi_recovering"] and
            protections
        )
        
        return result
        
    except Exception as e:
        return {"error": str(e), "symbol": trade.get("token")}


def main():
    print("=" * 60)
    print("NFI风格回测分析")
    print("=" * 60)
    
    trades = load_live_trades()
    completed = [t for t in trades if t.get("type") == "EXIT" and t.get("exit_reason") != "manual_reset"]
    
    print(f"\n加载 {len(completed)} 笔实盘交易\n")
    
    results = []
    for i, trade in enumerate(completed):
        symbol = trade.get("token")
        print(f"[{i+1}/{len(completed)}] 分析 {symbol}...")
        
        candles = fetch_ohlcv(symbol, interval="1h", limit=200)
        
        if not candles:
            print(f"  ✗ 无法获取K线数据")
            continue
        
        analysis = analyze_trade_with_nfi(trade, candles)
        
        if "error" in analysis:
            print(f"  ✗ 分析失败: {analysis['error']}")
            continue
        
        results.append(analysis)
        
        status = "✓" if analysis["nfi_passed"] else "✗"
        print(f"  {status} EMA趋势:{analysis['nfi_checks']['ema_trend_rising']} "
              f"RSI恢复:{analysis['nfi_checks']['rsi_recovering']} "
              f"安全回调:{analysis['nfi_checks']['safe_dips']} "
              f"结果:{'盈利' if analysis['win'] else '亏损'}")
    
    print("\n" + "=" * 60)
    print("回测结果汇总")
    print("=" * 60)
    
    if not results:
        print("无有效分析结果")
        return
    
    nfi_passed = [r for r in results if r["nfi_passed"]]
    nfi_failed = [r for r in results if not r["nfi_passed"]]
    
    print(f"\n总分析交易: {len(results)}")
    print(f"NFI通过: {len(nfi_passed)} | NFI失败: {len(nfi_failed)}")
    
    if nfi_passed:
        wins_passed = sum(1 for r in nfi_passed if r["win"])
        print(f"\nNFI通过交易:")
        print(f"  胜率: {wins_passed}/{len(nfi_passed)} = {wins_passed/len(nfi_passed)*100:.1f}%")
        print(f"  盈利: {sum(r['pnl'] for r in nfi_passed):.2f}U")
    
    if nfi_failed:
        wins_failed = sum(1 for r in nfi_failed if r["win"])
        print(f"\nNFI失败交易:")
        print(f"  胜率: {wins_failed}/{len(nfi_failed)} = {wins_failed/len(nfi_failed)*100:.1f}%")
        print(f"  盈利: {sum(r['pnl'] for r in nfi_failed):.2f}U")
    
    print("\n" + "-" * 60)
    print("NFI信号命中率统计")
    print("-" * 60)
    
    signal_stats = {
        "ema_trend_rising": {"hits": 0, "wins": 0},
        "rsi_recovering": {"hits": 0, "wins": 0},
        "safe_dips": {"hits": 0, "wins": 0},
        "safe_pump": {"hits": 0, "wins": 0},
    }
    
    for r in results:
        for sig_name in signal_stats.keys():
            if r["nfi_checks"].get(sig_name, False):
                signal_stats[sig_name]["hits"] += 1
                if r["win"]:
                    signal_stats[sig_name]["wins"] += 1
    
    print(f"\n{'信号':<20} {'命中':<8} {'盈利次数':<10} {'胜率':<10}")
    print("-" * 50)
    for sig, stats in signal_stats.items():
        hit_rate = stats["wins"]/stats["hits"]*100 if stats["hits"] > 0 else 0
        print(f"{sig:<20} {stats['hits']:<8} {stats['wins']:<10} {hit_rate:.1f}%")
    
    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)
    
    if nfi_passed and nfi_failed:
        win_rate_passed = sum(1 for r in nfi_passed if r["win"]) / len(nfi_passed)
        win_rate_failed = sum(1 for r in nfi_failed if r["win"]) / len(nfi_failed)
        
        if win_rate_passed > win_rate_failed:
            print(f"✓ NFI过滤有效! 通过组胜率({win_rate_passed*100:.1f}%) > 失败组({win_rate_failed*100:.1f}%)")
        else:
            print(f"✗ NFI过滤效果不明显，需要调整参数")
    elif nfi_passed and not nfi_failed:
        print("⚠ 所有交易都通过NFI检查，需要放宽条件")
    elif not nfi_passed and nfi_failed:
        print("⚠ 所有交易都被NFI过滤，需要收紧条件或收集更多样本")


if __name__ == "__main__":
    main()
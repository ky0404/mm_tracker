"""
NFI回测 - 真实OKX历史K线 + 实盘交易验证
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
    """从OKX获取历史K线"""
    # OKX bar格式: 1m, 3m, 5m, 15m, 30m, 1H, 2H, 4H, 6H, 12H, 1D, 1W, 1M
    url = "https://www.okx.com/api/v5/market/history-candles"
    params = {
        "instId": f"{symbol}-USDT-SWAP",
        "bar": bar,
        "limit": limit
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        
        if data.get("code") != "0" or not data.get("data"):
            # 尝试备用格式
            if bar == "1h":
                params["bar"] = "1H"
                resp = requests.get(url, params=params, timeout=15)
                data = resp.json()
        
        if data.get("code") != "0" or not data.get("data"):
            print(f"  ✗ {symbol} API错误: {data.get('msg', 'unknown')}")
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
        return candles[::-1]  # 转成正序
        
    except Exception as e:
        print(f"  ✗ {symbol} 请求失败: {e}")
        return None


def main():
    print("=" * 60)
    print("NFI回测 - OKX真实数据")
    print("=" * 60)
    
    # 加载实盘交易
    with open("trading/live_trades.json") as f:
        trades = json.load(f)
    
    completed = [t for t in trades if t.get("type") == "EXIT" and t.get("exit_reason") != "manual_reset"]
    
    print(f"\n共 {len(completed)} 笔实盘交易，尝试拉取K线...\n")
    
    params = load_params()
    nfi_params = params.get("nfi_protection_params", {})
    
    results = []
    
    for i, trade in enumerate(completed):
        token = trade.get("token")
        entry_time = trade.get("timestamp", "")
        entry_price = trade.get("entry_price", 0)
        
        print(f"[{i+1}/{len(completed)}] {token}...", end=" ")
        
        # 拉取1小时K线
        candles = fetch_okx_candles(token, "1H", 200)
        
        if not candles or len(candles) < 50:
            print("跳过 (数据不足)")
            continue
        
        # 找到入场时间点
        entry_ts = None
        if entry_time:
            try:
                entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                entry_ts = int(entry_dt.timestamp() * 1000)
            except:
                pass
        
        # 取入场前30根K线 + 入场后数据
        if entry_ts:
            # 找到最接近入场时间的K线索引
            for idx, c in enumerate(candles):
                if c["timestamp"] >= entry_ts:
                    start_idx = max(0, idx - 30)
                    candles = candles[start_idx:idx+20]  # 入场前30根 + 入场后20根
                    break
        
        # 计算NFI指标
        try:
            indicators = calculate_nfi_indicators(candles, nfi_params)
            df = pd.DataFrame(indicators)
            
            if len(df) < 10:
                print("数据不足")
                continue
            
            # 取入场时刻的信号 (倒数第20根左右，即入场时刻)
            entry_idx = max(0, len(df) - 25)  # 入场点
            
            # 方案A: 只看入场时刻
            # row = df.iloc[entry_idx] if entry_idx < len(df) else df.iloc[-1]
            
            # 方案B: 看入场后24小时内是否有任一时刻EMA恢复
            # 检查入场后24根K线 (1小时K线 = 24根)
            post_entry = df.iloc[entry_idx:min(entry_idx+24, len(df))]
            
            # 任一时刻EMA恢复就算通过
            ema_recovered = post_entry["ema_trend_rising"].any() if len(post_entry) > 0 else False
            rsi_ok = post_entry["rsi_recovering"].any() if len(post_entry) > 0 else False
            safe_ok = (post_entry["safe_dips"].any() or post_entry["safe_pump"].any()) if len(post_entry) > 0 else False
            
            row = df.iloc[-1]  # 最新数据用于 safe_dips/pump 计算
            
            result = {
                "token": token,
                "win": trade.get("win", False),
                "pnl": trade.get("pnl", 0),
                "ema_trend": ema_recovered,
                "rsi_recover": rsi_ok,
                "safe_dips": bool(row.get("safe_dips", False)),
                "safe_pump": bool(row.get("safe_pump", False)),
            }
            result["nfi_passed"] = (
                result["ema_trend"] and 
                result["rsi_recover"] and 
                (result["safe_dips"] or result["safe_pump"])
            )
            results.append(result)
            
            status = "✓" if result["nfi_passed"] else "✗"
            outcome = "盈利" if result["win"] else "亏损"
            print(f"{status} EMA={result['ema_trend']} RSI={result['rsi_recover']} 回调={result['safe_dips']} → {outcome}")
            
        except Exception as e:
            print(f"分析失败: {e}")
    
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    
    if not results:
        print("无有效数据")
        return
    
    nfi_passed = [r for r in results if r["nfi_passed"]]
    nfi_failed = [r for r in results if not r["nfi_passed"]]
    
    passed_wins = sum(1 for r in nfi_passed if r["win"])
    failed_wins = sum(1 for r in nfi_failed if r["win"])
    
    print(f"\n总分析: {len(results)}笔")
    print(f"NFI通过: {len(nfi_passed)}笔 | NFI拒绝: {len(nfi_failed)}笔")
    
    if nfi_passed:
        print(f"  通过组胜率: {passed_wins}/{len(nfi_passed)} = {passed_wins/len(nfi_passed)*100:.0f}%")
    if nfi_failed:
        print(f"  拒绝组胜率: {failed_wins}/{len(nfi_failed)} = {failed_wins/len(nfi_failed)*100:.0f}%")
    
    # 信号统计
    print("\n信号命中率:")
    for sig in ["ema_trend", "rsi_recover", "safe_dips", "safe_pump"]:
        hits = sum(1 for r in results if r.get(sig, False))
        wins = sum(1 for r in results if r.get(sig, False) and r["win"])
        rate = wins/hits*100 if hits else 0
        print(f"  {sig}: {hits}次, 胜率{rate:.0f}%")
    
    # 结论
    print("\n" + "=" * 60)
    if nfi_passed and nfi_failed:
        wr_pass = passed_wins/len(nfi_passed)*100
        wr_fail = failed_wins/len(nfi_failed)*100
        if wr_pass > wr_fail:
            print(f"✓ NFI过滤有效! 通过组({wr_pass:.0f}%) > 拒绝组({wr_fail:.0f}%)")
        else:
            print(f"○ NFI过滤效果不明显")
    elif nfi_passed:
        print("⚠ 所有交易都通过NFI")
    elif nfi_failed:
        print("⚠ 所有交易都被NFI拒绝，需调整参数")


if __name__ == "__main__":
    main()
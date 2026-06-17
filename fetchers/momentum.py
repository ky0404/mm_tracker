"""
动量信号获取器 - 基于OKX实时数据
专为短中线方向交易设计
不依赖CoinGecko
"""
import requests
import time
from typing import Dict, List, Any, Optional

def get_okx_price(symbol: str) -> Optional[float]:
    """直接从OKX获取实时价格"""
    try:
        url = "https://www.okx.com/api/v5/market/ticker"
        resp = requests.get(url, params={"instId": f"{symbol}-USDT-SWAP"}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return float(data["data"][0]["last"])
    except:
        pass
    return None

def get_hourly_momentum(symbol: str) -> Dict[str, Any]:
    """
    获取过去1小时的价格动量和成交量
    
    返回:
    {
      "price_change_1h_pct": float,   # 1小时涨跌幅
      "volume_ratio_1h": float,        # 当前1H成交量/过去6H均量
      "current_price": float,
      "is_bullish_momentum": bool      # 是否满足做多动量条件
    }
    """
    try:
        url = "https://www.okx.com/api/v5/market/history-candles"
        # 获取过去8根1小时K线
        params = {
            "instId": f"{symbol.upper()}-USDT-SWAP",
            "bar": "1H",
            "limit": "8"
        }
        resp = requests.get(url, params=params, timeout=8)
        if resp.status_code != 200:
            return {"error": "API failed", "is_bullish_momentum": False}
        
        data = resp.json()
        if data.get("code") != "0" or not data.get("data"):
            return {"error": "no data", "is_bullish_momentum": False}
        
        candles = data["data"]  # 最新的在前
        # 格式: [ts, open, high, low, close, vol, volCcy, ...]
        
        if len(candles) < 7:
            return {"error": "insufficient data", "is_bullish_momentum": False}
        
        # 最新一根K线（当前1H）
        latest = candles[0]
        latest_open = float(latest[1])
        latest_close = float(latest[4])
        latest_vol = float(latest[5])
        
        # 过去6根K线的成交量均值
        past_vols = [float(c[5]) for c in candles[1:7]]
        avg_vol_6h = sum(past_vols) / len(past_vols) if past_vols else 1
        
        # 计算1H价格变化
        price_change_1h_pct = (latest_close - latest_open) / latest_open * 100 if latest_open > 0 else 0
        
        # 成交量比率
        volume_ratio_1h = latest_vol / avg_vol_6h if avg_vol_6h > 0 else 1
        
        # 也检查2H内的整体方向（用前2根K线）
        if len(candles) >= 3:
            price_2h_ago = float(candles[2][1])  # 2小时前的open
            trend_2h = (latest_close - price_2h_ago) / price_2h_ago * 100 if price_2h_ago > 0 else 0
        else:
            trend_2h = price_change_1h_pct
        
        return {
            "price_change_1h_pct": round(price_change_1h_pct, 2),
            "trend_2h_pct": round(trend_2h, 2),
            "volume_ratio_1h": round(volume_ratio_1h, 2),
            "current_price": latest_close,
            "current_volume": latest_vol,
            "avg_volume_6h": avg_vol_6h,
            "is_bullish_momentum": (
                price_change_1h_pct >= 2.0 and     # 1H涨幅≥2%
                volume_ratio_1h >= 1.5 and          # 成交量放大1.5倍
                trend_2h >= 1.5                      # 2H整体向上
            ),
            "momentum_score": min(
                (price_change_1h_pct / 2.0) * 0.5 +
                (volume_ratio_1h / 1.5) * 0.5,
                2.0
            )
        }
    except Exception as e:
        return {"error": str(e), "is_bullish_momentum": False}

def scan_momentum_universe(top_n: int = 10) -> List[Dict[str, Any]]:
    """
    扫描OKX所有SWAP，找出过去1H动量最强的代币
    
    流程：
    1. 获取全量tickers（单次请求）
    2. 按1H价格变化排序
    3. 取Top N个做多候选
    4. 过滤掉过热的（资金费率过高）
    """
    try:
        # 获取全量行情
        url = "https://www.okx.com/api/v5/market/tickers"
        resp = requests.get(url, params={"instType": "SWAP"}, timeout=10)
        if resp.status_code != 200:
            return []
        
        data = resp.json()
        if data.get("code") != "0":
            return []
        
        tickers = data.get("data", [])
        
        candidates = []
        for t in tickers:
            inst_id = t.get("instId", "")
            if not inst_id.endswith("-USDT-SWAP"):
                continue
            
            symbol = inst_id.replace("-USDT-SWAP", "")
            
            # 排除主流大币（波动太小，杠杆效率低）
            if symbol in ["BTC", "ETH", "BNB", "XRP", "SOL", "ADA", "DOGE"]:
                continue
            
            try:
                last_price = float(t.get("last", 0))
                open_24h = float(t.get("open24h", 0))
                vol_24h_usd = float(t.get("volCcy24h", 0))
                
                if last_price <= 0 or open_24h <= 0:
                    continue
                
                # 过滤：成交量太低的不考虑（流动性差）
                if vol_24h_usd < 500_000:  # $50万以下不考虑
                    continue
                
                # 过滤：24H涨幅已经超过25%的可能已经过热
                if open_24h > 0:
                    change_24h_pct = (last_price - open_24h) / open_24h * 100
                else:
                    change_24h_pct = 0
                
                if change_24h_pct > 25:
                    continue
                
                # 过滤：价格范围（太小的币容易被操控）
                if last_price < 0.0001 or last_price > 1000:
                    continue
                
                candidates.append({
                    "symbol": symbol,
                    "price": last_price,
                    "change_24h_pct": round(change_24h_pct, 2),
                    "vol_24h_usd": vol_24h_usd,
                })
            except:
                continue
        
        # 按24H涨幅排序，取涨幅适中的（不能太大也不能太小）
        # 我们要找的是"正在启动但还没走完"的代币
        # 24H涨幅在3%-25%之间且成交量放大
        momentum_candidates = [
            c for c in candidates
            if 2.0 <= c["change_24h_pct"] <= 20.0
        ]
        
        # 按涨幅降序
        momentum_candidates.sort(key=lambda x: x["change_24h_pct"], reverse=True)
        
        print(f"[Momentum] 找到 {len(momentum_candidates)} 个候选，取前 {top_n} 个进行1H分析")
        
        # 取前N个进行1H分析（避免太多API请求）
        top_candidates = momentum_candidates[:top_n]
        
        # 获取每个候选的1H动量数据
        final_candidates = []
        for cand in top_candidates:
            symbol = cand["symbol"]
            momentum = get_hourly_momentum(symbol)
            cand.update(momentum)
            
            # 添加资金费率检查
            cand["funding_ok"] = True  # 默认OK，后续可以加精确检查
            
            final_candidates.append(cand)
            time.sleep(0.1)  # 避免限速
        
        # 按动量分数排序
        final_candidates.sort(
            key=lambda x: x.get("momentum_score", 0) * (1 if x.get("is_bullish_momentum") else 0.1),
            reverse=True
        )
        
        return final_candidates
    
    except Exception as e:
        print(f"[Momentum] 扫描失败: {e}")
        return []
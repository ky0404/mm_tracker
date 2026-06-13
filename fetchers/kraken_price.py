"""
价格数据获取器 - 使用可访问的 API
替代被封锁的 Binance API
"""

import requests
import logging
from typing import Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


def fetch_price_from_coinbase(symbol: str) -> dict:
    """
    从 Coinbase 获取价格
    
    Args:
        symbol: 代币符号，如 BTC, ETH
        
    Returns:
        {"price": float, "change_24h": float}
    """
    symbol = symbol.upper()
    
    # Coinbase API 格式
    try:
        url = f"https://api.coinbase.com/v2/prices/{symbol}-USD/spot"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        price = float(data.get("data", {}).get("amount", 0))
        
        print(f"[Coinbase] ✓ {symbol} 价格获取成功 (${price:,.2f})")
        
        return {
            "price": price,
            "source": "coinbase"
        }
    except Exception as e:
        logger.error(f"Coinbase API 失败: {e}")
        return {"price": 0, "error": str(e)}


def fetch_price_from_kraken(symbol: str) -> dict:
    """
    从 Kraken 获取价格
    
    Args:
        symbol: 代币符号，如 BTC, ETH
        
    Returns:
        {"price": float}
    """
    symbol = symbol.upper()
    
    # Kraken 交易对映射
    kraken_pairs = {
        "BTC": "XBTUSDT",
        "ETH": "ETHUSDT",
        "SOL": "SOLUSDT",
        "XRP": "XRPUSDT",
        "ADA": "ADAUSDT",
        "DOGE": "DOGEUSDT",
        "DOT": "DOTUSDT",
        "MATIC": "MATICUSDT",
        "AVAX": "AVAXUSDT",
        "LINK": "LINKUSDT",
    }
    
    pair = kraken_pairs.get(symbol, f"{symbol}USDT")
    
    try:
        url = f"https://api.kraken.com/0/public/Ticker?pair={pair}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        result = data.get("result", {})
        if result:
            # 获取第一个交易对的数据
            ticker = list(result.values())[0]
            price = float(ticker.get("c", [0])[0])  # 最后成交价
            
            print(f"[Kraken] ✓ {symbol} 价格获取成功 (${price:,.2f})")
            
            return {
                "price": price,
                "source": "kraken"
            }
        
        return {"price": 0, "error": "No result"}
    except Exception as e:
        logger.error(f"Kraken API 失败: {e}")
        return {"price": 0, "error": str(e)}


def fetch_klines_from_kraken(symbol: str, interval: str = "1d", limit: int = 30) -> List[dict]:
    """
    从 Kraken 获取 K 线数据（OHLC）
    
    Args:
        symbol: 代币符号
        interval: 周期 (1m, 5m, 15m, 1h, 4h, 1d, 1w)
        limit: 数量
        
    Returns:
        [{"timestamp":, "open":, "high":, "low":, "close":, "volume":}]
    """
    symbol = symbol.upper()
    
    # Kraken 交易对映射
    kraken_pairs = {
        "BTC": "XBTUSD",
        "ETH": "ETHUSD",
        "SOL": "SOLUSD",
    }
    
    pair = kraken_pairs.get(symbol, f"{symbol}USD")
    
    # Kraken 周期映射
    interval_map = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
        "1w": 10080,
    }
    
    interval_minutes = interval_map.get(interval, 1440)
    
    try:
        url = f"https://api.kraken.com/0/public/OHLC?pair={pair}&interval={interval_minutes}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        result = data.get("result", {})
        
        if not result:
            return []
        
        # 获取交易对数据（去掉 last 字段）
        ohlc_data = []
        for pair_key, pair_data in result.items():
            if pair_key == "last":
                continue
            
            for item in pair_data:
                if len(item) >= 6:
                    ohlc_data.append({
                        "timestamp": datetime.fromtimestamp(int(item[0])),
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[6]) if len(item) > 6 else 0,
                    })
        
        # 限制数量
        ohlc_data = ohlc_data[-limit:]
        
        if ohlc_data:
            print(f"[Kraken] ✓ {symbol} K线获取成功 ({len(ohlc_data)}根)")
        else:
            print(f"[Kraken] ✗ {symbol} K线为空")
        
        return ohlc_data
        
    except Exception as e:
        logger.error(f"Kraken K线 API 失败: {e}")
        return []


def fetch_current_price(symbol: str) -> Optional[float]:
    """
    获取当前价格（优先 Coinbase，备用 Kraken）
    """
    # 尝试 Coinbase
    result = fetch_price_from_coinbase(symbol)
    if result.get("price", 0) > 0:
        return result["price"]
    
    # 备用 Kraken
    result = fetch_price_from_kraken(symbol)
    if result.get("price", 0) > 0:
        return result["price"]
    
    return None


def fetch_daily_ohlcv(symbol: str, limit: int = 30) -> List[dict]:
    """
    获取日线 K 线数据（使用 Kraken）
    """
    return fetch_klines_from_kraken(symbol, "1d", limit)


def fetch_btc_daily(limit: int = 14) -> List[dict]:
    """
    获取 BTC 日线数据
    """
    return fetch_klines_from_kraken("BTC", "1d", limit)
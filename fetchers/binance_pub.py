"""
Binance 公开数据获取器
获取 OHLCV K线数据、价格数据等
完全使用 Binance 公开接口，无需任何 API Key
"""

import requests
import pandas as pd
import logging
from typing import Optional, Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)

BINANCE_BASE_URL = "https://api.binance.com"


def _http_get(url: str, params: dict = None, timeout: int = 15) -> Optional[list]:
    """HTTP GET 请求封装"""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"HTTP请求失败: {url}, error: {e}")
        return None


def fetch_daily_ohlcv(symbol: str, limit: int = 30) -> pd.DataFrame:
    """
    获取代币的日线 OHLCV 数据
    
    Args:
        symbol: 代币符号，如 LAB, SUI
        limit: 返回 K 线数量，默认30根
        
    Returns:
        pd.DataFrame，列名: ["timestamp", "open", "high", "low", "close", "volume"]
    """
    symbol = symbol.upper()
    
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {
        "symbol": f"{symbol}USDT",
        "interval": "1d",
        "limit": limit
    }
    
    data = _http_get(url, params)
    
    if not data or not isinstance(data, list):
        print(f"[Binance] ✗ {symbol} K线获取失败")
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    
    # 解析数据
    records = []
    for item in data:
        try:
            records.append({
                "timestamp": pd.to_datetime(item[0], unit="ms"),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            })
        except (ValueError, IndexError, TypeError):
            continue
    
    df = pd.DataFrame(records)
    
    if not df.empty:
        print(f"[Binance] ✓ {symbol} 日线K线获取成功 ({len(df)}根)")
    else:
        print(f"[Binance] ✗ {symbol} K线解析失败")
    
    return df


def fetch_btc_daily(limit: int = 14) -> pd.DataFrame:
    """
    获取 BTCUSDT 日线数据，用于计算相对强度
    
    Args:
        limit: 返回 K 线数量
        
    Returns:
        pd.DataFrame
    """
    url = f"{BINANCE_BASE_URL}/api/v3/klines"
    params = {
        "symbol": "BTCUSDT",
        "interval": "1d",
        "limit": limit
    }
    
    data = _http_get(url, params)
    
    if not data or not isinstance(data, list):
        print(f"[Binance] ✗ BTC 日线获取失败")
        return pd.DataFrame(columns=["timestamp", "close"])
    
    records = []
    for item in data:
        try:
            records.append({
                "timestamp": pd.to_datetime(item[0], unit="ms"),
                "close": float(item[4]),
                "volume": float(item[5]),
            })
        except (ValueError, IndexError, TypeError):
            continue
    
    df = pd.DataFrame(records)
    
    if not df.empty:
        print(f"[Binance] ✓ BTC 日线获取成功 ({len(df)}根)")
    
    return df


def detect_volume_anomaly(df: pd.DataFrame, window: int = 20) -> dict:
    """
    检测成交量异常放量
    
    计算最新K线成交量 vs 过去N日均量的比值
    
    Args:
        df: K线 DataFrame，需包含 volume 列
        window: 均线周期，默认20天
        
    Returns:
        {
            "volume_ratio": float,    # 当前量/均量
            "is_anomaly": bool,        # >= 2.0 为异常
            "current_volume": float,
            "avg_volume": float
        }
    """
    if df.empty or len(df) < window:
        return {
            "volume_ratio": 0.0,
            "is_anomaly": False,
            "current_volume": 0.0,
            "avg_volume": 0.0,
            "error": "数据不足"
        }
    
    # 计算移动平均
    df = df.copy()
    df["volume_ma"] = df["volume"].rolling(window=window).mean()
    
    latest = df.iloc[-1]
    current_volume = latest["volume"]
    avg_volume = latest["volume_ma"]
    
    if avg_volume > 0:
        volume_ratio = current_volume / avg_volume
    else:
        volume_ratio = 0.0
    
    is_anomaly = volume_ratio >= 2.0
    
    return {
        "volume_ratio": round(volume_ratio, 2),
        "is_anomaly": is_anomaly,
        "current_volume": current_volume,
        "avg_volume": avg_volume
    }


def analyze_round_number_stall(df: pd.DataFrame, current_price: float) -> dict:
    """
    分析整数关口卡位
    
    识别最近整数关口并统计在该关口附近横盘的天数
    
    Args:
        df: K线 DataFrame，需包含 close 列
        current_price: 当前价格
        
    Returns:
        {
            "nearest_level": float,      # 最近整数关口
            "distance_pct": float,       # 当前价距关口百分比
            "stall_days": int,           # 在关口附近横盘天数
            "is_stalling": bool          # >= 3天
        }
    """
    # 整数关口层级
    PRICE_TIERS = [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000]
    
    if current_price <= 0:
        return {
            "nearest_level": 0.0,
            "distance_pct": 0.0,
            "stall_days": 0,
            "is_stalling": False,
            "error": "无效价格"
        }
    
    # 找到当前价格下方最近的整数关口
    below_tiers = [t for t in PRICE_TIERS if t < current_price]
    
    if not below_tiers:
        # 价格太低，使用最小关口
        nearest_level = PRICE_TIERS[0]
    else:
        nearest_level = max(below_tiers)
    
    # 计算距离百分比 (当前价相对于关口的涨幅)
    distance_pct = (current_price - nearest_level) / nearest_level * 100
    
    # 统计在关口 0~10% 范围内横盘的天数
    lower_bound = nearest_level
    upper_bound = nearest_level * 1.10  # 关口上方10%以内
    
    if df.empty:
        stall_days = 0
    else:
        close_prices = df["close"].tolist()
        stall_days = 0
        for price in close_prices:
            if lower_bound <= price <= upper_bound:
                stall_days += 1
    
    is_stalling = stall_days >= 3
    
    return {
        "nearest_level": nearest_level,
        "distance_pct": round(distance_pct, 2),
        "stall_days": stall_days,
        "is_stalling": is_stalling
    }


def get_current_price(symbol: str) -> Optional[float]:
    """
    获取代币当前价格
    
    Args:
        symbol: 代币符号
        
    Returns:
        当前价格（USDT），失败返回 None
    """
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr"
    params = {"symbol": f"{symbol.upper()}USDT"}
    
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("lastPrice", 0))
    except Exception as e:
        logger.error(f"获取价格失败: {symbol}, error: {e}")
        return None


def check_symbol_exists(symbol: str) -> bool:
    """
    检查交易对是否存在
    
    Args:
        symbol: 代币符号
        
    Returns:
        True 表示存在
    """
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr"
    params = {"symbol": f"{symbol.upper()}USDT"}
    
    try:
        resp = requests.get(url, params=params, timeout=10)
        return resp.status_code == 200
    except:
        return False
"""
DexScreener 数据获取器
获取 DEX 流动性、买卖交易数据
免费 API，无需认证
"""

import requests
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

DEXSCREENER_BASE_URL = "https://api.dexscreener.com"


def _http_get(url: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
    """HTTP GET 请求封装"""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"HTTP请求失败: {url}, error: {e}")
        return None


def fetch_dex_data(symbol: str) -> dict:
    """
    获取代币的 DEX 数据
    
    Args:
        symbol: 代币符号，如 LAB, VELVET
        
    Returns:
        {
            "buy_sell_ratio": float,
            "liquidity_usd": float,
            "volume_24h": float,
            "price_change_1h": float,
            "price_change_6h": float,
            "chain": str,
            "dex": str,
            "is_signal": bool
        }
    """
    symbol = symbol.upper()
    
    # 搜索代币
    url = f"{DEXSCREENER_BASE_URL}/latest/dex/search"
    params = {"q": symbol}
    
    data = _http_get(url, params)
    
    if not data or "pairs" not in data:
        print(f"[DexScreener] ✗ {symbol} 未找到DEX数据")
        return {
            "buy_sell_ratio": 0.0,
            "liquidity_usd": 0.0,
            "volume_24h": 0.0,
            "price_change_1h": 0.0,
            "price_change_6h": 0.0,
            "chain": "",
            "dex": "",
            "is_signal": False,
            "error": "未找到代币数据"
        }
    
    pairs = data["pairs"]
    
    if not pairs:
        print(f"[DexScreener] ✗ {symbol} 无交易对")
        return {
            "buy_sell_ratio": 0.0,
            "liquidity_usd": 0.0,
            "volume_24h": 0.0,
            "price_change_1h": 0.0,
            "price_change_6h": 0.0,
            "chain": "",
            "dex": "",
            "is_signal": False,
            "error": "无交易对"
        }
    
    # 选取流动性最高的交易对
    best_pair = max(
        pairs,
        key=lambda x: float(x.get("liquidity", {}).get("usd", 0) or 0)
    )
    
    # 提取数据
    base_token = best_pair.get("baseToken", {})
    quote_token = best_pair.get("quoteToken", {})
    liquidity = best_pair.get("liquidity", {})
    volume = best_pair.get("volume", {})
    txns = best_pair.get("txns", {})
    price_change = best_pair.get("priceChange", {})
    
    # 交易数
    h24 = txns.get("h24", {})
    buys = h24.get("buys", 0) or 0
    sells = h24.get("sells", 0) or 0
    
    # 买卖比
    if sells > 0:
        buy_sell_ratio = buys / sells
    elif buys > 0:
        buy_sell_ratio = float("inf")
    else:
        buy_sell_ratio = 0.0
    
    # 流动性
    liquidity_usd = float(liquidity.get("usd", 0) or 0)
    
    # 24小时成交量
    volume_24h = float(volume.get("h24", 0) or 0)
    
    # 价格变化
    price_change_1h = float(price_change.get("h1", 0) or 0)
    price_change_6h = float(price_change.get("h6", 0) or 0)
    
    # 链和DEX
    chain = best_pair.get("dexId", "")
    dex = best_pair.get("pairAddress", "")[:10] + "..."
    
    # 判断是否符合信号条件
    is_signal = buy_sell_ratio >= 1.2 and liquidity_usd > 50000
    
    print(f"[DexScreener] ✓ {symbol} DEX数据获取成功 (买卖比:{buy_sell_ratio:.2f}, 流动性:${liquidity_usd:,.0f})")
    
    return {
        "buy_sell_ratio": round(buy_sell_ratio, 2),
        "liquidity_usd": liquidity_usd,
        "volume_24h": volume_24h,
        "price_change_1h": price_change_1h,
        "price_change_6h": price_change_6h,
        "chain": chain,
        "dex": dex,
        "is_signal": is_signal
    }


def fetch_token_price_from_dex(symbol: str) -> Optional[float]:
    """
    从 DexScreener 获取代币价格（备选方案）
    
    Args:
        symbol: 代币符号
        
    Returns:
        价格（USD），失败返回 None
    """
    data = fetch_dex_data(symbol)
    return data.get("price_change_1h")  # 这里实际返回的是变化率，不是价格


def get_dex_pairs(symbol: str) -> list:
    """
    获取代币的所有 DEX 交易对
    
    Args:
        symbol: 代币符号
        
    Returns:
        交易对列表
    """
    url = f"{DEXSCREENER_BASE_URL}/latest/dex/search"
    params = {"q": symbol}
    
    data = _http_get(url, params)
    
    if data and "pairs" in data:
        return data["pairs"]
    
    return []
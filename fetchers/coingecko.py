"""
CoinGecko 数据获取器
获取价格、7日涨跌幅、市值等基础数据
免费 API，有频率限制
"""

import requests
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"

# symbol → CoinGecko ID 映射表（完整版）
SYMBOL_TO_ID = {
    # 主流币
    "btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin", "sol": "solana",
    "xrp": "ripple", "ada": "cardano", "doge": "dogecoin", "dot": "polkadot",
    "matic": "matic-network", "avax": "avalanche-2", "link": "chainlink",
    "near": "near", "apt": "aptos", "trx": "tron", "atom": "cosmos",
    "sui": "sui", "shib": "shiba-inu", "ltc": "litecoin", "uni": "uniswap",
    "xlm": "stellar", "arb": "arbitrum", "op": "optimism", "inj": "injective-protocol",
    "fil": "filecoin", "hbar": "hedera-hashgraph", "egld": "elrond-erd-2",
    "ftm": "fantom", "rune": "thorchain", "kava": "kava", "algorand": "algorand",
    "icp": "internet-computer", "vechain": "vechain", "aave": "aave",
    "gala": "the-sandbox", "sand": "the-sandbox", "mana": "decentraland",
    "axs": "axie-infinity", "theta": "theta-token", "ftt": "ftx-token",
    # Meme 币
    "pepe": "pepe", "wif": "dogwifcoin", "bonk": "bonk", "popcat": "popcat",
    "neiro": "neiro-3", "goat": "goatse-token", "meme": "memecoin-2",
    "ordi": "ordinals", "sats": "sats-ordinals", "fwog": "fwog",
    "retardio": "retardio", "vine": "vine", "giga": "gigachad-2",
    # 生态币
    "imx": "immutable-x", "sei": "sei-network", "TIA": "celestia",
    "sc": "siacoin", "hype": "hyperliquid", "not": "not-financial",
    "ai16z": "ai16z", "viral": "viral",
    # 项目币
    "lab": "lab", "far": "farcana", "alex": "alexgo", "allo": "allora",
    "aero": "aerodrome-finance", "boden": "jeo-boden", "cookie": "cookie",
    "wld": "worldcoin-wld", "blur": "blur", "agix": "singularitynet",
# 稳定币
    "usdt": "tether", "usdc": "usd-coin", "dai": "dai",
}


def _http_get(url: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
    """HTTP GET 请求封装"""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"HTTP请求失败: {url}, error: {e}")
        return None


def get_coin_id(symbol: str) -> Optional[str]:
    """
    将代币符号转换为 CoinGecko ID
    
    Args:
        symbol: 代币符号
        
    Returns:
        CoinGecko ID，失败返回 None
    """
    symbol = symbol.lower()
    return SYMBOL_TO_ID.get(symbol)


def fetch_price_and_change(symbol: str) -> dict:
    """
    获取代币当前价格和7日涨跌幅
    
    Args:
        symbol: 代币符号，如 LAB, SUI
        
    Returns:
        {
            "price": float,
            "change_7d_pct": float,
            "market_cap": float,
            "cg_id": str
        }
    """
    symbol = symbol.upper()
    cg_id = get_coin_id(symbol.lower())
    
    if not cg_id:
        print(f"[CoinGecko] ✗ {symbol} 未找到CG_ID映射")
        return {
            "price": 0.0,
            "change_7d_pct": 0.0,
            "market_cap": 0.0,
            "cg_id": "",
            "error": "未找到代币ID"
        }
    
    # 获取市场数据
    url = f"{COINGECKO_BASE_URL}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": cg_id,
        "order": "market_cap_desc",
        "per_page": 1,
        "page": 1,
        "sparkline": "false",
        "price_change_percentage": "7d"
    }
    
    data = _http_get(url, params)
    
    if not data or not isinstance(data, list) or len(data) == 0:
        print(f"[CoinGecko] ✗ {symbol} 市场数据获取失败")
        return {
            "price": 0.0,
            "change_7d_pct": 0.0,
            "market_cap": 0.0,
            "cg_id": cg_id,
            "error": "API返回空数据"
        }
    
    coin_data = data[0]
    
    price = float(coin_data.get("current_price", 0) or 0)
    market_cap = float(coin_data.get("market_cap", 0) or 0)
    change_7d = float(coin_data.get("price_change_percentage_7d_in_currency", 0) or 0)
    
    print(f"[CoinGecko] ✓ {symbol} 价格数据获取成功 (${price:.6f}, 7日{change_7d:+.2f}%)")
    
    return {
        "price": price,
        "change_7d_pct": change_7d,
        "market_cap": market_cap,
        "cg_id": cg_id
    }


def fetch_token_info(symbol: str) -> dict:
    """
    获取代币详细信息
    
    Args:
        symbol: 代币符号
        
    Returns:
        代币详细信息字典
    """
    symbol = symbol.upper()
    cg_id = get_coin_id(symbol.lower())
    
    if not cg_id:
        return {"error": "未找到代币ID"}
    
    url = f"{COINGECKO_BASE_URL}/coins/{cg_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
    }
    
    data = _http_get(url, params)
    
    if not data:
        return {"error": "获取详情失败"}
    
    market_data = data.get("market_data", {})
    
    return {
        "cg_id": cg_id,
        "symbol": data.get("symbol", "").upper(),
        "name": data.get("name", ""),
        "image": data.get("image", {}).get("large", ""),
        "price": market_data.get("current_price", {}).get("usd", 0),
        "market_cap": market_data.get("market_cap", {}).get("usd", 0),
        "volume_24h": market_data.get("total_volume", {}).get("usd", 0),
        "change_24h": market_data.get("price_change_percentage_24h", 0),
        "change_7d": market_data.get("price_change_percentage_7d", 0),
        "ath": market_data.get("ath", {}).get("usd", 0),
        "atl": market_data.get("atl", {}).get("usd", 0),
    }


def get_top_coins(limit: int = 100) -> list:
    """
    获取市值排名前N的代币
    
    Args:
        limit: 返回数量
        
    Returns:
        代币列表
    """
    url = f"{COINGECKO_BASE_URL}/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": limit,
        "page": 1,
        "sparkline": "false"
    }
    
    data = _http_get(url, params)
    
    if data and isinstance(data, list):
        return data
    
    return []
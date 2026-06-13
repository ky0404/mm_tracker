"""
Binance 合约检测器
检测目标币种是否已在 Binance 上线永续合约
使用 Binance 公开 Futures API，无需 API Key
"""

import requests
import logging
from typing import Optional, Dict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BINANCE_FUTURES_URL = "https://fapi.binance.com"


def _http_get(url: str, params: dict = None, timeout: int = 15) -> Optional[dict]:
    """HTTP GET 请求封装"""
    try:
        resp = requests.get(url, params=params, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"HTTP请求失败: {url}, error: {e}")
        return None


# 缓存合约列表，避免频繁请求
_exchange_info_cache = None
_cache_timestamp = None
CACHE_TTL_SECONDS = 300  # 5分钟缓存


def get_exchange_info(force_refresh: bool = False) -> dict:
    """
    获取 Binance Futures 合约列表
    
    Args:
        force_refresh: 强制刷新缓存
        
    Returns:
        合约列表数据
    """
    global _exchange_info_cache, _cache_timestamp
    
    now = datetime.now()
    
    # 检查缓存是否有效
    if not force_refresh and _exchange_info_cache and _cache_timestamp:
        elapsed = (now - _cache_timestamp).total_seconds()
        if elapsed < CACHE_TTL_SECONDS:
            return _exchange_info_cache
    
    # 重新获取
    url = f"{BINANCE_FUTURES_URL}/fapi/v1/exchangeInfo"
    data = _http_get(url)
    
    if data:
        _exchange_info_cache = data
        _cache_timestamp = now
        print(f"[FuturesChecker] ✓ 合约列表更新成功")
        return data
    
    print(f"[FuturesChecker] ✗ 合约列表获取失败")
    return {}


def check_futures_contract(symbol: str) -> dict:
    """
    检查代币是否有永续合约
    
    Args:
        symbol: 代币符号，如 LAB, SUI
        
    Returns:
        {
            "has_futures": bool,
            "days_since_listing": int,    # 上线天数，-1表示未上线
            "contract_type": str,         # "PERPETUAL" 或 "DELIVERY"
            "is_recent": bool             # 上线 <= 30天 算最近
        }
    """
    symbol = symbol.upper()
    pair = f"{symbol}USDT"
    
    exchange_info = get_exchange_info()
    
    if not exchange_info or "symbols" not in exchange_info:
        print(f"[FuturesChecker] ✗ {symbol} 合约检测失败")
        return {
            "has_futures": False,
            "days_since_listing": -1,
            "contract_type": "",
            "is_recent": False,
            "error": "无法获取合约列表"
        }
    
    # 查找匹配的合约
    for contract in exchange_info["symbols"]:
        if contract.get("symbol") == pair:
            status = contract.get("status", "")
            
            # 只接受 TRADING 状态
            if status != "TRADING":
                continue
            
            contract_type = contract.get("contractType", "")
            onboard_date = contract.get("onboardDate", 0)
            
            # 计算上线天数
            days_since_listing = -1
            if onboard_date and isinstance(onboard_date, int) and onboard_date > 0:
                # onboardDate 是毫秒时间戳
                listing_date = datetime.fromtimestamp(onboard_date / 1000)
                days_since_listing = (datetime.now() - listing_date).days
            
            # 判断是否最近上线（30天内）
            is_recent = 0 <= days_since_listing <= 30
            
            print(f"[FuturesChecker] ✓ {symbol} 合约检测成功 (上线{days_since_listing}天)")
            
            return {
                "has_futures": True,
                "days_since_listing": days_since_listing,
                "contract_type": contract_type,
                "is_recent": is_recent
            }
    
    # 未找到合约
    print(f"[FuturesChecker] ○ {symbol} 无永续合约")
    return {
        "has_futures": False,
        "days_since_listing": -1,
        "contract_type": "",
        "is_recent": False
    }


def get_contract_status(symbol: str) -> Optional[str]:
    """
    获取合约状态
    
    Args:
        symbol: 代币符号
        
    Returns:
        状态字符串，如 "TRADING", "PENDING"，None 表示不存在
    """
    symbol = symbol.upper()
    pair = f"{symbol}USDT"
    
    exchange_info = get_exchange_info()
    
    if not exchange_info or "symbols" not in exchange_info:
        return None
    
    for contract in exchange_info["symbols"]:
        if contract.get("symbol") == pair:
            return contract.get("status")
    
    return None


def list_all_perpetual_contracts() -> list:
    """
    获取所有永续合约列表
    
    Returns:
        合约符号列表
    """
    exchange_info = get_exchange_info()
    
    if not exchange_info or "symbols" not in exchange_info:
        return []
    
    perpetual_contracts = []
    
    for contract in exchange_info["symbols"]:
        if contract.get("status") == "TRADING":
            symbol = contract.get("symbol", "")
            if symbol.endswith("USDT") and contract.get("contractType") == "PERPETUAL":
                perpetual_contracts.append(symbol)
    
    return perpetual_contracts


def clear_cache():
    """清除缓存，强制下次重新获取"""
    global _exchange_info_cache, _cache_timestamp
    _exchange_info_cache = None
    _cache_timestamp = None
    print("[FuturesChecker] 缓存已清除")
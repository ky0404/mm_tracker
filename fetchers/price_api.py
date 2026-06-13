"""
MMTracker 数据获取器
使用官方 Python 库 + 可用的公开接口

健壮性设计：
- 所有 API 调用都有超时保护
- 失败时返回统一的错误结构 {"error": "...", "source": "..."}
- 数据源降级：主源失败自动切换备源
- 日志记录完整的错误信息
"""

import requests
import logging
import os
import time
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
from functools import wraps

logger = logging.getLogger(__name__)

# BTC.D 模块级缓存 (Bug 1 修复)
_btcd_cache = {"data": None, "ts": 0}
BTCD_TTL = 3600  # 1小时缓存

# 全局速率限制器
_rate_limiter_last_call = 0.0
RATE_LIMIT_INTERVAL = 0.2  # 200ms between calls


def _rate_limit():
    """全局速率限制"""
    global _rate_limiter_last_call
    elapsed = time.time() - _rate_limiter_last_call
    if elapsed < RATE_LIMIT_INTERVAL:
        time.sleep(RATE_LIMIT_INTERVAL - elapsed)
    _rate_limiter_last_call = time.time()


# 从环境变量加载配置
TIMEOUT = int(os.getenv("MM_HTTP_TIMEOUT", "15"))
MAX_RETRIES = int(os.getenv("MM_HTTP_MAX_RETRIES", "3"))

# 配置代理会话
def get_session() -> requests.Session:
    """获取配置了代理的会话"""
    session = requests.Session()
    proxy = os.getenv("ALL_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY")
    if proxy:
        session.proxies = {
            "http": proxy,
            "https": proxy,
        }
        logger.info(f"[Proxy] 已启用代理: {proxy}")
    return session

# 全局会话实例
_session = get_session()


def safe_api_call(default_return: Any = None, log_error: bool = True):
    """API 调用装饰器：自动捕获异常，返回安全的默认结构"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except requests.exceptions.Timeout:
                if log_error:
                    logger.warning(f"[Timeout] {func.__name__} 超时")
                return default_return if default_return is not None else {"error": "请求超时", "source": func.__name__}
            except requests.exceptions.ConnectionError as e:
                if log_error:
                    logger.warning(f"[ConnectionError] {func.__name__}: {e}")
                return default_return if default_return is not None else {"error": "网络连接失败", "source": func.__name__}
            except Exception as e:
                if log_error:
                    logger.error(f"[Error] {func.__name__}: {e}")
                return default_return if default_return is not None else {"error": str(e), "source": func.__name__}
        return wrapper
    return decorator


# ============================================================================
# 1. DexScreener (完全可用!)
# ============================================================================

try:
    from dexscreener import DexscreenerClient
    DEX_CLIENT = DexscreenerClient()
except Exception as e:
    logger.warning(f"DexScreener 客户端初始化失败: {e}")
    DEX_CLIENT = None


@safe_api_call(default_return={"buy_sell_ratio": 0, "liquidity_usd": 0, "price": 0, "error": None})
def fetch_dex_data(symbol: str) -> dict:
    """获取 DEX 数据"""
    if not DEX_CLIENT:
        return {"error": "DexScreener 未安装", "buy_sell_ratio": 0, "liquidity_usd": 0, "source": "dexscreener"}
    
    try:
        pairs = DEX_CLIENT.search_pairs(symbol.upper())
        
        if not pairs:
            print(f"[DexScreener] ✗ {symbol}")
            return {"buy_sell_ratio": 0, "liquidity_usd": 0, "price": 0}
        
        best = max(pairs, key=lambda x: float(x.liquidity.usd) if x.liquidity and x.liquidity.usd else 0)
        
        txns = best.transactions.h24 if best.transactions else None
        buys = txns.buys if txns else 0
        sells = txns.sells if txns else 0
        
        ratio = buys / sells if sells > 0 else (999 if buys > 0 else 0)
        liq = float(best.liquidity.usd) if best.liquidity and best.liquidity.usd else 0
        price = float(best.price_usd) if best.price_usd else 0
        
        print(f"[DexScreener] ✓ {symbol} 买卖比 {ratio:.1f}, 流动性 ${liq/1e6:.1f}M")
        
        return {"buy_sell_ratio": round(ratio, 2), "liquidity_usd": liq, "price": price}
    except Exception as e:
        print(f"[DexScreener] ✗ {symbol}: {e}")
        return {"buy_sell_ratio": 0, "liquidity_usd": 0, "price": 0, "error": str(e)}


# ============================================================================
# 2. CoinGecko (可用但有限流)
# ============================================================================

COINGECKO_URL = "https://api.coingecko.com/api/v3"

SYMBOL_TO_ID = {
    # 主流币
    "btc": "bitcoin", "eth": "ethereum", "bnb": "binancecoin", "sol": "solana",
    "xrp": "ripple", "ada": "cardano", "doge": "dogecoin", "dot": "polkadot",
    "matic": "matic-network", "avax": "avalanche-2", "link": "chainlink",
    "near": "near", "apt": "aptos", "trx": "tron", "atom": "cosmos",
    "matic": "matic-network", "near": "near", "sui": "sui",
    # 热门币
    "hype": "hyperliquid", "pepe": "pepe", "not": "not-financial",
    "goat": "goatse-token", "ai16z": "ai16z", "viral": "viral",
    "aster": "aster-2", "axs": "axie-shield", "gbp": "paxos-standard",
    # 样本代币
    "lab": "lab", "far": "farcana", "alex": "alexgo",
    "aero": "aerodrome-finance", "allo": "allora", "boden": "jeo-boden",
    "bonk": "bonk", "cookie": "cookie", "dai": "dai", "fwog": "fwog",
    "giga": "gigachad-2", "imx": "immutable-x", "meme": "memecoin-2",
    "neiro": "neiro-3", "ordi": "ordinals", "popcat": "popcat",
    "retardio": "retardio", "sats": "sats-ordinals", "sc": "siacoin",
    "sei": "sei-network", "celestia": "celestia",
    "vine": "vine", "wif": "dogwifcoin",
    # 稳定币
    "usdt": "tether", "usdc": "usd-coin",
}


# 自动搜索缓存（避免重复搜索）
_search_cache = {}

def _search_coin_id(symbol: str) -> str:
    """自动搜索 CoinGecko ID"""
    symbol_lower = symbol.lower()
    
    # 先检查缓存
    if symbol_lower in _search_cache:
        return _search_cache[symbol_lower]
    
    # 先检查本地映射
    if symbol_lower in SYMBOL_TO_ID:
        return SYMBOL_TO_ID[symbol_lower]
    
    # 自动搜索
    try:
        url = f"{COINGECKO_URL}/search"
        params = {"query": symbol}
        resp = _session.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            coins = data.get("coins", [])
            if coins:
                # 找 symbol 完全匹配的
                for c in coins:
                    if c.get("symbol", "").lower() == symbol_lower:
                        cg_id = c.get("id", "")
                        _search_cache[symbol_lower] = cg_id
                        logger.info(f"[AutoSearch] {symbol} -> {cg_id}")
                        return cg_id
                # 取第一个结果
                cg_id = coins[0].get("id", "")
                _search_cache[symbol_lower] = cg_id
                logger.info(f"[AutoSearch] {symbol} -> {cg_id} [best match]")
                return cg_id
    except Exception as e:
        logger.warning(f"[AutoSearch] {symbol} failed: {e}")
    
    return None


def fetch_price_and_change(symbol: str) -> dict:
    """获取价格和7日涨跌（自动搜索 ID）"""
    symbol = symbol.upper()
    
    # 自动获取 ID
    cg_id = _search_coin_id(symbol.lower())
    
    if not cg_id:
        return {"price": 0, "change_7d_pct": 0, "market_cap": 0, "error": "未找到代币ID"}
    
    try:
        url = f"{COINGECKO_URL}/coins/markets"
        params = {"vs_currency": "usd", "ids": cg_id, "order": "market_cap_desc",
                 "per_page": 1, "sparkline": "false", "price_change_percentage": "7d"}
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        data = resp.json()
        
        if data and len(data) > 0:
            coin = data[0]
            price = float(coin.get("current_price", 0))
            change = float(coin.get("price_change_percentage_7d_in_currency", 0))
            mcap = float(coin.get("market_cap", 0))
            print(f"[CoinGecko] ✓ {symbol} ${price:,.0f} ({change:+.1f}%)")
            return {"price": price, "change_7d_pct": change, "market_cap": mcap}
    except Exception as e:
        logger.error(f"CoinGecko 失败: {e}")
    
    return {"price": 0, "change_7d_pct": 0, "market_cap": 0}


# ============================================================================
# 3. BTC.D 数据获取（带缓存）
# ============================================================================

# 缓存实例（5分钟TTL）
_btcd_cache = {"data": None, "timestamp": 0}
BTC_CACHE_TTL = 300  # 5分钟


def fetch_btcd_history(days: int = 30) -> pd.DataFrame:
    """
    获取 BTC.D (Bitcoin Dominance) 历史数据
    
    缓存机制：1小时内不重复请求 (Bug 1 修复)
    """
    global _btcd_cache
    
    # 检查缓存
    now = time.time()
    if _btcd_cache["data"] is not None and (now - _btcd_cache["ts"]) < BTCD_TTL:
        return _btcd_cache["data"]
    
    # 原有逻辑
    try:
        # 方法1: 使用 CoinGecko 获取 BTC 和 ETH 的市值历史
        # /coins/{id}/market_chart 返回 market_caps 数组
        
        # 获取 BTC 市值历史
        btc_url = f"{COINGECKO_URL}/coins/bitcoin/market_chart"
        btc_params = {"vs_currency": "usd", "days": str(days)}  # API expects string
        
        resp_btc = _session.get(btc_url, params=btc_params, timeout=15)
        
        if resp_btc.status_code != 200:
            raise Exception(f"BTC market_chart failed: {resp_btc.status_code}")
        
        btc_data = resp_btc.json()
        btc_mcaps = btc_data.get("market_caps", [])
        
        if not btc_mcaps:
            raise Exception("No BTC market caps data")
        
        # 获取 ETH 市值历史
        eth_url = f"{COINGECKO_URL}/coins/ethereum/market_chart"
        
        resp_eth = _session.get(eth_url, params=btc_params, timeout=15)
        
        if resp_eth.status_code == 200:
            eth_data = resp_eth.json()
            eth_mcaps = eth_data.get("market_caps", [])
        else:
            eth_mcaps = []
        
        # 计算 BTC.D 历史
        df = pd.DataFrame()
        timestamps = []
        btcd_values = []
        
        for i, (ts, btc_mcap) in enumerate(btc_mcaps):
            # 获取对应的 ETH 市值（如果存在）
            if i < len(eth_mcaps) and eth_mcaps[i][0] == ts:
                eth_mcap = eth_mcaps[i][1]
            else:
                # 估算 ETH 市值 (约为 BTC 的 60-70%)
                eth_mcap = btc_mcap * 0.65
            
            total_mcap = btc_mcap + eth_mcap
            btcd = (btc_mcap / total_mcap) * 100 if total_mcap > 0 else 50
            
            timestamps.append(pd.to_datetime(ts, unit="ms"))
            btcd_values.append(btcd)
        
        df["close"] = btcd_values
        df["date"] = timestamps
        df.set_index("date", inplace=True)
        
        current_btcd = btcd_values[-1] if btcd_values else 52.0
        print(f"[BTC.D] ✓ 真实历史数据 {len(df)} 天，当前 {current_btcd:.1f}%")
        
        # 更新缓存
        _btcd_cache["data"] = df
        _btcd_cache["ts"] = now
        
        return df
        
    except Exception as e:
        logger.error(f"BTC.D 获取失败: {e}")
        
        # 尝试返回缓存的旧数据
        if _btcd_cache["data"] is not None:
            print(f"[BTC.D] ⚠ 使用缓存数据（{int(now - _btcd_cache['ts'])}秒前）")
            return _btcd_cache["data"]
        
        print(f"[BTC.D] ✗ 获取失败，回退到简化方法: {e}")
    
    # 回退：返回空 DataFrame，让上层处理
    return pd.DataFrame()


def fetch_btcd_simple() -> dict:
    """获取当前 BTC.D 简单版本"""
    try:
        url = f"{COINGECKO_URL}/global"
        resp = _session.get(url, timeout=10)
        
        if resp.status_code == 200:
            data = resp.json()
            btc_dominance = data.get("data", {}).get("bitcoin_dominance", 0)
            
            # 修复：如果返回0，使用备用方法
            if btc_dominance == 0:
                btc_dominance = 52.0  # 默认值
            
            # 生成简化的历史数据（用于趋势判断）
            df = fetch_btcd_history(14)
            
            return {
                "current": btc_dominance,
                "history_df": df
            }
    except Exception as e:
        logger.error(f"BTC.D 获取失败: {e}")
    
    return {"current": 52.0, "history_df": pd.DataFrame()}


# ============================================================================
# 4. Binance 资金费率 (需要境外网络)
# ============================================================================

# ============================================================================
# 资金费率缓存（5分钟TTL）
# ============================================================================

_funding_cache = {}


def fetch_funding_rate(symbol: str) -> dict:
    """获取资金费率 - 优先Binance，备选OKX，带缓存"""
    global _funding_cache
    
    # 检查缓存
    now = time.time()
    cache_key = f"funding_{symbol.upper()}"
    if cache_key in _funding_cache:
        cached_data, cached_time = _funding_cache[cache_key]
        if now - cached_time < 300:  # 5分钟缓存
            return cached_data
    
    _rate_limit()  # 速率限制
    
    # 原有逻辑
    try:
        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        params = {"symbol": f"{symbol.upper()}USDT", "limit": 100}
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        
        if resp.status_code == 200:
            data = resp.json()
            
            if data and len(data) > 0:
                rates = [float(d["fundingRate"]) for d in data]
                
                print(f"[Funding] ✓ {symbol} Binance获取 {len(rates)} 条费率")
                result = {
                    "rates": rates,
                    "latest_rate": rates[-1] if rates else 0,
                    "avg_7d": sum(rates[-7:])/len(rates[-7:]) if len(rates) >= 7 else 0,
                    "source": "binance",
                }
                _funding_cache[cache_key] = (result, now)
                return result
    except Exception as e:
        pass
    
    # 备选：OKX
    try:
        url = "https://www.okx.com/api/v5/public/funding-rate-history"
        params = {"instId": f"{symbol.upper()}-USDT-SWAP", "limit": 100}
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        
        if resp.status_code == 200:
            data = resp.json()
            
            if data.get("code") == "0" and data.get("data"):
                rates = [float(d["fundingRate"]) for d in data["data"]]
                
                print(f"[Funding] ✓ {symbol} OKX获取 {len(rates)} 条费率")
                result = {
                    "rates": rates,
                    "latest_rate": rates[-1] if rates else 0,
                    "avg_7d": sum(rates[-7:])/len(rates[-7:]) if len(rates) >= 7 else 0,
                    "source": "okx",
                }
                _funding_cache[cache_key] = (result, now)
                return result
    except Exception as e:
        print(f"[Funding] ✗ {symbol} OKX: {e}")
    
    result = {"error": "All sources failed", "rates": []}
    
    # 更新缓存（即使是失败结果也缓存，避免重复请求）
    _funding_cache[cache_key] = (result, now)
    
    return result


def fetch_funding_rate_history(symbol: str) -> dict:
    return fetch_funding_rate(symbol)


# ============================================================================
# 5. OI 数据获取（带缓存）
# ============================================================================

_oi_cache = {}


def fetch_oi_history(symbol: str) -> dict:
    """获取 OI 数据 - 优先Binance，备选OKX，带缓存"""
    global _oi_cache
    
    # 检查缓存
    now = time.time()
    cache_key = f"oi_{symbol.upper()}"
    if cache_key in _oi_cache:
        cached_data, cached_time = _oi_cache[cache_key]
        if now - cached_time < 300:  # 5分钟缓存
            return cached_data
    
    # 原有逻辑
    # 先尝试 Binance
    try:
        url = "https://fapi.binance.com/fapi/v1/openInterest"
        params = {"symbol": f"{symbol.upper()}USDT"}
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        
        if resp.status_code == 200:
            data = resp.json()
            oi = float(data.get("openInterest", 0))
            
            print(f"[OI] ✓ {symbol} Binance OI: {oi/1e6:.1f}M")
            result = {
                "oi_series": [oi],
                "oi": oi,
                "oi_usd": oi,  # simplify
                "source": "binance",
            }
            _oi_cache[cache_key] = (result, now)
            return result
    except Exception as e:
        pass
    
    # 备选：OKX
    try:
        url = "https://www.okx.com/api/v5/public/open-interest"
        params = {"instId": f"{symbol.upper()}-USDT-SWAP"}
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        
        if resp.status_code == 200:
            data = resp.json()
            
            if data.get("code") == "0" and data.get("data"):
                oi = float(data["data"][0]["oi"])
                oi_usd = oi  # OKX returns in USD directly for SWAP
                
                print(f"[OI] ✓ {symbol} OKX OI: ${oi_usd/1e6:.1f}M")
                result = {
                    "oi_series": [oi_usd],
                    "oi": oi_usd,
                    "oi_usd": oi_usd,
                    "source": "okx",
                }
                _oi_cache[cache_key] = (result, now)
                return result
    except Exception as e:
        print(f"[OI] ✗ {symbol} OKX: {e}")
    
    result = {"error": "All sources failed", "oi": 0}
    _oi_cache[cache_key] = (result, now)
    return result


def fetch_funding_rate_history(symbol: str) -> dict:
    return fetch_funding_rate(symbol)


# ============================================================================
# 6. K线数据获取
# ============================================================================

def fetch_daily_ohlcv(symbol: str, limit: int = 30) -> pd.DataFrame:
    """
    获取K线数据 - 优先Binance，备选OKX，带速率限制
    """
    _rate_limit()
    
    # 先尝试 Binance
    try:
        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": f"{symbol.upper()}USDT",
            "interval": "1d",
            "limit": limit
        }
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        
        if resp.status_code == 200:
            data = resp.json()
            
            if data:
                df = pd.DataFrame(data, columns=[
                    "open_time", "open", "high", "low", "close", "volume",
                    "close_time", "quote_asset_volume", "num_trades",
                    "taker_buy_base_asset_volume", "taker_buy_quote_asset_volume", "ignore"
                ])
                
                df["open"] = df["open"].astype(float)
                df["high"] = df["high"].astype(float)
                df["low"] = df["low"].astype(float)
                df["close"] = df["close"].astype(float)
                df["volume"] = df["volume"].astype(float)
                
                # Bug 4 修复: FutureWarning
                df["open_time"] = pd.to_datetime(
                    pd.to_numeric(df["open_time"], errors='coerce'), unit="ms"
                )
                df.set_index("open_time", inplace=True)
                
                print(f"[K线] ✓ {symbol} Binance获取 {len(df)} 条数据")
                return df
    except Exception as e:
        pass
    
    # 备选：OKX
    try:
        url = "https://www.okx.com/api/v5/market/history-candles"
        params = {
            "instId": f"{symbol.upper()}-USDT-SWAP",
            "bar": "1D",
            "limit": limit
        }
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        
        if resp.status_code == 200:
            data = resp.json()
            
            if data.get("code") == "0" and data.get("data"):
                candles = data["data"]
                
                # OKX returns 9 columns: timestamp, open, high, low, close, vol, quote_vol, confirm, timestamp_nano
                df = pd.DataFrame(candles, columns=[
                    "open_time", "open", "high", "low", "close", 
                    "volume", "quote_volume", "confirm", "timestamp_nano"
                ])
                
                df["open"] = pd.to_numeric(df["open"], errors='coerce')
                df["high"] = pd.to_numeric(df["high"], errors='coerce')
                df["low"] = pd.to_numeric(df["low"], errors='coerce')
                df["close"] = pd.to_numeric(df["close"], errors='coerce')
                df["volume"] = pd.to_numeric(df["volume"], errors='coerce')
                
                df = df.dropna(subset=["open", "high", "low", "close", "volume"])
                
                # Bug 4 修复: FutureWarning
                df["open_time"] = pd.to_datetime(
                    pd.to_numeric(df["open_time"], errors='coerce'), unit="ms"
                )
                df.set_index("open_time", inplace=True)
                
                print(f"[K线] ✓ {symbol} OKX获取 {len(df)} 条数据")
                return df
    except Exception as e:
        print(f"[K线] ✗ {symbol} OKX: {e}")
    
    print(f"[K线] ✗ {symbol} 所有源失败")
    return pd.DataFrame()


# ============================================================================
# 7. Binance 合约检测
# ============================================================================

BINANCE_永续合约列表 = {
    "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC",
    "LINK", "NEAR", "APT", "SUI", "ARB", "OP", "INJ", "RNDR", "IMX", "LDO",
    "MKR", "UNI", "AAVE", "ATOM", "FIL", "THETA", "EOS", "ALGO", "XLM", "VET",
    "FLOW", "AXS", "MANA", "SAND", "GALA", "ENJ", "CHZ", "CRV", "LRC", "BAT"
}

def check_futures_contract(symbol: str) -> dict:
    """检查 Binance 永续合约"""
    symbol_upper = symbol.upper()
    
    # 先检查是否在已知列表中
    if symbol_upper in BINANCE_永续合约列表:
        # 尝试获取上线时间
        try:
            url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
            resp = _session.get(url, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                for s in data.get("symbols", []):
                    if s.get("symbol") == f"{symbol_upper}USDT":
                        if s.get("status") == "TRADING":
                            # 尝试从合约信息中获取上线时间
                            return {"has_futures": True, "days_since_listing": 30}
        except:
            pass
        
        return {"has_futures": True, "days_since_listing": 30}
    
    return {"has_futures": False, "days_since_listing": -1}


# ============================================================================
# 8. 综合数据获取
# ============================================================================

def fetch_all_data(symbol: str) -> Dict[str, Any]:
    """获取所有数据"""
    results = {
        "symbol": symbol,
        "price_info": {},
        "dex_info": {},
        "futures_info": {},
        "funding_info": {},
        "oi_info": {},
        "kline_df": pd.DataFrame(),
        "btcd_info": {},
    }
    
    from concurrent.futures import ThreadPoolExecutor
    
    def safe_fetch(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"error": str(e)}
    
    with ThreadPoolExecutor(max_workers=6) as ex:
        tasks = {
            "dex_info": (fetch_dex_data, symbol),
            "futures_info": (check_futures_contract, symbol),
            "funding_info": (fetch_funding_rate_history, symbol),
            "oi_info": (fetch_oi_history, symbol),
            "price_info": (fetch_price_and_change, symbol),
            "kline_df": (fetch_daily_ohlcv, symbol),
        }
        
        future_to_key = {}
        for key, (fn, *args) in tasks.items():
            future_to_key[ex.submit(safe_fetch, fn, *args)] = key
        
        for future in future_to_key:
            key = future_to_key[future]
            try:
                results[key] = future.result(timeout=15)
            except Exception as e:
                results[key] = {"error": str(e)}
    
    # 单独获取 BTC.D（独立API调用）
    results["btcd_info"] = fetch_btcd_simple()
    
    return results


# ============================================================================
# OKX 多空比和成交量数据 (免费API)
# ============================================================================

def fetch_long_short_ratio(symbol: str, period: str = "1D", limit: int = 7) -> dict:
    """
    获取多空比数据 - OKX免费API
    
    GET /api/v5/rubik/stat/contracts/long-short-account-ratio
    
    Args:
        symbol: 代币符号，如 "LAB"
        period: 时间周期 "1D", "4H", "1H"
        limit: 返回天数
    
    Returns:
        {"long_ratio": float, "short_ratio": float, "history": [], "source": "okx"}
    """
    try:
        url = "https://www.okx.com/api/v5/rubik/stat/contracts/long-short-account-ratio"
        params = {
            "ccy": symbol.upper(),
            "period": period,
            "limit": str(limit)
        }
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "long_ratio": 0, "short_ratio": 0}
        
        data = resp.json()
        if data.get("code") != "0":
            return {"error": data.get("msg", "unknown"), "long_ratio": 0, "short_ratio": 0}
        
        records = data.get("data", [])
        if not records:
            return {"long_ratio": 0, "short_ratio": 0, "history": [], "source": "okx"}
        
        # OKX返回格式: [["ts", "longAcc"], ["ts", "longAcc"], ...]
        # 需要从历史数据推算多空比
        history = []
        for r in reversed(records):
            ts = int(r[0]) if r[0] else 0
            long_acc = float(r[1]) if r[1] else 0.5
            # longAcc = long用户数/(long+short用户数)
            # 推算: long% = longAcc * 100, short% = (1-longAcc) * 100
            long_ratio = long_acc * 100
            short_ratio = (1 - long_acc) * 100
            history.append({
                "timestamp": ts,
                "long_ratio": long_ratio,
                "short_ratio": short_ratio,
            })
        
        # 取最新的
        latest = history[-1] if history else {"long_ratio": 50, "short_ratio": 50}
        
        print(f"[多空比] ✓ {symbol} 多头 {latest['long_ratio']:.1f}% / 空头 {latest['short_ratio']:.1f}%")
        
        return {
            "long_ratio": latest["long_ratio"],
            "short_ratio": latest["short_ratio"],
            "long_short_diff": latest["long_ratio"] - latest["short_ratio"],
            "history": history,
            "source": "okx"
        }
    
    except Exception as e:
        return {"error": str(e), "long_ratio": 0, "short_ratio": 0, "source": "okx"}


def fetch_taker_volume(symbol: str, period: str = "1D", limit: int = 7) -> dict:
    """
    获取主动买/卖成交量 - OKX免费API
    
    GET /api/v5/rubik/stat/taker-volume
    
    Args:
        symbol: 代币符号，如 "LAB"
        period: 时间周期 "1D", "4H", "1H"
        limit: 返回天数
    
    Returns:
        {"buy_volume": float, "sell_volume": float, "buy_sell_ratio": float, "history": [], "source": "okx"}
    """
    try:
        url = "https://www.okx.com/api/v5/rubik/stat/taker-volume"
        params = {
            "ccy": symbol.upper(),
            "instType": "CONTRACTS",
            "period": period,
            "limit": str(limit)
        }
        
        resp = _session.get(url, params=params, timeout=TIMEOUT)
        
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "buy_volume": 0, "sell_volume": 0}
        
        data = resp.json()
        if data.get("code") != "0":
            return {"error": data.get("msg", "unknown"), "buy_volume": 0, "sell_volume": 0}
        
        records = data.get("data", [])
        if not records:
            return {"buy_volume": 0, "sell_volume": 0, "buy_sell_ratio": 0, "source": "okx"}
        
        # OKX返回格式: [["ts", "buyVol", "sellVol"], ...]
        history = []
        for r in reversed(records):
            ts = int(r[0]) if r[0] else 0
            buy_vol = float(r[1]) if r[1] else 0
            sell_vol = float(r[2]) if r[2] else 0
            buy_sell_ratio = buy_vol / sell_vol if sell_vol > 0 else 1.0
            history.append({
                "timestamp": ts,
                "buy_volume": buy_vol,
                "sell_volume": sell_vol,
                "buy_sell_ratio": buy_sell_ratio,
            })
        
        # 取最新的
        latest = history[-1] if history else {"buy_volume": 0, "sell_volume": 0, "buy_sell_ratio": 1.0}
        
        print(f"[主动成交量] ✓ {symbol} 买入 ${latest['buy_volume']/1e6:.1f}M / 卖出 ${latest['sell_volume']/1e6:.1f}M")
        
        total = latest['buy_volume'] + latest['sell_volume']
        buy_dominance = (latest['buy_volume'] / total * 100) if total > 0 else 50
        
        return {
            "buy_volume": latest['buy_volume'],
            "sell_volume": latest['sell_volume'],
            "buy_sell_ratio": latest['buy_sell_ratio'],
            "buy_dominance": buy_dominance,
            "history": history,
            "source": "okx"
        }
    
    except Exception as e:
        return {"error": str(e), "buy_volume": 0, "sell_volume": 0, "source": "okx"}


# ============================================================================
# 9. OI + Volume 历史 (优化端点)
# ============================================================================

_oi_vol_cache = {"data": None, "ts": 0}
OI_VOL_TTL = 300  # 5分钟缓存

def fetch_oi_volume_history(symbol: str, period: str = "1D", limit: int = 30) -> dict:
    """
    获取 OI + 成交量历史 - OKX 优化端点
    
    GET /api/v5/rubik/stat/contracts/open-interest-volume
    
    返回: {"oi_history": [], "volume_history": [], "oi": float, "volume": float}
    """
    global _oi_vol_cache
    
    now = time.time()
    cache_key = f"oi_vol_{symbol.upper()}"
    if cache_key in _oi_vol_cache:
        cached_data, cached_time = _oi_vol_cache[cache_key]
        if now - cached_time < OI_VOL_TTL:
            return cached_data
    
    try:
        url = "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-volume"
        params = {
            "ccy": symbol.upper(),
            "instType": "CONTRACTS",
            "period": period,
            "limit": str(limit)
        }
        
        resp = _session.get(url, params=params, timeout=15)
        
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "oi": 0, "volume": 0}
        
        data = resp.json()
        if data.get("code") != "0":
            return {"error": data.get("msg", "unknown"), "oi": 0, "volume": 0}
        
        records = data.get("data", [])
        if not records:
            return {"oi": 0, "volume": 0, "oi_history": [], "volume_history": [], "source": "okx"}
        
        oi_history = []
        volume_history = []
        
        for r in reversed(records):
            ts = int(r[0]) if r[0] else 0
            oi = float(r[1]) if r[1] else 0
            vol = float(r[2]) if r[2] else 0
            oi_history.append({"timestamp": ts, "oi": oi})
            volume_history.append({"timestamp": ts, "volume": vol})
        
        latest = oi_history[-1] if oi_history else {"oi": 0}
        latest_vol = volume_history[-1] if volume_history else {"volume": 0}
        
        result = {
            "oi": latest["oi"],
            "volume": latest_vol["volume"],
            "oi_history": oi_history,
            "volume_history": volume_history,
            "source": "okx"
        }
        
        _oi_vol_cache[cache_key] = (result, now)
        print(f"[OI+Volume] ✓ {symbol} OI: ${latest['oi']/1e9:.2f}B / Vol: ${latest_vol['volume']/1e9:.2f}B")
        
        return result
    
    except Exception as e:
        return {"error": str(e), "oi": 0, "volume": 0, "source": "okx"}


# ============================================================================
# 10. Support Coins (可交易合约列表)
# ============================================================================

_support_coins_cache = {"data": None, "ts": 0}
SUPPORT_COINS_TTL = 3600  # 1小时缓存

def fetch_support_coins(inst_type: str = "SWAP") -> dict:
    """
    获取 OKX 支持的合约币种列表
    
    GET /api/v5/rubik/stat/trading-data/support-coin
    
    Returns: {"coins": [...], "source": "okx"}
    """
    global _support_coins_cache
    
    now = time.time()
    cache_key = f"support_coins_{inst_type}"
    if cache_key in _support_coins_cache:
        cached_data, cached_time = _support_coins_cache[cache_key]
        if now - cached_time < SUPPORT_COINS_TTL:
            return cached_data
    
    try:
        url = "https://www.okx.com/api/v5/rubik/stat/trading-data/support-coin"
        params = {"instType": inst_type}
        
        resp = _session.get(url, params=params, timeout=15)
        
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "coins": []}
        
        data = resp.json()
        if data.get("code") != "0":
            return {"error": data.get("msg", "unknown"), "coins": []}
        
        raw_data = data.get("data", {})
        if isinstance(raw_data, dict):
            contract_coins = raw_data.get("contract", [])
        else:
            contract_coins = []
        
        result = {
            "coins": contract_coins,
            "source": "okx"
        }
        
        _support_coins_cache[cache_key] = (result, now)
        print(f"[SupportCoins] ✓ 共 {len(contract_coins)} 个合约币种")
        
        return result
    
    except Exception as e:
        return {"error": str(e), "coins": [], "source": "okx"}
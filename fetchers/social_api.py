"""
MMTracker 社交情绪数据获取器
用于获取 Reddit、News 等社交平台的情绪数据
"""

import requests
import logging
from typing import Dict, Any, List, Optional
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ============================================================================
# 1. Reddit 公开数据 (无需Key)
# ============================================================================

REDDIT_HEADERS = {
    "User-Agent": "MMTracker/1.0 (Market Maker Tracker; Python/3.11+)"
}


def fetch_reddit_posts(subreddit: str = "cryptocurrency", limit: int = 10) -> dict:
    """
    获取 Reddit 公开帖子
    
    注意：Reddit 现在需要 OAuth 才能获取完整数据
    这里使用公开端点获取基本信息
    """
    try:
        url = f"https://www.reddit.com/r/{subreddit}/new.json"
        params = {"limit": min(limit, 25), "raw_json": 1}
        
        resp = requests.get(url, params=params, headers=REDDIT_HEADERS, timeout=15)
        
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "posts": []}
        
        data = resp.json()
        
        if "data" not in data:
            return {"error": "Invalid response", "posts": []}
        
        posts = []
        for child in data["data"].get("children", []):
            post = child.get("data", {})
            
            posts.append({
                "title": post.get("title", ""),
                "score": post.get("score", 0),
                "num_comments": post.get("num_comments", 0),
                "created_utc": post.get("created_utc", 0),
                "author": post.get("author", ""),
                "id": post.get("id", ""),
                "url": f"https://reddit.com{post.get('permalink', '')}",
            })
        
        return {
            "posts": posts,
            "count": len(posts),
            "subreddit": subreddit,
        }
        
    except Exception as e:
        logger.error(f"Reddit fetch error: {e}")
        return {"error": str(e), "posts": []}


def analyze_reddit_sentiment(subreddit: str = "cryptocurrency", limit: int = 20) -> dict:
    """
    分析 Reddit 情绪
    
    简单关键词情绪分析
    """
    posts_data = fetch_reddit_posts(subreddit, limit)
    
    if not posts_data.get("posts"):
        return {
            "sentiment": "neutral",
            "score": 0,
            "post_count": 0,
            "detail": "无法获取帖子数据"
        }
    
    # 情绪关键词
    bullish_keywords = ["moon", "bull", "buy", "long", "pump", "to the moon", "bullish", "up", "gain"]
    bearish_keywords = ["dump", "bear", "sell", "short", "crash", "bearish", "down", "loss", "rug"]
    fear_keywords = ["scam", "hack", "warning", "danger", "exit", "rug pull", "fake", "ponzi"]
    greed_keywords = ["gem", "soon", "confirm", "proof", "big", "huge", "million"]
    
    total_score = 0
    post_count = len(posts_data["posts"])
    
    for post in posts_data["posts"]:
        title = post.get("title", "").lower()
        
        for kw in bullish_keywords:
            if kw in title:
                total_score += 1
        for kw in bearish_keywords:
            if kw in title:
                total_score -= 1
        for kw in fear_keywords:
            if kw in title:
                total_score -= 2
        for kw in greed_keywords:
            if kw in title:
                total_score += 1
        
        # 评分权重
        total_score += 1 if post.get("score", 0) > 100 else 0
    
    # 归一化到 -1 到 1
    if post_count > 0:
        normalized_score = total_score / (post_count * 2)
        normalized_score = max(-1, min(1, normalized_score))
    else:
        normalized_score = 0
    
    # 判断情绪
    if normalized_score > 0.2:
        sentiment = "bullish"
    elif normalized_score < -0.2:
        sentiment = "bearish"
    else:
        sentiment = "neutral"
    
    return {
        "sentiment": sentiment,
        "score": round(normalized_score, 2),
        "post_count": post_count,
        "raw_score": total_score,
        "detail": f"Reddit情绪: {sentiment} ({normalized_score:+.1%})，共 {post_count} 篇帖子"
    }


# ============================================================================
# 2. Crypto News API (备用)
# ============================================================================

def fetch_crypto_news(limit: int = 10) -> dict:
    """
    获取加密货币新闻
    
    使用 CryptoPanic 免费 API 或替代方案
    """
    try:
        # 尝试 CryptoPanic
        url = "https://cryptopanic.com/api/v1/posts/"
        params = {"auth_token": "public", "filter": "hot", "limit": limit}
        
        resp = requests.get(url, params=params, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            
            posts = []
            for item in data.get("results", [])[:limit]:
                posts.append({
                    "title": item.get("title", ""),
                    "published_at": item.get("published_at", ""),
                    "url": item.get("url", ""),
                    "domain": item.get("domain", ""),
                })
            
            return {"posts": posts, "source": "cryptopanic"}
    
    except Exception as e:
        logger.error(f"Crypto news error: {e}")
    
    return {"posts": [], "source": "none"}


# ============================================================================
# 3. Whale Alert 鲸鱼监控 (需要 API Key)
# ============================================================================

def fetch_whale_alerts(api_key: str, limit: int = 10) -> dict:
    """
    获取大额转账提醒
    
    需要 Whale Alert API Key
    免费 tier: 1000 requests/month
    """
    if not api_key or api_key == "YOUR_API_KEY":
        return {"error": "需要 API Key", "alerts": []}
    
    try:
        url = "https://api.whale-alert.io/v1/transactions"
        params = {"api_key": api_key, "limit": limit, "min_value": 1000000}  # $1M+
        
        resp = requests.get(url, params=params, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            
            alerts = []
            for tx in data.get("transactions", []):
                alerts.append({
                    "symbol": tx.get("symbol", ""),
                    "amount": tx.get("amount", 0),
                    "amount_usd": tx.get("amount_usd", 0),
                    "from": tx.get("from", {}).get("owner", ""),
                    "to": tx.get("to", {}).get("owner", ""),
                    "tx_hash": tx.get("hash", ""),
                    "timestamp": tx.get("timestamp", 0),
                })
            
            return {
                "alerts": alerts,
                "count": len(alerts),
            }
        else:
            return {"error": f"HTTP {resp.status_code}", "alerts": []}
    
    except Exception as e:
        logger.error(f"Whale Alert error: {e}")
        return {"error": str(e), "alerts": []}


# ============================================================================
# 4. GeckoTerminal DEX 数据
# ============================================================================

GECKOTERMINAL_URL = "https://api.geckoterminal.com/api/v2"


def fetch_geckoterminal_token_pools(network: str, token_address: str) -> dict:
    """
    获取代币在特定网络的 DEX 池子
    
    Args:
        network: 网络名 (eth, bsc, arb, avax, etc.)
        token_address: 代币地址
    """
    try:
        # GeckoTerminal 不支持直接通过 token_address 搜索
        # 使用 token 的 symbol 搜索
        url = f"{GECKOTERMINAL_URL}/networks/{network}/tokens/{token_address}/pools"
        
        resp = requests.get(url, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            
            pools = []
            for pool in data.get("data", [])[:5]:
                attrs = pool.get("attributes", {})
                
                pools.append({
                    "dex": pool.get("relationships", {}).get("dex", {}).get("id", ""),
                    "name": attrs.get("name", ""),
                    "base_token_price_usd": attrs.get("base_token_price_usd", "0"),
                    "quote_token_price_usd": attrs.get("quote_token_price_usd", "0"),
                    "volume_usd_h24": attrs.get("volume_usd_h24", "0"),
                    "liquidity_usd": attrs.get("liquidity_usd", "0"),
                    "txns_h24_buys": attrs.get("txns_h24", {}).get("buys", 0),
                    "txns_h24_sells": attrs.get("txns_h24", {}).get("sells", 0),
                })
            
            return {
                "pools": pools,
                "network": network,
                "token_address": token_address,
            }
    
    except Exception as e:
        logger.error(f"GeckoTerminal error: {e}")
    
    return {"pools": [], "error": str(e)}


def fetch_geckoterminal_token_data(network: str, token_symbol: str) -> dict:
    """
    通过 symbol 搜索代币并获取池子数据
    """
    try:
        # 先搜索代币
        search_url = f"{GECKOTERMINAL_URL}/search"
        params = {"query": token_symbol}
        
        resp = requests.get(search_url, params=params, timeout=15)
        
        if resp.status_code == 200:
            data = resp.json()
            
            # 找到匹配的代币
            tokens = data.get("data", [])
            
            for token in tokens:
                if token.get("attributes", {}).get("symbol", "").upper() == token_symbol.upper():
                    # 获取该代币的池子
                    token_address = token.get("attributes", {}).get("address", "")
                    
                    # 简化：返回搜索结果的基本信息
                    return {
                        "name": token.get("attributes", {}).get("name", ""),
                        "symbol": token.get("attributes", {}).get("symbol", ""),
                        "network": network,
                        "address": token_address,
                        "price_usd": token.get("attributes", {}).get("price_usd", "0"),
                    }
        
    except Exception as e:
        logger.error(f"GeckoTerminal search error: {e}")
    
    return {"error": "Token not found"}


# ============================================================================
# 5. 综合社交情绪分析
# ============================================================================

def fetch_social_sentiment(symbol: str = None) -> Dict[str, Any]:
    """
    综合获取社交情绪数据
    """
    result = {
        "reddit": analyze_reddit_sentiment("cryptocurrency", 20),
        "news": fetch_crypto_news(10),
        "timestamp": datetime.now().isoformat(),
    }
    
    # 如果指定了代币，分析相关帖子
    if symbol:
        result["reddit_symbol"] = analyze_reddit_sentiment(symbol.lower(), 10)
    
    return result


# ============================================================================
# 6. 批量获取鲸鱼数据
# ============================================================================

def get_whale_activity(api_key: str, min_value_usd: int = 1000000) -> dict:
    """
    获取大额转账活动
    """
    result = fetch_whale_alerts(api_key, 20)
    
    if result.get("alerts"):
        # 统计
        total_value = sum(a.get("amount_usd", 0) for a in result["alerts"])
        symbols = {}
        
        for alert in result["alerts"]:
            sym = alert.get("symbol", "UNKNOWN")
            symbols[sym] = symbols.get(sym, 0) + 1
        
        result["summary"] = {
            "total_value_24h": total_value,
            "tx_count": len(result["alerts"]),
            "top_symbols": sorted(symbols.items(), key=lambda x: x[1], reverse=True)[:3],
        }
    
    return result
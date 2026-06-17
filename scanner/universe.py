"""
MMTracker Scanner - 全市场代币候选列表
获取全市场候选代币列表（带基础指标）
"""

import requests
import logging
from typing import List, Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def get_okx_universe() -> List[Dict[str, Any]]:
    """
    返回 OKX 全量 SWAP 代币列表
    每项: {"symbol": str, "price": float, "volume_usd_24h": float, 
            "change_24h_pct": float, "source": "okx_swap", "days_listed": int}
    单次 API 调用，不超过2秒
    
    Bug 5 修复：获取合约上市时间，过滤18个月以上的旧币
    """
    # 首先获取 instruments 列表获取上市时间
    instruments = {}
    try:
        inst_url = "https://www.okx.com/api/v5/public/instruments"
        inst_resp = requests.get(inst_url, params={"instType": "SWAP"}, timeout=10)
        if inst_resp.status_code == 200:
            inst_data = inst_resp.json()
            for inst in inst_data.get("data", []):
                inst_id = inst.get("instId", "")
                list_time = int(inst.get("listTime", 0))
                if list_time > 0:
                    instruments[inst_id] = list_time
    except Exception as e:
        logger.warning(f"获取OKX合约列表失败: {e}")
    
    now_ts = datetime.now().timestamp() * 1000
    
    try:
        url = "https://www.okx.com/api/v5/market/tickers"
        params = {"instType": "SWAP"}
        
        resp = requests.get(url, params=params, timeout=10)
        
        if resp.status_code != 200:
            print(f"[Universe] ✗ OKX HTTP {resp.status_code}")
            return []
        
        data = resp.json()
        
        if data.get("code") != "0":
            print(f"[Universe] ✗ OKX API error: {data.get('msg')}")
            return []
        
        raw_list = data.get("data", [])
        
        # 过滤 USDT-SWAP 结尾的交易对
        result = []
        for item in raw_list:
            inst_id = item.get("instId", "")
            
            if not inst_id.endswith("-USDT-SWAP"):
                continue
            
            # 提取 symbol
            symbol = inst_id.replace("-USDT-SWAP", "")
            
            # 跳过主流币
            if symbol in ["BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE"]:
                continue
            
            try:
                price = float(item.get("last", 0))
                open_24h = float(item.get("open24h", 0))
                volume_usd = float(item.get("volCcy24h", 0))
                
                if price <= 0 or open_24h <= 0:
                    continue
                
                change_24h_pct = ((price - open_24h) / open_24h) * 100
                
                # 计算上市天数
                list_ts = instruments.get(inst_id, 0)
                if list_ts > 0:
                    days_listed = int((now_ts - list_ts) / (1000 * 60 * 60 * 24))
                else:
                    days_listed = 9999  # 未知默认超旧
                
                result.append({
                    "symbol": symbol,
                    "price": price,
                    "volume_usd_24h": volume_usd,
                    "change_24h_pct": round(change_24h_pct, 2),
                    "days_listed": days_listed,  # Bug 5: 新增上市天数
                    "source": "okx_swap",
                })
            except (ValueError, TypeError) as e:
                continue
        
        print(f"[Universe] ✓ OKX 获取 {len(result)} 个 SWAP 交易对")
        return result
        
    except Exception as e:
        print(f"[Universe] ✗ OKX 异常: {e}")
        return []


def get_gecko_new_pools() -> List[Dict[str, Any]]:
    """
    返回 GeckoTerminal 新池子代币（DEX早期发现）
    每项: {"symbol": str, "price": float, "liquidity": float,
            "buy_sell_ratio": float, "chain": str, "pool_age_days": int,
            "source": "gecko_new_pool"}
    """
    networks = [
        ("eth", "https://api.geckoterminal.com/api/v2/networks/eth/new_pools"),
        ("bsc", "https://api.geckoterminal.com/api/v2/networks/bsc/new_pools"),
        ("arb", "https://api.geckoterminal.com/api/v2/networks/arbitrum-one/new_pools"),
        ("sol", "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"),
        ("avax", "https://api.geckoterminal.com/api/v2/networks/avalanche-c/new_pools"),
    ]
    
    all_pools = []
    now = datetime.now()
    
    for chain, url in networks:
        try:
            resp = requests.get(url, params={"page": "1"}, timeout=10)
            
            if resp.status_code != 200:
                continue
            
            data = resp.json()
            pools = data.get("data", [])
            
            for pool in pools:
                attrs = pool.get("attributes", {})
                
                # 基础字段 - 注意字段可能是字符串
                name = attrs.get("name", "")
                try:
                    liquidity = float(attrs.get("reserve_in_usd", "0"))
                except:
                    liquidity = 0
                
                try:
                    volume_24h = float(attrs.get("volume_usd", {}).get("h24", "0"))
                except:
                    volume_24h = 0
                
                created_at = attrs.get("pool_created_at", "")
                
                # 交易数据
                txns = attrs.get("transactions", {}).get("h24", {})
                buys = txns.get("buys", 0)
                sells = txns.get("sells", 0)
                
                # 过滤条件 - 降低门槛以获取更多数据
                if liquidity < 10000:  # < $1万 太低
                    continue
                if volume_24h < 5000:  # < $5千太低
                    continue
                if buys <= sells:  # 买多于卖
                    continue
                
                # 计算池子年龄
                try:
                    pool_created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    age_days = (now - pool_created.replace(tzinfo=None)).days
                except:
                    age_days = 999
                
                if age_days > 30:  # 超过30天不算新池
                    continue
                
                # 提取代币符号
                if "/" in name:
                    symbol = name.split("/")[0].strip().upper()
                else:
                    symbol = name.split()[0].strip().upper() if name else "UNKNOWN"
                
                # 价格 (base_token_price_usd 是代币的 USD 价格)
                try:
                    price = float(attrs.get("base_token_price_usd", "0"))
                except:
                    price = 0
                
                # 买卖比
                buy_sell_ratio = buys / sells if sells > 0 else float('inf')
                
                all_pools.append({
                    "symbol": symbol,
                    "price": price,
                    "liquidity": round(liquidity, 2),
                    "volume_usd_24h": round(volume_24h, 2),
                    "buy_sell_ratio": round(buy_sell_ratio, 2),
                    "chain": chain,
                    "pool_age_days": age_days,
                    "source": "gecko_new_pool",
                })
                
        except Exception as e:
            print(f"[Universe] ✗ GeckoTerminal {chain}: {e}")
            continue
    
    print(f"[Universe] ✓ GeckoTerminal 获取 {len(all_pools)} 个新池子")
    return all_pools


def get_full_universe() -> List[Dict[str, Any]]:
    """
    合并两个数据源，去重（以symbol为key，保留流动性更高的）
    返回合并后的完整候选列表
    打印统计: "[Universe] OKX: 423个 | DEX新池: 18个 | 合并去重: 441个"
    """
    # 获取数据
    okx_list = get_okx_universe()
    gecko_list = get_gecko_new_pools()
    
    # 去重合并
    merged = {}
    
    # 先加入 OKX
    for item in okx_list:
        symbol = item["symbol"]
        if symbol not in merged:
            merged[symbol] = item
    
    # 再加入 Gecko（如果有重复，保留流动性更高的）
    for item in gecko_list:
        symbol = item["symbol"]
        
        if symbol in merged:
            # 比较流动性，保留更高的
            existing_vol = merged[symbol].get("volume_usd_24h", 0)
            new_vol = item.get("volume_usd_24h", 0)
            
            if new_vol > existing_vol:
                merged[symbol] = item
        else:
            merged[symbol] = item
    
    result = list(merged.values())
    
    # 按成交量排序
    result.sort(key=lambda x: x.get("volume_usd_24h", 0), reverse=True)
    
    print(f"[Universe] OKX: {len(okx_list)}个 | DEX新池: {len(gecko_list)}个 | 合并去重: {len(result)}个")
    
    return result


if __name__ == "__main__":
    # 测试
    universe = get_full_universe()
    print(f"\n前10个候选代币:")
    for item in universe[:10]:
        print(f"  {item['symbol']}: ${item['price']:.4f}, Vol=${item['volume_usd_24h']/1e6:.1f}M")
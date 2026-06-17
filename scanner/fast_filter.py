"""
MMTracker Scanner - 快速画像过滤器
对全市场代币进行第一轮筛选，把 400+ 代币缩减到 20-30 个候选
"""

import requests
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

logger = logging.getLogger(__name__)


class ProfileFilter:
    """庄家目标代币画像过滤器"""
    
    # 要排除的主流大币（这些太大，庄家无法轻易操控）
    EXCLUDE_SYMBOLS = {
        "BTC", "ETH", "BNB", "SOL", "XRP", "ADA", "DOGE", "AVAX", 
        "DOT", "MATIC", "LINK", "UNI", "ATOM", "LTC", "ETC", "BCH",
        "NEAR", "APT", "ARB", "OP", "FIL", "ICP", "HBAR", "VET",
        "TRX", "XLM", "ALGO", "EGLD", "THETA", "FTM", "SAND", "MANA",
        "AAVE", "MKR", "SNX", "CRV", "LDO", "IMX", "INJ", "RNDR",
        "RUNE", "KAVA", "ZIL", "ENS", "MINA", "COMP", "SUSHI", "1INCH",
        "GRT", "ANT", "SKL", "BAT", "CELO", "QTUM", "ONE", "ZEC",
    }
    
    # 价格范围（这是LAB/ALLO/VELVET积累时的价格带）
    PRICE_MIN = 0.0005
    PRICE_MAX = 8.0
    
    # 成交量范围（有热度但还不热）
    VOLUME_MIN_USD = 300_000      # $30万
    VOLUME_MAX_USD = 30_000_000   # $3000万
    
    # 价格变化范围（横盘积累特征）
    CHANGE_24H_MIN = -25.0
    CHANGE_24H_MAX = 25.0
    
    # 整数关口层级
    INTEGER_LEVELS = [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100]
    
    def filter(self, candidates: List[Dict]) -> List[Dict]:
        """
        应用画像过滤器，返回符合条件的候选列表
        
        过滤逻辑：
        1. 排除 EXCLUDE_SYMBOLS 中的大币
        2. 价格在 PRICE_MIN ~ PRICE_MAX 之间
        3. 24H成交量在 VOLUME_MIN ~ VOLUME_MAX 之间
        4. 24H价格变化在 CHANGE_24H_MIN ~ CHANGE_24H_MAX 之间
        5. Bug 5: 排除上市超过18个月(548天)的旧币
        """
        filtered = []
        
        for cand in candidates:
            symbol = cand.get("symbol", "")
            
            # 1. 排除大币
            if symbol in self.EXCLUDE_SYMBOLS:
                continue
            
            # 获取数据（兼容不同字段名）
            price = cand.get("price", 0)
            volume = cand.get("volume_usd_24h", 0)
            change = cand.get("change_24h_pct", 0)
            
            # 如果字段不存在，尝试其他名称
            if price == 0:
                price = cand.get("priceUsd", 0) or cand.get("liquidity", 0)
            
            # 2. 价格过滤
            if price <= 0 or price < self.PRICE_MIN or price > self.PRICE_MAX:
                continue
            
            # 3. 成交量过滤
            if volume < self.VOLUME_MIN_USD or volume > self.VOLUME_MAX_USD:
                continue
            
            # 4. 价格变化过滤
            if change < self.CHANGE_24H_MIN or change > self.CHANGE_24H_MAX:
                continue
            
            # 5. Bug 5: 排除上市超过18个月的旧币
            days_listed = cand.get("days_listed", 9999)
            if days_listed > 548:  # 超过18个月
                continue
            
            filtered.append(cand)
        
        print(f"[Filter] 输入 {len(candidates)} 个 → 画像过滤后 {len(filtered)} 个候选")
        
        return filtered
    
    def _get_nearest_integer_level(self, price: float) -> tuple:
        """
        找到最近的整数关口
        
        返回: (nearest_level, distance_pct)
        """
        # 找到高于当前价格的最近层级
        higher_levels = [l for l in self.INTEGER_LEVELS if l > price]
        
        if higher_levels:
            nearest = min(higher_levels)
            distance_pct = ((nearest - price) / nearest) * 100
            return (nearest, distance_pct)
        
        # 如果价格超过所有层级，找最高的
        nearest = max(self.INTEGER_LEVELS)
        distance_pct = ((price - nearest) / nearest) * 100  # 负值表示已突破
        return (nearest, distance_pct)
    
    def score_quick(self, candidate: dict) -> float:
        """
        对每个候选进行快速预评分（0~10分），不需要额外API请求
        完全基于 volume/price 关系
        
        评分规则：
        +3分: 价格在 $0.01~$2 的"甜蜜区间"（LAB/ALLO都在这段启动）
        +2分: 成交量在 $500K~$5M（适中，还有上升空间）
        +2分: 24H价格变化 -5% ~ +5%（横盘特征）
        +2分: 价格接近整数关口（距最近整数关口 < 15%）
        +1分: DEX新池来源（来自GeckoTerminal = 更早期的发现）
        """
        score = 0.0
        
        price = candidate.get("price", 0)
        volume = candidate.get("volume_usd_24h", 0)
        change = candidate.get("change_24h_pct", 0)
        source = candidate.get("source", "")
        
        # +3分: 价格在 $0.01~$2 的"甜蜜区间"
        if 0.01 <= price <= 2.0:
            score += 3.0
        elif price < 0.01:
            score += 1.5  # 低价币也有潜力
        elif price < 0.1:
            score += 2.0
        
        # +2分: 成交量在 $500K~$5M（适中）
        if 500_000 <= volume <= 5_000_000:
            score += 2.0
        elif volume < 1_000_000:
            score += 1.0  # 偏低还有上升空间
        elif volume < 5_000_000:
            score += 1.5
        
        # +2分: 24H价格变化 -5% ~ +5%（横盘特征）
        if -5.0 <= change <= 5.0:
            score += 2.0
        elif -10.0 <= change <= 10.0:
            score += 1.0
        
        # +2分: 价格接近整数关口
        if price > 0:
            nearest, distance = self._get_nearest_integer_level(price)
            if distance > 0 and distance < 15.0:
                score += 2.0
            elif distance < 0 and abs(distance) < 15.0:
                score += 1.0  # 刚突破也有价值
        
        # +1分: DEX新池来源
        if source == "gecko_new_pool":
            score += 1.0
        
        # 改进6: 资金费率评分
        funding_rate = candidate.get("funding_rate", None)
        if funding_rate is not None:
            if funding_rate < -0.0001:   # 负费率：空头占优，接近LAB启动前状态
                score += 1.5
            elif funding_rate < 0:       # 轻微负费率
                score += 1.0
            elif funding_rate < 0.0001:  # 接近零
                score += 0.5
            # 正费率不加分（市场已经有热度了）
        
        # Bug 5 修复: 给新上市代币加分
        days_listed = candidate.get("days_listed", 9999)
        if days_listed <= 90:
            score += 2.0   # 非常新，+2分
        elif days_listed <= 180:
            score += 1.5   # 半年内，+1.5分
        elif days_listed <= 365:
            score += 1.0   # 一年内，+1分
        elif days_listed <= 548:
            score += 0.5   # 18月内，+0.5分
        # 超过18月不加分（已被filter()排除）
        
        return round(score, 1)
    
    def get_top_candidates(self, candidates: List[Dict], top_n: int = 30) -> List[Dict]:
        """
        对过滤后的候选按 quick_score 排序，取前 top_n 个
        在每个 candidate dict 里添加 "quick_score" 字段
        """
        # 计算每个候选的分数
        scored = []
        for cand in candidates:
            qscore = self.score_quick(cand)
            cand["quick_score"] = qscore
            scored.append(cand)
        
        # 按分数降序排列
        scored.sort(key=lambda x: x.get("quick_score", 0), reverse=True)
        
        # 取前 top_n
        top = scored[:top_n]
        
        # 打印排行榜
        print(f"\n[Filter] Top {top_n} 候选（按画像匹配度排序）:")
        print(f"{'#':<3} {'代币':<10} {'价格':<12} {'成交量':<12} {'变化':<10} {'分数':<6} {'特征'}")
        print("-" * 80)
        
        for i, c in enumerate(top, 1):
            price = c.get("price", 0)
            vol = c.get("volume_usd_24h", 0)
            change = c.get("change_24h_pct", 0)
            score = c.get("quick_score", 0)
            
            # 特征标签
            tags = []
            if 0.01 <= price <= 2.0:
                tags.append("甜蜜区")
            if -5.0 <= change <= 5.0:
                tags.append("横盘")
            if c.get("source") == "gecko_new_pool":
                tags.append("DEX新池")
            
            # 检查是否接近整数关口
            if price > 0:
                nearest, dist = self._get_nearest_integer_level(price)
                if 0 < dist < 15:
                    tags.append(f"接近${nearest}")
            
            tag_str = ", ".join(tags) if tags else "-"
            
            vol_str = f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K"
            
            print(f"{i:<3} {c['symbol']:<10} ${price:<11.4f} {vol_str:<12} {change:+6.1f}%   {score:<6.1f}  [{tag_str}]")
        
        return top


def batch_fetch_funding_rates(symbols: List[str]) -> Dict[str, float]:
    """
    并发获取多个代币的当前资金费率
    
    endpoint: GET https://www.okx.com/api/v5/public/funding-rate
    params: {"instId": "LAB-USDT-SWAP"}
    
    返回: {"LAB": -0.0002, "VELVET": 0.0001, ...}
    失败的返回 0.0
    """
    if not symbols:
        return {}
    
    results = {}
    success_count = 0
    
    def fetch_one(symbol: str) -> tuple:
        try:
            url = "https://www.okx.com/api/v5/public/funding-rate"
            params = {"instId": f"{symbol.upper()}-USDT-SWAP"}
            
            resp = requests.get(url, params=params, timeout=5)
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("code") == "0" and data.get("data"):
                    rate = float(data["data"][0].get("fundingRate", "0"))
                    return (symbol, rate)
        except:
            pass
        return (symbol, 0.0)
    
    print(f"[Funding] 批量获取 {len(symbols)} 个代币资金费率...")
    
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(fetch_one, sym): sym for sym in symbols}
        
        for future in as_completed(futures, timeout=30):
            try:
                symbol, rate = future.result()
                results[symbol] = rate
                if rate != 0.0:
                    success_count += 1
            except:
                pass
    
    print(f"[Funding] 完成 {success_count}/{len(symbols)}")
    
    return results


def run_fast_filter(universe: List[Dict]) -> List[Dict]:
    """
    一键执行：过滤 → 预评分 → 补充资金费率 → 返回Top30
    
    流程：
    1. ProfileFilter().filter(universe) → 过滤
    2. ProfileFilter().get_top_candidates(filtered, 30) → 预评分排序
    3. batch_fetch_funding_rates([c["symbol"] for c in top30]) → 补充费率
    4. 把费率附加到每个 candidate: {"funding_rate": float}
    5. 返回增强后的 top30
    """
    pf = ProfileFilter()
    
    # 1. 过滤
    filtered = pf.filter(universe)
    
    if not filtered:
        print("[Filter] 警告：无候选通过过滤")
        return []
    
    # 2. 预评分排序
    top30 = pf.get_top_candidates(filtered, 30)
    
    if not top30:
        return []
    
    # 3. 批量获取资金费率
    symbols = [c["symbol"] for c in top30]
    funding_rates = batch_fetch_funding_rates(symbols)
    
    # 4. 附加费率到候选
    for cand in top30:
        sym = cand["symbol"]
        cand["funding_rate"] = funding_rates.get(sym, 0.0)
    
    print(f"\n[Filter] ✅ 快速筛选完成，返回 {len(top30)} 个候选")
    
    return top30


if __name__ == "__main__":
    # 测试
    import sys
    sys.path.insert(0, "/mnt/c/Users/朱/Desktop/hexagon_copilot/mm_tracker")
    from scanner.universe import get_full_universe
    
    print("获取全市场代币...")
    universe = get_full_universe()
    
    print("\n执行快速筛选...")
    result = run_fast_filter(universe)
    
    print(f"\n最终结果: {len(result)} 个候选")
"""
MMTracker Scanner - 快速画像过滤器
对全市场代币进行第一轮筛选，把 400+ 代币缩减到 20-30 个候选
【修复版】2025-01 增加: 涨幅漏斗、72h/7d趋势因子
"""

import requests
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

logger = logging.getLogger(__name__)


class GainTracker:
    """
    涨幅漏斗模块 - 核心修复！
    自动追踪24h涨幅>5%的代币，无论是否在候选池中
    """
    
    GAIN_THRESHOLDS = {
        'hot': 10.0,      # 暴涨: 10%+
        'strong': 5.0,   # 强势: 5%+
        'moderate': 3.0, # 温和: 3%+
    }
    
    @staticmethod
    def fetch_all_token_24h_change() -> Dict[str, Dict]:
        """
        获取OKX所有现货代币的24h涨跌幅
        返回: {symbol: {price, change_24h_pct, volume_usd_24h, ...}}
        """
        url = "https://www.okx.com/api/v5/market/tickers"
        params = {"instType": "SPOT"}
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            
            if data.get('code') != '0':
                return {}
            
            result = {}
            for t in data.get('data', []):
                inst = t.get('instId', '')
                if not inst.endswith('-USDT'):
                    continue
                
                symbol = inst.replace('-USDT', '')
                last = float(t.get('last', 0))
                open_24h = float(t.get('open24h', 0))
                vol24h = float(t.get('vol24h', 0))
                
                if open_24h > 0:
                    pct_change = (last - open_24h) / open_24h * 100
                else:
                    pct_change = 0
                
                result[symbol] = {
                    'symbol': symbol,
                    'price': last,
                    'change_24h_pct': pct_change,
                    'volume_usd_24h': vol24h,
                    'open_24h': open_24h,
                }
            
            return result
        
        except Exception as e:
            print(f"[GainTracker] 获取涨幅数据失败: {e}")
            return {}
    
    @staticmethod
    def get_gainers(threshold: float = 5.0) -> List[Dict]:
        """
        获取24h涨幅超过threshold的代币
        这是"涨幅漏斗"的核心功能
        """
        all_tokens = GainTracker.fetch_all_token_24h_change()
        
        gainers = []
        for symbol, info in all_tokens.items():
            if info['change_24h_pct'] >= threshold:
                gainers.append(info)
        
        # 按涨幅排序
        gainers.sort(key=lambda x: x['change_24h_pct'], reverse=True)
        
        return gainers
    
    @staticmethod
    def get_top_gainers(top_n: int = 20) -> List[Dict]:
        """获取涨幅榜前N名"""
        all_tokens = GainTracker.fetch_all_token_24h_change()
        
        sorted_tokens = sorted(
            all_tokens.items(), 
            key=lambda x: x[1]['change_24h_pct'], 
            reverse=True
        )
        
        return [info for symbol, info in sorted_tokens[:top_n]]


class TrendAnalyzer:
    """
    趋势分析器 - 72h/7d趋势因子
    分析代币的长期趋势，不仅是短期波动
    """
    
    @staticmethod
    def get_trend_data(symbol: str) -> Dict[str, Any]:
        """
        获取代币的多周期趋势数据
        返回: {pct_4h, pct_24h, pct_72h, pct_7d, volatility, volume_ratio, ...}
        """
        url = f"https://www.okx.com/api/v5/market/candles"
        params = {
            "instId": f"{symbol.upper()}-USDT",
            "bar": "1H",
            "limit": 200
        }
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            
            if data.get('code') != '0' or not data.get('data'):
                return {}
            
            candles = data['data']
            closes = [float(c[4]) for c in candles]
            vols = [float(c[5]) for c in candles]
            
            if len(closes) < 50:
                return {}
            
            current = closes[0]
            
            # 多周期涨幅
            pct_4h = (closes[0] - closes[4]) / closes[4] * 100 if len(closes) > 4 else 0
            pct_24h = (closes[0] - closes[24]) / closes[24] * 100 if len(closes) > 24 else 0
            pct_72h = (closes[0] - closes[72]) / closes[72] * 100 if len(closes) > 72 else 0
            pct_7d = (closes[0] - closes[168]) / closes[168] * 100 if len(closes) > 168 else 0
            
            # 波动率
            returns = [closes[i]/closes[i+1]-1 for i in range(len(closes)-1) if closes[i+1] != 0]
            volatility = (sum(r*r for r in returns)/len(returns)) ** 0.5 * 100 if returns else 0
            
            # 成交量变化
            vol_now = sum(vols[:24])
            vol_prev = sum(vols[24:48]) if len(vols) > 48 else sum(vols[-24:])
            volume_ratio = vol_now / vol_prev if vol_prev > 0 else 1
            
            # 从最低点上涨
            low_72h = min(closes[:72]) if len(closes) >= 72 else min(closes)
            pct_from_low = (current - low_72h) / low_72h * 100 if low_72h > 0 else 0
            
            return {
                'symbol': symbol,
                'price': current,
                'pct_4h': pct_4h,
                'pct_24h': pct_24h,
                'pct_72h': pct_72h,
                'pct_7d': pct_7d,
                'pct_from_low': pct_from_low,
                'volatility': volatility,
                'volume_ratio': volume_ratio,
            }
        
        except Exception as e:
            return {}
    
    @staticmethod
    def score_trend(trend_data: Dict) -> float:
        """
        给趋势打分 (0-10分)
        """
        if not trend_data:
            return 0
        
        score = 0
        reasons = []
        
        # 24h涨幅
        if trend_data.get('pct_24h', 0) > 10:
            score += 3
            reasons.append('24h>10%')
        elif trend_data.get('pct_24h', 0) > 5:
            score += 2
            reasons.append('24h>5%')
        
        # 72h趋势
        if trend_data.get('pct_72h', 0) > 10:
            score += 3
            reasons.append('72h>10%')
        elif trend_data.get('pct_72h', 0) > 5:
            score += 2
            reasons.append('72h>5%')
        
        # 从低点上涨
        if trend_data.get('pct_from_low', 0) > 15:
            score += 2
            reasons.append('底部起来>15%')
        
        # 成交量放大
        if trend_data.get('volume_ratio', 1) > 1.5:
            score += 1
            reasons.append('放量')
        
        # 7天趋势
        if trend_data.get('pct_7d', 0) > 20:
            score += 2
            reasons.append('7d>20%')
        
        trend_data['trend_score'] = score
        trend_data['reasons'] = reasons
        
        return score


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


def run_fast_filter(universe: List[Dict], enable_gain_tracker: bool = True, enable_technical: bool = True) -> List[Dict]:
    """
    一键执行：过滤 → 涨幅漏斗 → 技术分析 → 返回Top30
    
    【完整版】整合所有量化因子:
    - 涨幅漏斗: 24h涨幅>5%
    - 趋势分析: 72h/7d趋势
    - 技术分析: EMA/RSI/成交量/资金费率/多空比/支撑阻力
    
    流程：
    1. ProfileFilter().filter(universe) → 基础过滤
    2. 涨幅漏斗: 补充24h涨幅>5%的代币
    3. 趋势分析: 补充72h涨幅>10%的代币
    4. 技术分析: 对所有候选进行完整技术评分
    5. 综合排序返回Top30
    """
    pf = ProfileFilter()
    
    # 1. 基础过滤
    filtered = pf.filter(universe)
    
    if not filtered:
        print("[Filter] 警告：无候选通过过滤")
        filtered = []
    
    # ========== 2. 涨幅漏斗 ==========
    if enable_gain_tracker:
        print("\n[GainTracker] 启动涨幅漏斗...")
        gainers = GainTracker.get_gainers(threshold=5.0)
        
        print(f"[GainTracker] 发现 {len(gainers)} 个24h涨幅>5%的代币")
        
        existing_symbols = {c['symbol'] for c in filtered}
        gainer_count = 0
        
        for g in gainers:
            symbol = g['symbol']
            if symbol not in existing_symbols:
                g['source'] = 'gain_tracker'
                g['quick_score'] = 8.0
                g['is_gainer'] = True
                g['gain_level'] = 'hot' if g['change_24h_pct'] >= 10 else 'strong'
                filtered.append(g)
                gainer_count += 1
                existing_symbols.add(symbol)
        
        # 补充72h趋势强劲但24h涨幅不够的代币
        print("\n[GainTracker] 检查72h趋势强劲但24h涨幅不足的代币...")
        
        all_tokens = GainTracker.fetch_all_token_24h_change()
        
        for symbol, info in all_tokens.items():
            if symbol in existing_symbols:
                continue
            trend = TrendAnalyzer.get_trend_data(symbol)
            if trend and trend.get('pct_72h', 0) > 10:
                info['source'] = 'trend_strong'
                info['quick_score'] = 7.0
                info['is_gainer'] = False
                info['trend_data'] = trend
                filtered.append(info)
                gainer_count += 1
                existing_symbols.add(symbol)
                print(f"  ➕ 添加 {symbol}: 72h+{trend.get('pct_72h'):.1f}%")
        
        print(f"[GainTracker] 添加了 {gainer_count} 个涨幅/趋势代币")
    
    if not filtered:
        print("[Filter] 警告：过滤后无候选")
        return []
    
    # ========== 3. 完整技术分析 ==========
    if enable_technical:
        print("\n[TechnicalAnalyzer] 执行完整技术分析...")
        
        try:
            from scanner.technical_analyzer import TechnicalAnalyzer
            
            tech_scores = {}
            symbols_to_analyze = [c['symbol'] for c in filtered[:30]]  # 限制API调用
            
            for symbol in symbols_to_analyze:
                try:
                    analyzer = TechnicalAnalyzer(symbol)
                    if analyzer.fetch_all_data():
                        result = analyzer.analyze()
                        tech_scores[symbol] = result
                        print(f"  ✅ {symbol}: 技术评分 {result.get('score', 0)}/20")
                    else:
                        print(f"  ❌ {symbol}: 数据获取失败")
                except Exception as e:
                    print(f"  ❌ {symbol}: {str(e)[:30]}")
            
            # 把技术分加到候选
            for cand in filtered:
                symbol = cand['symbol']
                if symbol in tech_scores:
                    cand['tech_score'] = tech_scores[symbol].get('score', 0)
                    cand['tech_data'] = tech_scores[symbol]
            
            print(f"[TechnicalAnalyzer] 完成 {len(tech_scores)} 个代币的技术分析")
            
        except ImportError as e:
            print(f"[TechnicalAnalyzer] 模块未找到，跳过技术分析: {e}")
    
    # ========== 4. 综合评分排序 ==========
    for cand in filtered:
        quick = cand.get('quick_score', 0)
        trend = cand.get('trend_score', 0)
        tech = cand.get('tech_score', 0)
        
        # 加权: 技术分权重最高
        cand['combined_score'] = quick * 0.2 + trend * 0.3 + tech * 0.5
    
    # 按综合分排序
    filtered.sort(key=lambda x: x.get('combined_score', 0), reverse=True)
    
    top30 = filtered[:30]
    
    print(f"\n[Filter] ✅ 扫描完成，返回 {len(top30)} 个候选")
    
    # 打印Top10
    print(f"\n{'='*60}")
    print("📊 Top 10 候选 (技术分析+涨幅漏斗)")
    print("="*60)
    for i, c in enumerate(top10 := top30[:10], 1):
        symbol = c['symbol']
        tech_score = c.get('tech_score', 0)
        change_24h = c.get('change_24h_pct', 0)
        combined = c.get('combined_score', 0)
        
        # 技术分析详情
        tech_data = c.get('tech_data', {})
        reasons = tech_data.get('reasons', [])[:3]
        
        emoji = '🚀' if tech_score >= 10 else '📈' if tech_score >= 6 else '⚠️'
        print(f"{i:2}. {emoji} {symbol:8} 技术分:{tech_score:2}/20  24h:{change_24h:+.1f}%  综合:{combined:.1f}")
        if reasons:
            print(f"     → {', '.join(reasons)}")
    
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
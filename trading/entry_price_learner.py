"""
挂单价学习器 - 核心自学习功能
学习在哪个价位挂单最容易成交且不追高
基于Freqtrade的Hyperopt思想，但用历史数据自动学习
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import statistics

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """交易记录"""
    token: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    hold_minutes: int
    entry_signals: List[str]
    # 挂单信息
    order_type: str = "limit"  # limit, market
    entry_offset_pct: float = 0  # 挂单价相对于买一价的偏移
    filled_at_limit: bool = False  # 是否限价单成交


@dataclass
class PriceLevel:
    """价格档位"""
    price: float
    volume: float
    orders: int


class EntryPriceLearner:
    """
    挂单价学习器
    目标：学习最佳挂单价偏移百分比
    
    学习方法：
    1. 记录每次挂单的价格和偏移
    2. 分析成交vs未成交的差异
    3. 分析盈利vs亏损的挂单价差异
    4. 输出最佳偏移建议
    """
    
    def __init__(self, trades_file: str = "trading/live_trades.json"):
        self.trades_file = trades_file
        self.learned_params: Dict[str, dict] = {}
        
        # 按代币统计
        self.token_stats: Dict[str, dict] = defaultdict(lambda: {
            "total_orders": 0,
            "limit_filled": 0,
            "market_filled": 0,
            "avg_limit_offset": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "avg_winning_offset": 0,  # 盈利交易的平均挂单偏移
            "avg_losing_offset": 0,   # 亏损交易的平均挂单偏移
        })
        
        self._load_trades()
        
    def _load_trades(self):
        """加载交易历史"""
        try:
            with open(self.trades_file, "r") as f:
                trades = json.load(f)
                
            for t in trades:
                if t.get("type") != "ENTRY":
                    continue
                    
                token = t.get("token", "")
                entry_price = t.get("entry_price", 0)
                entry_offset = t.get("market_context", {}).get("entry_offset_pct", 0)
                order_type = t.get("market_context", {}).get("order_type", "market")
                
                self.token_stats[token]["total_orders"] += 1
                
                if order_type == "limit":
                    self.token_stats[token]["avg_limit_offset"] = (
                        (self.token_stats[token]["avg_limit_offset"] * (self.token_stats[token]["limit_filled"]) + entry_offset) /
                        (self.token_stats[token]["limit_filled"] + 1)
                    )
                    self.token_stats[token]["limit_filled"] += 1
                else:
                    self.token_stats[token]["market_filled"] += 1
                    
        except Exception as e:
            logger.warning(f"[挂单学习] 加载交易历史失败: {e}")
    
    def record_trade_result(
        self, 
        token: str, 
        entry_price: float, 
        exit_price: float, 
        entry_offset_pct: float,
        hold_minutes: int,
        signals: List[str]
    ):
        """
        记录交易结果，用于学习
        在平仓时调用
        """
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        
        stats = self.token_stats[token]
        
        if pnl_pct > 0:
            stats["winning_trades"] += 1
            # 盈利交易的挂单价偏移学习
            if stats["winning_trades"] == 1:
                stats["avg_winning_offset"] = entry_offset_pct
            else:
                stats["avg_winning_offset"] = (
                    (stats["avg_winning_offset"] * (stats["winning_trades"] - 1) + entry_offset_pct) /
                    stats["winning_trades"]
                )
        else:
            stats["losing_trades"] += 1
            if stats["losing_trades"] == 1:
                stats["avg_losing_offset"] = entry_offset_pct
            else:
                stats["avg_losing_offset"] = (
                    (stats["avg_losing_offset"] * (stats["losing_trades"] - 1) + entry_offset_pct) /
                    stats["losing_trades"]
                )
        
        logger.info(f"[挂单学习] {token} 平仓: PnL={pnl_pct:+.2f}%, 挂单偏移={entry_offset_pct:+.2f}%")
        
        self._save_learned_params()
    
    def get_optimal_offset(self, token: str) -> dict:
        """
        获取最佳挂单偏移
        这是核心输出函数
        """
        stats = self.token_stats.get(token, {})
        
        total = stats.get("total_orders", 0)
        
        if total == 0:
            # 没有历史数据，返回默认参数
            return {
                "method": "default",
                "offset_pct": 0.5,
                "confidence": 0.0,
                "reason": "无历史数据，使用默认0.5%"
            }
        
        # 学习逻辑：
        # 1. 如果盈利交易的挂单偏移 < 亏损交易，说明挂低一点更容易盈利
        # 2. 如果盈利交易的挂单偏移 > 亏损交易，说明挂太低反而不好
        
        win_offset = stats.get("avg_winning_offset", 0)
        loss_offset = stats.get("avg_losing_offset", 0)
        
        win_count = stats.get("winning_trades", 0)
        loss_count = stats.get("losing_trades", 0)
        
        # 基础偏移
        base_offset = stats.get("avg_limit_offset", 0.5)
        
        # 调整逻辑
        if win_count > 0 and loss_count > 0:
            if win_offset < loss_offset:
                # 盈利时挂单价更低，说明偏低策略有效
                # 建议进一步降低
                suggested_offset = win_offset * 0.9
                reason = f"盈利交易挂单偏移{win_offset:.2f}% < 亏损{loss_offset:.2f}%，偏低策略有效"
            elif win_offset > loss_offset:
                # 盈利时挂单价更高，说明需要追高一点
                suggested_offset = win_offset * 1.1
                reason = f"盈利交易挂单偏移{win_offset:.2f}% > 亏损{loss_offset:.2f}%，需追高"
            else:
                suggested_offset = base_offset
                reason = "盈亏挂单偏移相近，保持当前策略"
        else:
            suggested_offset = base_offset
            reason = f"样本不足({win_count}胜/{loss_count}负)，保持历史平均"
        
        # 置信度计算
        confidence = min(total / 20, 1.0)  # 20笔交易达到最高置信度
        
        return {
            "method": "learned",
            "offset_pct": round(suggested_offset, 2),
            "confidence": round(confidence, 2),
            "reason": reason,
            "stats": {
                "total_trades": total,
                "winning": win_count,
                "losing": loss_count,
                "win_rate": win_count / total if total > 0 else 0,
                "avg_win_offset": round(win_offset, 2),
                "avg_loss_offset": round(loss_offset, 2)
            }
        }
    
    def get_entry_price(
        self, 
        token: str, 
        current_bid: float, 
        current_ask: float,
        order_type: str = "limit"
    ) -> dict:
        """
        获取推荐入场价
        综合学习结果和当前市场情况
        """
        if order_type == "market":
            # 市价单直接用卖一价
            return {
                "price": current_ask,
                "type": "market",
                "offset_pct": 0,
                "note": "市价单，不考虑偏移"
            }
        
        # 限价单
        optimal = self.get_optimal_offset(token)
        offset_pct = optimal["offset_pct"]
        
        # 计算挂单价：基于买一价
        entry_price = current_bid * (1 - offset_pct / 100)
        
        return {
            "price": round(entry_price, 5),
            "type": "limit",
            "offset_pct": offset_pct,
            "confidence": optimal["confidence"],
            "method": optimal["method"],
            "reason": optimal.get("reason", ""),
            "current_bid": current_bid,
            "current_ask": current_ask,
            "discount_to_ask": round((current_ask - entry_price) / current_ask * 100, 2)
        }
    
    def _save_learned_params(self):
        """保存学习到的参数"""
        try:
            with open("config/entry_price_learned.json", "w") as f:
                json.dump(dict(self.token_stats), f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"[挂单学习] 保存参数失败: {e}")
    
    def generate_report(self) -> str:
        """生成学习报告"""
        lines = ["=== 挂单价学习报告 ===", ""]
        
        for token, stats in self.token_stats.items():
            if stats["total_orders"] == 0:
                continue
                
            optimal = self.get_optimal_offset(token)
            
            lines.append(f"代币: {token}")
            lines.append(f"  总交易: {stats['total_orders']}")
            lines.append(f"  胜率: {stats['winning_trades'] / stats['total_orders'] * 100:.1f}%")
            lines.append(f"  建议挂单偏移: {optimal['offset_pct']:.2f}%")
            lines.append(f"  置信度: {optimal['confidence']:.0%}")
            lines.append(f"  原因: {optimal.get('reason', '')}")
            lines.append("")
        
        return "\n".join(lines)


class SmartOrderGenerator:
    """
    智能订单生成器
    结合挂单价学习和分批挂单
    """
    
    def __init__(self, learner: EntryPriceLearner = None):
        self.learner = learner or EntryPriceLearner()
        
    def generate_orders(
        self, 
        token: str, 
        amount_usdt: float,
        current_bid: float,
        current_ask: float,
        split_count: int = 3
    ) -> List[dict]:
        """
        生成智能订单列表
        分批挂单，越低越容易成交
        """
        # 获取推荐挂单价
        entry = self.learner.get_entry_price(token, current_bid, current_ask)
        
        base_price = entry["price"]
        amount_per_order = amount_usdt / split_count
        
        orders = []
        
        for i in range(split_count):
            # 每批价格递减，形成阶梯
            # 越低越容易成交，但可能买不到
            discount = i * 0.15  # 每批递减0.15%
            price = base_price * (1 - discount / 100)
            size = amount_per_order / price
            
            orders.append({
                "order_id": f"{token}_{i+1}",
                "price": round(price, 5),
                "size": round(size, 2),
                "total_usdt": round(price * size, 2),
                "discount_pct": round(discount, 2),
                "priority": "high" if i == 0 else ("medium" if i == 1 else "low")
            })
        
        return orders


# ==================== 测试 ====================
if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    
    # 模拟一些交易数据
    learner = EntryPriceLearner()
    
    # 模拟OP的交易结果
    learner.record_trade_result("OP", 0.112, 0.118, 0.5, 60, ["rsi_oversold"])
    learner.record_trade_result("OP", 0.118, 0.115, 0.3, 45, ["breakout"])
    learner.record_trade_result("OP", 0.115, 0.110, 0.8, 30, ["volume_spike"])
    
    # 获取推荐
    optimal = learner.get_optimal_offset("OP")
    print("=== OP 挂单价学习结果 ===")
    print(f"建议偏移: {optimal['offset_pct']:.2f}%")
    print(f"置信度: {optimal['confidence']:.0%}")
    print(f"原因: {optimal['reason']}")
    
    # 测试智能订单生成
    generator = SmartOrderGenerator(learner)
    orders = generator.generate_orders("OP", 888, 0.1115, 0.1116, 3)
    
    print("\n=== 智能订单列表 ===")
    for o in orders:
        print(f"  {o['price']:.5f} x {o['size']:.2f} = ${o['total_usdt']:.2f} ({o['discount_pct']}%折扣) [{o['priority']}]")
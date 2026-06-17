"""
DCA效果回测 - 用实盘交易记录模拟DCA效果
对比: 如果当时开启了DCA，会是什么结果?
"""

import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.nfi_dca import NFIDCAManager


def main():
    print("=" * 60)
    print("DCA效果回测 - 实盘交易模拟")
    print("=" * 60)
    
    # 加载实盘交易
    with open("trading/live_trades.json") as f:
        trades = json.load(f)
    
    # 只取已完成的交易
    completed = [t for t in trades if t.get("type") == "EXIT" and t.get("exit_reason") != "manual_reset"]
    
    print(f"\n共 {len(completed)} 笔实盘交易\n")
    
    dca_manager = NFIDCAManager()
    
    total_original_pnl = 0
    total_dca_pnl = 0
    dca_triggers = 0
    
    results = []
    
    for i, trade in enumerate(completed):
        token = trade.get("token", "UNKNOWN")
        entry_price = trade.get("entry_price", 0)
        exit_price = trade.get("exit_price", 0)
        original_pnl = trade.get("pnl", 0)
        
        if entry_price <= 0 or exit_price <= 0:
            continue
        
        # 计算实盘涨跌幅
        profit_pct = (exit_price - entry_price) / entry_price
        
        # 模拟DCA
        # 假设首仓亏损后，每下跌4%加仓一次
        simulated_pnl = profit_pct
        dca_count = 0
        
        # 模拟最多4次加仓
        current_profit = profit_pct
        for step in range(4):
            # 计算加仓阈值 (mode_0: -4%, -6%, -9%, -12%)
            thresholds = [-0.04, -0.06, -0.09, -0.12]
            if step >= len(thresholds):
                break
            
            if current_profit < thresholds[step]:
                # 模拟加仓后成本降低
                # 假设加仓50%仓位，价格向当前价格靠拢
                dca_count += 1
                # 简化模拟：每次加仓减少2%损失
                current_profit += 0.02
        
        # DCA后的盈亏 (简化模型)
        if dca_count > 0:
            simulated_pnl = current_profit
            dca_triggers += 1
        
        total_original_pnl += original_pnl
        total_dca_pnl += simulated_pnl * (entry_price * 888 / entry_price)  # 还原为USD
        
        result = {
            "token": token,
            "original_pnl": original_pnl,
            "profit_pct": profit_pct * 100,
            "dca_count": dca_count,
            "simulated_pnl": simulated_pnl * (entry_price * 888 / entry_price),
            "improvement": (simulated_pnl - profit_pct) * 100,
        }
        results.append(result)
        
        dca_mark = f" DCA×{dca_count}" if dca_count > 0 else ""
        print(f"[{i+1}] {token}: 原始{original_pnl:+.2f}U → 模拟{result['simulated_pnl']:+.2f}U{dca_mark}")
    
    print("\n" + "=" * 60)
    print("汇总对比")
    print("=" * 60)
    
    print(f"\n原始实盘:")
    print(f"  总盈亏: {total_original_pnl:.2f}U")
    print(f"  交易数: {len(completed)}")
    
    print(f"\n模拟DCA后:")
    print(f"  总盈亏: {total_dca_pnl:.2f}U")
    print(f"  触发次数: {dca_triggers}")
    
    improvement = total_dca_pnl - total_original_pnl
    if improvement > 0:
        print(f"\n✓ DCA改善: {improvement:.2f}U ({improvement/abs(total_original_pnl)*100:.1f}%)")
    else:
        print(f"\n✗ DCA未改善: {improvement:.2f}U")
    
    print("\n" + "-" * 60)
    print("按交易明细")
    print("-" * 60)
    print(f"{'Token':<8} {'原始PnL':<10} {'盈亏%':<10} {'DCA次数':<8} {'改善%':<10}")
    print("-" * 60)
    for r in results:
        print(f"{r['token']:<8} {r['original_pnl']:+8.2f}U {r['profit_pct']:+8.1f}% {r['dca_count']:<8} {r['improvement']:+8.1f}%")


if __name__ == "__main__":
    main()
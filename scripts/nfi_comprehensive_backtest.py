#!/usr/bin/env python3
"""
NFI + Freqtrade 风格综合回测脚本
使用真实OKX历史数据进行验证

分析:
1. 历史交易表现
2. NFI量化因子效果
3. Freqtrade Exit策略效果
4. 优化建议
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple
from collections import defaultdict

# 加载历史数据
def load_live_trades():
    """加载真实交易数据"""
    with open("trading/live_trades.json", "r") as f:
        return json.load(f)

def load_scan_reports():
    """加载扫描报告"""
    reports = []
    reports_dir = "reports"
    if os.path.exists(reports_dir):
        for f in sorted(os.listdir(reports_dir)):
            if f.startswith("scan_") and f.endswith(".json"):
                try:
                    with open(f"{reports_dir}/{f}", "r") as fp:
                        reports.append(json.load(fp))
                except:
                    pass
    return reports

def analyze_trade_performance(trades: List[Dict]) -> Dict:
    """分析交易表现"""
    # 只看完成的交易（EXIT类型，排除ABANDONED）
    completed = [t for t in trades if t.get("type") == "EXIT"]
    
    if not completed:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0, "avg_pnl": 0}
    
    wins = sum(1 for t in completed if t.get("win", False))
    losses = len(completed) - wins
    
    pnls = [t.get("pnl", 0) for t in completed]
    avg_pnl = sum(pnls) / len(pnls) if pnls else 0
    
    # 按exit_reason分组
    exit_reasons = defaultdict(list)
    for t in completed:
        reason = t.get("exit_reason", "UNKNOWN")
        exit_reasons[reason].append(t)
    
    return {
        "total": len(completed),
        "wins": wins,
        "losses": losses,
        "win_rate": wins / len(completed) if completed else 0,
        "avg_pnl": avg_pnl,
        "total_pnl": sum(pnls),
        "exit_reasons": {k: len(v) for k, v in exit_reasons.items()},
        "by_token": analyze_by_token(completed),
    }

def analyze_by_token(trades: List[Dict]) -> Dict:
    """按币种分析"""
    token_stats = defaultdict(lambda: {"trades": 0, "wins": 0, "total_pnl": 0})
    
    for t in trades:
        token = t.get("token", "UNKNOWN")
        token_stats[token]["trades"] += 1
        if t.get("win", False):
            token_stats[token]["wins"] += 1
        token_stats[token]["total_pnl"] += t.get("pnl", 0)
    
    return dict(token_stats)

def analyze_signal_effectiveness(trades: List[Dict]) -> Dict:
    """分析信号有效性"""
    signal_stats = defaultdict(lambda: {"total": 0, "wins": 0, "avg_score": 0})
    
    for t in trades:
        if t.get("type") != "EXIT":
            continue
        
        signals = t.get("signals", [])
        score = t.get("score", 0)
        win = t.get("win", False)
        
        for sig in signals:
            signal_stats[sig]["total"] += 1
            if win:
                signal_stats[sig]["wins"] += 1
            signal_stats[sig]["avg_score"] += score
    
    # 计算胜率
    result = {}
    for sig, stats in signal_stats.items():
        win_rate = stats["wins"] / stats["total"] if stats["total"] > 0 else 0
        avg_score = stats["avg_score"] / stats["total"] if stats["total"] > 0 else 0
        result[sig] = {
            "count": stats["total"],
            "win_rate": win_rate,
            "avg_score": avg_score,
        }
    
    return result

def analyze_entry_conditions(trades: List[Dict]) -> Dict:
    """分析入场条件效果"""
    # 按score区间分析
    score_buckets = defaultdict(lambda: {"total": 0, "wins": 0, "pnl": 0})
    
    for t in trades:
        if t.get("type") != "EXIT":
            continue
        
        score = t.get("score", 0)
        
        # 分数区间
        if score < 2:
            bucket = "0-2"
        elif score < 4:
            bucket = "2-4"
        elif score < 6:
            bucket = "4-6"
        else:
            bucket = "6+"
        
        score_buckets[bucket]["total"] += 1
        if t.get("win", False):
            score_buckets[bucket]["wins"] += 1
        score_buckets[bucket]["pnl"] += t.get("pnl", 0)
    
    # 计算每个区间的表现
    result = {}
    for bucket, stats in score_buckets.items():
        win_rate = stats["wins"] / stats["total"] if stats["total"] > 0 else 0
        result[bucket] = {
            "trades": stats["total"],
            "win_rate": win_rate,
            "total_pnl": stats["pnl"],
            "avg_pnl": stats["pnl"] / stats["total"] if stats["total"] > 0 else 0,
        }
    
    return result

def analyze_hold_time(trades: List[Dict]) -> Dict:
    """分析持仓时间"""
    hold_times = []
    
    for t in trades:
        if t.get("type") != "EXIT":
            continue
        
        # 计算持仓时间
        entry_ts = t.get("timestamp", "")
        exit_ts = t.get("exit_timestamp", "")
        
        if entry_ts and exit_ts:
            try:
                entry_time = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
                exit_time = datetime.fromisoformat(exit_ts.replace("Z", "+00:00"))
                hold_minutes = (exit_time - entry_time).total_seconds() / 60
                hold_times.append({
                    "token": t.get("token"),
                    "minutes": hold_minutes,
                    "win": t.get("win", False),
                    "pnl": t.get("pnl", 0),
                })
            except:
                pass
    
    if not hold_times:
        return {"short": 0, "medium": 0, "long": 0}
    
    short = sum(1 for h in hold_times if h["minutes"] < 60)
    medium = sum(1 for h in hold_times if 60 <= h["minutes"] < 240)
    long_ = sum(1 for h in hold_times if h["minutes"] >= 240)
    
    return {
        "short_trades": short,
        "medium_trades": medium,
        "long_trades": long_,
        "avg_hold_minutes": sum(h["minutes"] for h in hold_times) / len(hold_times),
        "details": hold_times[:10],  # 前10个详细
    }

def simulate_nfi_exit(trades: List[Dict]) -> Dict:
    """
    模拟 NFI + Freqtrade 风格的 Exit 策略效果
    看看如果用新策略，能减少多少亏损
    """
    from trading.dynamic_exit import EnhancedExitManager
    
    params = {
        "trailing_stop": True,
        "trailing_stop_positive": 0.05,
        "trailing_stop_positive_offset": 0.02,
        "use_custom_stoploss": True,
        "stoploss": 0.03,
        "stoploss_from_profit": {"0.10": -0.02, "0.20": -0.03, "0.30": -0.05},
        "use_custom_roi": True,
        "minimal_roi": {"0": 0.30, "30": 0.15, "60": 0.08, "120": 0.05},
        "use_custom_exit": True,
        "custom_exit_conditions": {"rsi_overbought": 75, "time_based_exit": 240},
    }
    
    manager = EnhancedExitManager(params)
    
    improvements = []
    
    for t in trades:
        if t.get("type") != "EXIT":
            continue
        
        # 模拟新策略
        entry_price = t.get("entry_price", 0)
        exit_price = t.get("exit_price", 0)
        
        if entry_price <= 0:
            continue
        
        actual_pnl_pct = (exit_price - entry_price) / entry_price
        
        # 模拟不同持仓时间的ROI
        entry_ts = t.get("timestamp", "")
        exit_ts = t.get("exit_timestamp", "")
        
        hold_minutes = 60  # 默认
        if entry_ts and exit_ts:
            try:
                entry_time = datetime.fromisoformat(entry_ts.replace("Z", "+00:00"))
                exit_time = datetime.fromisoformat(exit_ts.replace("Z", "+00:00"))
                hold_minutes = (exit_time - entry_time).total_seconds() / 60
            except:
                pass
        
        # 新策略判断
        trade_data = {
            "entry_price": entry_price,
            "entry_time": datetime.now() - timedelta(minutes=hold_minutes),
            "rsi": 50,  # 假设
        }
        
        # 模拟价格走势：假设先涨后跌
        # 检查不同价格点是否会触发exit
        test_prices = [
            entry_price * 1.02,  # +2%
            entry_price * 1.05,  # +5% (trailing激活)
            entry_price * 1.10,  # +10% (ROI目标)
            entry_price * 0.97,  # -3% (stoploss)
            exit_price,  # 实际退出价
        ]
        
        for price in test_prices:
            result = manager.should_exit(
                pair=t.get("token", "TEST"),
                trade_data=trade_data,
                current_time=datetime.now(),
                current_price=price,
            )
            
            if result["should_exit"]:
                new_pnl_pct = (price - entry_price) / entry_price
                improvement = actual_pnl_pct - new_pnl_pct
                
                improvements.append({
                    "token": t.get("token"),
                    "actual_exit": actual_pnl_pct,
                    "simulated_exit": new_pnl_pct,
                    "trigger": result["exit_reason"],
                    "improvement": improvement,
                })
                break
    
    return {
        "analyzed": len(improvements),
        "improvements": improvements[:10],
        "avg_improvement": sum(i["improvement"] for i in improvements) / len(improvements) if improvements else 0,
    }

def generate_optimization_report(trades: List[Dict]) -> Dict:
    """生成优化报告"""
    perf = analyze_trade_performance(trades)
    signals = analyze_signal_effectiveness(trades)
    entry_conds = analyze_entry_conditions(trades)
    hold_time = analyze_hold_time(trades)
    nfi_sim = simulate_nfi_exit(trades)
    
    # 识别问题
    problems = []
    
    # 问题1: 胜率低
    if perf["win_rate"] < 0.4:
        problems.append(f"胜率{perf['win_rate']:.1%}过低，需要优化入场条件")
    
    # 问题2: 某些信号胜率低
    low_win_signals = [s for s, v in signals.items() if v["win_rate"] < 0.3 and v["count"] >= 2]
    if low_win_signals:
        problems.append(f"低胜率信号: {', '.join(low_win_signals)}")
    
    # 问题3: 持仓时间过长
    if hold_time.get("avg_hold_minutes", 0) > 240:
        problems.append(f"平均持仓时间{hold_time['avg_hold_minutes']:.0f}分钟过长")
    
    # 问题4: 止损太多
    if perf["exit_reasons"].get("STOP_LOSS", 0) > perf["total"] * 0.5:
        problems.append("止损占比过高，需要收紧止损条件")
    
    # 优化建议
    suggestions = []
    
    # 建议1: 提高入场分数阈值
    if entry_conds:
        low_score_perf = entry_conds.get("0-2", {})
        if low_score_perf.get("trades", 0) > 0 and low_score_perf.get("win_rate", 0) < 0.3:
            suggestions.append("建议提高入场分数阈值到2分以上")
    
    # 建议2: 禁用低胜率信号
    if low_win_signals:
        suggestions.append(f"建议禁用或降低权重: {', '.join(low_win_signals)}")
    
    # 建议3: 使用NFI动态止损
    suggestions.append("启用Freqtrae风格trailing_stop减少最大亏损")
    
    # 建议4: 使用动态ROI
    suggestions.append("启用dynamic_roi，根据持仓时间止盈")
    
    return {
        "performance": perf,
        "signal_effectiveness": signals,
        "entry_conditions": entry_conds,
        "hold_time": hold_time,
        "nfi_exit_simulation": nfi_sim,
        "problems": problems,
        "suggestions": suggestions,
    }

def main():
    print("=" * 70)
    print("NFI + Freqtrade 风格综合回测分析")
    print("=" * 70)
    
    # 加载数据
    trades = load_live_trades()
    reports = load_scan_reports()
    
    print(f"\n📊 数据概览:")
    print(f"   历史交易记录: {len(trades)} 笔")
    print(f"   扫描报告: {len(reports)} 份")
    
    # 生成分析报告
    report = generate_optimization_report(trades)
    
    # 1. 交易表现
    print(f"\n{'='*70}")
    print("1️⃣ 交易表现分析")
    print(f"{'='*70}")
    perf = report["performance"]
    print(f"   完成交易: {perf['total']} 笔")
    print(f"   胜: {perf['wins']}, 负: {perf['losses']}")
    print(f"   胜率: {perf['win_rate']:.1%}")
    print(f"   平均PnL: ${perf['avg_pnl']:.2f}")
    print(f"   总PnL: ${perf['total_pnl']:.2f}")
    print(f"   平仓原因分布:")
    for reason, count in perf["exit_reasons"].items():
        print(f"      - {reason}: {count}笔")
    
    # 2. 信号有效性
    print(f"\n{'='*70}")
    print("2️⃣ 信号有效性分析")
    print(f"{'='*70}")
    for sig, stats in sorted(report["signal_effectiveness"].items(), key=lambda x: -x[1]["count"])[:10]:
        print(f"   {sig}:")
        print(f"      触发{stats['count']}次, 胜率{stats['win_rate']:.1%}, 平均分数{stats['avg_score']:.1f}")
    
    # 3. 入场分数效果
    print(f"\n{'='*70}")
    print("3️⃣ 入场分数与胜率关系")
    print(f"{'='*70}")
    for bucket, stats in sorted(report["entry_conditions"].items()):
        print(f"   分数{bucket}: {stats['trades']}笔, 胜率{stats['win_rate']:.1%}, 平均PnL${stats['avg_pnl']:.2f}")
    
    # 4. 持仓时间分析
    print(f"\n{'='*70}")
    print("4️⃣ 持仓时间分析")
    print(f"{'='*70}")
    ht = report["hold_time"]
    print(f"   短期(<1h): {ht.get('short_trades', 0)}笔")
    print(f"   中期(1-4h): {ht.get('medium_trades', 0)}笔")
    print(f"   长期(>4h): {ht.get('long_trades', 0)}笔")
    print(f"   平均持仓: {ht.get('avg_hold_minutes', 0):.0f}分钟")
    
    # 5. NFI Exit模拟
    print(f"\n{'='*70}")
    print("5️⃣ NFI + Freqtrade Exit 策略模拟")
    print(f"{'='*70}")
    nfi_sim = report["nfi_exit_simulation"]
    print(f"   分析交易: {nfi_sim['analyzed']}笔")
    print(f"   平均改善: {nfi_sim['avg_improvement']*100:.2f}%")
    print(f"   触发示例:")
    for imp in nfi_sim["improvements"][:5]:
        print(f"      - {imp['token']}: 实际{imp['actual_exit']*100:.1f}% → 模拟{imp['simulated_exit']*100:.1f}%, 触发{imp['trigger']}")
    
    # 6. 问题诊断
    print(f"\n{'='*70}")
    print("6️⃣ 问题诊断")
    print(f"{'='*70}")
    for i, prob in enumerate(report["problems"], 1):
        print(f"   ⚠️ {i}. {prob}")
    
    # 7. 优化建议
    print(f"\n{'='*70}")
    print("7️⃣ 优化建议")
    print(f"{'='*70}")
    for i, sug in enumerate(report["suggestions"], 1):
        print(f"   ✅ {i}. {sug}")
    
    # 保存报告
    output_file = f"reports/backtest_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*70}")
    print(f"📁 详细报告已保存: {output_file}")
    print(f"{'='*70}")
    
    return report

if __name__ == "__main__":
    main()
#!/usr/bin/env python3
"""
MMTracker — 庄家行为量化监测系统
使用官方 Python 库: Binance Futures + Coinglass + DexScreener

7信号框架:
1. 价格在整数关口下方横盘 3~7 天
2. 资金费率从负/零开始转正且持续上升
3. OI 在价格横盘期间悄悄增加
4. 某一天出现 3x 以上放量但价格未大涨
5. DexScreener 买卖比 >1.2 且持续多日
6. BTC.D 处于下降通道
7. Binance 新增了该币的永续合约

入场条件: 满足5个以上信号
离场铁律: Funding Rate > 0.5% 减仓, > 1% 清仓
"""

import sys
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

from fetchers.price_api import (
    fetch_price_and_change, fetch_dex_data, fetch_funding_rate_history,
    fetch_oi_history, check_futures_contract, fetch_daily_ohlcv,
    fetch_btcd_history, fetch_all_data,
)
from signals.calculator import SignalCalculator, calculate_all_signals, check_exit_signal
from signals.scorer import MMScorer, calculate_final_score, check_exit_rule


def safe_fetch(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        return {"error": str(e)}


def calculate_signals(raw_data: Dict[str, Any]) -> Dict[str, dict]:
    """计算所有7个信号"""
    calc = SignalCalculator()
    
    price_data = raw_data.get("price_info", {})
    dex_data = raw_data.get("dex_info", {})
    futures_data = raw_data.get("futures_info", {})
    funding_data = raw_data.get("funding_info", {})
    oi_data = raw_data.get("oi_info", {})
    kline_df = raw_data.get("kline_df", pd.DataFrame())
    btcd_info = raw_data.get("btcd_info", {})
    btcd_df = btcd_info.get("history_df", pd.DataFrame())
    
    # Signal 1: 整数关口横盘
    sig1 = calc.calc_signal_1_integer_consolidation(price_data, kline_df)
    
    # Signal 2: 资金费率转正
    sig2 = calc.calc_signal_2_funding_turn_positive(funding_data)
    
    # Signal 3: OI吸筹
    sig3 = calc.calc_signal_3_oi_accumulation(oi_data, price_data)
    
    # Signal 4: 放量未大涨
    sig4 = calc.calc_signal_4_volume_spike(kline_df)
    
    # Signal 5: DEX买压
    sig5 = calc.calc_signal_5_dex_buy_pressure(dex_data)
    
    # Signal 6: BTC.D下降
    sig6 = calc.calc_signal_6_btcd_downtrend(btcd_df)
    
    # Signal 6b: BTC相对强度
    sig6b = calc.calc_signal_6b_btc_relative_strength(kline_df, btcd_df)
    
    # Signal 7: 新合约
    sig7 = calc.calc_signal_7_new_futures(futures_data)
    
    # Signal 8: 洗盘测试期
    sig8 = calc.calc_signal_8_wash_test(kline_df, funding_data)
    
    # Signal 10: 关键心理关口突破
    sig10 = calc.calc_signal_10_breakout(kline_df, price_data)
    
    # 构建信号字典（signal_11需要其他信号先计算）
    all_signals = {
        "signal_1_integer_consolidation": sig1,
        "signal_2_funding_turn_positive": sig2,
        "signal_3_oi_accumulation": sig3,
        "signal_4_volume_spike": sig4,
        "signal_5_dex_buy_pressure": sig5,
        "signal_6_btcd_downtrend": sig6,
        "signal_6b_btc_relative_strength": sig6b,
        "signal_7_new_futures": sig7,
        "signal_8_wash_test": sig8,
        "signal_10_breakout": sig10,
    }
    
    # Signal 11: 早期组合预警
    sig11 = calc.calc_signal_11_early_warning(all_signals)
    all_signals["signal_11_early_warning"] = sig11
    
    return all_signals


def print_terminal(symbol: str, signals: dict, score: dict, exit_info: dict = None):
    """终端输出"""
    print(f"\n{'='*60}")
    print(f"🔍 {symbol}/USDT 庄家行为监测报告")
    print(f"{'='*60}")
    
    # 综合评分
    print(f"\n📊 综合评分: {score['total_score']}/10 {score['grade_emoji']} {score['grade_label']}")
    print(f"🎯 触发信号: {score['triggered_count']}/11 (入场需要 4+ 信号, 2+ 信号密切关注)")
    print(f"💡 {score['recommendation']}")
    
    # 离场规则检查
    if exit_info:
        exit_color = exit_info.get("color", "⚪")
        print(f"\n🚪 离场检查: {exit_color} {exit_info.get('reason', '')}")
    
    # 信号详情
    print("\n📈 信号详情:")
    names = {
        "signal_1_integer_consolidation": "整数关口横盘(3~7天)",
        "signal_2_funding_turn_positive": "资金费率转正",
        "signal_3_oi_accumulation": "OI暗中增加",
        "signal_4_volume_spike": "3x放量未大涨",
        "signal_5_dex_buy_pressure": "DEX买压>1.2",
        "signal_6_btcd_downtrend": "BTC.D下降通道",
        "signal_7_new_futures": "Binance新合约",
    }
    
    for sig_key, sig_val in signals.items():
        status = "✅" if sig_val.get("triggered") else "❌"
        detail = sig_val.get("detail", "")[:50]
        print(f"  {status} {names.get(sig_key, sig_key)}: {detail}")


def analyze_one(symbol: str, verbose: bool = True) -> Dict[str, Any]:
    """分析单个代币"""
    if verbose:
        print(f"\n🔍 分析 {symbol}...")
    
    # 1. 获取数据
    raw_data = fetch_all_data(symbol.upper())
    
    # 2. 计算信号
    all_signals = calculate_signals(raw_data)
    
    # 3. 评分
    scorer = MMScorer()
    score_result = scorer.score(all_signals)
    
    # 4. 离场规则检查
    funding_data = raw_data.get("funding_info", {})
    exit_result = check_exit_signal(funding_data)
    exit_info = {
        "action": exit_result.get("action", "HOLD"),
        "reason": exit_result.get("detail", ""),
        "funding_rate": exit_result.get("latest_rate", 0),
    }
    if exit_info["action"] == "EXIT":
        exit_info["color"] = "🔴"
    elif exit_info["action"] == "REDUCE":
        exit_info["color"] = "🟡"
    else:
        exit_info["color"] = "🟢"
    
    if verbose:
        print_terminal(symbol.upper(), all_signals, score_result, exit_info)
    
    return {
        "symbol": symbol.upper(),
        "signals": all_signals,
        "score": score_result,
        "exit_info": exit_info,
    }


def batch_analyze(symbols: list, verbose: bool = True) -> list:
    """批量分析多个代币"""
    results = []
    
    for symbol in symbols:
        try:
            result = analyze_one(symbol.upper(), verbose=verbose)
            results.append(result)
        except Exception as e:
            print(f"❌ {symbol} 分析失败: {e}")
    
    # 汇总结果
    if results and verbose:
        print(f"\n{'='*60}")
        print("📋 批量分析汇总")
        print(f"{'='*60}")
        
        # 按触发信号数排序
        results_sorted = sorted(results, key=lambda x: x["score"]["triggered_count"], reverse=True)
        
        for r in results_sorted:
            s = r["score"]
            print(f"{s['grade_emoji']} {r['symbol']}: {s['triggered_count']}/11 信号, {s['grade_label']}")
    
    return results


def load_trading_config():
    config_path = "config/testnet_config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def is_valid_api_key(api_key: str) -> bool:
    return api_key and "YOUR_" not in api_key and len(api_key) > 10


def main():
    parser = argparse.ArgumentParser(description="MMTracker — 庄家行为量化监测")
    parser.add_argument("tickers", nargs="*", help="代币如 BTC ETH SOL LAB")
    parser.add_argument("-q", "--quiet", action="store_true", help="静默模式")
    parser.add_argument("-m", "--monitor", action="store_true", help="持续监控模式")
    parser.add_argument("-i", "--interval", type=int, default=300, help="监控间隔(秒)，默认300秒")
    parser.add_argument("--sim", action="store_true", help="模拟交易模式")
    parser.add_argument("--api-key", type=str, help="OKX API Key (优先)")
    parser.add_argument("--api-secret", type=str, help="OKX API Secret")
    parser.add_argument("--passphrase", type=str, help="OKX Passphrase")
    parser.add_argument("--position-size", type=float, default=10.0, help="仓位大小")
    
    args = parser.parse_args()
    
    trader = None
    logger = None
    
    if True:
        try:
            from trading.mock_trader import create_trader
            from trading.result_logger import ResultLogger
            
            api_key = args.api_key
            api_secret = args.api_secret
            passphrase = args.passphrase
            
            if not api_key:
                config = load_trading_config()
                if config:
                    okx_cfg = config.get("okx_testnet", {})
                    api_key = okx_cfg.get("api_key")
                    api_secret = okx_cfg.get("api_secret")
                    passphrase = okx_cfg.get("passphrase")
            
            if args.sim or not is_valid_api_key(api_key if api_key else ""):
                trader = create_trader(sim_mode=True)
            elif api_key and api_secret and passphrase:
                trader = create_trader(
                    api_key=api_key,
                    api_secret=api_secret,
                    passphrase=passphrase,
                    sim_mode=False,
                )
            else:
                trader = create_trader(sim_mode=True)
            
            logger = ResultLogger()
            
            is_sim = args.sim or not is_valid_api_key(api_key if api_key else "")
            print(f"✅ 交易模式: {'模拟' if is_sim else '真实'}")
        except ImportError as e:
            print(f"⚠️ 交易模块未安装: {e}")
            trader = None
            logger = None
    
    if not args.tickers:
        print("用法: python main.py BTC ETH SOL LAB")
        print("  -q/--quiet: 静默模式")
        print("  -m/--monitor: 持续监控模式")
        print("  -i/--interval: 监控间隔秒数")
        print("  --sim: 模拟交易模式")
        sys.exit(1)
    
    from signals.state_machine import StateMachine
    state_machine = StateMachine(threshold_low=1, threshold_high=3)
    
    def analyze_and_trade(symbol: str) -> dict:
        result = analyze_one(symbol.upper(), verbose=not args.quiet)
        
        if trader and logger:
            score = result.get("score", {})
            entry_signals = score.get("entry_signals", [])
            exit_signals = score.get("exit_signals", [])
            
            state = state_machine.evaluate(entry_signals, exit_signals)
            
            if state["state"] in ["ENTRY_LOW_CONFIDENCE", "ENTRY_HIGH_CONFIDENCE"]:
                price = result.get("price", 0)
                if price > 0:
                    symbol_swap = f"SWAP-{symbol.upper()}-USDT"
                    order = trader.place_order(symbol_swap, "buy", args.position_size, price)
                    
                    if order.get("code") == "0":
                        logger.log_entry(
                            token=symbol.upper(),
                            signals=entry_signals,
                            score=score.get("total_score", 0),
                            entry_price=price,
                            entry_signals_count=len(entry_signals),
                            position_size=args.position_size,
                        )
                        print(f"📈 入场: {symbol} @ {price}")
            
            unfinished = logger.get_unfinished_trades()
            for trade in unfinished:
                token = trade["token"]
                symbol_swap = f"SWAP-{token}-USDT"
                pos = trader.get_position(symbol_swap)
                
                if pos and float(pos.get("pos", 0)) != 0:
                    last_px = float(pos.get("lastPx", 0))
                    avg_px = float(pos.get("avgPx", 0))
                    
                    if avg_px > 0:
                        pnl_pct = (last_px - avg_px) / avg_px * 100
                        
                        if pnl_pct > 10 or pnl_pct < -5:
                            exit_order = trader.close_position(symbol_swap)
                            
                            if exit_order.get("code") == "0":
                                pnl = (last_px - avg_px) * float(pos.get("pos", 0))
                                logger.log_exit(
                                    trade_index=trade["index"],
                                    exit_price=last_px,
                                    pnl=pnl,
                                    exit_reason="止盈/止损",
                                )
                                print(f"📉 出场: {token} @ {last_px}, PnL: {pnl:.2f}")
        
        return result
    
    if args.monitor:
        import time
        print(f"\n🔄 启动持续监控模式，间隔 {args.interval} 秒...")
        while True:
            for symbol in args.tickers:
                analyze_and_trade(symbol)
            
            if trader:
                status = trader.get_status()
                print(f"\n💰 账户状态: 余额=${status['balance']:.2f}, 总价值=${status['total_value']:.2f}, PnL=${status['pnl']:.2f}")
            
            print(f"\n⏰ 等待 {args.interval} 秒后再次检测...")
            time.sleep(args.interval)
    else:
        for symbol in args.tickers:
            analyze_and_trade(symbol)


if __name__ == "__main__":
    main()
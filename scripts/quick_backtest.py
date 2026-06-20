#!/usr/bin/env python3
"""
快速参数搜索 - 批量回测日内策略
用法: python3 scripts/quick_backtest.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np
from scripts.intraday_leverage import IntradayLeverageStrategy
from concurrent.futures import ThreadPoolExecutor
import time

def quick_backtest(symbol, leverage, target, stop, max_trades=50):
    """快速回测 - 简化版，跳过21信号工厂"""
    
    # 加载数据
    base = "/mnt/d/NostalgiaForInfinityData-main/binance/"
    path = f"{base}{symbol}_USDT-1h.feather"
    
    try:
        df = pd.read_feather(path)
        df = df.sort_values('date').reset_index(drop=True)
    except:
        return None
    
    if len(df) < 200:
        return None
    
    close = df['close']
    volume = df['volume']
    high = df['high']
    low = df['low']
    
    # 简化信号
    vol_ma = volume.rolling(20).mean()
    df['vol_ratio'] = volume / (vol_ma + 1)
    df['hourly_high'] = high.rolling(20).max().shift(1)
    df['breakout'] = (close > df['hourly_high']) & (df['vol_ratio'] > 1.5)
    
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    df['rsi'] = 100 - (100 / (1 + gain/(loss+1e-10)))
    df['rsi_prev'] = df['rsi'].shift(1)
    df['rsi_oversold_rebound'] = (df['rsi_prev'] < 35) & (df['rsi'] > df['rsi_prev']) & (df['rsi'] < 50)
    
    df['candle_body'] = (close - df['open']) / df['open']
    df['large_candle'] = df['candle_body'] > 0.03
    
    df['close_ma5'] = close.rolling(5).mean()
    df['momentum'] = (close > df['close_ma5']) & (close.pct_change(3) > 0.05)
    
    df['signal_count'] = df['breakout'].astype(int) + df['rsi_oversold_rebound'].astype(int) + df['large_candle'].astype(int) + df['momentum'].astype(int)
    df['entry_signal'] = df['signal_count'] >= 2
    
    # 回测 (只用简化版信号，跳过21信号工厂)
    fee = 0.0004 * 2
    trades = []
    position = None
    
    for i in range(50, len(df) - 24):
        if position is None and df.iloc[i]['entry_signal']:
            entry_price = df.iloc[i]['close'] * 1.001  # 滑点
            position = {'entry_price': entry_price, 'entry_idx': i}
            continue
        
        if position is not None:
            current_price = df.iloc[i]['close']
            raw_return = (current_price - position['entry_price']) / position['entry_price']
            leveraged_return = raw_return * leverage
            net_return = leveraged_return - fee
            
            # 止盈/止损
            if net_return >= target/100:
                trades.append({'return': net_return, 'type': 'TP'})
                position = None
            elif net_return <= -stop/100:
                trades.append({'return': net_return, 'type': 'SL'})
                position = None
            elif i - position['entry_idx'] >= 24:
                trades.append({'return': net_return, 'type': 'TO'})
                position = None
    
    if len(trades) < 5:
        return None
    
    wins = sum(1 for t in trades if t['return'] > 0)
    total_return = sum(t['return'] for t in trades)
    profit_factor = abs(sum(t['return'] for t in trades if t['return'] > 0) / sum(t['return'] for t in trades if t['return'] < 0)) if sum(t['return'] for t in trades if t['return'] < 0) != 0 else 0
    
    return {
        'symbol': symbol,
        'leverage': leverage,
        'target': target,
        'stop': stop,
        'trades': len(trades),
        'win_rate': wins / len(trades),
        'total_return': total_return,
        'profit_factor': profit_factor if not np.isnan(profit_factor) else 0,
    }

def run_grid_search():
    """网格搜索最优参数"""
    symbols = ["BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "AVAX", "LINK"]
    
    # 参数组合
    leverage_vals = [2, 3, 5]
    target_vals = [10, 15, 20]
    stop_vals = [3, 5, 8]
    
    results = []
    
    print("="*70)
    print("⚡ 快速参数网格搜索")
    print("="*70)
    print(f"币种: {', '.join(symbols)}")
    print(f"参数: leverage={leverage_vals}, target={target_vals}, stop={stop_vals}")
    print("="*70)
    
    total = len(symbols) * len(leverage_vals) * len(target_vals) * len(stop_vals)
    done = 0
    
    for symbol in symbols:
        for lev in leverage_vals:
            for tgt in target_vals:
                for sl in stop_vals:
                    done += 1
                    result = quick_backtest(symbol, lev, tgt, sl)
                    
                    if result:
                        results.append(result)
                        print(f"[{done}/{total}] {symbol} {lev}x TP{tgt}% SL{sl}%: {result['trades']}笔, 胜率{result['win_rate']*100:.1f}%, PF={result['profit_factor']:.2f}")
                    else:
                        print(f"[{done}/{total}] {symbol} {lev}x TP{tgt}% SL{sl}%: 数据不足")
    
    # 排序找最优
    if results:
        results = sorted(results, key=lambda x: x['profit_factor'] if x['profit_factor'] > 0 else 0, reverse=True)
        
        print("\n" + "="*70)
        print("🏆 TOP 10 参数组合")
        print("="*70)
        print(f"{'Symbol':^8} | {'Lev':^4} | {'TP%':^5} | {'SL%':^4} | {'Trades':^7} | {'Win%':^6} | {'PF':^6}")
        print("-"*70)
        
        for r in results[:10]:
            print(f"{r['symbol']:^8} | {r['leverage']:^4} | {r['target']:^5} | {r['stop']:^4} | {r['trades']:^7} | {r['win_rate']*100:^6.1f} | {r['profit_factor']:^6.2f}")
        
        # 多币种汇总
        print("\n" + "="*70)
        print("📊 多币种汇总")
        print("="*70)
        
        # 按参数组合汇总
        param_groups = {}
        for r in results:
            key = (r['leverage'], r['target'], r['stop'])
            if key not in param_groups:
                param_groups[key] = {'trades': 0, 'wins': 0, 'returns': 0, 'pf_sum': 0, 'count': 0}
            param_groups[key]['trades'] += r['trades']
            param_groups[key]['returns'] += r['total_return']
            param_groups[key]['pf_sum'] += r['profit_factor']
            param_groups[key]['count'] += 1
            if r['win_rate'] > 0:
                param_groups[key]['wins'] += r['trades'] * r['win_rate']
        
        best_params = sorted(param_groups.items(), key=lambda x: x[1]['pf_sum']/x[1]['count'] if x[1]['count'] > 0 else 0, reverse=True)
        
        print(f"{'Lev':^4} | {'TP%':^5} | {'SL%':^4} | {'Total Trades':^12} | {'Avg PF':^8} | {'Return':^10}")
        print("-"*70)
        
        for (lev, tgt, sl), stats in best_params[:10]:
            avg_pf = stats['pf_sum'] / stats['count'] if stats['count'] > 0 else 0
            print(f"{lev:^4} | {tgt:^5} | {sl:^4} | {stats['trades']:^12} | {avg_pf:^8.2f} | {stats['returns']*100:^+9.1f}%")

if __name__ == "__main__":
    run_grid_search()
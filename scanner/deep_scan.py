"""
MMTracker Scanner - 深度扫描模块
对 fast_filter 输出的 30 个候选，复用现有的信号计算器，跑完整 7 信号分析。
"""

import time
import logging
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


def fetch_full_data(symbol: str) -> Dict[str, Any]:
    """
    并发获取单个代币的全部数据
    """
    from fetchers.price_api import (
        fetch_daily_ohlcv, fetch_oi_history, fetch_price_and_change,
        fetch_funding_rate_history, fetch_dex_data, check_futures_contract,
        fetch_btcd_simple, fetch_long_short_ratio, fetch_taker_volume
    )
    
    results = {
        "symbol": symbol,
        "price_info": {},
        "kline_df": None,
        "oi_info": {},
        "funding_info": {},
        "dex_info": {},
        "futures_info": {},
        "btcd_info": {},
        "ls_info": {},
        "tv_info": {},
    }
    
    def safe_fetch(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.error(f"{symbol} {fn.__name__} error: {e}")
            return None
    
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            "price_info": ex.submit(safe_fetch, fetch_price_and_change, symbol),
            "kline_df": ex.submit(safe_fetch, fetch_daily_ohlcv, symbol, 30),
            "oi_info": ex.submit(safe_fetch, fetch_oi_history, symbol),
            "funding_info": ex.submit(safe_fetch, fetch_funding_rate_history, symbol),
            "dex_info": ex.submit(safe_fetch, fetch_dex_data, symbol),
            "futures_info": ex.submit(safe_fetch, check_futures_contract, symbol),
            "ls_info": ex.submit(safe_fetch, fetch_long_short_ratio, symbol),
            "tv_info": ex.submit(safe_fetch, fetch_taker_volume, symbol),
        }
        
        # BTC.D 只获取一次
        btcd_data = None
        try:
            btcd_data = fetch_btcd_simple()
        except:
            pass
        
        for key, future in futures.items():
            try:
                results[key] = future.result(timeout=15) or {}
            except Exception as e:
                results[key] = {}
        
        if btcd_data:
            results["btcd_info"] = btcd_data
    
    return results


def deep_scan_one(symbol: str, quick_data: dict = None, btcd_data: dict = None) -> dict:
    """
    对单个代币运行完整 7 信号分析
    
    Bug 1 修复：btcd_data 从外部传入，避免每个代币重复请求
    
    quick_data: fast_filter 阶段已经获取的数据（price, volume, funding_rate）
                用于避免重复请求
    btcd_data: 预先获取的 BTC.D 数据（从 deep_scan_batch 传入）
    
    返回：{
        "symbol": symbol,
        "signals": signals,
        "score": score,
        "price": price,
        "quick_score": quick_data.get("quick_score", 0),
        "funding_rate": quick_data.get("funding_rate", 0),
    }
    """
    from signals.calculator import SignalCalculator
    from signals.scorer import MMScorer
    
    # 1. 获取数据
    raw_data = fetch_full_data(symbol)
    
    import pandas as pd
    
    price_info = raw_data.get("price_info", {})
    kline_df = raw_data.get("kline_df")
    oi_info = raw_data.get("oi_info", {})
    funding_info = raw_data.get("funding_info", {})
    dex_info = raw_data.get("dex_info", {})
    futures_info = raw_data.get("futures_info", {})
    ls_info = raw_data.get("ls_info", {})
    tv_info = raw_data.get("tv_info", {})
    
    # Bug 1: 使用传入的 btcd_data，不再重复获取
    btcd_info = btcd_data if btcd_data else raw_data.get("btcd_info", {})
    
    # 确保 kline_df 是 DataFrame
    if kline_df is None:
        kline_df = pd.DataFrame()
    elif not isinstance(kline_df, pd.DataFrame):
        kline_df = pd.DataFrame()
    
    btcd_df = btcd_info.get("history_df") if btcd_info else None
    if btcd_df is not None and not isinstance(btcd_df, pd.DataFrame):
        btcd_df = None
    
    # 2. 使用 quick_data 的价格（如果有）
    if quick_data and quick_data.get("price", 0) > 0:
        price_info["price"] = quick_data["price"]
    
    # 3. 计算11个信号
    calc = SignalCalculator()
    
    signals = {
        "signal_1_integer_consolidation": calc.calc_signal_1_integer_consolidation(price_info, kline_df),
        "signal_2_funding_turn_positive": calc.calc_signal_2_funding_turn_positive(funding_info),
        "signal_3_oi_accumulation": calc.calc_signal_3_oi_accumulation(oi_info, price_info),
        "signal_4_volume_spike": calc.calc_signal_4_volume_spike(kline_df),
        "signal_5_dex_buy_pressure": calc.calc_signal_5_dex_buy_pressure(dex_info),
        "signal_6_btcd_downtrend": calc.calc_signal_6_btcd_downtrend(btcd_df),
        "signal_6b_btc_relative_strength": calc.calc_signal_6b_btc_relative_strength(kline_df, btcd_df),
        "signal_7_new_futures": calc.calc_signal_7_new_futures(futures_info),
        "signal_8_wash_test": calc.calc_signal_8_wash_test(kline_df, funding_info),
        "signal_10_breakout": calc.calc_signal_10_breakout(kline_df, price_info),
        "signal_12_long_short_ratio": calc.calc_signal_12_long_short_ratio(ls_info),
        "signal_13_taker_volume": calc.calc_signal_13_taker_volume(tv_info),
    }
    
    # Signal 11 需要在其他信号之后计算
    signals["signal_11_early_warning"] = calc.calc_signal_11_early_warning(signals)
    
    # 4. 评分
    scorer = MMScorer()
    score = scorer.score(signals)
    
    # 5. 获取价格用于返回
    price = price_info.get("price", 0)
    if quick_data and price == 0:
        price = quick_data.get("price", 0)
    
    # 6. 构建返回
    return {
        "symbol": symbol,
        "signals": signals,
        "score": score,
        "price": price,
        "quick_score": quick_data.get("quick_score", 0) if quick_data else 0,
        "funding_rate": quick_data.get("funding_rate", 0) if quick_data else 0,
    }


def deep_scan_batch(candidates: List[Dict], max_workers: int = 5) -> List[Dict]:
    """
    对候选列表并发运行深度扫描
    
    Bug 1 修复：预先获取一次 BTC.D 数据，传递给所有代币
    """
    results = []
    total = len(candidates)
    
    print(f"[DeepScan] 开始深度扫描 {total} 个候选代币...")
    
    # Bug 1: 预先获取一次 BTC.D（复用缓存）
    from fetchers.price_api import fetch_btcd_simple
    btcd_data = fetch_btcd_simple()
    print(f"[BTC.D] ✓ 预获取完成，当前 {btcd_data.get('current', 0):.1f}%")
    
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {}
        
        for i, cand in enumerate(candidates):
            symbol = cand.get("symbol", "")
            quick_data = cand  # 包含 quick_score, funding_rate 等
            
            # Bug 1: 传入 btcd_data
            future = ex.submit(deep_scan_one, symbol, quick_data, btcd_data)
            futures[future] = (i, symbol)
            
            # 避免 API 限速
            time.sleep(0.15)
        
        completed = 0
        for future in as_completed(futures):
            i, symbol = futures[future]
            try:
                result = future.result(timeout=60)
                results.append(result)
                completed += 1
                
                # 打印进度
                triggered = result.get("score", {}).get("triggered_count", 0)
                total_score = result.get("score", {}).get("total_score", 0)
                
                # 标记重点
                tag = ""
                if triggered >= 5:
                    tag = " ← 重点关注"
                elif triggered >= 3:
                    tag = " ← 值得注意"
                
                print(f"[DeepScan] ({completed}/{total}) {symbol:8s} ✓ 得分: {total_score:.1f}/10 触发{triggered}个信号{tag}")
                
            except Exception as e:
                completed += 1
                print(f"[DeepScan] ({completed}/{total}) {symbol:8s} ✗ 扫描失败: {e}")
        
        # 按 score 降序排列
        results.sort(key=lambda x: x.get("score", {}).get("total_score", 0), reverse=True)
    
    print(f"[DeepScan] 深度扫描完成，共 {len(results)} 个代币")
    
    return results


if __name__ == "__main__":
    # 测试
    from scanner.fast_filter import run_fast_filter
    from scanner.universe import get_full_universe
    
    print("获取全市场代币...")
    universe = get_full_universe()
    
    print("画像过滤...")
    candidates = run_fast_filter(universe)[:5]
    
    print("深度扫描...")
    results = deep_scan_batch(candidates)
    
    print(f"\n结果: {len(results)} 个代币")
    for r in results[:3]:
        s = r["score"]
        print(f"  {r['symbol']}: {s['triggered_count']}/11 信号, 评分 {s['total_score']:.1f}")
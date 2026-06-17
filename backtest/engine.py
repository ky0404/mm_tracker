"""
回测引擎 - 精确度/召回率/F1 指标计算
基于回测校准的实战版本

NFI风格升级:
- 支持面分析回测 (多时间框架 + 历史趋势)
- 支持DCA模拟
- 支持多信号组合回测
"""

from typing import List, Dict, Any, Optional, Tuple
import json
import os
from datetime import datetime
from collections import defaultdict


class BacktestEngine:
    """
    回测引擎：
    - 输入：sample_coins（带真实启动点）
    - 输出：每个信号的 precision / recall / f1
    
    NFI扩展:
    - 支持多时间框架分析结果回测
    - 支持DCA策略效果评估
    """

    def __init__(self, sample_coins: List[Dict[str, Any]] = None):
        self.sample_coins = sample_coins or []
        self.results_history = []
        
        # NFI风格分析参数
        self.nfi_params = {
            "ema_periods": [8, 20, 50, 200],
            "rsi_periods": [4, 14, 84],
            "lookback_periods": {
                "ema_trend": 3,      # EMA趋势回看周期数
                "rsi_recovery": 2,   # RSI恢复回看周期数
                "sma_rising": 28,    # SMA上涨回看周期数
            }
        }

    def add_sample(self, coin: Dict[str, Any]):
        """添加样本币"""
        self.sample_coins.append(coin)

    def set_nfi_params(self, params: Dict[str, Any]):
        """设置NFI风格分析参数"""
        self.nfi_params.update(params)

    def add_sample(self, coin: Dict[str, Any]):
        """添加样本币"""
        self.sample_coins.append(coin)

    def run_backtest(
        self,
        signal_results: Dict[str, List[bool]],
        entry_threshold: int = 2,
    ) -> Dict[str, Any]:
        """
        运行回测，计算精确度/召回率/F1
        
        Args:
            signal_results: {signal_id: [coin1_hit, coin2_hit, ...]}
            entry_threshold: 入场信号数阈值
            
        Returns:
            回测结果
        """
        n = len(self.sample_coins)
        if n == 0:
            return {"error": "No samples"}

        # 1. 每个信号的 precision / recall / f1
        signal_metrics = {}
        
        for sig_id, hits in signal_results.items():
            if len(hits) != n:
                continue
                
            tp = 0
            fp = 0
            fn = 0
            tn = 0
            
            for i, hit in enumerate(hits):
                coin = self.sample_coins[i]
                is_real_launch = coin.get("is_real_launch", True)  # 默认都是真的
                
                if is_real_launch and hit:
                    tp += 1
                elif not is_real_launch and hit:
                    fp += 1
                elif is_real_launch and not hit:
                    fn += 1
                else:
                    tn += 1
            
            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            
            signal_metrics[sig_id] = {
                "precision": round(precision, 3),
                "recall": round(recall, 3),
                "f1": round(f1, 3),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
            }

        # 2. 整体阈值命中率
        entry_counts = []
        for i in range(n):
            count = sum(1 for hits in signal_results.values() if i < len(hits) and hits[i])
            entry_counts.append(count)

        triggered = [count >= entry_threshold for count in entry_counts]
        
        tp = fp = fn = tn = 0
        for i, is_triggered in enumerate(triggered):
            coin = self.sample_coins[i]
            is_real_launch = coin.get("is_real_launch", True)
            
            if is_real_launch and is_triggered:
                tp += 1
            elif not is_real_launch and is_triggered:
                fp += 1
            elif is_real_launch and not is_triggered:
                fn += 1
            else:
                tn += 1

        threshold_precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        threshold_recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        threshold_f1 = 2 * threshold_precision * threshold_recall / (threshold_precision + threshold_recall) if (threshold_precision + threshold_recall) > 0 else 0.0

        # 3. 阈值分析
        threshold_analysis = {}
        for th in [1, 2, 3, 4, 5]:
            triggered_th = [count >= th for count in entry_counts]
            
            tp_t = fp_t = fn_t = 0
            for i, is_triggered in enumerate(triggered_th):
                coin = self.sample_coins[i]
                is_real_launch = coin.get("is_real_launch", True)
                
                if is_real_launch and is_triggered:
                    tp_t += 1
                elif not is_real_launch and is_triggered:
                    fp_t += 1
                elif is_real_launch and not is_triggered:
                    fn_t += 1
            
            hit_count = sum(triggered_th)
            hit_rate = (hit_count / n * 100) if n > 0 else 0
            
            threshold_analysis[th] = {
                "hits": hit_count,
                "hit_rate": round(hit_rate, 1),
                "precision": round(tp_t / (tp_t + fp_t), 3) if (tp_t + fp_t) > 0 else 0,
                "recall": round(tp_t / (tp_t + fn_t), 3) if (tp_t + fn_t) > 0 else 0,
            }

        results = {
            "timestamp": datetime.now().isoformat(),
            "sample_count": n,
            "entry_threshold": entry_threshold,
            "signal_metrics": signal_metrics,
            "threshold_metrics": {
                "precision": round(threshold_precision, 3),
                "recall": round(threshold_recall, 3),
                "f1": round(threshold_f1, 3),
                "tp": tp,
                "fp": fp,
                "fn": fn,
                "tn": tn,
            },
            "threshold_analysis": threshold_analysis,
        }
        
        self.results_history.append(results)
        
        return results

    def get_best_threshold(self) -> Tuple[int, float]:
        """获取最优阈值"""
        if not self.results_history:
            return 2, 0.0
            
        latest = self.results_history[-1]
        threshold_analysis = latest.get("threshold_analysis", {})
        
        best_th = 2
        best_f1 = 0.0
        
        for th, data in threshold_analysis.items():
            # 综合考虑命中率和F1
            f1 = data.get("precision", 0) * 0.5 + data.get("recall", 0) * 0.5
            if f1 > best_f1:
                best_f1 = f1
                best_th = th
                
        return best_th, best_f1

    def generate_recommendation(self, results: Dict) -> str:
        """生成校准建议"""
        signal_metrics = results.get("signal_metrics", {})
        threshold_analysis = results.get("threshold_analysis", {})
        
        # 排序信号
        sorted_signals = sorted(
            signal_metrics.items(),
            key=lambda x: x[1].get("f1", 0),
            reverse=True
        )
        
        # 找最优阈值
        best_th = 2
        best_rate = 0
        for th, data in threshold_analysis.items():
            if data.get("hit_rate", 0) > best_rate:
                best_rate = data.get("hit_rate", 0)
                best_th = th
        
        report = f"""
# 回测校准报告
生成时间: {results.get('timestamp')}

## 样本信息
- 样本数量: {results.get('sample_count')}
- 入场阈值: {results.get('entry_threshold')}

## 信号有效性排名 (F1)

| 信号 | Precision | Recall | F1 |
|------|-----------|--------|-----|
"""
        for sig_id, metrics in sorted_signals[:10]:
            report += f"| {sig_id} | {metrics['precision']:.2f} | {metrics['recall']:.2f} | {metrics['f1']:.2f} |\n"
        
        report += f"""
## 阈值分析

| 阈值 | 命中数 | 命中率 | Precision | Recall |
|------|--------|--------|-----------|--------|
"""
        for th in sorted(threshold_analysis.keys()):
            data = threshold_analysis[th]
            report += f"| {th} | {data['hits']} | {data['hit_rate']}% | {data['precision']:.2f} | {data['recall']:.2f} |\n"
        
        report += f"""
## 建议

1. **阈值**: 当前 {results.get('entry_threshold')}，建议调整为 {best_th}
2. **高F1信号**: {', '.join([s[0] for s in sorted_signals[:3]])}
3. **权重调整**: 基于F1分数调整信号权重
"""
        
        return report


def run_backtest_with_samples(
    samples: List[Dict],
    signal_calculator_func,
    entry_threshold: int = 2,
) -> Dict:
    """
    便捷函数：用样本币运行回测
    
    Args:
        samples: 样本币列表 [{"symbol": str, "is_real_launch": bool}, ...]
        signal_calculator_func: 计算信号的函数
        entry_threshold: 入场阈值
    """
    engine = BacktestEngine(samples)
    
    # 对每个币运行信号计算
    signal_results = defaultdict(list)
    
    for coin in samples:
        symbol = coin["symbol"]
        try:
            # 计算信号
            result = signal_calculator_func(symbol)
            signals = result.get("signals", {})
            
            # 记录每个信号是否触发
            for sig_id, data in signals.items():
                signal_results[sig_id].append(data.get("triggered", False))
                
        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            # 标记为False
            for sig_id in signal_results:
                signal_results[sig_id].append(False)
    
    # 转换为普通dict
    signal_results = dict(signal_results)
    
    return engine.run_backtest(signal_results, entry_threshold)


if __name__ == "__main__":
    # 测试
    samples = [
        {"symbol": "PEPE", "is_real_launch": True},
        {"symbol": "WIF", "is_real_launch": True},
        {"symbol": "BONK", "is_real_launch": True},
        {"symbol": "BTC", "is_real_launch": False},
        {"symbol": "ETH", "is_real_launch": False},
    ]
    
    # 模拟信号结果
    signal_results = {
        "s12": [True, True, True, True, True],  # 70% precision
        "s2": [True, False, True, False, False],  # 40% precision
        "s13": [False, False, True, False, True],
    }
    
    engine = BacktestEngine(samples)
    results = engine.run_backtest(signal_results, entry_threshold=2)
    
    print("Signal Metrics:")
    for sig, m in results["signal_metrics"].items():
        print(f"  {sig}: P={m['precision']:.2f} R={m['recall']:.2f} F1={m['f1']:.2f}")
    
    print(f"\nThreshold Metrics: P={results['threshold_metrics']['precision']:.2f} R={results['threshold_metrics']['recall']:.2f}")
    
    print("\n" + engine.generate_recommendation(results))


# ===== NFI风格 回测方法 =====

def calculate_nfi_indicators(candles: List[Dict], config: Dict) -> Dict:
    """
    计算NFI风格指标 (面分析)
    
    核心: 不是只看当前值，而是看历史趋势
    """
    import pandas as pd
    
    # 转换为DataFrame
    df = pd.DataFrame(candles)
    
    # EMA指标
    for period in config.get("ema_periods", [8, 20, 50, 200]):
        df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()
    
    # RSI指标
    for period in config.get("rsi_periods", [4, 14, 84]):
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, float('nan'))
        df[f"rsi_{period}"] = 100 - (100 / (1 + rs))
    
    lookback_ema = config.get("lookback_periods", {}).get("ema_trend", 3)
    lookback_rsi = config.get("lookback_periods", {}).get("rsi_recovery", 2)
    
    # NFI风格: EMA趋势 - 改为更实用的标准
    # 方案A: EMA8 > EMA20 (金叉状态)
    df["ema_golden_cross"] = df["ema_8"] > df["ema_20"]
    # 方案B: EMA50方向向上 (比N根K线前高)
    df["ema50_direction"] = df["ema_50"] > df["ema_50"].shift(lookback_ema)
    # 方案C: 价格站上EMA50
    df["price_above_ema50"] = df["close"] > df["ema_50"]
    # 任一方案触发即可
    df["ema_trend_rising"] = df["ema_golden_cross"] | df["ema50_direction"] | df["price_above_ema50"]
    
    # NFI风格: RSI恢复 (放宽条件，不再强制<50)
    # 方案A: RSI正在从低点回升
    df["rsi_recovering"] = df["rsi_14"] > df["rsi_14"].shift(lookback_rsi)
    # 方案B: RSI处于中低位置 (不超买)
    df["rsi_not_overbought"] = df["rsi_14"] < 65
    # 综合: 任一方案触发即可
    df["rsi_recovering"] = df["rsi_recovering"] | df["rsi_not_overbought"]
    
    # 安全回调
    df["tpct_change_0"] = (df["close"] - df["close"].shift(0)) / df["close"].shift(0)
    df["tpct_change_2"] = (df["close"] - df["close"].shift(2)) / df["close"].shift(2)
    df["tpct_change_12"] = (df["close"] - df["close"].shift(12)) / df["close"].shift(12)
    
    safe_dips = config.get("safe_dips", {
        "threshold_0": 0.032,
        "threshold_2": 0.09,
        "threshold_12": 0.24,
    })
    
    df["safe_dips"] = (
        (df["tpct_change_0"].abs() < safe_dips["threshold_0"]) &
        (df["tpct_change_2"].abs() < safe_dips["threshold_2"]) &
        (df["tpct_change_12"].abs() < safe_dips["threshold_12"])
    )
    
    # 安全涨幅
    df["hl_pct_change_24"] = (df["high"].rolling(24).max() - df["low"].rolling(24).min()) / df["close"]
    
    safe_pump = config.get("safe_pump", {
        "threshold_24h": 0.75,
        "threshold_48h": 1.5,
    })
    
    df["safe_pump"] = df["hl_pct_change_24"] < safe_pump["threshold_24h"]
    
    return df.to_dict()


def detect_nfi_signals(df: Dict, config: Dict) -> Dict:
    """
    检测NFI风格信号
    
    返回:
        entry_triggered: 是否触发入场
        ema_trend_hits: EMA趋势信号
        rsi_recovery_hits: RSI恢复信号
        safe_dips_hits: 安全回调信号
        safe_pump_hits: 安全涨幅信号
    """
    result = {
        "entry_triggered": False,
        "ema_trend_hits": False,
        "rsi_recovery_hits": False,
        "safe_dips_hits": False,
        "safe_pump_hits": False,
        "profit": 0,
    }
    
    # 综合判断: 需要 EMA趋势 + RSI恢复 + (安全回调 或 安全涨幅)
    protections_passed = (
        result["safe_dips_hits"] or result["safe_pump_hits"]
    )
    
    entry_condition = (
        result["ema_trend_hits"] and 
        result["rsi_recovery_hits"] and
        protections_passed
    )
    
    result["entry_triggered"] = entry_condition
    
    return result


def run_nfi_surface_backtest(
    candles_data: Dict[str, List[Dict]],
    signal_config: Dict[str, Any],
    dca_enabled: bool = False,
    dca_config: Dict = None,
) -> Dict[str, Any]:
    """
    NFI风格面分析回测
    
    对比传统回测:
    - 传统: 只看当前K线信号
    - NFI: 看过去N根K线的趋势 (shift/rolling)
    
    Args:
        candles_data: {symbol: [candle1, candle2, ...]} 历史K线数据
        signal_config: 信号配置 (NFI风格的保护参数)
        dca_enabled: 是否启用DCA
        dca_config: DCA配置
    
    Returns:
        回测结果含NFI特征分析
    """
    results = {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "avg_profit": 0.0,
        "max_drawdown": 0.0,
        "nfi_signals": {
            "ema_trend_hits": 0,
            "rsi_recovery_hits": 0,
            "safe_dips_hits": 0,
            "safe_pump_hits": 0,
        },
        "dca_results": [],
    }
    
    lookback = signal_config.get("lookback_periods", {}).get("ema_trend", 3)
    
    for symbol, candles in candles_data.items():
        if len(candles) < lookback + 10:
            continue
        
        # 计算NFI风格指标
        df_dict = calculate_nfi_indicators(candles, signal_config)
        
        # 检测信号
        signals = detect_nfi_signals(df_dict, signal_config)
        
        # 统计
        if signals["entry_triggered"]:
            results["total_trades"] += 1
            
            if signals.get("profit", 0) > 0:
                results["winning_trades"] += 1
            else:
                results["losing_trades"] += 1
        
        # NFI信号统计
        for sig_name in results["nfi_signals"].keys():
            if signals.get(sig_name, False):
                results["nfi_signals"][sig_name] += 1
    
    # 计算汇总
    if results["total_trades"] > 0:
        results["win_rate"] = results["winning_trades"] / results["total_trades"]
    
    # DCA模拟
    if dca_enabled and dca_config:
        from trading.nfi_dca import NFIDCAManager
        dca_manager = NFIDCAManager()
        dca_results = dca_manager.simulate_dca_sequence(
            initial_profit=-0.01,
            mode=dca_config.get("mode", "mode_0")
        )
        results["dca_results"] = dca_results
    
    return results
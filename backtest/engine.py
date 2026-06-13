"""
回测引擎 - 精确度/召回率/F1 指标计算
基于回测校准的实战版本
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
    """

    def __init__(self, sample_coins: List[Dict[str, Any]] = None):
        self.sample_coins = sample_coins or []
        self.results_history = []

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
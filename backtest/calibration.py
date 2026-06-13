"""
校准报告生成器
基于回测结果生成可读报告
"""

import os
import json
from datetime import datetime
from typing import Dict, List, Any
import pandas as pd


def generate_calibration_report(
    engine_results: Dict[str, Any],
    output_dir: str = "backtest/reports",
    format: str = "both",
) -> str:
    """
    生成校准报告
    
    Args:
        engine_results: BacktestEngine.run_backtest() 的结果
        output_dir: 输出目录
        format: "json", "md", "csv", "both"
        
    Returns:
        报告文件路径
    """
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # 1. JSON 报告
    json_path = None
    if format in ["json", "both"]:
        json_file = f"{output_dir}/calibration_{timestamp}.json"
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(engine_results, f, indent=2, ensure_ascii=False)
        json_path = json_file
    
    # 2. MD 报告
    md_path = None
    if format in ["md", "both"]:
        md_file = f"{output_dir}/calibration_{timestamp}.md"
        
        signal_metrics = engine_results.get("signal_metrics", {})
        threshold_analysis = engine_results.get("threshold_analysis", {})
        threshold_metrics = engine_results.get("threshold_metrics", {})
        
        md = f"""# MMTracker 校准报告

**生成时间**: {engine_results.get('timestamp', 'N/A')}
**样本数量**: {engine_results.get('sample_count', 0)}
**入场阈值**: {engine_results.get('entry_threshold', 2)}

---

## 1. 阈值整体指标

| 指标 | 值 |
|------|-----|
| Precision | {threshold_metrics.get('precision', 0):.3f} |
| Recall | {threshold_metrics.get('recall', 0):.3f} |
| F1 | {threshold_metrics.get('f1', 0):.3f} |
| True Positive | {threshold_metrics.get('tp', 0)} |
| False Positive | {threshold_metrics.get('fp', 0)} |
| False Negative | {threshold_metrics.get('fn', 0)} |

---

## 2. 信号有效性排名 (按 F1)

| 信号ID | Precision | Recall | F1 | TP | FP | FN |
|--------|-----------|--------|-----|----|----|-----|
"""
        # 按F1排序
        sorted_signals = sorted(
            signal_metrics.items(),
            key=lambda x: x[1].get("f1", 0),
            reverse=True
        )
        
        for sig_id, m in sorted_signals:
            md += f"| {sig_id} | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | {m['tp']} | {m['fp']} | {m['fn']} |\n"
        
        md += f"""
---

## 3. 阈值分析

| 阈值 | 命中数 | 命中率 | Precision | Recall |
|------|--------|--------|-----------|--------|
"""
        for th in sorted(threshold_analysis.keys()):
            data = threshold_analysis[th]
            md += f"| {th} | {data['hits']} | {data['hit_rate']}% | {data['precision']:.3f} | {data['recall']:.3f} |\n"
        
        # 生成建议
        best_th = 2
        best_rate = 0
        for th, data in threshold_analysis.items():
            if data.get("hit_rate", 0) > best_rate:
                best_rate = data.get("hit_rate", 0)
                best_th = th
        
        top_signals = [s[0] for s in sorted_signals[:3]]
        
        md += f"""
---

## 4. 校准建议

1. **阈值优化**: 当前 {engine_results.get('entry_threshold')} → 建议 {best_th} (命中率 {best_rate}%)
2. **高F1信号**: {', '.join(top_signals)}
3. **权重调整**: 建议提高 {top_signals[0] if top_signals else 'N/A'} 的权重

---

*报告自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""
        
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md)
        md_path = md_file
    
    # 3. CSV 报告
    csv_path = None
    if format in ["csv", "both"]:
        csv_file = f"{output_dir}/signal_metrics_{timestamp}.csv"
        
        rows = []
        for sig_id, m in signal_metrics.items():
            rows.append({
                "signal_id": sig_id,
                "precision": m["precision"],
                "recall": m["recall"],
                "f1": m["f1"],
                "tp": m["tp"],
                "fp": m["fp"],
                "fn": m["fn"],
            })
        
        df = pd.DataFrame(rows)
        df.to_csv(csv_file, index=False)
        csv_path = csv_file
    
    return json_path or md_path or csv_path


def update_config_from_results(
    results: Dict[str, Any],
    config_path: str = "signals/weighted_config.json",
) -> Dict:
    """
    根据回测结果更新配置文件
    
    Returns:
        更新后的配置
    """
    signal_metrics = results.get("signal_metrics", {})
    
    # 加载当前配置
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            config = json.load(f)
    else:
        config = {
            "entry_threshold": 2,
            "threshold_low": 2,
            "threshold_high": 3,
            "high_weight_threshold": 2.0,
            "signal_weights": {},
        }
    
    # 找到最优阈值
    threshold_analysis = results.get("threshold_analysis", {})
    best_th = 2
    best_f1 = 0
    for th, data in threshold_analysis.items():
        # 综合precision和recall
        f1 = data.get("precision", 0) * 0.5 + data.get("recall", 0) * 0.5
        if f1 > best_f1:
            best_f1 = f1
            best_th = th
    
    config["entry_threshold"] = best_th
    config["threshold_low"] = best_th
    config["threshold_high"] = best_th + 1
    
    # 调整权重：F1 > 0.5 的信号权重+20%, F1 < 0.2 的-20%
    if "signal_weights" not in config:
        config["signal_weights"] = {}
    
    base_weights = {
        "signal_1_integer_consolidation": 1.5,
        "signal_2_funding_turn_positive": 1.8,
        "signal_3_oi_accumulation": 1.5,
        "signal_4_volume_spike": 1.0,
        "signal_5_dex_buy_pressure": 1.0,
        "signal_6_btcd_downtrend": 1.0,
        "signal_6b_btc_relative_strength": 1.0,
        "signal_7_new_futures": 1.5,
        "signal_8_wash_test": 1.5,
        "signal_9_social_sentiment": 0.5,
        "signal_10_breakout": 2.0,
        "signal_11_early_warning": 1.0,
        "signal_12_long_short_ratio": 2.0,
        "signal_13_taker_volume": 1.2,
    }
    
    for sig_id, metrics in signal_metrics.items():
        f1 = metrics.get("f1", 0)
        base = base_weights.get(sig_id, 1.0)
        
        if f1 > 0.5:
            new_weight = base * 1.2
        elif f1 < 0.2:
            new_weight = base * 0.8
        else:
            new_weight = base
            
        config["signal_weights"][sig_id] = round(new_weight, 1)
    
    # 保存配置
    os.makedirs(os.path.dirname(config_path) if os.path.dirname(config_path) else ".", exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    
    return config


def load_latest_report(output_dir: str = "backtest/reports") -> Dict:
    """加载最新的校准报告"""
    if not os.path.exists(output_dir):
        return {}
    
    files = [f for f in os.listdir(output_dir) if f.endswith(".json")]
    if not files:
        return {}
    
    # 找最新的
    latest = sorted(files)[-1]
    with open(f"{output_dir}/{latest}", "r") as f:
        return json.load(f)


if __name__ == "__main__":
    # 测试
    test_results = {
        "timestamp": "2026-06-12T12:00:00",
        "sample_count": 10,
        "entry_threshold": 2,
        "signal_metrics": {
            "signal_12_long_short_ratio": {"precision": 0.7, "recall": 0.8, "f1": 0.75, "tp": 7, "fp": 3, "fn": 2},
            "signal_2_funding_turn_positive": {"precision": 0.3, "recall": 0.4, "f1": 0.35, "tp": 3, "fp": 7, "fn": 5},
            "signal_13_taker_volume": {"precision": 0.2, "recall": 0.3, "f1": 0.24, "tp": 2, "fp": 8, "fn": 6},
        },
        "threshold_metrics": {"precision": 0.25, "recall": 0.4, "f1": 0.3, "tp": 2, "fp": 6, "fn": 3},
        "threshold_analysis": {
            1: {"hits": 5, "hit_rate": 50.0, "precision": 0.3, "recall": 0.5},
            2: {"hits": 3, "hit_rate": 30.0, "precision": 0.4, "recall": 0.4},
            3: {"hits": 1, "hit_rate": 10.0, "precision": 0.6, "recall": 0.2},
        },
    }
    
    # 生成报告
    report_path = generate_calibration_report(test_results)
    print(f"Report saved to: {report_path}")
    
    # 更新配置
    new_config = update_config_from_results(test_results)
    print(f"\nNew config: {json.dumps(new_config, indent=2)}")
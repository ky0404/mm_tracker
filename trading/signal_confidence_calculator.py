"""
信号置信度计算器
基于 LEAN 的 ConfidenceWeightedPortfolioConstructionModel 设计
提供动态置信度权重，而非固定权重
"""
import numpy as np
from scipy import stats
from typing import Dict, List, Any, Optional
import json
import os
from datetime import datetime


class SignalConfidenceCalculator:
    """
    信号置信度计算器
    LEAN 参考：ConfidenceWeightedPortfolioConstructionModel.cs
    
    特性：
    1. 样本量评估
    2. 胜率统计显著性检验 (binomtest)
    3. 收益分布分析
    4. 胜率稳定性（滚动窗口）
    5. 综合置信度计算
    """
    
    def __init__(
        self,
        min_samples: int = 10,
        confidence_level: float = 0.95,
        confidence_cache_file: str = "config/signal_confidence_cache.json"
    ):
        self.min_samples = min_samples
        self.confidence_level = confidence_level
        self.confidence_cache_file = confidence_cache_file
        self._cache = self._load_cache()
    
    def _load_cache(self) -> dict:
        """加载缓存的置信度数据"""
        try:
            if os.path.exists(self.confidence_cache_file):
                with open(self.confidence_cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}
    
    def _save_cache(self):
        """保存置信度缓存"""
        os.makedirs(os.path.dirname(self.confidence_cache_file), exist_ok=True)
        with open(self.confidence_cache_file, 'w', encoding='utf-8') as f:
            json.dump(self._cache, f, indent=2, ensure_ascii=False)
    
    def calculate_confidence(
        self,
        signal_name: str,
        trades: List[dict],
        force_recalculate: bool = False
    ) -> dict:
        """
        计算信号的置信度
        
        基于：
        1. 样本量
        2. 胜率统计显著性
        3. 收益分布
        4. 胜率稳定性（滚动窗口）
        """
        # 检查缓存
        if not force_recalculate and signal_name in self._cache:
            cached = self._cache[signal_name]
            # 缓存有效期 24 小时
            try:
                cached_time = datetime.fromisoformat(cached.get("last_updated", "2000-01-01"))
                if (datetime.now() - cached_time).total_seconds() < 86400:
                    return cached
            except Exception:
                pass
        
        # 筛选该信号的交易
        signal_trades = self._filter_signal_trades(signal_name, trades)
        
        if len(signal_trades) < self.min_samples:
            result = {
                "confidence": 0.0,
                "level": "insufficient_data",
                "reason": f"only {len(signal_trades)} samples, need {self.min_samples}",
                "sample_size": len(signal_trades),
                "signal": signal_name,
                "last_updated": datetime.now().isoformat()
            }
            self._cache[signal_name] = result
            self._save_cache()
            return result
        
        # 1. 胜率计算
        wins = [t for t in signal_trades if (t.get("pnl_pct", 0) or 0) > 0 or t.get("win", False)]
        win_rate = len(wins) / len(signal_trades) if signal_trades else 0
        
        # 2. 统计显著性检验
        try:
            result = stats.binomtest(len(wins), len(signal_trades), p=0.5)
            p_value = result.pvalue
            ci = result.proportion_ci(confidence=self.confidence_level)
        except Exception:
            p_value = 1.0
            ci = type('obj', (object,), {'low': 0.3, 'high': 0.7})()
        
        # 3. 收益分布分析
        pnls = [t.get("pnl_pct", 0) or 0 for t in signal_trades]
        avg_pnl = np.mean(pnls) if pnls else 0
        pnl_std = np.std(pnls) if len(pnls) > 1 else 0.01
        
        # 4. 稳定性分析（滚动窗口）
        stability = self._calculate_stability(signal_trades)
        
        # 5. 综合置信度
        confidence = self._compute_composite_confidence(
            win_rate=win_rate,
            p_value=p_value,
            ci_width=ci.high - ci.low,
            avg_pnl=avg_pnl,
            pnl_std=pnl_std,
            stability=stability
        )
        
        result = {
            "confidence": round(confidence, 3),
            "level": self._confidence_level(confidence),
            "win_rate": round(win_rate, 3),
            "p_value": round(p_value, 4),
            "ci_lower": round(ci.low, 3),
            "ci_upper": round(ci.high, 3),
            "avg_pnl": round(avg_pnl, 3),
            "pnl_std": round(pnl_std, 3),
            "stability": round(stability, 3),
            "sample_size": len(signal_trades),
            "wins": len(wins),
            "losses": len(signal_trades) - len(wins),
            "recommendation": self._get_recommendation(confidence, win_rate, p_value),
            "signal": signal_name,
            "last_updated": datetime.now().isoformat()
        }
        
        # 缓存结果
        self._cache[signal_name] = result
        self._save_cache()
        
        return result
    
    def _filter_signal_trades(self, signal_name: str, trades: List[dict]) -> List[dict]:
        """筛选包含指定信号的交易"""
        filtered = []
        for trade in trades:
            # 检查多个可能的信号字段
            signals = trade.get("signals") or []
            entry_signals = trade.get("entry_signals") or []
            triggered = (trade.get("market_context") or {}).get("signals_triggered") or []
            
            if signals and signal_name in signals:
                filtered.append(trade)
            elif entry_signals and signal_name in entry_signals:
                filtered.append(trade)
            elif triggered and signal_name in triggered:
                filtered.append(trade)
        
        return filtered
    
    def _calculate_stability(self, trades: list) -> float:
        """
        计算胜率稳定性（滚动窗口）
        """
        if len(trades) < 20:
            return 0.5
        
        # 滚动窗口胜率
        window_size = min(10, len(trades) // 2)
        if window_size < 3:
            return 0.5
        
        rolling_wr = []
        sorted_trades = sorted(trades, key=lambda x: x.get("exit_timestamp", x.get("timestamp", "")))
        
        for i in range(window_size, len(sorted_trades)):
            window = sorted_trades[i-window_size:i]
            wins = sum(1 for t in window if (t.get("pnl_pct", 0) or 0) > 0 or t.get("win", False))
            rolling_wr.append(wins / window_size)
        
        # 稳定性 = 1 - 滚动胜率标准差
        if rolling_wr:
            return max(0, 1 - np.std(rolling_wr))
        return 0.5
    
    def _compute_composite_confidence(
        self,
        win_rate: float,
        p_value: float,
        ci_width: float,
        avg_pnl: float,
        pnl_std: float,
        stability: float
    ) -> float:
        """
        计算综合置信度
        
        权重分配：
        - 显著性得分: 30%
        - 置信区间得分: 20%
        - 收益得分: 25%
        - 胜率得分: 15%
        - 稳定性得分: 10%
        """
        # 显著性得分 (0-1)
        if p_value < 0.01:
            significance_score = 1.0
        elif p_value < 0.05:
            significance_score = 0.8
        elif p_value < 0.1:
            significance_score = 0.5
        elif p_value < 0.2:
            significance_score = 0.3
        else:
            significance_score = max(0, 1 - p_value * 5)
        
        # 置信区间得分 (越窄越好)
        ci_score = max(0, 1 - ci_width * 2)
        
        # 收益得分 (正向收益更好)
        if pnl_std > 0:
            pnl_score = min(1, abs(avg_pnl) / pnl_std)
        else:
            pnl_score = 0.5 if avg_pnl > 0 else 0.3
        
        # 胜率得分 (离 0.5 越远越好)
        wr_score = abs(win_rate - 0.5) * 2
        
        # 综合置信度
        confidence = (
            significance_score * 0.30 +
            ci_score * 0.20 +
            pnl_score * 0.25 +
            wr_score * 0.15 +
            stability * 0.10
        )
        
        return min(1.0, max(0.0, confidence))
    
    def _confidence_level(self, confidence: float) -> str:
        """
        置信度等级
        """
        if confidence >= 0.8:
            return "very_high"
        elif confidence >= 0.6:
            return "high"
        elif confidence >= 0.4:
            return "medium"
        elif confidence >= 0.2:
            return "low"
        else:
            return "very_low"
    
    def _get_recommendation(self, confidence: float, win_rate: float, p_value: float) -> str:
        """
        基于置信度的权重建议
        """
        if confidence < 0.2:
            return "disable"
        elif confidence < 0.4:
            return "reduce_weight"
        elif confidence >= 0.8 and p_value < 0.05 and win_rate > 0.55:
            return "increase_weight"
        else:
            return "maintain"
    
    def get_all_confidences(self, trades: List[dict]) -> Dict[str, dict]:
        """
        批量计算所有信号的置信度
        """
        # 收集所有信号
        all_signals = set()
        for trade in trades:
            signals = trade.get("signals") or []
            entry_signals = trade.get("entry_signals") or []
            triggered = (trade.get("market_context") or {}).get("signals_triggered") or []
            if signals:
                all_signals.update(signals)
            if entry_signals:
                all_signals.update(entry_signals)
            if triggered:
                all_signals.update(triggered)
        
        results = {}
        for signal in all_signals:
            results[signal] = self.calculate_confidence(signal, trades)
        
        return results
    
    def get_confidence_weight(self, signal_name: str, base_weight: float, trades: List[dict]) -> float:
        """
        获取置信度调整后的权重
        
        公式：final_weight = base_weight * confidence_factor
        
        confidence_factor:
        - very_high: 1.2 (增加权重)
        - high: 1.0 (保持)
        - medium: 0.8 (轻微降低)
        - low: 0.5 (降低)
        - very_low: 0.2 (大幅降低)
        - insufficient_data: 0.5 (数据不足，降低权重)
        """
        confidence_data = self.calculate_confidence(signal_name, trades)
        confidence = confidence_data.get("confidence", 0.5)
        level = confidence_data.get("level", "medium")
        
        # 置信度因子
        if level == "very_high":
            confidence_factor = 1.2
        elif level == "high":
            confidence_factor = 1.0
        elif level == "medium":
            confidence_factor = 0.8
        elif level == "low":
            confidence_factor = 0.5
        elif level == "very_low":
            confidence_factor = 0.2
        else:
            confidence_factor = 0.5  # insufficient_data
        
        final_weight = base_weight * confidence_factor
        
        # 边界保护
        return max(0.1, min(3.0, final_weight))


def get_confidence_calculator() -> SignalConfidenceCalculator:
    """获取置信度计算器单例"""
    global _confidence_calculator
    if _confidence_calculator is None:
        _confidence_calculator = SignalConfidenceCalculator()
    return _confidence_calculator


_confidence_calculator: Optional[SignalConfidenceCalculator] = None
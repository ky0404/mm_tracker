"""
四级状态机 + 置信度分层
基于回测校准的实战版本
"""

from typing import List, Dict, Any, Optional
import json
import os


class StateMachine:
    """
    四级状态机 + 置信度分层：
    - OBSERVE (0-1 信号): 观察
    - WARNING (2 信号): 预警
    - ENTRY_LOW_CONFIDENCE (2 信号 + 高权重信号 >=1): 低置信入场
    - ENTRY_HIGH_CONFIDENCE (3+ 信号 + 高权重信号 >=2): 高置信入场
    - EXIT (FR > 0.5%): 离场
    """

    STATE_OBSERVE = "OBSERVE"
    STATE_WARNING = "WARNING"
    STATE_ENTRY_LOW = "ENTRY_LOW_CONFIDENCE"
    STATE_ENTRY_HIGH = "ENTRY_HIGH_CONFIDENCE"
    STATE_EXIT = "EXIT"

    def __init__(
        self,
        threshold_low: int = 1,
        threshold_high: int = 3,
        high_weight_threshold: float = 2.0,
    ):
        self.threshold_low = threshold_low
        self.threshold_high = threshold_high
        self.high_weight_threshold = high_weight_threshold

    def classify_state(
        self,
        entry_signals: List[Dict[str, Any]],
        exit_signals: List[Dict[str, Any]],
    ) -> str:
        """
        根据入场/离场信号分类状态
        
        Args:
            entry_signals: [{"name": str, "score": float, "weight": float, "detail": str}, ...]
            exit_signals: 同上
            
        Returns:
            状态名
        """
        # 1. 先判断离场
        if exit_signals:
            return self.STATE_EXIT

        entry_count = len(entry_signals)

        if entry_count == 0:
            return self.STATE_OBSERVE

        # 2. 计算高权重信号数
        high_weight_count = sum(
            1 for s in entry_signals if s.get("weight", 0) >= self.high_weight_threshold
        )

        # 3. 分层判断
        if entry_count >= self.threshold_high and high_weight_count >= 2:
            return self.STATE_ENTRY_HIGH

        if entry_count >= self.threshold_low and high_weight_count >= 1:
            return self.STATE_ENTRY_LOW

        if entry_count >= self.threshold_low:
            return self.STATE_WARNING

        return self.STATE_OBSERVE

    def get_state_label(self, state: str) -> str:
        """获取状态标签"""
        labels = {
            self.STATE_OBSERVE: "🔵 OBSERVE - 观察",
            self.STATE_WARNING: "🟡 WARNING - 预警",
            self.STATE_ENTRY_LOW: "🟢 ENTRY (低置信) - 轻仓",
            self.STATE_ENTRY_HIGH: "🟢 ENTRY (高置信) - 建仓",
            self.STATE_EXIT: "🔴 EXIT - 离场",
        }
        return labels.get(state, state)
    
    def get_action(self, state: str) -> str:
        """获取状态对应的操作建议"""
        actions = {
            self.STATE_OBSERVE: "仅监控，不操作",
            self.STATE_WARNING: "密切关注，准备资金",
            self.STATE_ENTRY_LOW: "轻仓尝试 (20-30%)",
            self.STATE_ENTRY_HIGH: "建仓 (50-70%)",
            self.STATE_EXIT: "减仓或清仓",
        }
        return actions.get(state, "等待")

    def evaluate(
        self,
        entry_signals: List[Dict[str, Any]],
        exit_signals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        完整评估，返回状态详情
        """
        state = self.classify_state(entry_signals, exit_signals)
        
        # 计算高权重信号
        high_weight_count = sum(
            1 for s in entry_signals if s.get("weight", 0) >= self.high_weight_threshold
        )
        
        # 计算总分
        total_score = sum(s.get("score", 0) for s in entry_signals)
        
        return {
            "state": state,
            "state_label": self.get_state_label(state),
            "action": self.get_action(state),
            "entry_count": len(entry_signals),
            "exit_count": len(exit_signals),
            "high_weight_count": high_weight_count,
            "total_score": total_score,
            "threshold_low": self.threshold_low,
            "threshold_high": self.threshold_high,
        }


def load_config(config_path: str = "signals/weighted_config.json") -> Dict:
    """加载配置文件"""
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return json.load(f)
    return {}


def save_config(config: Dict, config_path: str = "signals/weighted_config.json"):
    """保存配置文件"""
    os.makedirs(os.path.dirname(config_path) if os.path.dirname(config_path) else ".", exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def create_default_config() -> Dict:
    """创建默认配置"""
    return {
        "entry_threshold": 2,
        "threshold_low": 2,
        "threshold_high": 3,
        "high_weight_threshold": 2.0,
        "signal_weights": {
            "signal_1_integer_consolidation": 1.5,
            "signal_2_funding_turn_positive": 1.8,
            "signal_3_oi_accumulation": 1.5,
            "signal_4_volume_spike": 1.0,
            "signal_5_dex_buy_pressure": 1.0,
            "signal_6_btcd_downtrend": 1.0,
            "signal_6b_btc_relative_strength": 1.0,
            "signal_7_new_futures": 0.0,  # 已禁用
            "signal_8_wash_test": 1.5,
            "signal_9_social_sentiment": 0.5,
            "signal_10_breakout": 2.0,
            "signal_11_early_warning": 1.0,
            "signal_12_long_short_ratio": 2.0,
            "signal_13_taker_volume": 1.2,
        }
    }


if __name__ == "__main__":
    # 测试
    sm = StateMachine(threshold_low=2, threshold_high=3, high_weight_threshold=2.0)
    
    # 测试场景
    test_cases = [
        ([], []),
        ([{"name": "s12", "score": 2.0, "weight": 2.0}], []),
        ([
            {"name": "s12", "score": 2.0, "weight": 2.0},
            {"name": "s2", "score": 1.5, "weight": 1.8},
        ], []),
        ([
            {"name": "s12", "score": 2.0, "weight": 2.0},
            {"name": "s2", "score": 1.5, "weight": 1.8},
            {"name": "s13", "score": 1.2, "weight": 1.2},
        ], []),
        ([], [{"name": "fr_high", "reason": "FR > 0.5%"}]),
    ]
    
    for i, (entry, exit_) in enumerate(test_cases, 1):
        state = sm.classify_state(entry, exit_)
        result = sm.evaluate(entry, exit_)
        print(f"Case {i}: {sm.get_state_label(state)} | {result['action']}")
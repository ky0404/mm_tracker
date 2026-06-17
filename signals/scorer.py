"""
信号评分器
综合7个信号，计算总分和风险等级
新7信号框架:
1. 价格在整数关口下方横盘 3~7 天
2. 资金费率从负/零开始转正且持续上升
3. OI 在价格横盘期间悄悄增加
4. 某一天出现 3x 以上放量但价格未大涨
5. DexScreener 买卖比 >1.2 且持续多日
6. BTC.D 处于下降通道
7. Binance 新增了该币的永续合约
"""

from typing import Dict, List, Any


class MMScorer:
    """
    MMTracker 评分器
    
    综合7个信号的触发情况，计算加权总分和风险等级
    入场条件: 满足5个以上信号
    离场铁律: Funding Rate > 0.5% 减仓, > 1% 清仓
    """
    
    # 每个信号的权重 - 回测校准后(2026-06-12)
    # 基于命中率调整: S12(70%) ↑, S2(20%) →, S13(10%) ↓
    WEIGHTS = {
        "signal_1_integer_consolidation": 1.5,  # 整数关口横盘（3-7天或>2周）
        "signal_2_funding_turn_positive": 1.8,  # 资金费率转正（命中率20%，略降）
        "signal_3_oi_accumulation": 1.5,        # OI吸筹
        "signal_4_volume_spike": 1.0,           # 放量（3x/5x/10x/20x）
        "signal_5_dex_buy_pressure": 1.0,       # DEX买压（持续多日更强）
        "signal_6_btcd_downtrend": 1.0,         # BTC.D下降（利好山寨）
        "signal_6b_btc_relative_strength": 1.0, # BTC相对强度（资金托盘）
        "signal_7_new_futures": 0.0,            # 新合约上线 - 已禁用，亏损严重
        "signal_8_wash_test": 1.5,              # 洗盘测试期（假拉升）
        "signal_9_social_sentiment": 0.5,       # 社媒情绪（占位，权重较低）
        "signal_10_breakout": 2.0,              # 关键心理关口突破（最重要）
        "signal_11_early_warning": 1.0,         # 早期组合预警
        "signal_12_long_short_ratio": 2.0,      # 多空比（命中率70%，权重↑）
        "signal_13_taker_volume": 1.2,          # 主动成交量（命中率10%，权重↓）
    }
    
    # 信号名称映射（用于显示）
    SIGNAL_NAMES = {
        "signal_1_integer_consolidation": "整数关口横盘(3-7天/>2周)",
        "signal_2_funding_turn_positive": "资金费率转正+上升趋势",
        "signal_3_oi_accumulation": "OI暗中增加",
        "signal_4_volume_spike": "放量未大涨(3x/5x/10x/20x)",
        "signal_5_dex_buy_pressure": "DEX买压>1.05(持续多日)",
        "signal_6_btcd_downtrend": "BTC.D下降通道",
        "signal_6b_btc_relative_strength": "BTC相对强度(托盘)",
        "signal_7_new_futures": "Binance新合约",
        "signal_8_wash_test": "洗盘测试期(假拉升)",
        "signal_9_social_sentiment": "社媒情绪",
        "signal_10_breakout": "关键关口突破",
        "signal_11_early_warning": "早期预警(2-3信号)",
        "signal_12_long_short_ratio": "多空比(多头>70%)",
        "signal_13_taker_volume": "主动成交量(买入>卖出)",
    }
    
    # 最大权重和
    MAX_WEIGHT_SUM = sum(WEIGHTS.values())  # 14.5
    
    def __init__(self, custom_weights: Dict[str, float] = None):
        """
        初始化评分器
        
        Args:
            custom_weights: 可选的权重覆盖
        """
        if custom_weights:
            self.WEIGHTS = custom_weights
    
    def score(self, signals: Dict[str, dict]) -> dict:
        """
        输入信号的计算结果，输出综合评分
        
        Args:
            signals: 信号结果字典
            
        Returns:
            综合评分结果（可解释）
        """
        # 1. 计算加权总分 + 每个信号的详细分数
        total_score = 0.0
        triggered_count = 0
        entry_signals = []
        exit_signals = []
        signal_details = []
        
        for signal_name, signal_result in signals.items():
            weight = self.WEIGHTS.get(signal_name, 1.0)
            signal_score = 0.0
            signal_strength = "none"
            detail = signal_result.get("detail", "")
            
            if signal_result.get("triggered", False):
                triggered_count += 1
                
                # 根据触发强度计算分数
                if signal_name == "signal_2_funding_turn_positive":
                    # 资金费率：强转正得满分，普通转正得80%
                    if signal_result.get("triggered_strong", False):
                        signal_score = weight
                        signal_strength = "strong"
                    else:
                        signal_score = weight * 0.8
                        signal_strength = "moderate"
                elif signal_name == "signal_4_volume_spike":
                    # 放量：根据级别给分
                    spike_level = signal_result.get("spike_level", "none")
                    if spike_level == "20x+":
                        signal_score = weight
                        signal_strength = "strong"
                    elif spike_level == "10x+":
                        signal_score = weight * 0.9
                        signal_strength = "strong"
                    elif spike_level == "5x+":
                        signal_score = weight * 0.7
                        signal_strength = "moderate"
                    else:
                        signal_score = weight * 0.5
                        signal_strength = "weak"
                elif signal_name == "signal_5_dex_buy_pressure":
                    # DEX买压：持续多日更强
                    sustained = signal_result.get("sustained_days", 0)
                    strength = signal_result.get("strength", "none")
                    if sustained >= 3 and strength == "strong":
                        signal_score = weight
                        signal_strength = "strong"
                    elif sustained >= 3:
                        signal_score = weight * 0.8
                        signal_strength = "moderate"
                    else:
                        signal_score = weight * 0.5
                        signal_strength = "weak"
                elif signal_name == "signal_12_long_short_ratio":
                    # 多空比
                    long_ratio = signal_result.get("long_ratio", 0)
                    if long_ratio > 80:
                        signal_score = weight
                        signal_strength = "strong"
                    else:
                        signal_score = weight * 0.7
                        signal_strength = "moderate"
                else:
                    # 其他信号：触发即得满分
                    signal_score = weight
                    signal_strength = "moderate"
                
                total_score += signal_score
                
                # 记录入场信号
                entry_signals.append({
                    "name": signal_name,
                    "score": round(signal_score, 2),
                    "weight": weight,
                    "strength": signal_strength,
                    "detail": detail[:80],
                    "full_detail": detail,
                })
            else:
                # 检查是否是退出信号（FR > 0.5%）
                if signal_name == "signal_2_funding_turn_positive":
                    current_rate = signal_result.get("current_avg", 0)
                    if current_rate > 0.5:
                        exit_signals.append({
                            "name": "funding_rate_high",
                            "reason": f"资金费率 {current_rate:.2f}% > 0.5%",
                            "action": "减仓",
                        })
                    elif current_rate > 1.0:
                        exit_signals.append({
                            "name": "funding_rate_very_high",
                            "reason": f"资金费率 {current_rate:.2f}% > 1.0%",
                            "action": "清仓",
                        })
            
            # 记录所有信号的详情（用于调试）
            signal_details.append({
                "name": signal_name,
                "triggered": signal_result.get("triggered", False),
                "weight": weight,
                "score": round(signal_score, 2),
                "strength": signal_strength,
            })
        
        # 2. 判定等级
        grade, grade_emoji, grade_label = self._determine_grade(
            triggered_count, total_score
        )
        
        # 3. 生成信号摘要
        signal_summary = self._build_signal_summary(signals)
        
        # 4. 找出最高权重的触发信号
        top_signals = self._get_top_signals(signals)
        
        # 5. 生成操作建议
        recommendation = self._generate_recommendation(
            triggered_count, total_score, grade, top_signals, exit_signals
        )
        
        return {
            "total_score": round(total_score, 2),
            "max_score": self.MAX_WEIGHT_SUM,
            "triggered_count": triggered_count,
            "entry_signals_count": len(entry_signals),
            "exit_signals_count": len(exit_signals),
            "grade": grade,
            "grade_emoji": grade_emoji,
            "grade_label": grade_label,
            "entry_signals": entry_signals,
            "exit_signals": exit_signals,
            "signal_summary": signal_summary,
            "top_signals": top_signals,
            "recommendation": recommendation,
            "weights": self.WEIGHTS,
            "entry_threshold": 2,  # 回测校准：从4改为2
            "signal_details": signal_details,
        }
    
    def _determine_grade(self, triggered_count: int, total_score: float) -> tuple:
        """
        判定风险等级
        
        回测校准 (2026-06-12):
        - 4信号阈值命中率0%，过于严格
        - 2信号阈值命中率20%，为最优
        - 调整为: 2+信号入场，1信号预警
        
        Args:
            triggered_count: 触发的信号数
            total_score: 加权总分
            
        Returns:
            (grade, emoji, label)
        """
        if triggered_count >= 2:
            return ("ENTRY", "🟢", "满足入场条件")
        elif triggered_count >= 1:
            return ("WATCH", "🟡", "密切关注 ⚠️")
        else:
            return ("MONITOR", "🔵", "持续监控")
    
    def _build_signal_summary(self, signals: Dict[str, dict]) -> List[dict]:
        """
        构建信号摘要
        
        Args:
            signals: 信号结果字典
            
        Returns:
            信号摘要列表
        """
        summary = []
        
        for signal_name in self.WEIGHTS.keys():
            signal_result = signals.get(signal_name, {})
            signal_name_cn = self.SIGNAL_NAMES.get(signal_name, signal_name)
            
            triggered = signal_result.get("triggered", False)
            detail = signal_result.get("detail", "")
            weight = self.WEIGHTS.get(signal_name, 1.0)
            
            summary.append({
                "signal": signal_name,
                "signal_cn": signal_name_cn,
                "triggered": triggered,
                "detail": detail,
                "weight": weight,
            })
        
        return summary
    
    def _get_top_signals(self, signals: Dict[str, dict]) -> List[str]:
        """
        获取触发的最高权重信号
        
        Args:
            signals: 信号结果字典
            
        Returns:
            触发信号名称列表（按权重排序）
        """
        triggered_signals = []
        
        for signal_name, signal_result in signals.items():
            if signal_result.get("triggered", False):
                weight = self.WEIGHTS.get(signal_name, 1.0)
                triggered_signals.append({
                    "name": signal_name,
                    "weight": weight,
                    "detail": signal_result.get("detail", ""),
                })
        
        # 按权重排序
        triggered_signals.sort(key=lambda x: x["weight"], reverse=True)
        
        # 返回名称列表
        return [s["name"] for s in triggered_signals]
    
    def _generate_recommendation(
        self,
        triggered_count: int,
        total_score: float,
        grade: str,
        top_signals: List[str],
        exit_signals: List[dict] = None
    ) -> str:
        """
        生成操作建议
        
        Args:
            triggered_count: 触发信号数
            total_score: 加权总分
            grade: 风险等级
            top_signals: 触发的最高权重信号
            exit_signals: 退出信号列表
            
        Returns:
            操作建议字符串
        """
        if exit_signals is None:
            exit_signals = []
        
        # 将信号名转换为中文
        signal_map = {
            "signal_1_integer_consolidation": "整数横盘",
            "signal_2_funding_turn_positive": "资金费率转正",
            "signal_3_oi_accumulation": "OI吸筹",
            "signal_4_volume_spike": "放量",
            "signal_5_dex_buy_pressure": "DEX买压",
            "signal_6_btcd_downtrend": "BTC.D下降",
            "signal_7_new_futures": "新合约",
            "signal_8_wash_test": "洗盘测试",
            "signal_10_breakout": "突破",
            "signal_12_long_short_ratio": "多空比",
            "signal_13_taker_volume": "主动买盘",
        }
        
        top_signals_cn = [signal_map.get(s, s) for s in top_signals]
        
        # 构建退出信号提示
        exit_warning = ""
        if exit_signals:
            exit_actions = [e["action"] for e in exit_signals]
            if "清仓" in exit_actions:
                exit_warning = " ⚠️ 离场信号：资金费率 >1%，建议清仓！"
            elif "减仓" in exit_actions:
                exit_warning = " ⚠️ 离场警告：资金费率 >0.5%，考虑减仓"
        
        if grade == "ENTRY":
            if top_signals_cn:
                return f"🟢 入场条件满足！触发 {triggered_count} 个信号，关键信号：{', '.join(top_signals_cn[:3])}"
            else:
                return "🟢 入场条件满足！触发 3+ 个信号"
        
        elif grade == "WATCH":
            return f"🟡 存在部分启动迹象，持续跟踪。已触发信号：{', '.join(top_signals_cn) if top_signals_cn else '无'}"
        
        elif grade == "MONITOR":
            return f"🔵 信号零星，加入监控池等待更多确认（还需 {4-triggered_count} 个信号）"
        
        else:
            return "⚪ 当前无明显庄家信号，保持观望"
    
    def get_score_breakdown(self, signals: Dict[str, dict]) -> dict:
        """
        获取分数分解（用于调试）
        
        Args:
            signals: 信号结果字典
            
        Returns:
            分数分解详情
        """
        breakdown = {}
        
        for signal_name, signal_result in signals.items():
            weight = self.WEIGHTS.get(signal_name, 1.0)
            triggered = signal_result.get("triggered", False)
            
            breakdown[signal_name] = {
                "weight": weight,
                "triggered": triggered,
                "score": weight if triggered else 0.0,
                "signal_cn": self.SIGNAL_NAMES.get(signal_name, signal_name),
            }
        
        return breakdown


def calculate_final_score(signals: Dict[str, dict]) -> dict:
    """
    便捷函数：计算最终评分
    
    Args:
        signals: 7个信号的结果字典
        
    Returns:
        评分结果
    """
    scorer = MMScorer()
    return scorer.score(signals)


def check_exit_rule(funding_rate_pct: float) -> dict:
    """
    检查离场铁律
    
    Args:
        funding_rate_pct: 资金费率（百分比形式，如 0.5 表示 0.5%）
        
    Returns:
        离场建议
    """
    if funding_rate_pct > 1.0:
        return {
            "action": "EXIT",
            "reason": f"资金费率 {funding_rate_pct:.3f}% > 1%，触发清仓铁律",
            "color": "🔴"
        }
    elif funding_rate_pct > 0.5:
        return {
            "action": "REDUCE",
            "reason": f"资金费率 {funding_rate_pct:.3f}% > 0.5%，触发减仓警告",
            "color": "🟡"
        }
    else:
        return {
            "action": "HOLD",
            "reason": f"资金费率 {funding_rate_pct:.3f}% 正常，继续持有",
            "color": "🟢"
        }
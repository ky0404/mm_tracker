"""
NFI风格 DCA (Dollar Cost Averaging) 模块
基于 NostalgiaForInfinityX 的6种加仓模式

核心思想：
- 不是一次性买入，而是分批建仓
- 亏损时加仓降低成本
- 但要严格控制加仓次数和金额
- 通过BTC趋势和价格稳定性过滤假突破
"""
import json
import logging
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DCAResult:
    """DCA决策结果"""
    should_dca: bool
    dca_amount: float  # 加仓金额（相对于首次买入的比例）
    dca_mode: str
    reason: str
    safety_passed: bool


class NFIDCAManager:
    """
    NFI风格 DCA 管理器
    6种模式：
    - mode_0: 标准模式 (默认)
    - mode_1: 保守模式
    - mode_2: 快速模式 (用于波动币)
    - mode_3: 激进模式
    - mode_4: 复合模式
    - mode_5: 半仓模式
    """
    
    # 模式配置
    MODES = {
        "mode_0": {
            "name": "标准模式",
            "max_orders": 4,
            "thresholds": [-0.04, -0.06, -0.09, -0.12],
            "multiplier": 0.15,
            "multiplier_increment": 0.005
        },
        "mode_1": {
            "name": "保守模式",
            "max_orders": 2,
            "thresholds": [-0.06, -0.12],
            "multiplier": 0.30,
            "multiplier_increment": 0
        },
        "mode_2": {
            "name": "快速模式",
            "max_orders": 4,
            "thresholds": [-0.03, -0.04, -0.06, -0.09],
            "multiplier": 0.15,
            "multiplier_increment": 0
        },
        "mode_3": {
            "name": "激进模式",
            "max_orders": 1,
            "thresholds": [-0.06, -0.12],
            "multiplier": 0.50,
            "multiplier_increment": 0
        },
        "mode_4": {
            "name": "复合模式",
            "max_orders": 3,
            "thresholds": [-0.02, -0.06, -0.10],
            "multiplier": 1.0,
            "multiplier_increment": 1.0
        },
        "mode_5": {
            "name": "半仓模式",
            "max_orders": 2,
            "thresholds": [-0.05, -0.08],
            "multiplier": 1.0,
            "multiplier_increment": 0
        }
    }
    
    def __init__(self, config_path: str = "config/params.json"):
        self.config = self._load_config(config_path)
        self.dca_config = self.config.get("dca_config", {})
        
    def _load_config(self, path: str) -> Dict:
        """加载配置"""
        try:
            with open(path) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载DCA配置失败，使用默认配置: {e}")
            return {"dca_config": {}}
    
    def select_mode(self, enter_tag: Optional[str] = None, 
                    rapid_mode: bool = False, 
                    half_mode: bool = False) -> str:
        """
        根据买入标签自动选择加仓模式
        
        NFI规则:
        - rapid_mode_tags (66-72): 使用快速模式
        - half_mode_tags (73-74): 使用半仓模式
        - 其他: 使用标准模式
        """
        if rapid_mode:
            return "mode_2"
        elif half_mode:
            return "mode_5"
        elif enter_tag:
            # 根据标签判断
            rapid_tags = {"66", "67", "68", "69", "70", "71", "72"}
            half_tags = {"73", "74"}
            
            if enter_tag in rapid_tags:
                return "mode_2"
            elif enter_tag in half_tags:
                return "mode_5"
        
        return "mode_0"  # 默认标准模式
    
    def check_safety(self, current_profit: float, 
                     btc_pct_max_72: float = 1.0,
                     close_max_48: float = float('inf'),
                     current_price: float = 0) -> Tuple[bool, str]:
        """
        安全检查 - NFI核心逻辑
        
        检查项:
        1. BTC安全: 72小时内BTC涨幅 < 2%
        2. 价格稳定: 48小时内最高价不超过当前价5%
        3. 已盈利不加仓: 利润 > -2% 时不加仓
        
        返回: (是否安全, 原因)
        """
        # 规则1: 已盈利不加仓
        if current_profit > -0.02:
            return False, f"已盈利 {current_profit*100:.1f}%，不加仓"
        
        # 规则2: BTC安全检查
        if self.dca_config.get("btc_safety_check", True):
            if btc_pct_max_72 > 1.02:
                return False, f"BTC 72h涨幅 {btc_pct_max_72*100:.1f}% 超过2%，不安全"
        
        # 规则3: 价格稳定性检查
        if self.dca_config.get("price_stability_check", True) and current_price > 0:
            if close_max_48 > current_price * 1.05:
                return False, f"48h最高价超出当前价5%，价格不稳定"
        
        return True, "安全检查通过"
    
    def calculate_dca(self, current_profit: float, 
                      entry_count: int,
                      mode: str = "mode_0",
                      btc_pct_max_72: float = 1.0,
                      close_max_48: float = float('inf'),
                      current_price: float = 0,
                      initial_entry_usd: float = 0) -> DCAResult:
        """
        计算是否需要DCA加仓
        
        参数:
            current_profit: 当前盈亏比例 (负数为亏损, 如 -0.05 = -5%)
            entry_count: 当前持仓次数 (1=首次买入, 2=加仓1次, ...)
            mode: 加仓模式
            btc_pct_max_72: BTC 72小时最大涨幅比例
            close_max_48: 48小时最高价
            current_price: 当前价格
            initial_entry_usd: 首次入场金额(USD)
        
        返回:
            DCAResult: 是否加仓、加仓金额、原因
        """
        # 获取模式配置
        mode_config = self.MODES.get(mode, self.MODES["mode_0"])
        
        # 检查是否超过最大加仓次数
        if entry_count > mode_config["max_orders"]:
            return DCAResult(
                should_dca=False,
                dca_amount=0,
                dca_mode=mode,
                reason=f"已达到最大加仓次数 {mode_config['max_orders']}",
                safety_passed=False
            )
        
        # 安全检查
        safety_ok, safety_reason = self.check_safety(
            current_profit, btc_pct_max_72, close_max_48, current_price
        )
        
        if not safety_ok:
            return DCAResult(
                should_dca=False,
                dca_amount=0,
                dca_mode=mode,
                reason=safety_reason,
                safety_passed=False
            )
        
        # 获取当前仓位的目标阈值
        # entry_count=1 表示第1次加仓(第2次入场)，对应 thresholds[0]
        # entry_count=2 表示第2次加仓(第3次入场)，对应 thresholds[1]
        threshold_index = entry_count - 1
        
        if threshold_index >= len(mode_config["thresholds"]):
            return DCAResult(
                should_dca=False,
                dca_amount=0,
                dca_mode=mode,
                reason="超出阈值数组范围",
                safety_passed=True
            )
        
        target_threshold = mode_config["thresholds"][threshold_index]
        
        # 检查是否触发加仓条件
        if current_profit < target_threshold:
            # 计算加仓金额
            base_multiplier = mode_config["multiplier"]
            increment = mode_config["multiplier_increment"]
            
            if increment > 0:
                # 复合模式：每次加仓递增
                multiplier = base_multiplier + (entry_count - 1) * increment
            else:
                # 标准模式：固定比例
                multiplier = base_multiplier
            
            dca_usd = initial_entry_usd * multiplier
            
            return DCAResult(
                should_dca=True,
                dca_amount=dca_usd,
                dca_mode=mode,
                reason=f"亏损 {current_profit*100:.1f}% 触发加仓 (阈值 {target_threshold*100:.0f}%)",
                safety_passed=True
            )
        
        return DCAResult(
            should_dca=False,
            dca_amount=0,
            dca_mode=mode,
            reason=f"亏损 {current_profit*100:.1f}% 未达到加仓阈值 {target_threshold*100:.0f}%",
            safety_passed=True
        )
    
    def simulate_dca_sequence(self, initial_profit: float = -0.01,
                               mode: str = "mode_0") -> list:
        """
        模拟一次完整加仓序列
        用于回测和参数优化
        """
        results = []
        current_profit = initial_profit
        entry_count = 1
        
        # 模拟最多10次加仓尝试
        for i in range(10):
            result = self.calculate_dca(
                current_profit=current_profit,
                entry_count=entry_count,
                mode=mode,
                btc_pct_max_72=1.01,  # 假设BTC稳定
                close_max_48=1.02,     # 假设价格稳定
                current_price=100,
                initial_entry_usd=888
            )
            
            results.append({
                "step": i + 1,
                "entry_count": entry_count,
                "profit_before": current_profit,
                "dca_triggered": result.should_dca,
                "dca_amount": result.dca_amount,
                "reason": result.reason
            })
            
            if result.should_dca:
                # 模拟加仓后成本降低
                # 假设加仓后平均成本向当前价格靠拢
                current_profit = (current_profit + 0.02) * 0.8  # 成本改善
                entry_count += 1
            else:
                break
                
            if entry_count > 10:
                break
        
        return results


# 全局实例
dca_manager = NFIDCAManager()


def calculate_dca(token: str, current_profit: float, 
                  entry_count: int, **kwargs) -> DCAResult:
    """
    便捷函数：计算DCA加仓
    """
    return dca_manager.calculate_dca(
        current_profit=current_profit,
        entry_count=entry_count,
        **kwargs
    )


if __name__ == "__main__":
    # 测试
    manager = NFIDCAManager()
    
    print("=== 测试 DCA 逻辑 ===")
    print(f"模式列表: {list(manager.MODES.keys())}")
    print()
    
    # 模拟标准模式加仓序列
    results = manager.simulate_dca_sequence(-0.01, "mode_0")
    for r in results:
        print(f"步骤{r['step']}: 入场次数={r['entry_count']}, "
              f"亏损={r['profit_before']*100:.1f}%, "
              f"加仓={r['dca_triggered']}, "
              f"金额=${r['dca_amount']:.2f}")
"""
MMTracker 配置文件
包含所有信号检测的阈值参数、数据源配置、评分规则等

新7信号框架 (2026-06):
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

import os
from dataclasses import dataclass
from typing import Optional


# ============================================================================
# 数据源开关配置
# ============================================================================

@dataclass
class DataSourceSwitches:
    """数据源启用/禁用开关"""
    
    ENABLE_OKX: bool = True          # OKX (主力数据源)
    ENABLE_BINANCE: bool = False     # Binance (国内被封锁)
    ENABLE_DEXSCRENER: bool = True   # DexScreener (DEX数据)
    ENABLE_COINGECKO: bool = True    # CoinGecko (价格数据)
    ENABLE_COINGLASS: bool = False   # Coinglass (需API Key)
    ENABLE_GECKOTERMINAL: bool = True  # GeckoTerminal (新池发现)
    ENABLE_KRAKEN: bool = False      # Kraken (备用)
    
    def __post_init__(self):
        """从环境变量加载开关状态"""
        self.ENABLE_OKX = os.getenv("MM_ENABLE_OKX", "true").lower() == "true"
        self.ENABLE_BINANCE = os.getenv("MM_ENABLE_BINANCE", "false").lower() == "true"
        self.ENABLE_DEXSCRENER = os.getenv("MM_ENABLE_DEXSCRENER", "true").lower() == "true"
        self.ENABLE_COINGECKO = os.getenv("MM_ENABLE_COINGECKO", "true").lower() == "true"
        self.ENABLE_COINGLASS = os.getenv("MM_ENABLE_COINGLASS", "false").lower() == "true"
        self.ENABLE_GECKOTERMINAL = os.getenv("MM_ENABLE_GECKOTERMINAL", "true").lower() == "true"
        self.ENABLE_KRAKEN = os.getenv("MM_ENABLE_KRAKEN", "false").lower() == "true"
    
    def get_status(self) -> dict:
        """获取所有数据源健康状态"""
        return {
            "OKX": self.ENABLE_OKX,
            "Binance": self.ENABLE_BINANCE,
            "DexScreener": self.ENABLE_DEXSCRENER,
            "CoinGecko": self.ENABLE_COINGECKO,
            "Coinglass": self.ENABLE_COINGLASS,
            "GeckoTerminal": self.ENABLE_GECKOTERMINAL,
            "Kraken": self.ENABLE_KRAKEN,
        }


# ============================================================================
# 数据源 API 配置
# ============================================================================

@dataclass
class DataSourceConfig:
    """数据源 API 配置"""
    
    # Coinglass API（资金费率、OI 数据）- 从环境变量或默认
    COINGLASS_BASE_URL: str = "https://open-api-v4.coinglass.com"
    COINGLASS_API_KEY: str = None
    
    # Binance 公开 API（已被封锁，改用其他源）
    BINANCE_BASE_URL: str = "https://api.binance.com"
    BINANCE_FUTURES_URL: str = "https://fapi.binance.com"
    
    # DexScreener API（DEX 流动性、买卖交易）
    DEXSCREENER_BASE_URL: str = "https://api.dexscreener.com"
    
    # CoinGecko API（价格、市值、供应量、BTC.D）
    COINGECKO_BASE_URL: str = "https://api.coingecko.com/api/v3"
    
    # OKX API
    OKX_BASE_URL: str = "https://www.okx.com/api/v5"
    
    # GeckoTerminal API
    GECKOTERMINAL_BASE_URL: str = "https://api.geckoterminal.com/api/v2"
    
    def __post_init__(self):
        """从环境变量加载敏感配置"""
        self.COINGLASS_API_KEY = os.getenv("MM_COINGLASS_API_KEY") or self.COINGLASS_API_KEY
        
        if not self.COINGLASS_API_KEY:
            pass  # 使用默认值或留空


# ============================================================================
# HTTP 请求配置
# ============================================================================

@dataclass
class HTTPConfig:
    """HTTP 请求通用配置"""
    
    # 重试配置
    MAX_RETRIES: int = 3
    RETRY_DELAY: float = 1.0  # 秒
    RETRY_BACKOFF: float = 2.0  # 指数退避倍率
    
    # 超时配置
    TIMEOUT: int = 15  # 秒
    
    # 请求头
    USER_AGENT: str = "MMTracker/1.0 (Market Maker Tracker; Python/3.11+)"
    
    # 请求间隔（避免触发限流）
    REQUEST_DELAY: float = 0.5  # 秒


# ============================================================================
# Signal 1: 整数关口横盘 3~7 天 或 >2周
# ============================================================================

@dataclass
class IntegerConsolidationConfig:
    """
    Signal 1: 价格在整数关口下方横盘
    
    支持两种模式：
    - 3-7天横盘（标准）
    - 14天+横盘（高确信度）
    
    逻辑：检测价格是否在整数关口（$0.01, $0.05, $0.1, $0.5, $1, $5...）下方横盘
    """
    
    # 整数关口层级
    PRICE_TIERS: list = None
    
    # 横盘天数范围（标准）
    CONSOLIDATION_DAYS_MIN: int = 3
    CONSOLIDATION_DAYS_MAX: int = 7
    
    # 长期横盘阈值（高确信度）
    CONSOLIDATION_DAYS_STRONG: int = 14
    
    # 距离整数关口的容忍范围（%）
    DISTANCE_PCT_MAX: float = 15.0
    
    def __post_init__(self):
        if self.PRICE_TIERS is None:
            self.PRICE_TIERS = [0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000, 5000]


# ============================================================================
# Signal 2: 资金费率转正
# ============================================================================

@dataclass
class FundingRateConfig:
    """
    Signal 2: 资金费率从负/零开始转正且持续上升
    
    逻辑：检测资金费率从负转正的转变，表明市场情绪从空转多
    """
    
    # 趋势判断
    TREND_DAYS: int = 3
    TREND_REQUIRED: bool = True
    
    # 容差：费率接近0时视为中性
    NEUTRAL_THRESHOLD: float = 0.0001


# ============================================================================
# Signal 3: OI 悄悄增加
# ============================================================================

@dataclass
class OIConfig:
    """
    Signal 3: OI 在价格横盘期间悄悄增加
    
    逻辑：持仓量(OI)大幅增长，但价格变化微小，说明资金在积累
    """
    
    # OI 变化率阈值
    OI_CHANGE_THRESHOLD: float = 10.0  # 10%
    
    # 价格变化阈值（横盘判断）
    PRICE_CHANGE_THRESHOLD: float = 10.0  # 10%
    
    # 乖离度阈值
    DIVERGENCE_THRESHOLD: float = 10.0


# ============================================================================
# Signal 4: 放量等级 (3x/5x/10x/20x)
# ============================================================================

@dataclass
class VolumeConfig:
    """
    Signal 4: 某一天出现放量但价格未大涨
    
    放量分档阈值：
    - 3x: 标准触发
    - 5x: 较大量
    - 10x: 大量
    - 20x+: 巨量（高确信度）
    
    逻辑：成交量突然放大，但价格涨幅有限，可能是庄家吸筹
    """
    
    # 放量倍数阈值（分档）
    VOLUME_RATIO_3X: float = 3.0
    VOLUME_RATIO_5X: float = 5.0
    VOLUME_RATIO_10X: float = 10.0
    VOLUME_RATIO_20X: float = 20.0
    
    # 向后兼容：默认触发阈值
    VOLUME_RATIO_THRESHOLD: float = 2.0  # 从3.0降低到2.0，更敏感
    
    # 价格涨幅阈值（量增价未涨）
    PRICE_CHANGE_THRESHOLD_3X: float = 8.0   # 3x时价格涨幅<8%
    PRICE_CHANGE_THRESHOLD_5X: float = 12.0   # 5x时价格涨幅<12%
    PRICE_CHANGE_THRESHOLD_10X: float = 15.0  # 10x时价格涨幅<15%
    
    # 均线周期
    MA_PERIOD: int = 20


# ============================================================================
# Signal 5: DEX 买压 >1.2
# ============================================================================

@dataclass
class DEXConfig:
    """
    Signal 5: DexScreener 买卖比 >1.2 且持续多日
    
    逻辑：DEX 买入交易显著多于卖出，说明散户/聪明钱在买入
    """
    
    # 买卖比阈值
    BUY_SELL_RATIO_THRESHOLD: float = 1.05  # 从1.2降低到1.05，更敏感
    
    # 最小流动性（排除低流动性代币）
    MIN_LIQUIDITY: float = 50000  # $50,000


# ============================================================================
# Signal 6: BTC.D 下降通道
# ============================================================================

@dataclass
class BTCDominanceConfig:
    """
    Signal 6: BTC.D 处于下降通道
    
    逻辑：BTC.D 下降利好山寨币
    """
    
    # 趋势判断参数
    HISTORY_DAYS: int = 14
    
    # 下降通道阈值（7日变化）
    CHANGE_THRESHOLD: float = -1.0  # -1%
    
    # 斜率阈值（每日变化）
    SLOPE_THRESHOLD: float = -0.1  # -0.1% / 天


# ============================================================================
# Signal 7: Binance 新合约
# ============================================================================

@dataclass
class FuturesContractConfig:
    """
    Signal 7: Binance 新增了该币的永续合约
    
    逻辑：检测目标币种是否已在 Binance 上线永续合约（30天内）
    """
    
    # 上线时间阈值（新上线合约更值得关注）
    NEW_CONTRACT_DAYS: int = 30


# ============================================================================
# 离场规则配置
# ============================================================================

@dataclass
class ExitRuleConfig:
    """
    离场铁律
    
    - Funding Rate > 0.5% → 减仓
    - Funding Rate > 1% → 清仓
    """
    
    # 减仓阈值
    REDUCE_THRESHOLD: float = 0.5  # 0.5%
    
    # 清仓阈值
    EXIT_THRESHOLD: float = 1.0  # 1.0%


# ============================================================================
# 入场规则配置
# ============================================================================

@dataclass
class EntryRuleConfig:
    """
    入场条件
    
    需要满足 3+ 个信号触发（降低阈值提高敏感度）
    """
    
    # 入场信号数阈值
    ENTRY_SIGNALS_MIN: int = 3  # 从5降低到3，更敏感


# ============================================================================
# 综合评分配置
# ============================================================================

@dataclass
class ScoringConfig:
    """
    综合评分配置
    """
    
    # 信号权重
    SIGNAL_WEIGHTS: dict = None
    
    # 信号名称映射
    SIGNAL_NAMES: dict = None
    
    def __post_init__(self):
        if self.SIGNAL_WEIGHTS is None:
            self.SIGNAL_WEIGHTS = {
                "signal_1_integer_consolidation": 1.5,   # 整数关口横盘(3-7天/>2周)
                "signal_2_funding_turn_positive": 2.0,   # 资金费率转正+上升趋势
                "signal_3_oi_accumulation": 1.5,          # OI暗中增加
                "signal_4_volume_spike": 1.0,            # 放量未大涨(3x/5x/10x/20x)
                "signal_5_dex_buy_pressure": 1.0,         # DEX买压>1.2(持续多日)
                "signal_6_btcd_downtrend": 1.0,           # BTC.D下降通道
                "signal_6b_btc_relative_strength": 1.0,   # BTC相对强度(托盘)
                "signal_7_new_futures": 1.5,              # Binance新合约
                "signal_8_wash_test": 1.5,                # 洗盘测试期(假拉升)
                "signal_9_social_sentiment": 0.5,         # 社媒情绪(占位)
            }
        
        if self.SIGNAL_NAMES is None:
            self.SIGNAL_NAMES = {
                "signal_1_integer_consolidation": "整数关口横盘(3-7天/>2周)",
                "signal_2_funding_turn_positive": "资金费率转正+上升趋势",
                "signal_3_oi_accumulation": "OI暗中增加",
                "signal_4_volume_spike": "放量未大涨(3x/5x/10x/20x)",
                "signal_5_dex_buy_pressure": "DEX买压>1.2(持续多日)",
                "signal_6_btcd_downtrend": "BTC.D下降通道",
                "signal_6b_btc_relative_strength": "BTC相对强度(托盘)",
                "signal_7_new_futures": "Binance新合约",
                "signal_8_wash_test": "洗盘测试期(假拉升)",
                "signal_9_social_sentiment": "社交媒体情绪(占位)",
            }


# ============================================================================
# 报告配置
# ============================================================================

@dataclass
class ReportConfig:
    """
    报告生成配置
    """
    
    # 报告输出目录
    REPORTS_DIR: str = "reports"
    
    # 报告文件名格式
    REPORT_FILENAME_FORMAT: str = "{symbol}_{timestamp}.md"


# ============================================================================
# 全局配置汇总
# ============================================================================

class MMTrackerConfig:
    """
    MMTracker 全局配置汇总
    
    使用方式：
        from config import MMTrackerConfig
        config = MMTrackerConfig()
        
        # 访问数据源开关
        config.datasource_switches.ENABLE_OKX
        
        # 访问各模块配置
        config.signals_signal_1.CONSOLIDATION_DAYS_MIN
    """
    
    # 数据源开关
    datasource_switches: DataSourceSwitches = DataSourceSwitches()
    
    # 数据源API配置
    datasource: DataSourceConfig = DataSourceConfig()
    
    # HTTP 请求
    http: HTTPConfig = HTTPConfig()
    
    # 信号配置
    signals_signal_1: IntegerConsolidationConfig = IntegerConsolidationConfig()
    signals_signal_2: FundingRateConfig = FundingRateConfig()
    signals_signal_3: OIConfig = OIConfig()
    signals_signal_4: VolumeConfig = VolumeConfig()
    signals_signal_5: DEXConfig = DEXConfig()
    signals_signal_6: BTCDominanceConfig = BTCDominanceConfig()
    signals_signal_7: FuturesContractConfig = FuturesContractConfig()
    
    # 离场规则
    exit_rules: ExitRuleConfig = ExitRuleConfig()
    
    # 入场规则
    entry_rules: EntryRuleConfig = EntryRuleConfig()
    
    # 评分
    scoring: ScoringConfig = ScoringConfig()
    
    # 报告
    report: ReportConfig = ReportConfig()
    
    def __init__(self):
        """从环境变量加载覆盖值"""
        self._load_env_overrides()
    
    def _load_env_overrides(self):
        """从环境变量加载配置覆盖"""
        
        # HTTP 配置
        if os.getenv("MM_HTTP_TIMEOUT"):
            self.http.TIMEOUT = int(os.getenv("MM_HTTP_TIMEOUT"))
        if os.getenv("MM_HTTP_MAX_RETRIES"):
            self.http.MAX_RETRIES = int(os.getenv("MM_HTTP_MAX_RETRIES"))
        
        # 信号配置
        if os.getenv("MM_VOLUME_RATIO"):
            self.signals_signal_4.VOLUME_RATIO_THRESHOLD = float(os.getenv("MM_VOLUME_RATIO"))
        if os.getenv("MM_BUY_SELL_RATIO"):
            self.signals_signal_5.BUY_SELL_RATIO_THRESHOLD = float(os.getenv("MM_BUY_SELL_RATIO"))
        if os.getenv("MM_MIN_LIQUIDITY"):
            self.signals_signal_5.MIN_LIQUIDITY = float(os.getenv("MM_MIN_LIQUIDITY"))
        
        # 离场规则
        if os.getenv("MM_EXIT_THRESHOLD"):
            self.exit_rules.EXIT_THRESHOLD = float(os.getenv("MM_EXIT_THRESHOLD"))
        if os.getenv("MM_REDUCE_THRESHOLD"):
            self.exit_rules.REDUCE_THRESHOLD = float(os.getenv("MM_REDUCE_THRESHOLD"))
        
        # 报告配置
        if os.getenv("MM_REPORTS_DIR"):
            self.report.REPORTS_DIR = os.getenv("MM_REPORTS_DIR")


# 全局配置实例
config = MMTrackerConfig()
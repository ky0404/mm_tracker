"""
历史启动币种样本集
用于回测和信号校准

记录格式:
- symbol: 代币符号
- name: 代币名称  
- launch_date: 首次上线交易所日期
- pump_start: 主力开始拉升的日期
- pump_end: 拉升结束日期
- pump_multiplier: 涨幅倍数
- notes: 备注

样本来源：2024-2025年热门币种的历史走势
"""

SAMPLE_COINS = [
    {
        "symbol": "PEPE",
        "name": "Pepe",
        "launch_date": "2023-04-17",
        "pump_start": "2024-03-15",
        "pump_end": "2024-05-27",
        "pump_multiplier": 30.0,
        "notes": "MEME币龙头，2024年3-5月暴涨30倍"
    },
    {
        "symbol": "WIF",
        "name": "dogwifhat",
        "launch_date": "2023-11-20",
        "pump_start": "2024-01-01",
        "pump_end": "2024-03-04",
        "pump_multiplier": 25.0,
        "notes": "Solana生态MEME，2024年初暴涨"
    },
    {
        "symbol": "BONK",
        "name": "Bonk",
        "launch_date": "2022-12-25",
        "pump_start": "2024-01-01",
        "pump_end": "2024-02-15",
        "pump_multiplier": 10.0,
        "notes": "Solana生态MEME，2024年初启动"
    },
    {
        "symbol": "MEME",
        "name": "Memecoin",
        "launch_date": "2023-09-12",
        "pump_start": "2024-03-01",
        "pump_end": "2024-04-15",
        "pump_multiplier": 8.0,
        "notes": "2024年3-4月涨8倍"
    },
    {
        "symbol": "ORDI",
        "name": "ORDI",
        "launch_date": "2023-10-19",
        "pump_start": "2023-11-25",
        "pump_end": "2023-12-10",
        "pump_multiplier": 5.0,
        "notes": "BTC铭文概念，2023年底启动"
    },
    {
        "symbol": "SATS",
        "name": "SATS",
        "launch_date": "2023-10-23",
        "pump_start": "2023-11-20",
        "pump_end": "2023-12-08",
        "pump_multiplier": 4.0,
        "notes": "BTC铭文概念"
    },
    {
        "symbol": "LAB",
        "name": "Labyrinth",
        "launch_date": "2025-01-15",
        "pump_start": "2025-02-15",
        "pump_end": "2025-03-10",
        "pump_multiplier": 15.0,
        "notes": "2025年初新币，2月启动"
    },
    {
        "symbol": "ALLO",
        "name": "Allo",
        "launch_date": "2024-10-20",
        "pump_start": "2024-11-20",
        "pump_end": "2024-12-15",
        "pump_multiplier": 6.0,
        "notes": "2024年底启动"
    },
    {
        "symbol": "GOAT",
        "name": "Goatseus Maximus",
        "launch_date": "2024-09-15",
        "pump_start": "2024-10-01",
        "pump_end": "2024-10-25",
        "pump_multiplier": 12.0,
        "notes": "2024年10月MEME币"
    },
    {
        "symbol": "VINE",
        "name": "Vine",
        "launch_date": "2024-10-24",
        "pump_start": "2024-10-25",
        "pump_end": "2024-11-01",
        "pump_multiplier": 8.0,
        "notes": "病毒式传播MEME币"
    },
{
        "symbol": "AERO",
        "name": "Aerodrome",
        "launch_date": "2024-06-28",
        "pump_start": "2024-10-15",
        "pump_end": "2024-12-01",
        "pump_multiplier": 5.0,
        "notes": "Base生态DeFi，2024Q4启动"
    },
    {
        "symbol": "NEIRO",
        "name": "Neiro",
        "launch_date": "2024-07-15",
        "pump_start": "2024-10-25",
        "pump_end": "2024-11-10",
        "pump_multiplier": 25.0,
        "notes": "2024年10月最疯狂MEME之一"
    },
    {
        "symbol": "COOKIE",
        "name": "Cookie",
        "launch_date": "2024-10-31",
        "pump_start": "2024-11-01",
        "pump_end": "2024-11-12",
        "pump_multiplier": 15.0,
        "notes": "2024年11月启动"
    },
    {
        "symbol": "GIGA",
        "name": "Giga",
        "launch_date": "2024-10-03",
        "pump_start": "2024-11-01",
        "pump_end": "2024-11-15",
        "pump_multiplier": 12.0,
        "notes": "11月启动MEME"
    },
    {
        "symbol": "FWOG",
        "name": "Fwog",
        "launch_date": "2024-09-03",
        "pump_start": "2024-10-01",
        "pump_end": "2024-10-25",
        "pump_multiplier": 20.0,
        "notes": "10月MEME启动"
    },
    {
        "symbol": "RETARDIO",
        "name": "Retardio",
        "launch_date": "2024-10-22",
        "pump_start": "2024-10-25",
        "pump_end": "2024-11-01",
        "pump_multiplier": 18.0,
        "notes": "争议MEME"
    },
    {
        "symbol": "POPCAT",
        "name": "Popcat",
        "launch_date": "2024-04-12",
        "pump_start": "2024-10-01",
        "pump_end": "2024-11-01",
        "pump_multiplier": 15.0,
        "notes": "Solana MEME"
    },
    {
        "symbol": "BODEN",
        "name": "Jeo Boden",
        "launch_date": "2024-02-10",
        "pump_start": "2024-03-01",
        "pump_end": "2024-04-15",
        "pump_multiplier": 30.0,
        "notes": "Base生态MEME"
    },
    {
        "symbol": "SC",
        "name": "Sui",
        "launch_date": "2023-05-03",
        "pump_start": "2024-03-01",
        "pump_end": "2024-03-15",
        "pump_multiplier": 4.0,
        "notes": "Sui生态币"
    },
    {
        "symbol": "IMX",
        "name": "Immutable",
        "launch_date": "2021-11-01",
        "pump_start": "2024-01-15",
        "pump_end": "2024-03-15",
        "pump_multiplier": 5.0,
        "notes": "2024年初启动"
    },
    {
        "symbol": "TIA",
        "name": "Celestia",
        "launch_date": "2023-10-31",
        "pump_start": "2023-12-01",
        "pump_end": "2024-01-15",
        "pump_multiplier": 8.0,
        "notes": "2023年底启动"
    },
    {
        "symbol": "SEI",
        "name": "Sei",
        "launch_date": "2022-10-01",
        "pump_start": "2024-01-10",
        "pump_end": "2024-02-15",
        "pump_multiplier": 3.5,
        "notes": "2024年初启动"
    },
]

# 负样本：未暴涨的币种（用于回测精确度）
NEGATIVE_SAMPLES = [
    {"symbol": "USDT", "name": "Tether", "notes": "稳定币"},
    {"symbol": "USDC", "name": "USD Coin", "notes": "稳定币"},
    {"symbol": "DAI", "name": "Dai", "notes": "稳定币"},
    {"symbol": "TRX", "name": "TRON", "notes": "长期横盘"},
    {"symbol": "XRP", "name": "Ripple", "notes": "长期横盘"},
    {"symbol": "ADA", "name": "Cardano", "notes": "长期横盘"},
    {"symbol": "DOT", "name": "Polkadot", "notes": "长期横盘"},
    {"symbol": "MATIC", "name": "Polygon", "notes": "长期横盘"},
    {"symbol": "LINK", "name": "Chainlink", "notes": "区间震荡"},
    {"symbol": "ATOM", "name": "Cosmos", "notes": "区间震荡"},
]

# 合并正负样本
ALL_SAMPLES = SAMPLE_COINS + [{"symbol": s["symbol"], "is_real_launch": False} for s in NEGATIVE_SAMPLES]

# 信号触发记录模板
SIGNAL_RECORD_TEMPLATE = {
    "symbol": "",
    "date": "",
    "signals": {},  # 各信号触发状态
    "price_change_7d": 0.0,
    "price_change_30d": 0.0,
    "actual_pump": False,  # 后续是否真的启动
}

# 样本币时间窗口配置
SAMPLE_WINDOWS = {
    "before_pump_7d": 7,   # 启动前7天
    "before_pump_14d": 14, # 启动前14天
    "before_pump_30d": 30, # 启动前30天
}
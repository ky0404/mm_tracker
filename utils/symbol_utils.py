"""
MMTracker Symbol 统一工具
解决不同模块 Symbol 格式不一致的问题:
- 市场扫描返回: "BTC", "ETH" (无后缀)
- OKX API 需要: "BTC-USDT", "BTC-USDT-SWAP"
- 内部存储混用: "BTC", "OP", "OP-USDT"
"""

def to_okx_symbol(symbol: str) -> str:
    """
    转换为 OKX 合约格式
    "BTC" -> "BTC-USDT-SWAP"
    "BTC-USDT" -> "BTC-USDT-SWAP"
    "BTC-USDT-SWAP" -> "BTC-USDT-SWAP"
    """
    if not symbol:
        return ""
    
    symbol = symbol.strip().upper()
    
    if symbol.endswith("-USDT-SWAP"):
        return symbol
    elif symbol.endswith("-USDT"):
        return f"{symbol}-SWAP"
    else:
        return f"{symbol}-USDT-SWAP"


def to_okx_spot(symbol: str) -> str:
    """
    转换为 OKX 现货格式
    "BTC" -> "BTC-USDT"
    "BTC-USDT" -> "BTC-USDT"
    """
    if not symbol:
        return ""
    
    symbol = symbol.strip().upper()
    
    if symbol.endswith("-USDT"):
        return symbol
    else:
        return f"{symbol}-USDT"


def to_simple(symbol: str) -> str:
    """
    转换为简洁格式 (内部使用)
    "BTC-USDT-SWAP" -> "BTC"
    "BTC-USDT" -> "BTC"
    "BTC" -> "BTC"
    """
    if not symbol:
        return ""
    
    symbol = symbol.strip().upper()
    
    if "-USDT-SWAP" in symbol:
        return symbol.replace("-USDT-SWAP", "")
    elif "-USDT" in symbol:
        return symbol.replace("-USDT", "")
    else:
        return symbol


def normalize(symbol: str) -> str:
    """
    标准化: 确保格式统一为简洁格式
    等同于 to_simple
    """
    return to_simple(symbol)


def is_valid(symbol: str) -> bool:
    """
    检查是否是有效的代币符号
    """
    if not symbol:
        return False
    
    s = symbol.strip().upper()
    
    if s in ["USDT", "USD", "BTC", "ETH", "OKB"]:
        return False
    
    return len(s) > 0 and s.isalpha() or ("-" in s and len(s.split("-")[0]) > 0)


def format_for_display(symbol: str) -> str:
    """
    格式化用于显示
    "BTC-USDT-SWAP" -> "BTC"
    """
    return to_simple(symbol)
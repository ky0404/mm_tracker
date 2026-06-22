#!/usr/bin/env python3
"""
交易器工厂 - 统一接口
支持 OKX 现货/合约 和 Binance 合约
"""
import logging
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger(__name__)


class Exchange(Enum):
    OKX_SPOT = "okx_spot"
    OKX_SWAP = "okx_swap"
    BINANCE_FUTURES = "binance_futures"


def create_trader(
    exchange: str = "okx",
    api_key: str = "",
    api_secret: str = "",
    passphrase: str = "",
    testnet: bool = True,
    use_spot: bool = True
) -> Any:
    """
    创建交易器实例
    
    Args:
        exchange: 交易所 "okx" 或 "binance"
        api_key: API Key
        api_secret: API Secret
        passphrase: OKX 密码 (仅 OKX 需要)
        testnet: 是否使用测试网
        use_spot: OKX 是否使用现货 (True=现货, False=合约)
    
    Returns:
        交易器实例
    """
    exchange = exchange.lower()
    
    if exchange == "okx":
        from trading.okx_testnet import OKXTestnetTrader
        
        logger.info(f"[TraderFactory] 创建 OKX 交易器: {'现货' if use_spot else '合约'}")
        return OKXTestnetTrader(
            api_key=api_key,
            api_secret=api_secret,
            passphrase=passphrase,
            testnet=testnet,
            use_spot=use_spot
        )
    
    elif exchange == "binance":
        from trading.binance_trader import BinanceFuturesTrader
        
        logger.info(f"[TraderFactory] 创建 Binance 合约交易器: {'测试网' if testnet else '真实'}")
        return BinanceFuturesTrader(
            api_key=api_key,
            api_secret=api_secret,
            testnet=testnet
        )
    
    else:
        raise ValueError(f"不支持的交易所: {exchange}")


class UnifiedTrader:
    """
    统一交易器接口
    屏蔽 OKX/Binance 差异，提供统一 API
    """
    
    def __init__(self, trader: Any, exchange: str):
        self.trader = trader
        self.exchange = exchange
    
    def get_balance(self) -> Dict[str, Any]:
        """获取余额"""
        return self.trader.get_balance()
    
    def get_positions(self) -> list:
        """获取持仓"""
        if hasattr(self.trader, 'get_positions'):
            return self.trader.get_positions()
        return []
    
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float = None,
        order_type: str = "market"
    ) -> Dict[str, Any]:
        """下单"""
        return self.trader.place_order(symbol, side, quantity, price, order_type)
    
    def close_position(self, symbol: str, quantity: float = None) -> Dict[str, Any]:
        """平仓"""
        return self.trader.close_position(symbol, quantity)
    
    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """获取实时价格"""
        if hasattr(self.trader, 'get_ticker'):
            return self.trader.get_ticker(symbol)
        return None
    
    def get_open_orders(self, symbol: str = None) -> list:
        """获取未完成订单"""
        if hasattr(self.trader, 'get_open_orders'):
            return self.trader.get_open_orders(symbol)
        return []


def create_unified_trader(
    exchange: str = "okx",
    api_key: str = "",
    api_secret: str = "",
    passphrase: str = "",
    testnet: bool = True,
    use_spot: bool = True
) -> UnifiedTrader:
    """创建统一交易器"""
    trader = create_trader(exchange, api_key, api_secret, passphrase, testnet, use_spot)
    return UnifiedTrader(trader, exchange)


# 便捷函数
def get_okx_trader(api_key: str, api_secret: str, passphrase: str, testnet: bool = True) -> Any:
    """创建 OKX 交易器 (便捷函数)"""
    return create_trader("okx", api_key, api_secret, passphrase, testnet, use_spot=True)


def get_binance_trader(api_key: str, api_secret: str, testnet: bool = True) -> Any:
    """创建 Binance 交易器 (便捷函数)"""
    return create_trader("binance", api_key, api_secret, "", testnet)
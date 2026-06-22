#!/usr/bin/env python3
"""
Binance 合约交易器
架构已就绪，支持合约交易
"""
import requests
import hmac
import hashlib
import base64
import json
import logging
import math
from datetime import datetime
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def normalize_symbol(symbol: str) -> str:
    """
    Binance 合约 symbol 规范化
    输入: "BTC" 或 "BTCUSDT" 或 "BTC-USDT"
    输出: "btcusdt" (Binance 小写格式)
    """
    s = symbol.strip().upper().replace("-", "").replace("USDT", "")
    return f"{s.lower()}usdt"


def round_to_precision(value: float, precision: int = 4) -> str:
    """格式化数量到指定精度"""
    return f"{value:.{precision}f}"


class BinanceFuturesTrader:
    """
    Binance 合约交易器
    支持: 永续合约 (USDT-M)
    """
    
    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        testnet: bool = True,
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        
        if testnet:
            self.base_url = "https://testnet.binance.vision"
        else:
            self.base_url = "https://fapi.binance.com"
        
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        
        logger.info(f"[BinanceTrader] 初始化: {'测试网' if testnet else '真实账户'}")
    
    def _sign(self, params: str) -> str:
        """生成签名"""
        return hmac.new(
            self.api_secret.encode('utf-8'),
            params.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
    
    def _get_headers(self, method: str, route: str, params: str = "") -> Dict:
        """获取请求头"""
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_secret:
            timestamp = int(datetime.now().timestamp() * 1000)
            params_with_ts = f"{params}&timestamp={timestamp}" if params else f"timestamp={timestamp}"
            signature = self._sign(params_with_ts)
            
            headers["X-MBX-APIKEY"] = self.api_key
            return headers, f"{params_with_ts}&signature={signature}"
        return headers, params
    
    def get_balance(self) -> Optional[Dict[str, Any]]:
        """获取账户余额 (USDT)"""
        route = "/fapi/v3/account"
        headers, query = self._get_headers("GET", route)
        
        try:
            resp = self.session.get(f"{self.base_url}{route}?{query}", headers=headers, timeout=10)
            data = resp.json()
            
            if data.get('code') is None or data.get('code') == 0:
                for asset in data.get('assets', []):
                    if asset.get('asset') == 'USDT':
                        return {
                            'total': float(asset.get('totalMarginBalance', 0)),
                            'available': float(asset.get('availableBalance', 0)),
                            'locked': float(asset.get('marginBalance', 0)) - float(asset.get('availableBalance', 0))
                        }
            else:
                logger.error(f"[Binance] 获取余额失败: {data}")
        except Exception as e:
            logger.error(f"[Binance] 获取余额异常: {e}")
        return None
    
    def get_positions(self) -> List[Dict[str, Any]]:
        """获取持仓"""
        route = "/fapi/v3/positionRisk"
        headers, query = self._get_headers("GET", route)
        
        try:
            resp = self.session.get(f"{self.base_url}{route}?{query}", headers=headers, timeout=10)
            data = resp.json()
            
            positions = []
            if isinstance(data, list):
                for pos in data:
                    if float(pos.get('positionAmt', 0)) != 0:
                        positions.append({
                            'symbol': pos.get('symbol', ''),
                            'amount': abs(float(pos.get('positionAmt', 0))),
                            'entry_price': float(pos.get('entryPrice', 0)),
                            'unrealized_pnl': float(pos.get('unrealizedProfit', 0)),
                            'leverage': int(pos.get('leverage', 1)),
                            'side': 'long' if float(pos.get('positionAmt', 0)) > 0 else 'short'
                        })
            return positions
        except Exception as e:
            logger.error(f"[Binance] 获取持仓异常: {e}")
        return []
    
    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: Optional[float] = None,
        order_type: str = "MARKET",
        leverage: int = 3
    ) -> Dict[str, Any]:
        """
        下单
        symbol: 代币符号 (如 "BTCUSDT")
        side: "BUY" 或 "SELL"
        quantity: 数量
        price: 价格 (市价单可为 None)
        order_type: "MARKET" 或 "LIMIT"
        """
        symbol = normalize_symbol(symbol)
        
        route = "/fapi/v1/order"
        params = f"symbol={symbol}&side={side.upper()}&type={order_type.upper()}&quantity={round_to_precision(quantity)}"
        
        if order_type.upper() == "LIMIT" and price:
            params += f"&price={price}&timeInForce=GTC"
        
        headers, signed_params = self._get_headers("POST", route, params)
        
        try:
            # 设置杠杆
            self._set_leverage(symbol, leverage)
            
            resp = self.session.post(f"{self.base_url}{route}?{signed_params}", headers=headers, timeout=10)
            data = resp.json()
            
            if data.get('code') is None or data.get('code') == 0:
                return {'code': '0', 'msg': 'success', 'data': data}
            else:
                logger.error(f"[Binance] 下单失败: {data}")
                return {'code': '1', 'msg': data.get('msg', 'error')}
        except Exception as e:
            logger.error(f"[Binance] 下单异常: {e}")
            return {'code': '1', 'msg': str(e)}
    
    def _set_leverage(self, symbol: str, leverage: int):
        """设置杠杆"""
        route = "/fapi/v1/leverage"
        params = f"symbol={normalize_symbol(symbol)}&leverage={leverage}"
        headers, signed_params = self._get_headers("POST", route, params)
        
        try:
            self.session.post(f"{self.base_url}{route}?{signed_params}", headers=headers, timeout=5)
        except:
            pass
    
    def close_position(self, symbol: str, quantity: Optional[float] = None) -> Dict[str, Any]:
        """
        平仓
        如果 quantity 为 None，则全部平掉
        """
        symbol = normalize_symbol(symbol)
        
        positions = self.get_positions()
        pos = next((p for p in positions if p['symbol'] == symbol.upper()), None)
        
        if not pos:
            return {'code': '0', 'msg': 'no_position'}
        
        qty = quantity or pos['amount']
        side = "SELL" if pos['side'] == 'long' else "BUY"
        
        return self.place_order(symbol, side, qty, None, "MARKET")
    
    def get_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取实时价格"""
        symbol = normalize_symbol(symbol)
        route = "/fapi/v1/ticker/price"
        
        try:
            resp = self.session.get(f"{self.base_url}{route}?symbol={symbol}", timeout=5)
            data = resp.json()
            
            if data.get('code') is None or data.get('code') == 0:
                return {
                    'price': float(data.get('price', 0)),
                    'symbol': symbol
                }
        except Exception as e:
            logger.error(f"[Binance] 获取价格异常: {e}")
        return None
    
    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """获取未完成订单"""
        route = "/fapi/v1/openOrders"
        headers, query = self._get_headers("GET", route)
        
        try:
            if symbol:
                query += f"&symbol={normalize_symbol(symbol)}"
            resp = self.session.get(f"{self.base_url}{route}?{query}", headers=headers, timeout=10)
            return resp.json()
        except Exception as e:
            logger.error(f"[Binance] 获取订单异常: {e}")
        return []
    
    def cancel_order(self, symbol: str, order_id: int) -> Dict:
        """取消订单"""
        route = "/fapi/v1/order"
        params = f"symbol={normalize_symbol(symbol)}&orderId={order_id}"
        headers, signed_params = self._get_headers("DELETE", route, params)
        
        try:
            resp = self.session.delete(f"{self.base_url}{route}?{signed_params}", headers=headers, timeout=10)
            return resp.json()
        except Exception as e:
            return {'code': '1', 'msg': str(e)}


def create_binance_trader(
    api_key: str = "",
    api_secret: str = "",
    testnet: bool = True
) -> BinanceFuturesTrader:
    """工厂函数创建 Binance 交易器"""
    return BinanceFuturesTrader(api_key, api_secret, testnet)
import requests
import time
import hmac
import base64
import json
import logging
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class OKXTestnetTrader:
    """
    OKX 交易器 (支持真实交易和模拟交易)
    - 真实交易: https://www.okx.com
    - 模拟交易: https://openapi.okx.com + x-simulated-trading: 1
    """

    def __init__(self, api_key: str, api_secret: str, passphrase: str, testnet: bool = True, use_spot: bool = True):
        """
        OKX 交易器 (支持真实交易和模拟交易)
        - 真实交易: https://www.okx.com
        - 模拟交易: https://openapi.okx.com + x-simulated-trading: 1
        
        Args:
            api_key: API Key
            api_secret: API Secret
            passphrase: Passphrase
            testnet: 是否使用模拟盘
            use_spot: 是否使用现货交易 (永续合约可能需要额外权限)
        """
        if testnet:
            self.base_url = "https://openapi.okx.com"
            self.is_simulation = True
        else:
            self.base_url = "https://www.okx.com"
            self.is_simulation = False
        
        # 如果是测试网但想用合约，需要把use_spot设为False
        if not testnet:
            self.use_spot = False  # 真实账户用合约
        
        self.use_spot = use_spot
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase

    def _generate_signature(self, timestamp: str, method: str, route: str, body: str = "") -> str:
        secret_key = self.api_secret.encode('utf-8')
        message = f"{timestamp}{method}{route}{body}".encode('utf-8')
        signed = hmac.new(secret_key, message, digestmod='sha256').digest()
        return base64.b64encode(signed).decode('utf-8')

    def _get_headers(self, method: str, route: str, body: str = "") -> Dict[str, str]:
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        signature = self._generate_signature(timestamp, method, route, body)

        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        
        if self.is_simulation:
            headers["x-simulated-trading"] = "1"
        
        return headers

    def place_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: Optional[float] = None,
        order_type: str = "limit",
    ) -> Dict[str, Any]:
        # 转换 symbol 格式: SWAP-BTC-USDT -> BTC-USDT-SWAP
        inst_id = self._convert_symbol(symbol)
        
        route = "/api/v5/trade/order"
        body_dict = {
            "instId": inst_id,
            "tdMode": "cash",  # 现货模式
            "side": side,
            "sz": str(size),
            "ordType": order_type,
        }
        if price:
            body_dict["px"] = str(price)
        body = json.dumps(body_dict)
        headers = self._get_headers("POST", route, body)

        resp = requests.post(f"{self.base_url}{route}", json=body_dict, headers=headers)
        return resp.json()

    def _convert_symbol(self, symbol: str) -> str:
        """转换 symbol 格式"""
        # 如果使用现货，直接返回
        if self.use_spot:
            # SWAP-BTC-USDT -> BTC-USDT (现货)
            if symbol.startswith("SWAP-"):
                parts = symbol.replace("SWAP-", "").split("-")
                if len(parts) == 2:
                    return f"{parts[0]}-{parts[1]}"
        else:
            # SWAP-BTC-USDT -> BTC-USDT-SWAP (永续)
            if symbol.startswith("SWAP-"):
                parts = symbol.replace("SWAP-", "").split("-")
                if len(parts) == 2:
                    return f"{parts[0]}-{parts[1]}-SWAP"
        return symbol

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        inst_id = self._convert_symbol(symbol)
        route = "/api/v5/trade/cancel-order"
        body_dict = {
            "instId": inst_id,
            "ordId": order_id,
        }
        body = json.dumps(body_dict)
        headers = self._get_headers("POST", route, body)

        resp = requests.post(f"{self.base_url}{route}", json=body_dict, headers=headers)
        return resp.json()

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        inst_id = self._convert_symbol(symbol)
        route = f"/api/v5/account/positions?instId={inst_id}"
        headers = self._get_headers("GET", route)

        resp = requests.get(f"{self.base_url}{route}", headers=headers)
        data = resp.json()
        if data.get("code") == "0" and data.get("data"):
            return data["data"][0]
        return None

    def close_position(self, symbol: str) -> Dict[str, Any]:
        # 现货模式：查询余额并卖出
        inst_id = self._convert_symbol(symbol)
        
        # 获取币种代码
        base_ccy = inst_id.split("-")[0] if "-" in inst_id else symbol
        
        # 查询余额
        balance = self.get_balance()
        if not balance:
            return {"code": "404", "msg": "Failed to get balance"}
        
        # 查找该币种余额
        size = 0
        for detail in balance.get("details", []):
            if detail.get("ccy") == base_ccy:
                size = float(detail.get("availBal", 0))
                break
        
        if size <= 0:
            return {"code": "404", "msg": f"No position for {base_ccy}"}
        
        # 市价卖出
        logger.info(f"[平仓] 卖出 {base_ccy} 数量: {size}")
        return self.place_order(symbol, "sell", size, None, "market")

    def get_open_orders(self, symbol: str) -> list:
        inst_id = self._convert_symbol(symbol)
        route = f"/api/v5/trade/orders-pending?instId={inst_id}"
        headers = self._get_headers("GET", route)

        resp = requests.get(f"{self.base_url}{route}", headers=headers)
        data = resp.json()
        if data.get("code") == "0":
            return data.get("data", [])
        return []

    def get_balance(self) -> Optional[Dict[str, Any]]:
        route = "/api/v5/account/balance"
        headers = self._get_headers("GET", route)

        resp = requests.get(f"{self.base_url}{route}", headers=headers)
        data = resp.json()
        if data.get("code") == "0" and data.get("data"):
            return data["data"][0]
        return None
    
    def get_order_info(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        inst_id = self._convert_symbol(symbol)
        route = f"/api/v5/trade/order?instId={inst_id}&ordId={order_id}"
        headers = self._get_headers("GET", route)
        
        resp = requests.get(f"{self.base_url}{route}", headers=headers)
        data = resp.json()
        if data.get("code") == "0" and data.get("data"):
            return data["data"][0]
        return None
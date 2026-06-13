import time
import json
from typing import Optional, Dict, Any
from datetime import datetime


class MockOKXTrader:
    """
    模拟交易器 - 用于测试和模拟交易
    不需要真实API Key，完全本地模拟
    """

    def __init__(self, initial_balance: float = 10000.0):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.positions = {}
        self.orders = []
        self.order_id_counter = 1000

    def place_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: Optional[float] = None,
        order_type: str = "limit",
    ) -> Dict[str, Any]:
        order_id = str(self.order_id_counter)
        self.order_id_counter += 1

        # 转换 symbol: SWAP-BTC-USDT -> BTC
        token = symbol.replace("SWAP-", "").replace("-USDT", "").replace("-SWAP", "")

        order = {
            "code": "0",
            "data": [{
                "ordId": order_id,
                "instId": symbol,
                "side": side,
                "sz": str(size),
                "px": str(price) if price else "0",
                "ordType": order_type,
                "state": "filled",
                "fillPx": str(price) if price else "0",
                "fillSz": str(size),
            }],
            "msg": "",
        }

        if price is None or order_type == "market":
            price = self._get_mock_price(token)
            order["data"][0]["fillPx"] = str(price)

        position_key = f"SWAP-{token}-USDT"
        if position_key not in self.positions:
            self.positions[position_key] = {
                "pos": 0.0,
                "avgPx": 0.0,
                "lastPx": price,
            }

        pos = self.positions[position_key]
        if side == "buy":
            new_pos = pos["pos"] + size
            pos["avgPx"] = (pos["avgPx"] * pos["pos"] + price * size) / new_pos if new_pos > 0 else 0
            pos["pos"] = new_pos
            self.balance -= price * size
        else:
            pos["pos"] -= size
            self.balance += price * size

        pos["lastPx"] = price

        print(f"[模拟下单] {token} {side} {size} @ {price}, 余额: {self.balance:.2f}")

        return order

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        return {"code": "0", "data": [], "msg": ""}

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        if symbol in self.positions:
            pos = self.positions[symbol]
            if pos["pos"] != 0:
                return pos
        return None

    def close_position(self, symbol: str) -> Dict[str, Any]:
        pos = self.get_position(symbol)
        if not pos or pos.get("pos", 0) == 0:
            return {"code": "404", "msg": "No position"}

        size = abs(pos["pos"])
        side = "sell" if pos["pos"] > 0 else "buy"
        price = self._get_mock_price(symbol.replace("SWAP-", "").replace("-USDT", ""))

        result = self.place_order(symbol, side, size, price)
        
        token = symbol.replace("SWAP-", "").replace("-USDT", "")
        print(f"[模拟平仓] {token} {side} {size} @ {price}")

        return result

    def get_open_orders(self, symbol: str) -> list:
        return []

    def get_balance(self) -> Dict[str, Any]:
        return {
            "totalEq": str(self.balance),
            "availEq": str(self.balance),
        }

    def get_order_info(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        return None

    def _get_mock_price(self, token: str) -> float:
        mock_prices = {
            "BTC": 63000.0,
            "ETH": 1660.0,
            "SOL": 145.0,
            "PEPE": 0.00001,
            "WIF": 3.2,
            "BONK": 0.000025,
        }
        return mock_prices.get(token, 1.0)

    def get_status(self) -> Dict[str, Any]:
        total_value = self.balance
        for symbol, pos in self.positions.items():
            if pos["pos"] != 0:
                total_value += pos["pos"] * pos["lastPx"]

        return {
            "balance": self.balance,
            "total_value": total_value,
            "pnl": total_value - self.initial_balance,
            "pnl_pct": (total_value - self.initial_balance) / self.initial_balance * 100,
            "positions": {k: v for k, v in self.positions.items() if v["pos"] != 0},
        }


def create_trader(
    api_key: str = None,
    api_secret: str = None,
    passphrase: str = None,
    sim_mode: bool = True,
    initial_balance: float = 10000.0,
):
    if sim_mode or not api_key:
        print(f"[Trader] 模拟模式启动，初始资金: ${initial_balance}")
        return MockOKXTrader(initial_balance)
    else:
        from trading.okx_testnet import OKXTestnetTrader
        print(f"[Trader] 真实交易模式启动 (模拟盘: {api_key is not None})")
        return OKXTestnetTrader(api_key, api_secret, passphrase, testnet=True)


if __name__ == "__main__":
    trader = create_trader(sim_mode=True)
    
    print("\n=== 模拟交易测试 ===")
    
    trader.place_order("SWAP-BTC-USDT", "buy", 0.1, 63000)
    trader.place_order("SWAP-ETH-USDT", "buy", 1.0, 1660)
    
    status = trader.get_status()
    print(f"\n状态: {status}")
    
    trader.close_position("SWAP-BTC-USDT")
    
    status = trader.get_status()
    print(f"\n平仓后: {status}")
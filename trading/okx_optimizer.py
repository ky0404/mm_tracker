"""
OKX 优化版 API 客户端
专注高价值信号: S12(多空比), S2(资金费率), S13(主动成交量), S3(OI)
"""

import requests
import time
import hmac
import base64
import json
from datetime import datetime
from typing import Optional, Dict, Any, List


class OKXOptimizer:
    """
    OKX 优化版交易器 - 专注高胜率信号
    """

    def __init__(self, api_key: str = "", api_secret: str = "", passphrase: str = "", testnet: bool = True):
        if testnet:
            self.base_url = "https://openapi.okx.com"
            self.is_simulation = True
        else:
            self.base_url = "https://www.okx.com"
            self.is_simulation = False

        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.session = requests.Session()

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

    def _public_request(self, method: str, route: str, params: Dict = None) -> Dict:
        """公开API无需签名"""
        url = f"{self.base_url}{route}"
        if params:
            query = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query}"

        resp = self.session.get(url, timeout=10)
        data = resp.json()

        code = data.get("code", "1")
        if code == "0" or code == 0:
            return {"code": 0, "data": data.get("data", [])}
        return {"code": str(code), "msg": data.get("msg", "error"), "data": []}

    def _signed_request(self, method: str, route: str, params: Dict = None) -> Dict:
        """私有API需要签名"""
        body = json.dumps(params) if params else ""
        headers = self._get_headers(method, route, body)

        url = f"{self.base_url}{route}"
        if method == "GET" and params:
            query = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query}"
            body = ""

        if method == "GET":
            resp = self.session.get(url, headers=headers, timeout=10)
        else:
            resp = self.session.post(url, json=params, headers=headers, timeout=10)

        data = resp.json()
        if data.get("code") == "0":
            return {"code": 0, "data": data.get("data", [])}
        return {"code": data.get("code", "1"), "msg": data.get("msg", "error"), "data": []}

    # ==================== 高价值信号 API ====================

    def get_long_short_ratio(self, symbol: str, period: str = "1H") -> Dict:
        """
        S12: 获取多空比 (Long/Short Account Ratio)
        这是命中率最高的信号 (70%)
        """
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        route = "/api/v5/rubik/stat/contracts/long-short-account-ratio"
        params = {
            "instId": inst_id,
            "ccy": symbol.upper(),
            "period": period,  # 5m, 1H, 1D
        }

        result = self._public_request("GET", route, params)

        if result.get("code") != 0:
            return {"error": result.get("msg", "no data"), "triggered": False}

        # API 返回: [[ts, ratio], ...]
        # ratio = long/short (例如 1.5 表示多头是空头的 1.5 倍)
        data_points = result.get("data", [])
        if not data_points:
            return {"error": "no data points", "triggered": False}

        # 取最新的 ratio
        latest = data_points[0]
        ratio = float(latest[1])

        # 转换为百分比: long% = ratio / (1 + ratio) * 100
        long_ratio = ratio / (1 + ratio) * 100
        short_ratio = 100 - long_ratio

        # 触发条件: long_ratio > 70%
        triggered = long_ratio > 70

        return {
            "signal": "signal_12_long_short_ratio",
            "triggered": triggered,
            "long_ratio": long_ratio,
            "short_ratio": short_ratio,
            "long_short_ratio": ratio,
            "detail": f"多空比: {long_ratio:.1f}% / {short_ratio:.1f}%, 倍数: {ratio:.2f}x",
            "strength": "strong" if long_ratio > 80 else "moderate" if long_ratio > 70 else "weak",
        }

    def get_taker_volume(self, symbol: str, period: str = "1H") -> Dict:
        """
        S13: 获取主动成交量 (Taker Volume)
        """
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        route = "/api/v5/rubik/stat/taker-volume"
        params = {
            "instId": inst_id,
            "ccy": symbol.upper(),
            "period": period,
        }

        result = self._public_request("GET", route, params)

        if result.get("code") != 0:
            return {"error": result.get("msg", "no data"), "triggered": False}

        data_points = result.get("data", [])
        if not data_points:
            return {"error": "no data", "triggered": False}

        # API 返回: [[ts, buyVol, sellVol, ...], ...]
        latest = data_points[0]
        buy_volume = float(latest[1]) if len(latest) > 1 else 0
        sell_volume = float(latest[2]) if len(latest) > 2 else 0
        total_volume = buy_volume + sell_volume

        # 触发条件: 买入 > 卖出 且 成交量放大
        buy_ratio = buy_volume / total_volume if total_volume > 0 else 0.5
        triggered = buy_ratio > 0.55

        return {
            "signal": "signal_13_taker_volume",
            "triggered": triggered,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "total_volume": total_volume,
            "buy_ratio": buy_ratio,
            "detail": f"主动买入: ${buy_volume/1e6:.2f}M, 卖出: ${sell_volume/1e6:.2f}M, 买入力度: {buy_ratio*100:.1f}%",
            "strength": "strong" if buy_ratio > 0.65 else "moderate",
        }

    def get_oi_and_volume(self, symbol: str, period: str = "1H") -> Dict:
        """
        S3: 获取持仓量和成交量 (OI + Volume)
        用于检测吸筹
        """
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        route = "/api/v5/rubik/stat/contracts/open-interest-volume"
        params = {
            "instId": inst_id,
            "ccy": symbol.upper(),
            "period": period,
        }

        result = self._public_request("GET", route, params)

        if result["code"] != "0" or not result["data"]:
            return {"error": result.get("msg", "no data"), "triggered": False}

        data = result["data"][0]
        oi = float(data.get("oi", 0))
        oi_volume = float(data.get("vol", 0))
        oi_usd = float(data.get("oiUsd", 0))

        return {
            "signal": "signal_3_oi_accumulation",
            "oi": oi,
            "oi_volume": oi_volume,
            "oi_usd": oi_usd,
            "detail": f"持仓量: {oi/1e6:.2f}M, 成交量: {oi_volume/1e6:.2f}M, USD: ${oi_usd/1e6:.2f}M",
        }

    def get_funding_rate(self, symbol: str) -> Dict:
        """
        S2: 获取当前资金费率
        """
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        route = "/api/v5/public/funding-rate"
        params = {"instId": inst_id}

        result = self._public_request("GET", route, params)

        if result.get("code") != 0:
            return {"error": result.get("msg", "no data"), "triggered": False}

        data_points = result.get("data", [])
        if not data_points:
            return {"error": "no data", "triggered": False}

        # API 返回格式变化: [{...}, ...]
        if isinstance(data_points[0], dict):
            data = data_points[0]
            funding_rate = float(data.get("fundingRate", 0))
            next_funding_time = data.get("nextFundingTime", "")
        else:
            # 旧格式: [[instId, fundingRate, ...], ...]
            data = data_points[0]
            funding_rate = float(data[1]) if len(data) > 1 else 0
            next_funding_time = data[2] if len(data) > 2 else ""

        # 触发条件: 资金费率转正
        triggered = funding_rate > 0

        return {
            "signal": "signal_2_funding_turn_positive",
            "triggered": triggered,
            "current_rate": funding_rate * 100,  # 转为百分比
            "next_funding_time": next_funding_time,
            "detail": f"资金费率: {funding_rate*100:.4f}%, 下次结算: {next_funding_time}",
        }

    def get_funding_rate_history(self, symbol: str, limit: int = 7) -> Dict:
        """
        S2: 获取资金费率历史 (判断趋势)
        """
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        route = "/api/v5/public/funding-rate-history"
        params = {
            "instId": inst_id,
            "limit": limit,
        }

        result = self._public_request("GET", route, params)

        if result["code"] != "0" or not result["data"]:
            return {"error": result.get("msg", "no data"), "rates": []}

        rates = []
        for item in result["data"]:
            rate = float(item.get("fundingRate", 0))
            ts = item.get("ts", "")
            rates.append({"rate": rate * 100, "ts": ts})

        if len(rates) >= 3:
            first_rate = rates[0]["rate"]
            last_rate = rates[-1]["rate"]
            trend = "rising" if last_rate > first_rate * 1.2 else "falling" if last_rate < first_rate * 0.8 else "flat"
        else:
            trend = "flat"

        avg_rate = sum(r["rate"] for r in rates) / len(rates) if rates else 0

        return {
            "rates": rates,
            "avg_7d": avg_rate,
            "trend_direction": trend,
            "latest_rate": rates[-1]["rate"] if rates else 0,
            "detail": f"7日平均: {avg_rate:.4f}%, 趋势: {trend}",
        }

    def get_candles(self, symbol: str, bar: str = "1h", limit: int = 100) -> List[Dict]:
        """
        获取K线数据 (用于多维度分析)
        """
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        route = "/api/v5/market/candles"
        params = {
            "instId": inst_id,
            "bar": bar,
            "limit": limit,
        }

        result = self._public_request("GET", route, params)

        if result["code"] != "0" or not result["data"]:
            return []

        candles = []
        for item in result["data"]:
            candles.append({
                "ts": item[0],
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "vol": float(item[5]),
                "vol_usd": float(item[6]) if len(item) > 6 else 0,
            })

        return candles

    def get_ticker(self, symbol: str) -> Dict:
        """
        获取当前价格和24h数据
        """
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        route = "/api/v5/market/ticker"
        params = {"instId": inst_id}

        result = self._public_request("GET", route, params)

        if result["code"] != "0" or not result["data"]:
            return {"error": result.get("msg", "no data")}

        data = result["data"][0]
        return {
            "last": float(data.get("last", 0)),
            "24h_change": float(data.get("sodUtc0", 0)),
            "24h_high": float(data.get("high24h", 0)),
            "24h_low": float(data.get("low24h", 0)),
            "24h_vol": float(data.get("vol24h", 0)),
            "24h_vol_usd": float(data.get("volCcy24h", 0)),
        }

    def get_orderbook(self, symbol: str, depth: int = 20) -> Dict:
        """
        获取订单簿数据
        """
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        route = "/api/v5/market/books"
        params = {
            "instId": inst_id,
            "sz": depth,
        }

        result = self._public_request("GET", route, params)

        if result["code"] != "0" or not result["data"]:
            return {"error": result.get("msg", "no data")}

        data = result["data"][0]
        bids = [[float(p[0]), float(p[1])] for p in json.loads(data.get("bids", "[]"))]
        asks = [[float(p[0]), float(p[1])] for p in json.loads(data.get("asks", "[]"))]

        bid_vol = sum(b[1] for b in bids)
        ask_vol = sum(a[1] for a in asks)
        order_imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0

        return {
            "bids": bids,
            "asks": asks,
            "bid_vol": bid_vol,
            "ask_vol": ask_vol,
            "imbalance": order_imbalance,
            "spread": asks[0][0] - bids[0][0] if asks and bids else 0,
        }

    # ==================== 交易 API ====================

    def place_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: Optional[float] = None,
        order_type: str = "market",
    ) -> Dict:
        """
        下单 (现货模式)
        """
        inst_id = f"{symbol.upper()}-USDT"
        route = "/api/v5/trade/order"
        params = {
            "instId": inst_id,
            "tdMode": "cash",
            "side": side,
            "sz": str(size),
            "ordType": order_type,
        }
        if price:
            params["px"] = str(price)

        return self._signed_request("POST", route, params)

    def place_algo_order(
        self,
        symbol: str,
        side: str,
        size: float,
        trigger_price: float,
        order_price: Optional[float] = None,
        algo_type: str = "conditional",
    ) -> Dict:
        """
        条件单 (止损/止盈)
        """
        inst_id = f"{symbol.upper()}-USDT"
        route = "/api/v5/trade/order-algo"
        params = {
            "instId": inst_id,
            "tdMode": "cash",
            "side": side,
            "sz": str(size),
            "ordType": algo_type,
            "slTrigger": {
                "triggerPx": str(trigger_price),
                "slOrdPx": str(order_price) if order_price else "-1",  # -1 = 市价
            },
        }

        return self._signed_request("POST", route, params)

    def cancel_order(self, symbol: str, order_id: str) -> Dict:
        """撤单"""
        inst_id = f"{symbol.upper()}-USDT"
        route = "/api/v5/trade/cancel-order"
        params = {"instId": inst_id, "ordId": order_id}
        return self._signed_request("POST", route, params)

    def get_order_info(self, symbol: str, order_id: str) -> Dict:
        """查询订单状态"""
        inst_id = f"{symbol.upper()}-USDT"
        route = "/api/v5/trade/order"
        params = {"instId": inst_id, "ordId": order_id}
        return self._signed_request("GET", route, params)

    def get_balance(self) -> Dict:
        """获取账户余额"""
        route = "/api/v5/account/balance"
        return self._signed_request("GET", route)

    def get_positions(self) -> List[Dict]:
        """获取当前持仓"""
        route = "/api/v5/account/positions"
        result = self._signed_request("GET", route)
        return result.get("data", [])

    def get_open_orders(self, symbol: str = None) -> List[Dict]:
        """获取未成交订单"""
        route = "/api/v5/trade/orders-pending"
        params = {}
        if symbol:
            params["instId"] = f"{symbol.upper()}-USDT"
        result = self._signed_request("GET", route, params)
        return result.get("data", [])

    # ==================== 组合信号获取 ====================

    def get_all_signals(self, symbol: str) -> Dict:
        """
        获取某个币的所有信号 (一次性请求)
        """
        signals = {}

        # S12: 多空比 (最重要)
        ls_ratio = self.get_long_short_ratio(symbol, "1h")
        signals["signal_12_long_short_ratio"] = ls_ratio

        # S13: 主动成交量
        taker_vol = self.get_taker_volume(symbol, "1h")
        signals["signal_13_taker_volume"] = taker_vol

        # S3: OI数据
        oi_data = self.get_oi_and_volume(symbol, "1h")
        signals["signal_3_oi_accumulation"] = oi_data

        # S2: 资金费率
        funding = self.get_funding_rate(symbol)
        signals["signal_2_funding_turn_positive"] = funding

        # 资金费率历史 (趋势)
        funding_hist = self.get_funding_rate_history(symbol, 7)
        signals["signal_2_funding_history"] = funding_hist

        # 补充数据
        ticker = self.get_ticker(symbol)
        signals["_ticker"] = ticker

        candles = self.get_candles(symbol, "1h", 24)
        signals["_candles"] = candles

        return signals


if __name__ == "__main__":
    # 测试
    client = OKXOptimizer(testnet=True)
    signals = client.get_all_signals("BTC")
    print(json.dumps(signals, indent=2, ensure_ascii=False))
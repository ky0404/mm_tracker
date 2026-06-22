"""
Freqtrade风格限价单挂单系统
- 限价单挂单，不追高
- 自动计算最佳挂单价
- 支持分批挂单
"""
import requests
import json
import hmac
import base64
import logging
from datetime import datetime
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OrderConfig:
    """挂单配置"""
    token: str
    side: str = "buy"
    amount_usdt: float = 888.0
    # 挂单方式
    price_type: str = "bid_minus_pct"  # bid_minus_pct, vwap_minus_pct, manual
    price_offset_pct: float = 0.5  # 比基准价低0.5%
    # 分批挂单
    split_orders: int = 1  # 分成N批
    split_offset_pct: float = 0.2  # 每批间隔0.2%
    # 超时设置
    max_wait_seconds: int = 300  # 最长等待5分钟
    cancel_if_timeout: bool = True


class OrderManager:
    """
    限价单管理器
    学习最佳挂单价是核心功能
    """
    
    def __init__(self, api_key: str, api_secret: str, passphrase: str, testnet: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.passphrase = passphrase
        self.testnet = testnet
        self.base_url = "https://openapi.okx.com" if testnet else "https://www.okx.com"
        
        # 挂单价历史记录，用于学习
        self.price_history: Dict[str, List[dict]] = {}
        
    def _get_headers(self, method: str, route: str, body: str = "") -> dict:
        timestamp = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        signature = hmac.new(
            self.api_secret.encode(),
            f"{timestamp}{method}{route}{body}".encode(),
            digestmod='sha256'
        ).digest()
        
        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": base64.b64encode(signature).decode(),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        if self.testnet:
            headers["x-simulated-trading"] = "1"
        return headers
    
    def get_market_price(self, token: str) -> Dict[str, float]:
        """获取当前市场价格"""
        url = "https://www.okx.com/api/v5/market/ticker"
        params = {"instId": f"{token}-USDT"}
        
        resp = requests.get(url, params=params, timeout=5)
        data = resp.json()["data"][0]
        
        return {
            "last": float(data["last"]),
            "bid": float(data["bidPx"]),
            "ask": float(data["askPx"]),
            "open": float(data["open24h"]),
            "high": float(data["high24h"]),
            "low": float(data["low24h"]),
        }
    
    def get_vwap(self, token: str, interval: str = "1h", limit: int = 24) -> float:
        """获取成交量加权均价"""
        url = "https://www.okx.com/api/v5/market/history-candles"
        params = {
            "instId": f"{token}-USDT",
            "bar": interval,
            "limit": limit
        }
        
        resp = requests.get(url, params=params, timeout=5)
        candles = resp.json()["data"]
        
        total_vol = 0
        total_price_vol = 0
        
        for c in candles:
            typical_price = (float(c[2]) + float(c[3]) + float(c[4])) / 3  # (high+low+close)/3
            volume = float(c[6])
            total_vol += volume
            total_price_vol += typical_price * volume
            
        return total_price_vol / total_vol if total_vol > 0 else 0
    
    def calculate_entry_price(self, token: str, config: OrderConfig) -> List[Tuple[float, float]]:
        """
        计算挂单价
        返回: [(价格, 数量), ...] 列表
        """
        market = self.get_market_price(token)
        
        # 选择基准价
        if config.price_type == "bid_minus_pct":
            base_price = market["bid"]
        elif config.price_type == "vwap_minus_pct":
            base_price = self.get_vwap(token)
        elif config.price_type == "ask_minus_pct":
            base_price = market["ask"] * 0.99
        else:
            base_price = market["last"] * 0.995
        
        # 计算每批挂单价
        orders = []
        amount_per_order = config.amount_usdt / config.split_orders
        
        for i in range(config.split_orders):
            # 每批价格递减，等价格回落成交
            offset = config.price_offset_pct + (i * config.split_offset_pct)
            entry_price = round(base_price * (1 - offset / 100), 5)
            size = amount_per_order / entry_price
            
            orders.append((entry_price, size))
            
        logger.info(f"[挂单价计算] {token}: 基准${base_price:.5f}, 挂单价区间${orders[0][0]:.5f}-${orders[-1][0]:.5f}")
        
        return orders
    
    def place_limit_order(self, token: str, side: str, size: float, price: float) -> dict:
        """挂限价单"""
        route = "/api/v5/trade/order"
        body = {
            "instId": f"{token}-USDT",
            "tdMode": "cash",
            "side": side,
            "ordType": "limit",
            "sz": str(size),
            "px": str(price),
            "tgtCcy": "base_ccy"  # 按数量下单
        }
        
        headers = self._get_headers("POST", route, json.dumps(body))
        resp = requests.post(f"{self.base_url}{route}", json=body, headers=headers, timeout=10)
        
        result = resp.json()
        if result.get("code") == "0":
            logger.info(f"[限价单成交] {token} {side} {size} @ ${price}")
            self._record_success(token, price, "filled")
        else:
            logger.warning(f"[限价单失败] {result}")
            
        return result
    
    def cancel_order(self, token: str, order_id: str) -> dict:
        """取消订单"""
        route = "/api/v5/trade/cancel-order"
        body = {
            "instId": f"{token}-USDT",
            "ordId": order_id
        }
        
        headers = self._get_headers("POST", route, json.dumps(body))
        resp = requests.post(f"{self.base_url}{route}", json=body, headers=headers, timeout=10)
        return resp.json()
    
    def get_pending_orders(self, token: str = None) -> List[dict]:
        """查询待成交订单"""
        route = "/api/v5/trade/orders-pending"
        if token:
            route += f"?instId={token}-USDT"
            
        headers = self._get_headers("GET", route)
        resp = requests.get(f"{self.base_url}{route}", headers=headers, timeout=10)
        
        data = resp.json()
        return data.get("data", []) if data.get("code") == "0" else []
    
    def _record_success(self, token: str, price: float, status: str):
        """记录挂单结果，用于学习"""
        if token not in self.price_history:
            self.price_history[token] = []
            
        self.price_history[token].append({
            "price": price,
            "status": status,
            "timestamp": datetime.now().isoformat()
        })
    
    def get_optimal_entry_offset(self, token: str) -> float:
        """
        获取最佳挂单偏移百分比
        基于历史数据学习
        """
        if token not in self.price_history:
            return 0.5  # 默认0.5%
            
        history = self.price_history[token]
        filled = [h for h in history if h["status"] == "filled"]
        
        if not filled:
            return 0.5
            
        # 计算平均成交价与当时买一价的差距
        avg_offset = sum([
            (h["price"] / self.get_market_price(token)["bid"] - 1) * 100
            for h in filled
        ]) / len(filled)
        
        # 学习结果：负值表示挂单价比买一低
        return abs(avg_offset) + 0.1  # 稍微激进一点


class FreqtradeStyleExit:
    """
    Freqtrade风格出场策略
    - Trailing Stop (跟踪止损)
    - Dynamic ROI (动态止盈)
    - Exit Signal (出场信号)
    """
    
    def __init__(self, params: dict = None):
        self.params = params or {}
        
        # Trailing Stop参数
        self.trailing_stop = self.params.get("trailing_stop", True)
        self.trailing_stop_positive = self.params.get("trailing_stop_positive", 0.02)  # 2%启动
        self.trailing_stop_offset = self.params.get("trailing_stop_offset", 0.04)  # 4%锁定利润
        
        # Dynamic ROI
        self.min_roi = self.params.get("min_roi", {
            "0": 0.05,    # 0-30分钟: 5%
            "30": 0.03,   # 30-60分钟: 3%
            "60": 0.02,   # 60-120分钟: 2%
            "120": 0.01,  # 2小时以上: 1%
        })
        
        # Exit Signal
        self.use_exit_signal = self.params.get("use_exit_signal", True)
        self.exit_profit_only = self.params.get("exit_profit_only", True)
        
        # 持仓状态
        self.entry_price: float = 0
        self.entry_time: datetime = None
        self.highest_price: float = 0
        
    def set_entry(self, price: float):
        """设置入场信息"""
        self.entry_price = price
        self.entry_time = datetime.now()
        self.highest_price = price
        
    def update_highest(self, current_price: float):
        """更新最高价"""
        if current_price > self.highest_price:
            self.highest_price = current_price
            
    def should_exit(
        self, 
        current_price: float, 
        current_time: datetime = None,
        rsi: float = None,
        sar_reversal: bool = False
    ) -> Tuple[bool, str]:
        """
        判断是否应该出场
        返回: (是否出场, 出场原因)
        """
        current_time = current_time or datetime.now()
        
        # 1. 检查Dynamic ROI (固定止盈)
        minutes_held = (current_time - self.entry_time).seconds / 60
        for minutes, profit_pct in sorted(self.min_roi.items(), key=lambda x: -int(x[0])):
            if minutes_held >= int(minutes):
                if (current_price - self.entry_price) / self.entry_price >= profit_pct:
                    return True, f"ROI_{minutes}m_{profit_pct*100}%"
                break
        
        # 2. 检查Trailing Stop
        if self.trailing_stop:
            profit_pct = (current_price - self.entry_price) / self.entry_price
            
            # 创新高后，检查是否触发跟踪止损
            if profit_pct >= self.trailing_stop_positive:
                # 止损线上移
                stop_price = self.highest_price * (1 - self.trailing_stop_offset)
                if current_price <= stop_price:
                    return True, f"TRAILING_STOP_{self.trailing_stop_offset*100}%"
        
        # 3. 检查Exit Signals
        if self.use_exit_signal:
            if rsi and rsi > 70:
                if not self.exit_profit_only or profit_pct > 0:
                    return True, "RSI_OVERBOUGHT"
                    
            if sar_reversal and sar_reversal == "down":
                return True, "SAR_REVERSAL"
        
        return False, ""
    
    def get_stop_price(self) -> float:
        """获取当前止损价"""
        if not self.trailing_stop:
            return self.entry_price * 0.98
            
        profit_pct = (self.highest_price - self.entry_price) / self.entry_price
        
        if profit_pct >= self.trailing_stop_positive:
            return self.highest_price * (1 - self.trailing_stop_offset)
        else:
            return self.entry_price


# ==================== 单元测试 ====================
if __name__ == "__main__":
    import sys
    sys.path.insert(0, ".")
    
    from trading.okx_testnet import OKXTestnetTrader
    
    # 读取配置
    with open("config/testnet_config.json") as f:
        cfg = json.load(f)
    
    # 测试OrderManager
    om = OrderManager(
        cfg["okx"]["api_key"],
        cfg["okx"]["api_secret"],
        cfg["okx"]["passphrase"],
        testnet=True
    )
    
    # 测试计算挂单价
    config = OrderConfig(
        token="OP",
        amount_usdt=888,
        price_type="bid_minus_pct",
        price_offset_pct=0.5,
        split_orders=2,
        split_offset_pct=0.3
    )
    
    orders = om.calculate_entry_price("OP", config)
    print(f"=== 挂单价测试 ===")
    for i, (px, sz) in enumerate(orders):
        print(f"单{i+1}: {sz:.2f} OP @ ${px:.5f}")
    
    # 测试FreqtradeStyleExit
    exit_mgr = FreqtradeStyleExit({
        "trailing_stop": True,
        "trailing_stop_positive": 0.02,
        "trailing_stop_offset": 0.04,
        "min_roi": {"0": 0.20}  # 20%止盈
    })
    
    exit_mgr.set_entry(0.10)
    
    # 模拟价格上涨到20%
    current_price = 0.12
    should_exit, reason = exit_mgr.should_exit(current_price)
    print(f"\n=== 出场测试 ===")
    print(f"入场: $0.10, 当前: ${current_price}, 出场: {should_exit}, 原因: {reason}")
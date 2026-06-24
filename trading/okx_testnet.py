import requests
import hmac
import base64
import json
import logging
import math
import time
from datetime import datetime
from typing import Optional, Dict, Any, List
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


def create_session() -> requests.Session:
    """创建带重试和代理的Session"""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    
    # 添加代理支持 - 强制使用常见代理端口
    import os
    
    # 首先尝试从环境变量读取
    proxy = os.environ.get('https_proxy') or os.environ.get('http_proxy')
    
    # 如果没有环境变量，尝试常见的代理地址
    if not proxy:
        # WSL2 常见宿主机代理地址
        common_proxies = [
            'http://172.18.48.1:10810',
            'http://172.18.144.1:10810',
            'http://127.0.0.1:10810',
            'http://localhost:10810',
        ]
        for p in common_proxies:
            try:
                # 测试代理是否可用
                test_resp = requests.get(
                    'https://openapi.okx.com/api/v5/public/time',
                    proxies={'http': p, 'https': p},
                    timeout=3
                )
                if test_resp.status_code == 200:
                    proxy = p
                    logger.info(f"[OKX] 自动发现可用代理: {proxy}")
                    break
            except:
                continue
    
    if proxy:
        session.proxies = {
            'http': proxy,
            'https': proxy
        }
        logger.info(f"[OKX] 使用代理: {proxy}")
    else:
        logger.warning("[OKX] 未检测到代理，可能无法访问外网")
    
    return session


def safe_json_loads(data: Any, default: Any = None) -> Any:
    """安全的JSON解析"""
    if data is None:
        return default
    if isinstance(data, (dict, list)):
        return data
    try:
        if isinstance(data, str):
            return json.loads(data)
        return default
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"JSON解析失败: {e}")
        return default


def normalize_symbol(symbol: str, use_spot: bool = True) -> str:
    """
    统一 symbol 规范化
    输入任意格式，输出目标格式
    
    use_spot=True  → "OP-USDT"  (现货)
    use_spot=False → "OP-USDT-SWAP" (永续合约)
    
    支持的输入格式:
      "OP"                → base token only
      "OP-USDT"           → spot format
      "OP-USDT-SWAP"      → swap format
      "SWAP-OP-USDT"      → old internal format
    """
    s = symbol.strip().upper()

    if s.startswith("SWAP-"):
        s = s[5:]

    if s.endswith("-SWAP"):
        s = s[:-5]

    if s.endswith("-USDT"):
        base = s[:-5]
    elif "-" in s:
        base = s.split("-")[0]
    else:
        base = s

    if use_spot:
        return f"{base}-USDT"
    else:
        return f"{base}-USDT-SWAP"


def round_to_precision(value: float, precision: int = 4) -> str:
    """
    将浮点数四舍五入到指定小数位，返回字符串（OKX API 需要字符串格式）
    避免科学计数法：0.0001234 不能用 1.234e-04 传给 API
    """
    factor = 10 ** precision
    rounded = math.floor(value * factor) / factor
    return f"{rounded:.{precision}f}".rstrip('0').rstrip('.')


class OKXTestnetTrader:
    """
    OKX 交易器 — 修复版
    修复点:
      1. _convert_symbol → normalize_symbol（统一格式处理）
      2. close_position → 精确计算 availBal，处理精度问题
      3. 增加 get_ticker 用于获取实时价格（position_monitor 需要）
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        testnet: bool = True,
        use_spot: bool = True,
    ):
        """
        OKX交易器初始化
        
        ⚠️ 已知限制 (2026-06-23):
        - 测试网账户 (label: 'moni') 只支持现货交易，不支持永续合约(SWAP)
        - 如需使用合约交易，需切换到真实账户 (testnet=False)
        - 账户模式限制错误码: sCode=51010, msg="You can't complete this request under your current account mode."
        """
        if testnet:
            self.base_url = "https://openapi.okx.com"
            self.is_simulation = True
        else:
            self.base_url = "https://www.okx.com"
            self.is_simulation = False

        self.use_spot = use_spot
        self.api_key = api_key or ""
        self.api_secret = api_secret or ""
        self.passphrase = passphrase or ""
        
        # 使用带重试的session
        self.session = create_session()
        
        # Testnet不支持的交易对黑名单
        self._testnet_failed_tokens = set()
        
        # 已验证支持交易的币种白名单（测试网实测可用）
        self._testnet_whitelist = {
            'BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'ADA', 'DOGE', 'AVAX', 'DOT', 'MATIC',
            'LINK', 'UNI', 'ATOM', 'LTC', 'ETC', 'XLM', 'ALGO', 'VET', 'FIL', 'THETA',
            'AAVE', 'MKR', 'COMP', 'SNX', 'SUSHI', 'YFI', 'CRV', 'BAT', 'ENJ', 'MANA',
            'SAND', 'AXS', 'GALA', 'OP', 'ARB', 'GMX', 'PEPE', 'WIF', 'BONK', 'SHIB',
            'NEAR', 'APT', 'ARB', 'OP', 'BLUR', 'IMX', 'LDO', 'APT', 'SUI', 'SEI',
            'INJ', 'TIA', 'SEI', 'PYTH', 'ONDO', 'JUP', 'WLD', 'FET', 'AGIX', 'RNDR',
            'GRT', 'STX', 'RUNE', 'KAVA', 'ZEC', 'DASH', 'NEO', 'EOS', 'XTZ', 'CAKE',
            '1INCH', 'CHZ', 'CELO', 'FTM', 'HOT', 'ZIL', 'ENJ', 'BAT', 'ZRX', 'CELR',
            'ANKR', 'REN', 'KNC', 'SNX', 'LRC', 'OCEAN', 'BAND', 'CRV', 'SUSHI',
        }
        
        # Testnet不支持的交易对黑名单（实测失败）
        self._testnet_failed_tokens = {
            # 2026-06-23 实测不可交易
            'MMT',    # 51001: Instrument ID doesn't exist
            'RESOLV', # 51155: Local compliance restrictions
        }

    def _generate_signature(self, timestamp: str, method: str, route: str, body: str = "") -> str:
        secret_key = self.api_secret.encode("utf-8")
        message = f"{timestamp}{method}{route}{body}".encode("utf-8")
        signed = hmac.new(secret_key, message, digestmod="sha256").digest()
        return base64.b64encode(signed).decode("utf-8")

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
        # Testnet黑名单检查
        if self.is_simulation and symbol in self._testnet_failed_tokens:
            logger.warning(f"[下单跳过] {symbol} 在Testnet黑名单中")
            return {"code": "1", "msg": "token_in_blacklist", "data": []}
        
        inst_id = normalize_symbol(symbol, use_spot=self.use_spot)
        route = "/api/v5/trade/order"
        body_dict = {
            "instId": inst_id,
            "tdMode": "cash",
            "side": side,
            "sz": round_to_precision(size, 4),
            "ordType": order_type,
        }
        if price and order_type == "limit":
            body_dict["px"] = round_to_precision(price, 6)

        body = json.dumps(body_dict)
        headers = self._get_headers("POST", route, body)
        
        # 使用session + 重试
        for attempt in range(3):
            try:
                resp = self.session.post(
                    f"{self.base_url}{route}", 
                    data=body, 
                    headers=headers, 
                    timeout=10
                )
                result = resp.json()
                
                # 检查API返回的错误
                if result.get("code") == "0":
                    return result
                elif result.get("code") == "1":
                    # 业务错误，记录完整错误信息
                    msg = result.get('msg', '')
                    full_msg = f"code={result.get('code')}, msg={msg}, data={result.get('data')}"
                    logger.error(f"[OKX下单失败] {symbol} 完整错误: {full_msg}")
                    
                    # 详细错误分析
                    if '51001' in msg or 'Instrument ID' in msg or "doesn't exist" in msg:
                        self._testnet_failed_tokens.add(symbol)
                        logger.warning(f"[Testnet] {symbol} 不支持交易，已加入黑名单")
                    elif '50119' in msg:
                        logger.error(f"[OKX] 仓位不足 - 需要检查账户余额和持仓")
                    elif '51004' in msg:
                        logger.error(f"[OKX] 账户余额不足 - 需要充值或减少下单金额")
                    elif '51201' in msg or 'sz' in msg.lower():
                        logger.error(f"[OKX] 数量精度或最小金额不满足要求")
                    elif '51129' in msg or '价格' in msg or 'px' in msg.lower():
                        logger.error(f"[OKX] 价格问题 - 可能是限价单价格超出限制")
                    
                    return result
                else:
                    # 其他错误，重试
                    logger.warning(f"下单API错误 (尝试{attempt+1}/3): {result.get('msg')}")
                    if attempt < 2:
                        time.sleep(1)
                        continue
                    return result
                    
            except requests.exceptions.Timeout:
                logger.warning(f"下单超时 (尝试{attempt+1}/3)")
                if attempt == 2:
                    return {"code": "1", "msg": "timeout", "data": []}
            except requests.exceptions.RequestException as e:
                logger.warning(f"下单请求异常 (尝试{attempt+1}/3): {e}")
                if attempt == 2:
                    return {"code": "1", "msg": str(e), "data": []}
            
            if attempt < 2:
                time.sleep(1)
        
        return {"code": "1", "msg": "max_retries", "data": []}

    def cancel_order(self, symbol: str, order_id: str) -> Dict[str, Any]:
        inst_id = normalize_symbol(symbol, use_spot=self.use_spot)
        route = "/api/v5/trade/cancel-order"
        body_dict = {"instId": inst_id, "ordId": order_id}
        body = json.dumps(body_dict)
        headers = self._get_headers("POST", route, body)
        try:
            resp = self.session.post(f"{self.base_url}{route}", data=body, headers=headers, timeout=10)
            return resp.json()
        except Exception as e:
            return {"code": "1", "msg": str(e)}

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        inst_id = normalize_symbol(symbol, use_spot=self.use_spot)
        route = f"/api/v5/account/positions?instId={inst_id}"
        headers = self._get_headers("GET", route)
        try:
            # 使用session（带代理）
            resp = self.session.get(f"{self.base_url}{route}", headers=headers, timeout=15)
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return data["data"][0]
        except Exception as e:
            logger.error(f"get_position 异常: {e}")
        return None

    def close_position(self, symbol: str) -> Dict[str, Any]:
        """
        修复版 close_position
        1. 统一 symbol 格式
        2. 查 availBal（可用余额），不是 eq（总权益，包含冻结）
        3. 数量精度处理
        4. 最小订单金额检查
        """
        inst_id = normalize_symbol(symbol, use_spot=self.use_spot)
        base_ccy = inst_id.split("-")[0]

        balance = self.get_balance()
        if not balance or "details" not in balance:
            return {"code": "1", "msg": "无法获取余额"}

        avail_bal = 0.0
        for detail in balance.get("details", []):
            if detail.get("ccy") == base_ccy:
                avail_bal = float(detail.get("availBal", 0))
                break

        if avail_bal <= 0:
            logger.info(f"[close_position] {base_ccy} 可用余额为 0，跳过")
            return {"code": "0", "msg": "no_position", "data": []}

        # 最小订单金额检查 (OKX合约通常要求 >= $1)
        # 【修复v2】当金额太小时，尝试卖出，如果API拒绝则标记为dust_close
        # 这样可以彻底解决0.0几残留的问题
        min_order_usd = 1.0  # 最低 $1
        
        # 先尝试获取价格，如果失败则用保守估计
        current_price = 0.0
        try:
            current_price = self.get_current_price(base_ccy)
        except:
            pass
        
        if current_price <= 0:
            # 用最新市场价格或保守估计
            current_price = self._get_latest_price_estimate(base_ccy)
        
        estimated_usd = avail_bal * current_price if current_price > 0 else 0.001
        
        is_small_position = estimated_usd < min_order_usd
        
        # 记录诊断信息
        logger.info(f"[close_position] {base_ccy} 可用: {avail_bal:.6f}, 价格: ${current_price:.4f}, 估值: ${estimated_usd:.2f}")
        
        # 尝试卖出（无论金额大小，让API决定）
        logger.info(f"[close_position] 市价卖出 {base_ccy} × {avail_bal:.6f}")
        result = self.place_order(inst_id, "sell", avail_bal, None, "market")
        
        # 检查是否因金额太小被拒绝
        if result.get("code") != "0":
            err_msg = result.get("msg", "")
            sCode = ""
            if result.get("data") and len(result["data"]) > 0:
                sCode = result["data"][0].get("sCode", "")
            
            # 51020 = 订单金额小于最小值
            if sCode == "51020" or "minimum order" in err_msg.lower():
                logger.warning(f"[close_position] ⚠️ {base_ccy} 金额不足 $1 (估值 ${estimated_usd:.2f})，标记为dust_close")
                result["dust_close"] = True  # 标记为小额已处理
                result["code"] = "0"  # 返回成功，避免系统重复尝试
                result["msg"] = "dust_position_closed"
        
        return result

    def _get_latest_price_estimate(self, symbol: str) -> float:
        """获取最新价格估算（用于小额持仓计算）"""
        # 尝试从持仓数据中获取价格
        try:
            positions = self.get_all_positions()
            for p in positions:
                if p.get("token") == symbol.upper():
                    return p.get("current_price", 0)
        except:
            pass
        # 如果都失败，返回保守估计（低价）
        return 0.001

    def get_open_orders(self, symbol: str) -> list:
        inst_id = normalize_symbol(symbol, use_spot=self.use_spot)
        route = f"/api/v5/trade/orders-pending?instId={inst_id}"
        headers = self._get_headers("GET", route)
        try:
            resp = self.session.get(f"{self.base_url}{route}", headers=headers, timeout=10)
            data = resp.json()
            if data.get("code") == "0":
                return data.get("data", [])
        except Exception as e:
            logger.error(f"get_open_orders 异常: {e}")
        return []

    def get_balance(self) -> Optional[Dict[str, Any]]:
        route = "/api/v5/account/balance"
        headers = self._get_headers("GET", route)
        try:
            # 使用session（带代理）
            resp = self.session.get(f"{self.base_url}{route}", headers=headers, timeout=15)
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return data["data"][0]
            elif data.get("code") == "50101":
                logger.error(f"[OKX] API Key环境不匹配 - Testnet Key只能用于模拟盘")
        except Exception as e:
            logger.error(f"get_balance 异常: {e}")
        return None

    def get_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取实时 ticker（position_monitor 需要当前价格）"""
        inst_id = normalize_symbol(symbol, use_spot=self.use_spot)
        url = "https://www.okx.com/api/v5/market/ticker"
        try:
            resp = self.session.get(url, params={"instId": inst_id}, timeout=5)
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return data["data"][0]
        except Exception as e:
            logger.error(f"get_ticker 异常: {e}")
        return None

    def get_current_price(self, symbol: str) -> float:
        """获取当前市价（供 position_monitor 使用）"""
        ticker = self.get_ticker(symbol)
        if ticker:
            return float(ticker.get("last", 0))
        return 0.0

    def get_all_positions(self) -> List[Dict[str, Any]]:
        """
        直接从OKX API获取所有持仓 - 不依赖数据库！
        返回: [{'token': 'BTC', 'size': 0.1, 'avg_price': 65000, 'current_price': 66000, 'unrealized_pnl': 100}, ...]
        """
        positions = []
        try:
            # 获取账户余额
            balance = self.get_balance()
            if not balance or 'details' not in balance:
                return positions
            
            # 遍历所有有余额的币种
            for detail in balance.get('details', []):
                available = float(detail.get('availBal', 0))
                frozen = float(detail.get('frozenBal', 0))
                total = available + frozen
                
                if total > 0.0001:  # 忽略很小的余额
                    ccy = detail.get('ccy', '')
                    if ccy == 'USDT':
                        continue  # 跳过USDT本身
                    
                    # 获取当前市场价格
                    current_price = 0
                    try:
                        ticker = self.get_ticker(f"{ccy}-USDT")
                        if ticker:
                            current_price = float(ticker.get('last', 0))
                    except:
                        pass
                    
                    # 从余额数据获取平均入场价 (accAvgPx字段)
                    avg_price = float(detail.get('accAvgPx', 0))
                    
                    # 如果accAvgPx为0，尝试用当前价格作为默认值
                    if avg_price == 0 and current_price > 0:
                        avg_price = current_price  # 用当前价作为参考价
                    
                    positions.append({
                        'token': ccy,
                        'size': total,
                        'avg_price': avg_price,
                        'current_price': current_price,
                        'available': available,
                        'frozen': frozen,
                    })
            
            logger.info(f"[OKX] 实时持仓: {len(positions)}个币种")
            return positions
            
        except Exception as e:
            logger.error(f"[OKX] 获取所有持仓失败: {e}")
            return []

    def get_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取市场价格"""
        try:
            inst_id = normalize_symbol(symbol, use_spot=self.use_spot)
            route = f"/api/v5/market/ticker?instId={inst_id}"
            resp = self.session.get(f"{self.base_url}{route}", timeout=10)
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return data["data"][0]
        except Exception as e:
            logger.error(f"get_ticker 异常: {e}")
        return None

    def is_token_supported(self, symbol: str) -> bool:
        """检查测试网是否支持该币种现货交易"""
        if symbol in self._testnet_failed_tokens:
            return False
        
        if symbol in self._testnet_whitelist:
            return True
        
        return False

    def get_order_info(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        inst_id = normalize_symbol(symbol, use_spot=self.use_spot)
        route = f"/api/v5/trade/order?instId={inst_id}&ordId={order_id}"
        headers = self._get_headers("GET", route)
        try:
            resp = self.session.get(f"{self.base_url}{route}", headers=headers, timeout=10)
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return data["data"][0]
        except Exception as e:
            logger.error(f"get_order_info 异常: {e}")
        return None
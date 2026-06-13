"""
Coinglass 数据获取器
使用 coinglass-api 库 + 用户 API Key
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

COINGLASS_API_KEY = "8ed5c100550a4b60985b5dc498db8044"

# 尝试导入 coinglass_api
try:
    from coinglass_api import CoinglassAPI
    COINGLASS_AVAILABLE = True
except ImportError:
    COINGLASS_AVAILABLE = False
    CoinglassAPI = None


def fetch_funding_rate_history(symbol: str) -> dict:
    """
    获取资金费率历史数据
    
    Args:
        symbol: 代币符号
        
    Returns:
        {"rates": [], "latest_rate": float, "avg_7d": float, "trend_direction": str}
    """
    if not COINGLASS_AVAILABLE:
        return {"error": "coinglass-api 库未安装"}
    
    try:
        cg = CoinglassAPI(coinglass_secret=COINGLASS_API_KEY)
        
        # 获取 USD 资金费率历史 (h8 = 8小时)
        data = cg.funding_usd_history(symbol=symbol.upper(), time_type="h8")
        
        if not data or len(data) == 0:
            return {"error": "无数据", "rates": []}
        
        # 提取费率
        rates = []
        for item in data:
            fr = item.get("fundingRate") or item.get("funding_rate")
            if fr is not None:
                try:
                    rates.append(float(fr))
                except (ValueError, TypeError):
                    pass
        
        if not rates:
            return {"error": "解析失败", "rates": []}
        
        latest_rate = rates[-1]
        recent = rates[-7:] if len(rates) >= 7 else rates
        avg_7d = sum(recent) / len(recent)
        
        # 判断趋势
        if len(recent) >= 3:
            if recent[-1] > recent[0] * 1.1:
                trend = "rising"
            elif recent[-1] < recent[0] * 0.9:
                trend = "falling"
            else:
                trend = "flat"
        else:
            trend = "flat"
        
        print(f"[Coinglass] ✓ {symbol} 资金费率 (最新: {latest_rate*100:.4f}%)")
        
        return {
            "rates": rates,
            "latest_rate": latest_rate,
            "avg_7d": avg_7d,
            "trend_direction": trend,
            "source": "coinglass"
        }
        
    except Exception as e:
        logger.error(f"Coinglass 资金费率失败: {e}")
        return {"error": str(e), "rates": []}


def fetch_oi_history(symbol: str) -> dict:
    """
    获取持仓量(OI)历史数据
    
    Args:
        symbol: 代币符号
        
    Returns:
        {"oi_series": [], "oi_change_7d_pct": float, "oi_latest": float}
    """
    if not COINGLASS_AVAILABLE:
        return {"error": "coinglass-api 库未安装"}
    
    try:
        cg = CoinglassAPI(coinglass_secret=COINGLASS_API_KEY)
        
        # 获取 OI 历史 (USD 计价)
        df = cg.open_interest_history(
            symbol=symbol.upper(),
            time_type="h1",
            currency="USD"
        )
        
        if df.empty or len(df) < 2:
            return {"error": "无数据", "oi_series": []}
        
        # 提取 OI 值
        oi_series = df["openInterest"].tolist() if "openInterest" in df.columns else []
        
        if not oi_series:
            return {"error": "无OI数据", "oi_series": []}
        
        oi_latest = oi_series[-1]
        oi_7d_ago = oi_series[0]
        
        if oi_7d_ago > 0:
            oi_change_7d_pct = ((oi_latest - oi_7d_ago) / oi_7d_ago) * 100
        else:
            oi_change_7d_pct = 0.0
        
        print(f"[Coinglass] ✓ {symbol} OI数据 (最新: ${oi_latest/1e9:.2f}B)")
        
        return {
            "oi_series": oi_series,
            "oi_change_7d_pct": oi_change_7d_pct,
            "oi_latest": oi_latest,
            "source": "coinglass"
        }
        
    except Exception as e:
        logger.error(f"Coinglass OI 失败: {e}")
        return {"error": str(e), "oi_series": []}


def get_funding_rate_current(symbol: str) -> float:
    """获取当前资金费率"""
    data = fetch_funding_rate_history(symbol)
    return data.get("latest_rate", 0)


def get_oi_current(symbol: str) -> float:
    """获取当前 OI"""
    data = fetch_oi_history(symbol)
    return data.get("oi_latest", 0)
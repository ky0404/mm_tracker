"""
清算扫单检测器 — 识别庄家清算操作的前兆和完成
基于三个公开OKX指标：资金费率 + OI变化 + 价格行为
"""
import requests
import time
from typing import Dict, Any, Optional


def get_recent_funding_rates(symbol: str, limit: int = 10) -> list:
    """获取最近N条资金费率历史（每8小时一条）"""
    try:
        url = "https://www.okx.com/api/v5/public/funding-rate-history"
        resp = requests.get(url, params={
            "instId": f"{symbol.upper()}-USDT-SWAP",
            "limit": str(limit)
        }, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "0":
                return [float(d.get("fundingRate", 0)) for d in data.get("data", [])]
    except:
        pass
    return []


def get_oi_last_4h(symbol: str) -> Dict[str, float]:
    """
    获取过去4小时的OI变化
    返回: {current_oi, oi_1h_ago, oi_4h_ago, oi_change_1h_pct, oi_change_4h_pct}
    """
    try:
        url = "https://www.okx.com/api/v5/rubik/stat/contracts/open-interest-history"
        resp = requests.get(url, params={
            "instId": f"{symbol.upper()}-USDT-SWAP",
            "period": "1H",
            "limit": "5"
        }, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "0" and data.get("data") and isinstance(data["data"], list):
                # OKX returns: [[ts, openInterest, openInterestUsd, ...], ...]
                oi_list = [float(d[1]) for d in data["data"]]
                if len(oi_list) >= 4:
                    current = oi_list[0]
                    one_h = oi_list[1]
                    four_h = oi_list[3]
                    return {
                        "current_oi": current,
                        "oi_1h_ago": one_h,
                        "oi_4h_ago": four_h,
                        "oi_change_1h_pct": (current - one_h) / one_h * 100 if one_h > 0 else 0,
                        "oi_change_4h_pct": (current - four_h) / four_h * 100 if four_h > 0 else 0,
                    }
    except:
        pass
    return {"current_oi": 0, "oi_change_1h_pct": 0, "oi_change_4h_pct": 0}


def get_current_funding_rate(symbol: str) -> float:
    """获取当前实时资金费率"""
    try:
        url = "https://www.okx.com/api/v5/public/funding-rate"
        resp = requests.get(url, params={"instId": f"{symbol.upper()}-USDT-SWAP"}, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                return float(data["data"][0].get("fundingRate", 0))
    except:
        pass
    return 0.0


def get_price_action_15m(symbol: str) -> Dict[str, Any]:
    """
    获取过去3根15分钟K线的价格行为
    用于判断：价格是否在阻力位卡住、是否刚刚快速反弹
    """
    try:
        url = "https://www.okx.com/api/v5/market/history-candles"
        resp = requests.get(url, params={
            "instId": f"{symbol.upper()}-USDT-SWAP",
            "bar": "15m",
            "limit": "6"
        }, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == "0" and data.get("data"):
                candles = data["data"]
                prices = [float(c[4]) for c in candles]
                volumes = [float(c[5]) for c in candles]

                latest_price = prices[0]
                price_15m_ago = prices[1]
                price_45m_ago = prices[3]

                change_15m = (latest_price - price_15m_ago) / price_15m_ago * 100 if price_15m_ago > 0 else 0
                change_45m = (latest_price - price_45m_ago) / price_45m_ago * 100 if price_45m_ago > 0 else 0

                avg_vol = sum(volumes[1:5]) / 4 if len(volumes) >= 5 else 1
                latest_vol_ratio = volumes[0] / avg_vol if avg_vol > 0 else 1

                return {
                    "latest_price": latest_price,
                    "change_15m_pct": round(change_15m, 2),
                    "change_45m_pct": round(change_45m, 2),
                    "vol_ratio_15m": round(latest_vol_ratio, 2),
                    "is_recovering": change_15m > 1.5 and change_45m < -2.0,
                    "is_stalling": abs(change_15m) < 0.5 and abs(change_45m) < 1.0,
                }
    except:
        pass
    return {"latest_price": 0, "change_15m_pct": 0, "change_45m_pct": 0, "vol_ratio_15m": 1, "is_recovering": False, "is_stalling": False}


def detect_sweep_status(symbol: str) -> Dict[str, Any]:
    """
    核心检测函数：判断当前代币处于清算周期的哪个阶段

    返回 status 的可能值：
    - "pre_sweep"   → 清算前兆，不要入场（费率高+OI高+价格停滞）
    - "sweeping"    → 正在清算，等待完成（价格快速下跌+量放大）
    - "post_sweep"  → 清算完成，入场窗口（价格快速反弹+费率归零）
    - "normal"      → 正常状态，按其他信号决策
    - "hot"         → 已经过热，费率太高风险大
    """
    funding_rate = get_current_funding_rate(symbol)
    oi_data = get_oi_last_4h(symbol)
    price_action = get_price_action_15m(symbol)

    fr_pct = funding_rate * 100
    oi_change_1h = oi_data.get("oi_change_1h_pct", 0)
    change_15m = price_action.get("change_15m_pct", 0)
    change_45m = price_action.get("change_45m_pct", 0)
    is_recovering = price_action.get("is_recovering", False)
    is_stalling = price_action.get("is_stalling", False)
    vol_ratio = price_action.get("vol_ratio_15m", 1)

    status = "normal"
    confidence = 0
    detail = ""

    if fr_pct > 0.3:
        status = "hot"
        confidence = 0
        detail = f"资金费率{fr_pct:.3f}%过高，庄家可能在出货"

    elif fr_pct > 0.1 and oi_change_1h > -2 and is_stalling:
        status = "pre_sweep"
        confidence = 0
        detail = f"费率{fr_pct:.3f}% + OI未减少 + 价格停滞，清算前兆，等待"

    elif change_15m < -3.0 and vol_ratio > 2.0:
        status = "sweeping"
        confidence = 0
        detail = f"15分钟下跌{change_15m:.1f}%，量比{vol_ratio:.1f}x，正在清算，等待反弹"

    elif is_recovering and fr_pct < 0.05 and vol_ratio < 3.0:
        status = "post_sweep"
        confidence = 2
        detail = f"清算完成！价格反弹{change_15m:.1f}%，费率{fr_pct:.3f}%，入场窗口"

    elif -0.01 < fr_pct < 0.05 and oi_change_1h > 0:
        status = "normal"
        confidence = 1
        detail = f"费率健康{fr_pct:.3f}%，OI增加{oi_change_1h:.1f}%，可以考虑"

    return {
        "symbol": symbol,
        "status": status,
        "confidence": confidence,
        "funding_rate_pct": round(fr_pct, 4),
        "oi_change_1h_pct": round(oi_data.get("oi_change_1h_pct", 0), 2) if oi_data.get("current_oi", 0) > 0 else 0,
        "oi_change_4h_pct": round(oi_data.get("oi_change_4h_pct", 0), 2) if oi_data.get("current_oi", 0) > 0 else 0,
        "price_change_15m_pct": round(change_15m, 2),
        "price_change_45m_pct": round(change_45m, 2),
        "vol_ratio_15m": round(vol_ratio, 2),
        "detail": detail,
        "safe_to_enter": status in ["post_sweep", "normal"] and confidence >= 1,
    }


def batch_detect_sweep(symbols: list) -> list:
    """
    批量检测多个代币的清算状态
    返回排序后的结果：post_sweep > normal > sweeping > pre_sweep > hot
    """
    results = []
    priority = {"post_sweep": 0, "normal": 1, "sweeping": 2, "pre_sweep": 3, "hot": 4}

    for sym in symbols:
        result = detect_sweep_status(sym)
        result["priority"] = priority.get(result["status"], 9)
        results.append(result)
        time.sleep(0.15)

    results.sort(key=lambda x: (x["priority"], -x["confidence"]))

    print(f"\n[SweepDetector] {len(symbols)} 个代币扫描完成:")
    for r in results[:5]:
        icon = {"post_sweep": "✅", "sweeping": "⏳", "pre_sweep": "⚠️", "hot": "🚫", "normal": "⚪"}.get(r["status"], "?")
        print(f"  {icon} {r['symbol']:8s} | {r['status']:<12s} | FR:{r['funding_rate_pct']:.3f}% | 15m:{r['price_change_15m_pct']:+.1f}% | {r['detail']}")

    return results


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        symbols = [s.upper() for s in sys.argv[1:]]
    else:
        symbols = ["WCT", "MORPHO", "TRUST"]

    print(f"检测代币: {symbols}")
    batch_detect_sweep(symbols)
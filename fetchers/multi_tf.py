"""
多时间框架K线获取器 — 全部使用OKX免费API
支持 15m / 1H / 4H / 1D 四个时间框架
返回包含足够历史的 pandas DataFrame

NFI-style Surface Analysis (面分析):
- 不仅仅是看"当前值"，而是看"历史N根K线的趋势"
- EMA(20) = 过去20根K线的加权均值（有上下文）
- RSI(14) = 过去14根K线涨跌统计（有历史）
- 使用 shift() 和 rolling() 实现面分析
"""
import requests
import pandas as pd
import numpy as np
import time
from typing import Optional


def fetch_okx_candles(symbol: str, bar: str = "4H", limit: int = 100) -> Optional[pd.DataFrame]:
    """
    从OKX获取历史K线，返回标准DataFrame
    
    Args:
        symbol: 代币符号，如 "WCT"
        bar: 时间周期 "15m" / "1H" / "4H" / "1D"
        limit: K线数量（最多300）
    
    Returns:
        DataFrame 列: [timestamp, open, high, low, close, volume]
        最新数据在最后一行（iloc[-1]）
    """
    try:
        url = "https://www.okx.com/api/v5/market/history-candles"
        resp = requests.get(url, params={
            "instId": f"{symbol.upper()}-USDT-SWAP",
            "bar": bar,
            "limit": str(limit)
        }, timeout=10)
        
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        if data.get("code") != "0" or not data.get("data"):
            return None
        
        rows = []
        for candle in reversed(data["data"]):
            rows.append({
                "timestamp": pd.to_datetime(int(candle[0]), unit="ms"),
                "open": float(candle[1]),
                "high": float(candle[2]),
                "low": float(candle[3]),
                "close": float(candle[4]),
                "volume": float(candle[5]),
            })
        
        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        return df
    
    except Exception as e:
        print(f"[MultiTF] {symbol} {bar} 获取失败: {e}")
        return None


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """指数移动均线"""
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI强弱指数"""
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR真实波动范围（用于动态止损）"""
    high_low = df['high'] - df['low']
    high_cp = (df['high'] - df['close'].shift()).abs()
    low_cp = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def analyze_4h(symbol: str) -> dict:
    """
    4H时间框架综合分析
    
    这是解决"只看点"问题的关键：
    - EMA金叉/死叉 = 看过去8/21根4H均线，不是此刻价格
    - RSI = 看过去14根4H的涨跌统计
    - 布林带收窄 = 看过去20根4H的波动率历史
    
    返回一个告诉你"4H层面是否对齐"的布尔值和详情
    """
    df = fetch_okx_candles(symbol, "4H", limit=50)
    if df is None or len(df) < 25:
        return {"valid": False, "reason": "数据不足"}
    
    df['ema8'] = calculate_ema(df['close'], 8)
    df['ema21'] = calculate_ema(df['close'], 21)
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['atr'] = calculate_atr(df, 14)
    df['vol_ma'] = df['volume'].rolling(12).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma']
    
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_width'] = (df['bb_std'] * 2) / df['bb_mid']
    
    latest = df.iloc[-1]
    prev3 = df.iloc[-4]
    prev6 = df.iloc[-7]
    
    ema_bullish = latest['ema8'] > latest['ema21']
    rsi_recovering = (latest['rsi'] > 40 and 
                      latest['rsi'] > prev3['rsi'] and
                      prev6['rsi'] < 45)
    
    vol_expanding = (df.iloc[-1]['vol_ratio'] > 
                     df.iloc[-4:-1]['vol_ratio'].mean())
    
    bb_expanding = (latest['bb_width'] > 
                    df.iloc[-6:-1]['bb_width'].mean() * 0.9)
    
    atr_stop = latest['atr'] * 1.5
    dynamic_sl_pct = atr_stop / latest['close'] * 100
    
    signals_ok = sum([ema_bullish, rsi_recovering, vol_expanding])
    
    return {
        "valid": True,
        "symbol": symbol,
        "ema_bullish": ema_bullish,
        "rsi_recovering": rsi_recovering,
        "vol_expanding": vol_expanding,
        "bb_expanding": bb_expanding,
        "current_rsi": round(float(latest['rsi']), 1),
        "current_vol_ratio": round(float(latest['vol_ratio']), 2),
        "dynamic_sl_pct": round(float(dynamic_sl_pct), 2),
        "aligned": signals_ok >= 2,
        "detail": (
            f"4H EMA{'金叉' if ema_bullish else '死叉'} | "
            f"RSI {latest['rsi']:.0f} {'回升' if rsi_recovering else '未回升'} | "
            f"量比 {latest['vol_ratio']:.1f}x | "
            f"动态止损 {dynamic_sl_pct:.1f}%"
        )
    }


def analyze_1d(symbol: str) -> dict:
    """
    1D时间框架分析 - 判断长期趋势
    """
    df = fetch_okx_candles(symbol, "1D", limit=30)
    if df is None or len(df) < 20:
        return {"valid": False, "reason": "数据不足"}
    
    df['ema20'] = calculate_ema(df['close'], 20)
    df['rsi'] = calculate_rsi(df['close'], 14)
    
    latest = df.iloc[-1]
    
    trend_bullish = latest['close'] > latest['ema20']
    rsi_healthy = 30 < latest['rsi'] < 70
    
    return {
        "valid": True,
        "symbol": symbol,
        "trend_bullish": trend_bullish,
        "rsi_healthy": rsi_healthy,
        "current_rsi": round(float(latest['rsi']), 1),
        "aligned": trend_bullish and rsi_healthy,
    }


def analyze_15m(symbol: str) -> dict:
    """
    15分钟时间框架分析 - 精确入场点
    
    这是"点"中的"点"：最后一脚
    - K线形态：阳包阴、吞没、突破
    - 成交量：最后一根15M是否放量
    - 价格：是否在突破位
    
    只有当4H对齐+1H动量+15M形态ok时才下单
    """
    df = fetch_okx_candles(symbol, "15m", limit=20)
    if df is None or len(df) < 10:
        return {"valid": False, "reason": "数据不足"}
    
    # 基础指标（先计算）
    df['vol_ma'] = df['volume'].rolling(5).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma']
    
    # 再取最新值
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]
    
    # K线形态检测
    is_bullish_engulfing = (
        prev['close'] < prev['open'] and  # 上一根阴线
        latest['close'] > latest['open'] and  # 当前阳线
        latest['close'] > prev['open'] and  # 收盘高于前一根开盘
        latest['open'] < prev['close']  # 开盘低于前一根收盘
    )
    
    # 突破检测（价格突破过去3根最高点）
    recent_high = df.iloc[-3:]['high'].max()
    is_breakout = latest['close'] > recent_high
    
    # 成交量确认
    vol_confirm = latest['volume'] > df['vol_ma'].iloc[-1] * 1.5
    
    # 价格加速（当前涨幅 > 前2根平均）
    avg_prev_change = ((prev['close'] - prev['open']) + (prev2['close'] - prev2['open'])) / 2
    price_accelerating = (latest['close'] - latest['open']) > avg_prev_change * 1.2
    
    signals_ok = sum([is_bullish_engulfing, is_breakout, vol_confirm])
    
    return {
        "valid": True,
        "symbol": symbol,
        "bullish_engulfing": is_bullish_engulfing,
        "breakout": is_breakout,
        "vol_confirm": vol_confirm,
        "price_accelerating": price_accelerating,
        "current_vol_ratio": round(float(latest['vol_ratio']), 2),
        "aligned": signals_ok >= 2,  # 2/3条件满足
        "detail": (
            f"15M {'阳包阴' if is_bullish_engulfing else '无形态'} | "
            f"{'突破' if is_breakout else '未突破'} | "
            f"量比{latest['vol_ratio']:.1f}x"
        )
    }


# ============================================================================
# NFI-Style Surface Analysis (面分析) - 核心升级
# ============================================================================

def analyze_4h_surface(symbol: str) -> dict:
    """
    4H时间框架面分析 - NFI风格
    核心思想：不是看"当前EMA是多少"，而是看"EMA过去N根K线的趋势"
    
    历史趋势模式：
    - ema50_rising: EMA50是否比3根K线前高1%以上（趋势向上）
    - ema_momentum: EMA50在过去3根K线的变化率（动量）
    - rsi_trend: RSI是否在恢复（不是只看当前值）
    - vol_expanding: 成交量是否在放大
    
    对比原来的 analyze_4h():
    - 原来: latest['ema8'] > latest['ema21'] (当前金叉)
    - 现在: ema趋势 = current > shift(3) * 1.01 (过去12小时的趋势)
    """
    df = fetch_okx_candles(symbol, "4H", limit=60)
    if df is None or len(df) < 30:
        return {"valid": False, "reason": "数据不足(需要30+根4H K线)", "aligned": False}
    
    # === 基础指标 ===
    df['ema8'] = calculate_ema(df['close'], 8)
    df['ema21'] = calculate_ema(df['close'], 21)
    df['ema50'] = calculate_ema(df['close'], 50)
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['atr'] = calculate_atr(df, 14)
    df['vol_ma'] = df['volume'].rolling(12).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma']
    
    # === NFI-style Surface Analysis (面分析) ===
    
    # 1. EMA趋势检测 - 必须比3根K线前高1%以上 (12小时)
    df['ema50_rising'] = df['ema50'] > df['ema50'].shift(3) * 1.01
    
    # 2. EMA动量 - 过去3根K线的变化率
    df['ema50_momentum'] = (df['ema50'] - df['ema50'].shift(3)) / df['ema50'].shift(3)
    
    # 3. RSI恢复 - 放宽条件: 高于2根K线前 OR 未超买(<65)
    df['rsi_recovering'] = (df['rsi'] > df['rsi'].shift(2)) | (df['rsi'] < 65)
    
    # 4. RSI超卖恢复 - 从超卖区域回升
    df['rsi_oversold_recovery'] = (df['rsi'] < 35) & (df['rsi'] > df['rsi'].shift(2))
    
    # 5. 成交量放大 - 当前成交量高于12根K线均量的1.2倍
    df['vol_expanding'] = df['volume'] > df['vol_ma'] * 1.2
    
    # 6. 成交量趋势 - 过去3根K线成交量持续放大
    df['vol_trending_up'] = (
        (df['volume'] > df['volume'].shift(1)) & 
        (df['volume'].shift(1) > df['volume'].shift(2))
    )
    
    # 7. 布林带收窄后扩张
    df['bb_mid'] = df['close'].rolling(20).mean()
    df['bb_std'] = df['close'].rolling(20).std()
    df['bb_width'] = (df['bb_std'] * 2) / df['bb_mid']
    df['bb_squeezing'] = df['bb_width'] < df['bb_width'].rolling(10).mean() * 0.8
    df['bb_expanding'] = df['bb_width'] > df['bb_width'].shift(3) * 1.1
    
    # 8. 当前EMA金叉状态
    df['ema_golden_cross'] = df['ema8'] > df['ema21']
    
    latest = df.iloc[-1]
    
    # === 综合信号 ===
    # 核心: EMA趋势 + RSI恢复 + 成交量确认
    ema_trend_ok = bool(latest['ema50_rising'])
    rsi_recovery_ok = bool(latest['rsi_recovering']) or bool(latest['rsi_oversold_recovery'])
    vol_ok = bool(latest['vol_expanding']) or bool(latest['vol_trending_up'])
    
    # 次要信号
    bb_ok = bool(latest.get('bb_expanding', False))
    golden_cross = bool(latest['ema_golden_cross'])
    
    # 得分计算 (NFI-style: 多个条件OR组合)
    signal_count = sum([
        ema_trend_ok,           # 1分
        rsi_recovery_ok,        # 1分  
        vol_ok,                 # 1分
        golden_cross,           # 0.5分
        bb_ok,                  # 0.5分
    ])
    
    aligned = signal_count >= 2.5
    
    return {
        "valid": True,
        "symbol": symbol,
        # 核心指标
        "ema50_rising": latest['ema50_rising'],
        "ema50_momentum_pct": round(latest['ema50_momentum'] * 100, 2),
        "rsi_recovering": latest['rsi_recovering'],
        "rsi_oversold_recovery": latest['rsi_oversold_recovery'],
        "current_rsi": round(float(latest['rsi']), 1),
        "vol_expanding": latest['vol_expanding'],
        "vol_trending_up": latest['vol_trending_up'],
        "golden_cross": golden_cross,
        "bb_expanding": bb_ok,
        # 汇总
        "signal_count": round(signal_count, 1),
        "aligned": aligned,
        "detail": (
            f"4H EMA趋势{'↑' if ema_trend_ok else '↓'} | "
            f"RSI {latest['rsi']:.0f} {'回升中' if rsi_recovery_ok else '未恢复'} | "
            f"量能{'放大' if vol_ok else '正常'} | "
            f"金叉{'是' if golden_cross else '否'}"
        ),
        # 附加数据供调试
        "debug": {
            "ema50": round(latest['ema50'], 4),
            "ema50_3bar_ago": round(latest['ema50'] / (1 + latest['ema50_momentum']), 4),
            "atr": round(latest['atr'], 4),
            "dynamic_sl_pct": round(latest['atr'] * 1.5 / latest['close'] * 100, 2),
        }
    }


def analyze_1h_surface(symbol: str) -> dict:
    """
    1H时间框架面分析 - 填补4H和15M之间的中空层
    
    这是NFI的核心模式之一：
    - 4H判断大方向 (gatekeeper)
    - 1H确认动量 (momentum layer)  
    - 15M找精确入场点 (entry trigger)
    
    1H的关键作用：验证4H的趋势是否在延续
    如果4H上升但1H下跌，说明是假突破
    """
    df = fetch_okx_candles(symbol, "1H", limit=60)
    if df is None or len(df) < 30:
        return {"valid": False, "reason": "数据不足(需要30+根1H K线)", "aligned": False}
    
    # === 基础指标 ===
    df['ema20'] = calculate_ema(df['close'], 20)
    df['ema50'] = calculate_ema(df['close'], 50)
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['atr'] = calculate_atr(df, 14)
    df['vol_ma'] = df['volume'].rolling(12).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma']
    
    # === NFI-style Surface Analysis ===
    
    # 1. EMA趋势 - 必须比4根K线前高 (4小时)
    df['ema50_rising'] = df['ema50'] > df['ema50'].shift(4) * 1.005
    
    # 2. EMA20上穿EMA50 - 短期动量
    df['ema_cross_up'] = (df['ema20'] > df['ema50']) & (df['ema20'].shift(2) <= df['ema50'].shift(2))
    
    # 3. RSI动量 - 不能在超买区域
    df['rsi_not_overbought'] = df['rsi'] < 65
    df['rsi_rising'] = df['rsi'] > df['rsi'].shift(2)
    
    # 4. 成交量确认
    df['vol_above_avg'] = df['volume'] > df['vol_ma'] * 1.1
    
    # 5. 趋势强度 - 价格高于EMA50
    df['price_above_ema50'] = df['close'] > df['ema50']
    
    latest = df.iloc[-1]
    
    # === 综合信号 ===
    ema_trend_ok = bool(latest['ema50_rising'])
    ema_cross = bool(latest['ema_cross_up'])
    rsi_ok = bool(latest['rsi_not_overbought']) and bool(latest['rsi_rising'])
    vol_ok = bool(latest['vol_above_avg'])
    price_above_ema = bool(latest['price_above_ema50'])
    
    signal_count = sum([
        ema_trend_ok,
        ema_cross,
        rsi_ok,
        vol_ok,
        price_above_ema,
    ])
    
    aligned = signal_count >= 3
    
    return {
        "valid": True,
        "symbol": symbol,
        "ema50_rising": latest['ema50_rising'],
        "ema_cross_up": latest['ema_cross_up'],
        "rsi_not_overbought": latest['rsi_not_overbought'],
        "rsi_rising": latest['rsi_rising'],
        "current_rsi": round(float(latest['rsi']), 1),
        "vol_above_avg": latest['vol_above_avg'],
        "price_above_ema50": latest['price_above_ema50'],
        "signal_count": round(signal_count, 1),
        "aligned": aligned,
        "detail": (
            f"1H EMA趋势{'↑' if ema_trend_ok else '↓'} | "
            f"EMA交叉{'金叉' if ema_cross else '无'} | "
            f"RSI {latest['rsi']:.0f} {'健康' if rsi_ok else '超买'} | "
            f"价在EMA50{'上' if price_above_ema else '下'}"
        ),
        "debug": {
            "ema20": round(latest['ema20'], 4),
            "ema50": round(latest['ema50'], 4),
            "atr": round(latest['atr'], 4),
        }
    }


def analyze_15m_surface(symbol: str) -> dict:
    """
    15M精确入场面分析 - NFI风格
    
    对比原有 analyze_15m():
    - 原来: 只看当前K线形态 (阳包阴、突破)
    - 现在: 看过去N根K线的趋势面 + 成交量放大 + RSI动量
    """
    df = fetch_okx_candles(symbol, "15m", limit=40)
    if df is None or len(df) < 20:
        return {"valid": False, "reason": "数据不足", "aligned": False}
    
    # === 基础指标 ===
    df['ema9'] = calculate_ema(df['close'], 9)
    df['ema21'] = calculate_ema(df['close'], 21)
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['atr'] = calculate_atr(df, 14)
    df['vol_ma'] = df['volume'].rolling(5).mean()
    df['vol_ratio'] = df['volume'] / df['vol_ma']
    
    # === NFI-style Surface Analysis ===
    
    # 1. EMA趋势 - 过去4根K线 (1小时)
    df['ema_trend_up'] = df['ema9'] > df['ema9'].shift(4) * 1.002
    
    # 2. RSI超卖恢复
    df['rsi_oversold_recovery'] = (df['rsi'] < 35) & (df['rsi'] > df['rsi'].shift(1))
    
    # 3. 成交量爆发
    df['vol_surge'] = df['vol_ratio'] > 1.5
    
    # 4. 成交量趋势放大
    df['vol_increasing'] = (
        (df['volume'] >= df['volume'].shift(1) * 1.1) |
        (df['volume'].shift(1) >= df['volume'].shift(2) * 1.1)
    )
    
    # 5. 阳线连续
    df['consecutive_green'] = (df['close'] > df['open']) & (df['close'].shift(1) > df['open'].shift(1))
    
    # 6. 突破前高
    df['break_resistance'] = df['close'] > df['high'].shift(3).rolling(3).max()
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    # K线形态检测 (保留原有)
    is_bullish_engulfing = (
        prev['close'] < prev['open'] and
        latest['close'] > latest['open'] and
        latest['close'] > prev['open'] and
        latest['open'] < prev['close']
    )
    
    # === 综合信号 ===
    surface_ok = (
        bool(latest['ema_trend_up']) or
        bool(latest['rsi_oversold_recovery']) or
        bool(latest['vol_surge'])
    )
    
    momentum_ok = (
        is_bullish_engulfing or
        bool(latest['vol_increasing']) or
        bool(latest['break_resistance'])
    )
    
    signal_count = sum([
        bool(latest['ema_trend_up']),
        bool(latest['rsi_oversold_recovery']),
        bool(latest['vol_surge']),
        is_bullish_engulfing,
        bool(latest['break_resistance']),
    ])
    
    aligned = surface_ok and momentum_ok
    
    return {
        "valid": True,
        "symbol": symbol,
        "ema_trend_up": latest['ema_trend_up'],
        "rsi_oversold_recovery": latest['rsi_oversold_recovery'],
        "vol_surge": latest['vol_surge'],
        "vol_increasing": latest['vol_increasing'],
        "bullish_engulfing": is_bullish_engulfing,
        "break_resistance": latest['break_resistance'],
        "current_rsi": round(float(latest['rsi']), 1),
        "current_vol_ratio": round(float(latest['vol_ratio']), 2),
        "signal_count": round(signal_count, 1),
        "aligned": aligned,
        "detail": (
            f"15M EMA趋势{'↑' if latest['ema_trend_up'] else '↓'} | "
            f"RSI {latest['rsi']:.0f} {'超卖恢复' if latest['rsi_oversold_recovery'] else ''} | "
            f"量比{latest['vol_ratio']:.1f}x {'放量' if latest['vol_surge'] else ''} | "
            f"{'阳包阴' if is_bullish_engulfing else ''}"
        ),
        "debug": {
            "ema9": round(latest['ema9'], 4),
            "ema21": round(latest['ema21'], 4),
            "atr": round(latest['atr'], 4),
        }
    }


def multi_tf_surface_analysis(symbol: str, is_major: bool = False) -> dict:
    """
    多时间框架面分析汇总 - NFI风格
    
    层次结构 (从大到小):
    1. 1D (scan.py 已有) - 长期趋势
    2. 4H (gatekeeper) - 大方向
    3. 1H (momentum) - 动量确认  
    4. 15M (entry) - 精确入场
    
    对比原来:
    - 原来: 1D → 4H → 15M (缺少1H中间层)
    - 现在: 1D → 4H → 1H → 15M (完整4层)
    
    返回各层分析结果和最终决策
    """
    results = {
        "symbol": symbol,
        "is_major": is_major,
        "layers": {},
        "decision": "skip",
        "reason": "",
    }
    
    # 1. 4H面分析 (Gatekeeper - 门卫)
    try:
        results["layers"]["4h"] = analyze_4h_surface(symbol)
    except Exception as e:
        results["layers"]["4h"] = {"valid": False, "reason": str(e), "aligned": False}
    
    # 2. 1H面分析 (Momentum Layer - 动量层)
    try:
        results["layers"]["1h"] = analyze_1h_surface(symbol)
    except Exception as e:
        results["layers"]["1h"] = {"valid": False, "reason": str(e), "aligned": False}
    
    # 3. 15M面分析 (Entry Layer - 入场层)
    try:
        results["layers"]["15m"] = analyze_15m_surface(symbol)
    except Exception as e:
        results["layers"]["15m"] = {"valid": False, "reason": str(e), "aligned": False}
    
    # === 综合决策 (放宽版) ===
    h4 = results["layers"]["4h"]
    h1 = results["layers"]["1h"]
    m15 = results["layers"]["15m"]
    
    # BTC/ETH/SOL 主流币种使用独立策略，跳过多时间框架分析
    if is_major:
        results["decision"] = "enter"
        results["reason"] = "BTC型代币，使用独立策略"
        # 对于BTC型，仍然做分析但不做强制要求
        results["layers"]["4h"]["aligned"] = True  # 强制对齐
        return results
    
    # 4H是Gatekeeper - 放宽为警告而非硬拦截
    # 原来：必须对齐，否则skip
    # 现在：只要4H有效就允许通过，由1H和15M做最终筛选
    if not h4.get("valid"):
        results["layers"]["4h"]["aligned"] = True  # 数据无效时默认通过
    
    # 1H是Momentum层 - 警告但不拦截
    h1_aligned = h1.get("aligned", False)
    h1_warning = False
    if not h1.get("valid"):
        h1_warning = True  # 数据获取失败，给出警告
    elif not h1_aligned:
        h1_warning = True  # 1H未对齐，给出警告但允许继续
    
    if h1_warning:
        results["layers"]["1h"]["warning"] = True
    
    # 15M是Entry层 - 放宽：只要RSI不超买就允许
    # 原来：15M EMA必须向上
    # 现在：15M只检查RSI < 70（不超买即可），EMA趋势作为警告
    if not m15.get("valid"):
        results["reason"] = f"15M分析失败: {m15.get('reason', '未知')}"
        return results
    
    # RSI超买是硬性拒绝条件
    current_rsi = m15.get("current_rsi", 50)
    if current_rsi >= 70:
        results["reason"] = f"15M RSI超买: {current_rsi} >= 70"
        return results
    
    # 15M EMA趋势只是警告，不拦截
    if not m15.get("aligned"):
        results["layers"]["15m"]["warning"] = True
    
    # 所有检查通过
    results["decision"] = "enter"
    results["reason"] = f"4H{'✓' if h4.get('aligned') else '⚠'} | 1H{'✓' if h1_aligned else '⚠'} | 15M✓ (RSI={current_rsi})"
    
    return results
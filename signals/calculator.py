"""
信号计算器
将原始 fetcher 数据转换为7个量化信号
新7信号框架 (2026-06):
1. 价格在整数关口下方横盘 3~7 天
2. 资金费率从负/零开始转正且持续上升
3. OI 在价格横盘期间悄悄增加
4. 某一天出现 3x 以上放量但价格未大涨
5. DexScreener 买卖比 >1.2 且持续多日
6. BTC.D 处于下降通道
7. Binance 新增了该币的永续合约
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List


class SignalCalculator:
    """
    信号计算器
    
    将 fetcher 输出的原始数据转换为标准化的信号 dict
    """
    
    # =========================================================================
    # Signal 1: 价格在整数关口下方横盘 3~7 天
    # =========================================================================
    def calc_signal_1_integer_consolidation(self, price_data: dict, kline_df: pd.DataFrame) -> dict:
        """
        Signal 1: 价格在整数关口下方横盘 3~7 天
        
        逻辑：
        1. 找到最近整数关口（$0.01, $0.05, $0.1, $0.5, $1, $5, $10...）
        2. 计算当前价格在关口下方多少%
        3. 检查最近3-7天价格是否在关口下方横盘
        """
        current_price = price_data.get("price", 0)
        
        if current_price <= 0:
            return {
                "triggered": False,
                "nearest_level": 0.0,
                "distance_pct": 0.0,
                "consolidation_days": 0,
                "detail": "价格数据缺失"
            }
        
        # 找到最近整数关口（包含小价格心理关口）
        tiers = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 500, 1000, 5000]
        nearest_level = min([t for t in tiers if t >= current_price], default=tiers[-1])
        if nearest_level == current_price:
            nearest_level = min([t for t in tiers if t > current_price], default=tiers[-1])
        
        distance_pct = ((nearest_level - current_price) / nearest_level) * 100
        
        # 检查是否接近关键心理关口（小于15%）
        is_near_key_level = distance_pct > 0 and distance_pct < 15
        
        # 检查横盘天数（用K线数据）
        consolidation_days = 0
        consolidation_14d = False
        consolidation_7d = False
        
        # 安全检查：kline_df 不能为 None
        if kline_df is None:
            kline_df = pd.DataFrame()
        
        if not kline_df.empty and len(kline_df) >= 3:
            # 使用全部K线数据检测横盘（最多30天）
            all_prices = kline_df["close"].tolist()
            
            # 检查最近N天价格是否都在同一整数关口下方
            for p in reversed(all_prices):
                if p < nearest_level and (nearest_level - p) / nearest_level < 0.15:
                    consolidation_days += 1
                else:
                    break
            
            # 增强检测：横盘 > 2周 (14天)
            consolidation_14d = consolidation_days >= 14 and distance_pct > 0 and distance_pct < 15
            # 3-7天标准检测
            consolidation_7d = 3 <= consolidation_days <= 7 and distance_pct > 0 and distance_pct < 15
            
            # 触发条件：3-7天 或 超过2周
            triggered = consolidation_7d or consolidation_14d
        else:
            # 简化版：基于价格变化趋势判断
            change_7d = price_data.get("change_7d_pct", 0)
            if abs(change_7d) < 10 and distance_pct > 0 and distance_pct < 15:
                consolidation_days = 3
                consolidation_7d = True
                triggered = True
            else:
                consolidation_days = 0
                triggered = False
        
        if consolidation_14d:
            detail = f"价格 ${current_price:.4f} 在整数关口 ${nearest_level} 下方横盘 {consolidation_days} 天(>2周)，距关口 {distance_pct:.1f}% ⭐高确信度"
            triggered_reason = "14d_consolidation"
        elif consolidation_7d:
            detail = f"价格 ${current_price:.4f} 在整数关口 ${nearest_level} 下方横盘 {consolidation_days} 天，距关口 {distance_pct:.1f}%"
            triggered_reason = "3_7d_consolidation"
        elif consolidation_days > 0:
            detail = f"价格略有横盘迹象 ({consolidation_days}天)，但未达3天阈值"
            triggered_reason = "insufficient_days"
        else:
            detail = f"价格 ${current_price:.4f} 距最近整数关口 ${nearest_level} 约 {distance_pct:.1f}%，无明显横盘"
            triggered_reason = "no_consolidation"
        
        return {
            "triggered": triggered,
            "triggered_reason": triggered_reason,
            "nearest_level": nearest_level,
            "distance_pct": round(distance_pct, 2),
            "consolidation_days": consolidation_days,
            "consolidation_14d": consolidation_14d,
            "consolidation_7d": consolidation_7d,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 2: 资金费率从负/零开始转正且持续上升
    # =========================================================================
    def calc_signal_2_funding_turn_positive(self, funding_data: dict) -> dict:
        """
        Signal 2: 资金费率从负/零开始转正且持续上升
        
        逻辑：
        1. 取最近21个8H周期费率（约7天）
        2. 判断7天前的平均费率 vs 最近7天的平均费率
        3. 要求：之前为负/零，现在转正，且呈上升趋势
        """
        # 安全检查
        if funding_data is None:
            funding_data = {}
        
        if not isinstance(funding_data, dict):
            return {
                "triggered": False,
                "latest_rate": 0.0,
                "previous_avg": 0.0,
                "current_avg": 0.0,
                "trend": "unknown",
                "detail": "资金费率数据格式错误"
            }
        
        rates = funding_data.get("rates", [])
        
        # 安全检查：rates 必须是 list
        if not isinstance(rates, list):
            return {
                "triggered": False,
                "latest_rate": 0.0,
                "previous_avg": 0.0,
                "current_avg": 0.0,
                "trend": "unknown",
                "detail": "资金费率数据格式错误"
            }
        
        if not rates or len(rates) < 7:
            return {
                "triggered": False,
                "latest_rate": 0.0,
                "previous_avg": 0.0,
                "current_avg": 0.0,
                "trend": "unknown",
                "detail": "资金费率数据不足"
            }
        
        # 取最近21个周期（约7天）
        recent_rates = rates[-21:] if len(rates) >= 21 else rates
        
        # 分割点：前7个 vs 后7个
        split_idx = max(0, len(recent_rates) - 7)
        
        older_rates = recent_rates[:split_idx] if split_idx > 0 else recent_rates[:3]
        current_rates = recent_rates[split_idx:]
        
        older_avg = sum(older_rates) / len(older_rates) if older_rates else 0.0
        current_avg = sum(current_rates) / len(current_rates) if current_rates else 0.0
        
        latest_rate = recent_rates[-1]
        
        # 判断趋势强度
        if len(current_rates) >= 3:
            # 计算趋势斜率 (线性回归)
            x = list(range(len(current_rates)))
            y = current_rates
            n = len(x)
            sum_x = sum(x)
            sum_y = sum(y)
            sum_xy = sum(x[i] * y[i] for i in range(n))
            sum_xx = sum(x[i] * x[i] for i in range(n))
            
            if n * sum_xx - sum_x * sum_x != 0:
                slope = (n * sum_xy - sum_x * sum_y) / (n * sum_xx - sum_x * sum_x)
            else:
                slope = 0
            
            if slope > 0.0001:
                trend = "rising_strong"
            elif slope > 0:
                trend = "rising"
            elif slope < -0.0001:
                trend = "falling_strong"
            elif slope < 0:
                trend = "falling"
            else:
                trend = "flat"
            
            trend_slope = slope * 10000  # 放大便于阅读
        else:
            trend = "unknown"
            trend_slope = 0
        
        # 转换为百分比
        latest_pct = latest_rate * 100
        older_pct = older_avg * 100
        current_pct = current_avg * 100
        
        # 触发条件：之前为负/零，现在转正，且趋势向上（强置信：slope > 0.0005）
        triggered = older_avg <= 0 and current_avg > 0 and trend in ["rising", "rising_strong"]
        triggered_strong = older_avg <= 0 and current_avg > 0.0005 and trend == "rising_strong"
        
        if triggered_strong:
            detail = f"资金费率从负转正: {older_pct:.3f}% → {current_pct:.3f}%，最新 {latest_pct:.3f}%，强上升趋势 ⭐高确信度"
        elif triggered:
            detail = f"资金费率从负转正: {older_pct:.3f}% → {current_pct:.3f}%，最新 {latest_pct:.3f}%，上升趋势确认"
        elif current_avg > 0:
            detail = f"资金费率为正 ({current_pct:.3f}%)，但趋势 {trend}，需更多确认"
        else:
            detail = f"资金费率仍为负 ({current_pct:.3f}%)，未出现转正信号"
        
        # 新增返回值
        triggered_reason = "none"
        if triggered_strong:
            triggered_reason = "strong_confirmed"
        elif triggered:
            triggered_reason = "confirmed"
        elif current_avg > 0:
            triggered_reason = "positive_but_weak"
        
        return {
            "triggered": triggered,
            "triggered_strong": triggered_strong,
            "triggered_reason": triggered_reason,
            "latest_rate": round(latest_pct, 4),
            "previous_avg": round(older_pct, 4),
            "current_avg": round(current_pct, 4),
            "trend": trend,
            "trend_slope": round(trend_slope, 4),
            "detail": detail
        }
    
    # =========================================================================
    # Signal 3: OI 在价格横盘期间悄悄增加
    # =========================================================================
    def calc_signal_3_oi_accumulation(self, oi_data: dict, price_data: dict) -> dict:
        """
        Signal 3: OI 在价格横盘期间悄悄增加
        
        逻辑：
        1. OI 7日增长 > 10%
        2. 价格7日变化 < 10%（横盘）
        3. 乖离度 = OI变化 - 价格变化 > 10%
        增强：同时检查OI历史序列趋势
        """
        oi_change_pct = oi_data.get("oi_change_7d_pct", 0.0)
        price_change_pct = price_data.get("change_7d_pct", 0.0)
        
        # 获取OI历史序列（如果有）
        oi_series = oi_data.get("oi_series", [])
        
        # 分析OI趋势
        oi_trend = "unknown"
        oi_trend_strength = 0
        
        if len(oi_series) >= 5:
            # 计算最近5个周期的OI趋势
            recent_oi = oi_series[-5:]
            if recent_oi[-1] > recent_oi[0] * 1.1:
                oi_trend = "increasing"
                oi_trend_strength = (recent_oi[-1] / recent_oi[0] - 1) * 100
            elif recent_oi[-1] < recent_oi[0] * 0.9:
                oi_trend = "decreasing"
                oi_trend_strength = (1 - recent_oi[-1] / recent_oi[0]) * 100
            else:
                oi_trend = "stable"
        
        divergence = oi_change_pct - price_change_pct
        
        # 触发条件增强
        triggered_basic = oi_change_pct > 10 and abs(price_change_pct) < 10 and divergence > 10
        triggered_trend = oi_change_pct > 5 and oi_trend == "increasing" and abs(price_change_pct) < 15
        triggered = triggered_basic or triggered_trend
        
        # 高确信度：既有OI增长，趋势又向上
        triggered_strong = triggered_basic and oi_trend == "increasing"
        
        if triggered_strong:
            detail = f"OI 7日增长 {oi_change_pct:.1f}%，趋势向上 {oi_trend}，价格仅变化 {price_change_pct:.1f}%，乖离度 {divergence:.1f}% ⭐高确信度"
        elif triggered:
            detail = f"OI 7日增长 {oi_change_pct:.1f}%，价格仅变化 {price_change_pct:.1f}%，乖离度 {divergence:.1f}%，疑似庄家暗中吸筹"
        elif oi_change_pct > 5:
            detail = f"OI 7日增长 {oi_change_pct:.1f}%，价格变化 {price_change_pct:.1f}%，有一定积累迹象"
        else:
            detail = f"OI变化 {oi_change_pct:.1f}%，价格变化 {price_change_pct:.1f}%，无明显吸筹特征"
        
        return {
            "triggered": triggered,
            "triggered_strong": triggered_strong,
            "oi_change_7d_pct": round(oi_change_pct, 2),
            "price_change_7d_pct": round(price_change_pct, 2),
            "divergence": round(divergence, 2),
            "oi_trend": oi_trend,
            "oi_trend_strength": round(oi_trend_strength, 2),
            "detail": detail
        }
    
    # =========================================================================
    # Signal 4: 某一天出现 3x 以上放量但价格未大涨
    # =========================================================================
    def calc_signal_4_volume_spike(self, kline_df: pd.DataFrame) -> dict:
        """
        Signal 4: 某一天出现 3x 以上放量但价格未大涨
        
        逻辑：
        1. 计算20日平均成交量
        2. 找到成交量 >= 3倍均量的交易日
        3. 检查当日价格涨幅 < 5%
        """
        if kline_df.empty or len(kline_df) < 20:
            return {
                "triggered": False,
                "volume_ratio": 0.0,
                "price_change_on_spike_day": 0.0,
                "spike_date": None,
                "detail": "K线数据不足，无法判断成交量"
            }
        
        # 计算20日均量
        df = kline_df.copy()
        df["volume_ma20"] = df["volume"].rolling(window=20).mean()
        
        # 找放量日
        df["volume_ratio"] = df["volume"] / df["volume_ma20"]
        
        # 找到最大的放量日
        max_vol_row = df.loc[df["volume_ratio"].idxmax()]
        
        volume_ratio = max_vol_row["volume_ratio"]
        price_change = max_vol_row.get("price_change_pct", 0.0)
        
        if pd.isna(price_change):
            # 计算当日涨跌幅
            if len(df) > 1:
                idx = df.index.get_loc(max_vol_row.name)
                if idx > 0:
                    prev_close = df.iloc[idx - 1]["close"]
                    price_change = ((max_vol_row["close"] - prev_close) / prev_close) * 100
                else:
                    price_change = 0.0
            else:
                price_change = 0.0
        
        # 触发条件：放量>=2倍，价格涨幅<8%（优化阈值提高灵敏度）
        # 增强：区分 2x/3x/5x/10x/20x 级别
        triggered_2x = volume_ratio >= 2.0 and price_change < 8.0
        triggered_3x = volume_ratio >= 3.0 and price_change < 5.0
        triggered_5x = volume_ratio >= 5.0 and price_change < 8.0
        triggered_10x = volume_ratio >= 10.0 and price_change < 10.0
        triggered_20x = volume_ratio >= 20.0
        
        triggered = triggered_2x or triggered_3x or triggered_5x or triggered_10x or triggered_20x
        
        # 确定放量级别
        if triggered_20x:
            spike_level = "20x+"
        elif triggered_10x:
            spike_level = "10x+"
        elif triggered_5x:
            spike_level = "5x+"
        elif triggered_3x:
            spike_level = "3x"
        elif triggered_2x:
            spike_level = "2x"
        else:
            spike_level = "none"
        
        spike_date = str(max_vol_row.name)[:10] if hasattr(max_vol_row.name, 'strftime') else str(max_vol_row.name)[:10]
        
        if triggered_20x:
            detail = f"成交量突增 {volume_ratio:.1f}倍 ({spike_level}级别，{spike_date})，价格仅 {price_change:.1f}% ⭐强烈吸筹信号"
        elif triggered_10x:
            detail = f"成交量突增 {volume_ratio:.1f}倍 ({spike_level}级别，{spike_date})，价格仅 {price_change:.1f}% 强烈吸筹"
        elif triggered:
            detail = f"成交量突增 {volume_ratio:.1f}倍（{spike_date}），但价格仅 {price_change:.1f}%，量价背离疑似吸筹"
        elif volume_ratio >= 3.0:
            detail = f"成交量突增 {volume_ratio:.1f}倍，但价格涨幅 {price_change:.1f}%，疑似真突破"
        else:
            detail = f"最大成交量比为 {volume_ratio:.1f}倍，无明显放量信号"
        
        return {
            "triggered": triggered,
            "spike_level": spike_level,
            "volume_ratio": round(volume_ratio, 2),
            "price_change_on_spike_day": round(price_change, 2),
            "spike_date": spike_date,
            "triggered_3x": triggered_3x,
            "triggered_5x": triggered_5x,
            "triggered_10x": triggered_10x,
            "triggered_20x": triggered_20x,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 5: DexScreener 买卖比 >1.2 且持续多日
    # =========================================================================
    def calc_signal_5_dex_buy_pressure(self, dex_data: dict) -> dict:
        """
        Signal 5: DexScreener 买卖比 >1.2 且持续多日
        
        Bug 3 修复：
        - 门槛必须是严格 >= 1.2
        - 加强度分级：strong/moderate/weak
        """
        buy_sell_ratio = dex_data.get("buy_sell_ratio", 0.0)
        liquidity_usd = dex_data.get("liquidity_usd", 0.0)
        
        # 获取历史买卖比（如果有）
        buy_history = dex_data.get("buy_history", [])
        
        # 分析持续性
        if len(buy_history) >= 3:
            # 计算连续 >= 1.2 的天数
            consecutive_days = 0
            max_consecutive = 0
            for r in buy_history:
                if r >= 1.2:
                    consecutive_days += 1
                    max_consecutive = max(max_consecutive, consecutive_days)
                else:
                    consecutive_days = 0
            
            sustained_days = max_consecutive
        else:
            sustained_days = 0
        
        # 强度分级
        if buy_sell_ratio >= 2.0 and liquidity_usd >= 100000:
            strength = "strong"
        elif buy_sell_ratio >= 1.5:
            strength = "moderate"
        elif buy_sell_ratio >= 1.2:
            strength = "weak"
        else:
            strength = "none"
        
        if liquidity_usd < 50000:
            triggered = False
            detail = f"DEX流动性仅 ${liquidity_usd:,.0f}，低于$5万门槛，信号不可靠"
        elif buy_sell_ratio >= 1.2 and sustained_days >= 3 and strength == "strong":
            triggered = True
            detail = f"DEX买压持续 {sustained_days} 天，买卖比 {buy_sell_ratio:.2f} >= 2.0，流动性 ${liquidity_usd:,.0f} ⭐高确信度"
        elif buy_sell_ratio >= 1.2 and sustained_days >= 3:
            triggered = True
            detail = f"DEX买压持续 {sustained_days} 天，买卖比 {buy_sell_ratio:.2f} >= 1.2，流动性 ${liquidity_usd:,.0f} 确认"
        elif buy_sell_ratio >= 1.2:
            triggered = True
            detail = f"DEX买卖比 {buy_sell_ratio:.2f} >= 1.2，流动性 ${liquidity_usd:,.0f}，买压明显"
        else:
            triggered = False
            detail = f"DEX买卖比 {buy_sell_ratio:.2f}，未达到1.2阈值"
        
        return {
            "triggered": triggered,
            "strength": strength,
            "buy_sell_ratio": round(buy_sell_ratio, 2),
            "liquidity_usd": liquidity_usd,
            "sustained_days": sustained_days,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 6: BTC.D 处于下降通道
    # =========================================================================
    def calc_signal_6_btcd_downtrend(self, btcd_df: pd.DataFrame) -> dict:
        """
        Signal 6: BTC.D 处于下降通道
        
        逻辑：
        1. 取最近14天的BTC.D数据
        2. 计算线性回归斜率
        3. 斜率为负且绝对值 > 0.5% / 天 → 下降通道
        """
        if btcd_df.empty or len(btcd_df) < 7:
            return {
                "triggered": False,
                "trend_slope": 0.0,
                "current_btcd": 0.0,
                "change_7d": 0.0,
                "detail": "BTC.D 数据不足，无法判断趋势"
            }
        
        df = btcd_df.copy()
        df = df.tail(14)
        
        if "close" not in df.columns:
            return {
                "triggered": False,
                "trend_slope": 0.0,
                "current_btcd": 0.0,
                "change_7d": 0.0,
                "detail": "BTC.D 数据格式错误"
            }
        
        current_btcd = df["close"].iloc[-1]
        first_btcd = df["close"].iloc[0]
        
        # 计算7日变化
        change_7d = ((current_btcd - first_btcd) / first_btcd) * 100 if first_btcd > 0 else 0.0
        
        # 线性回归计算斜率
        y = df["close"].values
        x = np.arange(len(y))
        
        if len(y) > 1:
            slope = np.polyfit(x, y, 1)[0]
        else:
            slope = 0.0
        
        # 斜率转换为日均变化百分比
        daily_change_pct = (slope / current_btcd) * 100 if current_btcd > 0 else 0.0
        
        # 触发条件：斜率为负，且7日跌幅 > 1%
        triggered = daily_change_pct < -0.1 and change_7d < -1.0
        
        # 增强：检查是否处于长期下降通道（30天）
        triggered_30d = False
        if len(btcd_df) >= 30:
            df_30d = btcd_df.copy().tail(30)
            if "close" in df_30d.columns and len(df_30d) >= 20:
                slope_30d = np.polyfit(np.arange(len(df_30d)), df_30d["close"].values, 1)[0]
                daily_change_30d = (slope_30d / df_30d["close"].iloc[-1]) * 100 if df_30d["close"].iloc[-1] > 0 else 0
                triggered_30d = daily_change_30d < -0.2
        
        if triggered or triggered_30d:
            detail = f"BTC.D 当前 {current_btcd:.1f}，7日下跌 {abs(change_7d):.1f}%，处于下降通道 ⭐高确信度"
        elif change_7d < 0:
            detail = f"BTC.D 当前 {current_btcd:.1f}，7日变化 {change_7d:.1f}%，略有下行趋势"
        else:
            detail = f"BTC.D 当前 {current_btcd:.1f}，7日变化 {change_7d:.1f}%，无下降趋势"
        
        return {
            "triggered": triggered or triggered_30d,
            "triggered_reason": "7d_downtrend" if triggered else ("30d_downtrend" if triggered_30d else "none"),
            "trend_slope": round(daily_change_pct, 3),
            "current_btcd": round(current_btcd, 2),
            "change_7d": round(change_7d, 2),
            "change_30d": round(((btcd_df["close"].iloc[-1] - btcd_df["close"].iloc[0]) / btcd_df["close"].iloc[0]) * 100, 2) if len(btcd_df) >= 30 else None,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 6b: BTC 相对强度 (大盘跌3%币跌1%)
    # =========================================================================
    def calc_signal_6b_btc_relative_strength(self, token_kline_df: pd.DataFrame, btcd_df: pd.DataFrame) -> dict:
        """
        Signal 6b: BTC 相对强度
        
        逻辑：
        1. 对比代币 vs BTC 最近7日涨跌幅
        2. 大盘跌时币跌更少 = 相对强度强
        3. 大盘涨时币涨更多 = 相对强度强
        """
        if token_kline_df.empty or len(token_kline_df) < 7:
            return {
                "triggered": False,
                "token_change_7d": 0.0,
                "btc_change_7d": 0.0,
                "relative_strength": 0.0,
                "detail": "K线数据不足，无法计算相对强度"
            }
        
        # 代币7日变化
        token_start = token_kline_df["close"].iloc[0]
        token_end = token_kline_df["close"].iloc[-1]
        token_change_7d = ((token_end - token_start) / token_start) * 100 if token_start > 0 else 0
        
        # BTC 7日变化
        btc_change_7d = 0.0
        if btcd_df is not None and not btcd_df.empty and len(btcd_df) >= 7:
            btc_start = btcd_df["close"].iloc[0]
            if btc_start > 0:
                btc_change_7d = ((btcd_df["close"].iloc[-1] - btc_start) / btc_start) * 100
        
        # 相对强度 = 代币变化 - BTC变化
        relative_strength = token_change_7d - btc_change_7d
        
        # 触发条件
        # 1. 大盘下跌时，币跌更少（相对跌幅 > 1%）
        # 2. 大盘上涨时，币涨更多（相对涨幅 > 1%）
        triggered = (btc_change_7d < -3 and relative_strength > 1) or \
                    (btc_change_7d > 3 and relative_strength > 1) or \
                    (relative_strength > 2 and abs(btc_change_7d) > 1)
        
        if triggered:
            if relative_strength > 0:
                detail = f"相对BTC强势: 代币 {token_change_7d:+.1f}% vs BTC {btc_change_7d:+.1f}%，相对强度 +{relative_strength:.1f}% ⭐有资金托盘"
            else:
                detail = f"相对BTC弱势: 代币 {token_change_7d:+.1f}% vs BTC {btc_change_7d:+.1f}%"
        else:
            detail = f"相对BTC: 代币 {token_change_7d:+.1f}% vs BTC {btc_change_7d:+.1f}%，相对强度 {relative_strength:+.1f}%"
        
        return {
            "triggered": triggered,
            "token_change_7d": round(token_change_7d, 2),
            "btc_change_7d": round(btc_change_7d, 2),
            "relative_strength": round(relative_strength, 2),
            "detail": detail
        }
    
    # =========================================================================
    # Signal 7: Binance 新增了该币的永续合约
    # =========================================================================
    def calc_signal_7_new_futures(self, futures_data: dict) -> dict:
        """
        Signal 7: Binance 新增了该币的永续合约
        
        Bug 2 修复：只有上线 180 天内才算新合约信号
        - 上线 0-30天: 强信号
        - 上线 31-90天: 中信号  
        - 上线 91-180天: 弱信号
        - 上线 >180天: 不触发
        """
        has_futures = futures_data.get("has_futures", False)
        days = futures_data.get("days_since_listing", -1)
        
        if not has_futures or days < 0:
            return {
                "triggered": False,
                "has_futures": False,
                "days_since_listing": -1,
                "recency_score": 0.0,
                "detail": "该代币尚未在 Binance 上线永续合约"
            }
        
        # 只有上线 180 天内才算新合约信号
        if days > 180:
            return {
                "triggered": False,
                "has_futures": has_futures,
                "days_since_listing": days,
                "recency_score": 0.0,
                "detail": f"合约上市已{days}天，超过180天不属于新合约"
            }
        
        if days <= 30:
            recency_score = 1.0
            triggered = True
            detail = f"Binance 永续合约新上线 {days} 天内 ⭐ 高确信度"
        elif days <= 90:
            recency_score = 0.7
            triggered = True
            detail = f"Binance 永续合约上线 {days} 天，中等新鲜度"
        else:
            recency_score = 0.3
            triggered = True
            detail = f"Binance 永续合约上线 {days} 天，弱信号（91-180天）"
        
        return {
            "triggered": triggered,
            "has_futures": has_futures,
            "days_since_listing": days,
            "recency_score": recency_score,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 8: 洗盘测试期（假拉升后快速跌回）
    # =========================================================================
    def calc_signal_8_wash_test(self, kline_df: pd.DataFrame, funding_data: dict) -> dict:
        """
        Signal 8: 洗盘测试期（假拉升后快速跌回）
        
        庄家特征：用一笔资金迅速把价格拉升 30%~80%，然后快速撤退回到原点
        目的：测试抛压，甩掉跟风盘
        
        逻辑：
        1. 找到30天内出现过 30%+ 涨幅的交易日
        2. 检查之后7天内价格是否跌回超过50%的涨幅
        3. Funding Rate 短暂飙升后归零
        """
        if kline_df.empty or len(kline_df) < 14:
            return {
                "triggered": False,
                "fake_pump_pct": 0.0,
                "retrace_pct": 0.0,
                "funding_spike": False,
                "detail": "K线数据不足，无法判断洗盘特征"
            }
        
        df = kline_df.copy().tail(30)
        
        # 计算每日涨跌幅
        df["pct_change"] = df["close"].pct_change() * 100
        
        # 找假拉升日（涨幅30%-80%）
        fake_pump_days = df[(df["pct_change"] >= 30) & (df["pct_change"] <= 80)]
        
        triggered = False
        fake_pump_pct = 0.0
        retrace_pct = 0.0
        
        for idx, row in fake_pump_days.iterrows():
            pump_day_idx = df.index.get_loc(idx)
            pump_price = row["close"]
            pump_day_change = row["pct_change"]
            
            # 检查之后7天的走势
            if pump_day_idx + 7 <= len(df):
                future_prices = df.iloc[pump_day_idx:pump_day_idx + 7]["close"]
                
                # 最低点
                min_price = future_prices.min()
                # 7天后价格
                day7_price = future_prices.iloc[-1]
                
                # 回撤幅度
                if pump_price > min_price:
                    retrace = (pump_price - min_price) / (pump_price - df.iloc[pump_day_idx - 1]["close"]) * 100 if pump_price > df.iloc[pump_day_idx - 1]["close"] else 0
                else:
                    retrace = 0
                
                # 触发条件：回撤超过50%的涨幅
                if retrace > 50:
                    triggered = True
                    fake_pump_pct = pump_day_change
                    retrace_pct = retrace
                    break
        
        # 检查资金费率是否短暂飙升后归零
        funding_spike = False
        rates = funding_data.get("rates", [])
        if len(rates) >= 7:
            # 最近7天有费率飙升到 >0.05% 然后归零
            recent = rates[-7:]
            for i in range(len(recent) - 1):
                if recent[i] > 0.0005 and recent[i + 1] < 0.0001:
                    funding_spike = True
                    break
        
        if triggered and funding_spike:
            detail = f"洗盘测试期: 30天内出现 {fake_pump_pct:.0f}% 假拉升后跌回 {retrace_pct:.0f}%，资金费率短暂飙升后归零 ⭐高确信度"
        elif triggered:
            detail = f"洗盘测试期: 30天内出现 {fake_pump_pct:.0f}% 假拉升后跌回 {retrace_pct:.0f}%"
        else:
            detail = "未检测到洗盘测试期特征（假拉升后快速跌回）"
        
        return {
            "triggered": triggered,
            "fake_pump_pct": round(fake_pump_pct, 2),
            "retrace_pct": round(retrace_pct, 2),
            "funding_spike": funding_spike,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 9: 社交媒体情绪（对接 social_api）
    # =========================================================================
    def calc_signal_9_social_sentiment(self, social_data: dict = None, symbol: str = None) -> dict:
        """
        Signal 9: 社交媒体情绪
        
        对接 social_api:
        - Twitter/X 提及量趋势
        - Telegram/Discord 活跃度
        - 搜索热度变化
        """
        # 如果没有传入数据，尝试获取
        if social_data is None and symbol:
            try:
                from fetchers.social_api import fetch_social_sentiment
                social_data = fetch_social_sentiment(symbol)
            except Exception:
                pass
        
        if social_data is None:
            social_data = {}
        
        # 从 social_data 提取情绪指标
        sentiment_score = 0.0
        mention_trend = 0.0
        
        # 尝试从多个来源获取情绪
        if "sentiment_score" in social_data:
            sentiment_score = social_data.get("sentiment_score", 0)
        elif "reddit" in social_data:
            reddit = social_data.get("reddit", {})
            sentiment_score = reddit.get("sentiment_score", 0)
        
        if "mention_trend" in social_data:
            mention_trend = social_data.get("mention_trend", 0)
        
        # 触发条件
        triggered = False
        if sentiment_score > 0.6 or mention_trend > 50:
            triggered = True
            detail = f"社交情绪升温: 情绪得分 {sentiment_score:.2f}, 提及量趋势 +{mention_trend:.0f}%"
        elif sentiment_score > 0 or mention_trend > 0:
            detail = f"社交情绪中性: 情绪得分 {sentiment_score:.2f}, 提及量趋势 +{mention_trend:.0f}%"
        else:
            detail = "社交情绪数据占位（待对接 social_api）"
        
        return {
            "triggered": triggered,
            "sentiment_score": sentiment_score,
            "mention_trend": mention_trend,
            "detail": detail
        }
    
    # =========================================================================
    # 离场规则检查
    # =========================================================================
    def check_exit_rules(self, funding_data: dict) -> dict:
        """
        离场铁律：
        - Funding Rate > 0.5% → 减仓
        - Funding Rate > 1% → 清仓
        
        返回离场信号级别
        """
        rates = funding_data.get("rates", [])
        
        if not rates:
            return {"action": "HOLD", "funding_rate": 0.0, "detail": "无资金费率数据", "latest_rate": 0}
        
        latest_rate = rates[-1] * 100  # 转为百分比
        
        if latest_rate > 1.0:
            action = "EXIT"
            detail = f"资金费率 {latest_rate:.3f}% > 1%，触发清仓铁律"
        elif latest_rate > 0.5:
            action = "REDUCE"
            detail = f"资金费率 {latest_rate:.3f}% > 0.5%，触发减仓警告"
        else:
            action = "HOLD"
            detail = f"资金费率 {latest_rate:.3f}% 正常，继续持有"
        
        return {
            "action": action,
            "funding_rate": round(latest_rate, 4),
            "detail": detail,
            "latest_rate": latest_rate,
        }
    
    # =========================================================================
    # Signal 10: 价格突破关键心理关口
    # =========================================================================
    def calc_signal_10_breakout(self, kline_df: pd.DataFrame, price_data: dict) -> dict:
        """
        Signal 10: 价格突破关键心理关口
        
        逻辑：价格放量突破 $0.01/$0.05/$0.1/$0.5/$1 等关键心理位置
        这是庄家正式启动的明确信号
        """
        current_price = price_data.get("price", 0)
        
        if current_price <= 0 or kline_df is None or kline_df.empty:
            return {
                "triggered": False,
                "breakout_level": 0.0,
                "breakout_strength": 0.0,
                "detail": "数据不足"
            }
        
        tiers = [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100]
        
        # 找最近的关键关口
        key_levels = [t for t in tiers if t >= current_price * 0.5 and t <= current_price * 2]
        
        if not key_levels:
            return {
                "triggered": False,
                "breakout_level": 0.0,
                "breakout_strength": 0.0,
                "detail": "无关键关口"
            }
        
        # 检查最近是否突破关键位
        breakout_detected = False
        breakout_level = 0.0
        breakout_candles = 0
        
        # 最近5根K线
        recent_klines = kline_df.tail(5) if len(kline_df) >= 5 else kline_df
        
        for idx, row in recent_klines.iterrows():
            close = row.get("close", 0)
            open_price = row.get("open", 0)
            volume = row.get("volume", 0)
            
            for level in key_levels:
                # 检查是否突破（收盘价突破且实体阳性）
                if open_price < level <= close and close > open_price:
                    # 计算突破强度（成交量是否放大）
                    avg_volume = kline_df["volume"].tail(20).mean() if len(kline_df) >= 20 else volume
                    vol_ratio = volume / avg_volume if avg_volume > 0 else 1
                    
                    if vol_ratio >= 1.5:  # 1.5倍以上成交量确认
                        breakout_detected = True
                        breakout_level = level
                        breakout_candles = len(recent_klines) - list(kline_df.index).index(idx)
                        break
            
            if breakout_detected:
                break
        
        if breakout_detected:
            detail = f"价格突破关键心理关口 ${breakout_level}，突破成交量放大 ⭐"
            triggered = True
        else:
            detail = f"近期无突破关键心理关口，当前 ${current_price:.4f}"
            triggered = False
        
        return {
            "triggered": triggered,
            "breakout_level": breakout_level,
            "breakout_candles": breakout_candles,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 11: 多信号早期组合预警
    # =========================================================================
    def calc_signal_11_early_warning(self, all_signals: dict) -> dict:
        """
        Signal 11: 多信号早期组合预警
        
        逻辑：当有2-3个信号同时触发时，发出"密切关注"预警
        这不是入场信号，而是提醒开始重点关注
        """
        triggered_count = 0
        triggered_signals = []
        
        for signal_name, signal_data in all_signals.items():
            if isinstance(signal_data, dict) and signal_data.get("triggered", False):
                triggered_count += 1
                triggered_signals.append(signal_name)
        
        # 2-3个信号：密切关注
        if triggered_count >= 2 and triggered_count <= 3:
            triggered = True
            level = "WATCH"  # 密切关注
            detail = f"早期预警：{triggered_count}个信号触发，建议密切关注 ⭐ 触发信号: {', '.join(triggered_signals[:3])}"
        # 4+个信号：确认入场
        elif triggered_count >= 4:
            triggered = True
            level = "ENTRY"  # 确认入场
            detail = f"多信号确认：{triggered_count}个信号触发，入场条件满足"
        else:
            triggered = False
            level = "IDLE"
            detail = f"信号数量不足，当前仅 {triggered_count} 个信号触发"
        
        return {
            "triggered": triggered,
            "triggered_count": triggered_count,
            "warning_level": level,
            "triggered_signals": triggered_signals,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 12: 多空比信号
    # =========================================================================
    def calc_signal_12_long_short_ratio(self, ls_data: dict) -> dict:
        """
        Signal 12: 多空比信号
        
        逻辑：当多头占比 > 70% 且空头 < 30% 时，说明市场情绪偏多
        这可能预示机构/做市商在做多
        """
        if not ls_data or ls_data.get("error"):
            return {
                "triggered": False,
                "long_ratio": 0,
                "short_ratio": 0,
                "detail": "多空比数据获取失败"
            }
        
        long_ratio = ls_data.get("long_ratio", 0)
        short_ratio = ls_data.get("short_ratio", 0)
        
        # 优化阈值: 多头>100% (原70%太敏感)
        triggered = long_ratio > 100
        
        if triggered:
            if long_ratio > 150:
                level = "STRONG"
                detail = f"多头主导：{long_ratio:.1f}% / {short_ratio:.1f}%，强信号"
            elif long_ratio > 120:
                level = "MODERATE"
                detail = f"多头偏多：{long_ratio:.1f}% / {short_ratio:.1f}%，中信号"
            else:
                level = "WEAK"
                detail = f"多头启动：{long_ratio:.1f}% / {short_ratio:.1f}%，弱信号"
        else:
            level = "IDLE"
            detail = f"多空平衡：{long_ratio:.1f}% / {short_ratio:.1f}%"
        
        return {
            "triggered": triggered,
            "long_ratio": long_ratio,
            "short_ratio": short_ratio,
            "signal_level": level,
            "detail": detail
        }
    
    # =========================================================================
    # Signal 13: 主动成交量信号
    # =========================================================================
    def calc_signal_13_taker_volume(self, tv_data: dict) -> dict:
        """
        Signal 13: 主动成交量信号
        
        逻辑：当买入成交量 > 卖出成交量 且 比率 > 1.1 时，说明买方主动
        这可能预示机构/做市商在吸筹
        """
        if not tv_data or tv_data.get("error"):
            return {
                "triggered": False,
                "buy_volume": 0,
                "sell_volume": 0,
                "buy_sell_ratio": 0,
                "detail": "主动成交量数据获取失败"
            }
        
        buy_volume = tv_data.get("buy_volume", 0)
        sell_volume = tv_data.get("sell_volume", 0)
        buy_sell_ratio = tv_data.get("buy_sell_ratio", 1.0)
        
        triggered = buy_sell_ratio > 1.1
        
        if triggered:
            if buy_sell_ratio > 1.3:
                level = "STRONG"
                detail = f"买入主导：买入 ${buy_volume/1e6:.1f}M / 卖出 ${sell_volume/1e6:.1f}M，强信号"
            else:
                level = "MODERATE"
                detail = f"买入偏多：买入 ${buy_volume/1e6:.1f}M / 卖出 ${sell_volume/1e6:.1f}M，中信号"
        else:
            level = "IDLE"
            detail = f"买卖平衡：买入 ${buy_volume/1e6:.1f}M / 卖出 ${sell_volume/1e6:.1f}M"
        
        return {
            "triggered": triggered,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "buy_sell_ratio": buy_sell_ratio,
            "signal_level": level,
            "detail": detail
        }


def calculate_all_signals(
    price_data: dict,
    funding_data: dict,
    oi_data: dict,
    kline_df: pd.DataFrame,
    dex_data: dict,
    futures_data: dict,
    btcd_df: pd.DataFrame,
    token_kline_df: pd.DataFrame = None,
    social_data: dict = None,
    symbol: str = None,
    ls_data: dict = None,
    tv_data: dict = None,
) -> Dict[str, dict]:
    """
    计算所有11个信号
    
    Args:
        price_data: 价格数据
        funding_data: 资金费率数据
        oi_data: OI数据
        kline_df: K线DataFrame (BTC相关，用于放量/洗盘)
        dex_data: DEX数据
        futures_data: 合约数据
        btcd_df: BTC.D DataFrame
        token_kline_df: 代币K线DataFrame（用于BTC相对强度）
        social_data: 社媒数据（Signal 9）
        symbol: 代币符号（用于获取社交数据）
        ls_data: 多空比数据（Signal 12）
        tv_data: 主动成交量数据（Signal 13）
        
    Returns:
        11个信号的结果字典
    """
    calc = SignalCalculator()
    
    signals = {
        "signal_1_integer_consolidation": calc.calc_signal_1_integer_consolidation(price_data, kline_df),
        "signal_2_funding_turn_positive": calc.calc_signal_2_funding_turn_positive(funding_data),
        "signal_3_oi_accumulation": calc.calc_signal_3_oi_accumulation(oi_data, price_data),
        "signal_4_volume_spike": calc.calc_signal_4_volume_spike(kline_df),
        "signal_5_dex_buy_pressure": calc.calc_signal_5_dex_buy_pressure(dex_data),
        "signal_6_btcd_downtrend": calc.calc_signal_6_btcd_downtrend(btcd_df),
        "signal_6b_btc_relative_strength": calc.calc_signal_6b_btc_relative_strength(token_kline_df, btcd_df),
        "signal_7_new_futures": calc.calc_signal_7_new_futures(futures_data),
        "signal_8_wash_test": calc.calc_signal_8_wash_test(kline_df, funding_data),
        "signal_9_social_sentiment": calc.calc_signal_9_social_sentiment(social_data, symbol),
        "signal_10_breakout": calc.calc_signal_10_breakout(kline_df, price_data),
        "signal_12_long_short_ratio": calc.calc_signal_12_long_short_ratio(ls_data),
        "signal_13_taker_volume": calc.calc_signal_13_taker_volume(tv_data),
    }
    
    # Signal 11 需要在其他信号之后计算
    signals["signal_11_early_warning"] = calc.calc_signal_11_early_warning(signals)
    
    return signals


def check_exit_signal(funding_data: dict) -> dict:
    """
    检查离场信号
    """
    calc = SignalCalculator()
    return calc.check_exit_rules(funding_data)


def judge_manipulation_stage(
    price_data: dict,
    funding_data: dict,
    oi_data: dict,
    kline_df: pd.DataFrame,
    dex_data: dict,
) -> dict:
    """
    判定庄家操盘阶段 (5阶段模型)
    
    阶段:
    1. 静默积累期 - 价格横盘，成交量萎靡
    2. 洗盘测试期 - 价格突然拉升后回落
    3. 拉升启动期 - 突破关键价位+放量+费率转正
    4. 整数关口收割期 - 在整数关口反复震荡
    5. 出货分发期 - 费率急速下降，价格回落
    
    Returns:
    {
        "stage": "拉升启动期",
        "confidence": 0.8,
        "signals": {...},
        "recommendation": "入场"
    }
    """
    calc = SignalCalculator()
    signals = {
        "signal_1": calc.calc_signal_1_integer_consolidation(price_data, kline_df),
        "signal_2": calc.calc_signal_2_funding_turn_positive(funding_data),
        "signal_3": calc.calc_signal_3_oi_accumulation(oi_data, price_data),
        "signal_4": calc.calc_signal_4_volume_spike(kline_df),
        "signal_5": calc.calc_signal_5_dex_buy_pressure(dex_data),
    }
    
    triggered_count = sum(1 for s in signals.values() if s.get("triggered"))
    
    # 获取关键指标
    price = price_data.get("price", 0)
    current_funding = funding_data.get("latest_rate", 0) * 100 if funding_data else 0
    funding_trend = funding_data.get("trend_direction", "unknown") if funding_data else "unknown"
    oi_change = oi_data.get("oi_change_7d_pct", 0) if oi_data else 0
    
    # 阶段判定逻辑
    stage = "静默积累期"
    confidence = 0.3
    recommendation = "监控"
    
    # 出货分发期特征
    if current_funding > 0.5 or funding_trend == "down_from_high":
        stage = "出货分发期"
        confidence = 0.9
        recommendation = "清仓"
    
    # 整数关口收割期特征 - 放宽条件：不需要3个信号，只要在整数关口附近
    in_integer_zone = False
    for level in [0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 50.0, 100.0]:
        if level * 0.85 <= price <= level * 1.15:  # 放宽到15%范围
            in_integer_zone = True
            break
    
    # 整数关口优先判断：如果在整数关口附近，直接返回
    if in_integer_zone:
        if triggered_count >= 1:
            stage = "整数关口收割期"
            confidence = 0.65
            recommendation = "密切观察"
        else:
            stage = "整数关口收割期"
            confidence = 0.5
            recommendation = "监控"
        return {
            "stage": stage,
            "confidence": confidence,
            "triggered_count": triggered_count,
            "recommendation": recommendation,
            "signals": signals,
            "price": price,
            "funding_rate": current_funding,
            "oi_change_7d": oi_change,
        }
    
    # 拉升启动期特征 (放宽条件 - 山寨币费率本来就很低)
    # 条件：放量 + (费率>=0 或 DEX买压 或 横盘)
    s4_triggered = signals["signal_4"].get("triggered", False)
    s2_triggered = signals["signal_2"].get("triggered", False) or current_funding >= 0
    s1_or_s5 = signals["signal_1"].get("triggered", False) or signals["signal_5"].get("triggered", False)
    
    if s4_triggered and s2_triggered and s1_or_s5:
        stage = "拉升启动期"
        confidence = 0.85
        recommendation = "入场"
    
    # 放宽条件2: 如果有放量+DEX买压，也可以考虑入场 (费率不是硬性要求)
    elif s4_triggered and signals["signal_5"].get("triggered", False):
        stage = "拉升启动期"
        confidence = 0.7
        recommendation = "入场"
    
    # 放宽条件3: 如果费率>=0且有DEX买压，也可以考虑
    elif current_funding >= 0 and signals["signal_5"].get("triggered", False):
        stage = "拉升启动期"
        confidence = 0.65
        recommendation = "入场"
    
    # 洗盘测试期特征
    elif signals["signal_4"].get("triggered") and oi_change < 0:
        stage = "洗盘测试期"
        confidence = 0.6
        recommendation = "等待"
    
    # 出货分发期特征（移到后面作为兜底）
    elif current_funding > 0.5 or funding_trend == "down_from_high":
        stage = "出货分发期"
        confidence = 0.9
        recommendation = "清仓"
    
    # 静默积累期 (默认)
    else:
        stage = "静默积累期"
        confidence = 0.4
        recommendation = "监控"
    
    return {
        "stage": stage,
        "confidence": confidence,
        "triggered_count": triggered_count,
        "recommendation": recommendation,
        "signals": signals,
        "price": price,
        "funding_rate": current_funding,
        "oi_change_7d": oi_change,
    }
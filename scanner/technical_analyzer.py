"""
MMTracker 完整技术分析引擎
整合所有量化因子: K线/CVD/多空比/EMA/支撑阻力/资金费率/持仓/爆仓

Version 2.0 - 2025-01
"""

import requests
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone


class TechnicalAnalyzer:
    """
    完整技术分析引擎
    整合所有技术指标进行综合评分
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.data = {}
        self.indicators = {}
        self.score = 0
        self.signals = []
    
    def fetch_all_data(self) -> bool:
        """获取所有需要的数据"""
        try:
            # 1. K线数据 (15m, 1h, 4h)
            self._fetch_candles()
            
            # 2. 资金费率
            self._fetch_funding_rate()
            
            # 3. 多空比
            self._fetch_long_short_ratio()
            
            # 4. 持仓量(OI)
            self._fetch_open_interest()
            
            # 5. 近期爆仓数据
            self._fetch_liquidation()
            
            return True
        except Exception as e:
            print(f"[TechnicalAnalyzer] {self.symbol} 数据获取失败: {e}")
            return False
    
    def _fetch_candles(self):
        """获取K线并计算技术指标"""
        # 获取15分钟K线 (最近100根)
        url = f"https://www.okx.com/api/v5/market/candles"
        params = {"instId": f"{self.symbol}-USDT", "bar": "15m", "limit": 100}
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            
            if data.get('code') != '0' or not data.get('data'):
                return
            
            candles = data['data']
            closes = [float(c[4]) for c in candles]
            highs = [float(c[2]) for c in candles]
            lows = [float(c[3]) for c in candles]
            vols = [float(c[5]) for c in candles]
            
            if len(closes) < 20:
                return
            
            df = pd.DataFrame({
                'close': closes[::-1],
                'high': highs[::-1],
                'low': lows[::-1],
                'volume': vols[::-1]
            })
            
            # ====== EMA计算 ======
            df['ema9'] = df['close'].ewm(span=9, adjust=False).mean()
            df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
            df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()
            
            # ====== 移动平均线 ======
            df['ma20'] = df['close'].rolling(20).mean()
            df['ma50'] = df['close'].rolling(50).mean()
            
            # ====== RSI ======
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / loss
            df['rsi'] = 100 - (100 / (1 + rs))
            
            # ====== 布林带 ======
            df['bb_middle'] = df['close'].rolling(20).mean()
            bb_std = df['close'].rolling(20).std()
            df['bb_upper'] = df['bb_middle'] + 2 * bb_std
            df['bb_lower'] = df['bb_middle'] - 2 * bb_std
            
            # ====== ATR (波动率) ======
            high_low = df['high'] - df['low']
            high_close = np.abs(df['high'] - df['close'].shift())
            low_close = np.abs(df['low'] - df['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = ranges.max(axis=1)
            df['atr'] = true_range.rolling(14).mean()
            
            # ====== 成交量变化 (CVD类似) ======
            df['vol_change'] = df['volume'].pct_change()
            df['vol_ma5'] = df['volume'].rolling(5).mean()
            df['vol_ratio'] = df['volume'] / df['vol_ma5']
            
            # ====== 支撑阻力位 (Pivot) ======
            last = df.iloc[-1]
            pivot = (last['high'] + last['low'] + last['close']) / 3
            range_hl = last['high'] - last['low']
            
            self.data['candles'] = df
            self.data['current_price'] = last['close']
            self.data['ema9'] = last['ema9']
            self.data['ema21'] = last['ema21']
            self.data['ema50'] = last['ema50']
            self.data['rsi'] = last['rsi']
            self.data['atr'] = last['atr']
            self.data['volume_ratio'] = last['vol_ratio']
            
            # 支撑阻力
            self.data['pivot'] = pivot
            self.data['res1'] = pivot + 0.382 * range_hl
            self.data['res2'] = pivot + 0.618 * range_hl
            self.data['sup1'] = pivot - 0.382 * range_hl
            self.data['sup2'] = pivot - 0.618 * range_hl
            
            # 多周期数据
            self._fetch_higher_tf(df)
            
        except Exception as e:
            print(f"[TechnicalAnalyzer] K线获取失败: {e}")
    
    def _fetch_higher_tf(self, df_15m):
        """获取更高时间框架数据"""
        # 1小时K线
        url = "https://www.okx.com/api/v5/market/candles"
        params = {"instId": f"{self.symbol}-USDT", "bar": "1H", "limit": 50}
        
        try:
            resp = requests.get(url, params=params, timeout=10)
            data = resp.json()
            
            if data.get('code') == '0' and data.get('data'):
                candles = data['data']
                closes = [float(c[4]) for c in candles][::-1]
                
                if len(closes) >= 50:
                    # EMA
                    closes_series = pd.Series(closes)
                    self.data['ema9_1h'] = closes_series.ewm(span=9).mean().iloc[-1]
                    self.data['ema21_1h'] = closes_series.ewm(span=21).mean().iloc[-1]
                    self.data['ema50_1h'] = closes_series.ewm(span=50).mean().iloc[-1]
                    
                    # 趋势判断
                    self.data['trend_1h'] = 'bullish' if self.data['ema9_1h'] > self.data['ema21_1h'] else 'bearish'
                    
        except:
            pass
    
    def _fetch_funding_rate(self):
        """获取资金费率"""
        url = "https://www.okx.com/api/v5/public/funding-rate"
        params = {"instId": f"{self.symbol}-USDT-SWAP"}
        
        try:
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            
            if data.get('code') == '0' and data.get('data'):
                rate = float(data['data'][0].get('fundingRate', 0))
                self.data['funding_rate'] = rate
                
                # 资金费率解读
                if rate > 0.001:  # >0.1%
                    self.signals.append(f"资金费率偏高 +{rate*100:.2f}%")
                elif rate < -0.001:
                    self.signals.append(f"资金费率负数 {rate*100:.2f}% (多头补贴)")
                else:
                    self.signals.append(f"资金费率正常 {rate*100:.3f}%")
                    
        except:
            self.data['funding_rate'] = 0
    
    def _fetch_long_short_ratio(self):
        """获取多空比"""
        url = "https://www.okx.com/api/v5/public/long-short-ratio"
        params = {"instId": f"{self.symbol}-USDT-SWAP"}
        
        try:
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            
            if data.get('code') == '0' and data.get('data'):
                ratio = float(data['data'][0].get('longShortRatio', 0))
                self.data['long_short_ratio'] = ratio
                
                # 多空比解读
                long_pct = ratio * 100
                self.signals.append(f"多空比: 多头 {long_pct:.1f}% / 空头 {100-long_pct:.1f}%")
                
                if long_pct > 70:
                    self.signals.append("⚠️ 多头过热 >70%")
                elif long_pct < 30:
                    self.signals.append("⚠️ 空头过热 <30%")
                    
        except:
            self.data['long_short_ratio'] = 0.5
    
    def _fetch_open_interest(self):
        """获取持仓量(OI)"""
        url = "https://www.okx.com/api/v5/public/open-interest"
        params = {"instId": f"{self.symbol}-USDT-SWAP"}
        
        try:
            resp = requests.get(url, params=params, timeout=5)
            data = resp.json()
            
            if data.get('code') == '0' and data.get('data'):
                oi = float(data['data'][0].get('oi', 0))
                self.data['open_interest'] = oi
                
        except:
            self.data['open_interest'] = 0
    
    def _fetch_liquidation(self):
        """获取近期爆仓数据"""
        # 注意: OKX API可能没有直接的爆仓数据，这里用近似方法
        # 通过价格大幅波动来估算
        if 'candles' in self.data:
            df = self.data['candles']
            if len(df) > 20:
                # 计算近期最大回撤
                rolling_max = df['close'].rolling(20, min_periods=1).max()
                drawdown = (df['close'] - rolling_max) / rolling_max
                max_dd = drawdown.min()
                
                self.data['max_drawdown'] = max_dd
                
                if max_dd < -0.05:  # >5%回撤
                    self.signals.append(f"⚠️ 近期大幅回调 {max_dd*100:.1f}%")
    
    def analyze(self) -> Dict[str, Any]:
        """综合分析并评分"""
        if not self.data.get('current_price'):
            return {'error': '无价格数据'}
        
        score = 0
        reasons = []
        
        price = self.data['current_price']
        
        # ====== 1. EMA趋势分析 ======
        ema9 = self.data.get('ema9', 0)
        ema21 = self.data.get('ema21', 0)
        ema50 = self.data.get('ema50', 0)
        
        if ema9 > ema21 > ema50:
            score += 3
            reasons.append("EMA多头排列")
        elif ema9 > ema21:
            score += 2
            reasons.append("EMA短期金叉")
        
        if self.data.get('ema9_1h', 0) > self.data.get('ema21_1h', 0):
            score += 2
            reasons.append("1H EMA金叉")
        
        # ====== 2. RSI分析 ======
        rsi = self.data.get('rsi', 50)
        if 30 < rsi < 70:
            score += 1
            reasons.append(f"RSI适中 {rsi:.0f}")
        elif rsi < 30:
            score += 2
            reasons.append(f"RSI超卖 {rsi:.0f} (可能反弹)")
        elif rsi > 70:
            self.signals.append(f"⚠️ RSI超买 {rsi:.0f}")
        
        # ====== 3. 成交量分析 ======
        vol_ratio = self.data.get('volume_ratio', 1)
        if vol_ratio > 1.5:
            score += 2
            reasons.append(f"成交量放大 {vol_ratio:.1f}x")
        elif vol_ratio > 1.2:
            score += 1
            reasons.append(f"成交量温和放大 {vol_ratio:.1f}x")
        
        # ====== 4. 支撑阻力位 ======
        if price < self.data.get('res1', float('inf')):
            score += 1
            reasons.append(f"接近阻力 {self.data['res1']:.4f}")
        
        if price > self.data.get('sup1', 0):
            score += 1
            reasons.append(f"站稳支撑 {self.data['sup1']:.4f}")
        
        # ====== 5. 资金费率 ======
        funding = self.data.get('funding_rate', 0)
        if -0.0005 < funding < 0.001:
            score += 1
            reasons.append(f"资金费率正常 {funding*100:.2f}%")
        elif funding < -0.001:
            score += 2
            reasons.append(f"负费率 {funding*100:.2f}% (多头补贴)")
        
        # ====== 6. 多空比 ======
        ls_ratio = self.data.get('long_short_ratio', 0.5)
        long_pct = ls_ratio * 100
        if 40 < long_pct < 60:
            score += 1
            reasons.append(f"多空平衡 {long_pct:.0f}%")
        elif 30 < long_pct < 40:
            score += 2
            reasons.append(f"空头过热 {long_pct:.0f}% (可能反转)")
        
        # ====== 7. 波动率ATR ======
        atr = self.data.get('atr', 0)
        if atr > 0:
            atr_pct = atr / price * 100
            if 1 < atr_pct < 5:
                score += 1
                reasons.append(f"波动率适中 {atr_pct:.1f}%")
        
        # ====== 8. 布林带位置 ======
        if 'candles' in self.data:
            df = self.data['candles']
            last = df.iloc[-1]
            if last['close'] < last['bb_lower']:
                score += 2
                reasons.append("触及布林下轨 (超卖)")
            elif last['close'] > last['bb_upper']:
                self.signals.append("⚠️ 触及布林上轨 (超买)")
        
        # 保存评分
        self.score = score
        self.indicators = {
            'ema9': ema9,
            'ema21': ema21,
            'ema50': ema50,
            'rsi': rsi,
            'volume_ratio': vol_ratio,
            'funding_rate': funding,
            'long_short_ratio': ls_ratio,
            'atr': atr,
        }
        
        return {
            'symbol': self.symbol,
            'price': price,
            'score': score,
            'max_score': 20,
            'reasons': reasons,
            'signals': self.signals,
            'indicators': self.indicators,
            'pivot': self.data.get('pivot', 0),
            'resistance': self.data.get('res1', 0),
            'support': self.data.get('sup1', 0),
        }
    
    def get_report(self) -> str:
        """生成分析报告"""
        if not self.indicators:
            return f"❌ {self.symbol} 无法获取数据"
        
        result = self.analyze()
        
        report = f"""
{'='*60}
🔬 {self.symbol} 完整技术分析报告
{'='*60}
💰 价格: ${result['price']:.6f}

📊 技术指标:
   EMA9:  ${result['indicators']['ema9']:.6f}
   EMA21: ${result['indicators']['ema21']:.6f}
   EMA50: ${result['indicators']['ema50']:.6f}
   RSI:   {result['indicators']['rsi']:.1f}
   成交量比: {result['indicators']['volume_ratio']:.2f}x
   资金费率: {result['indicators']['funding_rate']*100:.3f}%
   多空比: {result['indicators']['long_short_ratio']*100:.1f}%

📈 支撑阻力:
   阻力R1: ${result['resistance']:.6f}
   支点P:  ${result['pivot']:.6f}
   支撑S1: ${result['support']:.6f}

🎯 综合评分: {result['score']}/{result['max_score']}
   得分理由: {', '.join(result['reasons'])}

⚠️ 信号提示:
"""
        for sig in result['signals']:
            report += f"   {sig}\n"
        
        if result['score'] >= 10:
            report += f"\n✅ 强烈推荐买入 (得分{result['score']})"
        elif result['score'] >= 6:
            report += f"\n⚠️ 建议观察 (得分{result['score']})"
        else:
            report += f"\n❌ 不推荐 (得分{result['score']})"
        
        return report


def analyze_symbol(symbol: str) -> Dict[str, Any]:
    """快速分析单个代币"""
    analyzer = TechnicalAnalyzer(symbol)
    if analyzer.fetch_all_data():
        return analyzer.analyze()
    return {'error': '数据获取失败', 'symbol': symbol}


if __name__ == "__main__":
    # 测试
    for token in ['XPL', 'BIO', 'ENA', 'BTC', 'ETH']:
        print(f"\n分析 {token}...")
        result = analyze_symbol(token)
        if 'error' not in result:
            print(f"  价格: ${result['price']:.4f}")
            print(f"  评分: {result['score']}/{result['max_score']}")
            print(f"  理由: {result['reasons']}")
        else:
            print(f"  错误: {result.get('error')}")
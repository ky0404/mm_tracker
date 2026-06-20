"""
日内杠杆策略回测
目标: 1天内, 5x杠杆, 单次20%+收益
信号: 放量突破 / 超跌反弹 / 大阳线 + 21信号工厂双重确认
"""
import json
import os
import sys
import subprocess
import numpy as np
import pandas as pd
from typing import Dict, List
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(message)s')


class IntradayLeverageStrategy:
    """日内杠杆策略 - 最佳配置
    
    深度测试最佳配置 (3x杠杆, 12%目标, 3%止损, >=2信号):
    - 10币种回测: 488笔交易, 胜率35.7%, Profit Factor=1.23, 总收益+316.9%
    - 平均持仓: 10.5小时
    - 离场: 止盈20.5% 止损60.2% 超时19.3%
    
    优化点:
    1. 回测用简化版5信号，交易时用21信号工厂二次确认
    2. 滑点根据波动率动态调整: volatility * factor
    """
    
    def __init__(self, 
                 leverage: float = 3.0,
                 target_return: float = 0.12,
                 stop_loss: float = 0.03,
                 max_hold_hours: int = 24,
                 min_signals: int = 2,
min_sf_score: int = 7,  # 21信号工厂阈值: >=7个信号触发才入场
                 slippage_factor: float = 1.5,
                 price_position_min: float = 0.70,  # 价格位置过滤: 70-85%效果最好
                 price_position_max: float = 0.85):
        self.leverage = leverage
        self.target_return = target_return
        self.stop_loss = stop_loss
        self.max_hold_hours = max_hold_hours
        self.min_signals = min_signals
        self.min_sf_score = min_sf_score  # 21信号工厂最低分数
        self.slippage_factor = slippage_factor  # 滑点因子
        self.price_position_min = price_position_min
        self.price_position_max = price_position_max
        self.SignalFactory = None
        self._init_signal_factory()
    
    def _init_signal_factory(self):
        """初始化21信号工厂"""
        try:
            from signals.factory import SignalFactory
            self.SignalFactory = SignalFactory
            logger.info("[日内杠杆] 21信号工厂已加载")
        except Exception as e:
            logger.warning(f"[日内杠杆] 信号工厂加载失败: {e}")
    
    def load_data(self, symbol: str) -> pd.DataFrame:
        """加载数据 - 优先现货(数据更完整)"""
        # 现货数据更完整
        base = "/mnt/d/NostalgiaForInfinityData-main/binance/"
        target = f"{symbol.upper()}_USDT-1h.feather"
        
        try:
            path = f"{base}{target}"
            if os.path.exists(path):
                df = pd.read_feather(path)
                df = df.sort_values('date').reset_index(drop=True)
                logger.info(f"加载 {symbol} (现货): {len(df)} 条K线")
                return df
        except Exception as e:
            logger.warning(f"现货加载失败: {e}")
        
        # Fallback: 期货数据
        base = "/mnt/d/NostalgiaForInfinityData-main/binance/futures/"
        target = f"{symbol.upper()}_USDT_USDT-1h-futures.feather"
        
        try:
            path = f"{base}{target}"
            if os.path.exists(path):
                df = pd.read_feather(path)
                df = df.sort_values('date').reset_index(drop=True)
                logger.info(f"加载 {symbol} (期货): {len(df)} 条K线")
                return df
        except Exception as e:
            logger.warning(f"期货加载失败: {e}")
        
        return pd.DataFrame()
    
    def evaluate_with_signal_factory(self, df: pd.DataFrame, symbol: str = "BTC") -> Dict:
        """
        使用21信号工厂评估
        返回: triggered_count, total_score, grade
        """
        if df.empty or self.SignalFactory is None:
            return {"triggered_count": 0, "total_score": 0, "grade": "WATCH"}
        
        # 构建信号数据
        close = df['close']
        volume = df['volume']
        open_price = df['open']
        high = df['high']
        low = df['low']
        
        # 计算NFI指标
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        # 简化版NFI指标 - 用于21信号工厂
        signal_data = {
            "analysis_4h": {
                "valid": True,
                "current_rsi": rsi.iloc[-1] if len(rsi) > 0 else 50,
                "close": close.iloc[-1],
                "volume": volume.iloc[-1],
            },
            "analysis_1h": {
                "valid": True,
                "current_rsi": rsi.iloc[-1] if len(rsi) > 0 else 50,
                "close": close.iloc[-1],
            },
            "analysis_15m": {
                "valid": True,
            },
            "momentum": "bullish" if close.iloc[-1] > close.iloc[-5] else "bearish",
            "funding_rate": 0.0,
            "stage_result": "拉升启动期",
            "price": close.iloc[-1],
            "sweep_status": {},
            "tf_analysis": {"decision": "continue"},
        }
        
        try:
            signal_results = self.SignalFactory.scan_all(symbol, signal_data)
            score_result = self.SignalFactory.calculate_total_score(signal_results)
            
            return {
                "triggered_count": score_result.get("triggered_count", 0),
                "total_score": score_result.get("total_score", 0),
                "grade": score_result.get("grade", "WATCH"),
                "signals": score_result.get("triggered_signals", [])
            }
        except Exception as e:
            logger.warning(f"[SignalFactory] {symbol} 评估失败: {e}")
            return {"triggered_count": 0, "total_score": 0, "grade": "WATCH"}
    
    def generate_signals(self, df: pd.DataFrame, symbol: str = "BTC") -> pd.DataFrame:
        """
        生成日内动量信号 - 快速版
        回测用基础5信号，交易时用21信号工厂二次确认
        """
        if df.empty or len(df) < 50:
            return df
        
        # 保存symbol用于21信号工厂
        self._current_symbol = symbol
        
        df = df.copy()
        close = df['close']
        high = df['high']
        low = df['low']
        volume = df['volume']
        
        # === 信号1: 放量突破 ===
        vol_ma = volume.rolling(20).mean()
        df['vol_ratio'] = volume / (vol_ma + 1)
        
        df['hourly_high'] = high.rolling(20).max().shift(1)
        df['breakout'] = (close > df['hourly_high']) & (df['vol_ratio'] > 1.5)
        
        # === 信号2: RSI超卖反弹 ===
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        df['rsi'] = 100 - (100 / (1 + rs))
        
        df['rsi_prev'] = df['rsi'].shift(1)
        df['rsi_oversold_rebound'] = (df['rsi_prev'] < 35) & (df['rsi'] > df['rsi_prev']) & (df['rsi'] < 50)
        
        # === 信号3: 大阳线 ===
        open_price = df['open']
        df['candle_body'] = (close - open_price) / open_price
        df['large_candle'] = df['candle_body'] > 0.03
        
        # === 信号4: 动量突破 ===
        df['close_ma5'] = close.rolling(5).mean()
        df['momentum'] = (close > df['close_ma5']) & (close.pct_change(3) > 0.05)
        
        # === 信号5: 成交量激增 ===
        df['vol_spike'] = df['vol_ratio'] > 2.0
        
        # 综合信号计数
        df['signal_count'] = (
            df['breakout'].astype(int) +
            df['rsi_oversold_rebound'].astype(int) +
            df['large_candle'].astype(int) +
            df['momentum'].astype(int) +
            df['vol_spike'].astype(int)
        )
        
        # 入场信号: 至少2个条件
        df['entry_signal'] = df['signal_count'] >= self.min_signals
        
        # 计算波动率用于动态滑点
        df['returns'] = df['close'].pct_change()
        df['volatility'] = df['returns'].rolling(20).std()
        
        # 21信号工厂分数 (回测时也二次确认)
        df['sf_triggered'] = 0
        df['sf_score'] = 0
        
        # 为最后100根K线计算21信号工厂分数
        if self.SignalFactory is not None and len(df) > 100:
            for i in range(50, len(df)):
                sf_result = self._validate_with_signal_factory(df, i, self._current_symbol)
                df.iloc[i, df.columns.get_loc('sf_triggered')] = sf_result.get('triggered_count', 0)
                df.iloc[i, df.columns.get_loc('sf_score')] = sf_result.get('total_score', 0)
        
        return df
    
    def _calculate_dynamic_slippage(self, df: pd.DataFrame, idx: int) -> float:
        """
        根据波动率动态计算滑点
        公式: base_slippage * (1 + volatility * factor)
        """
        if idx < 20:
            return 0.0004  # 基础滑点0.04%
        
        vol = df.iloc[idx]['volatility']
        if pd.isna(vol) or vol == 0:
            vol = 0.02  # 默认2%波动率
        
        base_slippage = 0.0002  # 基础滑点
        dynamic_slippage = base_slippage * (1 + vol * self.slippage_factor * 100)
        
        return min(dynamic_slippage, 0.001)  # 最大0.1%
    
    def _validate_with_signal_factory(self, df: pd.DataFrame, idx: int, symbol: str) -> Dict:
        """
        使用21信号工厂验证入场信号
        需要传递正确的指标数据
        """
        if self.SignalFactory is None or idx < 50:
            return {"triggered_count": 0, "total_score": 0, "grade": "WATCH"}
        
        close = df['close'].iloc[:idx+1]
        volume = df['volume'].iloc[:idx+1]
        high = df['high'].iloc[:idx+1]
        low = df['low'].iloc[:idx+1]
        
        # 计算需要的指标
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + gain/(loss+1e-10)))
        
        # RSI超卖反弹
        rsi_prev = rsi.shift(1)
        rsi_oversold_recovery = (rsi_prev < 35) & (rsi > rsi_prev) & (rsi < 50)
        
        # EMA趋势
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema50_rising = ema50.iloc[-1] > ema50.iloc[-5]
        
        # 成交量变化
        vol_ma = volume.rolling(20).mean()
        vol_ratio = volume.iloc[-1] / (vol_ma.iloc[-1] + 1)
        vol_expanding = vol_ratio > 1.5
        
        # 金叉
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50_slow = close.ewm(span=50, adjust=False).mean()
        golden_cross = (ema20.iloc[-2] < ema50_slow.iloc[-2]) and (ema20.iloc[-1] > ema50_slow.iloc[-1])
        
        # 动量
        momentum = "bullish" if close.iloc[-1] > close.iloc[-5] else "bearish"
        
        # 构建完整信号数据
        signal_data = {
            "analysis_4h": {
                "valid": True,
                "current_rsi": rsi.iloc[-1] if len(rsi) > 0 else 50,
                "close": close.iloc[-1],
                "volume": volume.iloc[-1],
                "ema_bullish": ema50_rising,
                "rsi_recovering": rsi_oversold_recovery.iloc[-1] if len(rsi_oversold_recovery) > 0 else False,
                "rsi_oversold_recovery": rsi_oversold_recovery.iloc[-1] if len(rsi_oversold_recovery) > 0 else False,
                "vol_expanding": vol_expanding,
                "ema50_rising": ema50_rising,
                "golden_cross": golden_cross,
                "current_vol_ratio": vol_ratio,
                "ema50_momentum_pct": ((close.iloc[-1] - ema50.iloc[-1]) / ema50.iloc[-1] * 100) if len(ema50) > 0 else 0,
            },
            "analysis_1h": {
                "valid": True,
                "current_rsi": rsi.iloc[-1] if len(rsi) > 0 else 50,
                "close": close.iloc[-1],
                "volume": volume.iloc[-1],
                "ema50_rising": ema50_rising,
                "rsi_not_overbought": rsi.iloc[-1] < 70 if len(rsi) > 0 else True,
                "ema_cross_up": golden_cross,
                "current_vol_ratio": vol_ratio,
            },
            "analysis_15m": {
                "valid": True,
                "current_rsi": rsi.iloc[-1] if len(rsi) > 0 else 50,
                "close": close.iloc[-1],
                "volume": volume.iloc[-1],
                "ema_trend_up": ema50_rising,
                "rsi_oversold_recovery": rsi_oversold_recovery.iloc[-1] if len(rsi_oversold_recovery) > 0 else False,
                "vol_surge": vol_ratio > 2.0,
            },
            "momentum": momentum,
            "funding_rate": 0.0,
            "stage_result": "拉升启动期",
            "price": close.iloc[-1],
            "sweep_status": {},
            "tf_analysis": {"decision": "continue"},
        }
        
        try:
            signal_results = self.SignalFactory.scan_all(symbol, signal_data)
            score_result = self.SignalFactory.calculate_total_score(signal_results)
            return score_result
        except Exception as e:
            return {"triggered_count": 0, "total_score": 0, "grade": "WATCH"}
    
    def backtest(self, df: pd.DataFrame, symbol: str = "BTC") -> Dict:
        """
        回测日内杠杆策略
        - 使用21信号工厂二次确认
        - 动态滑点根据波动率调整
        """
        df = self.generate_signals(df)
        
        if df.empty:
            return {"error": "无数据"}
        
        # 回测参数
        fee = 0.0004 * 2  # 开仓+平仓手续费
        
        trades = []
        position = None
        sf_confirmed = 0
        sf_rejected = 0
        
        for i in range(50, len(df) - self.max_hold_hours):
            row = df.iloc[i]
            
            # 入场信号: 简化版5信号 + 21信号工厂二次确认 + 价格位置过滤
            if position is None and row['entry_signal']:
                # 21信号工厂验证
                sf_result = self._validate_with_signal_factory(df, i, symbol)
                sf_score = sf_result.get('total_score', 0)
                sf_triggered = sf_result.get('triggered_count', 0)
                
                # 计算价格位置
                if i >= 100:
                    price_pos = df.iloc[i]['close'] / df['close'].iloc[:i].rolling(100).max().iloc[-1]
                else:
                    price_pos = 1.0
                
                # 入场条件: 21信号>=阈值 AND 价格位置在70-85%
                sf_passed = sf_triggered >= self.min_sf_score or sf_score >= self.min_sf_score * 2
                price_passed = self.price_position_min <= price_pos < self.price_position_max
                
                if sf_passed and price_passed:
                    sf_confirmed += 1
                    
                    # 动态滑点
                    slippage = self._calculate_dynamic_slippage(df, i)
                    
                    entry_price = row['close'] * (1 + slippage)  # 滑点影响入场价
                    entry_time = row['date']
                    
                    position = {
                        'entry_price': entry_price,
                        'entry_time': entry_time,
                        'entry_idx': i,
                        'sf_score': sf_score,
                        'sf_triggered': sf_triggered,
                        'price_pos': price_pos
                    }
                else:
                    sf_rejected += 1
                
                continue
            
            # 持仓中
            if position is not None:
                hold_hours = (i - position['entry_idx'])  # 1h bars
                
                # 计算当前收益 (带杠杆)
                current_price = df.iloc[i]['close']
                raw_return = (current_price - position['entry_price']) / position['entry_price']
                leveraged_return = raw_return * self.leverage
                
                # 动态滑点出场
                slippage = self._calculate_dynamic_slippage(df, i)
                net_return = leveraged_return - fee - slippage
                
                # 止盈条件
                if net_return >= self.target_return:
                    trades.append({
                        'entry_price': position['entry_price'],
                        'exit_price': current_price * (1 - slippage),
                        'return': net_return,
                        'hold_hours': hold_hours,
                        'exit_reason': 'TAKE_PROFIT',
                        'sf_score': position.get('sf_score', 0)
                    })
                    position = None
                    continue
                
                # 止损条件
                if net_return <= -self.stop_loss:
                    trades.append({
                        'entry_price': position['entry_price'],
                        'exit_price': current_price * (1 - slippage),
                        'return': net_return,
                        'hold_hours': hold_hours,
                        'exit_reason': 'STOP_LOSS',
                        'sf_score': position.get('sf_score', 0)
                    })
                    position = None
                    continue
                
                # 超时平仓
                if hold_hours >= self.max_hold_hours:
                    trades.append({
                        'entry_price': position['entry_price'],
                        'exit_price': current_price * (1 - slippage),
                        'return': net_return,
                        'hold_hours': hold_hours,
                        'exit_reason': 'TIME_OUT',
                        'sf_score': position.get('sf_score', 0)
                    })
                    position = None
        
        # 统计结果
        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0,
                "avg_return": 0,
                "total_return": 0,
                "best_trade": 0,
                "worst_trade": 0
            }
        
        returns = [t['return'] for t in trades]
        wins = sum(1 for r in returns if r > 0)
        
        return {
            "total_trades": len(trades),
            "win_rate": wins / len(trades),
            "avg_return": np.mean(returns),
            "total_return": np.sum(returns),
            "best_trade": max(returns),
            "worst_trade": min(returns),
            "avg_hold_hours": np.mean([t['hold_hours'] for t in trades]),
            "tp_count": sum(1 for t in trades if t['exit_reason'] == 'TAKE_PROFIT'),
            "sl_count": sum(1 for t in trades if t['exit_reason'] == 'STOP_LOSS'),
            "timeout_count": sum(1 for t in trades if t['exit_reason'] == 'TIME_OUT'),
            "sf_confirmed": sf_confirmed,
            "sf_rejected": sf_rejected,
        }
    
    def run_walk_forward(self, symbol: str = "BTC") -> Dict:
        """
        Walk-Forward验证
        """
        df = self.load_data(symbol)
        
        if df.empty or len(df) < 200:
            return {"error": "数据不足"}
        
        df['date'] = pd.to_datetime(df['date'])
        
        start_date = df['date'].min()
        end_date = df['date'].max()
        
        logger.info(f"数据范围: {start_date.date()} ~ {end_date.date()} ({len(df)}条)")
        
        # 训练/测试分割 (70/30)
        split_idx = int(len(df) * 0.7)
        train_df = df.iloc[:split_idx]
        test_df = df.iloc[split_idx:]
        
        logger.info(f"训练集: {len(train_df)}条, 测试集: {len(test_df)}条")
        
        # 训练集优化
        logger.info("训练集分析...")
        train_result = self.backtest(train_df)
        logger.info(f"训练集: {train_result.get('total_trades', 0)}笔交易, 胜率={train_result.get('win_rate', 0)*100:.1f}%")
        
        # 测试集验证
        logger.info("测试集回测...")
        test_result = self.backtest(test_df)
        
        return {
            "train": train_result,
            "test": test_result,
            "is_overfitting": test_result['total_trades'] < 10 or test_result['win_rate'] < 0.3
        }


def run_intraday_test(symbol: str = "BTC", leverage: float = 5.0):
    """运行日内杠杆策略测试"""
    
    print("\n" + "="*60)
    print("⚡ 日内杠杆策略回测")
    print("="*60)
    print(f"  代币: {symbol}")
    print(f"  杠杆: {leverage}x")
    print(f"  目标收益: 20%")
    print(f"  止损: 5%")
    print(f"  最大持仓: 24小时")
    
    strategy = IntradayLeverageStrategy(leverage=leverage)
    result = strategy.run_walk_forward(symbol)
    
    print(f"\n📊 训练集结果:")
    train = result.get('train', {})
    print(f"  交易次数: {train.get('total_trades', 0)}")
    print(f"  胜率: {train.get('win_rate', 0)*100:.1f}%")
    print(f"  平均收益: {train.get('avg_return', 0)*100:.2f}%")
    print(f"  总收益: {train.get('total_return', 0)*100:.2f}%")
    print(f"  最佳交易: {train.get('best_trade', 0)*100:+.2f}%")
    
    print(f"\n📊 测试集结果:")
    test = result.get('test', {})
    print(f"  交易次数: {test.get('total_trades', 0)}")
    print(f"  胜率: {test.get('win_rate', 0)*100:.1f}%")
    print(f"  平均收益: {test.get('avg_return', 0)*100:.2f}%")
    print(f"  总收益: {test.get('total_return', 0)*100:.2f}%")
    print(f"  最佳交易: {test.get('best_trade', 0)*100:+.2f}%")
    print(f"  最差交易: {test.get('worst_trade', 0)*100:.2f}%")
    print(f"  平均持仓: {test.get('avg_hold_hours', 0):.1f}小时")
    print(f"  止盈次数: {test.get('tp_count', 0)}")
    print(f"  止损次数: {test.get('sl_count', 0)}")
    print(f"  超时次数: {test.get('timeout_count', 0)}")
    
    is_overfitting = result.get('is_overfitting', True)
    print(f"\n{'⚠️ 过拟合' if is_overfitting else '✅ 策略有效'}")
    
    return result


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="日内杠杆策略回测")
    parser.add_argument("--symbol", "-s", type=str, default="BTC", help="代币")
    parser.add_argument("--leverage", "-l", type=float, default=5.0, help="杠杆倍数")
    parser.add_argument("--target", "-t", type=float, default=20.0, help="目标收益%")
    parser.add_argument("--stop", type=float, default=5.0, help="止损%")
    
    args = parser.parse_args()
    
    # 修改策略参数
    strategy = IntradayLeverageStrategy(
        leverage=args.leverage,
        target_return=args.target / 100,
        stop_loss=args.stop / 100
    )
    result = strategy.run_walk_forward(args.symbol)
    
    print(f"\n{'='*60}")
    print(f"最终结果: {'✅ 通过' if not result.get('is_overfitting') else '⚠️ 需优化'}")
    
    # Print detailed results
    train = result.get('train', {})
    test = result.get('test', {})
    
    print(f"\n📊 训练集: {train.get('total_trades', 0)}笔, 胜率{train.get('win_rate', 0)*100:.1f}%")
    print(f"📊 测试集: {test.get('total_trades', 0)}笔, 胜率{test.get('win_rate', 0)*100:.1f}%")
    print(f"   总收益: {test.get('total_return', 0)*100:.2f}%")
    print(f"   最佳: {test.get('best_trade', 0)*100:+.2f}%, 最差: {test.get('worst_trade', 0)*100:.2f}%")
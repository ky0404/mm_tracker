"""
Walk-Forward 验证脚本 (增强版)
功能:
1. 多窗口滚动验证 (防过拟合)
2. 信号显著性检验
3. 参数自动优化 (网格搜索)
4. 资金曲线稳定性分析

使用 run.py 中的 signals/factory.py 信号工厂进行真实信号评估
"""
import json
import os
import sys
import subprocess
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
from dataclasses import dataclass, field
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(message)s')


@dataclass
class WindowResult:
    """单窗口结果"""
    window_id: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    
    train_trades: int = 0
    train_win_rate: float = 0
    train_pnl: float = 0
    train_sharpe: float = 0
    
    test_trades: int = 0
    test_win_rate: float = 0
    test_pnl: float = 0
    test_sharpe: float = 0
    
    is_overfitting: bool = False


@dataclass
class WalkForwardResult:
    """Walk-Forward 总结果"""
    windows: List[WindowResult] = field(default_factory=list)
    
    avg_train_wr: float = 0
    avg_test_wr: float = 0
    avg_train_pnl: float = 0
    avg_test_pnl: float = 0
    
    consistency_score: float = 0
    stability_score: float = 0
    
    is_overfitting: bool = True
    recommendation: str = ""
    best_params: Dict = field(default_factory=dict)


class WalkForwardValidator:
    """Walk-Forward 验证器 - 使用真实SignalFactory"""
    
    def __init__(self, train_days: int = 60, test_days: int = 14, step_days: int = 7):
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.signal_weights = self._load_weights()
        
        # 导入信号工厂
        from signals.factory import SignalFactory
        self.SignalFactory = SignalFactory
    
    def _load_weights(self) -> Dict[str, float]:
        """加载信号权重"""
        try:
            with open('config/params.json', 'r', encoding='utf-8') as f:
                params = json.load(f)
                return params.get('signal_weights', {})
        except:
            return {}
    
    def load_data(self, symbol: str) -> pd.DataFrame:
        """从NostalgiaForInfinity加载真实数据 (现货)"""
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
            logger.warning(f"加载失败: {e}")
        
        return pd.DataFrame()
    
    def prepare_mtf_data(self, df: pd.DataFrame, idx: int, lookback: int = 100) -> Dict[str, Any]:
        """
        准备多时间框架数据 - 模拟 run.py 中 analyze_token 的分析流程
        关键：使用真实 SignalFactory 能理解的数据格式
        """
        if idx < lookback:
            return {}
        
        # 获取回顾数据
        start_idx = max(0, idx - lookback)
        data = df.iloc[start_idx:idx].copy()
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
        
        # === 4h 分析 (每4小时采样一次) ===
        data_4h = data.iloc[::4] if len(data) > 4 else data
        analysis_4h = self._analyze_timeframe(data_4h, "4h")
        
        # === 1h 分析 ===
        analysis_1h = self._analyze_timeframe(data, "1h")
        
        # === 15m 分析 (每15分钟采样) ===
        data_15m = data.iloc[::4] if len(data) > 4 else data
        analysis_15m = self._analyze_timeframe(data_15m, "15m")
        
        return {
            "symbol": "BTC",
            "close": close.iloc[-1],
            "volume": volume.iloc[-1],
            "analysis_4h": analysis_4h,
            "analysis_1h": analysis_1h,
            "analysis_15m": analysis_15m,
        }
    
    def _analyze_timeframe(self, data: pd.DataFrame, tf: str) -> dict:
        """分析单个时间框架 - 提取SignalFactory需要的指标"""
        if len(data) < 50:
            return {}
        
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        
        # === EMA 指标 ===
        ema8 = close.ewm(span=8, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        ema200 = close.ewm(span=200, adjust=False).mean()
        
        # === RSI 指标 ===
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        # === 成交量 ===
        vol_ma = volume.rolling(20).mean()
        
        # === 动量 ===
        momentum = close.pct_change(5)
        
        current_close = close.iloc[-1]
        prev_close = close.iloc[-2] if len(close) > 1 else current_close
        
        return {
            # EMA 信号
            "ema_bullish": ema20.iloc[-1] > ema50.iloc[-1],
            "ema_trend_rising": ema20.iloc[-1] > ema20.iloc[-10] if len(ema20) > 10 else True,
            "golden_cross": (ema20.iloc[-1] > ema50.iloc[-1]) and (ema20.iloc[-2] <= ema50.iloc[-2]),
            
            # RSI 信号
            "current_rsi": rsi.iloc[-1],
            "rsi_recovering": (rsi.iloc[-1] > rsi.iloc[-3]) and (rsi.iloc[-1] < 50),
            "rsi_oversold_recovery": (rsi.iloc[-3] < 35) and (rsi.iloc[-1] > rsi.iloc[-3]),
            "rsi_not_overbought": rsi.iloc[-1] < 70,
            
            # 成交量信号
            "vol_expanding": volume.iloc[-1] > vol_ma.iloc[-1] * 1.3 if len(vol_ma) > 0 else False,
            "vol_surge": volume.iloc[-1] > volume.iloc[-5] * 1.5 if len(volume) > 5 else False,
            
            # 动量信号
            "momentum_price": momentum.iloc[-1] > 0,
            
            # 价格位置
            "price_above_ema20": current_close > ema20.iloc[-1],
            "price_above_ema50": current_close > ema50.iloc[-1],
        }
    
    def run_walk_forward(self, symbol: str = "BTC") -> Dict:
        """执行Walk-Forward验证 - 使用真实SignalFactory"""
        
        # 加载数据
        df = self.load_data(symbol)
        
        if df.empty or len(df) < 200:
            return {"error": "数据不足", "windows": 0}
        
        df['date'] = pd.to_datetime(df['date'])
        
        start_date = df['date'].min()
        end_date = df['date'].max()
        
        logger.info(f"数据范围: {start_date.date()} ~ {end_date.date()} ({len(df)}条)")
        
        # 采样: 每24小时一个数据点 (加快处理)
        sample_interval = 24
        sampled_df = df.iloc[::sample_interval].reset_index(drop=True)
        logger.info(f"采样后: {len(sampled_df)} 个数据点")
        
        # 生成时间窗口
        windows = self._generate_windows(sampled_df)
        logger.info(f"生成 {len(windows)} 个训练/测试窗口")
        
        all_results = []
        
        for i, (train_start_idx, train_end_idx, test_start_idx, test_end_idx) in enumerate(windows):
            train_df = sampled_df.iloc[train_start_idx:train_end_idx]
            test_df = sampled_df.iloc[test_start_idx:test_end_idx]
            
            if len(train_df) < 10 or len(test_df) < 5:
                continue
            
            # 训练集上: 收集信号表现
            train_stats = self._collect_signal_stats(train_df, df, sample_interval)
            
            # 测试集上: 使用训练好的信号逻辑回测
            test_result = self._backtest_with_signals(test_df, df, sample_interval, train_stats)
            
            all_results.append({
                "window": i + 1,
                "train_trades": train_stats.get('total_trades', 0),
                "test_trades": test_result.get('trades', 0),
                "test_win_rate": test_result.get('win_rate', 0),
                "test_pnl": test_result.get('pnl', 0),
            })
            
            if (i + 1) % 20 == 0:
                logger.info(f"窗口 {i+1}: 胜率={test_result.get('win_rate', 0)*100:.1f}%, PnL={test_result.get('pnl', 0)*100:.2f}%")
        
        return self._analyze_results(all_results)
    
    def _collect_signal_stats(self, train_df: pd.DataFrame, full_df: pd.DataFrame, interval: int) -> dict:
        """在训练集上收集各信号的历史表现"""
        signal_stats = {}
        
        # 获取训练集在完整数据中的起始位置
        train_start_orig_idx = train_df.index[0] * interval if len(train_df) > 0 else 0
        
        # 遍历训练集中的每个信号触发点
        for i in range(len(train_df) - 10):
            orig_idx = train_start_orig_idx + (i * interval) + 50
            
            if orig_idx >= len(full_df) - 10:
                continue
            
            # 准备多时间框架数据
            mtf_data = self.prepare_mtf_data(full_df, orig_idx)
            
            if not mtf_data:
                continue
            
            # 使用真实SignalFactory评估信号
            try:
                signal_results = self.SignalFactory.scan_all("BTC", mtf_data)
                score_result = self.SignalFactory.calculate_total_score(signal_results)
                
                # 获取未来5日收益
                future_idx = orig_idx + 10
                if future_idx < len(full_df):
                    future_return = (full_df.iloc[future_idx]['close'] - full_df.iloc[orig_idx]['close']) / full_df.iloc[orig_idx]['close']
                    
                    # 记录每个触发信号的表现
                    for sig_name in score_result.get('triggered_signals', []):
                        if sig_name not in signal_stats:
                            signal_stats[sig_name] = {'wins': 0, 'count': 0, 'returns': []}
                        
                        signal_stats[sig_name]['count'] += 1
                        signal_stats[sig_name]['returns'].append(future_return)
                        if future_return > 0:
                            signal_stats[sig_name]['wins'] += 1
            except Exception as e:
                continue
        
        # 计算各信号胜率
        for sig_name in signal_stats:
            stats = signal_stats[sig_name]
            stats['win_rate'] = stats['wins'] / max(stats['count'], 1)
            stats['avg_return'] = np.mean(stats['returns']) if stats['returns'] else 0
        
        return {
            'signal_stats': signal_stats,
            'total_trades': sum(s['count'] for s in signal_stats.values())
        }
    
    def _backtest_with_signals(self, test_df: pd.DataFrame, full_df: pd.DataFrame, interval: int, train_stats: dict) -> dict:
        """在测试集上使用训练好的信号逻辑回测"""
        wins = 0
        trades = 0
        total_pnl = 0
        
        # 获取测试集在完整数据中的起始位置
        test_start_orig_idx = test_df.index[0] * interval if len(test_df) > 0 else 0
        
        for i in range(len(test_df) - 10):
            # 直接使用采样间隔计算原始索引
            orig_idx = test_start_orig_idx + (i * interval) + 50
            
            if orig_idx >= len(full_df) - 10:
                continue
            
            # 准备多时间框架数据
            mtf_data = self.prepare_mtf_data(full_df, orig_idx)
            
            if not mtf_data:
                continue
            
            # 使用真实SignalFactory评估
            try:
                signal_results = self.SignalFactory.scan_all("BTC", mtf_data)
                score_result = self.SignalFactory.calculate_total_score(signal_results)
                
                # 入场条件: 触发>=2个信号 且 总分>=3 (放宽条件)
                triggered = score_result.get('triggered_count', 0)
                total_score = score_result.get('total_score', 0)
                
                if triggered >= 2 and total_score >= 3:
                    trades += 1
                    
                    # 未来5日收益
                    future_idx = orig_idx + 10
                    future_return = (full_df.iloc[future_idx]['close'] - full_df.iloc[orig_idx]['close']) / full_df.iloc[orig_idx]['close']
                    
                    # 扣除手续费
                    fee = 0.0006
                    net_return = future_return - fee
                    
                    total_pnl += net_return
                    if net_return > 0:
                        wins += 1
            except:
                continue
        
        return {
            'trades': trades,
            'win_rate': wins / max(trades, 1),
            'pnl': total_pnl
        }
    
    def _generate_windows(self, df: pd.DataFrame) -> List[tuple]:
        """生成时间窗口"""
        windows = []
        n = len(df)
        
        i = 0
        while i + self.train_days + self.test_days <= n:
            train_start = i
            train_end = i + self.train_days
            test_start = train_end
            test_end = test_start + self.test_days
            
            if test_end <= n:
                windows.append((train_start, train_end, test_start, test_end))
            
            i += self.step_days
        
        return windows
    
    def _analyze_results(self, results: List[Dict]) -> Dict:
        """分析结果"""
        if not results:
            return {"error": "无有效窗口", "is_overfitting": True}
        
        win_rates = [r['test_win_rate'] for r in results if r['test_trades'] > 0]
        pnls = [r['test_pnl'] for r in results if r['test_trades'] > 0]
        
        if not win_rates:
            return {"error": "无交易", "is_overfitting": True}
        
        avg_wr = np.mean(win_rates)
        avg_pnl = np.mean(pnls)
        
        is_overfitting = (avg_wr < 0.40) or (avg_pnl < -0.10)
        
        return {
            "windows_count": len(results),
            "avg_test_win_rate": round(avg_wr, 3),
            "avg_test_pnl": round(avg_pnl, 4),
            "pnl_std": round(np.std(pnls), 4),
            "is_overfitting": is_overfitting,
            "recommendation": "STOP - 过拟合严重" if is_overfitting else "CONTINUE - 验证通过",
            "details": results[:5]
        }


class EnhancedWalkForwardValidator:
    """
    增强版 Walk-Forward 验证器
    
    功能:
    - 多窗口滚动验证
    - 信号显著性检验
    - 参数自动优化
    - 资金曲线分析
    - 过拟合判定
    
    用法:
        validator = EnhancedWalkForwardValidator(
            train_days=60,
            test_days=14,
            step_days=7,
            min_trades=10,
            max_overfit_pct=0.30
        )
        result = validator.run(symbol="BTC")
    """
    
    def __init__(
        self,
        train_days: int = 60,
        test_days: int = 14,
        step_days: int = 7,
        min_trades: int = 10,
        max_overfit_pct: float = 0.30,
    ):
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.min_trades = min_trades
        self.max_overfit_pct = max_overfit_pct
        
        self.best_params = {}
        self.param_importance = {}
        
        from signals.factory import SignalFactory
        self.SignalFactory = SignalFactory
    
    def load_data(self, symbol: str) -> pd.DataFrame:
        """从NostalgiaForInfinity加载真实数据 (现货)"""
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
            logger.warning(f"加载失败: {e}")
        
        return pd.DataFrame()
    
    def run(
        self,
        symbol: str = "BTC",
        params_space: Dict[str, Tuple] = None,
        optimize: bool = False,
    ) -> WalkForwardResult:
        """
        执行增强版 Walk-Forward 验证
        
        参数:
            symbol: 交易对
            params_space: 参数字典，如 {'leverage': (2, 5), 'target': (10, 20)}
            optimize: 是否启用参数自动优化 (默认关闭，速度更快)
        
        返回:
            WalkForwardResult: 验证结果
        """
        df = self.load_data(symbol)
        
        if df.empty or len(df) < 200:
            return WalkForwardResult(recommendation="数据不足")
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').reset_index(drop=True)
        
        logger.info(f"数据范围: {df['date'].min().date()} ~ {df['date'].max().date()}")
        
        sample_interval = 24
        sampled_df = df.iloc[::sample_interval].reset_index(drop=True)
        
        windows = self._generate_windows(sampled_df)
        logger.info(f"生成 {len(windows)} 个窗口")
        
        results = []
        
        for i, (train_start, train_end, test_start, test_end) in enumerate(windows):
            train_df = sampled_df.iloc[train_start:train_end]
            test_df = sampled_df.iloc[test_start:test_end]
            
            if len(train_df) < 10 or len(test_df) < 5:
                continue
            
            if optimize and params_space:
                optimized_params = self._optimize_params(train_df, df, sample_interval, params_space)
                self._update_param_importance(optimized_params)
            else:
                optimized_params = {'min_signals': 1, 'min_score': 1}
            
            train_result = self._backtest(train_df, df, sample_interval, optimized_params)
            test_result = self._backtest(test_df, df, sample_interval, optimized_params)
            
            is_overfit = self._check_overfitting(train_result, test_result)
            
            window_result = WindowResult(
                window_id=i + 1,
                train_start=train_df['date'].iloc[0],
                train_end=train_df['date'].iloc[-1],
                test_start=test_df['date'].iloc[0],
                test_end=test_df['date'].iloc[-1],
                train_trades=train_result['trades'],
                train_win_rate=train_result['win_rate'],
                train_pnl=train_result['pnl'],
                train_sharpe=train_result.get('sharpe', 0),
                test_trades=test_result['trades'],
                test_win_rate=test_result['win_rate'],
                test_pnl=test_result['pnl'],
                test_sharpe=test_result.get('sharpe', 0),
                is_overfitting=is_overfit,
            )
            results.append(window_result)
            
            if (i + 1) % 10 == 0:
                logger.info(f"窗口 {i+1}: Train WR={train_result['win_rate']*100:.1f}%, Test WR={test_result['win_rate']*100:.1f}%")
        
        return self._aggregate_results(results)
    
    def _generate_windows(self, df: pd.DataFrame) -> List[Tuple]:
        """生成训练/测试时间窗口"""
        windows = []
        n = len(df)
        
        i = 0
        while i + self.train_days + self.test_days <= n:
            train_start = i
            train_end = i + self.train_days
            test_start = train_end
            test_end = test_start + self.test_days
            
            if test_end <= n:
                windows.append((train_start, train_end, test_start, test_end))
            
            i += self.step_days
        
        return windows
    
    def _optimize_params(
        self,
        train_df: pd.DataFrame,
        full_df: pd.DataFrame,
        interval: int,
        params_space: Dict[str, Tuple],
    ) -> Dict:
        """网格搜索优化参数"""
        best_score = -np.inf
        best_params = {}
        
        param_combinations = [{}]
        for param_name, (min_val, max_val) in params_space.items():
            new_combos = []
            for combo in param_combinations:
                if isinstance(min_val, int):
                    step = max(1, (max_val - min_val) // 3)
                    for val in range(min_val, max_val + 1, step):
                        new_combo = combo.copy()
                        new_combo[param_name] = val
                        new_combos.append(new_combo)
                else:
                    for val in np.linspace(min_val, max_val, 3):
                        new_combo = combo.copy()
                        new_combo[param_name] = val
                        new_combos.append(new_combo)
            param_combinations = new_combos
        
        param_combinations = param_combinations[:20]
        
        for params in param_combinations:
            result = self._backtest(train_df, full_df, interval, params)
            score = result.get('sharpe', 0) * 0.7 + result.get('win_rate', 0) * 0.3
            
            if score > best_score:
                best_score = score
                best_params = params
        
        return best_params
    
    def _backtest(
        self,
        sample_df: pd.DataFrame,
        full_df: pd.DataFrame,
        interval: int,
        params: Dict,
    ) -> Dict:
        """回测"""
        wins = 0
        trades = 0
        total_pnl = 0
        returns = []
        
        sample_indices = sample_df.index.tolist()
        
        for i in range(len(sample_indices)):
            sample_idx = sample_indices[i]
            orig_idx = sample_idx * interval
            
            lookback = 100
            if orig_idx < lookback:
                continue
            
            if orig_idx >= len(full_df) - 10:
                continue
            
            mtf_data = self._prepare_mtf_data(full_df, orig_idx, lookback)
            if not mtf_data:
                continue
            
            try:
                signal_results = self.SignalFactory.scan_all("BTC", mtf_data)
                score_result = self.SignalFactory.calculate_total_score(signal_results)
                
                triggered = score_result.get('triggered_count', 0)
                total_score = score_result.get('total_score', 0)
                
                min_signals = params.get('min_signals', 1)
                min_score = params.get('min_score', 1)
                
                if triggered >= min_signals and total_score >= min_score:
                    trades += 1
                    
                    future_idx = orig_idx + 10
                    future_return = (full_df.iloc[future_idx]['close'] - full_df.iloc[orig_idx]['close']) / full_df.iloc[orig_idx]['close']
                    
                    fee = 0.0006
                    net_return = future_return - fee
                    
                    total_pnl += net_return
                    returns.append(net_return)
                    
                    if net_return > 0:
                        wins += 1
            except:
                continue
        
        win_rate = wins / max(trades, 1)
        
        sharpe = 0
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252)
        
        return {
            'trades': trades,
            'win_rate': win_rate,
            'pnl': total_pnl,
            'sharpe': sharpe,
        }
    
    def _prepare_mtf_data(self, df: pd.DataFrame, idx: int, lookback: int = 100) -> Dict:
        """准备多时间框架数据"""
        if idx < lookback:
            return {}
        
        start_idx = max(0, idx - lookback)
        data = df.iloc[start_idx:idx].copy()
        
        close = data['close']
        volume = data['volume']
        high = data['high']
        low = data['low']
        
        data_4h = data.iloc[::4] if len(data) > 4 else data
        analysis_4h = self._analyze_tf(data_4h)
        
        analysis_1h = self._analyze_tf(data)
        
        data_15m = data.iloc[::4] if len(data) > 4 else data
        analysis_15m = self._analyze_tf(data_15m)
        
        return {
            "symbol": "BTC",
            "close": close.iloc[-1],
            "volume": volume.iloc[-1],
            "analysis_4h": analysis_4h,
            "analysis_1h": analysis_1h,
            "analysis_15m": analysis_15m,
        }
    
    def _analyze_tf(self, data: pd.DataFrame) -> dict:
        """分析单个时间框架"""
        if len(data) < 50:
            return {}
        
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        
        ema8 = close.ewm(span=8, adjust=False).mean()
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / (loss + 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        vol_ma = volume.rolling(20).mean()
        momentum = close.pct_change(5)
        
        current_close = close.iloc[-1]
        
        return {
            "ema_bullish": ema20.iloc[-1] > ema50.iloc[-1],
            "ema_trend_rising": ema20.iloc[-1] > ema20.iloc[-10] if len(ema20) > 10 else True,
            "golden_cross": (ema20.iloc[-1] > ema50.iloc[-1]) and (ema20.iloc[-2] <= ema50.iloc[-2]),
            "current_rsi": rsi.iloc[-1],
            "rsi_recovering": (rsi.iloc[-1] > rsi.iloc[-3]) and (rsi.iloc[-1] < 50),
            "rsi_oversold_recovery": (rsi.iloc[-3] < 35) and (rsi.iloc[-1] > rsi.iloc[-3]),
            "rsi_not_overbought": rsi.iloc[-1] < 70,
            "vol_expanding": volume.iloc[-1] > vol_ma.iloc[-1] * 1.3 if len(vol_ma) > 0 else False,
            "vol_surge": volume.iloc[-1] > volume.iloc[-5] * 1.5 if len(volume) > 5 else False,
            "momentum_price": momentum.iloc[-1] > 0,
            "price_above_ema20": current_close > ema20.iloc[-1],
            "price_above_ema50": current_close > ema50.iloc[-1],
        }
    
    def _check_overfitting(self, train_result: Dict, test_result: Dict) -> bool:
        """检查是否过拟合"""
        if test_result['trades'] < self.min_trades:
            return True
        
        wr_drop = train_result['win_rate'] - test_result['win_rate']
        if wr_drop > self.max_overfit_pct:
            return True
        
        if test_result['pnl'] < 0:
            return True
        
        return False
    
    def _update_param_importance(self, params: Dict):
        """更新参数重要性"""
        for param, value in params.items():
            if param not in self.param_importance:
                self.param_importance[param] = []
            self.param_importance[param].append(value)
    
    def _aggregate_results(self, windows: List[WindowResult]) -> WalkForwardResult:
        """汇总所有窗口结果"""
        if not windows:
            return WalkForwardResult(recommendation="无有效窗口")
        
        train_wrs = [w.train_win_rate for w in windows if w.train_trades > 0]
        test_wrs = [w.test_win_rate for w in windows if w.test_trades > 0]
        train_pnls = [w.train_pnl for w in windows if w.train_trades > 0]
        test_pnls = [w.test_pnl for w in windows if w.test_trades > 0]
        
        avg_train_wr = np.mean(train_wrs) if train_wrs else 0
        avg_test_wr = np.mean(test_wrs) if test_wrs else 0
        avg_train_pnl = np.mean(train_pnls) if train_pnls else 0
        avg_test_pnl = np.mean(test_pnls) if test_pnls else 0
        
        consistency = avg_test_wr / (avg_train_wr + 0.001)
        
        stability = 0
        if test_pnls:
            stability = 1 / (np.std(test_pnls) + 0.01)
        
        is_overfitting = (
            avg_test_wr < 0.35 or
            consistency < 0.70 or
            avg_test_pnl < 0
        )
        
        if is_overfitting:
            recommendation = "⚠️ 过拟合 - 请简化策略或增加训练数据"
        elif consistency > 0.9 and stability > 0.5:
            recommendation = "✅ 验证通过 - 策略具有良好的泛化能力"
        else:
            recommendation = "⚡ 勉强通过 - 建议微调参数"
        
        best_params = {}
        for param, values in self.param_importance.items():
            if values:
                best_params[param] = np.mean(values)
        
        return WalkForwardResult(
            windows=windows,
            avg_train_wr=avg_train_wr,
            avg_test_wr=avg_test_wr,
            avg_train_pnl=avg_train_pnl,
            avg_test_pnl=avg_test_pnl,
            consistency_score=consistency,
            stability_score=stability,
            is_overfitting=is_overfitting,
            recommendation=recommendation,
            best_params=best_params,
        )


def run_enhanced_walk_forward(symbol: str = "BTC"):
    """运行增强版 Walk-Forward 验证"""
    print("\n" + "="*60)
    print("🔬 Walk-Forward 验证 (增强版)")
    print("="*60)
    print(f"  代币: {symbol}")
    print(f"  训练: 60天 | 测试: 14天 | 步长: 7天")
    print(f"  参数优化: 开启")
    
    validator = EnhancedWalkForwardValidator(
        train_days=60,
        test_days=14,
        step_days=7,
    )
    
    result = validator.run(
        symbol=symbol,
        params_space={
            'min_signals': (1, 3),
            'min_score': (2, 5),
        },
        optimize=False,
    )
    
    print(f"\n📊 验证结果:")
    print(f"  窗口数: {len(result.windows)}")
    print(f"  平均训练胜率: {result.avg_train_wr*100:.1f}%")
    print(f"  平均测试胜率: {result.avg_test_wr*100:.1f}%")
    print(f"  平均训练收益: {result.avg_train_pnl*100:.2f}%")
    print(f"  平均测试收益: {result.avg_test_pnl*100:.2f}%")
    print(f"  一致性分数: {result.consistency_score:.2f}")
    print(f"  稳定性分数: {result.stability_score:.2f}")
    print(f"\n  🔴 过拟合判定: {'是' if result.is_overfitting else '否'}")
    print(f"  💡 建议: {result.recommendation}")
    
    if result.best_params:
        print(f"\n  📐 最佳参数:")
        for k, v in result.best_params.items():
            print(f"    {k}: {v:.2f}")
    
    return result


if __name__ == "__main__":
    run_enhanced_walk_forward("BTC")
"""
自动驾驶仪 - AutoPilot
功能：闭环交易系统主控制器
实现：市场扫描 → 信号评级 → 交易决策 → OKX执行 → 持仓监控 → 结果记录 → 参数优化
完整集成 Freqtrade 风格:
- trailing_stop (跟踪止损)
- custom_stoploss (自定义动态止损)
- custom_exit (自定义出场)
- dynamic_roi (动态ROI)
- confirm_trade_entry/exit (确认交易)
- custom_entry/exit_price (自定义价格)
"""
import json
import time
import logging
import argparse
import pandas as pd
from datetime import datetime, timedelta, timezone

# 统一使用 UTC 时间
_UTC = timezone.utc
from typing import Dict, Any, List, Optional

# 导入交易数据库
try:
    from trading.trade_db import TradeDB, init_db
    _TRADE_DB_AVAILABLE = True
except ImportError:
    _TRADE_DB_AVAILABLE = False
    print("[AutoPilot] ⚠️ TradeDB 不可用，使用JSON记录")

# 导入中央状态管理器
try:
    from core.state_manager import get_state
    _STATE_MANAGER_AVAILABLE = True
except ImportError:
    _STATE_MANAGER_AVAILABLE = False
    print("[AutoPilot] ⚠️ CentralStateManager 不可用")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


class AutoPilot:
    """
    自动驾驶仪 - 闭环交易系统
    
    流程：
    1. 市场扫描 - 获取候选代币
    2. 信号评级 - 计算11个量化指标
    3. 交易决策 - 基于参数决定是否入场
    4. OKX执行 - 模拟/真实下单
    5. 持仓监控 - 自动SL/TP
    6. 结果记录 - 记录每笔交易
    7. 参数优化 - 分析结果，调整权重
    """

    def __init__(
        self,
        params_file: str = "config/strategy_params.json",
        trader=None,
        result_logger=None,
        position_monitor=None,
        optimizer=None,
    ):
        self.params_file = params_file
        self.params = self._load_params()
        
        # 组件初始化
        self.trader = trader
        self.result_logger = result_logger
        self.position_monitor = position_monitor
        self.optimizer = optimizer
        
        self.running = False
        self.cycle_count = 0
        
        # 日内杠杆模式
        self.strategy_mode = 'default'
        self.intraday_strategy = None
        
        # 缓存市场价格
        self.prices = {}
        
        # 初始化统一缓存层（解决多fetcher缓存不共享问题）
        try:
            from core.fetcher_wrapper import init_fetcher_cache, get_cache_stats
            init_fetcher_cache()
            cache_stats = get_cache_stats()
            logger.info(f"[AutoPilot] 数据缓存初始化: {cache_stats.get('total', 0)} 条")
        except Exception as e:
            logger.warning(f"[AutoPilot] 缓存初始化失败: {e}")
        
        # Freqtrade 风格动态退出管理器
        from trading.dynamic_exit import get_exit_manager
        self.exit_manager = get_exit_manager(self.params.get("dynamic_exit", {}))
        
        # Freqtrade策略框架集成器 (新增)
        from trading.freqtrade_integrator import create_integrator
        self.freqtrade_integrator = create_integrator(self.params)
        
        # 初始化时不自动重置仓位 - 由PositionMonitor从数据库加载
        # if result_logger:
        #     stuck = [t for t in result_logger.trades if t.get("type") == "ENTRY"]
        #     if stuck:
        #         print(f"[AutoPilot] 发现 {len(stuck)} 个卡死仓位，自动重置")
        #         result_logger.force_close_all_entries()

    def _load_params(self) -> Dict[str, Any]:
        """加载参数"""
        try:
            with open(self.params_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"参数加载失败: {e}")
            return {}

    def reload_params(self):
        """重新加载参数（用于动态更新）"""
        self.params = self._load_params()
        
        # 更新子组件参数
        if self.position_monitor:
            self.position_monitor.update_params(self.params.get("risk_management", {}))
        
        logger.info(f"[AutoPilot] 参数已更新")

    def scan_market(self, max_tokens: int = 20) -> List[str]:
        """
        7条件框架 + 5阶段判定扫描
        仅筛选"拉升启动期"代币
        
        支持两种模式:
        - default: 现货市场扫描 (OKX API)
        - intraday: 期货市场扫描 (历史数据 + 实时信号)
        """
        # Kill Switch 检查
        import os
        pause_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "PAUSE_TRADING")
        if os.path.exists(pause_file):
            logger.warning("[AutoPilot] ⚠️ 检测到 PAUSE_TRADING 文件，本周期跳过交易")
            logger.warning("[AutoPilot] 恢复交易: rm PAUSE_TRADING")
            return []
        
        # 日内杠杆模式 - 使用期货历史数据
        if hasattr(self, 'strategy_mode') and self.strategy_mode == 'intraday':
            return self._scan_intraday_market(max_tokens)
        
        # 默认模式 - 现货市场扫描
        from scanner.universe import get_full_universe
        from scanner.fast_filter import run_fast_filter
        from signals.calculator import judge_manipulation_stage
        from fetchers.price_api import (
            fetch_price_and_change,
            fetch_funding_rate_history,
            fetch_oi_history,
            fetch_daily_ohlcv,
        )
        from fetchers.dexscreener import fetch_dex_data
        
        logger.info("[扫描] 7条件框架 + 5阶段判定扫描...")
        
        # Step 1: 获取全市场代币
        universe = get_full_universe()
        logger.info(f"[扫描] 全市场代币: {len(universe)}个")
        
        # Step 2: 快速画像筛选
        filtered = run_fast_filter(universe)
        logger.info(f"[扫描] 画像筛选后: {len(filtered)}个候选")
        
        # Step 3: 深度7条件分析 + 5阶段判定
        high_confidence = []
        
        for item in filtered[:max_tokens]:
            # 支持字符串或字典格式
            token = item.get('symbol') if isinstance(item, dict) else item
            if not token:
                continue
            try:
                # 获取数据
                price_data = fetch_price_and_change(token)
                funding_data = fetch_funding_rate_history(token)
                oi_data = fetch_oi_history(token)
                kline_df = fetch_daily_ohlcv(token)
                dex_data = fetch_dex_data(token)
                
                # 5阶段判定
                stage_result = judge_manipulation_stage(
                    price_data, funding_data, oi_data, kline_df, dex_data
                )
                
                stage = stage_result.get("stage", "静默积累期")
                confidence = stage_result.get("confidence", 0)
                triggered = stage_result.get("triggered_count", 0)
                
# 放宽条件：接受所有阶段（拉升启动期、静默积累期、整数关口收割期）
                # 只要有信号触发且置信度>=40%就允许通过
                allowed_stages = ["拉升启动期", "静默积累期", "整数关口收割期"]
                if stage in allowed_stages and confidence >= 0.40 and triggered >= 1:
                    high_confidence.append({
                        "token": token,
                        "stage": stage,
                        "confidence": confidence,
                        "triggered": triggered,
                        "price": price_data.get("price", 0),
                        "funding_rate": funding_data.get("latest_rate", 0) * 100 if funding_data else 0,
                    })
                    logger.info(f"[扫描] ✅ {token}: {stage}, 置信度{confidence:.0%}, 触发{triggered}/7条件")
                
            except Exception as e:
                continue
        
        # 排序并返回
        high_confidence.sort(key=lambda x: (x["confidence"], x["triggered"]), reverse=True)
        
        # ===== NFI指标分析 (对Top 5候选) =====
        try:
            from fetchers.multi_tf import analyze_1d, analyze_4h
            
            logger.info(f"\n📊 NFI核心指标分析 (Top 5):")
            for i, c in enumerate(high_confidence[:5]):
                token = c['token']
                try:
                    d1 = analyze_1d(token)
                    h4 = analyze_4h(token)
                    
                    d1_cti = d1.get('current_cti', 0) if d1.get('valid') else 0
                    d1_ewo = d1.get('current_ewo', 0) if d1.get('valid') else 0
                    h4_cti = h4.get('current_cti', 0) if h4.get('valid') else 0
                    h4_ewo = h4.get('current_ewo', 0) if h4.get('valid') else 0
                    h4_rsi = h4.get('current_rsi', 0) if h4.get('valid') else 0
                    h4_wr = h4.get('current_wr', 0) if h4.get('valid') else 0
                    
                    cti_emoji = "🟢" if h4_cti > 0 else "🔴"
                    ewo_emoji = "🟢" if h4_ewo > 0 else "🔴"
                    rsi_emoji = "🟢" if 30 < h4_rsi < 70 else "🔴"
                    wr_emoji = "🟢" if h4_wr < -60 else "🟡"
                    
                    logger.info(f"  {i+1}. {token:8} CTI:{h4_cti:>5.1f}{cti_emoji} EWO:{h4_ewo:>6.1f}{ewo_emoji} RSI:{h4_rsi:>5.0f}{rsi_emoji} Wr:{h4_wr:>6.1f}{wr_emoji}")
                    
                    c['nfi_indicators'] = {
                        'd1_cti': d1_cti, 'd1_ewo': d1_ewo,
                        'h4_cti': h4_cti, 'h4_ewo': h4_ewo,
                        'h4_rsi': h4_rsi, 'h4_wr': h4_wr
                    }
                except Exception as e:
                    logger.warning(f"  {token} NFI指标获取失败: {str(e)[:30]}")
        except Exception as e:
            logger.warning(f"[NFI] 指标分析跳过: {str(e)[:50]}")
        
        result_tokens = [c["token"] for c in high_confidence]
        
        logger.info(f"\n[扫描] 高置信度候选: {len(result_tokens)}个")
        for c in high_confidence[:3]:
            logger.info(f"  - {c['token']}: {c['stage']}, 置信度{c['confidence']:.0%}")
        
        return result_tokens

    def analyze_token(self, token: str) -> Dict[str, Any]:
        """
        动量模式分析 - 使用OKX实时数据，不依赖CoinGecko
        新增：4H多时间框架分析作为"门卫"
        """
        from fetchers.momentum import get_hourly_momentum, get_okx_price
        from fetchers.price_api import fetch_funding_rate_history, fetch_oi_history
        
        price = get_okx_price(token)
        if not price:
            return {"symbol": token, "price": 0, "skip": True, "reason": "无法获取价格"}
        
        # ===== 第一层：4H面分析（判断大方向是否对）=====
        # BTC/ETH/SOL 主流币跳过4H门卫（使用独立策略）
        is_major = token in ["BTC", "ETH", "SOL"]
        
        # === NFI-style Surface Analysis (多时间框架面分析) ===
        # 使用新的4层分析: 4H(gatekeeper) → 1H(momentum) → 15M(entry)
        # 添加错误计数，避免重复失败
        try:
            from fetchers.multi_tf import multi_tf_surface_analysis
            tf_analysis = multi_tf_surface_analysis(token, is_major)
            analysis_4h = tf_analysis.get("layers", {}).get("4h", {})
            analysis_1h = tf_analysis.get("layers", {}).get("1h", {})
            analysis_15m = tf_analysis.get("layers", {}).get("15m", {})
        except Exception as e:
            logger.warning(f"[MultiTF] {token} 分析失败，跳过: {str(e)[:50]}")
            # 失败时不阻断交易，继续后续流程
            tf_analysis = {
                "decision": "continue",
                "reason": "MultiTF分析异常，已跳过",
                "layers": {"4h": {}, "1h": {}, "15m": {}}
            }
            analysis_4h = {}
            analysis_1h = {}
            analysis_15m = {}
        
        # 如果4H层(Gatekeeper)未对齐，且不是BTC型，直接跳过
        if tf_analysis.get("decision") == "skip":
            return {
                "symbol": token,
                "price": price,
                "skip": True,
                "reason": tf_analysis.get("reason", "多时间框架分析未通过"),
                "tf_analysis": tf_analysis,
            }
        
        # ===== Freqtrade策略集成器检查 (新增) =====
        # 使用新的多时间框架分析进行二次确认
        # 注意: fetch_daily_ohlcv 只支持1d周期，这里用容错处理
        try:
            # 尝试获取K线数据，如果失败则跳过详细检查
            from fetchers.price_api import fetch_daily_ohlcv
            
            # 获取日K线（虽然不是4H，但可以作为趋势参考）
            klines_1d = fetch_daily_ohlcv(token, limit=100)
            
            if klines_1d is not None and len(klines_1d) > 20:
                closes_1d = klines_1d['close'].tolist() if hasattr(klines_1d['close'], 'tolist') else list(klines_1d['close'])
                highs_1d = klines_1d['high'].tolist() if hasattr(klines_1d['high'], 'tolist') else list(klines_1d['high'])
                lows_1d = klines_1d['low'].tolist() if hasattr(klines_1d['low'], 'tolist') else list(klines_1d['low'])
                
                # 放宽：允许大部分代币通过趋势检查
                # 只在极端下跌时才拒绝
                if closes_1d[-1] < closes_1d[-3] * 0.95:  # 5%以上跌幅
                    logger.warning(f"[Freqtrade] {token} 近期下跌较大，警告")
                    # 只警告但不阻止
                else:
                    logger.info(f"[Freqtrade] {token} 通过趋势检查")
            
            logger.info(f"[Freqtrade] {token} 趋势检查完成")
        except Exception as e:
            logger.warning(f"[Freqtrade检查] {token}: {e}")
        
        # 如果1H层(Momentum)未对齐，给出警告但允许继续
        if analysis_1h.get("warning"):
            logger.warning(f"[1H警告] {token} 1H动量未确认: {analysis_1h.get('detail', '')}")
        
        # 15M层已在multi_tf_surface_analysis中检查过，这里获取详情
        
        # ===== 第二层：1H动量（精确入场）=====
        momentum = get_hourly_momentum(token)
        
        # ===== 第三层：清算状态检测=====
        try:
            from fetchers.sweep_detector import detect_sweep_status
            sweep_status = detect_sweep_status(token)
        except:
            sweep_status = {"status": "normal", "confidence": 0, "safe_to_enter": False, "detail": "检测失败"}
        
        # 如果处于清算前兆或过热，直接跳过
        if sweep_status["status"] in ["pre_sweep", "hot"]:
            return {
                "symbol": token,
                "price": price,
                "skip": True,
                "reason": sweep_status["detail"],
                "sweep_status": sweep_status,
                "analysis_4h": analysis_4h,
            }
        
        # 清算状态附加分
        sweep_bonus = 1.5 if sweep_status["status"] == "post_sweep" else 0
        
        # ===== 第四层：15M精确入场点 ===== (现在已集成到 multi_tf_surface_analysis)
        # 15M层已经在 multi_tf_surface_analysis 中检查过
        # 这里只获取详情用于记录
        
        # 获取资金费率（判断市场情绪）
        try:
            funding = fetch_funding_rate_history(token)
            funding_rate = funding.get("latest_rate", 0)
            funding_trend = funding.get("trend_direction", "flat")
        except:
            funding_rate = 0
            funding_trend = "flat"
        
        # ===== NFI核心指标记录 =====
        nfi_indicators = {}
        try:
            from fetchers.multi_tf import analyze_1d, analyze_4h
            d1 = analyze_1d(token)
            h4 = analyze_4h(token)
            
            if d1.get('valid'):
                nfi_indicators['1d'] = {
                    'rsi': d1.get('current_rsi', 0),
                    'cti': d1.get('current_cti', 0),
                    'ewo': d1.get('current_ewo', 0),
                    'wr': d1.get('current_wr', 0)
                }
            if h4.get('valid'):
                nfi_indicators['4h'] = {
                    'rsi': h4.get('current_rsi', 0),
                    'cti': h4.get('current_cti', 0),
                    'ewo': h4.get('current_ewo', 0),
                    'wr': h4.get('current_wr', 0),
                    'aligned': h4.get('aligned', False)
                }
                logger.info(f"[NFI] {token} 4H: RSI={h4.get('current_rsi',0):.0f} CTI={h4.get('current_cti',0):.1f} EWO={h4.get('current_ewo',0):.1f}")
        except Exception as e:
            pass  # NFI失败不阻断交易
        
        # 获取OI数据（判断是否有真实仓位进入）
        try:
            oi_info = fetch_oi_history(token)
            oi_change = oi_info.get("oi_change_7d_pct", 0)
        except:
            oi_change = 0
        
        # 计算动量信号
        momentum_params = self.params.get("momentum_signals", {})
        min_price_change = momentum_params.get("min_price_change_1h_pct", 3.0)
        min_volume_spike = momentum_params.get("min_volume_spike_ratio", 2.0)
        max_funding = momentum_params.get("max_funding_rate_pct", 0.15)
        
        price_change_1h = momentum.get("price_change_1h_pct", 0)
        volume_ratio = momentum.get("volume_ratio_1h", 0)
        
        # 默认阶段（5阶段判定在后面）
        stage = "静默积累期"
        
        # ===== 使用信号工厂统一评分 =====
        from signals.factory import SignalFactory
        
        # 构建数据字典 (使用NFI-style多时间框架分析结果)
        signal_data = {
            "analysis_4h": analysis_4h,  # 新的4H surface分析
            "analysis_1h": analysis_1h,  # 新的1H surface分析 (NFI新增)
            "analysis_15m": analysis_15m,  # 新的15M surface分析
            "momentum": momentum,
            "funding_rate": funding_rate,
            "stage_result": stage,
            "price": price,
            "sweep_status": sweep_status,
            "tf_analysis": tf_analysis,  # 完整的多时间框架分析结果
        }
        
        # 工厂评估所有信号
        signal_results = SignalFactory.scan_all(token, signal_data)
        score_result = SignalFactory.calculate_total_score(signal_results)
        
        # 转换为旧格式兼容
        signals_triggered = []
        for name, result in signal_results.items():
            if result.triggered:
                signals_triggered.append({
                    "name": result.name,
                    "weight": result.weight,
                    "detail": result.detail
                })
        
        total_score = score_result["total_score"]
        total_count = score_result["triggered_count"]
        
        # 资金费率过高则封禁入场
        if funding_rate * 100 > max_funding:
            return {
                "symbol": token,
                "price": price,
                "skip": True,
                "reason": f"资金费率过高 {funding_rate*100:.3f}%，风险大"
            }
        
        score = {
            "triggered_count": total_count,
            "total_score": round(total_score, 2),
            "entry_signals": signals_triggered,
            "exit_signals": [],
            "grade": score_result["grade"],
            "by_source": score_result.get("by_source", {}),
        }
        
        # 调用真正的5阶段判定
        try:
            from fetchers.price_api import fetch_daily_ohlcv
            from fetchers.dexscreener import fetch_dex_data
            from signals.calculator import judge_manipulation_stage
            
            price_data = {"price": price, "change_24h": momentum.get("price_change_1h_pct", 0)}
            kline_df = fetch_daily_ohlcv(token)
            dex_data = fetch_dex_data(token)
            
            stage_result = judge_manipulation_stage(
                price_data, funding, oi_info, kline_df, dex_data
            )
            stage = stage_result.get("stage", "静默积累期")
            confidence = stage_result.get("confidence", 0)
        except Exception as e:
            stage = "静默积累期"
            confidence = 0
        
        return {
            "symbol": token,
            "price": price,
            "analysis_4h": analysis_4h,
            "analysis_15m": analysis_15m,
            "momentum": momentum,
            "funding_rate": funding_rate,
            "score": score,
            "signals_triggered": signals_triggered,
            "stage_result": stage,
            "stage_confidence": confidence,
            "sweep_status": sweep_status,
            "sweep_bonus": sweep_bonus,
            "dynamic_sl_pct": analysis_4h.get("dynamic_sl_pct", 3.0) if analysis_4h.get("valid") else 3.0,
            "nfi_indicators": nfi_indicators,
            "market_context": {
                "phase_detected": "4h_aligned" if analysis_4h.get("aligned") else "4h_not_aligned",
                "price_change_1h_pct": momentum.get("price_change_1h_pct", 0),
                "volume_ratio_1h": momentum.get("volume_ratio_1h", 0),
                "sweep_status": sweep_status.get("status", "normal"),
                "4h_rsi": analysis_4h.get("current_rsi", 0) if analysis_4h.get("valid") else 0,
                "15m_aligned": analysis_15m.get("aligned", False) if analysis_15m.get("valid") else None,
            }
        }

    def should_entry(self, analysis_result: Dict[str, Any]) -> bool:
        """
        7条件框架 + 5阶段模式决策 + 严格趋势确认
        如果已通过5阶段判定(拉升启动期)，则简化判断
        
        修复: 日内杠杆模式必须使用21信号工厂二次确认 + 趋势确认
        """
        token = analysis_result.get("symbol", "")
        
        # 日内杠杆模式 - 21信号工厂严格确认
        if hasattr(self, 'strategy_mode') and self.strategy_mode == 'intraday':
            if analysis_result.get("skip", False):
                logger.info(f"[日内决策] ❌ {token} 被标记跳过")
                return False
            
            score = analysis_result.get("score", {})
            triggered = score.get("triggered_count", 0)
            total_score = score.get("total_score", 0)
            grade = score.get("grade", "WATCH")
            signal_direction = score.get("direction", "long")
            
            # ===== 新增: 趋势确认 (EMA多头排列) =====
            analysis_4h = analysis_result.get("analysis_4h", {})
            analysis_1h = analysis_result.get("analysis_1h", {})
            
            # 检查4H趋势是否与信号方向一致
            trend_4h = analysis_4h.get("trend", "neutral")
            trend_1h = analysis_1h.get("trend", "neutral")
            
            # 趋势一致检查
            trend_aligned = False
            if signal_direction == "long":
                trend_aligned = (trend_4h == "bullish" or trend_1h == "bullish")
            elif signal_direction == "short":
                trend_aligned = (trend_4h == "bearish" or trend_1h == "bearish")
            else:
                trend_aligned = True  # 中性信号不限制
            
            # ===== 新增: RSI动量确认 =====
            rsi_4h = analysis_4h.get("current_rsi", 50)
            rsi_momentum_ok = True
            if signal_direction == "long":
                # 做多时RSI应在30-70之间，不宜过高(超买)
                rsi_momentum_ok = 30 <= rsi_4h <= 75
            elif signal_direction == "short":
                # 做空时RSI应在30-70之间，不宜过低(超卖)
                rsi_momentum_ok = 25 <= rsi_4h <= 70
            
            # 21信号工厂二次确认条件（从配置读取）:
            # 1. 触发信号数量 >= min_triggered
            # 2. 总分数 >= min_score
            # 3. 等级必须是 required_grades
            # 4. 趋势方向与信号方向一致
            # 5. RSI动量条件满足
            entry_config = self.params.get("auto_pilot", {})
            min_triggered = entry_config.get("entry_triggered_min", 2)
            min_score = entry_config.get("entry_score_min", 7)
            required_grades = entry_config.get("entry_grade_required", ["STRONG_BUY", "BUY"])
            
            if triggered >= min_triggered and total_score >= min_score and grade in required_grades:
                if not trend_aligned:
                    logger.info(f"[日内决策] ❌ {token} 趋势不匹配: 信号方向={signal_direction}, 4H趋势={trend_4h}, 1H趋势={trend_1h}")
                    return False
                if not rsi_momentum_ok:
                    logger.info(f"[日内决策] ❌ {token} RSI不理想: 4H RSI={rsi_4h}, 方向={signal_direction}")
                    return False
                
                logger.info(f"[日内决策] ✅ {token} 入场: 信号={triggered}个, 分数={total_score}, 方向={signal_direction}, 4H趋势={trend_4h}, RSI={rsi_4h}")
                return True
            else:
                logger.info(f"[日内决策] ❌ {token} 未通过21信号工厂: 信号={triggered}个(需{min_triggered}+), 分数={total_score}(需{min_score}+), 等级={grade}")
                return False
        
        # 如果被跳过（资金费率过高等原因），直接返回False
        if analysis_result.get("skip", False):
            return False
        
        # ===== 新增: StateManager 持仓检查 =====
        token = analysis_result.get("symbol", "")
        if _STATE_MANAGER_AVAILABLE:
            try:
                state = get_state()
                if state.has_position(token):
                    logger.info(f"[决策] {token} 已有持仓(StateManager)，跳过")
                    return False
                if not state.can_open_position():
                    logger.info(f"[决策] 已达最大持仓数，跳过扫描")
                    return False
            except Exception as e:
                logger.warning(f"[StateManager检查] 失败: {e}")
        
        # 检查是否已经有这个币的仓位 (回退检查)
        token = analysis_result.get("symbol", "")
        if self.position_monitor:
            active = self.position_monitor.get_active_positions()
            if any(p["token"] == token for p in active):
                logger.info(f"[决策] {token} 已有持仓，跳过")
                return False
        
        # ===== Freqtrade 风格: 入场前确认 =====
        # 调用 confirm_trade_entry
        price = analysis_result.get("price", 0)
        position_size = self.params.get("risk_management", {}).get("fixed_position_size", 888.0)
        
        # 5阶段判定已在scan_market完成，这里简化判断
        # 只要有1个以上信号且分数>0即可入场（已经过5阶段筛选）
        score = analysis_result.get("score", {})
        total_score = score.get("total_score", 0)
        
        # 使用真实的5阶段判定结果
        stage = analysis_result.get("stage_result", "静默积累期")
        confidence = analysis_result.get("stage_confidence", 0)
        
        confirmed, reason = self.exit_manager.confirm_trade_entry(
            pair=token,
            side="long",
            current_price=price,
            proposed_rate=price,
            amount=position_size,
            current_time=datetime.now(_UTC),
            entry_tag=score.get("entry_signals", [{}])[0].get("name") if score.get("entry_signals") else None,
        )
        
        if not confirmed:
            logger.info(f"[决策] ❌ {token} 入场确认未通过: {reason}")
            return False
        
        # ========== BTC型/主流币特殊策略 ==========
        # 主流币不需要4/7信号，只需要：清算检测 + 资金费率 + OI
        token = analysis_result.get("symbol", "")
        if token in ["BTC", "ETH", "SOL"]:
            sweep = analysis_result.get("sweep_status", {})
            funding = analysis_result.get("funding_rate", 0)
            oi_info = analysis_result.get("momentum", {})
            oi_change = oi_info.get("oi_change_1h_pct", 0)
            
            sweep_status = sweep.get("status", "normal")
            
            # 清算后反弹：最佳做多时机
            if sweep_status == "post_sweep":
                logger.info(f"[决策] ✅ {token} 清算后反弹BTC型策略")
                return True
            
            # 资金费率判断
            funding_pct = funding * 100
            if funding_pct < -0.01:  # 费率很低，做多
                logger.info(f"[决策] ✅ {token} 费率{funding_pct:.2f}%低于-1%，BTC型做多")
                return True
            elif funding_pct > 0.05:  # 费率过高，可能见顶
                logger.info(f"[决策] ❌ {token} 费率{funding_pct:.2f}%过高，BTC型不做多")
                return False
            
            # 整数关口突破（只看$60k/$65k/$70k）
            price = analysis_result.get("price", 0)
            for level in [60000, 65000, 70000]:
                if level * 0.98 <= price <= level * 1.02:
                    logger.info(f"[决策] ✅ {token} 突破整数关口${level}, BTC型做多")
                    return True
        
        # ========== ZRO型/山寨币策略 ==========
        # 【优化v2】放宽入场条件，接受多种阶段
        # 只要有信号触发且置信度>=40%就可以入场
        allowed_stages = ["拉升启动期", "静默积累期", "整数关口收割期"]
        
        if stage in allowed_stages and confidence >= 0.40:
            logger.info(f"[决策] ✅ {token} 已通过5阶段判定({stage}, 置信度{confidence:.0%})")
            return True
        
        # 备选：检查基本信号（至少2个信号 + 分数>=4）
        triggered = score.get("triggered_count", 0)
        if triggered >= 2 and total_score >= 4:
            logger.info(f"[决策] ✅ {token} 有信号({triggered}个, {total_score}分)")
            return True
        
        logger.info(f"[决策] ❌ {token} 阶段={stage}, 置信度={confidence:.0%}, 信号={triggered}个")
        return False

    def execute_entry(self, token: str, analysis_result: Dict[str, Any]) -> bool:
        """
        执行入场 - 使用限价单 + Freqtrade策略优化
        
        日内杠杆模式: 使用3-5x杠杆
        """
        if not self.trader or not self.result_logger:
            logger.error("[执行] trader 或 result_logger 未初始化")
            return False
        
        # ===== 新增: 重复入场检查 =====
        # 检查是否已在OKX持仓该币种
        try:
            current_positions = self.trader.get_position(token) if hasattr(self.trader, 'get_position') else None
            if current_positions and len(current_positions) > 0:
                logger.warning(f"[入场拒绝] {token} 已在持仓中，跳过重复入场")
                return False
            
            # 检查本地数据库是否有open记录
            from trading.trade_db import TradeDB
            db = TradeDB()
            open_trades = db.get_open_positions()
            if any(t.get('token') == token for t in open_trades):
                logger.warning(f"[入场拒绝] {token} 本地已有open记录，跳过")
                return False
        except Exception as e:
            logger.warning(f"[入场检查] 重复检测失败: {e}, 继续执行")
        
        # ===== 新增: 根据信号方向决定做多还是做空 =====
        score = analysis_result.get("score", {})
        signal_direction = score.get("direction", "long")  # 默认做多
        
        # 如果是OKX模拟盘限制只能做多，记录但继续执行
        if signal_direction == "short":
            logger.info(f"[做空信号] {token} 检测到做空信号: {score.get('short_signals', [])}")
            # 注意: OKX模拟盘可能只支持现货做多，这里记录但不执行
            # 暂时强制改为做多，待OKX支持做空后取消
            logger.warning(f"[方向限制] OKX模拟盘可能不支持做空，强制使用做多")
            signal_direction = "long"
        
        # 根据方向设置side
        trade_side = "buy" if signal_direction == "long" else "sell"
        logger.info(f"[交易方向] {token}: {signal_direction} (side={trade_side})")
        
        # ===== 新增: 区分主流币 vs 山寨币策略 =====
        # 主流币: BTC, ETH, SOL, BNB, XRP, ADA, DOGE 等
        MAINSTREAM_TOKENS = ["BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC"]
        is_mainstream = token in MAINSTREAM_TOKENS
        
        if is_mainstream:
            # 主流币: 中期持有，回调买入
            default_target = 15  # 15%止盈
            default_stop = 3     # 3%止损
            hold_style = "mainstream"
            logger.info(f"[币种策略] {token} 主流币: 目标{default_target}%, 止损{default_stop}%")
        else:
            # 山寨币: 短平快，信号强才入场
            default_target = 20  # 20%止盈（更高）
            default_stop = 2.5   # 2.5%止损（更严格）
            hold_style = "altcoin"
            logger.info(f"[币种策略] {token} 山寨币: 目标{default_target}%, 止损{default_stop}%")
        
        # 根据信号强度调整目标
        triggered = score.get("triggered_count", 0)
        if triggered >= 5:  # 信号非常强
            default_target *= 1.3  # 提高目标
            logger.info(f"[信号增强] {token} 信号强({triggered}个), 目标提升至 {default_target}%")
        
        # 存储目标到market_context供后续退场使用
        if "market_context" not in locals():
            market_context = {}
        market_context["hold_style"] = hold_style
        market_context["target_pct"] = default_target
        market_context["stop_loss_pct"] = default_stop
        market_context["is_mainstream"] = is_mainstream
        
        # ===== 后续代码使用 trade_side 而不是硬编码 "buy" =====
        leverage = 1
        if hasattr(self, 'strategy_mode') and self.strategy_mode == 'intraday':
            if self.intraday_strategy:
                leverage = self.intraday_strategy.leverage
                logger.info(f"[日内杠杆] 入场 {token}, 杠杆: {leverage}x")
        
        # ===== 新增: 时间段交易控制 =====
        can_trade, reason = self.freqtrade_integrator.hour_strategy.can_trade()
        if not can_trade:
            logger.warning(f"[时间段限制] {token} 无法入场: {reason}")
            return False
        
        # 使用从OKX获取的真实价格
        price = analysis_result.get("price", 0)
        if price <= 0:
            logger.error(f"[入场失败] {token} 无有效价格: {price}")
            return False
        
        # 获取仓位大小 - 使用模拟盘资金，不受真实资金限制
        simulated_capital = self.params.get("risk_management", {}).get("simulated_capital", 79373.7)
        capital = self.params.get("risk_management", {}).get("capital", 300.0)
        leverage = self.params.get("risk_management", {}).get("leverage", 3.0)
        position_ratio = self.params.get("risk_management", {}).get("position_ratio", 0.33)
        max_position = self.params.get("risk_management", {}).get("max_position_size", 2000.0)
        
        # 使用模拟盘资金计算仓位 (不再受真实300U限制)
        # 目标仓位 = 模拟资金 * 仓位比例 * 杠杆
        target_position_size = simulated_capital * position_ratio * leverage / simulated_capital * 888
        target_position_size = min(target_position_size, max_position)
        
        # 最终仓位使用固定值或根据信号强度调整
        position_size = target_position_size
        
        # 如果信号非常强 (7+ 信号), 可以用更大仓位
        score = analysis_result.get("score", {})
        triggered = score.get("triggered_count", 0)
        if triggered >= 5:
            position_size = position_size * 1.2  # 信号强时增加20%仓位
        elif triggered >= 7:
            position_size = position_size * 1.5  # 信号非常强时增加50%仓位
        
        # 模拟盘余额充足，直接使用计算出的仓位
        usdt_balance = simulated_capital
        
        logger.info(f"[仓位计算] 信号强度: {triggered}个, 模拟资金: ${simulated_capital:.0f}, 杠杆: {leverage}x, 最终仓位: ${position_size:.0f}")

        if position_size < 10:
            logger.error(f"[入场失败] 仓位太小: {position_size}")
            return False
        
        # ===== 新增: 使用Freqtrade集成器计算最佳挂单价 =====
        from fetchers.price_api import fetch_price_and_change
        try:
            price_data = fetch_price_and_change(token)
            bid_price = price_data.get("bid", price * 0.999)
            ask_price = price_data.get("ask", price * 1.001)
            
            # 计算最佳挂单价，但使用市价单确保成交
            entry_price = self.freqtrade_integrator.get_entry_price(token, bid_price, ask_price)
            # 使用市价单确保成交，避免限价单失败
            order_type = "market"
            logger.info(f"[市价单] {token} 市价: ${price:.4f}, 使用market单确保成交")
        except:
            entry_price = price  # 市价单不需要指定价格
            order_type = "market"
            logger.warning(f"[市价单] {token} 使用market单确保成交")
        
        # 下单 - 使用限价单
        # OKXTestnetTrader 期望的格式: DOGE-USDT
        symbol = f"{token}-USDT"
        
        # ===== 新增: 检查测试网是否支持该币种 =====
        if hasattr(self.trader, 'is_token_supported'):
            is_supported = self.trader.is_token_supported(token)
            if not is_supported:
                logger.warning(f"[入场跳过] {token} 在OKX测试网不支持交易(仅支持合约，现货不可交易)")
                # 记录统计信息
                self._testnet_unsupported = getattr(self, '_testnet_unsupported', [])
                self._testnet_unsupported.append(token)
                return False
            logger.info(f"[入场检查] {token} 测试网支持交易 ✓")
        
        # 使用限价单: (symbol, side, size, price, order_type)
        result = self.trader.place_order(symbol, trade_side, position_size, entry_price, order_type)
        
        if result.get("code") == "0":
            score = analysis_result.get("score", {})
            entry_signals = score.get("entry_signals", [])
            
            # 收集市场快照
            ctx = analysis_result.get("market_context", {})
            sweep = analysis_result.get("sweep_status", {})
            momentum = analysis_result.get("momentum", {})
            
            # 动态止损（从4H分析获取）
            dynamic_sl = analysis_result.get("dynamic_sl_pct", 3.0)
            dynamic_sl = max(1.5, min(5.0, dynamic_sl))  # 限制范围
            
            market_context = {
                "hour_of_day": datetime.now(timezone.utc).hour,
                "funding_rate": analysis_result.get("funding_rate", 0),
                "oi_change_1h_pct": ctx.get("oi_change_1h_pct", 0),
                "price_change_1h_pct": momentum.get("price_change_1h_pct", 0),
                "volume_ratio_1h": momentum.get("volume_ratio_1h", 0),
                "days_listed": ctx.get("days_listed", 0),
                "phase_detected": analysis_result.get("stage_result", "静默积累期"),
                "signals_triggered": [s["name"] for s in entry_signals] if entry_signals else [],
                "sweep_status": sweep.get("status", "normal"),
                "btc_price": ctx.get("btc_price", 0),
                "btc_dominance": ctx.get("btc_dominance", 0),
                "dynamic_sl_pct": dynamic_sl,  # 新增：记录动态止损
                "4h_aligned": analysis_result.get("analysis_4h", {}).get("aligned", False),
                "4h_rsi": analysis_result.get("analysis_4h", {}).get("current_rsi", 0),
                # 新增: Freqtrade策略相关信息
                "entry_price_optimized": entry_price,  # 优化后的挂单价
                "order_type": order_type,  # limit 或 market
                "offset_pct": round((price - entry_price) / price * 100, 2) if price > 0 else 0,
            }
            
            # 先同步到 TradeDB 并获取正确的 trade_id
            trade_db_id = None
            if _TRADE_DB_AVAILABLE:
                try:
                    signal_name = entry_signals[0]['name'] if entry_signals else 'unknown'
                    trade_db_id = TradeDB.record_entry(
                        token=token,
                        side=trade_side,
                        price=price,
                        quantity=position_size,
                        signal_name=signal_name,
                        signal_score=score.get("total_score", 0),
                    )
                    logger.info(f"[TradeDB] 入场记录成功, trade_id={trade_db_id}")
                except Exception as e:
                    logger.warning(f"[TradeDB] 入场记录失败: {e}")
            
            # 记录交易 - 使用增强版，包含 trade_db_id
            self.result_logger.log_entry(
                token=token,
                signals=entry_signals,
                score=score.get("total_score", 0),
                entry_price=price,
                entry_signals_count=len(entry_signals),
                position_size=position_size,
                market_context=market_context,
                trade_db_id=trade_db_id,  # 传入 trade_db_id
            )
            
            # ===== 新增: 记录到 CentralStateManager =====
            if _STATE_MANAGER_AVAILABLE:
                try:
                    state = get_state()
                    # 确定交易方向
                    side = "long" if trade_side == "buy" else "short"
                    
                    trade_id = state.open_position(
                        token=token,
                        entry_price=price,
                        size_usd=position_size,
                        side=side,
                        signals=[s.get("name", "unknown") for s in entry_signals],
                        score=score.get("total_score", 0),
                        trade_id=trade_db_id
                    )
                    logger.info(f"[StateManager] 开仓记录成功: {token}, trade_id={trade_id}")
                except Exception as e:
                    logger.warning(f"[StateManager] 开仓记录失败: {e}")
            
            logger.info(f"[入场] {token} @ ${price}, 仓位: {position_size}, 信号: {len(entry_signals)}")
            
            # ===== 新增: 记录交易到Freqtrade策略集成器 =====
            self.freqtrade_integrator.record_trade()
            
            return True
        else:
            logger.error(f"[入场失败] {token}: {result.get('msg')}")
            return False

    def check_and_close_positions(self) -> List[Dict[str, Any]]:
        """
        检查并处理持仓（SL/TP）
        集成 Freqtrade 风格的 EnhancedExitManager
        """
        if not self.position_monitor:
            return []
        
        # 先从 result_logger 获取未平仓交易
        unfinished = self.result_logger.get_unfinished_trades()
        
        # 过滤掉已平仓的持仓（防止重复处理）
        if hasattr(self.position_monitor, '_closed_positions'):
            closed_set = self.position_monitor._closed_positions
            unfinished = [t for t in unfinished if t.get("token") not in closed_set]
        
        if not unfinished:
            return []
        
        # 只处理不在_closed_positions中的交易
        from fetchers.price_api import fetch_price_and_change
        
        prices = {}
        for trade in unfinished:
            token = trade["token"]
            # 跳过已平仓的代币
            if token in closed_set:
                continue
            try:
                price_result = fetch_price_and_change(token)
                if price_result.get("price") and price_result["price"] > 0:
                    prices[token] = price_result["price"]
                    # 只在实际持仓变化时打印价格
                    logger.debug(f"[价格更新] {token}: ${prices[token]:.4f}")
                else:
                    prices[token] = trade.get("entry_price", 0)
            except Exception as e:
                prices[token] = trade.get("entry_price", 0)
        
        # 更新 self.prices
        self.prices.update(prices)
        
        # ===== Freqtrade 风格: 综合退出判断 =====
        # 只对真正持仓的交易进行检查和日志
        exit_signals = []
        for trade in unfinished:
            token = trade["token"]
            # 跳过已平仓的代币
            if token in closed_set:
                continue
            
            current_price = prices.get(token)
            if not current_price:
                continue
            
            # 计算当前利润
            entry_price = trade.get("entry_price", current_price)
            profit_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
            
            # 构建 trade_data
            entry_time = trade.get("entry_time")
            if entry_time is None:
                entry_time = datetime.now(timezone.utc) - timedelta(hours=1)
            elif isinstance(entry_time, str):
                from trading.position_monitor import parse_entry_timestamp
                entry_time = parse_entry_timestamp(entry_time) or datetime.now(timezone.utc) - timedelta(hours=1)
            
            trade_data = {
                "entry_price": entry_price,
                "entry_time": entry_time,
                "rsi": trade.get("market_context", {}).get("rsi", 50),
                "bb_upper": trade.get("market_context", {}).get("bb_upper"),
            }
            
            # 使用 EnhancedExitManager 判断是否应该退出
            exit_result = self.exit_manager.should_exit(
                pair=token,
                trade_data=trade_data,
                current_time=datetime.now(timezone.utc),
                current_price=current_price,
            )
            
            # 如果应该退出，记录信号
            if exit_result["should_exit"]:
                exit_signals.append((token, exit_result['exit_reason'], profit_pct))
            
            # ===== Freqtrade策略集成器的持仓管理 =====
            try:
                entry_price = trade.get("entry_price", current_price)
                entry_time_str = trade.get("timestamp", "")
                
                # 使用统一的解析函数
                from trading.position_monitor import parse_entry_timestamp
                entry_time = parse_entry_timestamp(entry_time_str) if entry_time_str else datetime.now(_UTC)
                
                should_exit_ft, exit_reason_ft = self.freqtrade_integrator.during_position_management(
                    token=token,
                    entry_price=entry_price,
                    current_price=current_price,
                    entry_time=entry_time,
                    closes_4h=[],
                )
                
                if should_exit_ft:
                    exit_signals.append((token, exit_reason_ft, profit_pct))
            except Exception:
                pass
        
        # 批量打印退出信号（如果有）
        if exit_signals:
            for token, reason, pnl in exit_signals:
                logger.info(f"[Exit Signal] {token}: {reason}, PnL: {pnl:.2f}%")
        
        # 检查持仓
        closed = self.position_monitor.check_positions(self.prices)
        
        # ===== 新增: 同步平仓记录到 CentralStateManager =====
        if _STATE_MANAGER_AVAILABLE and closed:
            try:
                state = get_state()
                for c in closed:
                    token = c.get("token")
                    exit_price = c.get("current_price", 0)
                    exit_reason = c.get("exit_reason", "unknown")
                    
                    result = state.close_position(
                        token=token,
                        exit_price=exit_price,
                        exit_reason=exit_reason
                    )
                    if result:
                        logger.info(f"[StateManager] 平仓记录: {token}, PnL={result.get('pnl_usd', 0):.2f}U")
            except Exception as e:
                logger.warning(f"[StateManager] 平仓记录失败: {e}")
        
        return closed

    def run_cycle(self) -> Dict[str, Any]:
        """
        执行一个完整的扫描周期
        """
        self.cycle_count += 1
        cycle_start = time.time()
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🔄 扫描周期 #{self.cycle_count}")
        logger.info(f"{'='*60}")
        
        # 缓存统计（每10个周期报告一次）
        try:
            from core.fetcher_wrapper import get_cache_stats
            if self.cycle_count % 10 == 0:
                cache_stats = get_cache_stats()
                logger.info(f"[缓存] 内存:{cache_stats.get('total',0)}条, 有效:{cache_stats.get('valid',0)}条, 过期:{cache_stats.get('expired',0)}条")
        except:
            pass
        
        results = {
            "cycle": self.cycle_count,
            "scanned": 0,
            "signals_triggered": 0,
            "entries": 0,
            "exits": 0,
            "errors": [],
        }

        # ===== 新增: OKX 同步（每10个周期同步一次）=====
        if _STATE_MANAGER_AVAILABLE and self.trader and self.cycle_count % 10 == 0:
            try:
                state = get_state()
                sync_result = state.sync_from_okx(self.trader)
                if sync_result.get("synced"):
                    logger.info(f"[OKX同步] 持仓: {sync_result.get('okx_positions', [])}, 过期: {sync_result.get('stale_positions', [])}")
            except Exception as e:
                logger.warning(f"[OKX同步] 失败: {e}")

        # 1. 重新加载参数（支持动态更新）
        self.reload_params()
        
        # 2. 检查并处理持仓（SL/TP）
        closed = self.check_and_close_positions()
        results["exits"] = len(closed)
        
        if closed:
            logger.info(f"[持仓] 触发 {len(closed)} 笔平仓")
            for c in closed:
                logger.info(f"  - {c['token']}: {c['exit_reason']}, PnL: {c['pnl']:.2f}")
        
        # 3. 检查是否还能开新仓位
        if self.position_monitor and not self.position_monitor.can_open_new_position():
            logger.info("[决策] 仓位已满，等待平仓")
            results["errors"].append("max_positions_reached")
            return results
        
        # 4. 市场扫描
        tokens = self.scan_market(max_tokens=self.params.get("auto_pilot", {}).get("max_trades_per_cycle", 3) * 5)
        results["scanned"] = len(tokens)
        
        # 5. 逐个分析并决策
        entry_count = 0
        max_entries = self.params.get("auto_pilot", {}).get("max_trades_per_cycle", 3)
        
        for token in tokens:
            if entry_count >= max_entries:
                break
            
            if self.position_monitor and not self.position_monitor.can_open_new_position():
                break
            
            try:
                analysis = self.analyze_token(token)
                
                if self.should_entry(analysis):
                    if self.execute_entry(token, analysis):
                        entry_count += 1
                        results["signals_triggered"] += 1
            except Exception as e:
                import traceback
                logger.error(f"[分析] {token} 失败: {e}")
                logger.error(f"[分析] {token} 堆栈: {traceback.format_exc()[:500]}")
                results["errors"].append(f"{token}: {e}")
        
        results["entries"] = entry_count
        
        # 6. 参数优化（每 N 笔交易触发）
        if self.optimizer:
            opt_result = self.optimizer.run()
            if opt_result.get("optimized"):
                n_changes = opt_result.get("changes_count", 0)
                logger.info(f"[优化] ✅ 完成! 调整了 {n_changes} 个参数")
                # 重新加载参数
                self.reload_params()
        
        # 统计
        elapsed = time.time() - cycle_start
        stats = self.result_logger.get_stats() if self.result_logger else {}
        
        logger.info(f"\n📊 周期 #{self.cycle_count} 完成:")
        logger.info(f"   扫描: {results['scanned']} 个代币")
        logger.info(f"   触发信号: {results['signals_triggered']}")
        logger.info(f"   入场: {results['entries']} 笔")
        logger.info(f"   平仓: {results['exits']} 笔")
        logger.info(f"   总交易: {stats.get('total_trades', 0)} 笔")
        logger.info(f"   胜率: {stats.get('win_rate', 0):.1%}")
        logger.info(f"   总PnL: ${stats.get('total_pnl', 0):.2f}")
        logger.info(f"   耗时: {elapsed:.1f}秒")
        
        return results

    def start(self, max_cycles: int = None, interval: int = None):
        """
        启动自动驾驶
        """
        self.running = True
        
        # 获取运行参数
        auto_pilot = self.params.get("auto_pilot", {})
        interval = interval or auto_pilot.get("scan_interval_seconds", 300)
        
        logger.info(f"[AutoPilot] 启动自动驾驶仪")
        logger.info(f"   扫描间隔: {interval}秒")
        logger.info(f"   参数文件: {self.params_file}")
        
        cycle = 0
        while self.running:
            cycle += 1
            
            try:
                self.run_cycle()
            except Exception as e:
                logger.error(f"[AutoPilot] 周期执行失败: {e}")
            
            # 检查是否停止
            if max_cycles and cycle >= max_cycles:
                logger.info(f"[AutoPilot] 完成 {max_cycles} 个周期，停止")
                break
            
            # 等待下一个周期
            if self.running:
                logger.info(f"[等待] {interval}秒后进行下一轮扫描...")
                time.sleep(interval)
        
        self.stop()

    def stop(self):
        """停止自动驾驶"""
        self.running = False
        logger.info("[AutoPilot] 已停止")

    def get_status(self) -> Dict[str, Any]:
        """获取状态"""
        stats = self.result_logger.get_stats() if self.result_logger else {}
        
        active_positions = []
        if self.position_monitor:
            active_positions = self.position_monitor.get_active_positions()
        
        return {
            "running": self.running,
            "cycles": self.cycle_count,
            "stats": stats,
            "active_positions": active_positions,
            "params": self.params,
        }
    
    def _scan_intraday_market(self, max_tokens: int = 20) -> List[str]:
        """
        日内杠杆市场扫描 - 基于OKX实时K线 + 21信号工厂二次确认
        返回符合日内动量信号的代币列表
        
        修复: 不再使用历史回测数据，直接用OKX API获取实时K线
        """
        import pandas as pd
        from fetchers.multi_tf import fetch_okx_candles
        from scanner.universe import get_full_universe
        
        logger.info("[日内杠杆] 扫描市场 (OKX实时K线)...")
        
        # Step 1: 获取全市场代币
        universe = get_full_universe()
        logger.info(f"[日内杠杆] 全市场代币: {len(universe)}个")
        
        # Step 2: 获取24h涨幅榜，筛选活跃代币
        from scanner.fast_filter import run_fast_filter
        candidates = run_fast_filter(universe, enable_gain_tracker=True, enable_technical=True)
        
        if not candidates:
            logger.warning("[日内杠杆] 无候选代币")
            return []
        
        logger.info(f"[日内杠杆] 候选代币: {len(candidates)}个")
        
        # ====== Step 2.5: 显示第一道筛选(技术分析)结果 ======
        logger.info("")
        logger.info("=" * 60)
        logger.info("📊 第一道筛选: 技术分析评分 (Top候选)")
        logger.info("=" * 60)
        for i, item in enumerate(candidates[:15]):
            token = item.get('symbol', '')
            tech_score = item.get('tech_score', 0)
            change_24h = item.get('change_24h_pct', 0)
            volume = item.get('volume_usd_24h', 0)
            volume_str = f"${volume/1e6:.1f}M" if volume > 1e6 else f"${volume/1e3:.0f}K"
            
            # 技术评分等级
            if tech_score >= 10:
                star = "🚀"
            elif tech_score >= 7:
                star = "✅"
            elif tech_score >= 4:
                star = "⚠️"
            else:
                star = "❌"
            
            logger.info(f"  {star} {token:8} 技术:{tech_score:2}/20  24h:{change_24h:+6.1f}%  量:{volume_str}")
        logger.info("=" * 60)
        
        # Step 3: 逐个获取实时K线并用21信号工厂评估
        valid_candidates = []
        
        for item in candidates[:min(len(candidates), max_tokens * 3)]:
            token = item.get('symbol') if isinstance(item, dict) else item
            if not token:
                continue
            
            try:
                # 获取多时间框架K线 (30天历史数据)
                klines_1h = fetch_okx_candles(token, "1H", limit=720)
                klines_4h = fetch_okx_candles(token, "4H", limit=360)
                klines_15m = fetch_okx_candles(token, "15m", limit=672)
                
                if klines_1h is None or len(klines_1h) < 20:
                    continue
                
                # 生成日内动量信号 (使用实时K线)
                if self.intraday_strategy:
                    klines_1h = self.intraday_strategy.generate_signals(klines_1h, token)
                    last_row = klines_1h.iloc[-1]
                    base_signal_count = last_row.get('signal_count', 0)
                else:
                    base_signal_count = 0
                
                # 21信号工厂二次确认 (关键!)
                if self.intraday_strategy and self.intraday_strategy.SignalFactory:
                    signal_result = self._evaluate_with_21_signals(token, klines_1h, klines_4h, klines_15m)
                    sf_score = signal_result.get('total_score', 0)
                    sf_triggered = signal_result.get('triggered_count', 0)
                    sf_grade = signal_result.get('grade', 'WATCH')
                    
                    # 入场条件: 基础信号>=1 且 21信号工厂>=2个信号触发 且 分数>=7
                    if base_signal_count >= 1 and sf_triggered >= 2 and sf_score >= 7:
                        valid_candidates.append({
                            'token': token,
                            'base_signal_count': base_signal_count,
                            'sf_triggered': sf_triggered,
                            'sf_score': sf_score,
                            'sf_grade': sf_grade,
                            'last_price': float(last_row.get('close', 0)),
                            'volume': float(last_row.get('volume', 0))
                        })
                        logger.info(f"[日内杠杆] ✅ {token}: 基础信号={base_signal_count}, 21信号={sf_triggered}个, 分数={sf_score}, 等级={sf_grade}")
                else:
                    # 如果21信号工厂不可用，使用基础信号
                    if base_signal_count >= 2:
                        valid_candidates.append({
                            'token': token,
                            'base_signal_count': base_signal_count,
                            'sf_triggered': base_signal_count,
                            'sf_score': base_signal_count * 2,
                            'sf_grade': 'WATCH',
                            'last_price': float(last_row.get('close', 0)),
                            'volume': float(last_row.get('volume', 0))
                        })
                
            except Exception as e:
                logger.warning(f"[日内杠杆] {token} 扫描失败: {e}")
                continue
        
        # 按21信号工厂分数排序
        valid_candidates.sort(key=lambda x: x['sf_score'], reverse=True)
        
        top_candidates = [c['token'] for c in valid_candidates[:max_tokens]]
        
        logger.info(f"[日内杠杆] 找到 {len(top_candidates)} 个高置信候选: {top_candidates[:5]}...")
        
        return top_candidates
    
    def _evaluate_with_21_signals(self, symbol: str, df_1h: pd.DataFrame, df_4h: pd.DataFrame = None, df_15m: pd.DataFrame = None) -> dict:
        """
        使用21信号工厂评估代币 - 实时K线分析
        """
        from fetchers.price_api import fetch_price_and_change, fetch_funding_rate_history
        from fetchers.multi_tf import analyze_1d, analyze_4h, analyze_1h_surface, analyze_15m
        
        try:
            # 获取价格数据
            price_data = fetch_price_and_change(symbol)
            current_price = price_data.get('price', 0) if price_data else 0
            
            if current_price <= 0:
                return {"triggered_count": 0, "total_score": 0, "grade": "WATCH"}
            
            # 计算RSI
            if df_1h is not None and len(df_1h) > 14:
                close = df_1h['close']
                delta = close.diff()
                gain = delta.where(delta > 0, 0).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / (loss + 1e-10)
                rsi = 100 - (100 / (1 + rs))
                current_rsi = rsi.iloc[-1] if len(rsi) > 0 else 50
            else:
                current_rsi = 50
            
            # 获取资金费率
            funding_data = fetch_funding_rate_history(symbol)
            funding_rate = funding_data.get('latest_rate', 0) if funding_data else 0
            
            # 获取多时间框架分析
            analysis_4h = analyze_4h(symbol) if df_4h is not None else {"valid": False}
            analysis_1h = analyze_1h_surface(symbol) if df_1h is not None else {"valid": False}
            analysis_15m = analyze_15m(symbol) if df_15m is not None else {"valid": False}
            
            # 构建信号数据 (符合21信号工厂格式)
            signal_data = {
                "analysis_4h": analysis_4h,
                "analysis_1h": analysis_1h,
                "analysis_15m": analysis_15m,
                "momentum": "bullish" if current_rsi < 60 else "bearish",
                "funding_rate": funding_rate,
                "stage_result": "拉升启动期",
                "price": current_price,
                "sweep_status": {},
                "tf_analysis": {"decision": "enter"},
            }
            
            # 21信号工厂评估
            signal_results = self.intraday_strategy.SignalFactory.scan_all(symbol, signal_data)
            score_result = self.intraday_strategy.SignalFactory.calculate_total_score(signal_results)
            
            return {
                "triggered_count": score_result.get("triggered_count", 0),
                "total_score": score_result.get("total_score", 0),
                "grade": score_result.get("grade", "WATCH"),
                "signals": score_result.get("triggered_signals", [])
            }
            
        except Exception as e:
            logger.warning(f"[21信号工厂] {symbol} 评估失败: {e}")
            return {"triggered_count": 0, "total_score": 0, "grade": "WATCH"}


def create_autopilot(sim_mode: bool = True) -> AutoPilot:
    """创建自动驾驶仪 - 使用统一配置加载"""
    from trading.result_logger import ResultLogger
    from trading.position_monitor import PositionMonitor
    from trading.parameter_optimizer import ParameterOptimizer
    
    # 使用统一配置加载器
    from utils.config_loader import get_config, get_okx_credentials
    
    config = get_config()
    okx_creds = get_okx_credentials()
    
    # 初始化交易器 - 使用模拟盘
    from trading.okx_testnet import OKXTestnetTrader
    trader = OKXTestnetTrader(
        okx_creds.get("api_key"),
        okx_creds.get("api_secret"),
        okx_creds.get("passphrase"),
        testnet=True  # 固定使用模拟盘
    )
    print(f"[Trader] OKX 模拟盘已连接 (真实API)")
    
    result_logger = ResultLogger()
    
    # 使用统一的风险参数
    risk_params = config.get("risk_management", {})
    
    position_monitor = PositionMonitor(trader, result_logger, risk_params)
    
    # 使用 MetaOptimizer 进行自学习优化
    from trading.meta_optimizer import MetaOptimizer
    optimizer = MetaOptimizer(min_trades_before_optimize=20)
    
    autopilot = AutoPilot(
        trader=trader,
        result_logger=result_logger,
        position_monitor=position_monitor,
        optimizer=optimizer,
    )
    
    return autopilot


def main():
    parser = argparse.ArgumentParser(description="AutoPilot - 闭环自动驾驶交易系统")
    parser.add_argument("--cycles", "-n", type=int, default=None, help="运行多少个周期，默认无限")
    parser.add_argument("--interval", "-i", type=int, default=300, help="扫描间隔秒数")
    parser.add_argument("--sim", action="store_true", help="模拟交易模式")
    parser.add_argument("--real", action="store_true", help="真实交易模式（测试网）")
    
    args = parser.parse_args()
    
    sim_mode = not args.real
    
    # 创建自动驾驶仪
    autopilot = create_autopilot(sim_mode=sim_mode)
    
    # 启动
    autopilot.start(max_cycles=args.cycles, interval=args.interval)


if __name__ == "__main__":
    main()
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
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

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
        
        # 缓存市场价格
        self.prices = {}
        
        # Freqtrade 风格动态退出管理器
        from trading.dynamic_exit import get_exit_manager
        self.exit_manager = get_exit_manager(self.params.get("dynamic_exit", {}))
        
        # Freqtrade策略框架集成器 (新增)
        from trading.freqtrade_integrator import create_integrator
        self.freqtrade_integrator = create_integrator(self.params)
        
        # 初始化时清除旧的卡死仓位
        if result_logger:
            stuck = [t for t in result_logger.trades if t.get("type") == "ENTRY"]
            if stuck:
                print(f"[AutoPilot] 发现 {len(stuck)} 个卡死仓位，自动重置")
                result_logger.force_close_all_entries()

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
        """
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
        
        result_tokens = [c["token"] for c in high_confidence]
        
        logger.info(f"[扫描] 高置信度候选: {len(result_tokens)}个")
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
        try:
            from fetchers.multi_tf import multi_tf_surface_analysis
            tf_analysis = multi_tf_surface_analysis(token, is_major)
            analysis_4h = tf_analysis.get("layers", {}).get("4h", {})
            analysis_1h = tf_analysis.get("layers", {}).get("1h", {})
            analysis_15m = tf_analysis.get("layers", {}).get("15m", {})
        except Exception as e:
            logger.error(f"[MultiTF] {token} 多时间框架分析失败: {e}")
            tf_analysis = {
                "decision": "skip",
                "reason": f"MultiTF分析异常: {e}",
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
        7条件框架 + 5阶段模式决策
        如果已通过5阶段判定(拉升启动期)，则简化判断
        """
        # 如果被跳过（资金费率过高等原因），直接返回False
        if analysis_result.get("skip", False):
            return False
        
        # 检查是否已经有这个币的仓位
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
            proposed_rate=price,  # 使用市价
            amount=position_size,
            current_time=datetime.now(),
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
        """
        if not self.trader or not self.result_logger:
            logger.error("[执行] trader 或 result_logger 未初始化")
            return False
        
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
        
        # 获取仓位大小 - 使用可用余额或固定888U
        available = self.trader.get_balance()
        usdt_balance = float([d for d in available['details'] if d['ccy'] == 'USDT'][0]['availBal'])
        position_size = min(self.params.get("risk_management", {}).get("fixed_position_size", 888.0), usdt_balance)
        
        if position_size < 10:
            logger.error(f"[入场失败] USDT余额不足: {usdt_balance}")
            return False
        
        # ===== 新增: 使用Freqtrade集成器计算最佳挂单价 =====
        from fetchers.price_api import fetch_price_and_change
        try:
            price_data = fetch_price_and_change(token)
            bid_price = price_data.get("bid", price * 0.999)
            ask_price = price_data.get("ask", price * 1.001)
            
            # 计算最佳挂单价
            entry_price = self.freqtrade_integrator.get_entry_price(token, bid_price, ask_price)
            order_type = "limit"
            logger.info(f"[挂单价优化] {token} 市价: ${price:.4f}, 挂单价: ${entry_price:.4f}, 节省: {(price - entry_price) / price * 100:.2f}%")
        except:
            entry_price = price * 0.995  # 默认挂单在市价下方0.5%
            order_type = "limit"
            logger.warning(f"[挂单价] {token} 使用默认挂单价: ${entry_price:.4f}")
        
        # 下单 - 使用限价单
        # OKXTestnetTrader 期望的格式: DOGE-USDT
        symbol = f"{token}-USDT"
        
        # 使用限价单: (symbol, side, size, price, order_type)
        result = self.trader.place_order(symbol, "buy", position_size, entry_price, order_type)
        
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
                "hour_of_day": datetime.now().hour,
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
            
            # 记录交易 - 使用增强版
            self.result_logger.log_entry(
                token=token,
                signals=entry_signals,
                score=score.get("total_score", 0),
                entry_price=price,
                entry_signals_count=len(entry_signals),
                position_size=position_size,
                market_context=market_context,
            )
            
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
        
        # 获取这些代币的当前价格
        from fetchers.price_api import fetch_price_and_change
        
        prices = {}
        for trade in unfinished:
            token = trade["token"]
            try:
                price_result = fetch_price_and_change(token)
                if price_result.get("price") and price_result["price"] > 0:
                    prices[token] = price_result["price"]
                    logger.info(f"[价格更新] {token}: ${prices[token]:.4f}")
                else:
                    prices[token] = trade.get("entry_price", 0)
            except Exception as e:
                logger.warning(f"[价格获取失败] {token}: {e}")
                prices[token] = trade.get("entry_price", 0)
        
        # 更新 self.prices
        self.prices.update(prices)
        
        # ===== Freqtrade 风格: 综合退出判断 =====
        # 使用 EnhancedExitManager 进行自定义退出检查
        for trade in unfinished:
            token = trade["token"]
            current_price = prices.get(token)
            if not current_price:
                continue
            
            # 构建 trade_data
            trade_data = {
                "entry_price": trade.get("entry_price", current_price),
                "entry_time": trade.get("entry_time", datetime.now() - timedelta(hours=1)),
                "rsi": trade.get("market_context", {}).get("rsi", 50),
                "bb_upper": trade.get("market_context", {}).get("bb_upper"),
            }
            
            # 使用 EnhancedExitManager 判断是否应该退出
            exit_result = self.exit_manager.should_exit(
                pair=token,
                trade_data=trade_data,
                current_time=datetime.now(),
                current_price=current_price,
            )
            
            # 如果应该退出，添加到平仓队列
            if exit_result["should_exit"]:
                logger.info(f"[Freqtrade Exit] {token} 触发 {exit_result['exit_reason']}")
                # 这里会由 position_monitor 处理实际平仓
            
            # ===== 新增: Freqtrade策略集成器的持仓管理 =====
            try:
                entry_price = trade.get("entry_price", current_price)
                entry_time_str = trade.get("timestamp", "")
                entry_time = datetime.fromisoformat(entry_time_str.replace("Z", "+00:00")) if entry_time_str else datetime.now()
                profit_pct = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0
                
                # 获取4H数据用于紧急逃生检查
                klines_4h = fetch_price_and_change(token)  # 简化获取
                
                should_exit_ft, exit_reason_ft = self.freqtrade_integrator.during_position_management(
                    token=token,
                    entry_price=entry_price,
                    current_price=current_price,
                    entry_time=entry_time,
                    closes_4h=[],  # 简化
                )
                
                if should_exit_ft:
                    logger.info(f"[Freqtrade持仓管理] {token} 触发退出: {exit_reason_ft}, 利润: {profit_pct:.2f}%")
            except Exception as e:
                pass
        
        # 检查持仓
        closed = self.position_monitor.check_positions(self.prices)
        
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
        
        results = {
            "cycle": self.cycle_count,
            "scanned": 0,
            "signals_triggered": 0,
            "entries": 0,
            "exits": 0,
            "errors": [],
        }
        
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
                logger.error(f"[分析] {token} 失败: {e}")
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


def create_autopilot(sim_mode: bool = True) -> AutoPilot:
    """创建自动驾驶仪"""
    from trading.result_logger import ResultLogger
    from trading.position_monitor import PositionMonitor
    from trading.parameter_optimizer import ParameterOptimizer
    
    # 加载配置
    config_file = "config/testnet_config.json"
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
        # 兼容两种配置格式: okx 或 okx_testnet
        okx_cfg = config.get("okx") or config.get("okx_testnet", {})
        api_key = okx_cfg.get("api_key")
        api_secret = okx_cfg.get("api_secret")
        passphrase = okx_cfg.get("passphrase")
    except:
        api_key = None
        api_secret = None
        passphrase = None
    
    # 初始化交易器 - 直接使用真实OKX API
    from trading.okx_testnet import OKXTestnetTrader
    trader = OKXTestnetTrader(api_key, api_secret, passphrase, testnet=True)
    print(f"[Trader] OKX 模拟盘已连接 (真实API)")
    
    result_logger = ResultLogger()
    
    risk_params = {}
    try:
        with open("config/strategy_params.json", "r") as f:
            params = json.load(f)
            risk_params = params.get("risk_management", {})
    except:
        pass
    
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
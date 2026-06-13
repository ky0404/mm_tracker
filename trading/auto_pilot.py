"""
自动驾驶仪 - AutoPilot
功能：闭环交易系统主控制器
实现：市场扫描 → 信号评级 → 交易决策 → OKX执行 → 持仓监控 → 结果记录 → 参数优化
"""
import json
import time
import logging
import argparse
from datetime import datetime
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
        市场扫描 - 获取候选代币列表
        """
        from fetchers.price_api import fetch_support_coins
        
        logger.info("[扫描] 获取可交易代币列表...")
        result = fetch_support_coins("SWAP")
        
        coins = result.get("coins", [])
        if not coins:
            # 回退到默认列表
            coins = ["BTC", "ETH", "SOL", "PEPE", "WIF", "BONK", "LAB", "NEIRO", "AERO", "GIGA"]
        
        selected = coins[:max_tokens]
        logger.info(f"[扫描] 选取 {len(selected)} 个代币进行信号检测")
        return selected

    def analyze_token(self, token: str) -> Dict[str, Any]:
        """
        分析单个代币 - 信号评级 + 决策
        """
        from main import analyze_one
        from fetchers.price_api import fetch_price_and_change, fetch_daily_ohlcv
        
        # 获取价格（优先 CoinGecko，备选 OKX）
        price = 0
        price_result = fetch_price_and_change(token)
        price = price_result.get("price", 0)
        
        # 如果 CoinGecko 失败，尝试从 K 线获取
        if price <= 0:
            try:
                df = fetch_daily_ohlcv(token)
                if not df.empty:
                    price = float(df["close"].iloc[-1])
                    logger.info(f"[价格备选] {token} OKX价格: ${price}")
            except:
                pass
        
        # 执行信号分析
        result = analyze_one(token.upper(), verbose=False)
        
        # 添加当前价格
        result["price"] = price
        
        return result

    def should_entry(self, analysis_result: Dict[str, Any]) -> bool:
        """
        交易决策 - 是否入场
        """
        score = analysis_result.get("score", {})
        entry_signals = score.get("entry_signals", [])
        
        # 获取参数
        thresholds = self.params.get("entry_thresholds", {})
        min_signals = thresholds.get("min_signals", 4)
        min_score = thresholds.get("min_score", 3.0)
        min_confidence = thresholds.get("min_confidence", 2)
        
        # 检查是否满足入场条件
        signal_count = len(entry_signals)
        total_score = score.get("total_score", 0)
        confidence = score.get("triggered_count", 0)
        
        if signal_count < min_signals:
            return False
        
        if total_score < min_score:
            return False
        
        if confidence < min_confidence:
            return False
        
        # 检查是否已经有这个币的仓位
        token = analysis_result.get("symbol", "")
        if self.position_monitor:
            active = self.position_monitor.get_active_positions()
            if any(p["token"] == token for p in active):
                logger.info(f"[决策] {token} 已有持仓，跳过")
                return False
        
        return True

    def execute_entry(self, token: str, analysis_result: Dict[str, Any]) -> bool:
        """
        执行入场 - 使用市价单
        """
        if not self.trader or not self.result_logger:
            logger.error("[执行] trader 或 result_logger 未初始化")
            return False
        
        price = analysis_result.get("price", 0)
        
        # 获取仓位大小
        position_size = self.params.get("risk_management", {}).get("default_position_size", 10.0)
        
        # 下单 - 使用市价单确保成交
        # 转换 symbol: DOGE -> DOGE-USDT (现货)
        symbol = f"{token}-USDT"
        
        # 使用市价单
        result = self.trader.place_order(symbol, "buy", position_size, None, "market")
        
        if result.get("code") == "0":
            score = analysis_result.get("score", {})
            entry_signals = score.get("entry_signals", [])
            
            # 记录交易
            self.result_logger.log_entry(
                token=token,
                signals=entry_signals,
                score=score.get("total_score", 0),
                entry_price=price,
                entry_signals_count=len(entry_signals),
                position_size=position_size,
            )
            
            logger.info(f"[入场] {token} @ ${price}, 仓位: {position_size}, 信号: {len(entry_signals)}")
            return True
        else:
            logger.error(f"[入场失败] {token}: {result.get('msg')}")
            return False

    def check_and_close_positions(self) -> List[Dict[str, Any]]:
        """
        检查并处理持仓（SL/TP）
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
        
        # 6. 参数优化（每 N 笔交易）
        if self.optimizer:
            opt_result = self.optimizer.optimize()
            if opt_result.get("optimized"):
                logger.info(f"[优化] 参数已更新: {opt_result.get('adjustments', [])}")
        
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
        okx_cfg = config.get("okx_testnet", {})
        api_key = okx_cfg.get("api_key")
        api_secret = okx_cfg.get("api_secret")
        passphrase = okx_cfg.get("passphrase")
    except:
        api_key = None
        api_secret = None
        passphrase = None
    
    # 初始化交易器
    if sim_mode or not api_key:
        from trading.mock_trader import create_trader
        trader = create_trader(sim_mode=True)
    else:
        from trading.okx_testnet import OKXTestnetTrader
        trader = OKXTestnetTrader(api_key, api_secret, passphrase, testnet=True)
        print(f"[Trader] OKX 模拟盘已连接")
    
    result_logger = ResultLogger()
    position_monitor = PositionMonitor(trader, result_logger)
    optimizer = ParameterOptimizer(result_logger)
    
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
# DEPRECATED: 使用 trading/auto_pilot.py 替代，勿直接调用
"""
实时交易执行器
整合 OKX API + 优化策略 + 交易执行
"""

import json
import time
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from threading import Thread, Event

from trading.okx_optimizer import OKXOptimizer
from trading.optimized_strategy import OptimizedStrategy, SignalEnhancer


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class LiveTrader:
    """
    实时交易执行器
    - 定时获取信号
    - 评估入场/离场
    - 自动执行交易
    - 风险管理
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        passphrase: str,
        symbols: List[str],
        testnet: bool = True,
        check_interval: int = 300,  # 5分钟检查一次
    ):
        self.okx = OKXOptimizer(api_key, api_secret, passphrase, testnet)
        self.strategy = OptimizedStrategy()
        self.enhancer = SignalEnhancer()

        self.symbols = symbols
        self.check_interval = check_interval
        self.running = False
        self.stop_event = Event()

        # 交易统计
        self.stats = {
            "start_time": datetime.now().isoformat(),
            "total_checks": 0,
            "entry_signals": 0,
            "exit_signals": 0,
            "orders_placed": 0,
        }

    def get_signals_for_symbol(self, symbol: str) -> Dict:
        """获取并增强某个币的信号"""
        # 获取原始信号
        raw_signals = self.okx.get_all_signals(symbol)

        if "error" in raw_signals.get("signal_12_long_short_ratio", {}):
            logger.warning(f"[{symbol}] 获取信号失败，跳过")
            return None

        # 增强信号
        signals = {}

        # S12: 多空比
        ls_ratio = raw_signals.get("signal_12_long_short_ratio", {})
        signals["signal_12_long_short_ratio"] = self.enhancer.enhance_long_short_ratio(ls_ratio)

        # S13: 主动成交量
        taker_vol = raw_signals.get("signal_13_taker_volume", {})
        candles = raw_signals.get("_candles", [])
        signals["signal_13_taker_volume"] = self.enhancer.enhance_taker_volume(taker_vol, candles)

        # S2: 资金费率
        funding = raw_signals.get("signal_2_funding_turn_positive", {})
        funding_hist = raw_signals.get("signal_2_funding_history", {})
        signals["signal_2_funding_turn_positive"] = self.enhancer.enhance_funding_rate(funding, funding_hist)

        # S3: OI数据
        signals["signal_3_oi_accumulation"] = raw_signals.get("signal_3_oi_accumulation", {})

        # 补充数据
        signals["_ticker"] = raw_signals.get("_ticker", {})

        return signals

    def check_and_trade(self) -> Dict:
        """
        检查所有币种，执行交易
        """
        results = {
            "timestamp": datetime.now().isoformat(),
            "symbols_checked": 0,
            "entries": [],
            "exits": [],
            "errors": [],
        }

        for symbol in self.symbols:
            try:
                # 1. 获取信号
                signals = self.get_signals_for_symbol(symbol)
                if not signals:
                    continue

                results["symbols_checked"] += 1

                # 2. 检查是否已有持仓
                has_position = symbol in self.strategy.positions

                if has_position:
                    # 3. 检查是否需要离场
                    current_price = signals["_ticker"].get("last", 0)
                    should_exit, reason, action = self.strategy.should_exit(
                        symbol, current_price, signals
                    )

                    if should_exit:
                        exit_result = self.execute_exit(symbol, current_price, reason)
                        results["exits"].append({
                            "symbol": symbol,
                            "reason": reason,
                            "result": exit_result,
                        })
                        self.stats["exit_signals"] += 1
                else:
                    # 4. 检查是否需要入场
                    should_enter, position_size, reason = self.strategy.should_enter(signals)

                    if should_enter:
                        entry_price = signals["_ticker"].get("last", 0)
                        if entry_price > 0:
                            entry_result = self.execute_entry(
                                symbol, entry_price, position_size, signals
                            )
                            results["entries"].append({
                                "symbol": symbol,
                                "entry_price": entry_price,
                                "position_size": position_size,
                                "reason": reason,
                                "result": entry_result,
                            })
                            self.stats["entry_signals"] += 1

            except Exception as e:
                logger.error(f"[{symbol}] 检查出错: {e}")
                results["errors"].append({"symbol": symbol, "error": str(e)})

        self.stats["total_checks"] += 1
        return results

    def execute_entry(
        self,
        symbol: str,
        entry_price: float,
        position_size: float,
        signals: Dict,
    ) -> Dict:
        """
        执行入场
        """
        try:
            # 计算仓位数量 (假设账户 10000 USDT)
            account_balance = 10000  # 可从 API 获取
            usd_amount = account_balance * position_size
            size = usd_amount / entry_price

            # 下单
            result = self.okx.place_order(symbol, "buy", size, entry_price, "market")

            if result.get("code") == "0":
                # 开仓成功，记录
                self.strategy.open_position(symbol, entry_price, position_size, signals)
                self.stats["orders_placed"] += 1

                logger.info(f"[{symbol}] 入场成功: {entry_price}, 仓位: {position_size*100}%")

                return {"status": "success", "order": result, "price": entry_price}
            else:
                logger.error(f"[{symbol}] 入场失败: {result}")
                return {"status": "failed", "error": result}

        except Exception as e:
            logger.error(f"[{symbol}] 入场异常: {e}")
            return {"status": "error", "error": str(e)}

    def execute_exit(
        self,
        symbol: str,
        exit_price: float,
        reason: str,
    ) -> Dict:
        """
        执行离场
        """
        try:
            # 获取持仓数量
            pos = self.strategy.positions.get(symbol)
            if not pos:
                return {"status": "no_position"}

            # 下单卖出
            result = self.okx.place_order(symbol, "sell", pos.size, exit_price, "market")

            if result.get("code") == "0":
                # 平仓成功，记录
                close_result = self.strategy.close_position(symbol, exit_price, reason)
                self.stats["orders_placed"] += 1

                logger.info(f"[{symbol}] 离场成功: {exit_price}, 原因: {reason}, PnL: {close_result.get('pnl_pct', 0)*100:.2f}%")

                return {"status": "success", "pnl": close_result.get("pnl_pct", 0)}
            else:
                logger.error(f"[{symbol}] 离场失败: {result}")
                return {"status": "failed", "error": result}

        except Exception as e:
            logger.error(f"[{symbol}] 离场异常: {e}")
            return {"status": "error", "error": str(e)}

    def start(self):
        """启动交易"""
        self.running = True
        logger.info(f"开始实时交易，监控: {self.symbols}")

        while self.running and not self.stop_event.is_set():
            try:
                results = self.check_and_trade()
                logger.info(f"检查完成: {results['symbols_checked']} 个币, 入场: {len(results['entries'])}, 离场: {len(results['exits'])}")

                # 保存结果
                self.save_results(results)

            except Exception as e:
                logger.error(f"交易循环异常: {e}")

            # 等待下次检查
            self.stop_event.wait(self.check_interval)

        logger.info("交易已停止")

    def stop(self):
        """停止交易"""
        self.running = False
        self.stop_event.set()

    def save_results(self, results: Dict):
        """保存交易结果"""
        import os
        os.makedirs("logs", exist_ok=True)

        filename = f"logs/trade_{datetime.now().strftime('%Y%m%d')}.json"
        with open(filename, "a") as f:
            f.write(json.dumps(results, ensure_ascii=False) + "\n")

    def get_status(self) -> Dict:
        """获取状态"""
        return {
            "running": self.running,
            "symbols": self.symbols,
            "positions": self.strategy.get_positions_summary(),
            "stats": self.stats,
            "strategy_stats": self.strategy.get_stats(),
        }


def run_live_trading(
    api_key: str,
    api_secret: str,
    passphrase: str,
    symbols: List[str],
    testnet: bool = True,
    duration_minutes: int = 60,
):
    """
    运行实时交易 (用于测试)
    """
    trader = LiveTrader(
        api_key=api_key,
        api_secret=api_secret,
        passphrase=passphrase,
        symbols=symbols,
        testnet=testnet,
        check_interval=60,  # 1分钟检查一次
    )

    # 运行指定时间
    import threading

    def run():
        trader.start()

    thread = threading.Thread(target=run)
    thread.start()

    # 等待指定时间
    time.sleep(duration_minutes * 60)

    # 停止
    trader.stop()
    thread.join()

    # 输出结果
    status = trader.get_status()
    print(json.dumps(status, indent=2, ensure_ascii=False))

    return status


if __name__ == "__main__":
    import sys

    # 测试模式
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # 测试信号获取
        okx = OKXOptimizer(testnet=True)
        symbols = ["BTC", "ETH", "SOL"]

        for symbol in symbols:
            print(f"\n=== {symbol} ===")
            signals = okx.get_all_signals(symbol)
            print(json.dumps(signals, indent=2, ensure_ascii=False))
            time.sleep(1)
    else:
        print("Usage: python live_trader.py --test")
        print("Or: run_live_trading(api_key, api_secret, passphrase, symbols)")
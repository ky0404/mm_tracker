#!/usr/bin/env python3
"""
MMTracker BotCore - 单一入口
替换多个入口 (run.py/main.py/robot.py/autopilot.py)，统一管理
"""
import os
import sys
import argparse
import logging
from datetime import datetime
from typing import Optional, Dict, Any

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.database import init_db, get_db
from core.scheduler import get_scheduler, start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 初始化交易数据库 (在 logger 定义之后)
try:
    from trading.trade_db import init_db as init_trade_db, TradeDB
    init_trade_db()
    
    # 清理超时的异常持仓，防止旧数据干扰
    cleanup_result = TradeDB.cleanup_stale_positions(max_hold_hours=24)
    if cleanup_result['cleaned'] > 0:
        logger.info(f"   🧹 清理异常持仓: {cleanup_result['cleaned']}个")
    
    logger.info("   📊 TradeDB: 已初始化")
except Exception as e:
    logger.warning(f"   ⚠️ TradeDB: 初始化失败 ({e})")


class BotCore:
    """
    MMTracker 核心控制器
    单一入口，统一管理所有组件
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.db = None
        self.scheduler = None
        self.trader = None
        self.autopilot = None
    
    def initialize(self):
        """初始化所有组件"""
        logger.info("🚀 初始化 BotCore...")
        
        # 1. 初始化数据库
        db_path = self.config.get('db_path', 'data/mmtracker.db')
        self.db = init_db(db_path)
        logger.info(f"   📦 数据库: {db_path}")
        
        # 2. 初始化调度器
        scan_interval = self.config.get('scan_interval', 300)
        monitor_interval = self.config.get('monitor_interval', 30)
        
        self.scheduler = get_scheduler({
            'scan_interval': scan_interval,
            'monitor_interval': monitor_interval,
            'optimize_threshold': 20
        }, force_new=True)
        
        # 3. 初始化交易器
        self._init_trader()
        
        # 4. 初始化自动驾驶仪
        self._init_autopilot()
        
        logger.info("✅ BotCore 初始化完成")
    
    def _init_trader(self):
        """初始化交易器"""
        try:
            from trading.okx_testnet import OKXTestnetTrader
            
            api_key = self.config.get('api_key', os.getenv('OKX_API_KEY', ''))
            api_secret = self.config.get('api_secret', os.getenv('OKX_API_SECRET', ''))
            passphrase = self.config.get('passphrase', os.getenv('OKX_PASSPHRASE', ''))
            
            testnet = not self.config.get('use_real', False)
            self.trader = OKXTestnetTrader(api_key, api_secret, passphrase, testnet=testnet)
            logger.info(f"   📡 交易器: {'测试网' if testnet else '真实账户'}")
        except Exception as e:
            logger.warning(f"   ⚠️ 交易器初始化失败: {e}")
    
    def _init_autopilot(self):
        """初始化自动驾驶仪"""
        try:
            from trading.auto_pilot import create_autopilot
            
            sim_mode = not self.config.get('use_real', True)
            
            # 检查是否是日内杠杆模式
            strategy_mode = self.config.get('strategy_mode', 'default')
            
            if strategy_mode == 'intraday':
                # 集成日内杠杆策略
                from scripts.intraday_leverage import IntradayLeverageStrategy
                
                leverage = self.config.get('leverage', 3.0)
                target_return = self.config.get('target_return', 0.12)  # 12%最佳
                stop_loss = self.config.get('stop_loss', 0.03)
                
                self.autopilot = create_autopilot(sim_mode=sim_mode)
                
                # 注入日内杠杆参数
                self.autopilot.strategy_mode = 'intraday'
                self.autopilot.intraday_strategy = IntradayLeverageStrategy(
                    leverage=leverage,
                    target_return=target_return,
                    stop_loss=stop_loss,
                    max_hold_hours=24
                )
                logger.info(f"   🤖 自动驾驶仪: 日内杠杆模式 ({leverage}x, 目标{target_return*100:.0f}%, 止损{stop_loss*100:.0f}%)")
            else:
                # 默认21信号模式
                self.autopilot = create_autopilot(sim_mode=sim_mode)
                logger.info(f"   🤖 自动驾驶仪: {'模拟' if sim_mode else '真实'} (默认21信号)")
            
            # 设置调度器回调
            if self.scheduler:
                self.scheduler.set_scan_callback(self.autopilot.run_cycle)
                self.scheduler.set_monitor_callback(self.autopilot.check_and_close_positions)
                self.scheduler.set_optimize_callback(self._run_optimization)
                
        except Exception as e:
            logger.warning(f"   ⚠️ 自动驾驶仪初始化失败: {e}")
    
    def _run_optimization(self):
        """运行优化"""
        try:
            from trading.parameter_optimizer import ParameterOptimizer
            from trading.result_logger import ResultLogger
            
            logger.info("🎯 执行参数优化...")
            logger_obj = ResultLogger()
            optimizer = ParameterOptimizer(logger_obj, "config/strategy_params.json")
            result = optimizer.optimize(force=True)
            
            if result.get('optimized'):
                # 记录优化
                self.db.add_optimization(
                    param_name='signal_weights',
                    old_value=0,
                    new_value=1,
                    reason='周期性优化',
                    pnl_before=result.get('pnl_before', 0),
                    pnl_after=result.get('pnl_after', 0)
                )
                logger.info("✅ 优化完成")
                return True
            
            return False
        except Exception as e:
            logger.error(f"❌ 优化失败: {e}")
            return False
    
    def start(self):
        """启动机器人"""
        if not self.db:
            self.initialize()
        
        logger.info("=" * 60)
        logger.info("🤖 MMTracker 量化交易机器人")
        logger.info("=" * 60)
        
        # 启动调度器
        if self.scheduler:
            self.scheduler.start()
        
        # 保存启动状态
        self.db.set_state('bot_status', 'running')
        self.db.set_state('bot_started_at', datetime.now().isoformat())
        
        logger.info("=" * 60)
        logger.info("✅ 机器人已启动")
        logger.info("   命令: python run.py bot stop  # 停止")
        logger.info("   命令: python run.py bot status  # 状态")
        logger.info("=" * 60)
    
    def stop(self):
        """停止机器人"""
        logger.info("🛑 停止机器人...")
        
        if self.scheduler:
            self.scheduler.stop()
        
        self.db.set_state('bot_status', 'stopped')
        self.db.set_state('bot_stopped_at', datetime.now().isoformat())
        
        logger.info("✅ 机器人已停止")
    
    def status(self) -> Dict[str, Any]:
        """获取状态"""
        bot_status = self.db.get_state('bot_status', 'stopped')
        scheduler_status = self.scheduler.get_status() if self.scheduler else {'running': False}
        
        # 优先使用 TradeDB 统计数据
        try:
            from trading.trade_db import TradeDB
            trade_stats = TradeDB.get_total_pnl(30)
            trade_stats['total_trades'] = trade_stats.get('total_trades', 0)
            trade_stats['win_rate'] = trade_stats.get('win_rate', 0) / 100
        except:
            trade_stats = self.db.get_trade_stats() if self.db else {}
        
        # 获取未平仓
        try:
            from trading.trade_db import TradeDB
            open_positions = TradeDB.get_open_positions()
        except:
            open_positions = []
        
        return {
            'bot_status': bot_status,
            'scheduler': scheduler_status,
            'trades': trade_stats,
            'open_positions': open_positions,
            'db_path': self.config.get('db_path', 'data/mmtracker.db')
        }


# 全局实例
_bot_instance: Optional[BotCore] = None


def get_bot(config: Dict[str, Any] = None) -> BotCore:
    """获取 BotCore 实例"""
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = BotCore(config)
    return _bot_instance


def start_bot(config: Dict[str, Any] = None) -> BotCore:
    """启动机器人"""
    bot = get_bot(config)
    bot.initialize()
    bot.start()
    return bot


def stop_bot():
    """停止机器人"""
    global _bot_instance
    if _bot_instance:
        _bot_instance.stop()
        _bot_instance = None


def main():
    parser = argparse.ArgumentParser(description="MMTracker BotCore")
    subparsers = parser.add_subparsers(dest='command', help='命令')
    
    # bot 子命令
    bot_parser = subparsers.add_parser('bot', help='机器人管理')
    bot_sub = bot_parser.add_subparsers(dest='bot_command', help='子命令')
    
    # bot start
    start_parser = bot_sub.add_parser('start', help='启动机器人')
    start_parser.add_argument('--interval', '-i', type=int, default=300, help='扫描间隔(秒)')
    start_parser.add_argument('--monitor', '-m', type=int, default=30, help='监控间隔(秒)')
    start_parser.add_argument('--real', action='store_true', help='使用真实账户')
    start_parser.add_argument('--strategy', '-s', type=str, default='intraday', help='策略: intraday/scan')
    start_parser.add_argument('--leverage', '-l', type=float, default=3.0, help='杠杆倍数')
    start_parser.add_argument('--target', '-t', type=float, default=15.0, help='目标收益%')
    start_parser.add_argument('--stop', type=float, default=3.0, help='止损%')
    start_parser.add_argument('--coins', type=str, default='AVAX,ETH,DOGE,XRP', help='交易币种(逗号分隔)')
    
    # bot stop
    bot_sub.add_parser('stop', help='停止机器人')
    
    # bot status
    bot_sub.add_parser('status', help='查看状态')
    
    args = parser.parse_args()
    
    if args.command == 'bot':
        if args.bot_command == 'start':
            config = {
                'scan_interval': args.interval,
                'monitor_interval': args.monitor,
                'use_real': args.real
            }
            bot = start_bot(config)
            
            # 保持运行
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n🛑 收到停止信号...")
                stop_bot()
                
        elif args.bot_command == 'stop':
            stop_bot()
            
        elif args.bot_command == 'status':
            bot = get_bot()
            if not bot.db:
                bot.initialize()
            status = bot.status()
            
            print(f"\n{'='*50}")
            print("🤖 MMTracker 状态")
            print(f"{'='*50}")
            print(f"  机器人状态: {status['bot_status']}")
            print(f"  调度器运行: {status['scheduler']['running']}")
            
            trades = status['trades']
            open_pos = status.get('open_positions', [])
            
            print(f"  {'='*50}")
            print(f"  【交易统计 (30天)】")
            print(f"    总交易: {trades.get('total_trades', 0)} 笔")
            print(f"    盈利: {trades.get('wins', 0)} | 亏损: {trades.get('losses', 0)}")
            print(f"    胜率: {trades.get('win_rate', 0):.1f}%")
            print(f"    总盈亏: ${trades.get('total_pnl', 0):+.2f}")
            print(f"  {'='*50}")
            print(f"  【当前持仓】: {len(open_pos)} 笔")
            for p in open_pos[:5]:
                print(f"    - {p['token']}: ${p['entry_price']:.4f} x {p['quantity']}")
            print(f"{'='*50}")
        else:
            bot_parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
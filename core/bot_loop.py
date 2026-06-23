import time
import logging
from core.state_manager import get_state
from trading.auto_pilot import create_autopilot
from trading.position_monitor import PositionMonitor

logger = logging.getLogger(__name__)


def run_bot(scan_interval: int = 300, monitor_interval: int = 30, use_real: bool = False):
    """
    统一主循环
    - 每30秒检查一次持仓（止盈/止损）
    - 每300秒做一次市场扫描（寻找新入场机会）
    """
    state = get_state()
    autopilot = create_autopilot(sim_mode=not use_real)
    monitor = PositionMonitor(autopilot.trader, autopilot.result_logger)
    
    last_scan_time = 0
    
    logger.info(f"Bot启动: 扫描间隔{scan_interval}s, 监控间隔{monitor_interval}s")
    print(f"[Bot] 启动: 扫描间隔{scan_interval}s, 监控间隔{monitor_interval}s")
    
    while True:
        loop_start = time.time()
        
        try:
            # ── 1. 同步OKX真实持仓 ──────────────────
            if hasattr(autopilot, 'trader') and autopilot.trader:
                sync_result = state.sync_from_okx(autopilot.trader)
                if not sync_result.get('synced'):
                    logger.warning(f"OKX同步失败: {sync_result.get('reason')}")
            
            # ── 2. 更新持仓价格 ──────────────────────
            from fetchers.momentum import get_okx_price
            for token in list(state.open_positions.keys()):
                price = get_okx_price(token)
                if price:
                    state.update_price(token, price)
            
            # ── 3. 检查止盈止损 ──────────────────────
            monitor.check_positions()
            
            # ── 4. 打印当前状态 ──────────────────────
            stats = state.get_stats()
            open_pos = state.open_positions
            if open_pos:
                for token, pos in open_pos.items():
                    price = state.get_price(token)
                    if price and pos.entry_price > 0:
                        pnl_pct = (price - pos.entry_price) / pos.entry_price * 100
                        tp = pos.take_profit_pct
                        sl = pos.stop_loss_pct
                        logger.info(
                            f"持仓 {token}: 入场{pos.entry_price:.4f} "
                            f"现价{price:.4f} PnL{pnl_pct:+.2f}% "
                            f"(TP{tp}% / SL-{sl}%)"
                        )
            else:
                logger.info("当前无持仓")
            
            # ── 5. 市场扫描（按间隔触发）────────────
            now = time.time()
            if now - last_scan_time >= scan_interval:
                current_count = state.get_position_count()
                max_pos = state.get_config('risk_management.max_open_positions', 3)
                
                if current_count < max_pos:
                    logger.info(f"开始市场扫描（当前{current_count}/{max_pos}仓）")
                    autopilot.run_cycle()
                else:
                    logger.info(f"持仓已满({current_count}/{max_pos})，跳过扫描")
                
                last_scan_time = now
            
            # ── 6. 打印统计 ──────────────────────────
            logger.info(
                f"统计: 总交易{stats['total_trades']}笔 "
                f"胜率{stats['win_rate']*100:.1f}% "
                f"总PnL{stats['total_pnl']:+.2f}U"
            )
        
        except KeyboardInterrupt:
            logger.info("收到停止信号，退出")
            break
        except Exception as e:
            logger.error(f"主循环异常: {e}", exc_info=True)
        
        elapsed = time.time() - loop_start
        sleep_time = max(0, monitor_interval - elapsed)
        time.sleep(sleep_time)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--real', action='store_true')
    parser.add_argument('--scan-interval', type=int, default=300)
    parser.add_argument('--monitor-interval', type=int, default=30)
    args = parser.parse_args()
    
    run_bot(
        scan_interval=args.scan_interval,
        monitor_interval=args.monitor_interval,
        use_real=args.real
    )
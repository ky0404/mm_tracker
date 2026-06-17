"""
关注列表实时监控 — 每5分钟刷新清算状态和入场信号
用法: python3 tools/watchlist_monitor.py --symbols WCT MORPHO TRUST ZRO
"""
import sys
import time
import argparse

sys.path.insert(0, "/mnt/c/Users/朱/Desktop/hexagon_copilot/mm_tracker")

from fetchers.sweep_detector import batch_detect_sweep
from fetchers.momentum import get_hourly_momentum


def monitor_loop(symbols: list, interval: int = 300):
    """持续监控关注列表"""
    cycle = 0
    while True:
        cycle += 1
        now = time.strftime("%H:%M:%S")
        print(f"\n{'='*60}")
        print(f"[{now}] 监控周期 #{cycle}")
        print(f"{'='*60}")

        # 清算状态检测
        sweep_results = batch_detect_sweep(symbols)

        # 动量检测
        print(f"\n[动量状态]")
        for sym in symbols:
            m = get_hourly_momentum(sym)
            price_1h = m.get("price_change_1h_pct", 0)
            vol_ratio = m.get("volume_ratio_1h", 0)
            is_bullish = m.get("is_bullish_momentum", False)
            icon = "✅" if is_bullish else "  "
            print(f"  {icon} {sym:8s} | 1H: {price_1h:+.1f}% | 量比: {vol_ratio:.1f}x")

        # 综合建议
        print(f"\n[综合建议]")
        for r in sweep_results:
            if r["safe_to_enter"]:
                m = get_hourly_momentum(r["symbol"])
                if m.get("is_bullish_momentum") or m.get("price_change_1h_pct", 0) > 2:
                    print(f"  *** 推荐入场: {r['symbol']} | {r['detail']}")
            elif r["status"] == "pre_sweep":
                print(f"  ⚠️  等待清算: {r['symbol']} | {r['detail']}")

        print(f"\n下次刷新: {interval}秒后...")
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=["WCT", "MORPHO", "TRUST"])
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()
    monitor_loop(args.symbols, args.interval)
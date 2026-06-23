#!/usr/bin/env python3
"""
MMTracker 统一入口 - 工厂模式规范化
用法:
  python run.py scan                    # 市场扫描
  python run.py analyze <SYMBOL>        # 单币分析
  python run.py trade                    # 自动交易模式
  python run.py test                     # 测试模式
  python run.py status                   # 查看持仓状态
"""
import sys
import os
import argparse
import json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_config() -> dict:
    """统一配置加载"""
    config_files = [
        "config/testnet_config.json",
        "config/strategy_params.json",
    ]
    config = {}
    for f in config_files:
        if os.path.exists(f):
            with open(f, 'r') as fp:
                data = json.load(fp)
                # 处理嵌套的okx配置 - 优先使用
                if "okx" in data:
                    if data["okx"].get("api_key"):
                        config["api_key"] = data["okx"].get("api_key", "")
                    if data["okx"].get("api_secret"):
                        config["api_secret"] = data["okx"].get("api_secret", "")
                    if data["okx"].get("passphrase"):
                        config["passphrase"] = data["okx"].get("passphrase", "")
                # 处理trading配置
                if "trading" in data:
                    config["default_position_size"] = data["trading"].get("default_position_size", 10.0)
                    config["max_position_size"] = data["trading"].get("max_position_size", 50.0)
                config.update(data)
    return config


class MMTrackerFactory:
    """
    MMTracker 工厂类
    统一管理所有模块
    """
    
    def __init__(self, config: dict = None):
        self.config = config or load_config()
        self.trader = None
        self.autopilot = None
        self._init_components()
    
    def _init_components(self):
        """初始化组件 - 统一使用OKX模拟盘"""
        try:
            from trading.okx_testnet import OKXTestnetTrader
            self.trader = OKXTestnetTrader(
                api_key=self.config.get("api_key", ""),
                api_secret=self.config.get("api_secret", ""),
                passphrase=self.config.get("passphrase", ""),
                testnet=True  # 固定使用模拟盘
            )
            print(f"✅ 交易器初始化: OKX 模拟盘")
        except Exception as e:
            print(f"⚠️ 交易器初始化失败: {e}")
    
    def scan_market(self, top_n: int = 20) -> list:
        """市场扫描 - 统一使用 fast_filter 逻辑"""
        from scanner.universe import get_full_universe
        from scanner.fast_filter import run_fast_filter
        
        print(f"\n{'='*60}")
        print("🔍 MMTracker 市场扫描 (统一数据源)")
        print(f"{'='*60}")
        
        # Step 1: 获取全市场代币
        universe = get_full_universe()
        print(f"📊 全市场代币: {len(universe)}个")
        
        # Step 2: 使用统一的 fast_filter (包含涨幅漏斗 + 技术分析)
        filtered = run_fast_filter(universe, enable_gain_tracker=True, enable_technical=True)
        print(f"📊 技术分析后: {len(filtered)}个候选")
        
        # 按技术评分排序
        filtered.sort(key=lambda x: x.get('tech_score', 0), reverse=True)
        
        # 输出 Top 结果
        print(f"\n📈 Top {min(top_n, len(filtered))} 候选代币:")
        for i, item in enumerate(filtered[:top_n], 1):
            symbol = item.get('symbol', 'N/A')
            change = item.get('change_24h_pct', 0)
            tech_score = item.get('tech_score', 0)
            print(f"  {i:2}. {symbol:8} 涨幅:{change:+6.1f}%  技术分:{tech_score}/20")
        
        return filtered[:top_n]
    
    def analyze_token(self, symbol: str) -> dict:
        """单币深度分析"""
        from fetchers.multi_tf import multi_tf_surface_analysis, fetch_okx_candles
        from small_cap_quant import (
            create_small_cap_quant, 
            calculate_pivot_points, 
            calculate_liquidation_price,
            PivotMode
        )
        
        print(f"\n{'='*60}")
        print(f"🔍 深度分析 {symbol}/USDT")
        print(f"{'='*60}")
        
        # Step 1: 多时间框架分析 (NFI风格)
        is_major = symbol.upper() in ["BTC", "ETH", "SOL", "BNB"]
        mtf_data = multi_tf_surface_analysis(symbol, is_major=is_major)
        
        print(f"\n📊 多时间框架分析 (NFI指标):")
        layers = mtf_data.get("layers", {})
        for tf in ["1d", "4h", "1h", "15m"]:
            if tf in layers:
                data = layers[tf]
                if data.get("valid"):
                    rsi = data.get("current_rsi", 0)
                    cti = data.get("current_cti", 0)
                    ewo = data.get("current_ewo", 0)
                    wr = data.get("current_wr", 0)
                    aligned = "✓" if data.get("aligned") else "✗"
                    print(f"  {tf:4}: RSI={rsi:>5.1f} CTI={cti:>5.1f} EWO={ewo:>6.1f} Wr={wr:>6.1f} {aligned}")
                else:
                    print(f"  {tf:4}: 数据获取失败")
        
        if mtf_data.get("decision") != "enter":
            print(f"❌ 多时间框架分析未通过: {mtf_data.get('reason', 'unknown')}")
            return {"error": mtf_data.get("reason")}
        
        # Step 2: 获取K线计算支撑阻力
        dataframes = {}
        for tf, interval in [("1d", "1D"), ("4h", "4H"), ("1h", "1H")]:
            df = fetch_okx_candles(symbol, interval, limit=100)
            if df is not None and len(df) >= 30:
                dataframes[tf] = df
        
        # Step 3: 计算支撑阻力
        pivots = None
        if "1d" in dataframes:
            pivots = calculate_pivot_points(dataframes["1d"], PivotMode.FIBONACCI)
            print(f"\n📈 支撑阻力位 (1D):")
            print(f"   Pivot: ${pivots.pivot:.4f}")
            print(f"   阻力: R1=${pivots.res1:.4f}, R2=${pivots.res2:.4f}")
            print(f"   支撑: S1=${pivots.sup1:.4f}, S2=${pivots.sup2:.4f}")
        
        # Step 4: 强平价格计算
        from fetchers.price_api import fetch_price_and_change
        price_data = fetch_price_and_change(symbol)
        current_price = price_data.get("price", 0) if price_data else 0
        
        if current_price > 0:
            leverage = 3  # 默认3倍
            liq_info = calculate_liquidation_price(
                open_rate=current_price,
                amount=100,
                stake_amount=100/leverage,
                leverage=leverage,
                wallet_balance=100/leverage
            )
            distance_pct = liq_info.distance_pct * 100
            print(f"\n💰 强平价格分析 (3x杠杆):")
            print(f"   当前价: ${current_price:.4f}")
            print(f"   强平价: ${liq_info.liquidation_price:.4f}")
            print(f"   距离强平: {distance_pct:.1f}%")
            
            if distance_pct < 15:
                print(f"   ⚠️ 距离强平太近！建议降低杠杆或放弃")
        
        # Step 5: 综合评分
        print(f"\n🎯 综合分析结果:")
        print(f"   决策: {mtf_data.get('decision', 'unknown')}")
        print(f"   原因: {mtf_data.get('reason', 'N/A')}")
        
        return {
            "symbol": symbol,
            "mtf_analysis": mtf_data,
            "pivots": {
                "pivot": pivots.pivot,
                "r1": pivots.res1,
                "s1": pivots.sup1
            } if pivots else None,
            "current_price": current_price,
            "liquidation_info": {
                "price": liq_info.liquidation_price,
                "distance_pct": distance_pct
            } if current_price > 0 else None
        }
    
    def run_autopilot(self, cycles: int = None, interval: int = 300):
        """自动驾驶模式 - 全自动交易闭环"""
        from core.bot_loop import run_bot
        from core.state_manager import get_state
        
        print(f"\n{'='*60}")
        print("🚀 全自动交易闭环 (信号→持仓→优化)")
        print(f"{'='*60}")
        print(f"   扫描间隔: {interval}秒")
        print(f"   监控间隔: 30秒")
        
        state = get_state()
        
        use_real = getattr(self, 'use_real', False)
        run_bot(
            scan_interval=interval,
            monitor_interval=30,
            use_real=use_real
        )
    
    def close_all_positions(self):
        """一键平仓所有OKX真实持仓"""
        from trading.okx_testnet import OKXTestnetTrader
        
        cfg = load_config()
        trader = OKXTestnetTrader(
            cfg.get('api_key', ''),
            cfg.get('api_secret', ''),
            cfg.get('passphrase', ''),
            testnet=True
        )
        
        # 获取余额详情
        balance = trader.get_balance()
        if not balance or 'details' not in balance:
            print('❌ 无法获取余额')
            return
        
        details = balance['details']
        closed_count = 0
        failed = []
        
        print(f"\n{'='*60}")
        print("🔴 一键平仓所有持仓")
        print(f"{'='*60}")
        
        for d in details:
            ccy = d.get('ccy', '')
            eq = float(d.get('eq', 0))
            
            # 跳过USDT和小额币种
            if ccy == 'USDT' or eq < 1:
                continue
            
            print(f"\n📤 平仓 {ccy}...")
            
            # 修复: 使用正确的格式 OP-USDT 而不是 OP
            symbol_for_api = f"{ccy}-USDT"
            
            # 市价卖出
            result = trader.place_order(symbol_for_api, "sell", eq, None, "market")
            
            if result.get('code') == '0':
                print(f"   ✅ 成功卖出 {ccy}")
                closed_count += 1
            else:
                msg = result.get('msg', '未知错误')
                print(f"   ❌ 失败: {msg}")
                failed.append(ccy)
        
        print(f"\n{'='*60}")
        print(f"✅ 平仓完成: {closed_count}个成功, {len(failed)}个失败")
        print(f"{'='*60}")
        
        if failed:
            print(f"失败代币: {', '.join(failed)}")
    
    def check_status(self):
        """查看状态 - 统一从StateManager读取"""
        from trading.okx_testnet import OKXTestnetTrader
        
        cfg = load_config()
        trader = OKXTestnetTrader(
            cfg.get('api_key', ''),
            cfg.get('api_secret', ''),
            cfg.get('passphrase', ''),
            testnet=True
        )
        
        balance = trader.get_balance()
        
        print(f"\n{'='*60}")
        print("📊 MMTracker 持仓状态 (统一数据源)")
        print(f"{'='*60}")
        
        # USDT 余额
        usdt_balance = 0
        if balance and 'details' in balance:
            for d in balance['details']:
                if d.get('ccy') == 'USDT':
                    usdt_balance = float(d.get('availBal', 0))
                    print(f"  💰 USDT: ${usdt_balance:,.2f}")
                    break
        
        # 优先从 StateManager 获取持仓
        positions = []
        try:
            from core.state_manager import get_state
            state = get_state()
            state_positions = state.get_all_positions()
            for token, pos in state_positions.items():
                # 估算当前价值
                try:
                    current_price = state.get_price(token, max_age=300) or pos.entry_price
                    usd_value = pos.size_usd * (current_price / pos.entry_price) if pos.entry_price > 0 else 0
                except:
                    usd_value = pos.size_usd
                positions.append({
                    'token': token,
                    'amount': pos.size_usd,
                    'avg_price': pos.entry_price,
                    'usd_value': usd_value
                })
                print(f"  📦 {token}: 入场价 ${pos.entry_price:.4f}, 仓位 ${pos.size_usd:.2f}")
        except Exception as e:
            print(f"  ⚠️ StateManager 读取失败: {e}")
            # 回退到 OKX API
            if balance and 'details' in balance:
                for d in balance['details']:
                    ccy = d.get('ccy', '')
                    eq = float(d.get('eq', 0))
                    eq_usd = float(d.get('eqUsd', 0))
                    if eq > 0.01 and ccy != 'USDT':
                        avgPx = float(d.get('accAvgPx', 0)) if d.get('accAvgPx') else 0
                        positions.append({'token': ccy, 'amount': eq, 'avg_price': avgPx, 'usd_value': eq_usd})
                        print(f"  📦 {ccy}: {eq:.4f} ≈ ${eq_usd:.2f}")
        
        print(f"\n  总资产: ${usdt_balance + sum(p['usd_value'] for p in positions):,.2f}")
        print(f"  持仓数量: {len(positions)}个")


def main():
    parser = argparse.ArgumentParser(description="MMTracker 统一入口 (模拟盘模式)")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # scan 命令 - 市场扫描
    scan_parser = subparsers.add_parser("scan", help="市场扫描")
    scan_parser.add_argument("--top", "-n", type=int, default=20, help="扫描数量")
    
    # analyze 命令 - 单币分析
    analyze_parser = subparsers.add_parser("analyze", help="单币分析")
    analyze_parser.add_argument("symbol", type=str, help="代币符号，如 BTC")
    
    # auto 命令 - 全自动交易闭环 (信号→持仓→优化)
    auto_parser = subparsers.add_parser("auto", help="全自动交易闭环")
    auto_parser.add_argument("--cycles", "-c", type=int, default=None, help="运行周期数")
    auto_parser.add_argument("--interval", "-i", type=int, default=300, help="扫描间隔(秒)")
    
    # close 命令 - 一键平仓
    subparsers.add_parser("close", help="一键平仓所有持仓")
    
    # status 命令
    subparsers.add_parser("status", help="查看持仓状态")
    
    # test 命令
    test_parser = subparsers.add_parser("test", help="运行测试")
    test_parser.add_argument("symbol", type=str, nargs="?", default="BTC", help="测试代币")
    
    # cycle 命令 - 内循环分析
    cycle_parser = subparsers.add_parser("cycle", help="内循环分析(基于盈亏优化因子)")
    
    # monitor 命令 - 全局监控
    monitor_parser = subparsers.add_parser("monitor", help="全局代币监控(涨幅榜/潜力币)")
    
    # scan2 命令 - 完整技术分析扫描
    scan2_parser = subparsers.add_parser("scan2", help="完整技术分析扫描(推荐)")
    scan2_parser.add_argument("--top", "-n", type=int, default=20, help="扫描数量")
    
    # check 命令 - 检查预测准确度
    check_parser = subparsers.add_parser("check", help="检查历史预测准确度")
    
    # walkforward 命令 - Walk-Forward 验证
    wf_parser = subparsers.add_parser("walkforward", help="Walk-Forward验证(防过拟合)")
    wf_parser.add_argument("--symbol", "-s", type=str, default="BTC", help="测试代币")
    wf_parser.add_argument("--train", "-t", type=int, default=60, help="训练集天数")
    wf_parser.add_argument("--test", type=int, default=14, help="测试集天数")
    
    # optimize 命令 - 元参数优化
    subparsers.add_parser("optimize", help="元参数优化(自动学习)")
    
    # backtest 命令 - 快速回测
    bt_parser = subparsers.add_parser("backtest", help="快速回测验证")
    bt_parser.add_argument("symbol", type=str, nargs="?", default="BTC", help="代币符号")
    
    # intraday 命令 - 日内杠杆策略
    id_parser = subparsers.add_parser("intraday", help="日内杠杆策略回测")
    id_parser.add_argument("--symbol", "-s", type=str, default="BTC", help="代币符号")
    id_parser.add_argument("--leverage", "-l", type=float, default=3.0, help="杠杆倍数")
    id_parser.add_argument("--target", "-t", type=float, default=10.0, help="目标收益%")
    id_parser.add_argument("--stop", type=float, default=3.0, help="止损%")
    
    # daemon 命令 - 24小时全天候自动驾驶 (简化版，等同于 auto)
    daemon_parser = subparsers.add_parser("daemon", help="24小时自动驾驶(同auto命令)")
    daemon_parser.add_argument("--interval", "-i", type=int, default=300, help="扫描间隔(秒)")
    daemon_parser.add_argument("--cycles", "-c", type=int, default=None, help="运行周期数(默认无限)")
    
    # optimize 命令 - 元参数优化
    subparsers.add_parser("optimize", help="元参数优化(自动学习)")
    
    # backtest 命令 - 快速回测
    bt_parser = subparsers.add_parser("backtest", help="快速回测验证")
    bt_parser.add_argument("symbol", type=str, nargs="?", default="BTC", help="代币符号")
    
    args = parser.parse_args()
    
    # 初始化工厂
    factory = MMTrackerFactory()
    
    if args.command == "scan":
        results = factory.scan_market(top_n=args.top)
        print(f"\n✅ 扫描完成，找到 {len(results)} 个候选")
        
    elif args.command == "analyze":
        result = factory.analyze_token(args.symbol)
        if "error" in result:
            print(f"❌ 分析失败: {result['error']}")
        else:
            print(f"✅ 分析完成")
            
    elif args.command in ("trade", "auto", "daemon"):
        # 统一为 auto 命令处理
        if args.command == "trade":
            print("⚠️ 'trade' 命令已废弃，使用 'auto' 或 'daemon' 代替")
        factory.run_autopilot(cycles=args.cycles, interval=args.interval)
        
    elif args.command == "status":
        factory.check_status()
    
    elif args.command == "close":
        factory.close_all_positions()
        
    elif args.command == "optimize":
        from trading.parameter_optimizer import ParameterOptimizer
        from trading.result_logger import ResultLogger
        
        print(f"\n{'='*60}")
        print("🔄 元参数优化")
        print(f"{'='*60}")
        
        logger = ResultLogger()
        optimizer = ParameterOptimizer(logger, "config/strategy_params.json")
        result = optimizer.optimize(force=True)
        
        if result.get('optimized'):
            print(f"\n✅ 因子优化完成:")
            for adj in result.get('adjustments', []):
                print(f"  • {adj}")
        else:
            print(f"\n⚠️ 跳过优化: {result.get('reason', '未知原因')}")
    
    elif args.command == "backtest":
        from scripts.nfi_comprehensive_backtest import quick_backtest
        
        print(f"\n{'='*60}")
        print("📊 快速回测")
        print(f"{'='*60}")
        
        result = quick_backtest(args.symbol)
        print(f"\n✅ 回测完成")
    
    else:
        # 简洁的帮助信息
        print("""
MMTracker 统一入口 (模拟盘模式)
===============================
核心命令:
  python run.py scan                    # 市场扫描 (推荐)
  python run.py analyze <SYMBOL>        # 单币深度分析
  python run.py auto                    # 全自动交易闭环
  python run.py auto -c 10              # 运行10个周期
  python run.py auto -i 180             # 扫描间隔180秒
  python run.py status                  # 查看持仓状态 (统一数据源)
  python run.py close                   # 一键平仓所有持仓
  python run.py daemon --interval 300   # 24小时自动驾驶 (推荐)

其他命令:
  python run.py optimize                # 元参数优化
  python run.py backtest BTC            # 快速回测

数据流: OKX API → StateManager → PositionMonitor → SQLite持久化
""")


if __name__ == "__main__":
    main()
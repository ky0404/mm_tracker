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
        """初始化组件"""
        # 交易器
        try:
            from trading.okx_testnet import OKXTestnetTrader
            testnet = not self.config.get("use_real", False)
            self.trader = OKXTestnetTrader(
                api_key=self.config.get("api_key", ""),
                api_secret=self.config.get("api_secret", ""),
                passphrase=self.config.get("passphrase", ""),
                testnet=testnet
            )
        except Exception as e:
            print(f"⚠️ 交易器初始化失败: {e}")
    
    def scan_market(self, top_n: int = 20) -> list:
        """市场扫描"""
        from scanner.universe import get_full_universe
        from scanner.fast_filter import run_fast_filter
        from signals.calculator import judge_manipulation_stage
        
        print(f"\n{'='*60}")
        print("🔍 MMTracker 市场扫描")
        print(f"{'='*60}")
        
        # Step 1: 获取全市场代币
        universe = get_full_universe()
        print(f"📊 全市场代币: {len(universe)}个")
        
        # Step 2: 画像筛选
        filtered = run_fast_filter(universe)
        print(f"📊 画像筛选后: {len(filtered)}个候选")
        
        # Step 3: 5阶段判定
        results = []
        for item in filtered[:top_n]:
            token = item.get('symbol') if isinstance(item, dict) else item
            if not token:
                continue
            try:
                stage_result = judge_manipulation_stage(
                    {}, {}, {}, None, None  # 简化版
                )
                stage = stage_result.get("stage", "静默积累期")
                confidence = stage_result.get("confidence", 0)
                
                if confidence >= 0.4:
                    results.append({
                        "token": token,
                        "stage": stage,
                        "confidence": confidence
                    })
                    print(f"  ✅ {token}: {stage}, 置信度{confidence:.0%}")
            except:
                continue
        
        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results[:top_n]
    
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
    
    def run_autopilot(self, symbol: str = None, cycles: int = None):
        """自动驾驶模式"""
        from trading.auto_pilot import create_autopilot
        
        print(f"\n{'='*60}")
        print("🚀 启动自动驾驶模式")
        print(f"{'='*60}")
        
        # create_autopilot 不接受参数，直接调用
        self.autopilot = create_autopilot(sim_mode=True)
        
        # 运行交易循环 - 使用 start() 方法
        self.autopilot.start(max_cycles=cycles, interval=60)
    
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
        """查看状态 - 读取OKX真实持仓"""
        from trading.okx_testnet import OKXTestnetTrader
        
        cfg = load_config()
        trader = OKXTestnetTrader(
            cfg.get('api_key', ''),
            cfg.get('api_secret', ''),
            cfg.get('passphrase', ''),
            testnet=True
        )
        
        balance = trader.get_balance()
        if not balance or 'details' not in balance:
            print('❌ 无法获取余额')
            return
        
        details = balance['details']
        
        print(f"\n{'='*60}")
        print("📊 OKX 真实持仓")
        print(f"{'='*60}")
        
        usdt_balance = 0
        positions = []
        for d in details:
            ccy = d.get('ccy', '')
            avail = float(d.get('availBal', 0))
            eq = float(d.get('eq', 0))
            eq_usd = float(d.get('eqUsd', 0))
            
            if ccy == 'USDT':
                usdt_balance = avail
                print(f"  💰 USDT: ${avail:,.2f}")
            elif eq > 0.01:
                avgPx = float(d.get('accAvgPx', 0)) if d.get('accAvgPx') else 0
                positions.append({'token': ccy, 'amount': eq, 'avg_price': avgPx, 'usd_value': eq_usd})
                print(f"  📦 {ccy}: {eq:.4f} ≈ ${eq_usd:.2f}")
        
        print(f"\n  总资产: ${usdt_balance + sum(p['usd_value'] for p in positions):,.2f}")
        print(f"  持仓数量: {len(positions)}个")


def main():
    parser = argparse.ArgumentParser(description="MMTracker 统一入口")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # scan 命令
    scan_parser = subparsers.add_parser("scan", help="市场扫描")
    scan_parser.add_argument("--top", "-n", type=int, default=20, help="扫描数量")
    
    # analyze 命令
    analyze_parser = subparsers.add_parser("analyze", help="单币分析")
    analyze_parser.add_argument("symbol", type=str, help="代币符号，如 BTC")
    analyze_parser.add_argument("--leverage", "-l", type=int, default=3, help="杠杆倍数")
    
    # trade 命令
    trade_parser = subparsers.add_parser("trade", help="自动驾驶交易")
    trade_parser.add_argument("--symbol", "-s", type=str, default=None, help="交易指定代币")
    trade_parser.add_argument("--cycles", "-c", type=int, default=None, help="运行周期数")
    trade_parser.add_argument("--real", action="store_true", help="使用真实账户")
    
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
    
    # daemon 命令 - 24小时全天候自动驾驶
    daemon_parser = subparsers.add_parser("daemon", help="24小时全天候自动驾驶(守护进程)")
    daemon_parser.add_argument("--interval", "-i", type=int, default=300, help="扫描间隔(秒)")
    daemon_parser.add_argument("--cycles", "-c", type=int, default=None, help="运行周期数(默认无限)")
    daemon_parser.add_argument("--real", action="store_true", help="使用真实账户")
    
    # bot 命令 - 新架构 BotCore
    bot_parser = subparsers.add_parser("bot", help="BotCore 机器人管理")
    bot_sub = bot_parser.add_subparsers(dest="bot_command", help="子命令")
    
    bot_start = bot_sub.add_parser("start", help="启动机器人(新架构)")
    bot_start.add_argument("--interval", "-i", type=int, default=300, help="扫描间隔(秒)")
    bot_start.add_argument("--monitor", "-m", type=int, default=30, help="监控间隔(秒)")
    bot_start.add_argument("--real", action="store_true", help="使用真实账户")
    bot_start.add_argument("--strategy", "-s", type=str, default="default", 
                          choices=["default", "intraday"], help="策略模式: default=21信号, intraday=日内杠杆")
    bot_start.add_argument("--leverage", "-l", type=float, default=3.0, help="杠杆倍数(默认3x)")
    bot_start.add_argument("--target", "-t", type=float, default=12.0, help="目标收益%(默认12%)")
    bot_start.add_argument("--stop", type=float, default=3.0, help="止损%(默认3%)")
    bot_start.add_argument("--coins", type=str, default='AVAX,ETH,DOGE,XRP', help="交易币种(逗号分隔)")
    
    bot_sub.add_parser("stop", help="停止机器人")
    bot_sub.add_parser("status", help="查看机器人状态")
    
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
            
    elif args.command == "trade":
        factory.config["use_real"] = args.real
        factory.run_autopilot(symbol=args.symbol, cycles=args.cycles)
        
    elif args.command == "status":
        factory.check_status()
    
    elif args.command == "close":
        factory.close_all_positions()
        
    elif args.command == "test":
        result = factory.analyze_token(args.symbol)
        print(f"\n✅ 测试完成: {result.get('symbol', 'N/A')}")
    
    elif args.command == "cycle":
        from trading.parameter_optimizer import ParameterOptimizer
        from trading.result_logger import ResultLogger
        
        print(f"\n{'='*60}")
        print("🔄 内循环分析 - 基于真实盈亏优化量化因子")
        print(f"{'='*60}")
        
        # 分析
        logger = ResultLogger()
        optimizer = ParameterOptimizer(logger, "config/strategy_params.json")
        analysis = optimizer.analyze_trades()
        
        print(f"\n📊 总交易: {analysis.get('total_trades', 0)}笔")
        print(f"📊 总体胜率: {analysis.get('overall_win_rate', 0)*100:.1f}%")
        
        # 优化
        result = optimizer.optimize(force=True)
        
        if result.get('optimized'):
            print(f"\n✅ 因子优化完成:")
            for adj in result.get('adjustments', []):
                print(f"  • {adj}")
        else:
            print(f"\n⚠️ 跳过优化: {result.get('reason', '未知原因')}")
        
        # 加载代币筛选配置
        if os.path.exists('config/token_screening.json'):
            with open('config/token_screening.json') as f:
                screening = json.load(f)
            
            print(f"\n📊 代币筛选配置:")
            bl = screening.get('blacklist', [])
            wl = screening.get('whitelist', [])
            print(f"  黑名单 ({len(bl)}个): {[t['token'] for t in bl]}")
            print(f"  白名单 ({len(wl)}个): {[t['token'] for t in wl]}")
        
        print(f"\n{'='*60}")
        print("✅ 内循环分析完成")
        print("="*60)
    
    elif args.command == "monitor":
        import requests
        from datetime import datetime
        
        print(f"\n{'='*60}")
        print("🛡️ 全局代币监控中心")
        print(f"{'='*60}")
        
        # 获取实时数据
        resp = requests.get('https://www.okx.com/api/v5/market/tickers?instType=SPOT')
        data = resp.json()
        
        tokens_data = []
        if data.get('code') == '0':
            for t in data['data']:
                inst = t.get('instId', '')
                if inst.endswith('-USDT'):
                    symbol = inst.replace('-USDT', '')
                    last = float(t.get('last', 0))
                    open_24h = float(t.get('open24h', 0))
                    high_24h = float(t.get('high24h', 0))
                    low_24h = float(t.get('low24h', 0))
                    vol24h = float(t.get('vol24h', 0))
                    
                    if open_24h > 0:
                        pct_change = (last - open_24h) / open_24h * 100
                        range_pct = (high_24h - low_24h) / low_24h * 100
                    else:
                        pct_change = 0
                        range_pct = 0
                    
                    tokens_data.append({
                        'symbol': symbol,
                        'price': last,
                        'pct_change': pct_change,
                        'range_pct': range_pct,
                        'vol24h': vol24h
                    })
        
        print(f"\n📊 OKX现货总数: {len(tokens_data)}个")
        
        # 今日涨幅榜
        tokens_data.sort(key=lambda x: x['pct_change'], reverse=True)
        
        print(f"\n{'='*60}")
        print("🔥 今日涨幅榜 Top 20")
        print("="*60)
        for t in tokens_data[:20]:
            emoji = '🚀' if t['pct_change'] > 10 else '📈' if t['pct_change'] > 5 else ''
            print(f"  {emoji} {t['symbol']:8} {t['pct_change']:>+7.2f}%  \${t['price']}")
        
        print(f"\n{'='*60}")
        print("💎 潜在暴涨候选 (成交量100万-1亿)")
        print("="*60)
        
        candidates = [t for t in tokens_data 
                      if 1e6 < t['vol24h'] < 1e8 and t['pct_change'] > 2]
        candidates.sort(key=lambda x: (x['pct_change'], t['vol24h']), reverse=True)
        
        for t in candidates[:15]:
            vol_m = t['vol24h'] / 1e6
            print(f"  💎 {t['symbol']:8} +{t['pct_change']:>5.2f}%  24h: {vol_m:>5.1f}M")
        
        # 读取历史交易
        if os.path.exists('trading/live_trades.json'):
            with open('trading/live_trades.json') as f:
                trades = json.load(f)
            
            traded = set(t.get('token') for t in trades if t.get('token'))
            
            print(f"\n{'='*60}")
            print("🎯 历史交易代币今日表现")
            print("="*60)
            
            for t in tokens_data:
                if t['symbol'] in traded:
                    emoji = '🟢' if t['pct_change'] > 0 else '🔴'
                    print(f"  {emoji} {t['symbol']:8} {t['pct_change']:>+7.2f}%")
        
        print(f"\n{'='*60}")
        print("✅ 监控完成 - 运行 'run.py analyze <SYMBOL>' 分析具体代币")
        print("="*60)
    
    elif args.command == "scan2":
        from scanner.universe import get_full_universe
        from scanner.fast_filter import run_fast_filter
        from scanner.technical_analyzer import TechnicalAnalyzer
        from fetchers.multi_tf import multi_tf_surface_analysis, analyze_1d, analyze_4h
        
        print(f"\n{'='*60}")
        print("🔬 完整技术分析扫描 (NFI Style)")
        print(f"{'='*60}")
        
        print("\n[1] 获取全市场代币...")
        universe = get_full_universe()
        print(f"    全市场代币: {len(universe)}个")
        
        print("\n[2] 执行增强扫描 (涨幅漏斗+趋势+技术分析)...")
        results = run_fast_filter(universe, enable_gain_tracker=True, enable_technical=True)
        
        print(f"\n{'='*60}")
        print("📊 扫描结果 Top 10 (NFI指标)")
        print("="*60)
        
        for i, c in enumerate(results[:10], 1):
            symbol = c['symbol']
            tech_score = c.get('tech_score', 0)
            change = c.get('change_24h_pct', 0)
            combined = c.get('combined_score', 0)
            
            emoji = '🚀' if tech_score >= 10 else '📈' if tech_score >= 6 else '⚠️'
            
            nfi_info = ""
            try:
                d1 = analyze_1d(symbol)
                h4 = analyze_4h(symbol)
                if d1.get('valid') and h4.get('valid'):
                    d1_cti = d1.get('current_cti', 0)
                    d1_ewo = d1.get('current_ewo', 0)
                    h4_cti = h4.get('current_cti', 0)
                    h4_ewo = h4.get('current_ewo', 0)
                    h4_rsi = h4.get('current_rsi', 0)
                    nfi_info = f" | CTI:{d1_cti:.1f}/{h4_cti:.1f} EWO:{d1_ewo:.1f}/{h4_ewo:.1f} RSI:{h4_rsi:.0f}"
            except:
                pass
            
            print(f"{i:2}. {emoji} {symbol:8} 技术:{tech_score:2}/20  24h:{change:+6.1f}%  综合:{combined:.1f}{nfi_info}")
        
        print(f"\n{'='*60}")
        print("📈 NFI核心指标说明")
        print("="*60)
        print("  CTI (Commodity Trading Index): 替代MACD, >0看涨")
        print("  EWO (Elliot Wave Oscillator): 动量指标, >0看涨")  
        print("  Williams %R: 超买超卖, <-80超卖, >-20超买")
        print("  HMA (Hull MA): 趋势指标, 向上看涨")
        
        print(f"\n✅ 扫描完成! 共 {len(results)} 个候选")
        print("💡 建议: 运行 'python run.py analyze <SYMBOL>' 深入分析")
    
    elif args.command == "check":
        import requests
        from datetime import datetime, timezone
        
        print(f"\n{'='*60}")
        print("🔍 检查历史预测准确度")
        print(f"{'='*60}")
        
        # 加载历史预测
        try:
            with open('logs/prediction_history.json', 'r') as f:
                history = json.load(f)
        except:
            print("❌ 无历史预测记录")
            return
        
        if not history:
            print("❌ 无历史预测记录")
            return
        
        # 获取最新预测
        latest = history[-1]
        scan_time = latest['timestamp']
        candidates = latest['candidates']
        
        print(f"\n📅 扫描时间: {scan_time}")
        print(f"📊 推荐代币: {len(candidates)} 个")
        
        # 获取当前价格
        print(f"\n{'='*60}")
        print("📈 预测 vs 实际表现")
        print("="*60)
        
        resp = requests.get('https://www.okx.com/api/v5/market/tickers?instType=SPOT', timeout=10)
        data = resp.json()
        
        current_prices = {}
        if data.get('code') == '0':
            for t in data['data']:
                inst = t.get('instId', '')
                if inst.endswith('-USDT'):
                    symbol = inst.replace('-USDT', '')
                    current_prices[symbol] = float(t.get('last', 0))
        
        # 对比
        for c in candidates:
            symbol = c['symbol']
            old_price = c['price']
            current_price = current_prices.get(symbol, 0)
            
            if current_price > 0:
                change = (current_price - old_price) / old_price * 100
                emoji = '✅' if change > 0 else '❌'
                print(f"{emoji} {symbol:8} 当时:${old_price:.6f} → 现在:${current_price:.6f}  ({change:+.2f}%)")
            else:
                print(f"❓ {symbol}: 无法获取当前价格")
        
        print(f"\n💡 运行 'python run.py scan2' 获取新预测")
    
    elif args.command == "daemon":
        from trading.auto_pilot import create_autopilot
        import time
        
        print(f"\n{'='*60}")
        print("🤖 24小时全天候自动驾驶 (守护进程模式)")
        print(f"{'='*60}")
        
        print(f"\n📋 运行参数:")
        print(f"   扫描间隔: {args.interval}秒 ({args.interval/60:.1f}分钟)")
        print(f"   最大周期: {args.cycles if args.cycles else '无限'}")
        print(f"   交易模式: {'真实账户' if args.real else '测试网'}")
        
        # 使用现有的create_autopilot
        print(f"\n🚀 初始化自动驾驶仪...")
        autopilot = create_autopilot(sim_mode=not args.real)
        print(f"✅ 自动驾驶仪已启动")
        
        # 使用interval参数
        print(f"\n🔄 开始自动交易循环 (按Ctrl+C停止)...")
        print(f"{'='*60}")
        
        # 直接调用start方法，传入interval
        autopilot.start(max_cycles=args.cycles, interval=args.interval)
    
    elif args.command == "bot":
        from core.bot_core import start_bot, stop_bot, get_bot
        
        if args.bot_command == "start":
            config = {
                'scan_interval': args.interval,
                'monitor_interval': args.monitor,
                'use_real': args.real,
                'strategy_mode': args.strategy,
                'leverage': args.leverage,
                'target_return': args.target / 100,
                'stop_loss': args.stop / 100,
                'coins': args.coins.split(',') if hasattr(args, 'coins') else ['AVAX', 'ETH', 'DOGE', 'XRP']
            }
            print(f"\n{'='*60}")
            print("🤖 启动 BotCore 机器人 (新架构)")
            print(f"{'='*60}")
            print(f"   扫描间隔: {args.interval}秒")
            print(f"   监控间隔: {args.monitor}秒")
            print(f"   交易模式: {'真实账户' if args.real else '测试网'}")
            print(f"   策略: {args.strategy}")
            if args.strategy == "intraday":
                print(f"   杠杆: {args.leverage}x")
                print(f"   目标收益: {args.target}%")
                print(f"   止损: {args.stop}%")
            
            # 启动bot
            bot = start_bot(config)
            
            try:
                import time
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n🛑 收到停止信号...")
                stop_bot()
                
        elif args.bot_command == "stop":
            stop_bot()
            print("✅ 机器人已停止")
            
        elif args.bot_command == "status":
            bot = get_bot()
            if not bot.db:
                bot.initialize()
            status = bot.status()
            
            print(f"\n{'='*50}")
            print("🤖 MMTracker 状态")
            print(f"{'='*50}")
            print(f"  机器人状态: {status['bot_status']}")
            print(f"  调度器运行: {status['scheduler']['running']}")
            print(f"  总交易数: {status['trades'].get('total_trades', 0)}")
            print(f"  胜率: {status['trades'].get('win_rate', 0)*100:.1f}%")
            print(f"  总盈亏: ${status['trades'].get('total_pnl', 0):.2f}")
            print(f"{'='*50}")
        
        else:
            bot_parser.print_help()
    
    elif args.command == "walkforward":
        from scripts.walk_forward_validator import WalkForwardValidator, run_enhanced_walk_forward
        
        print(f"\n{'='*60}")
        print("🔬 Walk-Forward 验证 (防过拟合)")
        print(f"{'='*60}")
        
        symbol = args.symbol if hasattr(args, 'symbol') else "BTC"
        result = run_enhanced_walk_forward(symbol=symbol)
        
        if hasattr(result, 'is_overfitting') and result.is_overfitting:
            print(f"\n⚠️ 过拟合警告: 请降低信号复杂度或增加验证数据")
        else:
            print(f"\n✅ 验证通过: 信号具有一定的泛化能力")
    
    elif args.command == "optimize":
        from trading.meta_optimizer import MetaOptimizer
        
        print(f"\n{'='*60}")
        print("🧠 元参数优化 (自动学习)")
        print(f"{'='*60}")
        
        optimizer = MetaOptimizer(min_trades_before_optimize=10)
        result = optimizer.run()
        
        if result.get('optimized'):
            print(f"\n✅ 优化完成:")
            for change in result.get('changes', []):
                print(f"  • {change.get('signal', change.get('type'))}: {change.get('old')} → {change.get('new')}")
            print(f"\n📊 统计:")
            print(f"  总交易: {result.get('overall_stats', {}).get('total_trades', 0)}")
            print(f"  胜率: {result.get('overall_stats', {}).get('win_rate', 0)*100:.1f}%")
        else:
            print(f"\n⚠️ 跳过优化: {result.get('reason', '')}")
    
    elif args.command == "backtest":
        from backtest.engine import BacktestEngine
        
        print(f"\n{'='*60}")
        print("📈 快速回测验证")
        print(f"{'='*60}")
        
        engine = BacktestEngine()
        
        result = engine.run_quick(
            symbol=args.symbol if hasattr(args, 'symbol') else "BTC",
            days=90
        )
        
        print(f"\n📊 回测结果:")
        print(f"  总交易: {result.get('total_trades', 0)}")
        print(f"  胜率: {result.get('win_rate', 0)*100:.1f}%")
        print(f"  总盈亏: {result.get('total_pnl', 0):.2f}%")
        print(f"  Sharpe: {result.get('sharpe', 0):.2f}")
        print(f"  最大回撤: {result.get('max_drawdown', 0)*100:.1f}%")
    
    elif args.command == "intraday":
        from scripts.intraday_leverage import IntradayLeverageStrategy
        
        print(f"\n{'='*60}")
        print("⚡ 日内杠杆策略回测")
        print("="*60)
        print(f"  代币: {args.symbol}")
        print(f"  杠杆: {args.leverage}x")
        print(f"  目标收益: {args.target}%")
        print(f"  止损: {args.stop}%")
        
        strategy = IntradayLeverageStrategy(
            leverage=args.leverage,
            target_return=args.target / 100,
            stop_loss=args.stop / 100
        )
        
        result = strategy.run_walk_forward(args.symbol)
        
        train = result.get('train', {})
        test = result.get('test', {})
        
        print(f"\n📊 训练集: {train.get('total_trades', 0)}笔, 胜率{train.get('win_rate', 0)*100:.1f}%")
        print(f"📊 测试集: {test.get('total_trades', 0)}笔, 胜率{test.get('win_rate', 0)*100:.1f}%")
        print(f"   总收益: {test.get('total_return', 0)*100:.2f}%")
        print(f"   最佳: {test.get('best_trade', 0)*100:+.2f}%, 最差: {test.get('worst_trade', 0)*100:.2f}%")
        print(f"\n{'✅ 策略有效' if not result.get('is_overfitting') else '⚠️ 需优化'}")
    
    else:
        parser.print_help()
        print(f"\n{'='*60}")
        print("📖 快速开始:")
        print(f"  python run.py scan                    # 市场扫描(基础)")
        print(f"  python run.py scan2                   # 完整技术分析扫描(推荐)")
        print(f"  python run.py analyze BTC             # 分析BTC")
        print(f"  python run.py monitor                 # 全局代币监控")
        print(f"  python run.py cycle                   # 内循环分析")
        print(f"  python run.py trade --cycles 3        # 自动交易(3轮)")
        print(f"  python run.py daemon                   # 24小时自动驾驶(守护进程)")
        print(f"  python run.py daemon --real            # 真实账户自动驾驶")
        print(f"  python run.py bot start                 # BotCore 新架构(推荐)")
        print(f"  python run.py bot status                # 查看 BotCore 状态")
        print(f"  python run.py close                    # 一键平仓")
        print(f"  python run.py status                   # 查看状态")
        print(f"  python run.py check                    # 检查预测准确度")
        print(f"  python run.py walkforward              # Walk-Forward验证(防过拟合)")
        print(f"  python run.py optimize                 # 元参数优化(自动学习)")
        print(f"  python run.py backtest BTC             # 快速回测验证")
        print(f"{'='*60}")


if __name__ == "__main__":
    main()
# MMTracker 代码功能完整梳理

> 整理日期: 2026-06-13
> 作者: AI Agent
> 用途: 核对开发方向是否正确

---

## 一、项目入口文件

| 文件 | 功能说明 |
|------|----------|
| `scan.py` | 市场全局扫描器 CLI入口，支持 `--top 30`, `--watch 600`, `--quick` 等参数 |
| `main.py` | 单币分析工具，直接分析指定代币 |
| `autopilot.py` | 自动驾驶仪（已重构为动量模式） |
| `test_okx_testnet.py` | OKX测试网连接测试 |

---

## 二、核心模块结构

```
mm_tracker/
├── scanner/          # 市场扫描模块
├── signals/          # 信号计算模块
├── fetchers/         # 数据获取模块
├── trading/          # 交易执行模块
├── backtest/         # 回测模块
├── report/           # 报告生成模块
├── config/           # 配置文件
└── tests/            # 测试模块
```

---

## 三、数据获取模块 (fetchers/)

| 文件 | 功能 |
|------|------|
| `price_api.py` | 价格/市值获取，OKX优先，CoinGecko备选 |
| `coingecko.py` | CoinGecko API封装 |
| `coinglass.py` | 资金费率/OI数据 (CoinGlass) |
| `dexscreener.py` | DEX流动性与买卖比 |
| `futures_check.py` | Binance新合约检查 |
| `momentum.py` | OKX动量扫描 (1H价格+成交量) |
| `binance_pub.py` | Binance公开数据 |
| `kraken_price.py` | Kraken价格 |
| `notification.py` | 桌面通知 |
| `utils.py` | 限流/缓存/429退避 |
| `social_api.py` | 社媒数据(占位) |
| `base.py` | 基础类 |

---

## 四、信号计算模块 (signals/)

### 11个信号详情

| # | 信号ID | 名称 | 说明 | 权重 |
|---|--------|------|------|------|
| 1 | signal_1_integer_consolidation | 整数关口横盘 | 价格在整数关口(0.05/0.1/0.5/1.0)附近横盘3-7天或>14天 | 1.5 |
| 2 | signal_2_funding_turn_positive | 资金费率转正 | 资金费率从负转正+上升趋势 | 1.8 |
| 3 | signal_3_oi_accumulation | OI吸筹 | OI在价格横盘期间暗中增加 | 1.5 |
| 4 | signal_4_volume_spike | 放量未大涨 | 成交量突增3x/5x/10x但价格未大涨，量价背离 | 1.0 |
| 5 | signal_5_dex_buy_pressure | DEX买压 | DexScreener买卖比>1.05持续多日 | 1.0 |
| 6 | signal_6_btcd_downtrend | BTC.D下降 | BTC.D处于下降通道 | 1.0 |
| 6b| signal_6b_btc_relative_strength | BTC相对强度 | BTC走强时该币走弱，资金托盘 | 0.8 |
| 7 | signal_7_new_futures | 新永续合约 | Binance新增该币永续合约30天内 | 1.5 |
| 8 | signal_8_wash_test | 洗盘测试 | 假拉升后回落，测试抛压 | 1.5 |
| 9 | signal_9_social_sentiment | 社媒情绪 | (占位待接入) | 0.5 |
| 10| signal_10_breakout | 关键关口突破 | 突破关键心理关口(0.1/0.5/1.0) | 2.0 |
| 11| signal_11_early_warning | 多信号组合预警 | 多信号早期组合预警 | 1.0 |

---

## 五、市场扫描模块 (scanner/)

| 文件 | 功能 |
|------|------|
| `universe.py` | 全市场代币列表获取 (OKX 350+个SWAP) |
| `fast_filter.py` | 快速画像筛选 (价格$0.01-$10, 成交量>$1M) |
| `deep_scan.py` | 深度7信号分析 |
| `scan_report.py` | 报告渲染与保存 |

---

## 六、交易执行模块 (trading/)

| 文件 | 功能 |
|------|------|
| `auto_pilot.py` | 自动驾驶仪 - 动量模式 |
| `okx_testnet.py` | OKX测试网真实交易 |
| `mock_trader.py` | 模拟交易器 |
| `live_trader.py` | 真实交易执行 |
| `position_monitor.py` | 持仓监控 (SL/TP/超时) |
| `result_logger.py` | 交易记录与统计 |
| `parameter_optimizer.py` | 参数优化器 |
| `optimized_strategy.py` | 优化策略 |
| `okx_optimizer.py` | OKX优化器 |

---

## 七、核心流程

### scan.py 全市场扫描流程
```
1. get_full_universe()   → 获取全市场代币列表 (OKX 350+个)
2. run_fast_filter()     → 画像筛选 (价格/成交量/市值)
3. deep_scan_batch()     → 深度7信号分析
4. render_scan_results() → 终端输出 + Markdown报告
```

### main.py 单币分析流程
```
1. fetch_price_and_change()      → 获取价格
2. fetch_funding_rate_history()  → 资金费率
3. fetch_oi_history()            → OI数据
4. fetch_daily_ohlcv()           → K线数据
5. fetch_dex_data()              → DEX买卖比
6. calculate_all_signals()       → 计算11个信号
7. calculate_score()             → 评分与分级
```

### auto_pilot.py 自动驾驶流程
```
1. scan_market()           → 动量扫描 (OKX 1H数据)
2. analyze_token()         → 动量信号分析
3. should_entry()          → 入场决策
4. execute_entry()         → 执行入场
5. check_and_close_positions() → 持仓监控(SL/TP/超时)
6. optimize()              → 参数优化(动量模式已禁用)
```

---

## 八、关键配置

### strategy_params.json 风控参数
```json
{
  "risk_management": {
    "stop_loss_pct": 1000,      // 用户禁用
    "take_profit_pct": 30,      // 止盈30%
    "max_hold_minutes": 240,    // 4小时超时
    "default_position_size": 128,
    "max_open_positions": 5
  }
}
```

### 离场铁律
- 资金费率 > 0.5% → 减仓
- 资金费率 > 1.0% → 清仓
- 止盈 30% → 平仓
- 持仓超4小时 → 强制平仓

---

## 九、README 7条件框架 vs 系统实现对比

### README 入场7条件
| # | 条件 |
|---|------|
| 1 | 价格在整数关口下方横盘 3~7天 或 >14天 |
| 2 | 资金费率从负/零开始转正且持续上升 |
| 3 | OI在价格横盘期间悄悄增加 |
| 4 | 某一天出现 3x 以上放量但价格未大涨 |
| 5 | DexScreener 买卖比 >1.2 且持续多日 |
| 6 | BTC.D 处于下降通道 |
| 7 | Binance 新增了该币的永续合约 |

### 系统信号对应关系

| README条件 | 系统信号实现 | 状态 |
|------------|-------------|------|
| 1 | signal_1_integer_consolidation | ✅ |
| 2 | signal_2_funding_turn_positive | ✅ |
| 3 | signal_3_oi_accumulation | ✅ |
| 4 | signal_4_volume_spike | ✅ |
| 5 | signal_5_dex_buy_pressure | ✅ |
| 6 | signal_6_btcd_downtrend | ✅ |
| 7 | signal_7_new_futures | ✅ |

### 结论
- ✅ 7条件框架在 `scan.py` / `main.py` 中完整实现
- ❌ `auto_pilot.py` 动量模式绕过了7条件，使用轻量信号

---

## 十、发现问题与修复状态

| # | 问题 | 状态 |
|---|------|------|
| 1 | PnL计算异常 (-$300万记录) | ✅ 已修复 |
| 2 | 优化器过早触发 | ✅ 已修复 |
| 3 | 入场价格记录错误 | ✅ 已修复 |
| 4 | 动量模式绕过7条件框架 | ⚠️ 待优化 |
| 5 | 启动时自动清除旧仓位 | ⚠️ 仅模拟模式执行 |
| 6 | 资金费率检查未完全集成 | ⚠️ 部分实现 |

---

## 十一、当前运行状态

### 持仓状态
- 代币: LIT
- 入场价: $1.5195
- 仓位: $50 (轻仓)
- 盈亏: -0.96% (-$0.73)
- 持仓时间: 107分钟
- 资金费率: 0.0050% ✅

### 运行模式
- 交易模式: 模拟交易 (mock_trader)
- 扫描模式: 动量模式 (OKX 1H数据)
- 优化模式: 已禁用
- 7条件框架: 仅在scan.py/main.py中使用

---

## 十二、建议优化方向

1. **统一扫描逻辑**: auto_pilot.py 应使用7条件框架替代动量扫描
2. **增强资金费率监控**: 在 scan_market 阶段过滤高费率币种
3. **分离模拟/真实模式**: 启动时清除仓位应区分模式
4. **完善日志记录**: 所有决策需记录完整日志

---

*文档结束*
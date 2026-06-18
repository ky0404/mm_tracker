# MMTracker 自优化闭环交易系统

> 基于 SVG 流程图: `auto_pilot_closed_loop.svg`

---

## 一、系统闭环架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        执行阶段（每次扫描）                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────────┐ │
│  │ 市场扫描 │───▶│ 信号评级 │───▶│ 交易决策 │───▶│ OKX模拟执行   │ │
│  │OKX全量代币│    │11个量化指标│    │ 触发则入场 │   │ 下单·建仓    │ │
│  └──────────┘    └──────────┘    └──────────┘    └──────────────┘ │
│                                                                     │
│                              ↓                                      │
│                                                                     │
│  ┌──────────────┐    ┌──────────┐    ┌──────────────┐             │
│  │  持仓监控    │───▶│ 结果记录  │───▶│  参数优化器   │             │
│  │ SL/TP自动管理│    │ 胜率·PnL统计│    │ 统计→调权重   │             │
│  └──────────────┘    └──────────┘    └──────────────┘             │
│                                                                     │
│                              ↑                                      │
│                              │                                      │
│                     ┌────────┴────────┐                            │
│                     │ 自动更新信号权重 │                            │
│                     │ (反馈到市场扫描)  │                            │
│                     └─────────────────┘                            │
│                                                                     │
│                        学习阶段（每N笔自动触发）                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 二、七大模块详解

### 1. 市场扫描
- **功能**: 获取OKX全量合约代币 + DEX新池
- **实现文件**:
  - `scanner/universe.py` → `get_full_universe()`
  - `scanner/fast_filter.py` → `run_fast_filter()`
- **输出**: 候选代币列表
- **代码对应**:
  ```python
  from scanner.universe import get_full_universe
  from scanner.fast_filter import run_fast_filter
  
  universe = get_full_universe()  # 获取全市场代币
  candidates = run_fast_filter(universe)  # 画像筛选
  ```

### 2. 信号评级
- **功能**: 使用11个量化指标评估每个代币
- **实现文件**:
  - `scanner/fast_filter.py` → 画像快速筛选
  - `signals/factory.py` → 21个信号工厂 `SignalFactory.scan_all()`
  - `fetchers/multi_tf.py` → NFI多时间框架分析 `multi_tf_surface_analysis()`
  - `signals/calculator.py` → 5阶段判定 `judge_manipulation_stage()`
- **输出**: 每个代币的信号分数
- **代码对应**:
  ```python
  from signals.factory import SignalFactory
  from fetchers.multi_tf import multi_tf_surface_analysis
  
  signal_results = SignalFactory.scan_all(token, signal_data)
  score_result = SignalFactory.calculate_total_score(signal_results)
  ```

### 3. 交易决策
- **功能**: 判断是否触发入场条件
- **实现文件**:
  - `trading/auto_pilot.py` → `should_entry()`, `analyze_token()`
- **条件**: 5阶段判定 + 信号分数阈值 + 4H门卫
- **代码对应**:
  ```python
  from trading.auto_pilot import create_autopilot
  
  autopilot = create_autopilot()
  analysis = autopilot.analyze_token(token)
  if autopilot.should_entry(analysis):
      autopilot.execute_entry(token, analysis)
  ```

### 4. OKX模拟执行 (Freqtrade风格)
- **功能**: 真实API下单建仓
- **核心原则**: 限价单挂单，不追高
- **实现文件**:
  - `trading/okx_testnet.py` → `OKXTestnetTrader` 类
  - `trading/order_manager.py` → `OrderManager` + `FreqtradeStyleExit`
  - `trading/multi_tf_analyzer.py` → 多时间框架确认
  - `trading/entry_price_learner.py` → 挂单价学习器
- **挂单策略**:
  - 限价单 `'entry': 'limit'` (不追高)
  - 挂单价 = 买一价 * (1 - offset_pct)
  - 默认 offset = 0.5% (可学习调整)
  - 分批挂单: 3批，梯度价格
- **多时间框架确认** (Freqtrade @informative):
  - 4H EMA趋势门卫 (最重要)
  - 1H RSI确认
  - 15m 超卖反弹信号
- **配置**: `config/testnet_config.json`
- **代码对应**:
  ```python
  from trading.order_manager import OrderManager, OrderConfig
  from trading.entry_price_learner import EntryPriceLearner
  from trading.multi_tf_analyzer import MultiTimeFrameAnalyzer
  
  # 1. 多时间框架分析
  analyzer = MultiTimeFrameAnalyzer()
  analysis = analyzer.analyze(token)  # 获取4H/1H/15m信号
  
  # 2. 计算最佳挂单价 (带学习)
  learner = EntryPriceLearner()
  entry = learner.get_entry_price(token, bid, ask)
  
  # 3. 生成智能订单 (分批挂单)
  generator = SmartOrderGenerator(learner)
  orders = generator.generate_orders(token, 888, bid, ask, split_count=3)
  
  # 4. 挂限价单
  order_mgr = OrderManager(api_key, api_secret, passphrase, testnet=True)
  for o in orders:
      order_mgr.place_limit_order(token, 'buy', o['size'], o['price'])
  ```

### 5. 持仓监控 (Freqtrade Trailing Stop)
- **功能**: 自动管理SL/TP
- **实现文件**:
  - `trading/position_monitor.py` → `check_positions()`, `_check_exit_conditions()`
  - `trading/dynamic_exit.py` → `EnhancedExitManager`
  - `trading/order_manager.py` → `FreqtradeStyleExit`
- **Freqtrade风格出场策略**:
  - **Trailing Stop**: 创新高后自动上移止损
    - 触发条件: 利润 >= 2%
    - 止损上移: highest_price * (1 - 4%)
  - **Dynamic ROI**: 不同持仓时间不同止盈
    - 0-30分钟: 5%
    - 30-60分钟: 3%
    - 60分钟以上: 20%固定
  - **Exit Signal**: RSI>70, SAR反转
- **策略**:
  - 固定止损 **0%**（3倍杠杆不平仓）
  - 固定止盈 **20%**（3倍杠杆=60%利润）
  - 持仓周期：1天（或一晚上）
- **代码对应**:
  ```python
  from trading.order_manager import FreqtradeStyleExit
  
  exit_mgr = FreqtradeStyleExit({
      "trailing_stop": True,
      "trailing_stop_positive": 0.02,  # 2%启动
      "trailing_stop_offset": 0.04,    # 4%锁定
      "min_roi": {"0": 0.20}            # 20%止盈
  })
  
  exit_mgr.set_entry(entry_price)
  
  # 每周期检查
  should_exit, reason = exit_mgr.should_exit(current_price, rsi=rsi)
  ```

### 6. 结果记录
- **功能**: 记录每笔交易的盈亏统计
- **实现文件**:
  - `trading/result_logger.py` → `log_entry()`, `log_exit()`, `get_stats()`
  - `trading/live_trades.json` → 历史交易记录
- **统计**: 胜率、总PnL、平均持仓时间
- **代码对应**:
  ```python
  from trading.result_logger import ResultLogger
  
  logger = ResultLogger()
  logger.log_entry(token, signals, score, entry_price, position_size, market_context)
  logger.log_exit(token, exit_price, exit_reason, pnl)
  stats = logger.get_stats()  # {'total_trades': 6, 'win_rate': 0.33, 'total_pnl': -136.59}
  ```

### 7. 参数优化器
- **功能**: 根据历史表现调整信号权重
- **实现文件**:
  - `trading/meta_optimizer.py` → `MetaOptimizer.run()`
  - `config/strategy_params.json` → 信号权重配置
- **触发**: 每20笔交易自动优化
- **更新**: 信号权重 `signal_weights` + 风险参数 `risk_management`
- **代码对应**:
  ```python
  from trading.meta_optimizer import MetaOptimizer
  
  optimizer = MetaOptimizer(min_trades_before_optimize=20)
  result = optimizer.run()  # {'optimized': True, 'changes_count': 5}
  ```

---

## 三、数据流

| 阶段 | 输入 | 处理 | 输出 |
|------|------|------|------|
| 1.市场扫描 | OKX API | universe.get_full_universe() | 代币列表 |
| 2.信号评级 | 代币列表 | fast_filter + factory | 信号分数 |
| 3.交易决策 | 信号分数 | should_entry() | boolean |
| 4.OKX执行 | 入场信号 | okx_testnet.place_order() | 订单ID |
| 5.持仓监控 | 价格数据 | check_positions() | 平仓信号 |
| 6.结果记录 | 平仓结果 | log_exit() | 交易记录 |
| 7.参数优化 | 20+笔交易 | meta_optimizer.run() | 新权重 |

---

## 四、关键配置文件

| 文件 | 作用 |
|------|------|
| `config/testnet_config.json` | OKX API配置 |
| `config/strategy_params.json` | 风险参数+信号权重 |
| `config/params.json` | NFI保护参数+DCA配置 |
| `trading/live_trades.json` | 历史交易记录 |

---

## 五、当前状态

### 交易统计 (2026-06-15 傍晚)

| 指标 | 值 |
|------|-----|
| 总交易 | 16笔 EXIT + 4笔持仓中 |
| 胜率 | **68.75%** (11胜5负) |
| 当前持仓 | OKB, WCT (DOGE已止损) |
| 触发优化 | 还需4笔 (需20笔) |

### 持仓监控策略

| 参数 | 值 | 说明 |
|------|-----|------|
| 止损 | 0% | 3倍杠杆合约不平仓，持有一个周期 |
| 止盈 | 20% | 收益率达到20%自动止盈（3倍杠杆→60%利润） |
| 持仓周期 | 1天 | 入场后持有一天或一个晚上再卖 |

### 历史教训 (2026-06-15)

| 代币 | 成本价 | 24h最高 | 最高涨幅 | 结局 |
|------|--------|---------|----------|------|
| DOGE | 0.0889 | 0.08921 | +0.35% | ❌ 亏损卖出 |
| OKB | 75.42 | 75.80 | +0.50% | 🟡 继续持有 |
| WCT | 0.0484 | 0.04951 | +2.25% | 🟡 继续持有 |

**教训**：历史数据显示这些代币都曾高于成本价，但因缺少止盈策略未能获利。

---

## 六、References
| 16 | NEAR | 2.08 | 2.24 | 42.98 | ✅赢 | SELL_ALL |

---

## 六、交易复盘与问题诊断

### 按出场原因统计

| 出场原因 | 笔数 | 累计PnL |
|----------|------|---------|
| STOP_LOSS | 3笔 | -$9.50 |
| MANUAL_EXIT | 1笔 | -$131.19 |
| SELL_ALL | 10笔 | +$65.70 |
| MAX_HOLD_TIME | 1笔 | +$4.10 |

### 信号效果分析

| 信号 | 胜 | 负 | 胜率 | 总PnL |
|------|----|----|------|-------|
| manual_sell_all | 9 | 1 | 90% | +$65.70 |
| signal_2_funding_turn | 1 | 2 | 33% | -$1.98 |
| signal_4_volume_spike | 1 | 1 | 50% | -$3.42 |
| signal_11_early_warning | 1 | 3 | 25% | -$9.50 |
| **signal_7_new_futures** | 1 | 4 | **20%** | **-$140.69** |

### 核心问题

1. **signal_7 (新上合约)**: 触发最多但亏损最严重
   - 胜率仅20%，亏损$140.69
   - **建议**: 降低权重 1.5→0.5，或增加过滤条件

2. **signal_11 (早期预警)**: 胜率过低
   - 胜率25%，亏损$9.50
   - **建议**: 暂时关闭或提高触发阈值

3. **SOL手动止损**: 亏损$131
   - 原因: 入场后趋势逆转，未及时触发止损
   - **建议**: 优化持仓监控的止损逻辑

4. **SELL_ALL清仓**: 不应计入系统绩效
   - 10笔清仓操作是手动行为，用于统计偏差

### 优化建议

| 参数 | 当前值 | 建议值 | 理由 |
|------|--------|--------|------|
| signal_7_new_futures.weight | 1.5 | 0.5 | 亏损严重 |
| signal_11_early_warning.enabled | True | False | 胜率过低 |
| entry_threshold | 3.0 | 4.5 | 提高入场门槛 |
| stop_loss_pct | 3% | 2% | 更早止损 |

---

打造一个完全契合自己交易节奏和心理承受能力的内循环系统，这个想法非常棒。程序化交易的最大优势就是排除了情绪干扰，并通过闭环系统不断迭代，最终逼近你想要的“高胜率+稳定盈利”模型。

不过，作为你的AI助手，我必须直言不讳地指正你关于“不止损，大方向正确，就不会被强平”的误区。在加密货币市场，3倍杠杆意味着标的资产价格反向波动约 **33.3%** 就会触发强平（Liquidation）。对于山寨币而言，单日或瞬间插针达到30%是非常常见的极端行情。即使它之后的“大方向”确实如你所料涨回去了，你的仓位在插针的那一刻就已经归零了。

既然你希望在系统层面实现“不止损”且“防强平”，我们就必须把所有的风险控制前置：**极度苛刻的入场条件**、**深度的挂单策略**以及**大周期级别的趋势确认**。

基于你的需求，我为你重新梳理并修改了 `agents.md` 中的核心模块。你可以将以下内容替换或补充到你的文档中：

#### 三、交易决策与时间管理 (修改版)

- **功能**: 确保每天仅在最佳时机交易3-4次，避免频繁开仓导致风险敞口过大。

- **核心逻辑**:

  - **大方向确认 (防强平基石)**: 必须满足日线 (1D) 和 4小时 (4H) 级别同时处于明确上升趋势 (如 EMA20 > EMA50 且 MACD 零轴上方)。
  - **时间段分散**: 将一天分为早 (00:00-08:00)、中 (08:00-16:00)、晚 (16:00-24:00) 三个区间，每个区间最多允许触发1-2次入场信号。
  - **频率锁**: 每日最大开仓次数限制为 4 次。

- **代码对应**:

  Python

  ```
  from trading.time_manager import DailyTradeLimit
  
  # 每日交易频率控制
  trade_limit = DailyTradeLimit(max_daily=4, max_per_session=2)
  if autopilot.should_entry(analysis) and trade_limit.can_trade():
      autopilot.execute_entry(token, analysis)
  ```

#### 四、OKX模拟执行与精准挂单 (修改版)

- **功能**: 3倍杠杆，888U总资金管理，寻找最优入场点与挂单时效。

- **资金管理**:

  - 总资金: **888U**
  - 杠杆倍数: **3x**
  - 单次开仓: 若每天计划3-4次，可采用总资金的1/4作为单次保证金（约222U），加上3x杠杆，单笔实际头寸约为 **666U**。

- **挂单策略 (Limit Orders)**:

  - **挂单点位**: 根据过去1小时的 VWAP（成交量加权平均价）或强支撑位挂单，坚决不市价追高。
  - **挂单深度**: `order_price = support_level_price` 或 `current_price * (1 - 1.5%)`（等待价格回调接针）。
  - **订单有效时长 (Time-in-Force)**: 设定为 **4小时**（GTD/GTT订单）。如果4小时内未成交，说明入场时机已过，系统自动撤单，等待下一次机会。

- **代码对应**:

  Python

  ```
  from trading.order_manager import OrderManager
  
  # 挂单参数设置
  position_size_usd = 222 * 3  # 3倍杠杆头寸
  entry_price = learner.calculate_pullback_entry(token) # 计算回调接针价
  
  # 发送带有效期的限价单 (4小时后自动撤销)
  order_mgr.place_limit_order(
      token=token, 
      side='buy', 
      size=position_size_usd, 
      price=entry_price,
      time_in_force='GTT', 
      cancel_after_hours=4
  )
  ```

#### 五、持仓监控与无止损管理 (修改版)

- **功能**: 执行20%目标收益率，无硬止损，依靠前端确认规避爆仓。

- **出场策略**:

  - **硬性止盈 (Take Profit)**: 达到 **20%** 收益率自动市价平仓（在3倍杠杆下，标的资产上涨约 **6.67%** 即可达成目标）。
  - **无硬止损 (Stop Loss = 0)**: 取消价格触发的硬止损机制。
  - **系统级防爆仓兜底 (Emergency Exit)**: 虽然不设常规止损，但必须监控日线级别的趋势逆转。如果日线级别的大方向彻底破坏（例如日线收盘跌破关键结构），系统触发“趋势失效平仓”，哪怕亏损也要走，防止插针到-33%导致全仓归零。

- **代码对应**:

  Python

  ```
  from trading.order_manager import CustomExit
  
  exit_mgr = CustomExit({
      "take_profit_pct": 0.20,  # 目标收益20%
      "stop_loss_pct": 0.00,    # 无固定止损
      "trend_reversal_exit": True # 趋势反转紧急逃生
  })
  ```

#### 七、参数优化器 (修改版)

- **优化目标转移**:
  - 由于取消了止损优化，优化器的核心指标从“胜率/盈亏比”转移到 **“挂单成交率”** 和 **“达成20%收益的时间”**。
  - **学习方向**: 如果挂单经常不成交，说明挂单价太低，优化器自动略微上调 `offset_pct`；如果成交后经常出现大额浮亏，说明接刀太早，优化器自动增加 `MACD` 或 `RSI` 等确认指标的权重。

## 七、References

- [[run.py|统一入口 - 工厂模式]]
- [[scanner/universe|市场扫描模块]]
- [[signals/factory|信号工厂]]
- [[trading/auto_pilot|自动驾驶仪]]
- [[trading/okx_testnet|OKX交易器]]
- [[trading/position_monitor|持仓监控]]
- [[trading/meta_optimizer|参数优化器]]
- [[config/params|参数配置]]
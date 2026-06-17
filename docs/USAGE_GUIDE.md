# MMTracker 交易系统使用指南

> 最后更新: 2026-06-14

---

## 一、项目入口文件（按功能区分）

| 文件 | 功能 | 使用场景 |
|------|------|----------|
| `scan.py` | 市场全局扫描器 | 快速扫描全市场，找候选代币 |
| `main.py` | 单币分析工具 | 分析指定代币的7条件+5阶段 |
| `autopilot.py` | 自动驾驶仪 | **自动执行交易决策（推荐）** |
| `test_okx_testnet.py` | OKX测试网测试 | 测试API连接和下单 |

---

## 二、快速使用流程

### 方式1: autopilot.py（自动交易，推荐）

```bash
cd /mnt/c/Users/朱/Desktop/hexagon_copilot/mm_tracker

# 模拟交易（不真下单）
python3 autopilot.py --cycles 1 --sim

# 真实OKX测试网下单
python3 autopilot.py --cycles 1 --real
```

### 方式2: scan.py + 手动交易

```bash
# 快速扫描市场
python3 scan.py --quick

# 分析单个代币
python3 main.py BTC ETH SOL
```

---

## 三、决策逻辑

```
7条件框架 + 5阶段判定 → 清算状态检测 → 入场决策
```

1. **7条件框架**: 价格在整数关口、资金费率转正、OI吸筹、成交量放大、DEX买压、BTC.D下降、新永续合约
2. **5阶段判定**: 静默积累期 → 洗盘测试期 → 拉升启动期 → 整数关口收割期 → 出货分发期
3. **清算检测**: pre_sweep(清算前兆) → sweeping(清算中) → post_sweep(入场窗口)
4. **风控参数**:
   - 杠杆: 3x
   - 仓位: 888U/笔
   - 止盈: 30%
   - 止损: 3%
   - 超时: 4小时

---

## 四、OKX测试网说明

### 测试网可交易的代币

```
✅ BTC  ✅ ETH  ✅ SOL  ✅ DOGE  ✅ XRP  ✅ FIL  ✅ OKB
✅ WCT  ✅ NEAR ✅ SUI  ✅ TIA  ✅ ZRO  ✅ MORPHO
❌ ZKP  ❌ AERO  ❌ TRUST（不在测试网）
```

**注意**: 不是所有代币都在OKX测试网可交易，下单前需确认。

### 测试网访问方式

- URL: `https://openapi.okx.com`（测试网）
- 需要header: `x-simulated-trading: 1`
- API配置文件: `config/testnet_config.json`

---

## 五、核心代码模块说明

### fetchers/
| 文件 | 功能 |
|------|------|
| `momentum.py` | **价格获取**: `get_okx_price(symbol)` ← 获取实时价格 |
| `sweep_detector.py` | 清算状态检测 |
| `price_api.py` | 价格/市值获取 |
| `dexscreener.py` | DEX流动性 |

### trading/
| 文件 | 功能 |
|------|------|
| `auto_pilot.py` | 自动驾驶主逻辑 |
| `okx_testnet.py` | OKX测试网API（真实下单） |
| `okx_optimizer.py` | OKX优化版API |
| `mock_trader.py` | 模拟交易器 |
| `position_monitor.py` | 持仓监控（SL/TP） |
| `result_logger.py` | 交易记录 |

### signals/
| 文件 | 功能 |
|------|------|
| `calculator.py` | 11个信号计算 + 5阶段判定 `judge_manipulation_stage()` |
| `scorer.py` | 评分系统 |
| `state_machine.py` | 状态机 |

---

## 六、配置参数

### config/strategy_params.json
```json
{
  "risk_management": {
    "fixed_position_size": 888,
    "take_profit_pct": 30,
    "stop_loss_pct": 3,
    "max_hold_minutes": 240,
    "max_open_positions": 3,
    "funding_warning_pct": 0.15,
    "funding_reduce_pct": 0.5,
    "funding_exit_pct": 1.0
  },
  "auto_pilot": {
    "use_7conditions": true,
    "use_stage_judgment": true,
    "momentum_mode": false
  }
}
```

---

## 七、完整交易流程（2026-06-14实操）

### Step 1: 市场扫描
```bash
python3 scan.py --quick
```

### Step 2: 筛选测试网可交易代币
```python
from scanner.universe import get_full_universe
from scanner.fast_filter import run_fast_filter

universe = get_full_universe()
candidates = run_fast_filter(universe)

# 测试网可交易的币
testnet_coins = ['BTC', 'ETH', 'SOL', 'DOGE', 'XRP', 'FIL', 'OKB', 'WCT', 'NEAR', 'SUI', 'TIA', 'ZRO', 'MORPHO']
```

### Step 3: 执行OKX测试网下单
```python
from trading.okx_testnet import OKXTestnetTrader

trader = OKXTestnetTrader(api_key, secret, passphrase, testnet=True, use_spot=True)

# 格式: SWAP-{COIN}-USDT
result = trader.place_order('SWAP-ZRO-USDT', 'buy', 939.78, None, 'market')
# result.get('code') == '0' 表示成功
```

### Step 4: 查看持仓
```python
bal = trader.get_balance()
# 查看各币种余额
```

---

## 八、明日复盘要点

1. **查看持仓盈亏**: 各币种涨跌多少？
2. **统计胜率**: 盈利/亏损笔数
3. **5阶段判定**: ZRO是否处于"拉升启动期"？
4. **优化方向**: 是否需要放宽/收紧条件？
5. **资金费率**: 是否有超过0.5%的币需要减仓？

---

## 九、常见问题

### Q: 为什么ZKP不能买？
A: ZKP在OKX测试网没有合约，只能买测试网支持的币（BTC、ETH、SOL、DOGE、XRP、ZRO等）

### Q: 下单失败怎么办？
A: 检查：
1. 币种是否在测试网可交易
2. 数量是否大于最小交易量
3. 账户是否被限制（reduce-only）

### Q: 如何查看当前持仓？
```python
from trading.okx_testnet import OKXTestnetTrader
trader = OKXTestnetTrader(...)
bal = trader.get_balance()
print(bal)
```

---

## 十、相关文档

- `docs/code_review.md` - 代码功能完整梳理
- `docs/final_optimization.md` - 优化记录
- `docs/optimization.md` - 历史优化



🚀 真正下单的代码在哪里
文件	类/函数	用途
trading/okx_testnet.py	OKXTestnetTrader	唯一真实下单入口
→ place_order()	下单方法	✅ 刚才NEAR就是这里下单的
→ get_balance()	查余额	刚才查余额也是这里
其他文件都是调用它：

- auto_pilot.py → 导入 OKXTestnetTrader
- main.py → 导入 OKXTestnetTrader  
- scan.py → 只扫描，不下单
- scripts/nfi_backtest_mock.py → 模拟测试，不下单
✅ 以后找下单代码
trading/okx_testnet.py  →  class OKXTestnetTrader
    ├── place_order()  ←  下单方法（用这个！）
    ├── get_balance()  ←  查余额
    └── get_position() ←  查持仓
记住：下单就找 OKXTestnetTrader.place_order()
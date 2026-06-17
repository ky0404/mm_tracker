# 系统优化建议 - 量化因子重构

> 基于 LIT 上涨案例分析
> 日期: 2026-06-13

---

## 一、问题诊断

### LIT 满足 3/7 条件，但上涨了 5.4%

| 满足的条件 | 实际作用 |
|------------|----------|
| 资金费率转正 ✅ | 多头成本低，可长期持有 |
| DEX买压999:1 ✅ | 散户FOMO，买盘源源不断 |
| 放量历史3x ✅ | 证明有主力关注 |

| 不满足的条件 | 为什么不影响上涨 |
|--------------|------------------|
| 整数横盘 ❌ | $1.5-$2是另一个关键区间 |
| OI吸筹 ❌ | OI稳定但资金持续买入 |
| BTC.D下降 ❌ | BTC.D横盘，山寨轮动 |

### 7条件框架的5大致命问题

1. **过度依赖历史数据** - 放量看的是"某天放量"，不是"正在放量"
2. **整数关口太死板** - 只认$0.05/$0.1/$0.5/$1，忽略$1.5/$2等
3. **费率阈值太高** - 0.5%阈值错过0.1%以下的安全币
4. **缺实时动量** - 没有1H价格走势监测
5. **缺市场情绪** - 没有BTC.D/市场整体监测

---

## 二、量化因子优化方案

### 优化1: 动态整数关口

```python
# 原逻辑: 只认固定关口
if price in [0.05, 0.1, 0.5, 1.0, 5.0]:
    trigger = True

# 优化后: 动态计算关键价位
def get_key_levels(price):
    """动态计算关键价位"""
    levels = []
    # 查找最近的10^n关口
    magnitude = 10 ** int(math.log10(price))
    for multiplier in [0.5, 1, 2, 5]:
        level = magnitude * multiplier
        if level > price * 0.5 and level < price * 2:
            levels.append(level)
    return levels
```

### 优化2: 实时动量因子

```python
# 新增信号: 实时1H动量
def calc_signal_realtime_momentum(kline_1h):
    """实时1H动量"""
    latest = kline_1h.iloc[-1]
    prev = kline_1h.iloc[-3:]  # 最近3小时
    
    # 价格动量
    price_change_1h = (latest['close'] - latest['open']) / latest['open']
    
    # 成交量放大 (当前1H / 过去6H均值)
    current_vol = latest['volume']
    avg_vol_6h = kline_1h.iloc[-7:-1]['volume'].mean()
    volume_ratio = current_vol / avg_vol_6h if avg_vol_6h > 0 else 1
    
    # 综合动量分数
    momentum_score = price_change_1h * 0.6 + (volume_ratio - 1) * 0.4
    
    return {
        'triggered': momentum_score > 0.02,  # 2%以上
        'score': momentum_score,
        'detail': f'1H动量{momentum_score*100:.1f}%,量比{volume_ratio:.1f}x'
    }
```

### 优化3: 资金费率分级

```python
# 原逻辑: 单一阈值
if funding_rate > 0.01:  # 1%
    exit_all()
elif funding_rate > 0.005:  # 0.5%
    reduce_position()

# 优化后: 分级管理
def get_funding_tier(rate):
    """资金费率分级"""
    if rate < 0:
        return 'negative'  # 空头主导，可能反弹
    elif rate < 0.002:  # 0.2%
        return 'safe'     # 安全，可以加仓
    elif rate < 0.005:  # 0.5%
        return 'caution' # 谨慎持有
    elif rate < 0.01:   # 1%
        return 'danger'  # 减仓
    else:
        return 'extreme' # 强制平仓
```

### 优化4: 新增"正在发生"因子

```python
# 新信号: 放量正在进行
def calc_signal_active_volume(kline_1h):
    """实时成交量放大"""
    current_vol = kline_1h.iloc[-1]['volume']
    avg_vol = kline_1h.iloc[-4:]['volume'].mean()  # 过去4小时
    
    if current_vol > avg_vol * 2:
        return {
            'triggered': True,
            'weight': 1.5,
            'detail': f'实时放量{current_vol/avg_vol:.1f}x'
        }
    return {'triggered': False}
```

### 优化5: 市场环境因子

```python
# 新信号: 市场情绪
def calc_signal_market_env():
    """市场环境因子"""
    btc_d = fetch_btc_dominance()
    fear_greed = fetch_fear_greed_index()
    
    # BTC.D下降 = 山寨有机会
    btcd_triggered = btc_d < 55
    
    # 恐惧指数低 = 潜在机会
    fg_triggered = fear_greed < 40
    
    return {
        'triggered': btcd_triggered or fg_triggered,
        'detail': f'BTC.D={btc_d:.1f}%, 恐惧指数={fear_greed}'
    }
```

---

## 三、新11信号体系

| # | 信号ID | 名称 | 权重 | 说明 |
|---|--------|------|------|------|
| 1 | signal_1_key_level | 关键价位突破 | 1.5 | 动态计算，非固定 |
| 2 | signal_2_funding_safe | 资金费率安全 | 1.2 | 分级管理 |
| 3 | signal_3_oi_growth | OI增长 | 1.0 | 7日增长>10% |
| 4 | signal_4_active_volume | 实时放量 | 1.5 | 正在发生 |
| 5 | signal_5_dex_pressure | DEX买压 | 1.0 | 买卖比>1.2 |
| 6 | signal_6_market_env | 市场环境 | 1.0 | BTC.D/恐惧指数 |
| 7 | signal_7_realtime_momentum | 实时动量 | 2.0 | 1H价格+量能 |
| 8 | signal_8_wash_test | 洗盘测试 | 1.0 | 假突破后回升 |
| 9 | signal_9_social | 社媒热度 | 0.5 | (占位) |
| 10| signal_10_breakout | 突破确认 | 1.5 | 收盘价突破 |
| 11| signal_11_early_warning | 多信号组合 | 1.0 | 综合预警 |

---

## 四、入场条件优化

### 原条件 (太严格)
```
满足 4/7 条件 → 可以买入
满足 5/7 条件 → 强烈买入
```

### 优化后 (动态权重)
```
总分数 >= 4 分 → 可以买入
且 满足以下任一条件:
  - 实时动量信号触发
  - 资金费率 < 0.1%
  - DEX买压 > 5
```

---

## 五、风控参数优化

| 参数 | 原值 | 优化值 | 说明 |
|------|------|--------|------|
| 资金费率离场 | >1% | >1% | 保持 |
| 资金费率减仓 | >0.5% | >0.3% | 降低阈值 |
| 资金费率预警 | - | >0.15% | 新增预警 |
| 持仓超时 | 4小时 | 8小时 | 延长 |
| 止盈 | 30% | 50% | 提高 |

---

## 六、实施优先级

| 优先级 | 优化项 | 难度 | 收益 |
|--------|--------|------|------|
| P0 | 实时动量因子 | 中 | 高 |
| P0 | 资金费率分级 | 低 | 高 |
| P1 | 动态关键价位 | 中 | 中 |
| P1 | 市场环境因子 | 中 | 中 |
| P2 | 放量正在发生 | 低 | 中 |

---

*优化建议完成*
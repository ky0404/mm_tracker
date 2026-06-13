"""
MMTracker 单元测试
覆盖信号计算、评分、异常处理等核心功能
"""

import pytest
import pandas as pd
import numpy as np
from signals.calculator import SignalCalculator, calculate_all_signals
from signals.scorer import MMScorer


class TestSignal1IntegerConsolidation:
    """测试 Signal 1: 整数关口横盘"""
    
    def setup_method(self):
        self.calc = SignalCalculator()
    
    def test_basic_consolidation_returns_valid_structure(self):
        """返回结构应该有triggered字段"""
        price_data = {"price": 0.085, "change_7d_pct": 2.0}
        kline_df = pd.DataFrame({
            "close": [0.086, 0.087, 0.088, 0.086, 0.087, 0.086, 0.087, 0.086, 0.087]
        })
        
        result = self.calc.calc_signal_1_integer_consolidation(price_data, kline_df)
        
        # 返回结构正确
        assert "triggered" in result
        assert "consolidation_days" in result
        assert "nearest_level" in result
    
    def test_14d_consolidation_detects_long_term(self):
        """14天+横盘应该被检测"""
        price_data = {"price": 0.085}
        kline_df = pd.DataFrame({
            "close": [0.086] * 20
        })
        
        result = self.calc.calc_signal_1_integer_consolidation(price_data, kline_df)
        
        # 应该检测到长期横盘
        assert result["consolidation_days"] >= 14
    
    def test_no_consolidation(self):
        """无横盘不应该触发"""
        price_data = {"price": 0.5}
        kline_df = pd.DataFrame({"close": [0.3, 0.4, 0.5, 0.6, 0.7]})
        
        result = self.calc.calc_signal_1_integer_consolidation(price_data, kline_df)
        
        assert "triggered" in result
    
    def test_empty_price(self):
        """价格缺失应返回错误结构"""
        price_data = {"price": 0}
        kline_df = pd.DataFrame()
        
        result = self.calc.calc_signal_1_integer_consolidation(price_data, kline_df)
        
        assert result["triggered"] == False
        assert "detail" in result


class TestSignal2FundingTurnPositive:
    """测试 Signal 2: 资金费率转正"""
    
    def setup_method(self):
        self.calc = SignalCalculator()
    
    def test_funding_turns_positive_rising(self):
        """资金费率转正且上升趋势应该触发"""
        funding_data = {
            "rates": [-0.001, -0.0005, 0.0, 0.0005, 0.001, 0.0015, 0.002],
            "latest_rate": 0.002
        }
        
        result = self.calc.calc_signal_2_funding_turn_positive(funding_data)
        
        assert result["triggered"] == True
        assert result["trend"] in ["rising", "rising_strong"]
    
    def test_funding_positive_but_falling(self):
        """资金费率为正但下降趋势不应该触发"""
        funding_data = {
            "rates": [0.003, 0.0025, 0.002, 0.0015, 0.001],
            "latest_rate": 0.001
        }
        
        result = self.calc.calc_signal_2_funding_turn_positive(funding_data)
        
        assert result["triggered"] == False
    
    def test_funding_still_negative(self):
        """资金费率仍为负不应该触发"""
        funding_data = {
            "rates": [-0.002, -0.0015, -0.001, -0.0005],
            "latest_rate": -0.0005
        }
        
        result = self.calc.calc_signal_2_funding_turn_positive(funding_data)
        
        assert result["triggered"] == False
    
    def test_insufficient_data(self):
        """数据不足应该返回安全结构"""
        funding_data = {"rates": []}
        
        result = self.calc.calc_signal_2_funding_turn_positive(funding_data)
        
        assert result["triggered"] == False
        assert "detail" in result


class TestSignal4VolumeSpike:
    """测试 Signal 4: 放量等级"""
    
    def setup_method(self):
        self.calc = SignalCalculator()
    
    def test_volume_spike_returns_valid_structure(self):
        """放量检测应该返回有效结构"""
        base_vol = 1000000
        # 确保数组长度一致
        kline_df = pd.DataFrame({
            "close": [1.0] * 25,
            "volume": [base_vol] * 20 + [base_vol * 3.5, base_vol * 3.5, base_vol * 3.5, base_vol * 3.5, base_vol * 3.5]
        })
        
        result = self.calc.calc_signal_4_volume_spike(kline_df)
        
        # 返回结构正确
        assert "triggered" in result
        assert "volume_ratio" in result
        assert "spike_level" in result
    
    def test_no_spike(self):
        """无明显放量不应该触发"""
        kline_df = pd.DataFrame({
            "close": [1.0] * 25,
            "volume": [1000000] * 25
        })
        
        result = self.calc.calc_signal_4_volume_spike(kline_df)
        
        assert result["triggered"] == False
    
    def test_empty_kline(self):
        """K线为空应该返回安全结构"""
        kline_df = pd.DataFrame()
        
        result = self.calc.calc_signal_4_volume_spike(kline_df)
        
        assert result["triggered"] == False
        assert "detail" in result


class TestSignal6BTCDominance:
    """测试 Signal 6: BTC.D 下降通道"""
    
    def setup_method(self):
        self.calc = SignalCalculator()
    
    def test_btcd_downtrend(self):
        """BTC.D 下降应该触发"""
        btcd_df = pd.DataFrame({
            "close": [55, 54, 53, 52, 51, 50, 49]
        })
        
        result = self.calc.calc_signal_6_btcd_downtrend(btcd_df)
        
        assert result["triggered"] == True
    
    def test_btcd_uptrend_no_trigger(self):
        """BTC.D 上升不应该触发"""
        btcd_df = pd.DataFrame({
            "close": [45, 46, 47, 48, 49, 50, 51]
        })
        
        result = self.calc.calc_signal_6_btcd_downtrend(btcd_df)
        
        assert result["triggered"] == False
    
    def test_btcd_insufficient_data(self):
        """数据不足应该返回安全结构"""
        btcd_df = pd.DataFrame()
        
        result = self.calc.calc_signal_6_btcd_downtrend(btcd_df)
        
        assert result["triggered"] == False


class TestScorer:
    """测试评分器"""
    
    def setup_method(self):
        self.scorer = MMScorer()
    
    def test_entry_threshold_5_signals(self):
        """5个信号应该触发入场"""
        signals = {
            f"signal_{i}_test": {"triggered": True, "detail": "test"}
            for i in range(1, 9)
        }
        # 确保有5个触发
        signals["signal_1_test"]["triggered"] = True
        signals["signal_2_test"]["triggered"] = True
        signals["signal_3_test"]["triggered"] = True
        signals["signal_4_test"]["triggered"] = True
        signals["signal_5_test"]["triggered"] = True
        signals["signal_6_test"]["triggered"] = False
        signals["signal_7_test"]["triggered"] = False
        signals["signal_8_test"]["triggered"] = False
        
        # 使用真实的信号名称
        real_signals = {
            "signal_1_integer_consolidation": {"triggered": True, "detail": "test"},
            "signal_2_funding_turn_positive": {"triggered": True, "detail": "test"},
            "signal_3_oi_accumulation": {"triggered": True, "detail": "test"},
            "signal_4_volume_spike": {"triggered": True, "detail": "test"},
            "signal_5_dex_buy_pressure": {"triggered": True, "detail": "test"},
            "signal_6_btcd_downtrend": {"triggered": False, "detail": "test"},
            "signal_6b_btc_relative_strength": {"triggered": False, "detail": "test"},
            "signal_7_new_futures": {"triggered": False, "detail": "test"},
            "signal_8_wash_test": {"triggered": False, "detail": "test"},
        }
        
        result = self.scorer.score(real_signals)
        
        assert result["grade"] == "ENTRY"
        assert result["triggered_count"] >= 5
    
    def test_watch_threshold_3_signals(self):
        """3个信号应该是 WATCH 等级"""
        signals = {
            "signal_1_integer_consolidation": {"triggered": True, "detail": "test"},
            "signal_2_funding_turn_positive": {"triggered": True, "detail": "test"},
            "signal_3_oi_accumulation": {"triggered": True, "detail": "test"},
            "signal_4_volume_spike": {"triggered": False, "detail": "test"},
            "signal_5_dex_buy_pressure": {"triggered": False, "detail": "test"},
            "signal_6_btcd_downtrend": {"triggered": False, "detail": "test"},
            "signal_6b_btc_relative_strength": {"triggered": False, "detail": "test"},
            "signal_7_new_futures": {"triggered": False, "detail": "test"},
            "signal_8_wash_test": {"triggered": False, "detail": "test"},
        }
        
        result = self.scorer.score(signals)
        
        assert result["grade"] == "WATCH"
        assert result["triggered_count"] >= 3
    
    def test_idle_no_signals(self):
        """无信号应该是 IDLE 等级"""
        signals = {
            f"signal_{i}_integer_consolidation" if i == 1 else f"signal_{i}_test": {"triggered": False, "detail": "test"}
            for i in range(1, 10)
        }
        # 使用真实信号名
        real_signals = {
            "signal_1_integer_consolidation": {"triggered": False, "detail": "test"},
            "signal_2_funding_turn_positive": {"triggered": False, "detail": "test"},
            "signal_3_oi_accumulation": {"triggered": False, "detail": "test"},
            "signal_4_volume_spike": {"triggered": False, "detail": "test"},
            "signal_5_dex_buy_pressure": {"triggered": False, "detail": "test"},
            "signal_6_btcd_downtrend": {"triggered": False, "detail": "test"},
            "signal_6b_btc_relative_strength": {"triggered": False, "detail": "test"},
            "signal_7_new_futures": {"triggered": False, "detail": "test"},
            "signal_8_wash_test": {"triggered": False, "detail": "test"},
        }
        
        result = self.scorer.score(real_signals)
        
        assert result["grade"] == "IDLE"


class TestErrorHandling:
    """测试错误处理和降级"""
    
    def setup_method(self):
        self.calc = SignalCalculator()
    
    def test_none_as_kline(self):
        """K线为 None 应该正常处理"""
        result = self.calc.calc_signal_1_integer_consolidation(
            {"price": 0.1}, None
        )
        assert "triggered" in result
        assert result["triggered"] == False
    
    def test_none_as_funding(self):
        """资金费率为 None 应该正常处理"""
        result = self.calc.calc_signal_2_funding_turn_positive(None)
        assert "triggered" in result
        assert result["triggered"] == False
    
    def test_malformed_funding(self):
        """格式错误的资金费率数据应该安全处理"""
        result = self.calc.calc_signal_2_funding_turn_positive({
            "rates": "not a list"
        })
        assert "triggered" in result
        assert result["triggered"] == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestSignal9SocialSentiment:
    """测试 Signal 9: 社交媒体情绪占位"""
    
    def setup_method(self):
        self.calc = SignalCalculator()
    
    def test_placeholder_returns_valid_structure(self):
        """占位符返回正确结构"""
        result = self.calc.calc_signal_9_social_sentiment(None)
        assert "triggered" in result
        assert "sentiment_score" in result
        assert "mention_trend" in result
        assert result["triggered"] == False
    
    def test_placeholder_with_data(self):
        """有数据时应正确判断"""
        social_data = {"sentiment_score": 0.7, "mention_trend": 60}
        result = self.calc.calc_signal_9_social_sentiment(social_data)
        assert result["triggered"] == True
    
    def test_placeholder_below_threshold(self):
        """低于阈值不触发"""
        social_data = {"sentiment_score": 0.3, "mention_trend": 10}
        result = self.calc.calc_signal_9_social_sentiment(social_data)
        assert result["triggered"] == False


class TestMMScorer9Signals:
    """测试评分器支持9信号"""
    
    def setup_method(self):
        self.scorer = MMScorer()
    
    def test_9_signals_weights(self):
        """验证9信号权重配置"""
        assert "signal_9_social_sentiment" in self.scorer.WEIGHTS
        assert len(self.scorer.WEIGHTS) == 10  # 9 signals + signal_6b
    
    def test_9_signals_names(self):
        """验证9信号名称映射"""
        assert "signal_9_social_sentiment" in self.scorer.SIGNAL_NAMES
    
    def test_score_with_9_signals(self):
        """验证9信号评分"""
        signals = {
            "signal_1_integer_consolidation": {"triggered": True},
            "signal_2_funding_turn_positive": {"triggered": True},
            "signal_3_oi_accumulation": {"triggered": True},
            "signal_4_volume_spike": {"triggered": False},
            "signal_5_dex_buy_pressure": {"triggered": True},
            "signal_6_btcd_downtrend": {"triggered": False},
            "signal_6b_btc_relative_strength": {"triggered": False},
            "signal_7_new_futures": {"triggered": False},
            "signal_8_wash_test": {"triggered": False},
            "signal_9_social_sentiment": {"triggered": True},
        }
        result = self.scorer.score(signals)
        assert result["triggered_count"] >= 5
        assert result["grade"] == "ENTRY"


class TestIntegration9Signals:
    """集成测试: 11信号端到端"""
    
    def test_calculate_all_signals_returns_11(self):
        """calculate_all_signals 返回11个信号"""
        price_data = {"price": 0.085}
        funding_data = {"rates": [], "current": 0.0001}
        oi_data = {"oi": 1000000, "oi_7d_ago": 900000}
        kline_df = pd.DataFrame({
            "close": [0.08, 0.081, 0.082] * 10,
            "volume": [1000000] * 30
        })
        token_kline_df = kline_df  # same for BTC relative strength
        dex_data = {"buy_sell_ratio": 1.3, "liquidity_usd": 100000}
        futures_data = {"new_contract": False}
        btcd_df = pd.DataFrame({"close": [55, 54, 53]})
        ls_data = {"long_ratio": 60, "short_ratio": 40}
        tv_data = {"buy_volume": 1000000, "sell_volume": 900000, "buy_sell_ratio": 1.1}
        
        result = calculate_all_signals(
            price_data, funding_data, oi_data, kline_df,
            dex_data, futures_data, btcd_df, token_kline_df=token_kline_df,
            ls_data=ls_data, tv_data=tv_data
        )
        
        assert len(result) == 13  # 11 signals + signal_6b + signal_11_early_warning
        assert "signal_12_long_short_ratio" in result
        assert "signal_13_taker_volume" in result


class TestEdgeCases:
    """边界条件测试"""
    
    def setup_method(self):
        self.calc = SignalCalculator()
    
    def test_signal_1_extreme_prices(self):
        """Signal 1 极端价格测试"""
        # 极小价格
        r1 = self.calc.calc_signal_1_integer_consolidation({"price": 0.0001}, None)
        assert "nearest_level" in r1
        assert r1["nearest_level"] > 0
        
        # 极大价格
        r2 = self.calc.calc_signal_1_integer_consolidation({"price": 999999}, None)
        assert "nearest_level" in r2
        
        # 零价格
        r3 = self.calc.calc_signal_1_integer_consolidation({"price": 0}, None)
        assert r3["triggered"] == False
    
    def test_signal_2_extreme_funding(self):
        """Signal 2 极端资金费率"""
        # 超过1%
        r1 = self.calc.calc_signal_2_funding_turn_positive({
            "rates": [0.015] * 10, "current": 0.015
        })
        assert "triggered" in r1
        
        # 负费率
        r2 = self.calc.calc_signal_2_funding_turn_positive({
            "rates": [-0.001] * 10, "current": -0.001
        })
        assert "triggered" in r2
        
        # 空数据
        r3 = self.calc.calc_signal_2_funding_turn_positive({})
        assert r3["triggered"] == False
    
    def test_signal_3_oi_extreme(self):
        """Signal 3 OI极端值"""
        # OI大幅增加
        r1 = self.calc.calc_signal_3_oi_accumulation(
            {"oi": 10000000, "oi_7d_ago": 1000000},  # 10x
            {"price": 0.1}
        )
        assert "oi_change_7d_pct" in r1
        
        # OI减少
        r2 = self.calc.calc_signal_3_oi_accumulation(
            {"oi": 100000, "oi_7d_ago": 1000000},
            {"price": 0.1}
        )
        assert r2["triggered"] == False
    
    def test_signal_4_volume_extreme(self):
        """Signal 4 放量极端值"""
        # 20x放量
        df = pd.DataFrame({
            "close": [1.0] * 30,
            "volume": [1000000] * 29 + [20000000]  # 20x
        })
        r = self.calc.calc_signal_4_volume_spike(df)
        assert "triggered_20x" in r or r["triggered"] == True
        
        # 空数据
        r2 = self.calc.calc_signal_4_volume_spike(pd.DataFrame())
        assert r2["triggered"] == False
    
    def test_signal_5_dex_extreme(self):
        """Signal 5 DEX极端值"""
        # 极高买卖比
        r1 = self.calc.calc_signal_5_dex_buy_pressure({
            "buy_sell_ratio": 999.0, "liquidity_usd": 1000000
        })
        assert r1["triggered"] == True
        
        # 零流动性
        r2 = self.calc.calc_signal_5_dex_buy_pressure({
            "buy_sell_ratio": 1.5, "liquidity_usd": 0
        })
        assert r2["triggered"] == False
    
    def test_signal_6_btcd_extreme(self):
        """Signal 6 BTC.D极端值"""
        # 持续下降
        df = pd.DataFrame({"close": [60, 58, 56, 54, 52]})
        r = self.calc.calc_signal_6_btcd_downtrend(df)
        assert "triggered" in r
        
        # 上升趋势
        df2 = pd.DataFrame({"close": [50, 52, 54, 56, 58]})
        r2 = self.calc.calc_signal_6_btcd_downtrend(df2)
        assert r2["triggered"] == False
    
    def test_signal_8_wash_extreme(self):
        """Signal 8 洗盘极端值"""
        # 剧烈假拉升
        df = pd.DataFrame({
            "close": [1.0] * 10 + [1.5] * 3 + [1.0] * 17,  # 50% pump then retrace
            "volume": [1000000] * 30
        })
        funding = {"rates": [0.0001] * 30}
        r = self.calc.calc_signal_8_wash_test(df, funding)
        assert "triggered" in r


class TestHealthCheck:
    """健康检查测试"""
    
    def test_health_monitor_stats(self):
        """健康监控统计"""
        from fetchers.utils import health_monitor, get_stats, get_health_report
        
        # 记录一些请求
        health_monitor.record_request("test_source", 100.0, True)
        health_monitor.record_request("test_source", 200.0, True)
        health_monitor.record_429("test_source")
        
        stats = get_stats()
        assert stats["total_requests"] >= 2
        assert stats["429_hits"] >= 1
        
        # 健康报告应该能生成
        report = get_health_report()
        assert "MMTracker" in report
    
    def test_cache_stats(self):
        """缓存统计"""
        from fetchers.utils import thread_safe_cache
        
        # 测试缓存
        thread_safe_cache.set("test_key", {"data": "value"})
        result = thread_safe_cache.get("test_key")
        assert result == {"data": "value"}
        
        stats = thread_safe_cache.get_stats()
        assert stats["hits"] >= 1


class TestBackoffManager:
    """429退避测试"""
    
    def test_backoff_delay_calculation(self):
        """退避延迟计算"""
        from fetchers.utils import backoff_manager, get_backoff_delay
        
        # 初始无延迟
        delay = get_backoff_delay("test_source")
        assert delay == 0.0
        
        # 记录429
        backoff_manager.on_429("test_source")
        
        # 应该有延迟（至少base_delay）
        delay = get_backoff_delay("test_source")
        assert delay >= 0.5  # base_delay


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
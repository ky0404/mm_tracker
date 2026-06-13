"""
MMTracker 工具模块
包含速率限制、缓存、请求装饰器、429退避、健康检查等
"""

import time
import functools
import os
import json
import hashlib
import random
import threading
from typing import Any, Callable, Optional, Dict
from pathlib import Path
from dataclasses import dataclass, field


# ============================================================================
# 429 限流退避策略
# ============================================================================

@dataclass
class RateLimitState:
    """429 限流状态追踪"""
    last_429_time: float = 0.0
    consecutive_429s: int = 0
    cooldown_until: float = 0.0


class BackoffManager:
    """指数退避 + 随机抖动管理器"""
    
    def __init__(self):
        self.source_states: Dict[str, RateLimitState] = {}
        self._lock = threading.Lock()
        
        # 退避配置
        self.base_delay = 0.5  # 初始延迟 0.5s
        self.max_delay = 30.0  # 最大延迟 30s
        self.multiplier = 2.0  # 指数倍率
        self.jitter_pct = 0.3  # 抖动 30%
        self.cooldown_seconds = 60  # 源级冷却 60s
    
    def get_delay(self, source: str) -> float:
        """计算退避延迟"""
        with self._lock:
            state = self.source_states.get(source)
            if not state:
                return 0.0
            
            # 如果在冷却期内
            now = time.time()
            if now < state.cooldown_until:
                remaining = state.cooldown_until - now
                return max(0, remaining)
            
            # 指数退避 + 抖动
            delay = self.base_delay * (self.multiplier ** state.consecutive_429s)
            delay = min(delay, self.max_delay)
            
            # 添加随机抖动
            jitter = delay * self.jitter_pct * (random.random() * 2 - 1)
            delay = delay + jitter
            
            return max(0, delay)
    
    def on_429(self, source: str):
        """记录 429 错误"""
        with self._lock:
            if source not in self.source_states:
                self.source_states[source] = RateLimitState()
            
            state = self.source_states[source]
            state.consecutive_429s += 1
            state.last_429_time = time.time()
            state.cooldown_until = time.time() + self.cooldown_seconds
    
    def on_success(self, source: str):
        """记录成功，重置计数"""
        with self._lock:
            if source in self.source_states:
                self.source_states[source].consecutive_429s = 0


# 全局退避管理器
backoff_manager = BackoffManager()


def get_backoff_delay(source: str) -> float:
    """获取指定源的退避延迟"""
    return backoff_manager.get_delay(source)


def record_429(source: str):
    """记录429错误"""
    backoff_manager.on_429(source)
    print(f"[429] {source} 触发限流，已记录退避")


def record_success(source: str):
    """记录成功"""
    backoff_manager.on_success(source)


# ============================================================================
# 线程安全缓存
# ============================================================================

class ThreadSafeCache:
    """线程安全内存缓存"""
    
    def __init__(self, ttl_seconds: int = 300):
        self._cache: Dict[str, tuple] = {}  # key -> (value, timestamp)
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
        
        # 统计
        self._hits = 0
        self._misses = 0
    
    def get(self, key: str) -> Optional[Any]:
        """获取缓存"""
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            value, timestamp = self._cache[key]
            if time.time() - timestamp > self._ttl:
                del self._cache[key]
                self._misses += 1
                return None
            
            self._hits += 1
            return value
    
    def set(self, key: str, value: Any):
        """设置缓存"""
        with self._lock:
            self._cache[key] = (value, time.time())
    
    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()
    
    def get_stats(self) -> dict:
        """获取缓存统计"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0
            return {
                "hits": self._hits,
                "misses": self._misses,
                "total": total,
                "hit_rate": hit_rate,
                "size": len(self._cache)
            }


# 全局线程安全缓存
thread_safe_cache = ThreadSafeCache(ttl_seconds=300)


# ============================================================================
# 速率限制器
# ============================================================================

class RateLimiter:
    """简单速率限制器"""
    
    def __init__(self, calls_per_second: float = 5.0):
        self.calls_per_second = calls_per_second
        self.min_interval = 1.0 / calls_per_second
        self._lock = threading.Lock()
        self._last_call = 0.0
    
    def wait(self):
        """等待直到可以发起下一次调用"""
        with self._lock:
            elapsed = time.time() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.time()


# 全局速率限制器
global_rate_limiter = RateLimiter(calls_per_second=5.0)


def api_call_with_rate_limit(func: Callable) -> Callable:
    """API调用速率限制装饰器"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global_rate_limiter.wait()
        return func(*args, **kwargs)
    return wrapper


# ============================================================================
# 健康检查与统计
# ============================================================================

class HealthMonitor:
    """健康监控"""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._stats = {
            "total_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "429_hits": 0,
            "errors": 0,
            "latencies": [],
        }
        self._source_stats: Dict[str, dict] = {}
    
    def record_request(self, source: str, latency_ms: float, success: bool = True):
        """记录请求"""
        with self._lock:
            self._stats["total_requests"] += 1
            self._stats["latencies"].append(latency_ms)
            
            # 保持最近1000个延迟数据
            if len(self._stats["latencies"]) > 1000:
                self._stats["latencies"] = self._stats["latencies"][-1000:]
            
            if not success:
                self._stats["errors"] += 1
            
            # 源级统计
            if source not in self._source_stats:
                self._source_stats[source] = {
                    "requests": 0, "errors": 0, "429s": 0, "last_success": 0, "last_error": 0
                }
            self._source_stats[source]["requests"] += 1
            if success:
                self._source_stats[source]["last_success"] = time.time()
            else:
                self._source_stats[source]["last_error"] = time.time()
                self._source_stats[source]["errors"] += 1
    
    def record_429(self, source: str):
        """记录429"""
        with self._lock:
            self._stats["429_hits"] += 1
            if source in self._source_stats:
                self._source_stats[source]["429s"] += 1
    
    def record_cache_hit(self):
        """记录缓存命中"""
        with self._lock:
            self._stats["cache_hits"] += 1
    
    def record_cache_miss(self):
        """记录缓存未命中"""
        with self._lock:
            self._stats["cache_misses"] += 1
    
    def get_stats(self) -> dict:
        """获取统计"""
        with self._lock:
            latencies = self._stats["latencies"]
            return {
                "total_requests": self._stats["total_requests"],
                "cache_hits": self._stats["cache_hits"],
                "cache_misses": self._stats["cache_misses"],
                "429_hits": self._stats["429_hits"],
                "errors": self._stats["errors"],
                "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
                "max_latency_ms": max(latencies) if latencies else 0,
                "sources": dict(self._source_stats),
            }
    
    def get_health_report(self) -> str:
        """生成健康报告"""
        stats = self.get_stats()
        
        lines = [
            "=" * 50,
            "📊 MMTracker 健康检查报告",
            "=" * 50,
            f"总请求数: {stats['total_requests']}",
            f"缓存命中: {stats['cache_hits']} ({stats['cache_hits']/(stats['cache_hits']+stats['cache_misses'])*100:.1f}%)" if stats['cache_hits'] + stats['cache_misses'] > 0 else "缓存命中: 0",
            f"429限流: {stats['429_hits']}",
            f"错误数: {stats['errors']}",
            f"平均延迟: {stats['avg_latency_ms']:.1f}ms",
            f"最大延迟: {stats['max_latency_ms']:.1f}ms",
            "",
            "数据源状态:",
        ]
        
        for source, s in stats.get("sources", {}).items():
            status = "✅" if s["errors"] == 0 else "⚠️"
            lines.append(f"  {status} {source}: {s['requests']} 请求, {s['errors']} 错误, {s['429s']} 429")
        
        return "\n".join(lines)


# 全局健康监控
health_monitor = HealthMonitor()


def get_health_report() -> str:
    """获取健康报告"""
    return health_monitor.get_health_report()


def get_stats() -> dict:
    """获取统计"""
    return health_monitor.get_stats()


# ============================================================================
# 导出
# ============================================================================

__all__ = [
    "BackoffManager",
    "backoff_manager",
    "get_backoff_delay",
    "record_429",
    "record_success",
    "ThreadSafeCache",
    "thread_safe_cache",
    "RateLimiter",
    "global_rate_limiter",
    "api_call_with_rate_limit",
    "HealthMonitor",
    "health_monitor",
    "get_health_report",
    "get_stats",
]
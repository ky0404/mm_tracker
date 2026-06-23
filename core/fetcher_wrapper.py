"""
MMTracker Fetcher 缓存包装器
在入口层自动缓存所有fetcher结果，原有代码无需改动

工作原理:
- 拦截所有 fetcher 函数调用
- 先查缓存，缓存命中则返回
- 未命中则调用原始函数，结果写入缓存
- 自动处理 TTL 过期

用法:
    from core.fetcher_wrapper import wrap_fetcher, wrap_module
    
    # 方式1: 包装单个函数
    cached_fetch_price = wrap_fetcher(
        fetch_price_and_change,
        key_prefix="price",
        ttl=60
    )
    
    # 方式2: 批量包装模块（推荐）
    wrap_module("fetchers.price_api", {
        "fetch_price_and_change": {"ttl": 60},
        "fetch_funding_rate_history": {"ttl": 300},
        "fetch_oi_history": {"ttl": 300},
    })
"""

import functools
import time
import hashlib
import importlib
from typing import Callable, Any, Optional, Dict
from core.cache import get_cache


class FetcherWrapper:
    """Fetcher函数包装器"""
    
    def __init__(self):
        self._cache = get_cache()
        self._wrapped: Dict[Callable, Callable] = {}
    
    def wrap(
        self, 
        func: Callable, 
        key_prefix: str = None, 
        ttl: int = 60,
        arg_to_key: Callable = None
    ) -> Callable:
        """
        包装fetcher函数
        
        Args:
            func: 原始fetcher函数
            key_prefix: 缓存键前缀，如 "price", "funding"
            ttl: 缓存时间（秒）
            arg_to_key: 自定义参数转缓存键的函数
        """
        if func in self._wrapped:
            return self._wrapped[func]
        
        prefix = key_prefix or func.__name__
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            if arg_to_key:
                cache_key = arg_to_key(*args, **kwargs)
            else:
                # 默认：用函数名+第一个参数作为键
                symbol = args[0] if args else kwargs.get('symbol', '')
                if isinstance(symbol, str):
                    symbol = symbol.upper()
                cache_key = f"{prefix}:{symbol}"
            
            # 尝试从缓存获取
            cached = self._cache.get(cache_key, max_age=ttl)
            if cached is not None:
                return cached
            
            # 缓存未命中，调用原始函数
            result = func(*args, **kwargs)
            
            # 写入缓存（只缓存有效结果）
            if result is not None and not (isinstance(result, dict) and result.get('error')):
                self._cache.set(cache_key, result, ttl=ttl)
            
            return result
        
        self._wrapped[func] = wrapper
        return wrapper
    
    def wrap_many(self, module_name: str, functions: Dict[str, Dict]):
        """
        批量包装模块中的函数
        
        Args:
            module_name: 模块名，如 "fetchers.price_api"
            functions: 函数配置 {"func_name": {"ttl": 60, "prefix": "xxx"}}
        """
        try:
            module = importlib.import_module(module_name)
        except ImportError as e:
            print(f"[FetcherWrapper] 导入模块失败 {module_name}: {e}")
            return
        
        wrapped = {}
        for func_name, config in functions.items():
            if hasattr(module, func_name):
                func = getattr(module, func_name)
                wrapped[func_name] = self.wrap(
                    func,
                    key_prefix=config.get("prefix", func_name),
                    ttl=config.get("ttl", 60)
                )
                print(f"[FetcherWrapper] 已包装: {module_name}.{func_name} (TTL={config.get('ttl', 60)}s)")
        
        return wrapped


# 全局包装器实例
_wrapper = FetcherWrapper()


def wrap_fetcher(func: Callable, key_prefix: str = None, ttl: int = 60) -> Callable:
    """包装单个fetcher函数"""
    return _wrapper.wrap(func, key_prefix, ttl)


def wrap_module(module_name: str, functions: Dict[str, Dict]) -> Dict[str, Callable]:
    """批量包装模块"""
    return _wrapper.wrap_many(module_name, functions)


# 常用配置
DEFAULT_FETCHER_CONFIG = {
    "fetch_price_and_change": {"ttl": 60, "prefix": "price"},
    "fetch_funding_rate_history": {"ttl": 300, "prefix": "funding"},
    "fetch_oi_history": {"ttl": 300, "prefix": "oi"},
    "fetch_daily_ohlcv": {"ttl": 300, "prefix": "kline"},
    "fetch_dex_data": {"ttl": 300, "prefix": "dex"},
    "get_okx_price": {"ttl": 60, "prefix": "price"},
    "get_okx_candles": {"ttl": 60, "prefix": "candle"},
    "fetch_btcd": {"ttl": 300, "prefix": "btcd"},
}


def init_fetcher_cache():
    """初始化所有fetcher缓存（推荐在auto_pilot启动时调用）"""
    print("[FetcherWrapper] 初始化fetcher缓存...")
    
    wrap_module("fetchers.price_api", {
        "fetch_price_and_change": {"ttl": 60, "prefix": "price"},
        "fetch_funding_rate_history": {"ttl": 300, "prefix": "funding"},
        "fetch_oi_history": {"ttl": 300, "prefix": "oi"},
        "fetch_daily_ohlcv": {"ttl": 300, "prefix": "kline"},
        "fetch_dex_data": {"ttl": 300, "prefix": "dex"},
    })
    
    wrap_module("fetchers.momentum", {
        "get_okx_price": {"ttl": 60, "prefix": "price"},
        "get_okx_candles": {"ttl": 60, "prefix": "candle"},
    })
    
    wrap_module("fetchers.multi_tf", {
        "fetch_okx_candles": {"ttl": 60, "prefix": "candle"},
    })
    
    print("[FetcherWrapper] 初始化完成")


def get_cache_stats() -> Dict[str, Any]:
    """获取缓存统计"""
    return get_cache().get_stats()
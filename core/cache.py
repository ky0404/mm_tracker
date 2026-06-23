"""
MMTracker 统一数据缓存层
解决多fetcher缓存不共享、重启丢失问题

设计原则：
- 单例模式，进程级共享
- SQLite持久化，重启后自动恢复
- TTL自动过期，支持手动清理
- 线程安全

用法:
    from core.cache import DataCacheHub
    
    cache = DataCacheHub()
    
    # 写入缓存
    cache.set("price:DOGE", {"price": 0.08, "change": 5.2}, ttl=60)
    
    # 读取缓存
    data = cache.get("price:DOGE", max_age=60)
    
    # 批量写入
    cache.set_many({
        "price:DOGE": {"price": 0.08},
        "price:BTC": {"price": 65000},
    }, ttl=60)
"""

import json
import time
import sqlite3
import threading
from typing import Any, Optional, Dict
from pathlib import Path
from dataclasses import dataclass


@dataclass
class CacheEntry:
    key: str
    value: Any
    created_at: float
    ttl: int  # 秒，0表示永不过期


class DataCacheHub:
    """
    统一数据缓存中心
    支持多数据类型、TTL过期、持久化
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        
        self._memory_cache: Dict[str, CacheEntry] = {}
        self._cache_lock = threading.RLock()
        
        # 初始化SQLite持久化
        self._init_db()
        
        # 从DB恢复缓存
        self._restore_from_db()
        
        print(f"[DataCacheHub] 初始化完成，内存缓存: {len(self._memory_cache)} 条")
    
    def _init_db(self):
        """初始化SQLite数据库"""
        base_dir = Path(__file__).parent.parent
        self._db_path = base_dir / "trading" / "cache.db"
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        
        self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS data_cache (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                created_at REAL NOT NULL,
                ttl INTEGER NOT NULL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_created ON data_cache(created_at)")
        self._db.commit()
    
    def _restore_from_db(self):
        """从SQLite恢复缓存"""
        try:
            cursor = self._db.execute(
                "SELECT key, value, created_at, ttl FROM data_cache"
            )
            now = time.time()
            restored = 0
            
            for row in cursor:
                key, value_str, created_at, ttl = row
                # 检查是否过期
                if ttl > 0 and (now - created_at) > ttl:
                    continue  # 跳过过期数据
                
                try:
                    value = json.loads(value_str)
                    self._memory_cache[key] = CacheEntry(
                        key=key,
                        value=value,
                        created_at=created_at,
                        ttl=ttl
                    )
                    restored += 1
                except:
                    pass
            
            if restored > 0:
                print(f"[DataCacheHub] 从DB恢复 {restored} 条缓存")
        except Exception as e:
            print(f"[DataCacheHub] DB恢复失败: {e}")
    
    def set(self, key: str, value: Any, ttl: int = 60):
        """
        设置缓存
        
        Args:
            key: 缓存键，格式建议 "type:symbol" 如 "price:DOGE"
            value: 缓存值（可序列化对象）
            ttl: 过期秒数，默认60秒，0表示永不过期
        """
        with self._cache_lock:
            now = time.time()
            
            # 内存缓存
            entry = CacheEntry(
                key=key,
                value=value,
                created_at=now,
                ttl=ttl
            )
            self._memory_cache[key] = entry
            
            # 持久化到SQLite
            try:
                value_str = json.dumps(value, ensure_ascii=False)
                self._db.execute(
                    "INSERT OR REPLACE INTO data_cache (key, value, created_at, ttl) VALUES (?, ?, ?, ?)",
                    (key, value_str, now, ttl)
                )
                self._db.commit()
            except Exception as e:
                print(f"[DataCacheHub] DB写入失败: {e}")
    
    def get(self, key: str, max_age: float = None) -> Optional[Any]:
        """
        获取缓存
        
        Args:
            key: 缓存键
            max_age: 最大允许的缓存年龄（秒），None表示不检查
            
        Returns:
            缓存值，不存在或过期返回None
        """
        with self._cache_lock:
            entry = self._memory_cache.get(key)
            if entry is None:
                return None
            
            # 检查TTL过期
            if entry.ttl > 0:
                age = time.time() - entry.created_at
                if age > entry.ttl:
                    # 过期，删除
                    self._memory_cache.pop(key, None)
                    self._db.execute("DELETE FROM data_cache WHERE key = ?", (key,))
                    self._db.commit()
                    return None
            
            # 检查max_age
            if max_age is not None:
                age = time.time() - entry.created_at
                if age > max_age:
                    return None
            
            return entry.value
    
    def set_many(self, items: Dict[str, Any], ttl: int = 60):
        """批量设置缓存"""
        for key, value in items.items():
            self.set(key, value, ttl)
    
    def get_many(self, keys: list, max_age: float = None) -> Dict[str, Any]:
        """批量获取缓存"""
        result = {}
        for key in keys:
            val = self.get(key, max_age)
            if val is not None:
                result[key] = val
        return result
    
    def delete(self, key: str):
        """删除缓存"""
        with self._cache_lock:
            self._memory_cache.pop(key, None)
            self._db.execute("DELETE FROM data_cache WHERE key = ?", (key,))
            self._db.commit()
    
    def clear_expired(self):
        """清理过期缓存"""
        with self._cache_lock:
            now = time.time()
            expired_keys = []
            
            for key, entry in self._memory_cache.items():
                if entry.ttl > 0 and (now - entry.created_at) > entry.ttl:
                    expired_keys.append(key)
            
            for key in expired_keys:
                self._memory_cache.pop(key, None)
                self._db.execute("DELETE FROM data_cache WHERE key = ?", (key,))
            
            if expired_keys:
                self._db.commit()
                print(f"[DataCacheHub] 清理 {len(expired_keys)} 条过期缓存")
            
            return len(expired_keys)
    
    def clear_all(self):
        """清空所有缓存"""
        with self._cache_lock:
            self._memory_cache.clear()
            self._db.execute("DELETE FROM data_cache")
            self._db.commit()
            print("[DataCacheHub] 已清空所有缓存")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        with self._cache_lock:
            now = time.time()
            valid = 0
            expired = 0
            
            for entry in self._memory_cache.values():
                if entry.ttl > 0 and (now - entry.created_at) > entry.ttl:
                    expired += 1
                else:
                    valid += 1
            
            return {
                "total": len(self._memory_cache),
                "valid": valid,
                "expired": expired,
                "memory_size_mb": sum(
                    len(json.dumps(v.value, ensure_ascii=False)) 
                    for v in self._memory_cache.values()
                ) / 1024 / 1024
            }
    
    def close(self):
        """关闭数据库连接"""
        if hasattr(self, '_db'):
            self._db.close()


# 全局单例
_cache_instance: Optional[DataCacheHub] = None


def get_cache() -> DataCacheHub:
    """获取缓存单例"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = DataCacheHub()
    return _cache_instance


# 便捷函数
def cache_set(key: str, value: Any, ttl: int = 60):
    """缓存设置快捷函数"""
    get_cache().set(key, value, ttl)


def cache_get(key: str, max_age: float = None) -> Optional[Any]:
    """缓存读取快捷函数"""
    return get_cache().get(key, max_age)
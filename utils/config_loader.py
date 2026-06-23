"""
MMTracker 统一配置管理器
单一入口加载所有配置，解决散落各处的问题
"""

import os
import json
from typing import Dict, Any, Optional
from pathlib import Path

# 单例实例
_config_instance: Optional[Dict] = None


def get_config(config_file: str = None) -> Dict[str, Any]:
    """
    获取配置 - 统一入口
    优先加载传入的 config_file，否则使用默认配置
    """
    global _config_instance
    
    if _config_instance is not None:
        return _config_instance
    
    # 默认配置路径
    base_dir = Path(__file__).parent.parent
    
    config_files = [
        base_dir / "config" / "testnet_config.json",
        base_dir / "config" / "strategy_params.json",
    ]
    
    merged = {}
    
    for cf in config_files:
        if cf.exists():
            with open(cf, 'r') as f:
                data = json.load(f)
                merged.update(data)
    
    # 提取关键配置
    result = {
        # OKX API 配置
        "api_key": merged.get("okx", {}).get("api_key", ""),
        "api_secret": merged.get("okx", {}).get("api_secret", ""),
        "passphrase": merged.get("okx", {}).get("passphrase", ""),
        
        # 交易参数 - 统一从 risk_management 读取
        "risk_management": merged.get("risk_management", {}),
        "exit_params": merged.get("exit_params", merged.get("risk_management", {})),
        
        # 信号权重
        "signal_weights": merged.get("signal_weights", {}),
        
        # 信号统计
        "signal_stats": merged.get("signal_stats", {}),
        
        # 入场条件
        "screener": merged.get("screener", {}),
        
        # 多空模式
        "long_short_mode": merged.get("long_short_mode", {}),
        
        # 自动驾驶参数
        "auto_pilot": merged.get("auto_pilot", {}),
        
        # 原始数据备份
        "_raw": merged,
    }
    
    # 兼容旧字段
    result["use_real"] = False  # 强制使用模拟盘
    result["default_position_size"] = merged.get("trading", {}).get("default_position_size", 888)
    result["max_position_size"] = merged.get("trading", {}).get("max_position_size", 2000)
    
    _config_instance = result
    return result


def get_risk_params() -> Dict[str, Any]:
    """
    获取风险参数 - 止盈止损核心配置
    """
    config = get_config()
    return config.get("risk_management", {})


def get_exit_params() -> Dict[str, Any]:
    """
    获取出场参数
    """
    config = get_config()
    return config.get("exit_params", config.get("risk_management", {}))


def get_signal_weights() -> Dict[str, float]:
    """
    获取信号权重
    """
    config = get_config()
    return config.get("signal_weights", {})


def reload_config() -> Dict[str, Any]:
    """
    重新加载配置
    """
    global _config_instance
    _config_instance = None
    return get_config()


def save_config(key: str, value: Any, config_file: str = "strategy_params.json") -> bool:
    """
    保存配置到文件
    """
    base_dir = Path(__file__).parent.parent
    path = base_dir / "config" / config_file
    
    if not path.exists():
        return False
    
    with open(path, 'r') as f:
        data = json.load(f)
    
    # 嵌套更新
    keys = key.split(".")
    current = data
    for k in keys[:-1]:
        if k not in current:
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value
    
    with open(path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    # 清除缓存
    global _config_instance
    _config_instance = None
    
    return True


def get_okx_credentials() -> Dict[str, str]:
    """
    获取 OKX API 凭证
    """
    config = get_config()
    return {
        "api_key": config.get("api_key", ""),
        "api_secret": config.get("api_secret", ""),
        "passphrase": config.get("passphrase", ""),
    }


def get_proxy() -> Optional[Dict[str, str]]:
    """
    获取 HTTP 代理配置
    """
    config = get_config()
    proxy = config.get("http", {}).get("proxy", "")
    if proxy:
        return {"http": proxy, "https": proxy}
    # 兼容环境变量
    import os
    proxy = os.environ.get('https_proxy') or os.environ.get('http_proxy')
    if proxy:
        return {"http": proxy, "https": proxy}
    return None
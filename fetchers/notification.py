"""
桌面通知模块
支持 Linux (notify-send), Windows (win10toast), macOS (osascript)
"""

import os
import sys
import subprocess
import threading
from typing import Optional


def _send_linux_notification(title: str, message: str, urgency: str = "normal"):
    """Linux: 使用 notify-send"""
    try:
        subprocess.run(
            ["notify-send", "-u", urgency, title, message],
            capture_output=True,
            timeout=5
        )
        return True
    except Exception:
        return False


def _send_windows_notification(title: str, message: str):
    """Windows: 使用 win10toast"""
    try:
        from win10toast import ToastNotifier
        toaster = ToastNotifier()
        toaster.show_toast(title, message, duration=10, threaded=True)
        return True
    except ImportError:
        # 尝试用 PowerShell
        try:
            script = f'''
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $text = $template.GetElementsByTagName("text")
            $text[0].AppendChild($template.CreateTextNode("{title}")) > $null
            $text[1].AppendChild($template.CreateTextNode("{message}")) > $null
            $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("MMTracker").Show($toast)
            '''
            subprocess.run(["powershell", "-Command", script], capture_output=True, timeout=10)
            return True
        except Exception:
            return False
    except Exception:
        return False


def _send_macos_notification(title: str, message: str):
    """macOS: 使用 osascript"""
    try:
        script = f'display notification "{message}" with title "{title}"'
        subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


def send_notification(title: str, message: str, urgency: str = "normal") -> bool:
    """
    发送跨平台桌面通知
    
    Args:
        title: 通知标题
        message: 通知内容
        urgency: 紧急程度 (low/normal/critical) - 仅 Linux 有效
    
    Returns:
        是否发送成功
    """
    if sys.platform == "linux":
        return _send_linux_notification(title, message, urgency)
    elif sys.platform == "win32":
        return _send_windows_notification(title, message)
    elif sys.platform == "darwin":
        return _send_macos_notification(title, message)
    else:
        return False


def send_notification_async(title: str, message: str, urgency: str = "normal"):
    """异步发送通知（不阻塞主线程）"""
    thread = threading.Thread(target=send_notification, args=(title, message, urgency))
    thread.daemon = True
    thread.start()


def notify_signal_alert(symbol: str, signal_count: int, signals: list, level: str = "ENTRY"):
    """
    发送信号预警通知
    
    Args:
        symbol: 代币符号
        signal_count: 触发的信号数量
        signals: 信号列表
        level: 预警级别 (ENTRY/WATCH)
    """
    if level == "ENTRY":
        title = f"🟢 MMTracker: {symbol} 满足入场条件！"
        urgency = "critical"
    else:
        title = f"⚠️ MMTracker: {symbol} 密切关注"
        urgency = "normal"
    
    signal_str = ", ".join(signals[:3])
    if len(signals) > 3:
        signal_str += f" (+{len(signals)-3})"
    
    message = f"触发 {signal_count} 个信号: {signal_str}"
    
    send_notification_async(title, message, urgency)


# ============================================================================
# 通知过滤器 - 避免重复通知
# ============================================================================

class NotificationFilter:
    """避免重复通知的过滤器"""
    
    def __init__(self, cooldown_seconds: int = 3600):
        self.cooldown_seconds = cooldown_seconds
        self.last_notification: dict = {}
        self._lock = threading.Lock()
    
    def should_notify(self, key: str) -> bool:
        """检查是否应该发送通知"""
        with self._lock:
            import time
            now = time.time()
            
            if key not in self.last_notification:
                return True
            
            last_time = self.last_notification[key]
            if now - last_time >= self.cooldown_seconds:
                return True
            
            return False
    
    def record_notification(self, key: str):
        """记录已发送的通知"""
        with self._lock:
            import time
            self.last_notification[key] = time.time()


# 全局通知过滤器（1小时内不重复通知同一代币）
notification_filter = NotificationFilter(cooldown_seconds=3600)


def notify_if_needed(symbol: str, signal_count: int, triggered_signals: list, grade: str):
    """
    根据信号情况发送通知（带去重）
    
    Args:
        symbol: 代币符号
        signal_count: 触发信号数
        triggered_signals: 触发信号列表
        grade: 等级 (ENTRY/WATCH)
    """
    # 只有 ENTRY (4+) 或 WATCH (2-3) 级别才通知
    if grade not in ["ENTRY", "WATCH"]:
        return
    
    key = f"{symbol}_{grade}"
    
    if notification_filter.should_notify(key):
        notify_signal_alert(symbol, signal_count, triggered_signals, grade)
        notification_filter.record_notification(key)
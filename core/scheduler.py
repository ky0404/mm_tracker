#!/usr/bin/env python3
"""
MMTracker Scheduler 调度器
使用 APScheduler 实现定时任务调度
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from core.database import get_db

logger = logging.getLogger(__name__)


class MMTrackerScheduler:
    """
    MMTracker 调度器
    - 市场扫描: 每5分钟
    - 持仓监控: 每30秒
    - 优化触发: 每20笔交易
    """
    
    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self.scheduler = BackgroundScheduler()
        self.running = False
        
        # 调度间隔配置
        self.scan_interval = self.config.get('scan_interval', 300)  # 5分钟
        self.monitor_interval = self.config.get('monitor_interval', 30)  # 30秒
        self.optimize_threshold = self.config.get('optimize_threshold', 20)  # 20笔
        
        # 回调函数
        self.scan_callback: Optional[Callable] = None
        self.monitor_callback: Optional[Callable] = None
        self.optimize_callback: Optional[Callable] = None
        
        # 数据库
        self.db = get_db()
        
        # 状态
        self.last_scan_time: Optional[datetime] = None
        self.last_optimize_time: Optional[datetime] = None
    
    def set_scan_callback(self, callback: Callable):
        """设置市场扫描回调"""
        logger.info(f"[Scheduler] set_scan_callback: {callback}")
        self.scan_callback = callback
        logger.info(f"[Scheduler] scan_callback 已设置: {self.scan_callback}")
    
    def set_monitor_callback(self, callback: Callable):
        """设置持仓监控回调"""
        self.monitor_callback = callback
    
    def set_optimize_callback(self, callback: Callable):
        """设置优化回调"""
        self.optimize_callback = callback
    
    def start(self):
        """启动调度器"""
        if self.running:
            logger.warning("Scheduler 已在运行中")
            return
        
        logger.info(f"🚀 启动 Scheduler: 扫描={self.scan_interval}秒, 监控={self.monitor_interval}秒")
        
    def start(self):
        """启动调度器"""
        if self.scheduler.running:
            logger.warning("[Scheduler] 调度器已在运行")
            return
        
        # 添加市场扫描任务 (每5分钟)
        self.scheduler.add_job(
            self._run_scan,
            trigger=IntervalTrigger(seconds=self.scan_interval),
            id='market_scan',
            name='市场扫描',
            replace_existing=True,
            max_instances=3,  # 增加到3，防止因扫描时间过长被跳过
            misfire_grace_time=600,  # 任务延迟超过10分钟才跳过
            coalesce=True,  # 合并多次错过的执行为一次
            next_run_time=datetime.now() + timedelta(seconds=self.scan_interval)
        )
        
        # 添加持仓监控任务 (每30秒)
        self.scheduler.add_job(
            self._run_monitor,
            trigger=IntervalTrigger(seconds=self.monitor_interval),
            id='position_monitor',
            name='持仓监控',
            replace_existing=True,
            max_instances=3,  # 增加到3，允许并发执行
            misfire_grace_time=60,  # 任务延迟超过60秒才跳过
            coalesce=True,  # 合并多次错过的执行为一次
            next_run_time=datetime.now() + timedelta(seconds=self.monitor_interval)  # 立即计划
        )
        
        # 添加优化检查任务 (每5分钟检查一次是否需要优化)
        self.scheduler.add_job(
            self._check_optimize,
            trigger=IntervalTrigger(seconds=300),
            id='optimize_check',
            name='优化检查',
            replace_existing=True,
            max_instances=2,
            misfire_grace_time=300,  # 延迟超过5分钟才跳过
            coalesce=True  # 合并多次错过的执行
        )
        
        self.scheduler.start()
        self.running = True
        
        # 列出所有已添加的任务
        jobs = self.scheduler.get_jobs()
        logger.info(f"[Scheduler] 已添加 {len(jobs)} 个任务:")
        for job in jobs:
            next_run = job.next_run_time
            next_run_str = next_run.strftime('%H:%M:%S') if next_run else 'N/A'
            logger.info(f"  - {job.name}: next_run={next_run_str}")
        
        # 保存状态
        self.db.set_state('scheduler_status', 'running')
        self.db.set_state('scheduler_started_at', datetime.now().isoformat())
        
        logger.info("✅ Scheduler 已启动")
    
    def stop(self):
        """停止调度器"""
        if not self.running:
            return
        
        logger.info("🛑 停止 Scheduler...")
        self.scheduler.shutdown(wait=False)
        self.running = False
        
        # 保存状态
        self.db.set_state('scheduler_status', 'stopped')
        self.db.set_state('scheduler_stopped_at', datetime.now().isoformat())
        
        logger.info("✅ Scheduler 已停止")
    
    def _run_scan(self):
        """执行市场扫描"""
        import traceback
        logger.info("🔍 [市场扫描任务开始] ===================")
        logger.info(f"[市场扫描] scan_callback = {self.scan_callback}")
        
        if not self.scan_callback:
            logger.warning("[市场扫描] ❌ 未设置 scan_callback，跳过")
            return
        
        try:
            logger.info("[市场扫描] 开始执行 scan_callback...")
            result = self.scan_callback()
            self.last_scan_time = datetime.now()
            
            # 记录扫描结果 (run_cycle 返回字典，不是列表)
            if isinstance(result, dict):
                scanned = result.get('scanned', 0)
                signals = result.get('signals_triggered', 0)
                entries = result.get('entries', 0)
            else:
                scanned = len(result) if result else 0
                signals = 0
                entries = 0
            
            self.db.set_state('last_scan_time', self.last_scan_time.isoformat())
            self.db.set_state('last_scan_result', {
                'candidates': scanned,
                'signals_triggered': signals,
                'entries': entries,
                'timestamp': self.last_scan_time.isoformat()
            })
            
            logger.info(f"✅ 扫描完成: {scanned} 个候选, 信号: {signals}, 入场: {entries}")
        except Exception as e:
            logger.error(f"❌ 扫描失败: {e}")
            logger.error(traceback.format_exc())
    
    def _run_monitor(self):
        """执行持仓监控"""
        if not self.monitor_callback:
            logger.debug("未设置 monitor_callback，跳过")
            return
        
        try:
            result = self.monitor_callback()
            
            # 记录监控结果 - 处理 result 可能为 list 或 dict
            if result:
                if isinstance(result, dict):
                    closed_count = len(result.get('closed', []))
                elif isinstance(result, list):
                    closed_count = len(result)
                else:
                    closed_count = 0
                    
                if closed_count > 0:
                    logger.info(f"📊 持仓监控: 平仓 {closed_count} 个")
                    
                    # 检查是否触发优化
                    self._check_optimize()
        except Exception as e:
            logger.error(f"❌ 监控失败: {e}")
            # 添加详细错误信息用于调试
            import traceback
            logger.debug(f"详细错误: {traceback.format_exc()}")
    
    def _check_optimize(self):
        """检查是否需要优化"""
        if not self.optimize_callback:
            return
        
        try:
            # 获取交易统计
            stats = self.db.get_trade_stats()
            total_trades = stats.get('total_trades', 0)
            
            # 获取上次优化时间
            last_optimize = self.db.get_state('last_optimize_trade_count', 0)
            
            # 检查是否达到优化阈值
            if total_trades - last_optimize >= self.optimize_threshold:
                logger.info(f"🎯 触发优化阈值: {total_trades - last_optimize} 笔新交易")
                
                # 执行优化
                result = self.optimize_callback()
                
                if result:
                    # 更新优化计数
                    self.db.set_state('last_optimize_trade_count', total_trades)
                    self.last_optimize_time = datetime.now()
                    logger.info("✅ 优化完成")
                    
        except Exception as e:
            logger.error(f"❌ 优化检查失败: {e}")
    
    def get_status(self) -> Dict[str, Any]:
        """获取调度器状态"""
        return {
            'running': self.running,
            'scan_interval': self.scan_interval,
            'monitor_interval': self.monitor_interval,
            'last_scan_time': self.last_scan_time.isoformat() if self.last_scan_time else None,
            'last_optimize_time': self.last_optimize_time.isoformat() if self.last_optimize_time else None,
            'jobs': [
                {'id': job.id, 'name': job.name, 'next_run': str(job.next_run_time) if job.next_run_time else None}
                for job in self.scheduler.get_jobs()
            ]
        }
    
    def pause(self):
        """暂停调度器"""
        self.scheduler.pause()
        self.db.set_state('scheduler_status', 'paused')
        logger.info("⏸ Scheduler 已暂停")
    
    def resume(self):
        """恢复调度器"""
        self.scheduler.resume()
        self.db.set_state('scheduler_status', 'running')
        logger.info("▶️ Scheduler 已恢复")


# 全局调度器实例
_scheduler_instance: Optional[MMTrackerScheduler] = None


def get_scheduler(config: Dict[str, Any] = None, force_new: bool = False) -> MMTrackerScheduler:
    """获取调度器实例
    
    Args:
        config: 调度器配置
        force_new: 强制创建新实例（用于重新启动）
    """
    global _scheduler_instance
    
    # 如果需要强制创建新实例，先停止并清空旧的
    if force_new and _scheduler_instance is not None:
        try:
            _scheduler_instance.stop()
        except:
            pass
        _scheduler_instance = None
    
    # 如果没有实例，创建新实例
    if _scheduler_instance is None:
        _scheduler_instance = MMTrackerScheduler(config)
    else:
        # 更新配置参数
        if config:
            _scheduler_instance.config.update(config)
            # 更新间隔配置
            _scheduler_instance.scan_interval = config.get('scan_interval', _scheduler_instance.scan_interval)
            _scheduler_instance.monitor_interval = config.get('monitor_interval', _scheduler_instance.monitor_interval)
            _scheduler_instance.optimize_threshold = config.get('optimize_threshold', _scheduler_instance.optimize_threshold)
    
    return _scheduler_instance


def start_scheduler(config: Dict[str, Any] = None) -> MMTrackerScheduler:
    """启动调度器"""
    scheduler = get_scheduler(config)
    scheduler.start()
    return scheduler


def stop_scheduler():
    """停止调度器"""
    global _scheduler_instance
    if _scheduler_instance:
        _scheduler_instance.stop()
        _scheduler_instance = None
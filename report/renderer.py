"""
报告渲染器
使用 rich 库生成终端彩色报告，并保存 Markdown 文件
"""

import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console()


class ReportRenderer:
    """
    报告渲染器
    
    负责：
    1. Rich 终端彩色输出
    2. Markdown 文件保存
    """
    
    def __init__(self, reports_dir: str = "reports"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
    
    def render_terminal(
        self,
        symbol: str,
        all_signals: dict,
        score_result: dict,
        raw_data: dict
    ):
        """
        用 rich 库在终端打印完整报告
        """
        # 获取当前时间
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
        
        # ===== 第1块：顶部标题 =====
        title_panel = Panel(
            Text(f"🔍 {symbol}/USDT  庄家行为量化监测报告", style="bold cyan"),
            subtitle=f"生成时间: {now}",
            border_style="cyan",
            box=box.DOUBLE
        )
        console.print(title_panel)
        console.print()
        
        # ===== 第2块：综合评分Panel =====
        grade = score_result.get("grade", "IDLE")
        grade_emoji = score_result.get("grade_emoji", "⚪")
        grade_label = score_result.get("grade_label", "无明显信号")
        total_score = score_result.get("total_score", 0)
        max_score = score_result.get("max_score", 10)
        triggered_count = score_result.get("triggered_count", 0)
        recommendation = score_result.get("recommendation", "")
        
        # 根据等级选择边框颜色
        border_color = {
            "CRITICAL": "red",
            "WATCH": "yellow",
            "MONITOR": "green",
            "IDLE": "white"
        }.get(grade, "white")
        
        score_content = f"""
{grade_emoji} 风险等级: {grade_label}
📊 综合得分: {total_score:.1f}/{max_score}
🎯 触发信号: {triggered_count}/11

💡 操作建议: {recommendation}
        """.strip()
        
        score_panel = Panel(
            Text(score_content, style=f"bold {border_color}"),
            title="📈 综合评估",
            border_style=border_color,
            box=box.ROUNDED
        )
        console.print(score_panel)
        console.print()
        
        # ===== 第3块：7个信号状态表格 =====
        signal_table = Table(
            title="🎯 信号检测详情",
            show_lines=True,
            box=box.SIMPLE
        )
        signal_table.add_column("#", justify="center", width=3)
        signal_table.add_column("信号名称", style="cyan", width=18)
        signal_table.add_column("状态", justify="center", width=8)
        signal_table.add_column("数值", justify="right", width=20)
        signal_table.add_column("详情", style="white")
        
        signal_names = {
            "signal_1_funding": "资金费率趋势",
            "signal_2_oi": "OI增长价格横盘",
            "signal_3_volume": "异常放量",
            "signal_4_round": "整数关口卡位",
            "signal_5_futures": "Binance合约新上线",
            "signal_6_dex": "DEX买压",
            "signal_7_rs": "相对BTC强度",
        }
        
        for idx, (signal_key, signal_result) in enumerate(all_signals.items(), 1):
            triggered = signal_result.get("triggered", False)
            signal_cn = signal_names.get(signal_key, signal_key)
            
            # 状态列
            if triggered:
                status = "✅ 触发"
                status_style = "green bold"
            else:
                status = "❌ 未触发"
                status_style = "red"
            
            # 数值列 - 根据不同信号提取
            value_str = self._format_signal_value(signal_key, signal_result)
            
            # 详情列
            detail = signal_result.get("detail", "")[:40]
            
            # 触发行高亮
            if triggered:
                signal_cn = f"[green bold]{signal_cn}[/green bold]"
            
            signal_table.add_row(
                str(idx),
                signal_cn,
                Text(status, style=status_style),
                value_str,
                detail
            )
        
        console.print(signal_table)
        console.print()
        
        # ===== 第4块：关键价格数据 =====
        price_info = raw_data.get("price_info", {})
        funding_info = raw_data.get("funding_info", {})
        oi_info = raw_data.get("oi_info", {})
        dex_info = raw_data.get("dex_info", {})
        
        price_content = f"""
💰 当前价格: ${price_info.get('price', 0):.4f} | 7日涨跌: {price_info.get('change_7d_pct', 0):+.2f}%
📊 24h成交量: ${price_info.get('volume_24h', 0):,.0f} | 市值: ${price_info.get('market_cap', 0):,.0f}

💵 资金费率: {funding_info.get('latest_rate', 0):.4f}% ({funding_info.get('trend', 'N/A')})
📈 OI规模: ${oi_info.get('oi_latest', 0)/1e6:.1f}M | 7日变化: {oi_info.get('oi_change_7d_pct', 0):+.1f}%

🌊 DEX流动性: ${dex_info.get('liquidity_usd', 0):,.0f} | 买卖比: {dex_info.get('buy_sell_ratio', 0):.2f}
        """.strip()
        
        price_panel = Panel(
            price_content,
            title="📊 市场数据概览",
            border_style="blue",
            box=box.ROUNDED
        )
        console.print(price_panel)
        console.print()
        
        # ===== 第5块：底部风险提示 =====
        risk_tips = self._generate_risk_tips(all_signals, score_result)
        
        if risk_tips:
            risk_panel = Panel(
                "\n".join(risk_tips),
                title="⚡ 风险提示",
                border_style="magenta",
                box=box.ROUNDED
            )
            console.print(risk_panel)
        
        # 数据源可用性
        data_status = self._check_data_status(raw_data)
        if data_status:
            status_panel = Panel(
                "\n".join(data_status),
                title="📡 数据源状态",
                border_style="dim",
                box=box.ROUNDED
            )
            console.print(status_panel)
    
    def _format_signal_value(self, signal_key: str, signal_result: dict) -> str:
        """格式化信号数值"""
        if signal_key == "signal_1_funding":
            rate = signal_result.get("latest_rate", 0)
            trend = signal_result.get("trend", "")
            arrow = "↑" if trend == "rising" else ("↓" if trend == "falling" else "→")
            return f"{rate:+.4f}% {arrow}"
        
        elif signal_key == "signal_2_oi":
            oi_change = signal_result.get("oi_change_7d_pct", 0)
            price_change = signal_result.get("price_change_7d_pct", 0)
            return f"OI {oi_change:+.1f}% vs 价格 {price_change:+.1f}%"
        
        elif signal_key == "signal_3_volume":
            ratio = signal_result.get("volume_ratio", 0)
            signal_type = signal_result.get("signal_type", "normal")
            return f"{ratio:.1f}x [{signal_type}]"
        
        elif signal_key == "signal_4_round":
            level = signal_result.get("nearest_level", 0)
            days = signal_result.get("stall_days", 0)
            signal_type = signal_result.get("signal_type", "far")
            return f"${level} 关口卡{days}天 [{signal_type}]"
        
        elif signal_key == "signal_5_futures":
            days = signal_result.get("days_since_listing", -1)
            score = signal_result.get("recency_score", 0)
            return f"上线{days}天前 (得分{score:.1f})"
        
        elif signal_key == "signal_6_dex":
            ratio = signal_result.get("buy_sell_ratio", 0)
            liq = signal_result.get("liquidity_usd", 0)
            return f"买卖比{ratio:.2f} | 流动性${liq/1e6:.1f}M"
        
        elif signal_key == "signal_7_rs":
            rs = signal_result.get("relative_strength_pct", 0)
            return f"{rs:+.2f}% vs BTC"
        
        return "N/A"
    
    def _generate_risk_tips(self, all_signals: dict, score_result: dict) -> List[str]:
        """生成风险提示"""
        tips = []
        
        # Signal 5: 合约新上线
        sig5 = all_signals.get("signal_5_futures", {})
        if sig5.get("triggered"):
            days = sig5.get("days_since_listing", 0)
            if 0 <= days <= 30:
                tips.append(f"⚡ 合约上线仅 {days} 天，做多窗口期仍在，重点关注")
        
        # Signal 1: 资金费率过高
        sig1 = all_signals.get("signal_1_funding", {})
        rate = sig1.get("latest_rate", 0)
        if rate > 0.5:
            tips.append(f"⚠️ 资金费率偏高 ({rate:.2f}%)，接近顶部风险，谨慎追高")
        
        # Signal 4: 刚刚突破
        sig4 = all_signals.get("signal_4_round", {})
        if sig4.get("signal_type") == "just_broke":
            level = sig4.get("nearest_level", 0)
            tips.append(f"🚀 刚刚突破 ${level} 整数关口，确认入场信号")
        
        # Signal 3: 量增价涨（真突破）
        sig3 = all_signals.get("signal_3_volume", {})
        if sig3.get("signal_type") == "breakout":
            tips.append("🚀 成交量放大且价格上涨，确认突破信号，可考虑顺势而为")
        
        # 综合评分警告
        grade = score_result.get("grade", "IDLE")
        if grade == "CRITICAL":
            tips.append("🔴 警告：多个做多信号同时触发，庄家可能正在启动，建议密切关注")
        
        return tips
    
    def _check_data_status(self, raw_data: dict) -> List[str]:
        """检查数据源可用性"""
        status = []
        
        if raw_data.get("funding_info", {}).get("error"):
            status.append("❌ 资金费率数据不可用")
        else:
            status.append("✅ 资金费率数据正常")
        
        if raw_data.get("oi_info", {}).get("error"):
            status.append("❌ OI数据不可用")
        else:
            status.append("✅ OI数据正常")
        
        if raw_data.get("kline_info", {}).get("error"):
            status.append("❌ K线数据不可用")
        else:
            status.append("✅ K线数据正常")
        
        return status
    
    def save_markdown(
        self,
        symbol: str,
        all_signals: dict,
        score_result: dict,
        raw_data: dict
    ) -> str:
        """
        保存 Markdown 报告到 reports/ 目录
        """
        now = datetime.now()
        timestamp = now.strftime("%Y%m%d_%H%M%S")
        filename = f"{symbol}_{timestamp}.md"
        filepath = self.reports_dir / filename
        
        # 构建 Markdown 内容
        lines = []
        
        # 标题
        lines.append(f"# 🔍 {symbol}/USDT 庄家行为量化监测报告")
        lines.append("")
        lines.append(f"**生成时间**: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 综合评估
        grade = score_result.get("grade", "IDLE")
        grade_emoji = score_result.get("grade_emoji", "⚪")
        grade_label = score_result.get("grade_label", "无明显信号")
        total_score = score_result.get("total_score", 0)
        max_score = score_result.get("max_score", 10)
        triggered_count = score_result.get("triggered_count", 0)
        recommendation = score_result.get("recommendation", "")
        
        lines.append("## 📈 综合评估")
        lines.append("")
        lines.append(f"| 指标 | 数值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 风险等级 | {grade_emoji} {grade_label} |")
        lines.append(f"| 综合得分 | **{total_score:.1f}/{max_score}** |")
        lines.append(f"| 触发信号 | {triggered_count}/11 |")
        lines.append(f"| 操作建议 | {recommendation} |")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 信号详情表格
        lines.append("## 🎯 信号检测详情")
        lines.append("")
        lines.append("| # | 信号名称 | 状态 | 数值 | 详情 |")
        lines.append("|---|----------|------|------|------|")
        
        signal_names = {
            "signal_1_funding": "资金费率趋势",
            "signal_2_oi": "OI增长价格横盘",
            "signal_3_volume": "异常放量",
            "signal_4_round": "整数关口卡位",
            "signal_5_futures": "Binance合约新上线",
            "signal_6_dex": "DEX买压",
            "signal_7_rs": "相对BTC强度",
        }
        
        for idx, (signal_key, signal_result) in enumerate(all_signals.items(), 1):
            triggered = signal_result.get("triggered", False)
            signal_cn = signal_names.get(signal_key, signal_key)
            
            status = "✅ 触发" if triggered else "❌ 未触发"
            value = self._format_signal_value(signal_key, signal_result)
            detail = signal_result.get("detail", "")[:30]
            
            lines.append(f"| {idx} | {signal_cn} | {status} | {value} | {detail} |")
        
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 市场数据
        price_info = raw_data.get("price_info", {})
        lines.append("## 📊 市场数据概览")
        lines.append("")
        lines.append(f"- 当前价格: **${price_info.get('price', 0):.4f}**")
        lines.append(f"- 7日涨跌: {price_info.get('change_7d_pct', 0):+.2f}%")
        lines.append(f"- 24h成交量: ${price_info.get('volume_24h', 0):,.0f}")
        lines.append(f"- 市值: ${price_info.get('market_cap', 0):,.0f}")
        lines.append("")
        lines.append("---")
        lines.append("")
        
        # 底部信息
        lines.append("*本报告由 MMTracker 自动生成*")
        
        # 写入文件
        content = "\n".join(lines)
        filepath.write_text(content, encoding="utf-8")
        
        return str(filepath)
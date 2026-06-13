#!/usr/bin/env python3
"""
AutoPilot - 闭环自动驾驶交易系统入口
用法:
  python3 autopilot.py                    # 模拟模式运行
  python3 autopilot.py --real              # 真实测试网模式
  python3 autopilot.py -n 10 -i 60         # 运行10个周期，间隔60秒
"""
import sys
import os

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading.auto_pilot import create_autopilot, main

if __name__ == "__main__":
    main()
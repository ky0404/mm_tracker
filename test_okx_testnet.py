#!/usr/bin/env python3
"""
OKX Testnet 测试脚本
用法: python test_okx_testnet.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import argparse


def load_config(config_path: str = "config/testnet_config.json"):
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"❌ 配置文件未找到: {config_path}")
        print("请先创建 config/testnet_config.json")
        return None
    except json.JSONDecodeError as e:
        print(f"❌ 配置文件格式错误: {e}")
        return None


def test_api_connection(trader):
    print("\n=== 1. 测试 API 连接 ===")
    try:
        balance = trader.get_balance()
        if balance:
            total_eq = balance.get('totalEq', '0')
            print(f"✅ API 连接成功")
            print(f"   账户信息: {total_eq} USDT")
            return True
        else:
            print(f"❌ API 返回空数据")
            return False
    except Exception as e:
        print(f"❌ API 连接失败: {e}")
        return False


def test_place_order(trader):
    print("\n=== 2. 测试现货市价单下单 ===")
    try:
        # 使用现货格式: DOGE-USDT (不是 SWAP-DOGE-USDT)
        order = trader.place_order(
            symbol="DOGE-USDT",
            side="buy",
            size=10,
            price=None,
            order_type="market",
        )
        
        if order.get("code") == "0":
            print(f"✅ 现货市价单下单成功")
            print(f"   订单ID: {order['data'][0].get('ordId')}")
            print(f"   成交价: {order['data'][0].get('fillPx')}")
            return True, order['data'][0].get('ordId')
        else:
            print(f"❌ 下单失败: {order.get('msg')}")
            print(f"   错误详情: {order}")
            return False, None
    except Exception as e:
        print(f"❌ 下单异常: {e}")
        return False, None


def test_get_position(trader):
    print("\n=== 3. 测试查询持仓 ===")
    try:
        pos = trader.get_position("BTC-USDT-SWAP")
        if pos:
            print(f"✅ 查询持仓成功")
            print(f"   持仓量: {pos.get('pos')}")
            print(f"   开仓均价: {pos.get('avgPx')}")
            return pos
        else:
            print(f"ℹ️ 当前无持仓")
            return None
    except Exception as e:
        print(f"❌ 查询持仓失败: {e}")
        return None


def test_cancel_order(trader):
    print("\n=== 4. 测试挂单 ===")
    try:
        order = trader.place_order(
            symbol="ETH-USDT-SWAP",
            side="buy",
            size=0.01,
            price=1000.0,
            order_type="limit",
        )
        
        if order.get("code") == "0":
            order_id = order["data"][0].get("ordId")
            print(f"✅ 限价挂单成功")
            print(f"   订单ID: {order_id}")
            
            cancel = trader.cancel_order("ETH-USDT-SWAP", order_id)
            if cancel.get("code") == "0":
                print(f"✅ 撤单成功")
                return True
            else:
                print(f"⚠️ 撤单失败: {cancel.get('msg')}")
                return False
        else:
            print(f"❌ 挂单失败: {order.get('msg')}")
            return False
    except Exception as e:
        print(f"❌ 挂单异常: {e}")
        return False


def test_close_position(trader):
    print("\n=== 5. 测试平仓 ===")
    try:
        pos = trader.get_position("BTC-USDT-SWAP")
        if pos and float(pos.get("pos", 0)) > 0:
            close = trader.close_position("BTC-USDT-SWAP")
            if close.get("code") == "0":
                print(f"✅ 平仓成功")
                return True
            else:
                print(f"⚠️ 平仓失败: {close.get('msg')}")
                return False
        else:
            print(f"ℹ️ 无持仓，无需平仓")
            return True
    except Exception as e:
        print(f"❌ 平仓异常: {e}")
        return False


def run_full_test():
    parser = argparse.ArgumentParser(description="OKX Testnet 测试")
    parser.add_argument("--config", default="config/testnet_config.json", help="配置文件路径")
    parser.add_argument("--symbol", default="BTC", help="测试币种")
    args = parser.parse_args()
    
    config = load_config(args.config)
    if not config:
        return
    
    okx_config = config.get("okx_testnet", {})
    if not okx_config.get("api_key") or "YOUR_" in okx_config.get("api_key", ""):
        print("❌ 请先在 config/testnet_config.json 中填写正确的 API Key")
        print("\n请按以下步骤操作:")
        print("1. 访问 https://www.okx.com/join/testnet 注册测试账户")
        print("2. 进入 Testnet 控制台创建 API Key")
        print("3. 将 API Key 填入 config/testnet_config.json")
        return
    
    print(f"正在测试 OKX Testnet...")
    print(f"API Key: {okx_config['api_key'][:10]}...")
    
    from trading.okx_testnet import OKXTestnetTrader
    
    # 现货模式 (永续合约需要额外权限)
    trader = OKXTestnetTrader(
        api_key=okx_config["api_key"],
        api_secret=okx_config["api_secret"],
        passphrase=okx_config["passphrase"],
        testnet=True,
        use_spot=True,
    )
    
    results = []
    
    results.append(("API 连接", test_api_connection(trader)))
    success, order_id = test_place_order(trader)
    results.append(("下单", success))
    results.append(("查询持仓", test_get_position(trader)))
    results.append(("挂单/撤单", test_cancel_order(trader)))
    results.append(("平仓", test_close_position(trader)))
    
    print("\n" + "="*50)
    print("测试结果汇总")
    print("="*50)
    
    all_passed = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False
    
    print("="*50)
    if all_passed:
        print("🎉 所有测试通过！OKX Testnet 配置正确")
    else:
        print("⚠️ 部分测试失败，请检查配置")
    
    return all_passed


if __name__ == "__main__":
    run_full_test()
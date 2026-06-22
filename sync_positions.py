import os
os.environ['https_proxy'] = 'http://172.18.48.1:10810'

from trading.okx_testnet import OKXTestnetTrader
from trading.trade_db import TradeDB
import json

with open('config/testnet_config.json') as f:
    config = json.load(f)

trader = OKXTestnetTrader(
    api_key=config['okx']['api_key'],
    api_secret=config['okx']['api_secret'],
    passphrase=config['okx']['passphrase'],
    testnet=True
)

print("=== OKX Testnet 实时持仓 ===")
balance = trader.get_balance()
details = balance.get('details', [])
positions = []

for d in details:
    ccy = d.get('ccy')
    total = float(d.get('availBal', 0)) + float(d.get('frozenBal', 0))
    if total > 0.001 and ccy != 'USDT':
        avg_px = float(d.get('accAvgPx', 0))
        current_price = trader.get_current_price(f"{ccy}-USDT")
        positions.append({
            'token': ccy,
            'quantity': total,
            'avg_price': avg_px if avg_px > 0 else current_price,
            'current_price': current_price
        })

print(f"共 {len(positions)} 个持仓:\n")
for p in positions:
    pnl_pct = (p['current_price'] - p['avg_price']) / p['avg_price'] * 100 if p['avg_price'] > 0 else 0
    print(f"  {p['token']:6} 数量:{p['quantity']:>12.2f}  入场:${p['avg_price']:.4f}  当前:${p['current_price']:.4f}  PnL:{pnl_pct:+.1f}%")

total_pnl = sum(p['current_price'] * p['quantity'] - p['avg_price'] * p['quantity'] for p in positions)
print(f"\n总未实现盈亏: {total_pnl:+.2f} USDT")

print("\n=== 同步到数据库 ===")
db = TradeDB()
db_positions = db.get_open_positions()
print(f"数据库现有: {len(db_positions)}笔")

for p in positions:
    existing = [x for x in db_positions if x['token'] == p['token']]
    if not existing:
        db.log_entry(
            token=p['token'],
            entry_price=p['avg_price'],
            position_size=p['quantity'],
            signals=['okx_api_sync']
        )
        print(f"  + 添加 {p['token']}")
    else:
        print(f"  = 已存在 {p['token']}")

print("\n✅ 系统就绪!")
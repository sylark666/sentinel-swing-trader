"""
process_today_stop_losses.py
=============================================================================
Processes the stop-loss exits for IRB.NS and ICICIAMC.NS in portfolio_tracker.db
and dispatches the official Exit alerts to Telegram and Gmail.
=============================================================================
"""

import sys
import portfolio_manager as pm
import notifications as notif

print("=" * 80)
print("PROCESSING STOP LOSS EXITS FOR TODAY (NOV 18, 2026)")
print("=" * 80)

# 1. Close IRB.NS
print("\n1. Closing IRB.NS @ Stop Loss ₹17.80...")
ok1, msg1 = pm.log_full_sell(
    ticker="IRB.NS",
    exit_price=17.80,
    reason="Initial Hard Stop Loss Hit @ ₹17.80"
)
print(f"Result: {ok1} -> {msg1}")

# Dispatch Exit Alert for IRB
notif.dispatch_exit_alert(
    ticker="IRB.NS",
    exit_price=17.80,
    reason="Initial Hard Stop Loss Hit (-5.07%)",
    pnl_pct=-5.07,
    net_pnl_rs=-1439.25,
    holding_days=6.0,
    current_qty=1515
)

# 2. Close ICICIAMC.NS
print("\n2. Closing ICICIAMC.NS @ Stop Loss ₹2,989.40...")
ok2, msg2 = pm.log_full_sell(
    ticker="ICICIAMC.NS",
    exit_price=2989.40,
    reason="Initial Hard Stop Loss Hit @ ₹2,989.40"
)
print(f"Result: {ok2} -> {msg2}")

# Dispatch Exit Alert for ICICIAMC
notif.dispatch_exit_alert(
    ticker="ICICIAMC.NS",
    exit_price=2989.40,
    reason="Initial Hard Stop Loss Hit (-4.19%)",
    pnl_pct=-4.19,
    net_pnl_rs=-1175.40,
    holding_days=6.0,
    current_qty=9
)

# 3. Print updated portfolio summary
print("\n" + "=" * 80)
print("UPDATED PORTFOLIO SUMMARY:")
print("=" * 80)
summary = pm.get_portfolio_summary()
active = pm.get_active_trades()
print(f"• Total Capital:        ₹{summary['total_capital']:,.2f}")
print(f"• Cash Available:       ₹{summary['cash_available']:,.2f} ({summary['cash_pct']:.1f}%)")
print(f"• Invested Capital:     ₹{summary['invested_capital']:,.2f}")
print(f"• Slots Occupied:       {summary['effective_slots_occupied']:.1f} / 3.0")
print(f"• Slots Available:      {summary['effective_slots_available']:.1f} open slot(s)")
print(f"• Can Take New Trade:   {'✅ Yes' if summary['can_take_new_trade'] else '❌ No'}")
print(f"• Target per New Slot:  ₹{summary['slot_target_allocation']:,.2f}")

print("\nRemaining Active Position:")
for p in active:
    print(f"  - {p['ticker']}: {p['qty']} shares @ ₹{p['entry_price']:,.2f} | Stop: ₹{p['current_trailing_stop']:,.2f}")

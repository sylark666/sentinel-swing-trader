import pandas as pd
import numpy as np
import logging
import os

from data_fetcher import fetch_data
from signal_engine import calculate_indicators
from backtester import EngineMechanics

# Suppress logs for fast evaluation
logging.getLogger("DataFetcher").setLevel(logging.CRITICAL)

def evaluate(tp, sl, bb, rsi, trailing):
    test_symbols = [
        "ZOMATO.NS", "HAL.NS", "IREDA.NS", "RVNL.NS", "DIXON.NS", "TRENT.NS", "BHEL.NS",
        "ADANIENT.NS", "KPITTECH.NS", "IRFC.NS", "KALYANKJIL.NS", "MCX.NS", "DELHIVERY.NS",
        "SUZLON.NS", "PFC.NS", "RECLTD.NS", "JINDALSTEL.NS", "LALPATHLAB.NS", "TATASTEEL.NS",
        "RELIANCE.NS", "INFY.NS", "HDFCBANK.NS", "ITC.NS", "SBI.NS", "TCS.NS", "LT.NS",
        "BAJFINANCE.NS", "AXISBANK.NS", "KOTAKBANK.NS", "M&M.NS", "MARUTI.NS", "SUNPHARMA.NS",
        "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS", "INDIGO.NS", "DLF.NS", "GODREJPROP.NS",
        "PERSISTENT.NS", "LTIM.NS", "COFORGE.NS", "TVSMOTOR.NS", "EICHERMOT.NS", "CHOLAFIN.NS",
        "SHRIRAMFIN.NS", "POLYCAB.NS", "HAVELLS.NS", "CUMMINSIND.NS", "SIEMENS.NS", "ABB.NS"
    ]
    
    engine = EngineMechanics(initial_capital=100000, max_positions=3)
    all_net = []
    wins = 0
    total = 0
    
    for sym in test_symbols:
        df = fetch_data(sym, "4H", "730d")
        if df.empty: continue
        df = calculate_indicators(df, bb_std=bb)
        df = df.dropna()
        
        in_trade = False
        entry_price = 0
        current_sl = 0
        cooldown = 0
        
        for i in range(3, len(df)):
            if cooldown > 0:
                cooldown -= 1
                continue
                
            row = df.iloc[i]
            c = row['close']
            
            if not in_trade:
                is_up = c > row['EMA200']
                is_st = c < row['BB_Lower']
                is_ov = row['RSI4'] < rsi
                
                if is_up and is_st and is_ov:
                    in_trade = True
                    entry_price = c * 1.005
                    current_sl = entry_price * (1 - (sl/100.0))
            else:
                target = entry_price * (1 + (tp/100.0))
                exit_price = None
                
                if row['high'] >= target:
                    exit_price = target
                elif row['low'] <= current_sl:
                    exit_price = current_sl
                    
                if not exit_price and trailing:
                    pot_sl = row['high'] * (1 - (sl/100.0))
                    if pot_sl > current_sl:
                        current_sl = pot_sl
                        
                if exit_price:
                    gross_buy = 30000
                    qty = 30000 / entry_price
                    gross_sell = qty * exit_price
                    fric = engine.calculate_friction(gross_buy, True) + engine.calculate_friction(gross_sell, False)
                    net = (gross_sell - gross_buy) - fric
                    
                    all_net.append(net)
                    if net > 0: wins += 1
                    total += 1
                    in_trade = False
                    cooldown = 5
                    
    if total == 0: return 0, 0, 0
    return sum(all_net), (wins/total)*100, total

print("Evaluating...")
for trailing in [False, True]:
    for tp in [3, 5, 8, 10]:
        for sl in [5, 8, 10]:
            net, wr, t = evaluate(tp, sl, 2.5, 20, trailing)
            if t > 20:
                print(f"TP:{tp}% SL:{sl}% Trail:{trailing} -> PnL: {net:.0f} | WinRate: {wr:.1f}% | Trades: {t}")

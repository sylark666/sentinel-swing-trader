import sqlite3
import pandas as pd
import numpy as np
from golden_cross_4h_optimizer import load_4h_universe
from backtester import EngineMechanics

with sqlite3.connect('market_data.db') as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT ticker FROM market_data WHERE ticker != 'NIFTY' ORDER BY ticker ASC")
    all_syms = [r[0] for r in cursor.fetchall()]

# Let's test different Chandelier periods & multipliers
ch_configs = [
    {"period": 22, "mult": 3.0, "name": "Baseline Chandelier (22, 3.0)"},
    {"period": 22, "mult": 2.5, "name": "Chandelier (22, 2.5)"},
    {"period": 22, "mult": 2.2, "name": "Chandelier (22, 2.2)"},
    {"period": 22, "mult": 2.0, "name": "Chandelier (22, 2.0)"},
    {"period": 14, "mult": 2.5, "name": "Chandelier (14, 2.5)"},
    {"period": 14, "mult": 2.0, "name": "Chandelier (14, 2.0)"},
    {"period": 10, "mult": 2.0, "name": "Chandelier (10, 2.0)"},
]

# We need to re-compute indicators for each config or simulate
def run_ch_test(period, mult):
    # Load and calculate
    stock_dfs, all_ts = load_4h_universe(start_date="2018-01-01", symbols=all_syms, timeframe="4H")
    
    # Recalculate Chandelier on each stock
    for s, df_dict in stock_dfs.items():
        # df_dict is a dict of timestamp -> bar dict
        # But load_4h_universe computed indicators inside it
        pass

# Let's do it directly in simulation:


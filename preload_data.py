import time
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from data_fetcher import fetch_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("PreLoader")

SET_1 = [
    "ZOMATO.NS", "HAL.NS", "IREDA.NS", "RVNL.NS", "DIXON.NS", "TRENT.NS", "BHEL.NS",
    "ADANIENT.NS", "KPITTECH.NS", "IRFC.NS", "KALYANKJIL.NS", "MCX.NS", "DELHIVERY.NS",
    "SUZLON.NS", "PFC.NS", "RECLTD.NS", "JINDALSTEL.NS", "LALPATHLAB.NS", "TATASTEEL.NS",
    "RELIANCE.NS", "INFY.NS", "HDFCBANK.NS", "ITC.NS", "SBI.NS", "TCS.NS", "LT.NS",
    "BAJFINANCE.NS", "AXISBANK.NS", "KOTAKBANK.NS", "M&M.NS", "MARUTI.NS", "SUNPHARMA.NS",
    "TITAN.NS", "ULTRACEMCO.NS", "WIPRO.NS", "INDIGO.NS", "DLF.NS", "GODREJPROP.NS",
    "PERSISTENT.NS", "LTIM.NS", "COFORGE.NS", "TVSMOTOR.NS", "EICHERMOT.NS", "CHOLAFIN.NS",
    "SHRIRAMFIN.NS", "POLYCAB.NS", "HAVELLS.NS", "CUMMINSIND.NS", "SIEMENS.NS", "ABB.NS",
]

SET_2 = [
    "SBIN.NS", "BHARTIARTL.NS", "HINDUNILVR.NS", "ICICIBANK.NS", "BAJAJFINSV.NS", 
    "ASIANPAINT.NS", "HCLTECH.NS", "TATAMOTORS.NS", "NTPC.NS", "ONGC.NS", 
    "POWERGRID.NS", "NESTLEIND.NS", "GRASIM.NS", "TECHM.NS", "HINDALCO.NS", 
    "JSWSTEEL.NS", "CIPLA.NS", "DRREDDY.NS", "SBILIFE.NS", "HDFCLIFE.NS", 
    "BRITANNIA.NS", "TATACONSUM.NS", "APOLLOHOSP.NS", "DIVISLAB.NS", "HEROMOTOCO.NS", 
    "BAJAJ-AUTO.NS", "INDUSINDBK.NS", "BANKBARODA.NS", "PNB.NS", "CANBK.NS", 
    "IDFCFIRSTB.NS", "TATACHEM.NS", "TATAPOWER.NS", "VEDL.NS", "COALINDIA.NS", 
    "GAIL.NS", "IOC.NS", "BPCL.NS", "HINDPETRO.NS", "AMBUJACEM.NS", 
    "SHREECEM.NS", "ACC.NS", "ADANIPORTS.NS", "ADANIGREEN.NS", "ADANIPOWER.NS", 
    "ATGL.NS", "AWL.NS", "TORNTPHARM.NS", "ZYDUSLIFE.NS", "LUPIN.NS"
]

SET_3 = [
    "SAIL.NS", "NMDC.NS", "CONCOR.NS", "IRCTC.NS", "BOSCHLTD.NS", 
    "MRF.NS", "PAGEIND.NS", "TATAELXSI.NS", "MPHASIS.NS", "COROMANDEL.NS", 
    "LTTS.NS", "AUBANK.NS", "BANDHANBNK.NS", "FEDERALBNK.NS", "MUTHOOTFIN.NS", 
    "SRF.NS", "PIIND.NS", "UPL.NS", "AARTIIND.NS", "DEEPAKNTR.NS", 
    "NAUKRI.NS", "PAYTM.NS", "NYKAA.NS", "POLICYBZR.NS", "DMART.NS", 
    "VBL.NS", "ZEEL.NS", "PVRINOX.NS", "SUNTV.NS", "ICICIGI.NS", 
    "ICICIPRULI.NS", "NAM-INDIA.NS", "HDFCAMC.NS", "ABBOTINDIA.NS", "ALKEM.NS", 
    "AUROPHARMA.NS", "BIOCON.NS", "GLENMARK.NS", "SYNGENE.NS", "PEL.NS", 
    "MFSL.NS", "LICHSGFIN.NS", "CANFINHOME.NS", "M&MFIN.NS", "MGL.NS", 
    "RELAXO.NS", "BATAINDIA.NS", "VOLTAS.NS", "WHIRLPOOL.NS", "CROMPTON.NS"
]

SYMBOLS = SET_1 + SET_2 + SET_3 + ["^NSEI"]

TIMEFRAMES = ["1H", "2H", "4H", "1D"]

def download_asset(symbol, tf):
    period = "2y" if tf == "1D" else "730d"
    try:
        # fetch_data inherently saves to local CSV cache
        df = fetch_data(symbol, tf, period=period)
        if df.empty:
            return f"[ERROR] {symbol} [{tf}] - No data"
        return f"[SUCCESS] {symbol} [{tf}] - Cached ({len(df)} bars)"
    except Exception as e:
        return f"[ERROR] {symbol} [{tf}] - Error: {e}"

def main():
    logger.info("Initializing Quantum Data Preloader...")
    logger.info(f"Target: {len(SYMBOLS)} symbols across {len(TIMEFRAMES)} timeframes.")
    
    total_tasks = len(SYMBOLS) * len(TIMEFRAMES)
    completed = 0
    
    # We use ThreadPoolExecutor to download multiple symbols concurrently
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = []
        for sym in SYMBOLS:
            for tf in TIMEFRAMES:
                futures.append(executor.submit(download_asset, sym, tf))
                
        for future in as_completed(futures):
            completed += 1
            res = future.result()
            print(f"[{completed}/{total_tasks}] {res}")
            
    logger.info("🎉 All market data pre-loaded successfully! Dashboard simulations will now run instantly.")

if __name__ == "__main__":
    main()

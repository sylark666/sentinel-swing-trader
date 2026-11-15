# v1.0.10
import logging
from data_fetcher import fetch_data
from signal_engine import calculate_indicators, evaluate_signal
from state_machine import SentinelState

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("LocalTester")

def run_local_test(symbol="RELIANCE.NS"):
    logger.info(f"--- 🛡️ SENTINEL-SWING LOCAL DIAGNOSTICS ---")
    logger.info(f"Testing Symbol: {symbol}")
    
    # 1. Fetch Data
    logger.info("1. Fetching Market Data...")
    df_1d = fetch_data(symbol, "1d", period="3y")
    
    if df_1d.empty:
        logger.error("Data fetch failed. Exiting.")
        return
        
    logger.info(f"   ✅ Fetched {len(df_1d)} daily bars.")
    
    # 2. State Machine (Mock 09:45 AM Synthetic Candle)
    state = SentinelState()
    mock_live_quote = {
        'open': df_1d.iloc[-1]['close'] * 0.995,  
        'high': df_1d.iloc[-1]['close'] * 1.01,
        'low': df_1d.iloc[-1]['close'] * 0.99,
        'last_price': df_1d.iloc[-1]['close'] * 1.005,
        'volume': 1500000
    }
    
    logger.info("2. Synthesizing 09:45 AM Bar...")
    df_synthetic = state.synthesize_daily_candle(df_1d, mock_live_quote)
    logger.info("   ✅ Synthetic candle appended successfully.")
    
    # 3. Calculate Indicators
    logger.info("3. Running Signal Engine & Hybrid Chandelier Logic...")
    df_indicators = calculate_indicators(df_synthetic)
    
    # 4. Evaluate Signal
    signal_result = evaluate_signal(df_indicators)
    
    logger.info("\n--- 📊 SIGNAL EVALUATION RESULT ---")
    for key, val in signal_result.items():
        if isinstance(val, float):
            logger.info(f"{key.upper()}: {val:.2f}")
        else:
            logger.info(f"{key.upper()}: {val}")
            
    if signal_result.get('signal'):
        logger.info("\n   🟢 CONFLUENCE BUY SIGNAL TRIGGERED!")
        trigger = signal_result['trigger_price']
        pad_limit = state.calculate_execution_pad(trigger, is_buy=True, pad_pct=0.005)
        logger.info(f"   => Order Type: Stop-Loss Limit")
        logger.info(f"   => Trigger Price: ₹{trigger:.2f}")
        logger.info(f"   => Padded Limit Price: ₹{pad_limit:.2f}")
    else:
        logger.info("\n   🔴 SIGNAL REJECTED.")
        logger.info(f"   => Reason: {signal_result.get('reason')}")

if __name__ == "__main__":
    # Test specific stock known to have strong moves for testing
    run_local_test("TATASTEEL.NS")

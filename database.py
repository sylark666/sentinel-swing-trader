"""
database.py
High-performance local database layer for market candlestick data.
Uses SQLite WAL mode for fast queries and batch writes, with schema aligned to schema.sql.
"""

import sqlite3
import os
import logging
import pandas as pd
from typing import List, Optional, Dict

logger = logging.getLogger("Database")

DB_PATH = "market_data.db"

def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Connect to SQLite database with WAL mode enabled for fast concurrent read/write."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn

def init_db(db_path: str = DB_PATH):
    """Initializes the database schema matching schema.sql."""
    # Seed operational DB on fresh cloud instances if market_data.db is missing
    seed_path = "seed_market_data.db"
    if db_path == "market_data.db" and not os.path.exists("market_data.db") and os.path.exists(seed_path):
        import shutil
        try:
            shutil.copyfile(seed_path, "market_data.db")
            logger.info(f"Seeded market_data.db from {seed_path}")
        except Exception as e:
            logger.warning(f"Failed to seed market_data.db from {seed_path}: {e}")

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_data (
                ticker TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                PRIMARY KEY (ticker, timestamp, timeframe)
            );
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS ix_market_data_lookup 
            ON market_data (ticker, timeframe, timestamp DESC);
        """)
        conn.commit()
    logger.info(f"Database initialized at {db_path}")

def save_candles_batch(df: pd.DataFrame, db_path: str = DB_PATH) -> int:
    """
    Saves a DataFrame of candlestick data to the database.
    Uses INSERT OR REPLACE to seamlessly handle updates without duplicates.
    Expects columns: ['ticker', 'timestamp', 'timeframe', 'open', 'high', 'low', 'close', 'volume']
    """
    if df is None or df.empty:
        return 0

    required_cols = ['ticker', 'timestamp', 'timeframe', 'open', 'high', 'low', 'close', 'volume']
    for col in required_cols:
        if col not in df.columns:
            raise ValueError(f"Missing required column: {col}")

    # Ensure timestamp is string for consistent SQLite ISO storage
    df = df.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')

    records = df[required_cols].to_records(index=False).tolist()

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT OR REPLACE INTO market_data 
            (ticker, timestamp, timeframe, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, records)
        conn.commit()

    return len(records)

def load_candles_from_db(
    ticker: str, 
    timeframe: str = "1D", 
    limit: Optional[int] = None, 
    db_path: str = DB_PATH
) -> pd.DataFrame:
    """
    Loads historical candles for a ticker and timeframe from the database.
    """
    clean_ticker = ticker.upper()
    if not clean_ticker.endswith(".NS"):
        clean_ticker += ".NS"

    query = """
        SELECT ticker, timestamp, timeframe, open, high, low, close, volume
        FROM market_data
        WHERE ticker = ? AND timeframe = ?
        ORDER BY timestamp ASC
    """
    params = [clean_ticker, timeframe.upper()]

    if limit:
        # If limit is specified, fetch the latest N rows
        query = f"""
            SELECT * FROM (
                SELECT ticker, timestamp, timeframe, open, high, low, close, volume
                FROM market_data
                WHERE ticker = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ) ORDER BY timestamp ASC
        """
        params.append(limit)

    with get_connection(db_path) as conn:
        df = pd.read_sql_query(query, conn, params=params)

    if not df.empty:
        df['timestamp'] = pd.to_datetime(df['timestamp'])

    return df

def get_latest_timestamps(timeframe: str, db_path: str = DB_PATH) -> Dict[str, str]:
    """
    Returns a dictionary mapping ticker -> latest timestamp string for a given timeframe.
    e.g. {'RELIANCE.NS': '2026-11-04 13:15:00', ...}
    Fast index-driven query taking ~0.5s for 500 stocks.
    """
    if not os.path.exists(db_path):
        return {}
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT ticker, MAX(timestamp) FROM market_data WHERE timeframe = ? GROUP BY ticker",
            (timeframe.upper(),)
        )
        return dict(cursor.fetchall())

def get_db_stats(db_path: str = DB_PATH) -> Dict:
    """Returns overview statistics of the local database."""
    if not os.path.exists(db_path):
        return {"total_candles": 0, "unique_tickers": 0, "timeframes": []}

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM market_data;")
        total_candles = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(DISTINCT ticker) FROM market_data;")
        unique_tickers = cursor.fetchone()[0]

        cursor.execute("SELECT DISTINCT timeframe FROM market_data;")
        timeframes = [row[0] for row in cursor.fetchall()]

    return {
        "total_candles": total_candles,
        "unique_tickers": unique_tickers,
        "timeframes": timeframes
    }

if __name__ == "__main__":
    init_db()
    stats = get_db_stats()
    print("Database Stats:", stats)


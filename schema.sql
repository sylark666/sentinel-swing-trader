-- v1.0.5
-- Sentinel-Swing (v2.0) PostgreSQL TimescaleDB Schema

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Table for Nifty 500 Historical Data
CREATE TABLE IF NOT EXISTS market_data (
    ticker VARCHAR(20) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    timeframe VARCHAR(5) NOT NULL, -- '1D' or '15M'
    open NUMERIC,
    high NUMERIC,
    low NUMERIC,
    close NUMERIC,
    volume BIGINT,
    UNIQUE(ticker, timestamp, timeframe)
);

-- Convert to hypertable for time-series optimization
SELECT create_hypertable('market_data', 'timestamp', if_not_exists => TRUE);

-- Explicit Indexing for fast backward scanning
CREATE INDEX IF NOT EXISTS ix_market_data_ticker_time ON market_data (ticker, timestamp DESC);

-- Table for Live Open Trades State Machine
CREATE TABLE IF NOT EXISTS active_trades (
    trade_id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    entry_price NUMERIC NOT NULL,
    current_tsl NUMERIC NOT NULL,     -- Dynamic Trailing Stop Loss
    target_price NUMERIC NOT NULL,
    capital_allocated NUMERIC NOT NULL,
    status VARCHAR(20) DEFAULT 'OPEN',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

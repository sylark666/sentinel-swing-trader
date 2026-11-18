import sqlite3

conn = sqlite3.connect('market_data.db')
c = conn.cursor()
c.execute("SELECT DISTINCT ticker FROM market_data WHERE ticker LIKE '%BEES%' OR ticker LIKE '%ETF%' OR ticker LIKE '%NIFTY%' OR ticker LIKE '%BANK%' ORDER BY ticker")
rows = c.fetchall()
print('Found ETF/Index tickers in DB:', [r[0] for r in rows])

# Also check all tickers in DB to see what ETFs are present
c.execute("SELECT DISTINCT ticker FROM market_data ORDER BY ticker")
all_tickers = [r[0] for r in c.fetchall()]
print(f"Total tickers in DB: {len(all_tickers)}")
etf_candidates = [t for t in all_tickers if any(w in t for w in ['BEES', 'ETF', 'GOLD', 'SILVER', 'NIFTY', 'MON100'])]
print("ETF Candidates in DB:", etf_candidates)

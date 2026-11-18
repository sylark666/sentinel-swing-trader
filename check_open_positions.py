import sqlite3
import os
import glob

print("Checking SQLite DBs in current directory:")
for f in glob.glob("*.db"):
    print("Database:", f)
    conn = sqlite3.connect(f)
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [r[0] for r in c.fetchall()]
    print("  Tables:", tables)
    for t in tables:
        c.execute(f"SELECT count(*) FROM {t}")
        cnt = c.fetchone()[0]
        print(f"    Table {t}: {cnt} rows")
        if any(w in t.lower() for w in ['trade', 'pos', 'port', 'open', 'order', 'signal', 'state']):
            c.execute(f"PRAGMA table_info({t})")
            cols = [col[1] for col in c.fetchall()]
            print(f"      Columns: {cols}")
            c.execute(f"SELECT * FROM {t} LIMIT 10")
            rows = c.fetchall()
            for r in rows:
                print(f"      Row: {r}")

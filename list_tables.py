import sqlite3
c = sqlite3.connect("/app/data/app.db")
for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(r)

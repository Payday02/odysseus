import sqlite3
c = sqlite3.connect("/app/data/app.db")
for r in c.execute("SELECT id, name, base_url, supports_tools FROM endpoints"):
    print(r)

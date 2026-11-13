import sqlite3
c = sqlite3.connect("/app/data/app.db")
print("--- columns ---")
for r in c.execute("PRAGMA table_info(model_endpoints)"):
    print(r)
print("--- rows ---")
for r in c.execute("SELECT * FROM model_endpoints"):
    print(r)

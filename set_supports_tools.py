import sqlite3
c = sqlite3.connect("/app/data/app.db")
c.execute("UPDATE model_endpoints SET supports_tools = 1 WHERE id = ?", ("747bd8bc",))
c.commit()
for r in c.execute("SELECT id, name, supports_tools FROM model_endpoints"):
    print(r)

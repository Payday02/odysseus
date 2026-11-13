import sqlite3

# --- 1. Re-apply the model_context.py llama-swap metadata fix ---
f = "/app/src/model_context.py"
old = """    meta = m.get("meta") or m.get("model_extra") or {}
    if isinstance(meta, dict):
        # n_ctx is the actual serving context (set via -c flag in llama.cpp)
        for field in ("n_ctx", "context_length", "context_window", "max_model_len"):
            val = meta.get(field)
            if val and isinstance(val, (int, float)) and val > 0:
                return int(val)
    return None"""
new = """    meta = m.get("meta") or m.get("model_extra") or {}
    if isinstance(meta, dict):
        # n_ctx is the actual serving context (set via -c flag in llama.cpp)
        for field in ("n_ctx", "context_length", "context_window", "max_model_len"):
            val = meta.get(field)
            if val and isinstance(val, (int, float)) and val > 0:
                return int(val)
        # llama-swap nests its metadata one level deeper: meta.llamaswap.contextLength
        llamaswap_meta = meta.get("llamaswap") or {}
        if isinstance(llamaswap_meta, dict):
            val = llamaswap_meta.get("contextLength")
            if val and isinstance(val, (int, float)) and val > 0:
                return int(val)
    return None"""

s = open(f).read()
if new in s:
    print("[model_context.py] already patched, skipping")
elif old in s:
    open(f, "w").write(s.replace(old, new))
    print("[model_context.py] patched OK")
else:
    print("[model_context.py] WARNING: neither old nor new pattern found — "
          "upstream likely changed this function. Manual review needed.")

# --- 2. Ensure local endpoints have tool calling enabled ---
# (This is normally unnecessary since supports_tools lives in the DB volume,
#  not the image — but harmless to re-check, and covers a fresh DB scenario.)
c = sqlite3.connect("/app/data/app.db")
rows = c.execute(
    "SELECT id, name, supports_tools FROM model_endpoints WHERE endpoint_kind = 'local'"
).fetchall()
for row_id, name, current in rows:
    if current != 1:
        c.execute("UPDATE model_endpoints SET supports_tools = 1 WHERE id = ?", (row_id,))
        print(f"[db] enabled supports_tools for {name} ({row_id})")
    else:
        print(f"[db] {name} ({row_id}) already has supports_tools enabled")
c.commit()
c.close()

print("done")

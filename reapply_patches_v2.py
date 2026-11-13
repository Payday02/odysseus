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

# --- 3. Make agent tool output caps env-overridable (constants.py) ---
# (constants.py is CRLF inside the container image; detect the line
#  endings so old/new match byte-for-byte on a fresh pull too.)
f = "/app/src/constants.py"
# newline="" keeps the file's original line endings (CRLF in the container
# image) intact on both read and write; nl is detected from the raw content.
s = open(f, newline="").read()
nl = "\r\n" if "\r\n" in s else "\n"
old = nl.join([
    "MAX_OUTPUT_CHARS = 10_000       # cap for bash/python/web_search/web_fetch output",
    "MAX_READ_CHARS = 20_000         # cap for read_file / document preview",
    "MAX_DIFF_LINES = 400            # cap for edit_file unified-diff display",
])
new = nl.join([
    'MAX_OUTPUT_CHARS = int(os.getenv("ODYSSEUS_MAX_OUTPUT_CHARS", "10000"))  # cap for bash/python/web_search/web_fetch output',
    'MAX_READ_CHARS = int(os.getenv("ODYSSEUS_MAX_READ_CHARS", "20000"))  # cap for read_file / document preview',
    'MAX_DIFF_LINES = int(os.getenv("ODYSSEUS_MAX_DIFF_LINES", "400"))  # cap for edit_file unified-diff display',
])
if new in s:
    print("[constants.py] already patched, skipping")
elif old in s:
    open(f, "w", newline="").write(s.replace(old, new))
    print("[constants.py] patched OK")
else:
    print("[constants.py] WARNING: neither old nor new pattern found — "
          "upstream likely changed these constants. Manual review needed.")

print("done")
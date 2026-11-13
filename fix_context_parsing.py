f = "/app/src/model_context.py"
old = '''    meta = m.get("meta") or m.get("model_extra") or {}
    if isinstance(meta, dict):
        # n_ctx is the actual serving context (set via -c flag in llama.cpp)
        for field in ("n_ctx", "context_length", "context_window", "max_model_len"):
            val = meta.get(field)
            if val and isinstance(val, (int, float)) and val > 0:
                return int(val)
    return None'''
new = '''    meta = m.get("meta") or m.get("model_extra") or {}
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
    return None'''
s = open(f).read()
assert old in s, "pattern not found — check exact whitespace"
open(f, "w").write(s.replace(old, new))
print("patched OK")

f = r"routes\chat_routes.py"
old = """    requested = (raw_value or "").strip()
    if not requested:
        return "", \"\""""
new = """    requested = (raw_value or "").strip()
    if not requested:
        # Local patch: fall back to a configured default workspace when the
        # request/session didn't specify one, so a default survives restarts
        # instead of needing /workspace set every new chat.
        import os
        requested = (os.getenv("ODYSSEUS_DEFAULT_WORKSPACE") or "").strip()
        if not requested:
            return "", \"\""""
s = open(f, encoding="utf-8").read()
assert old in s, "pattern not found -- check exact indentation/quoting"
s = s.replace(old, new)
open(f, "w", encoding="utf-8").write(s)
print("patched OK")
f = "/app/src/llm_core.py"

old1 = "if len(self.recent_tokens) > 96:"
new1 = "if len(self.recent_tokens) > 200:"

old2 = "self.recent_tokens = self.recent_tokens[-96:]"
new2 = "self.recent_tokens = self.recent_tokens[-200:]"

old3 = "if gram_count >= 10:"
new3 = "if gram_count >= 35:"

s = open(f).read()
for old, new, label in [(old1, new1, "window check"), (old2, new2, "window slice"), (old3, new3, "gram threshold")]:
    assert old in s, f"pattern not found: {label}"
    s = s.replace(old, new)

open(f, "w").write(s)
print("patched OK")

f = "/app/src/research_handler.py"
old = "    @staticmethod\n    def _env_timeout(self, name: str, stock: int) -> int:"
new = "    def _env_timeout(self, name: str, stock: int) -> int:"
s = open(f).read()
assert old in s, "pattern not found — check exact whitespace/line break"
s = s.replace(old, new)
open(f, "w").write(s)
print("patched OK")

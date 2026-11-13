f = "/app/src/research_handler.py"
old = "    async def _probe_endpoint(endpoint: str, model: str, headers: dict = None):"
new = "    async def _probe_endpoint(self, endpoint: str, model: str, headers: dict = None):"
s = open(f).read()
assert old in s, "pattern not found"
s = s.replace(old, new)
open(f, "w").write(s)
print("patched OK")

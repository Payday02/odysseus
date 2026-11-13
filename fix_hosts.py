f = "/app/src/agent_loop.py"
old = "\"ollama.com\", \"api.venice.ai\", \"api.kimi.com\","
new = "\"ollama.com\", \"host.docker.internal\", \"localhost\", \"api.venice.ai\", \"api.kimi.com\","
s = open(f).read()
assert old in s, "pattern not found"
open(f, "w").write(s.replace(old, new))
print("patched OK")

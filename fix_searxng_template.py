import re
p = r"config\searxng\settings.yml"
raw = open(p, encoding="utf-8", newline="").read()
nl = "\r\n" if "\r\n" in raw else "\n"
block = re.search(r"^engines:[ \t]*\r?\n[ \t]+keep_only:[ \t]*\r?\n((?:[ \t]+-[ \t]*\S.*(?:\r?\n|$))+)", raw, re.M)
assert block, "top-level engines/keep_only block not found"
names = [ln.strip()[1:].strip() for ln in block.group(1).splitlines() if ln.strip()]
raw = raw[:block.start()] + raw[block.end():]
raw = raw.rstrip() + nl
assert "use_default_settings: true" in raw, "use_default_settings line not found"
head = nl.join(["use_default_settings:", "  engines:", "    keep_only:"] + ["      - " + n for n in names])
raw = raw.replace("use_default_settings: true", head, 1)
open(p, "w", encoding="utf-8", newline="").write(raw)
print("fixed:", names)
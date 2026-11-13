f = "data/patches/patch_odysseus.py"
s = open(f, encoding="utf-8").read()

anchor = ("""    elif "def _env_timeout" not in s:
        return "warn", "research_handler.py: no stock timeout sites found -- upstream changed them? manual review needed"

    # -- (e) chat_processor.py: web-search query extraction -------------------""")

new_block = '''    elif "def _env_timeout" not in s:
        return "warn", "research_handler.py: no stock timeout sites found -- upstream changed them? manual review needed"

    # -- (d2) research_handler.py: repair a half-applied earlier pass -- missing
    #    `self` on _probe_endpoint, or a stray @staticmethod left on _env_timeout
    #    despite it declaring self. Runs regardless of (d)'s own outcome, since a
    #    checkout can carry this corruption even when (d) reports "skip".
    p = root / "src" / "research_handler.py"
    s, nl = _lines_of(p)
    d2_changed = False
    old_sig = "    async def _probe_endpoint(endpoint: str, model: str, headers: dict = None):"
    new_sig = "    async def _probe_endpoint(self, endpoint: str, model: str, headers: dict = None):"
    if old_sig in s:
        if s.count(old_sig) != 1:
            return "warn", f"research_handler.py: _probe_endpoint anchor count={s.count(old_sig)} -- manual review needed"
        s = s.replace(old_sig, new_sig, 1)
        d2_changed = True
    old_static = "    @staticmethod\\n    def _env_timeout(self, name: str, stock: int) -> int:"
    new_static = "    def _env_timeout(self, name: str, stock: int) -> int:"
    if old_static in s:
        if s.count(old_static) != 1:
            return "warn", f"research_handler.py: _env_timeout staticmethod anchor count={s.count(old_static)} -- manual review needed"
        s = s.replace(old_static, new_static, 1)
        d2_changed = True
    if d2_changed:
        _write(p, s, nl)
        changed.append("research_handler.py (repair: _probe_endpoint self / _env_timeout staticmethod)")

    # -- (e) chat_processor.py: web-search query extraction -------------------'''

assert anchor in s, "anchor not found -- paste the exact (d)/(e) boundary lines again"
s = s.replace(anchor, new_block)
open(f, "w", encoding="utf-8").write(s)
print("patch_timeouts: added self-healing (d2) block for research_handler.py")
#!/usr/bin/env python3
"""
patch_odysseus.py -- consolidated local patches for the odysseus Docker setup.

Run on the Docker HOST, from anywhere inside the odysseus checkout, BEFORE
`docker compose up` (ideally right after `docker compose down` / a git pull):

    python3 patch_odysseus.py                # patch files + optional deps + searxng + rebuild image
    python3 patch_odysseus.py --no-build     # patch files only (you'll rebuild yourself)
    python3 patch_odysseus.py --force-build  # rebuild even if no source file changed
    python3 patch_odysseus.py --no-docker    # files only; print manual steps for the rest

Exit code 0 = everything in place (or already patched).
Exit code 1 = at least one section needs manual review (it prints why).

Sections (all idempotent -- check-before-patch, safe to re-run any number of
times, CRLF/LF aware so a Windows checkout can't break matching):

  1. src/constants.py            MAX_OUTPUT_CHARS / MAX_READ_CHARS / MAX_DIFF_LINES
                                 become env-overridable (ODYSSEUS_MAX_*). The stock
                                 compose setup bakes src/ into the image (no bind
                                 mount), so the final section rebuilds the image and the
                                 change then survives `compose down` + `up` forever.
  2. src/model_context.py        recognize llama-swap's nested contextLength
                                 metadata (meta.llamaswap.contextLength).
  3. docker-compose.yml          forward the three ODYSSEUS_MAX_* vars from the
                                 host .env into the app container (compose only
                                 passes variables that are listed explicitly).
  4. docker-compose.yml          enable the Dockerfile's INSTALL_OPTIONAL
                                 build-arg so the optional packages
                                 (requirements-optional.txt: faster-whisper, ddgs,
                                 PyMuPDF, markitdown, kokoro/soundfile on
                                 python<3.13) are baked into the image at build
                                 time -- the supported way, since a runtime
                                 `pip install` in the container is wiped on the
                                 next rebuild (which the rebuild does anyway).
  5. config/searxng/settings.yml searXNG engine whitelist in the TEMPLATE:
                                 keep_only = google, wikipedia, duckduckgo, bing
                                 (json format already present; the
                                 __SEARXNG_SECRET__ placeholder is preserved).
  6. data/app.db                 tool calling enabled (supports_tools) for local
                                 endpoints (covers fresh-DB scenarios).
  7. searxng volume              remove the already-rendered live settings.yml so
                                 searxng's entrypoint re-renders it from the
                                 patched template at next boot (uses `docker run`;
                                 prints the manual command if docker CLI is gone).
  8. static/style.css            constrain markdown images in chat bubbles to the
                                 bubble width (.msg .body img { max-width: 100%;
                                 height: auto; }) so wide rendered images
                                 (diagrams, plots, screenshots) don't overflow
                                 and get clipped by the bubble.
  9. src/llm_core.py             widen the repetition guard (token window
                                 96 -> 200, repeated 4-gram 10 -> 35, and make
                                 the remaining thresholds env-overridable with
                                 wider defaults: same-run 28 ->
                                 ODYSSEUS_REPEAT_SAME_RUN=150, dominant
                                 60/0.78 -> ODYSSEUS_REPEAT_DOM_COUNT/_RATIO
                                 =150/0.92, gram -> ODYSSEUS_REPEAT_GRAM_COUNT
                                 =100, plus single-word 4-grams excluded from
                                 the phrase check) so table-heavy output
                                 (netlists, BOMs) isn't cut off as "looping".
  10. src/tool_index.py          always-on tool selection: the env var
                                 ODYSSEUS_ALWAYS_TOOLS (comma-separated tool
                                 names, default "bash") is unioned into RAG's
                                 ALWAYS_AVAILABLE set, so the listed tools
                                 (shell etc.) are sent to the model every turn
                                 instead of only when retrieval picks them.
  11. src/chat_processor.py +    user-defined global rules injected into
      data/settings.json         EVERY prompt: adds `global_rules` (list of
                                 strings) to settings.json and renders it as a
                                 static system message in
                                 build_context_preface -- the single choke
                                 point for normal chat AND agent mode. Edit
                                 the list (2s settings cache) and it applies
                                 from the next message; no restart, no rebuild.
  12. vision tool (7 source files) a `vision` agent tool: sends an image on
                                 disk to the configured vision model and
                                 returns its description. Reuses the app's
                                 proven pipeline (_resolve_vl_model + the
                                 vision fallback chain) -- no new ML plumbing.
                                 Optional no_thinking (skips model reasoning,
                                 ~30% faster) and max_tokens (length cap)
                                 params. Touched: document_processor.py
                                 (prompt/no_thinking/max_tokens params),
                                 NEW agent_tools/vision_tools.py,
                                 agent_tools/__init__.py, tool_schemas.py,
                                 agent_loop.py (prompt section + upload seed),
                                 tool_index.py (retrieval), tool_security.py
                                 (plan-mode allowlist).
  13. LLM call timeouts (7 files) the stock non-streaming LLM call sites
                                 have tiny fixed read budgets (probe 15s,
                                 plan 30s, compaction 30s, research
                                 synthesis 180s, agent salvage 60s) that
                                 kill long generations on slow local
                                 models -- a timed-out compaction aborts
                                 the whole chat, a timed-out synthesis
                                 degrades research to raw findings. Every
                                 site becomes _env_timeout('ODYSSEUS_
                                 TIMEOUT_*', stock) with env override;
                                 research max_time default 300 -> 600,
                                 agent round-deadline floor 1200 -> 3600.
  14. vision timeout budget    the VL analysis call in
                                 document_processor.py had a hardcoded
                                 timeout=120 -- a thinking VL model
                                 (30-60s of reasoning before the first
                                 description token) blows it on long
                                 images / busy endpoints, surfacing as
                                 "VL model unavailable - image not
                                 analyzed". Now _env_timeout('ODYSSEUS_
                                 TIMEOUT_VISION', 300), env-overridable,
                                 forwarded via docker-compose.yml.
  15. reload-freeze fix (3 files)  reopening a chat whose run is still
                                 in flight froze the tab: the per-run SSE
                                 replay buffer was unbounded and the client
                                 re-rendered the whole accumulated round on
                                 every replayed token (O(n^2) markdown).
                                 Now: buffer capped at 2000 events +
                                 replay_complete marker (agent_runs.py),
                                 client fast-forwards replayed deltas with
                                 one full render (chat.js), SQLite runs in
                                 WAL + busy_timeout so history reads don't
                                 stall on agent writes (core/database.py).
  16. prefill keepalive (5 files)  large-context prefills are SILENT for
                                 minutes (700-800s measured at 236k tokens);
                                 the browser's 6-minute agent watchdog
                                 (RESEARCH_TIMEOUT_MS=360000) then aborts the
                                 stream even though the server round would
                                 finish (server rounds DO complete at
                                 1200s+). Also, the server emitted
                                 agent_prep early but the route event chain
                                 had no catch-all and dropped it. Now:
                                 stream_llm emits a stream_keepalive data
                                 event after N silent seconds (feeder-task +
                                 queue, since cancelling a wait on
                                 __anext__ closes the inner generator) via
                                 ODYSSEUS_PREFILL_KEEPALIVE_S (default 30,
                                 0=off); the route chains forward unknown
                                 data events (catch-all) so agent_prep and
                                 keepalives reach the browser; the agent
                                 round loop forwards keepalives; the browser
                                 treats a keepalive as activity (clears the
                                 watchdog) and shows a pre-filling hint.
                                 Touched: llm_core.py, chat_routes.py,
                                 agent_loop.py, static/js/chat.js,
                                 docker-compose.yml.
  17. context-overflow recovery (3 files) a router (llama-swap) can
                                 report a catalog contextLength (1M) the
                                 serving instance does not run with (262k
                                 slot); the first round whose REAL prompt
                                 exceeds the real limit 400s and the run
                                 ends. Also estimate_tokens undercounts
                                 dense code/table content ~40-70%, so
                                 trim/compact gates fire late. Now:
                                 model_context.py learns the real n_ctx
                                 (ground truth) + the estimate->real ratio
                                 from each overflow error and serves the
                                 learned value over the catalog;
                                 agent_loop.py parses n_ctx/n_prompt_tokens
                                 out of the 400, trims the live history to
                                 85% of the real window (in honest tokens)
                                 and retries the round; the soft-trim
                                 budget and maybe_compact usage scale by
                                 the learned ratio. Touched:
                                 model_context.py, agent_loop.py,
                                 context_compactor.py.
  19. KV-exhaustion guard (1 file) a quieter cousin of section 17:
                                 when a prompt FITS but input + generated
                                 tokens (mostly thinking) fill the real
                                 context window MID-generation, llama.cpp
                                 stops silently -- clean stream end, no
                                 400 -- and the stock loop ends the run
                                 (measured: 253359 in + 8785 out = 262144
                                 = exactly the KV cache size). Now
                                 agent_loop.py (a) learns the
                                 estimate->real token ratio from every
                                 round's real backend usage (continuous,
                                 not only on 400s) and (b) on the
                                 no-tool-call path detects usage >=98% of
                                 the real window with no visible output,
                                 trims the live history to 85% of the
                                 window (honest tokens) and continues
                                 (max 3 per run).
  20. degeneration guard (2 files) thinking models can degenerate:
                                         80 minutes of pure thinking (no visible
                                         output) until the stock round deadline ends
                                         the run, and the intent supervisor nags the
                                         model to 'call the actual tool' after the
                                         loop-breaker removed the tools. Adds a
                                         per-round thinking-stall cap
                                         (ODYSSEUS_AGENT_THINKING_STALL_S, default 600,
                                         0 = off) that cuts the round short and
                                         retries with a corrective nudge (max 2
                                         retrims per run), skips the intent supervisor
                                         on force-answer rounds, and names the
                                         placeholder-tool-call degeneration in the
                                         loop-breaker message. docker-compose.yml gets
                                         the env passthrough.
  21. docker compose build       rebuild the odysseus image so
                                 1+2+4+8+9+10+11+12+13+14+15+16+17+19 are
                                 baked in (passes --build-arg
                                 INSTALL_OPTIONAL=true).
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- helpers --

def read_raw(p):
    with open(p, "rb") as f:
        return f.read()


def write_raw(p, b):
    with open(p, "wb") as f:
        f.write(b)


def find_root():
    here = Path(__file__).resolve().parent
    for start in (Path.cwd(), here):
        for cand in [start, *start.parents]:
            if (cand / "docker-compose.yml").is_file() or (cand / "docker-compose.yaml").is_file():
                return cand
    raise SystemExit("ERROR: no docker-compose.yml found above cwd or the script location.\n"
                     "       Run this from inside the odysseus checkout.")


def compose_path(root):
    for name in ("docker-compose.yml", "docker-compose.yaml"):
        p = root / name
        if p.is_file():
            return p
    return None


def sh(cmd, **kw):
    # bytes->text with UTF-8 + replacement: docker build logs occasionally emit
    # raw bytes (progress/spinner glyphs) that crash the cp1252 reader thread
    # on Windows, so decode leniently instead of locale-default text=True.
    return subprocess.run(cmd, capture_output=True,
                           encoding="utf-8", errors="replace", **kw)


# --------------------------------------------- 1. src/constants.py --

CONST_LINES = [
    ("MAX_OUTPUT_CHARS", "ODYSSEUS_MAX_OUTPUT_CHARS", "10000"),
    ("MAX_READ_CHARS",   "ODYSSEUS_MAX_READ_CHARS",   "20000"),
    ("MAX_DIFF_LINES",   "ODYSSEUS_MAX_DIFF_LINES",   "400"),
]


def patch_constants(path):
    """Make the three output caps env-overridable, preserving comments and the
    file's line endings. Returns (status, detail).

    If upstream changed the literal default, that literal is kept as the env
    default (behavior-preserving); if the line is a non-literal expression or
    missing, it warns for manual review."""
    if not path.is_file():
        return "warn", f"{path} not found"
    s = read_raw(path).decode("utf-8")
    lines = s.splitlines(keepends=True)
    statuses = []
    for name, env, default in CONST_LINES:
        found = False
        for i, ln in enumerate(lines):
            body = ln.rstrip("\r\n")
            m = re.match(rf"^{name}\s*=\s*(.+)$", body)
            if not m:
                continue
            found = True
            expr = m.group(1).strip()
            cm = re.search(r"\s+#.*$", expr)
            comment = ("  " + cm.group(0).strip()) if cm else ""
            target = expr[: cm.start()].strip() if cm else expr
            if re.fullmatch(rf'int\(os\.getenv\(["\']{env}["\'],\s*["\']\d+["\']\)\)', target):
                statuses.append(("skip", name))
                break
            lit = re.fullmatch(r"(\d[\d_]*)", target)
            if not lit:
                statuses.append(("warn", f"{name} = {target!r} (unexpected expression)"))
                break
            newline = ln[len(body):]
            lines[i] = f'{name} = int(os.getenv("{env}", "{lit.group(1).replace("_", "")}")){comment}{newline}'
            statuses.append(("patched", name))
            break
        if not found:
            statuses.append(("warn", f"{name} line not found"))
    if any(st == "warn" for st, _ in statuses):
        return "warn", "; ".join(d for st, d in statuses if st == "warn")
    if any(st == "patched" for st, _ in statuses):
        write_raw(path, "".join(lines).encode("utf-8"))
        return "patched", ""
    return "skip", ""


# ------------------------------------------ 2. src/model_context.py --

MC_OLD = """    meta = m.get("meta") or m.get("model_extra") or {}
    if isinstance(meta, dict):
        # n_ctx is the actual serving context (set via -c flag in llama.cpp)
        for field in ("n_ctx", "context_length", "context_window", "max_model_len"):
            val = meta.get(field)
            if val and isinstance(val, (int, float)) and val > 0:
                return int(val)
    return None"""

MC_EXTRA = """        # llama-swap nests its metadata one level deeper: meta.llamaswap.contextLength
        llamaswap_meta = meta.get("llamaswap") or {}
        if isinstance(llamaswap_meta, dict):
            val = llamaswap_meta.get("contextLength")
            if val and isinstance(val, (int, float)) and val > 0:
                return int(val)
"""

MC_NEW = MC_OLD.replace("    return None", MC_EXTRA + "    return None")


def patch_model_context(path):
    """Add llama-swap contextLength recognition. CRLF/LF tolerant."""
    if not path.is_file():
        return "warn", f"{path} not found"
    s = read_raw(path).decode("utf-8")
    for nl in ("\n", "\r\n"):
        old = MC_OLD.replace("\n", nl)
        new = MC_NEW.replace("\n", nl)
        if new in s:
            return "skip", ""
        if old in s:
            write_raw(path, s.replace(old, new, 1).encode("utf-8"))
            return "patched", ""
    return "warn", "neither stock nor patched pattern found -- upstream likely changed this function; manual review needed"


# ------------------------------------------- 3. docker-compose.yml --

COMPOSE_ANCHOR = "      - ODYSSEUS_TTS_CACHE_MAX_BYTES=${ODYSSEUS_TTS_CACHE_MAX_BYTES}"
COMPOSE_NEWLINES = [
    "      - ODYSSEUS_MAX_OUTPUT_CHARS=${ODYSSEUS_MAX_OUTPUT_CHARS:-10000}",
    "      - ODYSSEUS_MAX_READ_CHARS=${ODYSSEUS_MAX_READ_CHARS:-20000}",
    "      - ODYSSEUS_MAX_DIFF_LINES=${ODYSSEUS_MAX_DIFF_LINES:-400}",
    "      - ODYSSEUS_ALWAYS_TOOLS=${ODYSSEUS_ALWAYS_TOOLS:-bash}",
    "      - ODYSSEUS_REPEAT_SAME_RUN=${ODYSSEUS_REPEAT_SAME_RUN:-150}",
    "      - ODYSSEUS_REPEAT_DOM_COUNT=${ODYSSEUS_REPEAT_DOM_COUNT:-150}",
    "      - ODYSSEUS_REPEAT_DOM_RATIO=${ODYSSEUS_REPEAT_DOM_RATIO:-0.92}",
    "      - ODYSSEUS_REPEAT_GRAM_COUNT=${ODYSSEUS_REPEAT_GRAM_COUNT:-100}",
]


def patch_compose(path):
    """Forward the ODYSSEUS_MAX_* / ODYSSEUS_ALWAYS_TOOLS / ODYSSEUS_REPEAT_*
    vars into the app container. The ${VAR:-default} fallbacks match the code
    defaults, so leaving them out of .env is a safe no-op.

    Three stages so a partially-patched file converges: stage 1 anchors on the
    stock ODYSSEUS_TTS_CACHE_MAX_BYTES line; stage 2 anchors on the
    ODYSSEUS_MAX_DIFF_LINES line that stage 1 itself inserted (covers
    checkouts patched by an older version of this script); stage 3 anchors on
    the ODYSSEUS_ALWAYS_TOOLS line that stage 2 inserted and appends the four
    repetition-guard tuning vars."""
    if not path.is_file():
        return "warn", f"{path} not found"
    s = read_raw(path).decode("utf-8")
    changed = []
    # Stage 1: the three stock MAX_* passthroughs
    if "ODYSSEUS_MAX_OUTPUT_CHARS" not in s:
        for nl in ("\n", "\r\n"):
            anchor = COMPOSE_ANCHOR + nl
            if anchor in s:
                s = s.replace(anchor, anchor + nl.join(COMPOSE_NEWLINES[:3]) + nl, 1)
                changed.append("MAX_* passthrough")
                break
        else:
            return "warn", "anchor line (ODYSSEUS_TTS_CACHE_MAX_BYTES env entry) not found -- upstream likely changed compose; manual review needed"
    # Stage 2: the always-on tools selection var (anchor = stage 1's last line)
    if "ODYSSEUS_ALWAYS_TOOLS" not in s:
        for nl in ("\n", "\r\n"):
            anchor = COMPOSE_NEWLINES[2] + nl
            if anchor in s:
                s = s.replace(anchor, anchor + COMPOSE_NEWLINES[3] + nl, 1)
                changed.append("ALWAYS_TOOLS passthrough")
                break
        else:
            return "warn", ("ODYSSEUS_MAX_* passthrough present but its anchor line "
                            "(ODYSSEUS_MAX_DIFF_LINES env entry) not found -- "
                            "upstream likely changed compose; manual review needed")
    # Stage 3: the repetition-guard tuning vars (anchor = stage 2's line)
    if "ODYSSEUS_REPEAT_SAME_RUN" not in s:
        for nl in ("\n", "\r\n"):
            anchor = COMPOSE_NEWLINES[3] + nl
            if anchor in s:
                s = s.replace(anchor, anchor + nl.join(COMPOSE_NEWLINES[4:]) + nl, 1)
                changed.append("REPEAT_* passthrough")
                break
        else:
            return "warn", ("ODYSSEUS_ALWAYS_TOOLS passthrough present but its "
                            "anchor line not found -- upstream likely changed "
                            "compose; manual review needed")
    if changed:
        write_raw(path, s.encode("utf-8"))
        return "patched", ", ".join(changed)
    return "skip", "already present"


# ------------------- 4. docker-compose.yml build args (optional deps) --

def patch_compose_build_args(path):
    """Turn on the Dockerfile's INSTALL_OPTIONAL build-arg.

    The stock Dockerfile already supports this:

        ARG INSTALL_OPTIONAL=false
        ...
        RUN pip install --no-cache-dir -r requirements.txt \
            && if [ "$INSTALL_OPTIONAL" = "true" ]; then pip install --no-cache-dir -r requirements-optional.txt; fi

    A runtime `docker exec ... pip install` also works, but the packages live
    in the container's writable layer and vanish on the next image rebuild --
    and section 10 rebuilds after every pull, so that path is not durable.
    Baking them in via the build-arg is the supported way; later rebuilds
    reuse the cached pip layer, so it costs nothing extra."""
    if not path.is_file():
        return "warn", f"{path} not found"
    s = read_raw(path).decode("utf-8")
    if "INSTALL_OPTIONAL" in s:
        return "skip", "INSTALL_OPTIONAL already present"
    for nl in ("\n", "\r\n"):
        m = re.search(r"^( *)build: \." + re.escape(nl), s, re.M)
        if m:
            ind = m.group(1)
            c1, c2 = ind + "  ", ind + "    "
            block = (f"{ind}build:{nl}"
                     f"{c1}context: .{nl}"
                     f"{c1}args:{nl}"
                     f'{c2}INSTALL_OPTIONAL: "true"{nl}')
            write_raw(path, (s[:m.start()] + block + s[m.end():]).encode("utf-8"))
            return "patched", ""
    if re.search(r"^\s*build:\s*$", s, re.M):
        return "warn", "compose has a `build:` block (not plain `build: .`) -- add its args manually: args: [INSTALL_OPTIONAL: \"true\"]"
    return "warn", "no `build: .` line found in compose -- manual review needed"


# -------------------------------------- 5. searxng settings template --

SEARX_STOCK = [
    "use_default_settings: true",
    "",
    "server:",
    '  secret_key: "__SEARXNG_SECRET__"',
    "",
    "search:",
    "  formats:",
    "    - html",
    "    - json",
]
SEARX_KEEP_ONLY = [
    "engines:",
    "  keep_only:",
    "    - google",
    "    - wikipedia",
    "    - duckduckgo",
    "    - bing",
]


def patch_searxng_template(path):
    """Add the engine whitelist to the template (source of truth; the live
    file in the searxng-data volume is re-rendered from it at boot)."""
    if not path.is_file():
        return "warn", f"{path} not found"
    text = read_raw(path).decode("utf-8")
    if "keep_only" in text:
        return "skip", "keep_only already present"
    nl = "\r\n" if "\r\n" in text else "\n"
    old_block = nl.join(SEARX_STOCK)
    if old_block in text:
        new_block = nl.join(SEARX_STOCK + [""] + SEARX_KEEP_ONLY)
        write_raw(path, text.replace(old_block, new_block, 1).encode("utf-8"))
        return "patched", ""
    if re.search(r"^engines:\s*$", text, re.M):
        return "warn", "file has an engines: block but no keep_only (hand-edited?) -- manual review needed"
    return "warn", "stock template content not found -- upstream likely changed it; manual review needed"


# ------------------------------------------- 6. data/app.db --

def patch_db(root):
    db = root / "data" / "app.db"
    if not db.is_file():
        return "skip", f"{db} not present yet (fresh install) -- nothing to do"
    c = sqlite3.connect(db)
    try:
        rows = c.execute(
            "SELECT id, name, supports_tools FROM model_endpoints WHERE endpoint_kind = 'local'"
        ).fetchall()
    except sqlite3.OperationalError as e:
        return "warn", f"DB unreadable ({e})"
    finally:
        c.close()
    changed = 0
    for _id, name, current in rows:
        if current != 1:
            c = sqlite3.connect(db)
            c.execute("UPDATE model_endpoints SET supports_tools = 1 WHERE id = ?", (_id,))
            c.commit()
            c.close()
            changed += 1
    if rows and changed:
        return "patched", f"supports_tools enabled for {changed}/{len(rows)} local endpoint(s)"
    if rows:
        return "skip", f"{len(rows)} local endpoint(s) already OK"
    return "skip", "no local endpoints in DB"


# --------------------------------- 7. remove rendered searxng settings --

def remove_live_searxng(root, no_docker):
    if no_docker:
        return "manual", "--no-docker given"
    if not shutil.which("docker"):
        return "manual", "docker CLI not found on this host"
    out = sh(["docker", "volume", "ls", "--format", "{{.Name}}"])
    vols = [v for v in out.stdout.split() if "searxng" in v.lower()]
    if not vols:
        return "none", "no searxng volume yet (fresh install?) -- first `up` will render the patched template"
    vol = vols[0]
    comp = compose_path(root)
    img = None
    if comp is not None:
        for m in re.finditer(r"image:\s*['\"]?([^\s'\"]+)['\"]?", comp.read_text()):
            if "searxng" in m.group(1).lower():
                img = m.group(1)
                break
    if not img:
        return "manual", "could not determine searxng image from compose"
    r = sh(["docker", "run", "--rm", "--entrypoint", "sh", "-v", f"{vol}:/etc/searxng", img, "-c",
            "if [ -f /etc/searxng/settings.yml ]; then rm -f /etc/searxng/settings.yml && echo removed; else echo absent; fi"])
    if r.returncode == 0:
        return "patched", f"live file '{(r.stdout or '').strip()}' (volume {vol})"
    return "manual", f"docker run failed: {(r.stderr or r.stdout).strip()[:200]}"


# ------------------------------------------- 8. static/style.css --

STYLE_CSS_ANCHOR = """    .msg .body > * {
      margin-top: 8px;
      margin-bottom: 8px;
    }"""
STYLE_CSS_RULE = """    /* Markdown images: constrain to bubble width so wide images (e.g.
       rendered diagrams) don't overflow and get clipped by .msg .body */
    .msg .body img {
      max-width: 100%;
      height: auto;
    }"""


def patch_style_css(path):
    """Constrain markdown images in chat bubbles to the bubble width.

    The chat client sanitizes markdown image URLs down to http(s) only, and
    the app's own /api/generated-image/ route is relative, so images posted
    in chat always land inside .msg .body. Without a width cap on <img> there,
    wide renders (diagrams, plots, screenshots) overflow the bubble and get
    clipped by its overflow:hidden. Idempotent, CRLF/LF tolerant."""
    if not path.is_file():
        return "warn", f"{path} not found"
    s = read_raw(path).decode("utf-8")
    if ".msg .body img" in s:
        return "skip", "already present"
    for nl in ("\n", "\r\n"):
        rule = STYLE_CSS_RULE.replace("\n", nl)
        anchor = STYLE_CSS_ANCHOR.replace("\n", nl)
        if rule in s:
            return "skip", "already present"
        if anchor in s:
            write_raw(path, s.replace(anchor, anchor + nl + rule, 1).encode("utf-8"))
            return "patched", ""
    return "warn", "anchor block (.msg .body > * margins) not found -- upstream likely restyled the chat CSS; manual review needed"


# ------------------------------------------- 9. src/llm_core.py --

# Stage A: plain literal widening (token window 96 -> 200, gram 10 -> 35)
# from the earlier fix.
REPEAT_STAGE_A = [
    ("if len(self.recent_tokens) > 96:",
     "if len(self.recent_tokens) > 200:", "window check"),
    ("self.recent_tokens = self.recent_tokens[-96:]",
     "self.recent_tokens = self.recent_tokens[-200:]", "window slice"),
    ("if gram_count >= 10:",
     "if gram_count >= 35:", "gram threshold"),
]

# Stage B: the remaining hardcoded thresholds become env-overridable with
# wider defaults so table-heavy output (netlists, BOMs, pin tables -- long
# runs of short repeated words like "net"/"gnd"/"vcc") is not cut off as
# "looping". Entry shape: (label, stock check regex -- group 1 = indent,
# group 2 = trailing CR; idempotency local var names; env lines; new check).
REPEAT_STAGE_B = [
    ("same-run threshold",
     r"^( +)if self\.same_run >= \d+ and (self\.total_chars >= \d+):(\r?)$",
     ("same_run_cap",),
     ['{ind}same_run_cap = int(os.getenv("ODYSSEUS_REPEAT_SAME_RUN", "150"))'],
     "{ind}if self.same_run >= same_run_cap and {clause}:"),
    ("dominant-token check",
     r"^( +)if count >= \d+ and count / max\(len\(self\.recent_tokens\), 1\) >= 0\.\d+:(\r?)$",
     ("dom_count", "dom_ratio"),
     ['{ind}dom_count = int(os.getenv("ODYSSEUS_REPEAT_DOM_COUNT", "150"))',
      '{ind}dom_ratio = float(os.getenv("ODYSSEUS_REPEAT_DOM_RATIO", "0.92"))'],
     "{ind}if count >= dom_count and count / max(len(self.recent_tokens), 1) >= dom_ratio:"),
    ("gram threshold (env)",
     r"^( +)if gram_count >= \d+:(\r?)$",
     ("gram_cap",),
     ['{ind}gram_cap = int(os.getenv("ODYSSEUS_REPEAT_GRAM_COUNT", "100"))'],
     "{ind}if gram_count >= gram_cap:"),
    # Table-safe phrase check: single-word 4-grams ("net net net net") are the
    # same-run check's job; counting them here aborted table output at far
    # lower effective thresholds. Genuine phrase loops keep 2+ distinct words.
    ("gram single-word exclusion",
     r"^( +)grams = \[tuple\(self\.recent_tokens\[i:i \+ 4\]\) for i in range\(0, len\(self\.recent_tokens\) - 3\)\](\r?)$",
     ("len(set(g)) > 1",),
     ['{ind}grams = [tuple(self.recent_tokens[i:i + 4]) for i in range(0, len(self.recent_tokens) - 3)]',
      '{ind}# table-safe: single-word runs belong to the same-run check, not the phrase check'],
     "{ind}grams = [g for g in grams if len(set(g)) > 1]"),
]


def _apply_env_threshold(s, label, stock_re, locals_, env_lines, new_check):
    """Insert env-threshold lines above a stock check line and swap in the
    new check. Returns (s, status, detail) with status skip/patched/warn."""
    if all(v in s for v in locals_):
        return s, "skip", "already patched"
    m = re.search(stock_re, s, re.M)
    if not m:
        return s, "warn", (f"{label}: stock check line not found -- upstream "
                           "likely changed this code; manual review needed")
    ind = m.group(1)
    cr = (m.group(m.re.groups) or "") if m.re.groups >= 2 else ""
    nl = cr + "\n" if cr else "\n"
    clause = m.group(2) if "{clause}" in new_check else ""
    def fill(tmpl):
        return tmpl.replace("{ind}", ind).replace("{clause}", clause)
    inserted = [fill(t) for t in env_lines]
    inserted.append(fill(new_check))
    replacement = nl.join(inserted) + cr
    return s.replace(m.group(0), replacement, 1), "patched", ""


def patch_llm_core(path):
    """Widen the repetition guard in src/llm_core.py so long table output is
    not cut off as "looping". Stage A widens literals (window 96 -> 200,
    gram 10 -> 35); Stage B makes the remaining checks env-overridable with
    wider defaults: same-run 28 -> ODYSSEUS_REPEAT_SAME_RUN (150), dominant
    60/0.78 -> ODYSSEUS_REPEAT_DOM_COUNT/_DOM_RATIO (150/0.92), gram 35 ->
    ODYSSEUS_REPEAT_GRAM_COUNT (100) -- and excludes single-word 4-grams
    from the phrase check (those are the same-run check's job; counting
    them aborted table output at ~13-token runs).

    Idempotent per check; warns for manual review if upstream moved a stock
    line. `os` is already imported by stock llm_core.py. The env vars are
    read at import time, so retuning = edit .env + docker compose up -d."""
    if not path.is_file():
        return "warn", f"{path} not found"
    s = read_raw(path).decode("utf-8")
    results = []
    for old, new, label in REPEAT_STAGE_A:
        if new in s:
            continue
        if "ODYSSEUS_REPEAT_GRAM_COUNT" in s and "gram" in label:
            continue  # Stage B already env-ified this literal
        n = s.count(old)
        if n != 1:
            return "warn", (f"{label}: pattern {old!r} found {n} time(s), expected 1 -- "
                            "upstream likely changed this code; manual review needed")
        s = s.replace(old, new)
        results.append(label)
    for label, stock_re, locals_, env_lines, new_check in REPEAT_STAGE_B:
        s, st, detail = _apply_env_threshold(s, label, stock_re, locals_, env_lines, new_check)
        if st == "warn":
            return "warn", detail
        if st == "patched":
            results.append(label)
    if results:
        write_raw(path, s.encode("utf-8"))
        return "patched", ", ".join(results)
    return "skip", "already patched"


# ------------------------------------ 10. src/tool_index.py (always-on tools) --

TOOL_INDEX_OLD = (
    "ALWAYS_AVAILABLE = frozenset({\n"
    "    # Memory is ambient — \"remember this\" can follow any message regardless\n"
    "    # of topic. Without this, RAG drops it and the agent falls back to\n"
    "    # app_api /api/memory/add which fails with 422 on first attempt.\n"
    "    \"manage_memory\",\n"
    "    # Ask the user a multiple-choice question for a decision/clarification.\n"
    "    # Always reachable so the agent can pause and ask at any point.\n"
    "    \"ask_user\",\n"
    "    # Write back to the active plan (tick steps done / revise) during execution.\n"
    "    \"update_plan\",\n"
    "})\n"
)

TOOL_INDEX_NEW = (
    "ALWAYS_AVAILABLE = frozenset({\n"
    "    # Memory is ambient — \"remember this\" can follow any message regardless\n"
    "    # of topic. Without this, RAG drops it and the agent falls back to\n"
    "    # app_api /api/memory/add which fails with 422 on first attempt.\n"
    "    \"manage_memory\",\n"
    "    # Ask the user a multiple-choice question for a decision/clarification.\n"
    "    # Always reachable so the agent can pause and ask at any point.\n"
    "    \"ask_user\",\n"
    "    # Write back to the active plan (tick steps done / revise) during execution.\n"
    "    \"update_plan\",\n"
    "})\n"
    "# Local patch: always-on tools from the environment. Comma-separated\n"
    "# tool names (e.g. ODYSSEUS_ALWAYS_TOOLS=bash,python) are UNIONED into\n"
    "# ALWAYS_AVAILABLE so they are sent to the model every turn instead of\n"
    "# only when retrieval happens to pick them. Unknown names are simply\n"
    "# ignored (prompt/schema assembly filters to registered tools).\n"
    "# Default \"bash\" keeps the shell tool always accessible. The env var is\n"
    "# read at import time, so change it in .env and `docker compose up -d`\n"
    "# (no image rebuild needed for a different selection).\n"
    "_ALWAYS_FROM_ENV = os.getenv(\"ODYSSEUS_ALWAYS_TOOLS\", \"bash\")\n"
    "_env_always = frozenset(\n"
    "    t.strip() for t in _ALWAYS_FROM_ENV.split(\",\") if t.strip()\n"
    ")\n"
    "ALWAYS_AVAILABLE = ALWAYS_AVAILABLE | _env_always\n"
)


def patch_tool_index(path):
    """Make the always-on tool set selectable via ODYSSEUS_ALWAYS_TOOLS.

    Stock behavior: RAG retrieval (get_tools_for_query) starts from the
    hardcoded ALWAYS_AVAILABLE frozenset and drops anything it doesn't
    retrieve, so e.g. the shell tool only reaches the model when a query's
    embeddings/keywords happen to pick it. This unions an env-controlled set
    into ALWAYS_AVAILABLE.

    Also adds `import os` to the module header (stock imports are
    logging/hashlib/re/time/typing/embedding_lanes only).

    Patterns are multi-line, so CRLF vs LF is handled by normalizing each
    variant. Idempotent; warns if upstream moved the frozenset."""
    if not path.is_file():
        return "warn", f"{path} not found"
    s = read_raw(path).decode("utf-8")
    if "_ALWAYS_FROM_ENV" in s:
        return "skip", "already patched"
    import_line = "import os"
    if import_line not in s:
        # Insert after the first "import <name>" header line, keeping the
        # file's line endings (the header is pure-LF-agnostic single lines).
        nl = "\r\n" if "\r\n" in s else "\n"
        m = re.search(r"^import [a-z_]+\r?\n", s, re.M)
        if not m:
            return "warn", "could not find the import header -- upstream likely restructured; manual review needed"
        s = s[: m.end()] + import_line + nl + s[m.end():]
    old = None
    for nl in ("\n", "\r\n"):
        if TOOL_INDEX_OLD.replace("\n", nl) in s:
            old = TOOL_INDEX_OLD.replace("\n", nl)
            break
    if old is None:
        return "warn", ("ALWAYS_AVAILABLE frozenset pattern not found -- upstream "
                        "likely restructured tool_index.py; manual review needed")
    # line-ending-safe: derive nl from the matched block itself
    nl_matched = "\r\n" if "\r\n" in old else "\n"
    new = TOOL_INDEX_NEW.replace("\n", nl_matched)
    s = s.replace(old, new, 1)
    write_raw(path, s.encode("utf-8"))
    return "patched", "ODYSSEUS_ALWAYS_TOOLS wired into ALWAYS_AVAILABLE"


# ------------------------------------------ 11. src/chat_processor.py + settings.json (global rules) --

GLOBAL_RULES_INJECT = (
    "\n"
    "\n"
    "        # Local patch: global rules from settings.json (global_rules: list[str],\n"
    "        # editable via the UI or by editing data/settings.json; 2s cache, so no\n"
    "        # restart needed). Injected into EVERY prompt -- this preface is the\n"
    "        # single choke point for both normal chat and agent mode -- as a static\n"
    "        # system message, KV-cache friendly (the text only changes when the\n"
    "        # rules themselves change).\n"
    "        try:\n"
    "            from src.settings import load_settings as _load_settings\n"
    "            _global_rules = _load_settings().get(\"global_rules\")\n"
    "            if isinstance(_global_rules, list):\n"
    "                _global_rules = [str(r).strip() for r in _global_rules if str(r).strip()]\n"
    "            else:\n"
    "                _global_rules = []\n"
    "            if _global_rules:\n"
    "                preface.append({\n"
    "                    \"role\": \"system\",\n"
    "                    \"content\": \"Global rules (user-defined; apply to every message; take precedence over defaults):\\n\"\n"
    "                               + \"\\n\".join(f\"{i + 1}. {r}\" for i, r in enumerate(_global_rules)),\n"
    "                })\n"
    "        except Exception as _e:  # never break a request over a settings hiccup\n"
    "            logger.warning(\"global rules injection failed: %s\", _e)\n"
)


def patch_global_rules(root):
    """Inject user-defined global rules into every prompt.

    Two independent, idempotent halves:
      a) chat_processor.build_context_preface renders settings' `global_rules`
         list as a static system message -- the preface is the single choke
         point for BOTH normal chat and agent mode (llm_core consolidates
         system messages into the leading one), so one injection covers
         every prompt.
      b) data/settings.json gains a `global_rules` list (the user edits it via
         the UI or the file -- 2s settings cache, so no restart needed).
    Returns 'patched' only if something actually changed this run.
    """
    changed = False

    # -- (a) the injection -------------------------------------------------
    cp = root / "src" / "chat_processor.py"
    if not cp.is_file():
        return "warn", f"{cp} not found"
    s = read_raw(cp).decode("utf-8")
    if "# Local patch: global rules" not in s:
        nl = "\r\n" if "\r\n" in s else "\n"
        anchor = nl.join([
            "        # Add preset system prompt if specified",
            "        if preset_system_prompt:",
            "            preface.append({",
            '                "role": "system",',
            '                "content": preset_system_prompt',
            "            })",
        ])
        if anchor not in s:
            return "warn", ("build_context_preface anchor not found -- upstream likely "
                            "restructured chat_processor.py; manual review needed")
        s = s.replace(anchor, anchor + GLOBAL_RULES_INJECT.replace("\n", nl), 1)
        write_raw(cp, s.encode("utf-8"))
        print("      [11] chat_processor.py: global rules injection added")
        changed = True

    # -- (b) the settings key ----------------------------------------------
    sf = root / "data" / "settings.json"
    if sf.is_file():
        raw = read_raw(sf).decode("utf-8")
        if "global_rules" not in raw:
            settings = json.loads(raw)
            if not isinstance(settings.get("global_rules"), list):
                settings["global_rules"] = ["Keep thinking/reasoning succinct -- no restating the question or the plan."]
            sfnl = "\r\n" if "\r\n" in raw else "\n"
            body, trailing_nl = (raw[:-1], "\n") if raw.endswith("\n") else (raw, "")
            lines = body.split(sfnl)

            def _rendered(comma):
                r = ['  "global_rules": [']
                r += ['    "%s"' % x for x in settings["global_rules"] if isinstance(x, str)]
                r.append("  ]," if comma else "  ]")
                return r

            inserted = False
            for k, l in enumerate(lines):
                if not l.strip().startswith('"disabled_tools"'):
                    continue
                after_colon = l.split(":", 1)[1].strip()
                if not (after_colon.rstrip(",").startswith("[") or after_colon.rstrip(",").startswith("{")):
                    lines[k + 1:k + 1] = _rendered(True)  # inline value: next line is a sibling key or }
                    inserted = True
                    break
                close = "]" if after_colon.lstrip()[0] == "[" else "}"
                i = k
                while not lines[i].strip().rstrip(",").endswith(close):
                    i += 1
                is_last_key = i + 1 >= len(lines) or lines[i + 1].strip() == "}"
                lines[i + 1:i + 1] = _rendered(not is_last_key)
                inserted = True
                break
            if not inserted:
                for j in range(len(lines) - 1, -1, -1):
                    if lines[j].strip() == "}":
                        lines[j:j] = _rendered(False)
                        break
            write_raw(sf, (sfnl.join(lines) + trailing_nl).encode("utf-8"))
            print("      [11] settings.json: global_rules added")
            changed = True
    else:
        print("      [11] settings.json not found (fresh install) -- add global_rules via the UI later")

    if changed:
        return "patched", "global rules -> every prompt (settings.json: global_rules)"
    return "skip", "already patched"


# ------------------------------------------ 12. vision tool (new agent tool) --
# A `vision` tool so the agent can look at images on disk with the configured
# vision model mid-task (circuit screenshots, diagrams, photos -- not just
# chat attachments, which the pre-pass already describes generically).
# Reuses the app's proven pipeline (_resolve_vl_model + vision fallback chain
# in document_processor), so no new ML plumbing.
#
# Seven independent halves (a partially-patched checkout converges on the
# next run), all check-before-patch, CRLF-aware:
#   a) document_processor.py  analyze_image_with_vl_result gains `prompt`,
#                             `no_thinking` (Qwen3 /no_think, ~30% faster)
#                             and `max_tokens` (length cap)
#   b) agent_tools/vision_tools.py   NEW: VisionTool (path-confined via
#                                    _resolve_tool_path, same rules as read_file)
#   c) agent_tools/__init__.py  TOOL_HANDLERS + TOOL_TAGS + import
#   d) tool_schemas.py          FUNCTION_TOOL_SCHEMAS entry + required args
#   e) agent_loop.py            TOOL_SECTIONS plain-text block + files domain
#                               + seed vision when uploads contain images
#   f) tool_index.py            BUILTIN_TOOL_DESCRIPTIONS (retrieval)
#   g) tool_security.py         PLAN_MODE_READONLY_TOOLS (read-only tool)
#
# No tool_execution.py change needed: unknown-to-the-specific-branches tools
# dispatch via the generic TOOL_HANDLERS branch (desc "registry: vision ...").

VISION_TOOL_LINES = [
    '"""Vision tool: agent-invoked image analysis with the configured VL model.',
    '',
    'Local patch -- re-applied by data/patches/patch_odysseus.py (section 12).',
    '"""',
    'import asyncio',
    'import json',
    'import os',
    '',
    'IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}',
    '',
    '',
    'class VisionTool:',
    '    async def execute(self, content: str, ctx: dict) -> dict:',
    '        raw = str(content or "").strip()',
    '        if raw.startswith("{"):',
    '            try:',
    '                a = json.loads(raw)',
    '            except json.JSONDecodeError:',
    '                a = {}',
    '            if isinstance(a, dict):',
    '                path = str(a.get("path", "")).strip()',
    '                prompt = str(a.get("prompt", "")).strip()',
    '                # Local defaults: no thinking, generous cap (user preference 2026-09-23).',
    '                _nt = a.get("no_thinking")',
    '                if isinstance(_nt, str):',
    '                    _nt = _nt.strip().lower() in ("1", "true", "yes", "on")',
    '                no_thinking = bool(_nt) if _nt is not None else True',
    '                max_tokens = a.get("max_tokens")',
    '                if isinstance(max_tokens, str):',
    '                    max_tokens = int(max_tokens) if max_tokens.strip().isdigit() else None',
    '                elif isinstance(max_tokens, bool) or not isinstance(max_tokens, int):',
    '                    max_tokens = None',
    '                if max_tokens is None:',
    '                    max_tokens = 5000',
    '        else:',
    '            lines = raw.split("\\n", 1)',
    '            path = lines[0].strip()',
    '            prompt = lines[1].strip() if len(lines) > 1 else ""',
    '            no_thinking = True  # Local default',
    '            max_tokens = 5000  # Local default',
    '        if not path:',
    '            return {"error": "vision: path is required", "exit_code": 1}',
    '        try:',
    '            from src.tool_execution import _resolve_tool_path',
    '            path = _resolve_tool_path(path)',
    '        except ValueError as e:',
    '            return {"error": "vision: %s" % e, "exit_code": 1}',
    '        if os.path.splitext(path)[1].lower() not in IMAGE_EXTS:',
    '            return {"error": "vision: not an image (supported: %s)" % ", ".join(sorted(IMAGE_EXTS)), "exit_code": 1}',
    '        if not os.path.isfile(path):',
    '            return {"error": "vision: %s not found" % path, "exit_code": 1}',
    '        try:',
    '            from src.document_processor import analyze_image_with_vl_result',
    '            res = await asyncio.to_thread(analyze_image_with_vl_result, path, ctx.get("owner"), prompt, no_thinking, max_tokens)',
    '        except Exception as e:',
    '            return {"error": "vision: %s: %s" % (type(e).__name__, e), "exit_code": 1}',
    '        text = str(res.get("text") or "").strip()',
    '        if not text:',
    '            return {"error": "vision: no description returned for %s" % path, "exit_code": 1}',
    '        if text.startswith("["):  # the pipeline signals config problems in brackets',
    '            return {"error": "vision: %s" % text.strip("[]"), "exit_code": 1}',
    '        model = res.get("model") or ""',
    '        return {"output": ("[via %s]\\n" % model if model else "") + text, "exit_code": 0}',
    '',
    '',
    '__all__ = ["VisionTool", "IMAGE_EXTS"]',
]

VISION_TOOL_PATH = "src/agent_tools/vision_tools.py"

VISION_SCHEMA_LINES = [
    '    {',
    '        "type": "function",',
    '        "function": {',
    '            "name": "vision",',
    '            "description": "Analyze an image on disk (screenshot, diagram, photo, chart) with the configured vision model and get its description back. Optional prompt focuses the analysis. Use for images whose contents you need to reason about, e.g. circuit screenshots or charts, or an attachment path listed in the prompt.",',
    '            "parameters": {',
    '                "type": "object",',
    '                "properties": {',
    '                    "path": {"type": "string", "description": "Absolute path of the image file (png/jpg/jpeg/gif/webp/bmp)"},',
    '                    "prompt": {"type": "string", "description": "Optional focus, e.g. which component or connection to describe; default is describe in detail"},',
    '                    "no_thinking": {"type": "boolean", "description": "Default true: skip the model reasoning step for roughly 30 percent faster analysis (Qwen3 /no_think switch). Set false for very complex images where extra reasoning helps."},',
    '                    "max_tokens": {"type": "integer", "description": "Cap on description length in tokens. Default 5000."},',
    '                },',
    '                "required": ["path"]',
    '            }',
    '        }',
    '    },',
]

VISION_SECTION_LINES = [
    '    "vision": """\\',
    '```vision',
    '{"path": "<image file path>", "prompt": "<what to look for>"}',
    '```',
    'Analyze an image on disk with the configured vision model and return its description. '
    'Use for images whose contents you must reason about -- circuit screenshots, diagrams, charts, '
    'photos -- including attachment paths listed in the prompt. Optional "prompt" focuses the analysis '
    '(default: describe in detail). Optional "no_thinking": true skips model reasoning (~30% faster, '
    'Paths are confined to the same roots as read_file. Defaults: no_thinking is true and '
    'max_tokens is 5000 -- override either in the JSON args only when needed.',
    '""",',
]

VISION_INDEX_LINES = [
    "    \"vision\": \"Analyze an image on disk (screenshot, diagram, photo, chart) with the configured "
    "vision model and return its description. Use when the model must SEE or reason about an image's "
    "contents -- circuit screenshots, rendered diagrams, plots, attached photos. Optional focus prompt. "
    "Distinct from read_file (text) and from the automatic chat-attachment pre-pass, which gives only "
    "one generic description.\",",
]


def _lines_of(p):
    """Read file, normalize EOL to \\n for matching, remember the EOL."""
    raw = read_raw(p).decode("utf-8")
    nl = "\r\n" if "\r\n" in raw else "\n"
    return raw.replace(nl, "\n"), nl


def _write(p, text, nl):
    write_raw(p, text.replace("\n", nl).encode("utf-8"))


def patch_vision_tool(root):
    """Wire a `vision` agent tool through all integration points.

    Each file is an independent half: a partially-patched checkout converges
    on the next run. Returns (status, detail) like the other sections."""
    changed = False

    # -- (a) document_processor.py: prompt + no_thinking + max_tokens --------
    # Staged: stock -> +prompt -> +no_thinking/max_tokens; the call site gains
    # the max_tokens arg here (section 14 independently handles its timeout,
    # including the max_tokens-bearing variant of the same line).
    dp = root / "src" / "document_processor.py"
    s, nl = _lines_of(dp)
    a_stages = [
        ("def analyze_image_with_vl_result(image_path: str, owner: str | None = None) -> dict:",
         "def analyze_image_with_vl_result(image_path: str, owner: str | None = None, prompt: str | None = None) -> dict:"),
        ('                    {"type": "text", "text": "Describe this image in detail"},',
         '                    {"type": "text", "text": prompt or "Describe this image in detail"},'),
        ("def analyze_image_with_vl_result(image_path: str, owner: str | None = None, prompt: str | None = None) -> dict:",
         "def analyze_image_with_vl_result(image_path: str, owner: str | None = None, prompt: str | None = None,\n"
         "                          no_thinking: bool = False, max_tokens: int | None = None) -> dict:"),
        ("        vl_messages = [\n"
         "            {\n"
         "                \"role\": \"user\",\n"
         "                \"content\": [\n"
         '                    {"type": "text", "text": prompt or "Describe this image in detail"},',
         "        vl_text = prompt or \"Describe this image in detail\"\n"
         "        if no_thinking:\n"
         "            # Qwen3-style thinking switch-off, honored by llama.cpp for thinking models\n"
         "            vl_text = vl_text + \"\\n/no_think\"\n"
         "        vl_messages = [\n"
         "            {\n"
         "                \"role\": \"user\",\n"
         "                \"content\": [\n"
         '                    {"type": "text", "text": vl_text},'),
        ("llm_call(_url, _model, vl_messages, headers=_headers, timeout=120)",
         "llm_call(_url, _model, vl_messages, headers=_headers, timeout=120, max_tokens=(max_tokens or 0))"),
        ("headers=_headers, timeout=_env_timeout('ODYSSEUS_TIMEOUT_VISION', 300))",
         "headers=_headers, timeout=_env_timeout('ODYSSEUS_TIMEOUT_VISION', 300), max_tokens=(max_tokens or 0))"),
    ]
    if any(o in s for o, n in a_stages):
        for o, n in a_stages:
            if o in s and s.count(o) != 1:
                return "warn", "document_processor.py anchor %r not unique -- manual review needed" % o[:60]
        a_applied = False
        for o, n in a_stages:
            if o in s and n not in s:
                s = s.replace(o, n, 1)
                a_applied = True
        if a_applied:
            _write(dp, s, nl)
            print("      [12] document_processor.py: prompt/no_thinking/max_tokens params")
            changed = True
    elif "no_thinking: bool = False" not in s and "vl_text = prompt or" not in s:
        # No stock anchors AND no patch markers: the VL pipeline is neither
        # stock nor patched -- upstream restructured it.
        return "warn", ("document_processor.py: VL pipeline anchors and patch markers "
                        "both absent -- upstream likely restructured it; manual review needed")

    # -- (b) new tool implementation ------------------------------------------
    vt = root / VISION_TOOL_PATH
    if not vt.is_file():
        probe = root / "src" / "agent_tools" / "__init__.py"
        _, vnl = _lines_of(probe) if probe.is_file() else ("", "\r\n")
        write_raw(vt, (vnl.join(VISION_TOOL_LINES) + vnl).encode("utf-8"))
        print("      [12] agent_tools/vision_tools.py: created")
        changed = True
    else:
        # Converge an existing (older-generation) tool file. Generations:
        #   1: prompt-only  2: params, no defaults  3: params + local defaults
        # Each stage anchors on a full block so generations never mix.
        s, nl = _lines_of(vt)
        b_stages = [
            # gen1 -> gen3 (JSON branch: prompt line directly followed by else)
            ('                prompt = str(a.get("prompt", "")).strip()\n'
             '        else:',
             '                prompt = str(a.get("prompt", "")).strip()\n'
             '                # Local defaults: no thinking, generous cap (user preference 2026-09-23).\n'
             '                _nt = a.get("no_thinking")\n'
             '                if isinstance(_nt, str):\n'
             '                    _nt = _nt.strip().lower() in ("1", "true", "yes", "on")\n'
             '                no_thinking = bool(_nt) if _nt is not None else True\n'
             '                max_tokens = a.get("max_tokens")\n'
             '                if isinstance(max_tokens, str):\n'
             '                    max_tokens = int(max_tokens) if max_tokens.strip().isdigit() else None\n'
             '                elif isinstance(max_tokens, bool) or not isinstance(max_tokens, int):\n'
             '                    max_tokens = None\n'
             '                if max_tokens is None:\n'
             '                    max_tokens = 5000\n'
             '        else:'),
            # gen2 -> gen3 (JSON branch: no_thinking line gains a default)
            ('                no_thinking = bool(a.get("no_thinking", False))',
             '                # Local defaults: no thinking, generous cap (user preference 2026-09-23).\n'
             '                _nt = a.get("no_thinking")\n'
             '                if isinstance(_nt, str):\n'
             '                    _nt = _nt.strip().lower() in ("1", "true", "yes", "on")\n'
             '                no_thinking = bool(_nt) if _nt is not None else True'),
            # gen2b -> gen3 (string-typed no_thinking coercion)
            ('                _nt = a.get("no_thinking")\n'
             '                no_thinking = bool(_nt) if _nt is not None else True\n',
             '                _nt = a.get("no_thinking")\n'
             '                if isinstance(_nt, str):\n'
             '                    _nt = _nt.strip().lower() in ("1", "true", "yes", "on")\n'
             '                no_thinking = bool(_nt) if _nt is not None else True\n'),
            # gen2 -> gen3 (JSON branch: parse block ends, then else)
            ('                elif isinstance(max_tokens, bool) or not isinstance(max_tokens, int):\n'
             '                    max_tokens = None\n'
             '        else:',
             '                elif isinstance(max_tokens, bool) or not isinstance(max_tokens, int):\n'
             '                    max_tokens = None\n'
             '                if max_tokens is None:\n'
             '                    max_tokens = 5000  # Local default\n'
             '        else:'),
            # gen1 -> gen3 (bare-text branch)
            ('            prompt = lines[1].strip() if len(lines) > 1 else ""\n'
             '        if not path:',
             '            prompt = lines[1].strip() if len(lines) > 1 else ""\n'
             '            no_thinking = True  # Local default\n'
             '            max_tokens = 5000  # Local default\n'
             '        if not path:'),
            # gen2 -> gen3 (bare-text branch)
            ('            no_thinking = False\n'
             '            max_tokens = None\n'
             '        if not path:',
             '            no_thinking = True  # Local default\n'
             '            max_tokens = 5000  # Local default\n'
             '        if not path:'),
            # gen1 -> gen2 (the pipeline call gains the two args)
            ('res = await asyncio.to_thread(analyze_image_with_vl_result, path, ctx.get("owner"), prompt)',
             'res = await asyncio.to_thread(analyze_image_with_vl_result, path, ctx.get("owner"), prompt, no_thinking, max_tokens)'),
        ]
        b_applied = False
        for o, n in b_stages:
            if o in s:
                if s.count(o) != 1:
                    return "warn", "vision_tools.py anchor %r not unique -- manual review needed" % o[:60]
                if n not in s:
                    s = s.replace(o, n, 1)
                    b_applied = True
        if b_applied:
            _write(vt, s, nl)
            print("      [12] agent_tools/vision_tools.py: converged to local defaults")
            changed = True

    # -- (c) agent_tools/__init__.py: import + handler + tag ------------------
    ti = root / "src" / "agent_tools" / "__init__.py"
    s, nl = _lines_of(ti)
    edits = []
    if "from .vision_tools import VisionTool" not in s:
        edits.append((
            "from .bg_job_tools import ManageBgJobsTool",
            "from .bg_job_tools import ManageBgJobsTool\nfrom .vision_tools import VisionTool"))
    if '"vision": VisionTool().execute' not in s:
        edits.append((
            '    "list_models": ListModelsTool().execute,',
            '    "list_models": ListModelsTool().execute,\n    "vision": VisionTool().execute,'))
    if '"vision"' not in s.split("TOOL_TAGS = {")[1].split("} | BUILTIN_EMAIL_TOOLS")[0]:
        edits.append((
            '             "ui_control", "generate_image", "ask_user", "update_plan",',
            '             "ui_control", "generate_image", "ask_user", "update_plan",\n'
            '             "vision",  # Local patch: agent-invoked image analysis'))
    if edits:
        for old, new in edits:
            if s.count(old) != 1:
                return "warn", ("agent_tools/__init__.py anchor %r not unique -- "
                                "manual review needed" % old[:50])
            s = s.replace(old, new, 1)
        _write(ti, s, nl)
        print("      [12] agent_tools/__init__.py: VisionTool registered (import + handler + tag)")
        changed = True

    # -- (d) tool_schemas.py: required args + native schema --------------------
    ts = root / "src" / "tool_schemas.py"
    s, nl = _lines_of(ts)
    edits = []
    if '"vision": ("path",)' not in s:
        edits.append((
            '    "read_file": ("path",),',
            '    "read_file": ("path",),\n    "vision": ("path",),'))
    if '"name": "vision"' not in s:
        opener = "\n".join([
            '    {',
            '        "type": "function",',
            '        "function": {',
            '            "name": "read_file",',
        ])
        edits.append((opener, "\n".join(VISION_SCHEMA_LINES) + "\n" + opener))
    if edits:
        for old, new in edits:
            if s.count(old) != 1:
                return "warn", ("tool_schemas.py anchor %r not unique -- "
                                "manual review needed" % old[:50])
            s = s.replace(old, new, 1)
        _write(ts, s, nl)
        print("      [12] tool_schemas.py: vision schema + required-args entry")
        changed = True
    # (d2) converge an existing-but-old schema entry (prompt-only version) to
    #      the no_thinking/max_tokens version.
    _d_prompt = '                    "prompt": {"type": "string", "description": "Optional focus, e.g. which component or connection to describe; default is describe in detail"}'
    if '"no_thinking"' not in s and _d_prompt in s:
        if s.count(_d_prompt) != 1:
            return "warn", "tool_schemas.py prompt-line count != 1 -- manual review needed"
        s = s.replace(_d_prompt, _d_prompt + "," + "\n"
                      '                    "no_thinking": {"type": "boolean", "description": "Default true: skip the model reasoning step for roughly 30 percent faster analysis (Qwen3 /no_think switch). Set false for very complex images where extra reasoning helps."},\n'
                      '                    "max_tokens": {"type": "integer", "description": "Cap on description length in tokens. Default 5000."},', 1)
        _write(ts, s, nl)
        print("      [12] tool_schemas.py: vision schema gained no_thinking/max_tokens")
        changed = True
    # (d3) converge an existing-but-old no_thinking/max_tokens schema (the
    #      pre-defaults wording) to the current descriptions.
    _d_old_nt = '"no_thinking": {"type": "boolean", "description": "Set true to skip the model reasoning step for roughly 30 percent faster analysis (Qwen3 /no_think switch). Good for routine descriptions."}'
    _d_new_nt = '"no_thinking": {"type": "boolean", "description": "Default true: skip the model reasoning step for roughly 30 percent faster analysis (Qwen3 /no_think switch). Set false for very complex images where extra reasoning helps."}'
    _d_old_mt = '"max_tokens": {"type": "integer", "description": "Optional cap on description length in tokens (default: server default)"}'
    _d_new_mt = '"max_tokens": {"type": "integer", "description": "Cap on description length in tokens. Default 5000."}'
    if _d_old_nt in s or _d_old_mt in s:
        if s.count(_d_old_nt) not in (0, 1) or s.count(_d_old_mt) not in (0, 1):
            return "warn", "tool_schemas.py vision-param description count != 1 -- manual review needed"
        if _d_old_nt in s:
            s = s.replace(_d_old_nt, _d_new_nt, 1)
        if _d_old_mt in s:
            s = s.replace(_d_old_mt, _d_new_mt, 1)
        _write(ts, s, nl)
        print("      [12] tool_schemas.py: vision schema descriptions updated to defaults")
        changed = True

    # -- (e) agent_loop.py: prompt section + files domain + upload seed -------
    al = root / "src" / "agent_loop.py"
    s, nl = _lines_of(al)
    edits = []
    if '"vision": """' not in s:
        edits.append((
            "Read a file and return its contents.\"\"\",",
            "Read a file and return its contents.\"\"\",\n\n" + "\n".join(VISION_SECTION_LINES)))
    if '"manage_bg_jobs", "vision"}' not in s:
        edits.append((
            '    "files": {"bash", "python", "read_file", "write_file", "edit_file", "apply_patch", "todowrite", "grep", "glob", "ls", "get_workspace", "manage_bg_jobs"},',
            '    "files": {"bash", "python", "read_file", "write_file", "edit_file", "apply_patch", "todowrite", "grep", "glob", "ls", "get_workspace", "manage_bg_jobs", "vision"},  # Local patch: + vision'))
    if "Local patch: uploads with images can be re-inspected on demand" not in s:
        edits.append((
            '        _relevant_tools.update({"read_file", "grep", "ls", "manage_documents"})',
            '        _relevant_tools.update({"read_file", "grep", "ls", "manage_documents"})\n'
            '        # Local patch: uploads with images can be re-inspected on demand.\n'
            '        if any(str(u.get("path", "")).lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")) for u in uploaded_files if isinstance(u, dict)):\n'
            '            _relevant_tools.add("vision")'))
    if edits:
        for old, new in edits:
            if s.count(old) != 1:
                return "warn", ("agent_loop.py anchor %r not unique -- "
                                "manual review needed" % old[:50])
            s = s.replace(old, new, 1)
        _write(al, s, nl)
        print("      [12] agent_loop.py: TOOL_SECTIONS vision + files domain + upload seed")
        changed = True
    # (e2) converge an existing-but-old fence text to the no_thinking/max_tokens
    #      version.
    _e_old = ('Analyze an image on disk with the configured vision model and return its description. '
              'Use for images whose contents you must reason about -- circuit screenshots, diagrams, charts, '
              'photos -- including attachment paths listed in the prompt. Optional "prompt" focuses the analysis '
              '(default: describe in detail). Paths are confined to the same roots as read_file.\n')
    if (_e_old in s and '"no_thinking": true' not in s
            and 'Defaults: no_thinking is true and' not in s):
        if s.count(_e_old) != 1:
            return "warn", "agent_loop.py fence-text count != 1 -- manual review needed"
        s = s.replace(_e_old, ('Analyze an image on disk with the configured vision model and return its description. '
              'Use for images whose contents you must reason about -- circuit screenshots, diagrams, charts, '
              'photos -- including attachment paths listed in the prompt. Optional "prompt" focuses the analysis '
              '(default: describe in detail). Paths are confined to the same roots as read_file. '
              'Defaults: no_thinking is true and max_tokens is 5000 -- override either in the JSON args '
              'only when needed.'), 1)
        _write(al, s, nl)
        print("      [12] agent_loop.py: vision fence text gained defaults")
        changed = True
    # (e3) converge an existing-but-old fence text (the pre-defaults wording).
    _e2_old = ('Analyze an image on disk with the configured vision model and return its description. '
               'Use for images whose contents you must reason about -- circuit screenshots, diagrams, charts, '
               'photos -- including attachment paths listed in the prompt. Optional "prompt" focuses the analysis '
               '(default: describe in detail). Optional "no_thinking": true skips model reasoning (~30% faster, '
               'use for routine descriptions). Optional "max_tokens": N caps description length. '
               'Paths are confined to the same roots as read_file.')
    if _e2_old in s:
        if s.count(_e2_old) != 1:
            return "warn", "agent_loop.py old fence-text count != 1 -- manual review needed"
        s = s.replace(_e2_old, ('Analyze an image on disk with the configured vision model and return its description. '
              'Use for images whose contents you must reason about -- circuit screenshots, diagrams, charts, '
              'photos -- including attachment paths listed in the prompt. Optional "prompt" focuses the analysis '
              '(default: describe in detail). Paths are confined to the same roots as read_file. '
              'Defaults: no_thinking is true and max_tokens is 5000 -- override either in the JSON args '
              'only when needed.'), 1)
        _write(al, s, nl)
        print("      [12] agent_loop.py: vision fence text updated to defaults")
        changed = True

    # -- (f) tool_index.py: retrieval description ------------------------------
    tx = root / "src" / "tool_index.py"
    s, nl = _lines_of(tx)
    if '"vision": "Analyze an image on disk' not in s:
        old = '    "read_file": "Read a file from disk and return its contents.'
        if s.count(old) != 1:
            return "warn", "tool_index.py anchor not unique -- manual review needed"
        s = s.replace(old, "\n".join(VISION_INDEX_LINES) + "\n" + old, 1)
        _write(tx, s, nl)
        print("      [12] tool_index.py: BUILTIN_TOOL_DESCRIPTIONS vision entry")
        changed = True

    # -- (g) tool_security.py: plan-mode allowlist -----------------------------
    sec = root / "src" / "tool_security.py"
    s, nl = _lines_of(sec)
    if '"vision",  # Local patch' not in s:
        old = '    "chat_with_model",\n    "ask_teacher",\n}'
        if s.count(old) != 1:
            return "warn", ("tool_security.py anchor not unique -- manual review "
                            "needed (vision stays plan-mode-blocked until you add it)")
        s = s.replace(old, '    "chat_with_model",\n    "ask_teacher",\n    "vision",  # Local patch: read-only image analysis, like chat_with_model\n}', 1)
        _write(sec, s, nl)
        print("      [12] tool_security.py: vision in PLAN_MODE_READONLY_TOOLS")
        changed = True

    if changed:
        return "patched", ("vision tool wired (document_processor, vision_tools.py, __init__, "
                           "tool_schemas, agent_loop, tool_index, tool_security)")
    return "skip", "already patched"




# ---------------------- 13. LLM call timeouts for slow local models --------

# The 2026-09-20 incidents: a context-compaction call (30 s budget, 3 retries)
# and a deep-research synthesis (180 s budget, 3 retries) were killed
# mid-generation on a local qwen3.8 decoding at ~6 tok/s. The compaction
# abort left a 59-round agent chat unable to finish ("chats completing
# prematurely"); the synthesis failure degraded research to the raw-findings
# fallback. The small 15-30 s budgets (probe / plan / query synthesis /
# query generation) were also flapping on cold prefill. Fix: every affected
# call site becomes `timeout=_env_timeout('<ODYSSEUS_TIMEOUT_*>', <stock>)` --
# an env-overridable budget (read at call time, so .env edits apply on the
# next `up -d`, no rebuild) whose default stays the stock value.

_TIMEOUT_HELPER = (
    "def _env_timeout(name: str, stock: int) -> int:\n"
    "    # Local patch: env-overridable read timeout for one LLM call site.\n"
    "    try:\n"
    "        return max(5, int(float(os.getenv(name, str(stock)))))\n"
    "    except (TypeError, ValueError):\n"
    "        return stock\n"
)

TIMEOUT_COMPOSE_LINES = [
    "      - ODYSSEUS_TIMEOUT_COMPACT=${ODYSSEUS_TIMEOUT_COMPACT:-300}",
    "      - ODYSSEUS_TIMEOUT_RESEARCH=${ODYSSEUS_TIMEOUT_RESEARCH:-600}",
]
TIMEOUT_COMPOSE_STAGE3 = "      - ODYSSEUS_REPEAT_GRAM_COUNT=${ODYSSEUS_REPEAT_GRAM_COUNT:-100}"


def patch_timeouts(root):
    """Widen the fixed LLM-call budgets that kill long generations on slow
    local models. Each file is an independent half (a partially-patched
    checkout converges on the next run). Returns (status, detail)."""
    changed = []

    # -- (a) context_compactor.py: the budget whose timeout kills the chat ---
    p = root / "src" / "context_compactor.py"
    s, nl = _lines_of(p)
    if "timeout=30," in s:
        if "def _env_timeout" in s or s.count("SUMMARY_MAX_TOKENS = 1024") != 1:
            return "warn", "context_compactor.py: partial/unknown state -- manual review needed"
        s = s.replace("import logging\nimport re", "import logging\nimport os\nimport re", 1)
        s = s.replace("SUMMARY_MAX_TOKENS = 1024",
                      "SUMMARY_MAX_TOKENS = 1024\n" + _TIMEOUT_HELPER, 1)
        s = s.replace("timeout=30,",
                      "timeout=_env_timeout('ODYSSEUS_TIMEOUT_COMPACT', 30),", 1)
        _write(p, s, nl)
        changed.append("context_compactor.py (30s budget env-overridable)")
    elif "def _env_timeout" not in s:
        return "warn", "context_compactor.py: timeout=30 call site not found -- upstream changed it? manual review needed"

    # -- (b) deep_research.py: synthesis + final report (the 179.30 s case) --
    p = root / "src" / "deep_research.py"
    s, nl = _lines_of(p)
    n180 = s.count("timeout=180,")
    if n180:
        if "def _env_timeout" in s and n180 > 0:
            return "warn", f"deep_research.py: helper present but {n180}x timeout=180 left -- manual review needed"
        if s.count("import json\nimport logging") != 1 or s.count("from src.prompt_security import untrusted_context_message") != 1:
            return "warn", "deep_research.py: import anchor not found -- manual review needed"
        s = s.replace("import json\nimport logging",
                      "import json\nimport logging\nimport os", 1)
        s = s.replace("from src.prompt_security import untrusted_context_message",
                      "from src.prompt_security import untrusted_context_message\n" + _TIMEOUT_HELPER, 1)
        s = s.replace("timeout=180,",
                      "timeout=_env_timeout('ODYSSEUS_TIMEOUT_RESEARCH', 180),")
        _write(p, s, nl)
        changed.append(f"deep_research.py ({n180}x 180s budgets env-overridable)")
    elif "def _env_timeout" not in s:
        return "warn", "deep_research.py: no timeout=180 call sites found -- upstream changed them? manual review needed"

    # -- (c) agent_loop.py: verifier/salvage budgets + round-deadline floor --
    p = root / "src" / "agent_loop.py"
    s, nl = _lines_of(p)
    c_sites = [
        ("max_tokens=600, timeout=60,",
         "max_tokens=600, timeout=_env_timeout('ODYSSEUS_TIMEOUT_VERIFY', 60),"),
        ("max_tokens=max_tokens, timeout=60,",
         "max_tokens=max_tokens, timeout=_env_timeout('ODYSSEUS_TIMEOUT_SYNTH', 60),"),
        ("_round_deadline = time.time() + max(agent_stream_timeout * 4, 1200)",
         "_round_deadline = time.time() + max(agent_stream_timeout * 4, "
         "int(os.getenv('ODYSSEUS_AGENT_ROUND_DEADLINE', '3600') or '3600'))"),
        ("max(agent_stream_timeout * 4, 1200),",
         "max(agent_stream_timeout * 4, "
         "int(os.getenv('ODYSSEUS_AGENT_ROUND_DEADLINE', '3600') or '3600')),"),
    ]
    c_todo = [(o, n) for o, n in c_sites if o in s]
    if c_todo:
        if "def _env_timeout" in s:
            return "warn", "agent_loop.py: helper present but call sites unpatched -- manual review needed"
        for o, n in c_sites:
            if o in s and s.count(o) != 1:
                return "warn", f"agent_loop.py: site {o.strip()[:45]!r} count={s.count(o)} -- manual review needed"
        if s.count("import json\nimport re") != 1:
            return "warn", "agent_loop.py: import anchor not found -- manual review needed"
        s = s.replace("import json\nimport re", "import json\nimport os\nimport re", 1)
        for o, n in c_sites:
            if o in s:
                s = s.replace(o, n, 1)
        # module-level helper above the verifier function (first user); the
        # salvage site (stream_agent_loop) sees it too -- both are module fns.
        anchor = "async def _run_verifier_subagent("
        if s.count(anchor) != 1:
            return "warn", "agent_loop.py: verifier function anchor not found -- manual review needed"
        s = s.replace(anchor, _TIMEOUT_HELPER + "\n" + anchor, 1)
        _write(p, s, nl)
        changed.append("agent_loop.py (verify/salvage budgets + deadline floor 1200->3600)")
    elif "def _env_timeout" not in s and "ODYSSEUS_AGENT_ROUND_DEADLINE" not in s:
        return "warn", "agent_loop.py: no stock timeout=60 / deadline-1200 sites found -- upstream changed them? manual review needed"

    # -- (d) research_handler.py: probe / plan / synthesize-query ------------
    p = root / "src" / "research_handler.py"
    s, nl = _lines_of(p)
    d_sites = [
        ("max_tokens=5,\n                headers=headers,\n                timeout=15,",
         "max_tokens=5,\n                headers=headers,\n                timeout=self._env_timeout('ODYSSEUS_TIMEOUT_PROBE', 15),"),
        ("max_tokens=1024,\n                headers=llm_headers,\n                timeout=30,\n                max_retries=1,",
         "max_tokens=1024,\n                headers=llm_headers,\n                timeout=self._env_timeout('ODYSSEUS_TIMEOUT_PLAN', 30),\n                max_retries=1,"),
        ("max_tokens=200,\n                headers=llm_headers,\n                timeout=15,\n                max_retries=1,",
         "max_tokens=200,\n                headers=llm_headers,\n                timeout=self._env_timeout('ODYSSEUS_TIMEOUT_SYNTH_QUERY', 15),\n                max_retries=1,"),
    ]
    d_todo = [(o, n) for o, n in d_sites if o in s]
    if d_todo:
        if "def _env_timeout" in s:
            return "warn", "research_handler.py: helper present but call sites unpatched -- manual review needed"
        for o, n in d_sites:
            if o in s and s.count(o) != 1:
                return "warn", f"research_handler.py: site {o.splitlines()[0].strip()[:45]!r} count={s.count(o)} -- manual review needed"
        if s.count("import json\nimport logging") != 1 or s.count("\n    async def _probe_endpoint(") != 1:
            return "warn", "research_handler.py: import/probe anchor not found -- manual review needed"
        s = s.replace("import json\nimport logging", "import json\nimport logging\nimport os", 1)
        s = s.replace("\n    async def _probe_endpoint(",
                      "\n"
                      "    def _env_timeout(self, name: str, stock: int) -> int:\n"
                      "        # Local patch: env-overridable read timeout for one LLM call site.\n"
                      "        try:\n"
                      "            return max(5, int(float(os.getenv(name, str(stock)))))\n"
                      "        except (TypeError, ValueError):\n"
                      "            return stock\n"
                      "\n"
                      "    async def _probe_endpoint(", 1)
        for o, n in d_sites:
            if o in s:
                s = s.replace(o, n, 1)
        _write(p, s, nl)
        changed.append("research_handler.py (probe/plan/query budgets env-overridable)")
    elif "def _env_timeout" not in s:
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
    old_static = "    @staticmethod\n    def _env_timeout(self, name: str, stock: int) -> int:"
    new_static = "    def _env_timeout(self, name: str, stock: int) -> int:"
    if old_static in s:
        if s.count(old_static) != 1:
            return "warn", f"research_handler.py: _env_timeout staticmethod anchor count={s.count(old_static)} -- manual review needed"
        s = s.replace(old_static, new_static, 1)
        d2_changed = True
    if d2_changed:
        _write(p, s, nl)
        changed.append("research_handler.py (repair: _probe_endpoint self / _env_timeout staticmethod)")

    # -- (e) chat_processor.py: web-search query extraction -------------------
    p = root / "src" / "chat_processor.py"
    s, nl = _lines_of(p)
    site_e = "max_tokens=50,\n                        timeout=15,"
    if site_e in s:
        if "def _env_timeout" in s:
            return "warn", "chat_processor.py: helper present but call site unpatched -- manual review needed"
        if s.count("import logging\nimport math") != 1:
            return "warn", "chat_processor.py: import anchor not found -- manual review needed"
        if s.count("\n    def build_context_preface(") != 1:
            return "warn", "chat_processor.py: build_context_preface anchor not found -- manual review needed"
        # the call site is inside class ChatProcessor -> method-style helper
        method = ("    def _env_timeout(self, name: str, stock: int) -> int:\n"
                 "        # Local patch: env-overridable read timeout for one LLM call site.\n"
                 "        try:\n"
                 "            return max(5, int(float(os.getenv(name, str(stock)))))\n"
                 "        except (TypeError, ValueError):\n"
                 "            return stock\n\n")
        s = s.replace("import logging\nimport math", "import logging\nimport math\nimport os", 1)
        s = s.replace("\n    def build_context_preface(", "\n" + method + "    def build_context_preface(", 1)
        s = s.replace(site_e,
                      "max_tokens=50,\n                        timeout=self._env_timeout('ODYSSEUS_TIMEOUT_QUERY_GEN', 15),", 1)
        _write(p, s, nl)
        changed.append("chat_processor.py (query-extraction budget env-overridable)")

    # -- (f) settings.json: research_max_time_seconds (add only if absent) ----
    pj = root / "data" / "settings.json"
    if pj.is_file():
        data = json.loads(read_raw(pj).decode("utf-8"))
        if "research_max_time_seconds" not in data:
            data["research_max_time_seconds"] = 600
            write_raw(pj, (json.dumps(data, indent=2) + "\n").encode("utf-8"))
            changed.append("settings.json (research_max_time_seconds=600 added)")

    # -- (g) research_routes.py: max_time -> Optional (settings-driven) -------
    p = root / "routes" / "research" / "research_routes.py"
    s, nl = _lines_of(p)
    if "max_time: int = Field(default=300, ge=60, le=1800)" in s:
        s = s.replace(
            "max_time: int = Field(default=300, ge=60, le=1800)",
            "max_time: Optional[int] = Field(default=None, ge=60, le=3600)", 1)
        _write(p, s, nl)
        changed.append("research_routes.py (max_time optional, cap 3600)")
    elif "Field(default=300, ge=60, le=1800)" in s:
        return "warn", "research_routes.py: max_time Field anchor not found -- manual review needed"

    # -- (g2) research_handler.py: resolve max_time from settings -------------
    p = root / "src" / "research_handler.py"
    s, nl = _lines_of(p)
    res_old = ("                hard_timeout = _bounded_int(\n"
               "                    raw_timeout,\n"
               "                    default=1800,\n"
               "                    minimum=60,\n"
               "                    maximum=86400,\n"
               "                )")
    res_new = res_old + (
        "\n\n"
        "        # Local patch: max_time from settings when the caller didn't pin\n"
        "        # one (stock default was 300 s -- too short for slow local\n"
        "        # models). Tune via data/settings.json: research_max_time_seconds.\n"
        "        if max_time is None:\n"
        "            from src.settings import get_setting\n"
        "            max_time = _bounded_int(\n"
        "                get_setting('research_max_time_seconds', 600),\n"
        "                default=600,\n"
        "                minimum=60,\n"
        "                maximum=3600,\n"
        "            )")
    g2_edits = []
    if "max_time: int = 300,\n        hard_timeout: int = None," in s:
        g2_edits.append(("max_time: int = 300,\n        hard_timeout: int = None,",
                        "max_time: Optional[int] = None,\n        hard_timeout: int = None,"))
    if "max_time: int = 300,\n        progress_callback=None," in s:
        g2_edits.append(("max_time: int = 300,\n        progress_callback=None,",
                        "max_time: Optional[int] = None,\n        progress_callback=None,"))
    if res_old in s and "research_max_time_seconds" not in s:
        g2_edits.append((res_old, res_new))
    if "max_time: Maximum research time in seconds (default 5 minutes)" in s:
        g2_edits.append(("max_time: Maximum research time in seconds (default 5 minutes)",
                        "max_time: Maximum research time in seconds (None = settings research_max_time_seconds)"))
    if g2_edits:
        for o, n in g2_edits:
            if s.count(o) != 1:
                return "warn", f"research_handler.py: max_time anchor {o.splitlines()[0].strip()[:45]!r} count={s.count(o)} -- manual review needed"
            s = s.replace(o, n, 1)
        _write(p, s, nl)
        changed.append(f"research_handler.py (max_time settings-driven, {len(g2_edits)} edits)")
    elif "max_time: int = 300," in s:
        return "warn", "research_handler.py: max_time signature anchor not found -- manual review needed"

    # -- (h) docker-compose.yml: forward the two headline vars (stage 4) ------
    cp = compose_path(root)
    if cp.is_file():
        s = read_raw(cp).decode("utf-8")
        if "ODYSSEUS_TIMEOUT_COMPACT" not in s:
            for nl_ in ("\n", "\r\n"):
                anchor = TIMEOUT_COMPOSE_STAGE3 + nl_
                if anchor in s:
                    s = s.replace(anchor, anchor + nl_.join(TIMEOUT_COMPOSE_LINES) + nl_, 1)
                    write_raw(cp, s.encode("utf-8"))
                    changed.append("docker-compose.yml (ODYSSEUS_TIMEOUT_* passthrough)")
                    break
            else:
                return "warn", "docker-compose.yml: REPEAT_* passthrough anchor not found -- manual review needed"

    if changed:
        return "patched", "; ".join(changed)
    return "skip", "all timeout budgets already patched"


def rebuild(root, no_docker):
    if no_docker:
        return "manual", "--no-docker given"
    if not shutil.which("docker"):
        return "manual", "docker CLI not found on this host"
    r = sh(["docker", "compose", "build", "--no-cache", "--build-arg", "INSTALL_OPTIONAL=true", "odysseus"], cwd=str(root))
    if r.returncode == 0:
        return "patched", "odysseus image rebuilt with INSTALL_OPTIONAL=true"
    tail = (r.stdout or "")[-300:] + (r.stderr or "")[-300:]
    return "warn", f"build failed:\n{tail}"


# --- 14. vision timeout budget (document_processor.py + compose) --------
# The VL call in analyze_image_with_vl_result had a stock hardcoded
# timeout=120. A thinking VL model (qwen3.8) spends 30-60s reasoning
# before the first description token, and a long agent turn leaves the
# endpoint busy -- so real analysis routinely needs 100-200s and the 120s
# read budget killed it right at the edge (every "VL model unavailable"
# incident in the logs was a timeout miss). Same _env_timeout pattern as
# section 13: env-overridable, read at call time, default 300.

VISION_CALL_OLD = "llm_call(_url, _model, vl_messages, headers=_headers, timeout=120)"
VISION_CALL_NEW = ("llm_call(_url, _model, vl_messages, headers=_headers, "
                  "timeout=_env_timeout('ODYSSEUS_TIMEOUT_VISION', 300))")
# Section 12 may already have added the max_tokens arg to the same call;
# handle that variant too (same timeout upgrade, arg preserved).
VISION_CALL_OLD2 = "llm_call(_url, _model, vl_messages, headers=_headers, timeout=120, max_tokens=(max_tokens or 0))"
VISION_CALL_NEW2 = ("llm_call(_url, _model, vl_messages, headers=_headers, "
                    "timeout=_env_timeout('ODYSSEUS_TIMEOUT_VISION', 300), max_tokens=(max_tokens or 0))")
VISION_COMPOSE_LINE = "      - ODYSSEUS_TIMEOUT_VISION=${ODYSSEUS_TIMEOUT_VISION:-300}"
VISION_COMPOSE_ANCHOR = "      - ODYSSEUS_TIMEOUT_RESEARCH=${ODYSSEUS_TIMEOUT_RESEARCH:-600}"


def patch_vision_timeout(root):
    """Widen the vision (VL) call budget; env-overridable. Independent
    halves: (a) document_processor.py helper, (b) its call site,
    (c) docker-compose.yml passthrough. Returns (status, detail)."""
    changed = []
    dp = root / "src" / "document_processor.py"
    s, nl = _lines_of(dp)
    helper_done = "def _env_timeout" in s
    if not helper_done:
        anchor = "def analyze_image_with_vl_result"
        if s.count(anchor) == 1:
            s = s.replace(anchor, _TIMEOUT_HELPER + "\n" + anchor, 1)
            helper_done = True
            changed.append("document_processor.py (_env_timeout helper)")
    vision_done = "ODYSSEUS_TIMEOUT_VISION" in s
    if not vision_done:
        if VISION_CALL_OLD in s:
            s = s.replace(VISION_CALL_OLD, VISION_CALL_NEW, 1)
            vision_done = True
            changed.append("document_processor.py (vision call site)")
        elif VISION_CALL_OLD2 in s:
            s = s.replace(VISION_CALL_OLD2, VISION_CALL_NEW2, 1)
            vision_done = True
            changed.append("document_processor.py (vision call site, max_tokens variant)")
    if changed:
        _write(dp, s, nl)

    # (c) compose passthrough -- independent of (a)/(b)
    cp = compose_path(root)
    if cp.is_file():
        cs = read_raw(cp).decode("utf-8")
        if "ODYSSEUS_TIMEOUT_VISION" not in cs:
            for nl_ in ("\n", "\r\n"):
                anchor = VISION_COMPOSE_ANCHOR + nl_
                if anchor in cs:
                    cs = cs.replace(anchor, anchor + VISION_COMPOSE_LINE + nl_, 1)
                    write_raw(cp, cs.encode("utf-8"))
                    changed.append("docker-compose.yml (ODYSSEUS_TIMEOUT_VISION passthrough)")
                    break

    if helper_done and vision_done:
        if changed:
            return "patched", "; ".join(changed)
        return "skip", "already patched"
    return "warn", ("document_processor.py: upstream likely changed the vision "
                    "call (helper/timeout=120 anchor) -- manual review needed")


# ------------------------------------------------------------- main --

STATUS_LABEL = {"patched": "PATCHED", "skip": "ok (already)", "none": "ok (n/a)", "manual": "MANUAL", "warn": "WARNING"}


# ---------------------- 15. Reload-freeze on running chats (3 files) --------
#
# Reopening a chat while its run is still in flight froze the tab:
#   1) src/agent_runs.py kept an UNBOUNDED per-run SSE replay buffer (every
#      event of the whole run, forever) and replayed all of it on reconnect.
#   2) static/js/chat.js resumeStream() re-rendered the ENTIRE accumulated
#      round (markdown parse + innerHTML + scroll) on EVERY replayed token --
#      O(n^2) markdown work: a multi-hour run meant minutes of frozen JS.
#   3) SQLite ran in the default journal mode, so paginated history reads
#      could stall on the write lock while the agent persisted big messages.
# Fix: cap the replay buffer (2000 events) with a replay_seq offset and a
# replay_complete marker event; the client fast-forwards replayed deltas
# (accumulate only, one full render at the marker); SQLite gets WAL +
# busy_timeout so reads no longer block on agent writes.

def patch_resume_freeze(root):
    """Reopening a running chat no longer freezes the tab. Three independent
    halves (agent_runs.py buffer cap, chat.js replay fast-forward,
    core/database.py WAL + busy_timeout); a partially-patched checkout
    converges on the next run."""
    warns = []
    did = []

    # ---- (a) src/agent_runs.py: bounded replay buffer + replay_complete ----
    p = root / "src" / "agent_runs.py"
    if not p.is_file():
        warns.append(f"{p.name} not found")
    elif "replay_complete" in read_raw(p).decode("utf-8", "replace"):
        pass
    else:
        s, nl = _lines_of(p)
        edits = [
            # 1. __slots__ gains replay_seq
            ("    __slots__ = (\"buffer\", \"subscribers\", \"status\", \"task\", \"evict_task\", \"run_id\")",
             "    __slots__ = (\"buffer\", \"replay_seq\", \"subscribers\", \"status\", \"task\", \"evict_task\", \"run_id\")"),
            # 2. __init__ initializes the offset
            ("        self.buffer: list = []          # ordered SSE event strings (replay log)",
             "        self.buffer: list = []          # ordered SSE event strings (replay log, capped)\n"
             "        # 0-based index (in the original untrimmed stream) of the\n"
             "        # oldest event still in the buffer -- grows when the cap trims old events.\n"
             "        self.replay_seq: int = 0"),
            # 3. the cap constant
            ("_EVICT_GRACE_S = 180",
             "_EVICT_GRACE_S = 180\n"
             "\n"
             "# Replay-buffer cap (events). An unbounded per-run log let a multi-hour\n"
             "# run's whole event stream be replayed to a reconnecting client; the cap\n"
             "# bounds both memory and reconnect replay size. Older events are already\n"
             "# persisted in the session's message history, so a partial replay is fine\n"
             "# -- the client fast-forwards it (replay_complete marker).\n"
             "_REPLAY_MAX_EVENTS = 2000"),
            # 4. _publish trims the buffer and tracks how much was dropped
            ("    run.buffer.append(ev)\n"
             "    seq = len(run.buffer) - 1\n"
             "    for q in list(run.subscribers):\n"
             "        try:\n"
             "            q.put_nowait((seq, ev))\n"
             "        except Exception:\n"
             "            pass",
             "    run.buffer.append(ev)\n"
             "    overflow = len(run.buffer) - _REPLAY_MAX_EVENTS\n"
             "    if overflow > 0:\n"
             "        # Bounded memory: drop the oldest replay events. replay_seq\n"
             "        # tracks how many original events were dropped so subscribe()\n"
             "        # can mark the replay as partial.\n"
             "        del run.buffer[:overflow]\n"
             "        run.replay_seq += overflow\n"
             "    seq = len(run.buffer) - 1\n"
             "    for q in list(run.subscribers):\n"
             "        try:\n"
             "            q.put_nowait((run.replay_seq + seq, ev))\n"
             "        except Exception:\n"
             "            pass"),
            # 5. subscribe: partial-replay marker + replay_complete end marker
            ("        next_seq = 0\n"
             "        while next_seq < len(run.buffer):\n"
             "            yield run.buffer[next_seq]\n"
             "            next_seq += 1",
             "        next_seq = 0\n"
             "        if run.replay_seq > 0:\n"
             "            # Replay is partial (oldest events dropped to bound the\n"
             "            # buffer): tell the client so it can treat it as fast-forward.\n"
             "            yield 'event: replay_partial\\ndata: {}\\n\\n'\n"
             "        while next_seq < len(run.buffer):\n"
             "            yield run.buffer[next_seq]\n"
             "            next_seq += 1\n"
             "        # End of replay: everything after this marker is live. The client\n"
             "        # uses it to switch from fast-forward accumulation to rendering.\n"
             "        yield 'event: replay_complete\\ndata: {}\\n\\n'"),
        ]
        missing = [i for i, (old, _n) in enumerate(edits, 1) if old not in s]
        if missing:
            warns.append("agent_runs.py: anchor(s) " + ",".join(map(str, missing)) + " missing -- upstream likely restructured the replay machinery; manual review needed")
        else:
            for old, new in edits:
                s = s.replace(old, new, 1)
            _write(p, s, nl)
            did.append("agent_runs.py buffer cap")

    # ---- (b) static/js/chat.js: fast-forward the replay, render once ----
    p = root / "static" / "js" / "chat.js"
    if not p.is_file():
        warns.append(f"{p.name} not found")
    elif "fastForward" in read_raw(p).decode("utf-8", "replace"):
        pass
    else:
        s, nl = _lines_of(p)
        edits = [
            # 1. the flag
            ("    let gotDelta = false;",
             "    let gotDelta = false;\n"
             "    let fastForward = true;   // replay fast-forward until the replay_complete marker"),
            # 2. the delta branch: accumulate during fast-forward, render after
            ("            roundText += json.delta;\n"
             "            if (!docFenceOpened && (roundText.includes('```create_document\\n') || roundText.includes('```document\\n') || roundText.includes('```documen\\n'))) {\n"
             "              docFenceOpened = true;\n"
             "              rich = true;\n"
             "            }\n"
             "            if (!gotDelta) { gotDelta = true; try { spinner.destroy(); } catch (_) {} }\n"
             "            renderDelta();",
             "            roundText += json.delta;\n"
             "            if (!gotDelta) { gotDelta = true; try { spinner.destroy(); } catch (_) {} }\n"
             "            if (fastForward) {\n"
             "              // Replay fast-forward: accumulate without re-rendering every token --\n"
             "              // re-parsing the whole round per token was O(n^2) and froze the\n"
             "              // tab on long replays. One full render happens at replay_complete.\n"
             "              if (!contentDiv.innerHTML) contentDiv.innerHTML = '<div style=\"opacity:.6;font-style:italic\">Catching up with the running response...</div>';\n"
             "              if (!docFenceOpened && roundText.length % 8192 < json.delta.length) {\n"
             "                if (roundText.includes('```create_document\\n') || roundText.includes('```document\\n') || roundText.includes('```documen\\n')) { docFenceOpened = true; rich = true; }\n"
             "              }\n"
             "            } else {\n"
             "              if (!docFenceOpened && (roundText.includes('```create_document\\n') || roundText.includes('```document\\n') || roundText.includes('```documen\\n'))) {\n"
             "                docFenceOpened = true;\n"
             "                rich = true;\n"
             "              }\n"
             "              renderDelta();\n"
             "            }"),
            # 3. the replay_complete marker: one full render, then live mode
            ("          let json;\n"
             "          try { json = JSON.parse(payload); } catch (_) { continue; }\n"
             "          if (eventIsError) {\n"
             "            replayError = createTerminalStreamError(json);",
             "          if (part.split('\\n').some(l => l.trim() === 'event: replay_complete')) {\n"
             "            // End of (possibly partial) buffer replay: render the accumulated\n"
             "            // round once, then switch to normal per-token rendering.\n"
             "            if (fastForward) { fastForward = false; renderDelta(); }\n"
             "            continue;\n"
             "          }\n"
             "          let json;\n"
             "          try { json = JSON.parse(payload); } catch (_) { continue; }\n"
             "          if (eventIsError) {\n"
             "            replayError = createTerminalStreamError(json);"),
        ]
        missing = [i for i, (old, _n) in enumerate(edits, 1) if old not in s]
        if missing:
            warns.append("chat.js: anchor(s) " + ",".join(map(str, missing)) + " missing -- upstream likely rewrote resumeStream; manual review needed")
        else:
            for old, new in edits:
                s = s.replace(old, new, 1)
            _write(p, s, nl)
            did.append("chat.js fast-forward")

    # ---- (c) core/database.py: WAL + busy_timeout ----
    p = root / "core" / "database.py"
    if not p.is_file():
        warns.append(f"{p.name} not found")
    elif "PRAGMA journal_mode=WAL" in read_raw(p).decode("utf-8", "replace"):
        pass
    else:
        s, nl = _lines_of(p)
        old = '        cursor.execute("PRAGMA foreign_keys=ON")'
        new = ('        cursor.execute("PRAGMA foreign_keys=ON")\n'
               "        # Local patch: WAL lets paginated history reads proceed while the agent\n"
               "        # persists large messages (no write-lock stalls on chat reload);\n"
               "        # busy_timeout waits on the lock instead of raising immediately.\n"
               '        cursor.execute("PRAGMA journal_mode=WAL")\n'
               '        cursor.execute("PRAGMA busy_timeout=10000")')
        if old not in s:
            warns.append("database.py: PRAGMA hook anchor missing -- upstream likely restructured set_sqlite_pragma; manual review needed")
        else:
            _write(p, s.replace(old, new, 1), nl)
            did.append("SQLite WAL")

    if warns:
        return "warn", "; ".join(warns)
    if did:
        return "patched", " + ".join(did)
    return "skip", "already patched"


# ---------------------- 16. Prefill keepalive for long contexts (5 files) ------
#
# Large contexts (100k-250k+ tokens) make the local model spend 5-13 minutes
# pre-filling before the first output token (measured on this install: 700-800s
# at 236k prompt tokens). During that window the SSE connection is COMPLETELY
# silent. Two stock behaviors combine into a hard failure at exactly 6 minutes:
#   1) The browser aborts an agent stream that sends nothing for 6 minutes
#      (RESEARCH_TIMEOUT_MS = 360000, static/js/chat.js) and shows "Response
#      timed out" -- even though the server-side round would have completed
#      (logs show 1200s+ rounds finishing normally, so the server side is not
#      the killer).
#   2) The server DOES emit agent_prep early, but the route's event chain has
#      no catch-all and silently drops it (and any other unrecognized data
#      event), so the browser's watchdog is never cleared.
# Fix: stream_llm() emits a `stream_keepalive` data event after N seconds of
# silence (ODYSSEUS_PREFILL_KEEPALIVE_S, default 30, 0 = off) via a feeder-task
# + queue wrapper (cancelling a wait on __anext__ closes the inner async
# generator -- verified on this Python -- so the inner stream is drained by a
# task and the queue wait is the cancellable point). The route chains gain a
# catch-all `else: yield chunk` so agent_prep AND keepalives reach the browser;
# the agent round loop forwards keepalives explicitly; the browser treats a
# stream_keepalive as activity (clears the watchdog) and keeps the spinner
# honest ("pre-filling context").

def _prefill_keepalive_block():
    return (
        "def _prefill_keepalive_interval() -> float:\n"
        "    \"\"\"Local patch: seconds of stream silence before a stream_keepalive event.\n"
        "    A large-context prefill emits zero bytes for minutes; a silent SSE\n"
        "    connection looks dead to the client (the browser's 6-minute watchdog\n"
        "    aborts it). 0 disables keepalives. Tune via ODYSSEUS_PREFILL_KEEPALIVE_S.\"\"\"\n"
        "    try:\n"
        "        return max(0.0, float(os.getenv(\"ODYSSEUS_PREFILL_KEEPALIVE_S\", \"30\")))\n"
        "    except (TypeError, ValueError):\n"
        "        return 30.0\n"
    )


def patch_prefill_keepalive(root):
    """Keepalive events during long prefills so the browser watchdog stays
    quiet. Five independent halves; a partially-patched checkout converges on
    the next run. Returns (status, detail)."""
    changed = []
    warns = []

    # -- (a) src/llm_core.py: keepalive wrapper + fallback passthrough --------
    p = root / "src" / "llm_core.py"
    if p.is_file():
        s, nl = _lines_of(p)
        a_changed = []
        helper_anchor = (
            "def _stream_timeout(read_timeout) -> httpx.Timeout:\n"
            "    \"\"\"Per-request timeout for streaming LLM calls (connect from config).\"\"\"\n"
            "    return httpx.Timeout(connect=LLMConfig.CONNECT_TIMEOUT, read=float(read_timeout), write=30.0, pool=5.0)"
        )
        w_old = ("    target_url = _stream_target_url(url)\n"
                 "    async with _local_model_slot(target_url, model, workload):\n"
                 "        async for chunk in _stream_llm_inner(\n"
                 "            url,\n"
                 "            model,\n"
                 "            messages,\n"
                 "            temperature=temperature,\n"
                 "            max_tokens=max_tokens,\n"
                 "            headers=headers,\n"
                 "            timeout=timeout,\n"
                 "            prompt_type=prompt_type,\n"
                 "            tools=tools,\n"
                 "            session_id=session_id,\n"
                 "            tool_choice_none=tool_choice_none,\n"
                 "        ):\n"
                 "            yield chunk")
        w_new = ("    target_url = _stream_target_url(url)\n"
                 "    async with _local_model_slot(target_url, model, workload):\n"
                 "        # Local patch: prefill keepalive. A feeder task drains the inner\n"
                 "        # stream into a queue; we race the queue wait against a timer and\n"
                 "        # emit a stream_keepalive event on silence. The queue wait is the\n"
                 "        # cancellable point -- cancelling a wait on __anext__ would close\n"
                 "        # the inner async generator and kill the model request.\n"
                 "        _ka_interval = _prefill_keepalive_interval()\n"
                 "        _ka_inner = _stream_llm_inner(\n"
                 "            url,\n"
                 "            model,\n"
                 "            messages,\n"
                 "            temperature=temperature,\n"
                 "            max_tokens=max_tokens,\n"
                 "            headers=headers,\n"
                 "            timeout=timeout,\n"
                 "            prompt_type=prompt_type,\n"
                 "            tools=tools,\n"
                 "            session_id=session_id,\n"
                 "            tool_choice_none=tool_choice_none,\n"
                 "        )\n"
                 "        if not _ka_interval:\n"
                 "            async for chunk in _ka_inner:\n"
                 "                yield chunk\n"
                 "            return\n"
                 "        _ka_q: asyncio.Queue = asyncio.Queue()\n"
                 "        _ka_sentinel = object()\n"
                 "\n"
                 "        async def _ka_feed():\n"
                 "            try:\n"
                 "                async for _c in _ka_inner:\n"
                 "                    await _ka_q.put(_c)\n"
                 "            except BaseException as _e:\n"
                 "                await _ka_q.put(_e)\n"
                 "            finally:\n"
                 "                await _ka_q.put(_ka_sentinel)\n"
                 "\n"
                 "        _ka_feeder = asyncio.create_task(_ka_feed())\n"
                 "        try:\n"
                 "            while True:\n"
                 "                try:\n"
                 "                    _item = await asyncio.wait_for(_ka_q.get(), timeout=_ka_interval)\n"
                 "                except asyncio.TimeoutError:\n"
                 "                    yield 'data: ' + json.dumps({\"type\": \"stream_keepalive\"}) + '\\n\\n'\n"
                 "                    continue\n"
                 "                if _item is _ka_sentinel:\n"
                 "                    break\n"
                 "                if isinstance(_item, BaseException):\n"
                 "                    raise _item\n"
                 "                yield _item\n"
                 "        finally:\n"
                 "            if not _ka_feeder.done():\n"
                 "                _ka_feeder.cancel()")
        f_old = ("                if substantive or emitted:\n"
                 "                    yield chunk\n"
                 "                elif not is_done:\n"
                 "                    pending_metadata.append(chunk)")
        f_new = ("                if substantive or emitted:\n"
                 "                    yield chunk\n"
                 "                elif event_type == \"stream_keepalive\":\n"
                 "                    # Local patch: prefill heartbeat -- forward immediately\n"
                 "                    # (it is a liveness signal, not metadata to flush at\n"
                 "                    # first output) but never let it commit a candidate.\n"
                 "                    yield chunk\n"
                 "                elif not is_done:\n"
                 "                    pending_metadata.append(chunk)")

        a1_todo = "_prefill_keepalive_interval" not in s
        a2_todo = "_ka_interval" not in s
        a3_todo = 'event_type == "stream_keepalive"' not in s
        # Pre-validate all anchors BEFORE applying any, so a drifted checkout
        # never ends up half-patched (orphan helper, or wrapper without helper).
        a_problems = []
        if a1_todo and s.count("def _stream_timeout(read_timeout) -> httpx.Timeout:") != 1:
            a_problems.append("llm_core.py: _stream_timeout anchor count!=1 -- upstream restructured the timeout helpers")
        if a2_todo and s.count(w_old) != 1:
            a_problems.append("llm_core.py: stream_llm wrapper anchor count!=1 -- upstream restructured stream_llm")
        if a3_todo and s.count(f_old) != 1:
            a_problems.append("llm_core.py: with_fallback substantive block anchor count!=1")
        if a_problems:
            warns.extend(a + " -- manual review needed" for a in a_problems)
        else:
            if a1_todo:
                s = s.replace(helper_anchor, helper_anchor + "\n\n" + _prefill_keepalive_block(), 1)
                a_changed.append("keepalive interval helper")
            if a2_todo:
                s = s.replace(w_old, w_new, 1)
                a_changed.append("stream_llm keepalive wrapper")
            if a3_todo:
                s = s.replace(f_old, f_new, 1)
                a_changed.append("with_fallback keepalive passthrough")
            if a_changed:
                _write(p, s, nl)
                changed.append("llm_core.py (" + ", ".join(a_changed) + ")")

    # -- (b) routes/chat_routes.py: catch-all forwarding in both event chains --
    p = root / "routes" / "chat_routes.py"
    if p.is_file():
        s, nl = _lines_of(p)
        b_changed = []
        b_sites = [
            # agent chain: after the metrics elif
            ("                                    yield f'data: {json.dumps(_metrics_event)}\\n\\n'\n"
             "                            except json.JSONDecodeError:\n"
             "                                yield chunk",
             "                                    yield f'data: {json.dumps(_metrics_event)}\\n\\n'\n"
             "                                else:\n"
             "                                    # Local patch: forward unrecognized data events\n"
             "                                    # (agent_prep, stream_keepalive). Stock dropped\n"
             "                                    # them here, so the browser never saw early\n"
             "                                    # progress and its 6-minute watchdog fired\n"
             "                                    # during long prefills.\n"
             "                                    yield chunk\n"
             "                            except json.JSONDecodeError:\n"
             "                                yield chunk"),
            # chat chain: after its metrics yield
            ("                                    yield f'data: {json.dumps({\"type\": \"metrics\", \"data\": last_metrics})}\\n\\n'\n"
             "                            except json.JSONDecodeError:\n"
             "                                yield chunk",
             "                                    yield f'data: {json.dumps({\"type\": \"metrics\", \"data\": last_metrics})}\\n\\n'\n"
             "                                else:\n"
             "                                    # Local patch: forward unrecognized data events\n"
             "                                    # (stream_keepalive) so the browser watchdog\n"
             "                                    # stays quiet during long prefills.\n"
             "                                    yield chunk\n"
             "                            except json.JSONDecodeError:\n"
             "                                yield chunk"),
        ]
        for i, (o, n) in enumerate(b_sites):
            if n in s:
                continue  # already patched (idempotent)
            if s.count(o) != 1:
                warns.append(f"chat_routes.py: catch-all anchor #{i+1} count!=1 -- upstream restructured the SSE chain; manual review needed")
            else:
                s = s.replace(o, n, 1)
                b_changed.append(f"chain #{i+1} catch-all")
        if b_changed:
            _write(p, s, nl)
            changed.append("chat_routes.py (" + ", ".join(b_changed) + ")")

    # -- (c) src/agent_loop.py: forward keepalives through the round loop ------
    p = root / "src" / "agent_loop.py"
    if p.is_file():
        s, nl = _lines_of(p)
        c_changed = []
        c1_old = '                    "error" if chunk.startswith("event: error") else "data",'
        c1_new = '                    "error" if chunk.startswith("event: error") else ("keepalive" if "stream_keepalive" in chunk else "data"),'
        if c1_old in s and c1_new not in s:
            if s.count(c1_old) != 1:
                warns.append("agent_loop.py: first_event kind line count!=1 -- manual review needed")
            else:
                s = s.replace(c1_old, c1_new, 1)
                c_changed.append("first_event kind")
        c2_old = ('                    elif data.get("error"):\n'
                  '                        err_msg = data.get("error", "unknown")\n'
                  '                        logger.error(f"Agent round {round_num}: stream error: {err_msg}")')
        c2_new = ('                    elif data.get("type") == "stream_keepalive":\n'
                  '                        # Local patch: prefill heartbeat from the stream\n'
                  '                        # wrapper -- forward to the client so its watchdog\n'
                  '                        # stays quiet while a large context pre-fills.\n'
                  '                        yield chunk\n' + c2_old)
        if 'data.get("type") == "stream_keepalive"' not in s:
            if s.count(c2_old) != 1:
                warns.append("agent_loop.py: round-loop error-branch anchor count!=1 -- manual review needed")
            else:
                s = s.replace(c2_old, c2_new, 1)
                c_changed.append("keepalive forwarding")
        if c_changed:
            _write(p, s, nl)
            changed.append("agent_loop.py (" + ", ".join(c_changed) + ")")

    # -- (d) static/js/chat.js: watchdog cleared by keepalives + spinner hint --
    p = root / "static" / "js" / "chat.js"
    if p.is_file():
        s, nl = _lines_of(p)
        d_changed = []
        d1_old = "json.type === 'doc_stream_delta' || json.type === 'research_progress') {"
        d1_new = "json.type === 'doc_stream_delta' || json.type === 'research_progress' || json.type === 'stream_keepalive') {"
        if d1_old in s and d1_new not in s:
            if s.count(d1_old) != 1:
                warns.append("chat.js: watchdog clear-list anchor count!=1 -- manual review needed")
            else:
                s = s.replace(d1_old, d1_new, 1)
                d_changed.append("watchdog clear-list")
        d2_old = ("              if (json.type === 'agent_prep') {\n"
                  "                if (!_isBg) {\n"
                  "                  _cancelThinkingTimer();\n"
                  "                  _replaceThinkingSpinner('Preparing agent');\n"
                  "                }\n"
                  "                continue;\n"
                  "              }")
        d2_new = d2_old + (
            "\n"
            "              if (json.type === 'stream_keepalive') {\n"
            "                // Local patch: prefill heartbeat from the server -- the model\n"
            "                // is alive but has emitted no tokens yet (large-context\n"
            "                // pre-fill). Keeps the spinner honest so a 5-10 minute prefill\n"
            "                // does not look like a frozen tab.\n"
            "                if (spinner && spinner.element && !roundText) {\n"
            "                  spinner.updateMessage('Model is pre-filling context - no tokens yet. Large contexts can take several minutes.');\n"
            "                }\n"
            "                continue;\n"
            "              }")
        if "Model is pre-filling context - no tokens yet" not in s:
            if s.count(d2_old) != 1:
                warns.append("chat.js: agent_prep branch anchor count!=1 -- manual review needed")
            else:
                s = s.replace(d2_old, d2_new, 1)
                d_changed.append("keepalive branch")
        if d_changed:
            _write(p, s, nl)
            changed.append("chat.js (" + ", ".join(d_changed) + ")")

    # -- (e) docker-compose.yml: forward the tuning var ------------------------
    cp = compose_path(root)
    if cp is not None and cp.is_file():
        cs = read_raw(cp).decode("utf-8")
        if "ODYSSEUS_PREFILL_KEEPALIVE_S" not in cs:
            anchor = "      - ODYSSEUS_REPEAT_GRAM_COUNT=${ODYSSEUS_REPEAT_GRAM_COUNT:-100}"
            line = "      - ODYSSEUS_PREFILL_KEEPALIVE_S=${ODYSSEUS_PREFILL_KEEPALIVE_S:-30}"
            for nl_ in ("\n", "\r\n"):
                if anchor + nl_ in cs:
                    cs = cs.replace(anchor + nl_, anchor + nl_ + line + nl_, 1)
                    write_raw(cp, cs.encode("utf-8"))
                    changed.append("docker-compose.yml (ODYSSEUS_PREFILL_KEEPALIVE_S)")
                    break
            else:
                warns.append("docker-compose.yml: REPEAT_GRAM_COUNT anchor not found -- manual review needed")

    if warns:
        return "warn", "; ".join(warns)
    if changed:
        return "patched", "; ".join(changed)
    return "skip", "already patched"



# ---------------------------------------------------------------------------
# Section 17: context-overflow recovery -- learn the REAL served context
# window from the backend's exceed_context_size error, scale token budgets
# by the measured estimate->real ratio, and trim-and-retry instead of
# ending the run. (model_context.py + agent_loop.py + context_compactor.py)
# ---------------------------------------------------------------------------


def patch_context_overflow(root):
    """Learn the real context window from overflow 400s + recover instead of dying.

    Stock behaviour (the failure mode this fixes): a router like llama-swap
    can report a model's catalog contextLength (e.g. 1048576) that the serving
    instance does not actually run with (e.g. a 262144 slot). Odysseus budgets
    against the claimed window, so soft-trim and compaction never engage; the
    first round whose REAL prompt exceeds the real window 400s, the agent loop
    sees a stream error with no tool calls, and the run ends.

    This adds:
      (a) model_context: learn_real_context() records n_ctx + the measured
          estimate->real token ratio from an overflow error; the cached context
          lookup returns the learned value (ground truth) over the catalog.
      (b) agent_loop: the event branch captures n_ctx/n_prompt_tokens from the
          400; the round loop then trims the live history to a safe budget
          (85% of the real window in REAL tokens, converted to estimate tokens
          via the ratio) and retries the round once per overflow.
      (c) agent_loop: the soft-trim budget is divided by the learned ratio so
          budgets expressed in estimate tokens are honest.
      (d) context_compactor: maybe_compact scales its usage number by the
          learned ratio so compaction engages on the same honest basis.
    """
    # ---- (a) src/model_context.py ----------------------------------------
    f = root / "src" / "model_context.py"
    if not f.is_file():
        return "skip", "not found"
    s, nl = _lines_of(f)
    NEW_A = "_real_ctx_overrides"
    if NEW_A in s:
        # already patched -- verify the cache override hook too
        if "Using learned real context window" in s:
            return "skip", "already patched"
        return "warn", "helper present but cache override hook missing (drift?)"

    anchors = [
        '_context_cache: Dict[Tuple[str, str], Tuple[int, bool]] = {}\n\n\n',
        '    cache_key = (endpoint_url, model)\n'
        '    if not is_local and cache_key in _context_cache:\n'
        '        return _context_cache[cache_key]\n',
    ]
    for a in anchors:
        if s.count(a) != 1:
            return "warn", (
                f"upstream changed model_context.py (anchor found {s.count(a)}x); "
                "manual review needed -- see section 17 in patch_odysseus.py"
            )

    helper = (
        '_context_cache: Dict[Tuple[str, str], Tuple[int, bool]] = {}\n'
        '\n'
        '\n'
        '# Local patch: the backend\'s ACTUALLY-enforced context window, learned\n'
        '# from exceed_context_size errors (their n_ctx field). A router\'s\n'
        '# /v1/models catalog can claim a window (e.g. 1M) the serving instance\n'
        '# does not really run with (e.g. a 262k slot), so the error is ground\n'
        '# truth. _real_ctx_ratios stores the measured estimate->real token ratio\n'
        '# (estimate_tokens undercounts dense code/table content by ~40-70%), so\n'
        '# budgets and compaction thresholds can be expressed honestly.\n'
        '_real_ctx_overrides: Dict[Tuple[str, str], int] = {}\n'
        '_real_ctx_ratios: Dict[Tuple[str, str], float] = {}\n'
        '\n'
        '\n'
        'def learn_real_context(endpoint_url: str, model: str, n_ctx: int,\n'
        '                       n_prompt_tokens: int = 0, est_tokens: int = 0) -> int:\n'
        '    """Record the context window the backend actually enforces (from an\n'
        '    exceed_context_size error) and, when both the real prompt size and\n'
        '    our estimate are known, the measured estimate->real token ratio."""\n'
        '    key = (endpoint_url, model)\n'
        '    try:\n'
        '        n_ctx = int(n_ctx or 0)\n'
        '    except (TypeError, ValueError):\n'
        '        n_ctx = 0\n'
        '    if n_ctx > 0:\n'
        '        _real_ctx_overrides[key] = n_ctx\n'
        '    try:\n'
        '        n_prompt_tokens = int(n_prompt_tokens or 0)\n'
        '        est_tokens = int(est_tokens or 0)\n'
        '    except (TypeError, ValueError):\n'
        '        n_prompt_tokens = est_tokens = 0\n'
        '    if n_prompt_tokens > 0 and est_tokens > 0:\n'
        '        ratio = n_prompt_tokens / est_tokens\n'
        '        if 1.0 <= ratio <= 5.0:\n'
        '            _real_ctx_ratios[key] = ratio\n'
        '    return _real_ctx_overrides.get(key, 0)\n'
        '\n'
        '\n'
        'def get_real_context(endpoint_url: str, model: str) -> int:\n'
        '    return _real_ctx_overrides.get((endpoint_url, model), 0)\n'
        '\n'
        '\n'
        'def get_real_context_ratio(endpoint_url: str, model: str) -> float:\n'
        '    return _real_ctx_ratios.get((endpoint_url, model), 1.0)\n'
        '\n'
        '\n'
    )
    s = s.replace(anchors[0], helper, 1)

    hook = (
        '    cache_key = (endpoint_url, model)\n'
        '    if not is_local and cache_key in _context_cache:\n'
        '        return _context_cache[cache_key]\n'
        '\n'
        '    # Local patch: a learned real window (from a backend\n'
        '    # exceed_context_size error) is ground truth -- it overrides the\n'
        '    # catalog claim, which may describe a different serving instance.\n'
        '    override = _real_ctx_overrides.get(cache_key)\n'
        '    if override:\n'
        '        logger.info(f"Using learned real context window for {model}: {override}")\n'
        '        return override, True\n'
    )
    s = s.replace(anchors[1], hook, 1)
    _write(f, s, nl)

    # ---- (b)+(c) src/agent_loop.py ----------------------------------------
    f = root / "src" / "agent_loop.py"
    if not f.is_file():
        return "warn", "model_context patched but agent_loop.py missing"
    s, nl = _lines_of(f)
    NEW_B = "_ctx_overflow = None"
    if NEW_B in s:
        return "skip", "already patched"

    anchors = [
        "from src.model_context import estimate_tokens\n",
        "    for round_num in range(1, max_rounds + 1):\n"
        '        round_response = ""\n'
        '        round_reasoning = ""  # reasoning_content deltas (DeepSeek-thinking, vLLM --reasoning-parser)\n',
        '            elif chunk.startswith("event: "):\n'
        "                # Forward error events to frontend as visible text\n"
        "                yield chunk\n",
        "        round_models.append(_round_actual_model)\n"
        "        round_endpoint_ids.append(_round_actual_endpoint_id)\n"
        "        round_endpoint_labels.append(_round_actual_endpoint_label)\n",
        "            effective_budget = compute_input_token_budget(\n"
        "                soft_budget,\n"
        "                candidate_context,\n"
        "                budget_is_explicit,\n"
        "                hard_max=hard_max,\n"
        "            )\n",
    ]
    for a in anchors:
        if s.count(a) != 1:
            return "warn", (
                f"upstream changed agent_loop.py (anchor found {s.count(a)}x); "
                "manual review needed -- see section 17 in patch_odysseus.py"
            )

    s = s.replace(
        anchors[0],
        "from src.model_context import (\n"
        "    estimate_tokens,\n"
        "    get_real_context_ratio,\n"
        "    learn_real_context,\n"
        ")\n",
        1,
    )
    s = s.replace(
        anchors[1],
        "    for round_num in range(1, max_rounds + 1):\n"
        '        round_response = ""\n'
        '        _ctx_overflow = None  # local patch: (n_ctx, n_prompt_tokens) from an overflow 400\n'
        '        round_reasoning = ""  # reasoning_content deltas (DeepSeek-thinking, vLLM --reasoning-parser)\n',
        1,
    )
    s = s.replace(
        anchors[2],
        '            elif chunk.startswith("event: "):\n'
        "                # Local patch: an exceed_context_size 400 carries the\n"
        "                # backend's REAL context window (n_ctx) -- learn it and\n"
        "                # retry the round with a safe budget instead of ending.\n"
        '                if "exceeds the available context" in chunk or "exceed_context_size" in chunk:\n'
        '                    _mctx = re.search(r\'\\\\?"n_ctx\\\\?"\\s*:\\s*(\\d+)\', chunk)\n'
        '                    _mpt = re.search(r\'\\\\?"n_prompt_tokens\\\\?"\\s*:\\s*(\\d+)\', chunk)\n'
        "                    if _mctx:\n"
        "                        _ctx_overflow = (int(_mctx.group(1)), int(_mpt.group(1)) if _mpt else 0)\n"
        "                        logger.warning(\n"
        "                            f\"[agent] round {round_num}: backend real context limit \"\n"
        "                            f\"{_ctx_overflow[0]} tokens (catalog claimed more) -- will trim and retry\"\n"
        "                        )\n"
        "                # Forward error events to frontend as visible text\n"
        "                yield chunk\n",
        1,
    )
    s = s.replace(
        anchors[3],
        anchors[3] +
        '\n'
        "        # -- Local patch: context-overflow recovery --\n"
        "        # The backend rejected this round's prompt as larger than its real\n"
        "        # context. n_ctx in the error is ground truth even when /v1/models\n"
        "        # claims a bigger window, so learn it (and the estimate->real\n"
        "        # token ratio), trim the live history to a safe budget, and retry\n"
        "        # the round instead of ending the run.\n"
        "        if _ctx_overflow:\n"
        "            _ov_ctx, _ov_prompt = _ctx_overflow\n"
        "            _ov_req = _last_route_request_messages or messages\n"
        "            _ov_est = estimate_tokens(_ov_req)\n"
        "            learn_real_context(endpoint_url, model, _ov_ctx, _ov_prompt, _ov_est)\n"
        "            from src.context_compactor import trim_for_context as _trim_ctx\n"
        "            _ov_ratio = get_real_context_ratio(endpoint_url, model)\n"
        "            _ov_target = max(8000, int(_ov_ctx * 0.85 / max(_ov_ratio, 1.0)))\n"
        "            _ov_before = estimate_tokens(messages)\n"
        "            messages = _trim_ctx(messages, _ov_target, reserve_tokens=1024)\n"
        "            _ov_after = estimate_tokens(messages)\n"
        "            logger.info(\n"
        "                f\"[agent] context-overflow recovery round={round_num}: real limit \"\n"
        "                f\"{_ov_ctx} (prompt was {_ov_prompt}), est {_ov_before} -> {_ov_after} \"\n"
        "                f\"(target est {_ov_target}, ratio {_ov_ratio:.2f})\"\n"
        "            )\n"
        "            _active_route_state[\"messages\"] = messages\n"
        "            _active_route_state.pop(\"request_messages\", None)\n"
        "            _ov_note = (\n"
        "                \"*[Context overflow: the model rejected this round's prompt \"\n"
        "                \"(%s tokens, real limit %s). Trimmed older context and continuing.]*\"\n"
        "                % (\"{:,}\".format(_ov_prompt), \"{:,}\".format(_ov_ctx))\n"
        "            )\n"
        "            yield f\"data: {json.dumps({'delta': chr(10) + chr(10) + _ov_note})}\\n\\n\"\n"
        "            continue\n",
        1,
    )
    s = s.replace(
        anchors[4],
        anchors[4] +
        "            # Local patch: estimate_tokens undercounts dense content by a\n"
        "            # measured ratio (learned from overflow 400s), so express the\n"
        "            # budget in estimate tokens honestly.\n"
        "            _est_ratio = get_real_context_ratio(candidate_url, candidate_model)\n"
        "            if _est_ratio > 1.05:\n"
        "                effective_budget = max(1000, int(effective_budget / _est_ratio))\n",
        1,
    )
    _write(f, s, nl)

    # ---- (d) src/context_compactor.py -------------------------------------
    f = root / "src" / "context_compactor.py"
    if not f.is_file():
        return "warn", "model_context + agent_loop patched but context_compactor.py missing"
    s, nl = _lines_of(f)
    NEW_D = "_real_ratio = get_real_context_ratio(endpoint_url, model)"
    if NEW_D in s:
        return "skip", "already patched"
    anchors = [
        "from src.model_context import get_context_length, estimate_tokens\n",
        "    context_length = get_context_length(endpoint_url, model)\n"
        "    used = estimate_tokens(messages)\n",
    ]
    for a in anchors:
        if s.count(a) != 1:
            return "warn", (
                f"upstream changed context_compactor.py (anchor found {s.count(a)}x); "
                "manual review needed -- see section 17 in patch_odysseus.py"
            )
    s = s.replace(
        anchors[0],
        "from src.model_context import get_context_length, estimate_tokens, get_real_context_ratio\n",
        1,
    )
    s = s.replace(
        anchors[1],
        "    context_length = get_context_length(endpoint_url, model)\n"
        "    used = estimate_tokens(messages)\n"
        "    # Local patch: scale by the measured estimate->real ratio (learned\n"
        "    # from overflow 400s) so compaction engages on an honest basis.\n"
        "    _real_ratio = get_real_context_ratio(endpoint_url, model)\n"
        "    if _real_ratio > 1.05:\n"
        "        used = int(used * _real_ratio)\n",
        1,
    )
    _write(f, s, nl)
    return "patched", "real context learning + overflow recovery (3 files)"


def patch_kv_exhaustion(root):
    """Silent KV-exhaustion guard + continuous ratio learning (agent_loop.py).

    Section 17 recovers from a HARD context-overflow 400 (the backend
    REJECTS the prompt). There is a second, quieter failure: when the
    prompt FITS but input + generated tokens (mostly thinking) fill the
    real context window MID-generation, llama.cpp simply stops. The
    stream ends cleanly -- no 400, no error event -- so the stock loop
    sees "no tool calls" and ends the run. Measured: a 49-round run
    died with usage 253359 in + 8785 out = 262144 = exactly the KV
    cache size.

    This adds, in agent_loop.py:
      (a) the estimate->real token ratio is learned continuously from
          every round's real backend usage (the 400-based learning in
          section 17 never fires when the stream stops cleanly, which is
          exactly when the ratio matters most).
      (b) a KV-exhaustion guard on the "no tools -> done" path: when the
          round produced no visible text and no tool calls, and its real
          usage filled >=98% of the real window, trim the live history to
          a safe budget (85% of the window in honest tokens) and continue
          instead of ending the run. Capped at 3 per run so a
          pathological context can't loop forever.
    """
    f = root / "src" / "agent_loop.py"
    if not f.is_file():
        return "skip", "not found"
    s, nl = _lines_of(f)

    MARK = "_kv_guard_count"
    RATIO_MARK = "learned estimate->real token ratio"
    if MARK in s and RATIO_MARK in s:
        return "skip", "already patched"
    if MARK in s or RATIO_MARK in s:
        return "warn", ("partial state (KV guard and/or ratio learning "
                        "present) -- manual review needed")

    anchors = {
        "import": (
            "from src.model_context import (\n"
            "    estimate_tokens,\n"
            "    get_real_context_ratio,\n"
            "    learn_real_context,\n"
            ")\n"
        ),
        "loop_head": "    for round_num in range(1, max_rounds + 1):\n",
        "finalize": "        _finalize_round_usage()\n",
    }
    for name, a in anchors.items():
        if s.count(a) != 1:
            return "warn", (
                f"upstream changed agent_loop.py (anchor '{name}' found "
                f"{s.count(a)}x) -- manual review needed"
            )
    # The "no tools -> done" branch is at 8-space indent; there is another
    # `if not tool_blocks:` at 16-space indent (fenced-code path) that must
    # NOT be touched. Anchor at line start so only the 8-space one matches.
    guard_pat = re.compile(
        r"^(        if not tool_blocks:\n"
        r"            # \u2500\u2500 Completion verifier \(mechanism 3a\) .*?\n)",
        re.M,
    )
    if len(guard_pat.findall(s)) != 1:
        return "warn", (
            "upstream changed agent_loop.py (the 'no tools -> done' branch "
            "was not found exactly once) -- manual review needed"
        )

    # (a) import: add get_context_length + get_real_context
    new_import = (
        "from src.model_context import (\n"
        "    estimate_tokens,\n"
        "    get_context_length,\n"
        "    get_real_context,\n"
        "    get_real_context_ratio,\n"
        "    learn_real_context,\n"
        ")\n"
    )

    # (b) round-loop counter init (inside the for loop body)
    counter = (
        "    for round_num in range(1, max_rounds + 1):\n"
        "        _kv_guard_count = 0  # local patch: KV-exhaustion retrims this run (max 3)\n"
    )

    # (c) ratio learning after the round's usage is finalized
    ratio_block = (
        "        _finalize_round_usage()\n"
        "\n"
        "        # -- Local patch (section 19): learn the estimate->real\n"
        "        # token ratio from the backend's per-round usage. estimate_tokens\n"
        "        # undercounts dense code/table content; real usage is ground\n"
        "        # truth. The 400-based learning (section 17) never fires when\n"
        "        # the stream stops cleanly at KV exhaustion, which is exactly\n"
        "        # when an honest ratio is needed for the guard below.\n"
        "        if _round_has_real_usage and _round_real_input_tokens > 1000:\n"
        "            _lr_req = _last_route_request_messages or messages\n"
        "            _lr_est = estimate_tokens(_lr_req)\n"
        "            if _lr_est > 0:\n"
        "                _lr_ratio = _round_real_input_tokens / _lr_est\n"
        "                if 1.02 <= _lr_ratio <= 5.0:\n"
        "                    _lr_cur = get_real_context_ratio(endpoint_url, model)\n"
        "                    if _lr_ratio > _lr_cur * 1.02:\n"
        "                        learn_real_context(\n"
        "                            endpoint_url, model, 0,\n"
        "                            _round_real_input_tokens, _lr_est,\n"
        "                        )\n"
        "                        logger.info(\n"
        "                            \"[agent] learned estimate->real token ratio \"\n"
        "                            f\"{_lr_ratio:.2f} (est {_lr_est} vs real \"\n"
        "                            f\"{_round_real_input_tokens})\"\n"
        "                        )\n"
    )

    # (d) the KV-exhaustion guard: its own `if` at 8-space loop-body level,
    #     inserted immediately before the stock "no tools -> done" branch.
    #     When it triggers it trims and `continue`s (skipping the break);
    #     otherwise execution falls through to the stock branch unchanged.
    guard = (
        "        # -- Local patch (section 19): KV-exhaustion guard --\n"
        "        # llama.cpp stops SILENTLY mid-generation (usually during\n"
        "        # thinking) when input + output fills the real context\n"
        "        # window; the stream ends cleanly with no 400, so the stock\n"
        "        # loop mistakes it for a finished turn and ends the run.\n"
        "        # If the backend-reported usage for this round already\n"
        "        # fills >=98% of the real window and nothing visible was\n"
        "        # produced, trim the live history and continue.\n"
        "        if (_round_has_real_usage and not native_tool_calls\n"
        "                and _strip_think_blocks(cleaned_round).strip() == \"\"\n"
        "                and _round_real_input_tokens > 0):\n"
        "            try:\n"
        "                _kv_ctx = (get_real_context(endpoint_url, model)\n"
        "                           or get_context_length(endpoint_url, model))\n"
        "                _kv_room = int(_kv_ctx * 0.98)\n"
        "                if (_round_real_input_tokens + _round_real_output_tokens\n"
        "                        >= _kv_room and _kv_guard_count < 3):\n"
        "                    _kv_guard_count += 1\n"
        "                    from src.context_compactor import trim_for_context as _trim_kv\n"
        "                    _kv_ratio = get_real_context_ratio(endpoint_url, model)\n"
        "                    _kv_target = max(8000, int(_kv_ctx * 0.85 / max(_kv_ratio, 1.0)))\n"
        "                    _kv_before = estimate_tokens(messages)\n"
        "                    messages = _trim_kv(messages, _kv_target, reserve_tokens=1024)\n"
        "                    _kv_after = estimate_tokens(messages)\n"
        "                    _active_route_state[\"messages\"] = messages\n"
        "                    _active_route_state.pop(\"request_messages\", None)\n"
        "                    logger.warning(\n"
        "                        f\"[agent] KV-exhaustion guard round={round_num}: usage \"\n"
        "                        f\"{_round_real_input_tokens}+{_round_real_output_tokens} vs \"\n"
        "                        f\"real window {_kv_ctx} (98% = {_kv_room}); trimmed est \"\n"
        "                        f\"{_kv_before} -> {_kv_after} (target {_kv_target}, \"\n"
        "                        f\"ratio {_kv_ratio:.2f}) and continuing\"\n"
        "                    )\n"
        "                    _kv_note = (\n"
        "                        \"*[Context exhausted mid-generation (input + thinking \"\n"
        "                        f\"filled the model's {_kv_ctx:,} token window). Trimmed \"\n"
        "                        \"older context and continuing.]*\"\n"
        "                    )\n"
        "                    _kv_event = {\"delta\": chr(10) + chr(10) + _kv_note}\n"
        "                    yield \"data: \" + json.dumps(_kv_event) + \"\\n\\n\"\n"
        "                    continue\n"
        "            except Exception as _kv_err:\n"
        "                logger.warning(\n"
        "                    \"[agent] KV-exhaustion guard skipped on round %s: %s\",\n"
        "                    round_num, _kv_err,\n"
        "                )\n"
        "\n"
    )

    out = s
    out = out.replace(anchors["import"], new_import, 1)
    out = out.replace(anchors["loop_head"], counter, 1)
    out = out.replace(anchors["finalize"], ratio_block, 1)
    out, nsub = guard_pat.subn(lambda mm: guard + mm.group(1), out, count=1)
    if nsub != 1 or out == s:
        return "warn", "no anchor replaced (drift?)"
    _write(f, out, nl)
    return "patched", "KV-exhaustion guard + ratio learning"
def patch_degeneration_guard(root: Path):
    """Section 20: agent degeneration guard (2 files: agent_loop.py, compose).

    A thinking model can degenerate two ways, and BOTH end the run
    silently in stock:
      * it emits ONLY thinking for ~80 minutes (no visible text, no tool
        call); the stock round deadline then fires and "no tools -> done"
        ends the run with nothing;
      * the loop-breaker strips tools (force-answer round) but the stock
        intent-without-action supervisor stays active and nudges "call the
        actual tool now" -- which no longer exists in the round, so the
        model repeats its promise as plain text until the nudge cap ends
        the run (the 10-13 04:47 incident: 164 placeholder tool calls,
        then 3x "Let me check what's on your box first").

    Adds (check-before-patch, idempotent, self-healing across the
    intermediate v1 state of this section):
      (a) ODYSSEUS_AGENT_THINKING_STALL_S (default 600, 0 = off): when a
          round has emitted ONLY thinking past the cap, the stream is cut
          short (in-stream detector) and the round is retried with a
          corrective nudge (post-stream guard). Max 2 stall retries per
          run; then the run ends with a visible note.
      (b) the intent-without-action supervisor is skipped on force-answer
          rounds (no tools exist there).
      (c) the loop-breaker nudge explicitly names the placeholder-tool-call
          degeneration so the model knows what went wrong.
      (d) docker-compose.yml passthrough of the env var.

    v1 of this section put the guard INSIDE the stream loop, where its
    `continue` skipped to the next chunk instead of retrying the round
    (and its SSE yield appended a literal "\n\n"). Step (0) below
    removes that broken region when present, then the corrected guard is
    inserted (detector in-stream, retry after the stream).
    """
    OLD_REGION = (
        '            # -- Local patch (section 20): thinking-stall guard --\n'
        '            # The round has been emitting ONLY thinking for longer than\n'
        '            # ODYSSEUS_AGENT_THINKING_STALL_S with no visible text and\n'
        '            # no tool calls. Cut the round short and retry it with a\n'
        '            # corrective nudge instead of waiting out the 80-minute\n'
        '            # stock deadline (which ends the whole run with nothing).\n'
        '            if (_thinking_stall_s > 0\n'
        '                    and not _force_answer\n'
        '                    and not native_tool_calls\n'
        '                    and not round_response\n'
        '                    and round_reasoning\n'
        '                    and time.time() - _stall_last_visible > _thinking_stall_s):\n'
        '                if _stall_retrim_count < 2:\n'
        '                    _stall_retrim_count += 1\n'
        '                    logger.warning(\n'
        '                        "[agent] thinking-stall guard round=%s: %ds of thinking "\n'
        '                        "with no visible output (cap %ds); cutting the round "\n'
        '                        "short and retrying with a nudge (retrim %d/2)",\n'
        '                        round_num,\n'
        '                        int(time.time() - _stall_last_visible),\n'
        '                        int(_thinking_stall_s),\n'
        '                        _stall_retrim_count,\n'
        '                    )\n'
        '                    _stall_note = (\n'
        '                        "*[The model was reasoning for a long time without "\n'
        '                        "progress, so this step was cut short and retried.]*"\n'
        '                    )\n'
        '                    yield "data: " + json.dumps(\n'
        '                        {"delta": chr(10) + chr(10) + _stall_note}\n'
        '                    ) + "\\\\n\\\\n"\n'
        '                    messages.append({\n'
        '                        "role": "system",\n'
        '                        "content": (\n'
        '                            "You were reasoning for a very long time without "\n'
        '                            "producing any visible output or tool call, and "\n'
        '                            "this step was cut short. Do not deliberate in "\n'
        '                            "circles. Either make ONE concrete tool call now, "\n'
        '                            "or write your visible answer/next step in plain "\n'
        '                            "text this turn."\n'
        '                        ),\n'
        '                    })\n'
        '                    _stall_last_visible = time.time()\n'
        '                    continue\n'
        '                else:\n'
        '                    logger.warning(\n'
        '                        "[agent] thinking-stall guard EXHAUSTED on round %s "\n'
        '                        "(2 retrims); ending the round",\n'
        '                        round_num,\n'
        '                    )\n'
        '                    _stall_note2 = (\n'
        '                        "*[The model kept reasoning without progress after "\n'
        '                        "two retries; the run is stopping here.]*"\n'
        '                    )\n'
        '                    yield "data: " + json.dumps(\n'
        '                        {"delta": chr(10) + chr(10) + _stall_note2}\n'
        '                    ) + "\\\\n\\\\n"\n'
        '                    break\n'
        '                logger.warning(\n'
        '                    "[agent-timing] round_deadline round=%s elapsed=%.3fs deadline_s=%s",\n'
        '                    round_num,\n'
        '                    time.time() - _round_start,\n'
        "                    max(agent_stream_timeout * 4, int(os.getenv('ODYSSEUS_AGENT_ROUND_DEADLINE', '3600') or '3600')),\n"
        '                )\n'
        '                break\n'
    )

    f = root / "src" / "agent_loop.py"
    if not f.is_file():
        return "warn", "agent_loop.py missing"
    s, nl = _lines_of(f)

    A_INIT = (
        "    _intent_nudge_count = 0\n"
        "    _MAX_INTENT_NUDGES = 2\n"
    )
    A_ROUND_INIT = (
        "        _round_first_event_logged = False\n"
        "        _round_first_token_logged = False\n"
    )
    A_DEADLINE = (
        "            if time.time() > _round_deadline:\n"
        "                logger.warning(\n"
        "                    \"[agent-timing] round_deadline round=%s elapsed=%.3fs deadline_s=%s\",\n"
        "                    round_num,\n"
        "                    time.time() - _round_start,\n"
        "                    max(agent_stream_timeout * 4, int(os.getenv('ODYSSEUS_AGENT_ROUND_DEADLINE', '3600') or '3600')),\n"
        "                )\n"
        "                break\n"
    )
    A_DELTA = (
        "                        if data.get(\"thinking\"):\n"
        "                            round_reasoning += data[\"delta\"]\n"
        "                        else:\n"
    )
    A_NO_TOOLS = (
        "        if not tool_blocks:\n"
        '            # ── Completion verifier (mechanism 3a) ────────────────────\n'
    )
    A_PROMISE = (
        "            _looks_like_promise = (\n"
        "                not guide_only\n"
        "                and _intent_match is not None\n"
    )
    NEW_INIT = (
        "    # -- Local patch (section 20): thinking-stall guard state --\n"
        "    # A thinking model can spend ~80 minutes reasoning with ZERO\n"
        "    # visible output before the stock round deadline ends the run\n"
        "    # with nothing. Cap that (wall-clock, per round): when it fires\n"
        "    # the round is cut short and retried with a corrective nudge.\n"
        "    # 0 disables the guard.\n"
        "    _thinking_stall_s = max(0.0, float(os.getenv(\"ODYSSEUS_AGENT_THINKING_STALL_S\", \"600\")))\n"
        "    _stall_retrim_count = 0  # local patch: max 2 stall retrims per run\n"
    )
    NEW_ROUND_INIT = (
        "        # -- Local patch (section 20): per-round thinking-stall clock --\n"
        "        _stall_last_visible = time.time()\n"
        "        _stall_flagged = False  # set by the in-stream detector when the cap fires\n"
    )
    DETECTOR = (
        "            # -- Local patch (section 20): thinking-stall detector --\n"
        "            # The round has been emitting ONLY thinking for longer\n"
        "            # than ODYSSEUS_AGENT_THINKING_STALL_S with no visible\n"
        "            # text and no tool calls. Cut the stream short; the\n"
        "            # post-stream guard below retries the round with a\n"
        "            # corrective nudge instead of waiting out the 80-minute\n"
        "            # stock deadline and ending the run with nothing.\n"
        "            if (_thinking_stall_s > 0\n"
        "                    and not _force_answer\n"
        "                    and not native_tool_calls\n"
        "                    and not round_response\n"
        "                    and round_reasoning\n"
        "                    and time.time() - _stall_last_visible > _thinking_stall_s):\n"
        "                logger.warning(\n"
        "                    \"[agent] thinking-stall detector round=%s: >%ds of thinking \"\n"
        "                    \"with no visible output (cap %ds); cutting the round short \"\n"
        "                    \"for a nudge retry\",\n"
        "                    round_num,\n"
        "                    int(time.time() - _stall_last_visible),\n"
        "                    int(_thinking_stall_s),\n"
        "                )\n"
        "                _stall_flagged = True\n"
        "                break\n"
    )
    RESET = (
        "                            # Local patch (section 20): visible progress\n"
        "                            # resets the thinking-stall clock.\n"
        "                            _stall_last_visible = time.time()\n"
    )
    RETRY = (
        "        # -- Local patch (section 20): thinking-stall retry --\n"
        "        # The round above was cut short after emitting only thinking\n"
        "        # past the stall cap. Retry the round with a corrective nudge\n"
        "        # instead of letting \"no tools -> done\" end the run (the stock\n"
        "        # behavior for a thinking model that deliberates itself into\n"
        "        # silence).\n"
        "        if (_stall_flagged and not _force_answer\n"
        "                and not native_tool_calls\n"
        "                and _strip_think_blocks(cleaned_round).strip() == \"\"):\n"
        "            if _stall_retrim_count < 2:\n"
        "                _stall_retrim_count += 1\n"
        "                logger.warning(\n"
        "                    \"[agent] thinking-stall guard round=%s: round cut short after \"\n"
        "                    \">%ds of thinking with no visible output; retrying with a \"\n"
        "                    \"nudge (retry %d/2)\",\n"
        "                    round_num,\n"
        "                    int(_thinking_stall_s),\n"
        "                    _stall_retrim_count,\n"
        "                )\n"
        "                _stall_note = (\n"
        "                    \"*[The model was reasoning for a long time without progress, \"\n"
        "                    \"so this step was cut short and is being retried.]*\"\n"
        "                )\n"
        "                _stall_event = {\"delta\": chr(10) + chr(10) + _stall_note}\n"
        "                yield \"data: \" + json.dumps(_stall_event) + \"\\n\\n\"\n"
        "                messages.append({\n"
        "                    \"role\": \"system\",\n"
        "                    \"content\": (\n"
        "                        \"You were reasoning for a very long time without producing \"\n"
        "                        \"any visible output or tool call, and this step was cut short. \"\n"
        "                        \"Do not deliberate in circles. Either make ONE concrete tool \"\n"
        "                        \"call now, or write your visible answer/next step in plain \"\n"
        "                        \"text this turn.\"\n"
        "                    ),\n"
        "                })\n"
        "                _stall_flagged = False\n"
        "                continue\n"
        "            logger.warning(\n"
        "                \"[agent] thinking-stall guard EXHAUSTED on round %s (2 retries); \"\n"
        "                \"ending the run here\", round_num,\n"
        "            )\n"
        "            _stall_note2 = (\n"
        "                \"*[The model kept reasoning without progress after two retries; \"\n"
        "                \"the run is stopping here.]*\"\n"
        "            )\n"
        "            _stall_event2 = {\"delta\": chr(10) + chr(10) + _stall_note2}\n"
        "            yield \"data: \" + json.dumps(_stall_event2) + \"\\n\\n\"\n"
        "            break\n"
    )
    NEW_PROMISE = (
        "            _looks_like_promise = (\n"
        "                not guide_only\n"
        "                and not _force_answer  # local patch (section 20): no tools on force-answer rounds\n"
        "                and _intent_match is not None\n"
    )
    NEW_LB = (
        "                    \"You're repeating tool calls without converging. If you were \"\n"
        "                    \"emitting the same placeholder tool call (e.g. a ```json block \"\n"
        "                    \"with {...}) over and over, that is a mistake: make ONE real \"\n"
        "                    \"tool call with actual arguments, or stop calling tools and end \"\n"
        "                    \"the turn one of two ways: (a) write your best final answer NOW \"\n"
        "                    \"from the information already gathered, or (b) if you're genuinely \"\n"
        "                    \"blocked, say plainly what's blocking you in a sentence or two.\" + _off_note\n"
        "                ),\n"
    )

    changed = False

    # (0) remove the broken v1 in-stream region (continue-in-stream-loop +
    # literal "\n\n" SSE) when present
    if OLD_REGION in s:
        s = s.replace(OLD_REGION, "", 1)
        changed = True

    # (a) run-level guard state
    if "_thinking_stall_s" not in s:
        if s.count(A_INIT) != 1:
            return "warn", "upstream changed agent_loop.py (init anchor) -- manual review"
        s = s.replace(A_INIT, A_INIT + NEW_INIT, 1)
        changed = True

    # (b) per-round stall clock + flag
    if "_stall_last_visible = time.time()" not in s:
        if s.count(A_ROUND_INIT) != 1:
            return "warn", "upstream changed agent_loop.py (round-init anchor) -- manual review"
        s = s.replace(A_ROUND_INIT, A_ROUND_INIT + NEW_ROUND_INIT, 1)
        changed = True
    elif "_stall_flagged" not in s:
        s = s.replace(
            "        _stall_last_visible = time.time()\n",
            "        _stall_last_visible = time.time()\n"
            "        _stall_flagged = False  # set by the in-stream detector when the cap fires\n",
            1,
        )
        changed = True

    # (c) in-stream detector (after the stock round deadline)
    if "_stall_flagged = True" not in s:
        if s.count(A_DEADLINE) != 1:
            return "warn", "upstream changed agent_loop.py (deadline anchor) -- manual review"
        s = s.replace(A_DEADLINE, A_DEADLINE + DETECTOR, 1)
        changed = True

    # (d) visible-progress reset of the stall clock
    if "resets the thinking-stall clock." not in s:
        if s.count(A_DELTA) != 1:
            return "warn", "upstream changed agent_loop.py (delta anchor) -- manual review"
        s = s.replace(A_DELTA, A_DELTA + RESET, 1)
        changed = True

    # (e) post-stream stall retry (before the no-tools path)
    if "thinking-stall retry" not in s:
        if s.count(A_NO_TOOLS) != 1:
            return "warn", "upstream changed agent_loop.py (no-tools anchor) -- manual review"
        s = s.replace(A_NO_TOOLS, RETRY + A_NO_TOOLS, 1)
        changed = True

    # (f) skip intent supervisor on force-answer rounds
    if "and not _force_answer  # local patch (section 20)" not in s:
        if s.count(A_PROMISE) != 1:
            return "warn", "upstream changed agent_loop.py (promise anchor) -- manual review"
        s = s.replace(A_PROMISE, NEW_PROMISE, 1)
        changed = True

    # (g) name the placeholder degeneration in the loop-breaker nudge.
    # The stock message spans a VARIABLE number of lines across upstream
    # versions (2/3/5 lines, different phrase wrap), so replace the whole
    # block by its stable opening line, consuming through the stable
    # closing line "                ),". This never leaves orphaned
    # string lines behind (an exact multi-line match did).
    LB_OPEN = "                    \"You're repeating tool calls without converging. "
    LB_CLOSE = "                ),"
    if "emitting the same placeholder tool call" not in s:
        _lo = [i for i, ln in enumerate(s.split(chr(10))) if ln.startswith(LB_OPEN)]
        if len(_lo) != 1:
            return "warn", "upstream changed agent_loop.py (loop-breaker nudge) -- manual review"
        _ls = s.split(chr(10))
        _li = _lo[0]
        _lc = None
        for _k in range(_li, min(_li + 30, len(_ls))):
            if _ls[_k].strip() == "),":
                _lc = _k
                break
        if _lc is None or _lc - _li > 25:
            return "warn", "loop-breaker nudge block looks malformed -- manual review"
        _ls[_li:_lc + 1] = NEW_LB.rstrip(chr(10)).split(chr(10))
        s = chr(10).join(_ls)
        changed = True

    if not changed:
        return "skip", "already patched"
    _write(f, s, nl)

    # (h) docker-compose.yml passthrough
    cp = compose_path(root)
    if cp.is_file():
        cs, cnl = _lines_of(cp)
        if "ODYSSEUS_AGENT_THINKING_STALL_S" not in cs and "ODYSSEUS_PREFILL_KEEPALIVE_S" in cs:
            cs = cs.replace(
                "ODYSSEUS_PREFILL_KEEPALIVE_S=${ODYSSEUS_PREFILL_KEEPALIVE_S:-30}",
                "ODYSSEUS_PREFILL_KEEPALIVE_S=${ODYSSEUS_PREFILL_KEEPALIVE_S:-30}\n"
                "      - ODYSSEUS_AGENT_THINKING_STALL_S=${ODYSSEUS_AGENT_THINKING_STALL_S:-600}",
                1,
            )
            _write(cp, cs, cnl)
    return "patched", "thinking-stall guard + force-answer supervisor fix"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-build", action="store_true", help="skip the docker compose rebuild (section 21)")
    ap.add_argument("--force-build", action="store_true", help="rebuild even if no source file changed")
    ap.add_argument("--no-docker", action="store_true", help="never call docker; print manual steps instead")
    args = ap.parse_args()

    root = find_root()
    print(f"patching odysseus checkout: {root}\n")

    statuses = {}

    st, detail = patch_constants(root / "src" / "constants.py")
    statuses[1] = st
    print(f"[1] constants.py       env-overridable MAX_* caps ......... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_model_context(root / "src" / "model_context.py")
    statuses[2] = st
    print(f"[2] model_context.py   llama-swap contextLength ......... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_compose(compose_path(root))
    statuses[3] = st
    print(f"[3] docker-compose.yml ODYSSEUS_MAX_* passthrough ....... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_compose_build_args(compose_path(root))
    statuses[4] = st
    print(f"[4] compose build args INSTALL_OPTIONAL (optional deps) {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_searxng_template(root / "config" / "searxng" / "settings.yml")
    statuses[5] = st
    print(f"[5] searxng template   engine whitelist (keep_only) ..... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_db(root)
    statuses[6] = st
    print(f"[6] data/app.db        local endpoints supports_tools ... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = remove_live_searxng(root, args.no_docker)
    statuses[7] = st
    print(f"[7] searxng volume     re-render live settings at boot .. {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_style_css(root / "static" / "style.css")
    statuses[8] = st
    print(f"[8] static/style.css   markdown img max-width in chat ... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_llm_core(root / "src" / "llm_core.py")
    statuses[9] = st
    print(f"[9] llm_core.py        repetition guard (window 200, env thresholds) .. {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_tool_index(root / "src" / "tool_index.py")
    statuses[10] = st
    print(f"[10] tool_index.py     always-on tools (ODYSSEUS_ALWAYS_TOOLS) . {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_global_rules(root)
    statuses[11] = st
    print(f"[11] global rules      every prompt (settings.json) ...... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_vision_tool(root)
    statuses[12] = st
    print(f"[12] vision tool       image analysis agent tool ......... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_timeouts(root)
    statuses[13] = st
    print(f"[13] timeout budgets   slow local model (env-overridable) . {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))
    st, detail = patch_vision_timeout(root)
    statuses[14] = st
    print(f"[14] vision timeout    VL call budget (env-overridable) . {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_resume_freeze(root)
    statuses[15] = st
    print(f"[15] resume freeze     reload of running chat (buffer+render+WAL) . {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_prefill_keepalive(root)
    statuses[16] = st
    print(f"[16] prefill keepalive long-context first-token (no 6-min kill) . {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_context_overflow(root)
    statuses[17] = st
    print(f"[17] ctx-overflow      real-context learning + retry ....... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_kv_exhaustion(root)
    statuses[19] = st
    print(f"[19] kv-exhaustion     silent KV-full mid-generation guard . {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    st, detail = patch_degeneration_guard(root)
    statuses[20] = st
    print(f"[20] degeneration      thinking-stall guard + force-answer fix . {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    src_changed = any(statuses.get(i) == "patched" for i in (1, 2, 4, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19, 20))
    if args.no_build:
        st, detail = "skip", "--no-build"
    elif src_changed or args.force_build:
        st, detail = rebuild(root, args.no_docker)
    else:
        st, detail = "skip", "no source changes (use --force-build to rebuild anyway)"
    statuses[21] = st
    print(f"[21] rebuild image     bake 1+2+4+8+9+10+11+12+13+14+15+16+17+19+20 in . {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

    print()
    if any(st == "warn" for st in statuses.values()):
        print("One or more sections need MANUAL REVIEW (see warnings above). Fix them, then re-run.")
        print("Next (once clean):  docker compose up -d")
        sys.exit(1)
    if any(st == "manual" for st in statuses.values()):
        print("Manual steps remaining (docker CLI unavailable or --no-docker):")
        print("  * remove the live searXNG settings so it re-renders from the patched template:")
        print("      docker volume ls | Select-String searxng        # note the volume name")
        print("      docker run --rm --entrypoint sh -v <vol>:/etc/searxng <searxng image> -c 'rm -f /etc/searxng/settings.yml'")
        print("  * rebuild the app image:  docker compose build odysseus --build-arg INSTALL_OPTIONAL=true")
        print("Next:  docker compose up -d")
    else:
        print("All patches in place. Next:  docker compose up -d")
        print("Tip: raise the caps in .env any time, e.g. ODYSSEUS_MAX_OUTPUT_CHARS=30000, then `docker compose up -d` again (no rebuild needed).")
        print("Tip: pick which tools the model ALWAYS sees via ODYSSEUS_ALWAYS_TOOLS in .env (comma-separated, default bash), e.g. ODYSSEUS_ALWAYS_TOOLS=bash,python,vision -- also just an `up -d`.")
        print("Tip: repetition guard is tunable in .env -- ODYSSEUS_REPEAT_SAME_RUN (150), ODYSSEUS_REPEAT_DOM_COUNT (150), ODYSSEUS_REPEAT_DOM_RATIO (0.92), ODYSSEUS_REPEAT_GRAM_COUNT (100). Raise them if long tables still get cut; `docker compose up -d` after editing.")
        print("Tip: global rules live in data/settings.json under `global_rules` (list of strings) -- every line is sent in EVERY prompt (chat and agent mode). Edit the file; applies from the next message, no restart, no rebuild.")
        print("Tip: slow local model? LLM call budgets are env-overridable -- e.g. ODYSSEUS_TIMEOUT_COMPACT=300, ODYSSEUS_TIMEOUT_RESEARCH=600 in .env, then `docker compose up -d`. research_max_time_seconds in data/settings.json caps a run (600 now).")
        print("Tip: vision calls time out after 300s by default (ODYSSEUS_TIMEOUT_VISION) -- raise it in .env if your VL model is slow, then `docker compose up -d`.")
        print("Tip: if a chat dies with an exceeds-the-available-context error, section 17 learns the backend real limit and trims + retries automatically; check the llama-swap config if a model keeps serving under its claimed window.")
        print("Tip: a run that ends mid-thinking with an empty final round usually means the KV cache filled (input + thinking >= context). Section 19 now auto-trims and continues; the ratio it learns also keeps trim/compact honest.")


if __name__ == "__main__":
    main()

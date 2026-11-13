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
                                 mount), so section 8 rebuilds the image and the
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
                                 next rebuild (which section 8 does anyway).
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
  8. docker compose build        rebuild the odysseus image so 1+2+4 are baked in
                                 (passes --build-arg INSTALL_OPTIONAL=true).
"""
import argparse
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
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


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
]


def patch_compose(path):
    """Forward the three ODYSSEUS_MAX_* vars into the app container.
    The ${VAR:-default} fallbacks match the code defaults, so leaving them
    out of .env is a safe no-op."""
    if not path.is_file():
        return "warn", f"{path} not found"
    s = read_raw(path).decode("utf-8")
    if "ODYSSEUS_MAX_OUTPUT_CHARS" in s:
        return "skip", "already present"
    for nl in ("\n", "\r\n"):
        anchor = COMPOSE_ANCHOR + nl
        if anchor in s:
            insertion = nl.join(COMPOSE_NEWLINES) + nl
            write_raw(path, s.replace(anchor, anchor + insertion, 1).encode("utf-8"))
            return "patched", ""
    return "warn", "anchor line (ODYSSEUS_TTS_CACHE_MAX_BYTES env entry) not found -- upstream likely changed compose; manual review needed"


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
    and section 8 rebuilds after every pull, so that path is not durable.
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
        for m in re.finditer(r"image:\s*['\"]?([^\s'\"]+)[ '\"]?", comp.read_text()):
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


# ------------------------------------------- 8. rebuild image --

def rebuild(root, no_docker):
    if no_docker:
        return "manual", "--no-docker given"
    if not shutil.which("docker"):
        return "manual", "docker CLI not found on this host"
    r = sh(["docker", "compose", "build", "odysseus", "--build-arg", "INSTALL_OPTIONAL=true"], cwd=str(root))
    if r.returncode == 0:
        return "patched", "odysseus image rebuilt with INSTALL_OPTIONAL=true"
    tail = (r.stdout or "")[-300:] + (r.stderr or "")[-300:]
    return "warn", f"build failed:\n{tail}"


# ------------------------------------------------------------- main --

STATUS_LABEL = {"patched": "PATCHED", "skip": "ok (already)", "none": "ok (n/a)", "manual": "MANUAL", "warn": "WARNING"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-build", action="store_true", help="skip the docker compose rebuild (section 8)")
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

    src_changed = any(statuses.get(i) == "patched" for i in (1, 2, 4))
    if args.no_build:
        st, detail = "skip", "--no-build"
    elif src_changed or args.force_build:
        st, detail = rebuild(root, args.no_docker)
    else:
        st, detail = "skip", "no source changes (use --force-build to rebuild anyway)"
    statuses[8] = st
    print(f"[8] rebuild image    bake 1+2+4 (optional deps) in ...... {STATUS_LABEL[st]}" + (f" -- {detail}" if detail else ""))

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


if __name__ == "__main__":
    main()

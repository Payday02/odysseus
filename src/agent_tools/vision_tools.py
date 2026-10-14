"""Vision tool: agent-invoked image analysis with the configured VL model.

Local patch -- re-applied by data/patches/patch_odysseus.py (section 12).
"""
import asyncio
import json
import os

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


class VisionTool:
    async def execute(self, content: str, ctx: dict) -> dict:
        raw = str(content or "").strip()
        if raw.startswith("{"):
            try:
                a = json.loads(raw)
            except json.JSONDecodeError:
                a = {}
            if isinstance(a, dict):
                path = str(a.get("path", "")).strip()
                prompt = str(a.get("prompt", "")).strip()
                # Local defaults: no thinking, generous cap (user preference 2026-09-23).
                _nt = a.get("no_thinking")
                if isinstance(_nt, str):
                    _nt = _nt.strip().lower() in ("1", "true", "yes", "on")
                no_thinking = bool(_nt) if _nt is not None else True
                max_tokens = a.get("max_tokens")
                if isinstance(max_tokens, str):
                    max_tokens = int(max_tokens) if max_tokens.strip().isdigit() else None
                elif isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
                    max_tokens = None
                if max_tokens is None:
                    max_tokens = 5000
        else:
            lines = raw.split("\n", 1)
            path = lines[0].strip()
            prompt = lines[1].strip() if len(lines) > 1 else ""
            no_thinking = True  # Local default
            max_tokens = 5000  # Local default
        if not path:
            return {"error": "vision: path is required", "exit_code": 1}
        try:
            from src.tool_execution import _resolve_tool_path
            path = _resolve_tool_path(path)
        except ValueError as e:
            return {"error": "vision: %s" % e, "exit_code": 1}
        if os.path.splitext(path)[1].lower() not in IMAGE_EXTS:
            return {"error": "vision: not an image (supported: %s)" % ", ".join(sorted(IMAGE_EXTS)), "exit_code": 1}
        if not os.path.isfile(path):
            return {"error": "vision: %s not found" % path, "exit_code": 1}
        try:
            from src.document_processor import analyze_image_with_vl_result
            res = await asyncio.to_thread(analyze_image_with_vl_result, path, ctx.get("owner"), prompt, no_thinking, max_tokens)
        except Exception as e:
            return {"error": "vision: %s: %s" % (type(e).__name__, e), "exit_code": 1}
        text = str(res.get("text") or "").strip()
        if not text:
            return {"error": "vision: no description returned for %s" % path, "exit_code": 1}
        if text.startswith("["):  # the pipeline signals config problems in brackets
            return {"error": "vision: %s" % text.strip("[]"), "exit_code": 1}
        model = res.get("model") or ""
        return {"output": ("[via %s]\n" % model if model else "") + text, "exit_code": 0}


__all__ = ["VisionTool", "IMAGE_EXTS"]

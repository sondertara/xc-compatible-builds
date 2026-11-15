#!/usr/bin/env python3
"""Verify DBX frontend compatibility with Kylin V10 WebKitGTK 2.38.6 (JSC 2.38).

Checks:
1. JS parse-time blockers in WebKitGTK 2.38 (outside comments/strings/templates):
   - RegExp literal lookbehind assertions: (?<=...) or (?<!...)
   - ES2022 class static initialization blocks: `static { ... }`
   Strings/template-literal contents (such as Shiki TextMate grammars or
   try { new RegExp("(?<=...)") } catch {} probes) are skipped lexically.
2. CSS modern color functions:
   - Reports @supports-condition, @supports-guarded, and unguarded color-mix()
     and oklch() declarations.
   - Emits [WARN] with exit 0 by default (Route A fallback), or exits 1 when
     --strict-css is passed.

Usage:
  python3 apps/dbx/scripts/verify-web-compat.py --self-test
  python3 apps/dbx/scripts/verify-web-compat.py --bin ./DBX/libexec/dbx-web-bin
  python3 apps/dbx/scripts/verify-web-compat.py --url http://127.0.0.1:4224
  python3 apps/dbx/scripts/verify-web-compat.py --dir ./dist
"""

from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ASSET_REF_RE = re.compile(
    r"""["']((?:assets/|/assets/|\./assets/|\./)[A-Za-z0-9_./-]{1,160}\.(?:js|mjs|css))["']"""
)
HTML_ATTR_RE = re.compile(
    r"""(?:src|href)\s*=\s*["']([A-Za-z0-9_./?-]{1,200}\.(?:js|mjs|css)(?:\?[^"']{0,64})?)["']""",
    re.IGNORECASE,
)
MEDIA_RANGE_OP_RE = re.compile(r"<=|>=|[<>]")
CSS_COLOR_MIX_FN_RE = re.compile(r"(?<![a-z0-9_-])color-mix\(")
CSS_OKLCH_FN_RE = re.compile(r"(?<![a-z0-9_-])oklch\(")

REGEX_AFTER_KEYWORDS = frozenset(
    {
        "return",
        "throw",
        "case",
        "delete",
        "void",
        "typeof",
        "yield",
        "await",
        "in",
        "instanceof",
        "of",
        "new",
        "else",
        "do",
    }
)
CONTROL_PAREN_KEYWORDS = frozenset({"if", "while", "for", "with"})


@dataclass
class JSBlocker:
    file: str
    line: int
    kind: str
    snippet: str


@dataclass
class CSSStats:
    file: str
    supports_condition_color_mix: int = 0
    supports_condition_oklch: int = 0
    guarded_color_mix: int = 0
    guarded_oklch: int = 0
    unguarded_color_mix: int = 0
    unguarded_oklch: int = 0
    unguarded_media_range: int = 0

    @property
    def unguarded_total(self) -> int:
        return self.unguarded_color_mix + self.unguarded_oklch + self.unguarded_media_range

    @property
    def guarded_total(self) -> int:
        return self.guarded_color_mix + self.guarded_oklch


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch in ("_", "$")


def _is_ident_part(ch: str) -> bool:
    return ch.isalnum() or ch in ("_", "$")


def _find_unescaped_lookbehind(pattern: str) -> str | None:
    """Return '(?<=' or '(?<!' if an unescaped lookbehind appears outside [...]."""
    in_class = False
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "\\":
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
            i += 1
            continue
        if ch == "[":
            in_class = True
            i += 1
            continue
        if pattern.startswith("(?<=", i):
            return "(?<="
        if pattern.startswith("(?<!", i):
            return "(?<!"
        i += 1
    return None


def _scan_regex_literal(text: str, slash_idx: int) -> tuple[int, str] | None:
    """Try to scan a /pattern/flags literal starting at slash_idx.

    Returns (end_idx_after_flags, pattern) if syntactically valid on one line,
    otherwise None so caller can treat '/' as division.
    """
    n = len(text)
    j = slash_idx + 1
    in_class = False
    while j < n:
        ch = text[j]
        if ch in ("\n", "\r"):
            return None
        if ch == "\\":
            if j + 1 >= n or text[j + 1] in ("\n", "\r"):
                return None
            j += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
            j += 1
            continue
        if ch == "[":
            in_class = True
            j += 1
            continue
        if ch == "/":
            pattern = text[slash_idx + 1 : j]
            j += 1
            while j < n and (text[j].isalpha() or text[j] in ("_", "$")):
                j += 1
            return j, pattern
        j += 1
    return None


def _line_snippet(text: str, pos: int, max_len: int = 80) -> str:
    start = max(0, pos - 24)
    end = min(len(text), pos + max_len)
    return text[start:end].replace("\n", " ").replace("\r", "").strip()


def scan_js(text: str, filename: str = "<memory>") -> list[JSBlocker]:
    """Scan JavaScript source for JSC 2.38 parse-time syntax blockers."""
    blockers: list[JSBlocker] = []
    n = len(text)
    i = 0
    line = 1

    # Lexical/parser context stacks:
    # - mode_stack tracks whether we're inside a template literal string or JS code.
    #   Entries are either ("code",) or ("template",).
    # - brace_stack distinguishes regular '{' vs template interpolation '${'.
    # - paren_stack distinguishes control header parens ('cond') vs ('expr').
    in_template_text = False
    brace_stack: list[str] = []
    paren_stack: list[str] = []

    can_start_regex = True
    prev_was_dot = False
    pending_static = False
    pending_static_line = 1
    pending_static_pos = 0
    saw_control_for_paren: str | None = None

    while i < n:
        ch = text[i]

        if in_template_text:
            if ch == "\n":
                line += 1
                i += 1
                continue
            if ch == "\\":
                if i + 1 < n and text[i + 1] == "\n":
                    line += 1
                elif i + 2 < n and text[i + 1] == "\r" and text[i + 2] == "\n":
                    line += 1
                    i += 1
                i += 2
                continue
            if ch == "`":
                in_template_text = False
                can_start_regex = False
                prev_was_dot = False
                pending_static = False
                saw_control_for_paren = None
                i += 1
                continue
            if ch == "$" and i + 1 < n and text[i + 1] == "{":
                brace_stack.append("template_expr")
                in_template_text = False
                can_start_regex = True
                prev_was_dot = False
                pending_static = False
                saw_control_for_paren = None
                i += 2
                continue
            i += 1
            continue

        # Whitespace preserves token state.
        if ch in (" ", "\t", "\r", "\f", "\v"):
            i += 1
            continue
        if ch == "\n":
            line += 1
            i += 1
            continue

        # Line comment preserves token state.
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
            continue

        # Block comment preserves token state.
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                line += text.count("\n", i)
                break
            line += text.count("\n", i, end + 2)
            i = end + 2
            continue

        # Quoted strings ('...' / "...").
        if ch in ("'", '"'):
            quote = ch
            pending_static = False
            saw_control_for_paren = None
            prev_was_dot = False
            i += 1
            while i < n:
                sc = text[i]
                if sc == "\n":
                    line += 1
                    i += 1
                    break
                if sc == "\\":
                    if i + 1 < n and text[i + 1] == "\n":
                        line += 1
                    elif i + 2 < n and text[i + 1] == "\r" and text[i + 2] == "\n":
                        line += 1
                        i += 1
                    i += 2
                    continue
                if sc == quote:
                    i += 1
                    break
                i += 1
            can_start_regex = False
            continue

        # Template literal start (`...`).
        if ch == "`":
            pending_static = False
            saw_control_for_paren = None
            prev_was_dot = False
            in_template_text = True
            i += 1
            continue

        # Slash: either regex literal or division.
        if ch == "/":
            pending_static = False
            saw_control_for_paren = None
            prev_was_dot = False
            if can_start_regex:
                res = _scan_regex_literal(text, i)
                if res is not None:
                    end_idx, pattern = res
                    lb = _find_unescaped_lookbehind(pattern)
                    if lb is not None:
                        blockers.append(
                            JSBlocker(
                                file=filename,
                                line=line,
                                kind=f"regex_lookbehind({lb})",
                                snippet=_line_snippet(text, i),
                            )
                        )
                    can_start_regex = False
                    i = end_idx
                    continue
            # Division '/' or '/='.
            if i + 1 < n and text[i + 1] == "=":
                i += 2
            else:
                i += 1
            can_start_regex = True
            continue

        # Identifiers & keywords.
        if _is_ident_start(ch):
            start = i
            i += 1
            while i < n and _is_ident_part(text[i]):
                i += 1
            ident = text[start:i]

            if prev_was_dot:
                prev_was_dot = False
                pending_static = False
                saw_control_for_paren = None
                can_start_regex = False
                continue

            if ident == "static":
                pending_static = True
                pending_static_line = line
                pending_static_pos = start
                saw_control_for_paren = None
                can_start_regex = False
                continue

            pending_static = False
            if ident in CONTROL_PAREN_KEYWORDS:
                saw_control_for_paren = ident
                can_start_regex = True
            elif ident == "await" and saw_control_for_paren == "for":
                # Preserve 'for' across `for await (...)` headers.
                can_start_regex = True
            elif ident in REGEX_AFTER_KEYWORDS:
                saw_control_for_paren = None
                can_start_regex = True
            else:
                saw_control_for_paren = None
                can_start_regex = False
            continue

        # Numbers (consume full numeric literal including scientific notation / separators).
        if ch.isdigit() or (ch == "." and i + 1 < n and text[i + 1].isdigit()):
            pending_static = False
            prev_was_dot = False
            saw_control_for_paren = None
            can_start_regex = False
            if ch == "0" and i + 1 < n and text[i + 1] in ("x", "X", "b", "B", "o", "O"):
                i += 2
                while i < n and (text[i].isalnum() or text[i] == "_"):
                    i += 1
            else:
                while i < n and (text[i].isdigit() or text[i] in ("_", ".")):
                    i += 1
                if i < n and text[i] in ("e", "E"):
                    i += 1
                    if i < n and text[i] in ("+", "-"):
                        i += 1
                    while i < n and (text[i].isdigit() or text[i] == "_"):
                        i += 1
                if i < n and text[i] == "n":
                    i += 1
            continue

        # Class static initialization block check right at '{'.
        if ch == "{":
            if pending_static:
                blockers.append(
                    JSBlocker(
                        file=filename,
                        line=pending_static_line,
                        kind="class_static_block",
                        snippet=_line_snippet(text, pending_static_pos),
                    )
                )
            pending_static = False
            prev_was_dot = False
            saw_control_for_paren = None
            brace_stack.append("brace")
            can_start_regex = True
            i += 1
            continue

        if ch == "}":
            pending_static = False
            prev_was_dot = False
            saw_control_for_paren = None
            if brace_stack:
                top = brace_stack.pop()
                if top == "template_expr":
                    in_template_text = True
                    can_start_regex = False
                    i += 1
                    continue
            can_start_regex = True
            i += 1
            continue

        if ch == "(":
            pending_static = False
            prev_was_dot = False
            paren_stack.append("cond" if saw_control_for_paren else "expr")
            saw_control_for_paren = None
            can_start_regex = True
            i += 1
            continue

        if ch == ")":
            pending_static = False
            prev_was_dot = False
            saw_control_for_paren = None
            top_p = paren_stack.pop() if paren_stack else "expr"
            can_start_regex = top_p == "cond"
            i += 1
            continue

        if ch == ".":
            pending_static = False
            saw_control_for_paren = None
            if text.startswith("...", i):
                prev_was_dot = False
                can_start_regex = True
                i += 3
            else:
                prev_was_dot = True
                can_start_regex = False
                i += 1
            continue

        pending_static = False
        prev_was_dot = False
        saw_control_for_paren = None

        if ch in ("[", ",", ";", ":", "?", "~", "!"):
            can_start_regex = True
            i += 1
            continue

        if ch == "]" or ch == "#":
            can_start_regex = False
            i += 1
            continue

        if ch in ("+", "-") and i + 1 < n and text[i + 1] == ch:
            # Postfix x++/x-- keeps can_start_regex False; prefix ++x/--x yields True.
            i += 2
            continue

        if ch in ("=", "&", "|", "^", "<", ">", "*", "%", "+", "-"):
            can_start_regex = True
            i += 1
            continue

        i += 1

    return blockers


def _extract_clean_at_prelude(text: str, start: int) -> str:
    """Return CSS prelude from start up to `{`/`;`/`}` with comments/strings stripped."""
    n = len(text)
    i = start
    parts: list[str] = []
    while i < n:
        ch = text[i]
        if ch in ("{", ";", "}"):
            break
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            parts.append(" ")
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                sc = text[i]
                if sc == "\\":
                    i += 2
                    continue
                if sc == quote:
                    i += 1
                    break
                i += 1
            parts.append(" ")
            continue
        parts.append(ch)
        i += 1
    return "".join(parts)


def scan_css(text: str, filename: str = "<memory>") -> CSSStats:
    """Scan CSS using a brace stack to classify color-mix(), oklch(), and @media range syntax."""
    stats = CSSStats(file=filename)
    n = len(text)
    i = 0

    brace_stack: list[tuple[bool, bool]] = []
    cm_guard_depth = 0
    ok_guard_depth = 0
    in_supports_prelude = False
    pending_supports_guard: tuple[bool, bool] = (False, False)
    prelude_start = True

    while i < n:
        ch = text[i]

        if ch.isspace():
            i += 1
            continue

        # Block comment.
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue

        # Quoted strings.
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                sc = text[i]
                if sc == "\\":
                    i += 2
                    continue
                if sc == quote:
                    i += 1
                    break
                i += 1
            prelude_start = False
            continue

        if ch == "{":
            brace_stack.append(pending_supports_guard)
            if pending_supports_guard[0]:
                cm_guard_depth += 1
            if pending_supports_guard[1]:
                ok_guard_depth += 1
            pending_supports_guard = (False, False)
            in_supports_prelude = False
            prelude_start = True
            i += 1
            continue

        if ch == "}":
            if brace_stack:
                guards_cm, guards_ok = brace_stack.pop()
                if guards_cm and cm_guard_depth > 0:
                    cm_guard_depth -= 1
                if guards_ok and ok_guard_depth > 0:
                    ok_guard_depth -= 1
            pending_supports_guard = (False, False)
            in_supports_prelude = False
            prelude_start = True
            i += 1
            continue

        if ch == ";":
            pending_supports_guard = (False, False)
            in_supports_prelude = False
            prelude_start = True
            i += 1
            continue

        if ch == "@" and prelude_start:
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] in ("-", "_")):
                j += 1
            at_name = text[i + 1 : j].lower()
            prelude_start = False
            if at_name == "supports":
                clean_prelude = _extract_clean_at_prelude(text, j).strip().lower()
                is_not = clean_prelude == "not" or (
                    clean_prelude.startswith("not")
                    and (len(clean_prelude) == 3 or not (clean_prelude[3].isalnum() or clean_prelude[3] in ("-", "_")))
                )
                in_supports_prelude = True
                if is_not:
                    pending_supports_guard = (False, False)
                else:
                    has_cm = bool(CSS_COLOR_MIX_FN_RE.search(clean_prelude))
                    has_ok = bool(CSS_OKLCH_FN_RE.search(clean_prelude))
                    if has_cm:
                        # Safari 16.2+ supports both color-mix() and oklch(); WebKitGTK 2.38.6 skips the block.
                        pending_supports_guard = (True, True)
                    elif has_ok:
                        # WebKitGTK 2.38.6 supports oklch(), so color-mix() inside stays unguarded.
                        pending_supports_guard = (False, True)
                    else:
                        pending_supports_guard = (False, False)
            elif at_name == "media":
                clean_prelude = _extract_clean_at_prelude(text, j)
                stats.unguarded_media_range += len(MEDIA_RANGE_OP_RE.findall(clean_prelude))
            i = j
            continue

        # Match function calls color-mix( or oklch( case-insensitively on identifier boundary.
        if ch.isalpha() or ch in ("_", "-"):
            j = i
            while j < n and (text[j].isalnum() or text[j] in ("-", "_")):
                j += 1
            ident = text[i:j].lower()
            prelude_start = False
            if j < n and text[j] == "(":
                if ident == "color-mix":
                    if in_supports_prelude:
                        stats.supports_condition_color_mix += 1
                    elif cm_guard_depth > 0:
                        stats.guarded_color_mix += 1
                    else:
                        stats.unguarded_color_mix += 1
                elif ident == "oklch":
                    if in_supports_prelude:
                        stats.supports_condition_oklch += 1
                    elif ok_guard_depth > 0:
                        stats.guarded_oklch += 1
                    else:
                        stats.unguarded_oklch += 1
                i = j + 1
                continue
            i = j
            continue

        prelude_start = False
        i += 1

    return stats


def _resolve_asset_url(base_root: str, current_url: str, ref: str) -> str:
    ref = ref.strip()
    if ref.startswith(("/assets/", "assets/", "./assets/")):
        return urllib.parse.urljoin(base_root, ref.lstrip("./"))
    return urllib.parse.urljoin(current_url, ref)


def crawl_url(base_url: str, timeout: float = 10.0) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Crawl served HTML + reachable JS/CSS assets under base_url (proxy bypassed)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    parsed_root = urllib.parse.urlparse(base_url)
    root_origin = (parsed_root.scheme, parsed_root.netloc)
    root_url = f"{parsed_root.scheme}://{parsed_root.netloc}/"

    def fetch(target: str, allow_404: bool = False) -> tuple[int, str, str]:
        req = urllib.request.Request(target, headers={"User-Agent": "dbx-compat-check/1.0"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                raw = resp.read()
            return resp.status, ctype, raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if allow_404 and exc.code == 404:
                ctype = (exc.headers.get("Content-Type") or "").lower()
                exc.close()
                return 404, ctype, ""
            raise

    _, _, index_html = fetch(base_url)
    queue: deque[tuple[str, bool]] = deque()
    seen: set[str] = set()

    def enqueue(resolved: str, required: bool = False) -> None:
        p = urllib.parse.urlparse(resolved)
        if (p.scheme, p.netloc) != root_origin or len(p.path) > 256:
            return
        clean = urllib.parse.urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
        if clean not in seen:
            seen.add(clean)
            queue.append((clean, required))

    for m in HTML_ATTR_RE.finditer(index_html):
        enqueue(_resolve_asset_url(root_url, base_url, m.group(1)), required=True)
    for m in ASSET_REF_RE.finditer(index_html):
        enqueue(_resolve_asset_url(root_url, base_url, m.group(1)), required=True)

    js_files: dict[str, str] = {}
    css_files: dict[str, str] = {}
    spa_fallbacks: list[str] = []

    while queue:
        asset_url, required = queue.popleft()
        status, ctype, body = fetch(asset_url, allow_404=not required)
        path = urllib.parse.urlparse(asset_url).path.lstrip("/") or asset_url

        if status == 404 or "text/html" in ctype:
            if required:
                raise RuntimeError(f"required entrypoint asset {asset_url} returned SPA fallback / HTTP {status}")
            spa_fallbacks.append(path)
            continue

        if path.endswith(".css") or "text/css" in ctype:
            css_files[path] = body
            continue

        if path.endswith((".js", ".mjs")) or "javascript" in ctype or "ecmascript" in ctype:
            js_files[path] = body
            for m in ASSET_REF_RE.finditer(body):
                enqueue(_resolve_asset_url(root_url, asset_url, m.group(1)), required=False)

    return js_files, css_files, spa_fallbacks


def scan_dir(root_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Collect JS/MJS and CSS files recursively from root_dir."""
    if not root_dir.is_dir():
        raise FileNotFoundError(f"directory not found: {root_dir}")
    js_files: dict[str, str] = {}
    css_files: dict[str, str] = {}
    for path in sorted(root_dir.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root_dir))
        if path.suffix in (".js", ".mjs"):
            js_files[rel] = path.read_text(encoding="utf-8", errors="replace")
        elif path.suffix == ".css":
            css_files[rel] = path.read_text(encoding="utf-8", errors="replace")
    return js_files, css_files


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(url: str, proc: subprocess.Popen[bytes], timeout: float = 20.0) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    last_err = ""
    while time.monotonic() < deadline:
        rc = proc.poll()
        if rc is not None:
            raise RuntimeError(f"dbx-web-bin exited prematurely with status {rc}")
        try:
            with opener.open(url, timeout=1.5) as resp:
                if 200 <= resp.status < 500:
                    return
        except (urllib.error.URLError, OSError) as exc:
            last_err = str(exc)
        time.sleep(0.15)
    raise TimeoutError(f"timed out waiting for {url} ({last_err})")


def crawl_binary(bin_path: Path, static_dir: Path | None = None) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Start dbx-web-bin ephemerally on a free loopback port, crawl assets, and terminate it."""
    bin_path = bin_path.resolve()
    if not bin_path.is_file():
        raise FileNotFoundError(f"binary not found: {bin_path}")
    resolved_static_dir: Path | None = None
    if static_dir is not None:
        resolved_static_dir = static_dir.resolve()
        if not (resolved_static_dir / "index.html").is_file():
            raise FileNotFoundError(f"static index.html not found under {resolved_static_dir}")
    port = _free_loopback_port()
    with tempfile.TemporaryDirectory(prefix="dbx-compat-bin-") as tmpdir:
        data_dir = Path(tmpdir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        for key in ("DBX_PUBLIC_BASE_PATH", "DBX_STATIC_DIR", "DBX_WEB_COOKIE_SECURE"):
            env.pop(key, None)
        env.update(
            {
                "DBX_BIND_ADDR": "127.0.0.1",
                "DBX_PORT": str(port),
                "DBX_DATA_DIR": str(data_dir),
                "DBX_DISABLE_PASSWORD": "1",
                "DBX_WEB_CAPTCHA": "off",
            }
        )
        if resolved_static_dir is not None:
            env["DBX_STATIC_DIR"] = str(resolved_static_dir)
        proc = subprocess.Popen(
            [str(bin_path)],
            cwd=tmpdir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            _wait_ready(f"http://127.0.0.1:{port}/api/auth/check", proc, timeout=20.0)
            return crawl_url(f"http://127.0.0.1:{port}/")
        except Exception as exc:
            out = ""
            if proc.poll() is not None and proc.stdout is not None:
                out = proc.stdout.read().decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"{exc}\n{out}".strip()) from exc
        finally:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2.0)


def run_self_test() -> None:
    # 1. Division after parenthesized expressions must not be mistaken for regex.
    b1 = scan_js("const x = (a + b) / c + d / e; const y = /(?<=ok)/;")
    assert len(b1) == 1 and b1[0].line == 1 and b1[0].kind == "regex_lookbehind((?<=)" and "/(?<=ok)/" in b1[0].snippet

    # 2. Control-paren followed by lookbehind regex literal MUST be caught.
    assert len(scan_js("if (x) /(?<=a)b/.test(s);")) == 1
    assert len(scan_js("for await (const x of items) /(?<!a)b/.test(x);")) == 1

    # 3. Runtime-probe strings & Shiki template strings MUST be ignored.
    assert scan_js('try { new RegExp("(?<=a)b"); } catch (e) {}') == []
    assert scan_js("const grammar = `(?<=a) static { } (?<!b)`;") == []

    # 4. Template interpolation & nested template interpolation MUST be scanned.
    assert len(scan_js("const s = `prefix ${/(?<!x)y/.test(v)} suffix`;")) == 1
    assert len(scan_js("const s = `outer ${`inner ${v}`} /(?<=a)/ ${/(?<!b)/.test(v)}`;")) == 1

    # 5. Class static initialization block vs static method vs property access.
    assert len(scan_js("class A { static { this.x = 1; } }")) == 1
    assert len(scan_js("class A { static /* comment */ { this.x = 1; } }")) == 1
    assert scan_js("class B { static foo() {} } const c = { static: 1 }; obj.static;") == []

    # 6. CSS @supports guarded vs unguarded (oklch guard does not guard color-mix).
    css_ok = scan_css(
        "@supports (color: color-mix(in oklab, red, blue)) { "
        ".a { color: color-mix(in oklab, red, blue); border-color: oklch(0.6 0.2 250); } }"
    )
    assert css_ok.supports_condition_color_mix == 1
    assert css_ok.guarded_color_mix == 1
    assert css_ok.guarded_oklch == 1
    assert css_ok.unguarded_total == 0

    css_oklch_only_guard = scan_css(
        "@supports (color: oklch(0% 0 0)) { "
        ".a { border-color: oklch(0.6 0.2 250); color: color-mix(in srgb, red 50%, blue); } }"
    )
    assert css_oklch_only_guard.supports_condition_oklch == 1
    assert css_oklch_only_guard.guarded_oklch == 1
    assert css_oklch_only_guard.unguarded_color_mix == 1
    assert css_oklch_only_guard.unguarded_total == 1

    css_bad = scan_css(".b { color: color-mix(in srgb, red 50%, blue); background: oklch(0.7 0.1 120); }")
    assert css_bad.unguarded_color_mix == 1
    assert css_bad.unguarded_oklch == 1
    assert css_bad.unguarded_total == 2

    # 7. @media range syntax vs classic min-width/max-width syntax.
    css_mq_bad = scan_css("@media (width >= 40rem) and (height < 900px) { .a { display: flex; } }")
    assert css_mq_bad.unguarded_media_range >= 2
    assert css_mq_bad.unguarded_total >= 2

    css_mq_ok = scan_css("@media (min-width: 40rem) and (max-height: 899px) { .a { display: flex; } }")
    assert css_mq_ok.unguarded_media_range == 0
    assert css_mq_ok.unguarded_total == 0

    print("self-test passed (all assertions green)")


def report_and_exit(
    js_files: dict[str, str],
    css_files: dict[str, str],
    spa_fallbacks: list[str],
    strict_css: bool,
) -> int:
    if not js_files:
        print("[FAIL] 0 JavaScript assets were scanned.", file=sys.stderr)
        return 1

    js_blockers: list[JSBlocker] = []
    for name, content in js_files.items():
        js_blockers.extend(scan_js(content, name))

    css_reports: list[CSSStats] = []
    for name, content in css_files.items():
        css_reports.append(scan_css(content, name))

    sup_cond_cm = sum(r.supports_condition_color_mix for r in css_reports)
    sup_cond_ok = sum(r.supports_condition_oklch for r in css_reports)
    guarded_cm = sum(r.guarded_color_mix for r in css_reports)
    guarded_ok = sum(r.guarded_oklch for r in css_reports)
    unguarded_cm = sum(r.unguarded_color_mix for r in css_reports)
    unguarded_ok = sum(r.unguarded_oklch for r in css_reports)
    unguarded_mr = sum(r.unguarded_media_range for r in css_reports)

    print(
        f"Scanned {len(js_files)} JS files and {len(css_files)} CSS files"
        + (f" (skipped {len(spa_fallbacks)} HTML SPA fallbacks)" if spa_fallbacks else "")
    )
    print(f"JS parse-time blockers (JSC 2.38): {len(js_blockers)}")
    for b in js_blockers[:25]:
        print(f"  [FAIL] {b.file}:{b.line} {b.kind} :: {b.snippet}", file=sys.stderr)

    print(
        "CSS color-mix()/oklch()/media-range: "
        f"@supports-cond=({sup_cond_cm} color-mix, {sup_cond_ok} oklch), "
        f"guarded=({guarded_cm} color-mix, {guarded_ok} oklch), "
        f"unguarded=({unguarded_cm} color-mix, {unguarded_ok} oklch, {unguarded_mr} media-range)"
    )

    offenders = sorted((r for r in css_reports if r.unguarded_total > 0), key=lambda r: -r.unguarded_total)
    if offenders:
        level = "FAIL" if strict_css else "WARN"
        total_unguarded = unguarded_cm + unguarded_ok + unguarded_mr
        print(
            f"[{level}] {len(offenders)} CSS file(s) contain unguarded color-mix()/oklch()/media-range "
            f"(total {total_unguarded}; top files):"
        )
        for r in offenders[:12]:
            print(
                f"  - {r.file}: unguarded color-mix={r.unguarded_color_mix}, "
                f"oklch={r.unguarded_oklch}, media-range={r.unguarded_media_range} "
                f"(guarded={r.guarded_total})"
            )

    if js_blockers:
        return 1
    if strict_css and (unguarded_cm + unguarded_ok + unguarded_mr) > 0:
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bin", type=Path, help="Path to dbx-web-bin to launch temporarily and crawl")
    mode.add_argument("--url", help="URL of a running dbx-web instance (e.g. http://127.0.0.1:4224/)")
    mode.add_argument("--dir", type=Path, help="Extracted frontend dist directory containing JS/CSS")
    mode.add_argument("--self-test", action="store_true", help="Run built-in lexer self-tests")
    ap.add_argument(
        "--static-dir",
        type=Path,
        default=None,
        help="With --bin, pass DBX_STATIC_DIR so dbx-web-bin serves this directory",
    )
    ap.add_argument(
        "--strict-css",
        action="store_true",
        help="Fail (exit 1) if unguarded CSS color-mix(), oklch(), or @media range syntax is present",
    )
    args = ap.parse_args()

    if args.static_dir is not None and args.bin is None:
        ap.error("--static-dir requires --bin")

    if args.self_test:
        run_self_test()
        return 0

    spa_fallbacks: list[str] = []
    if args.bin is not None:
        js_files, css_files, spa_fallbacks = crawl_binary(args.bin, static_dir=args.static_dir)
    elif args.url is not None:
        js_files, css_files, spa_fallbacks = crawl_url(args.url)
    else:
        js_files, css_files = scan_dir(args.dir)

    return report_and_exit(js_files, css_files, spa_fallbacks, strict_css=args.strict_css)


if __name__ == "__main__":
    raise SystemExit(main())

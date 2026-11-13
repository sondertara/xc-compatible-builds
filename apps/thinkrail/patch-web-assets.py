#!/usr/bin/env python3
"""Remove JavaScript syntax that WebKitGTK 4.0 (JavaScriptCore 2.38) lacks.

ThinkRail's web bundle targets WebKitGTK 4.1 / Safari 17+.  On Kylin V10 the
system WebKitGTK is 4.0 / 2.38.6, which has no regex lookbehind and no class
static blocks.  A single such construct is a parse-time SyntaxError, so the
whole bundle fails and the window stays blank.

Usage: patch-web-assets.py /path/to/ThinkRail
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPLACEMENTS = 0


def replace(path: Path, old: str, new: str, label: str) -> None:
    global REPLACEMENTS
    text = path.read_text()
    if old not in text:
        raise SystemExit(f"{path.name}: expected {label} snippet not found")
    path.write_text(text.replace(old, new))
    REPLACEMENTS += 1


def patch_index(assets: Path) -> None:
    index = next(assets.glob("index-*.js"), None)
    if index is None:
        raise SystemExit("index-*.js not found")

    # Version parser: lookbehind -> leading boundary capture group.
    old = (
        "var dT=/(?<![\\d.])\\d{1,3}(?:\\.\\d{1,3})*(?![\\d.])(?![bB]\\b|x\\d)/;"
        "function fT(e){let t=e.replace(/\\([^)]*\\)/g,``).match(dT);"
        "return t?t[0].split(`.`).map(Number):null}"
    )
    new = (
        "var dT=/(?:^|[^\\d.])(\\d{1,3}(?:\\.\\d{1,3})*)(?![\\d.])(?![bB]\\b|x\\d)/;"
        "function fT(e){let t=e.replace(/\\([^)]*\\)/g,``).match(dT);"
        "return t?t[1].split(`.`).map(Number):null}"
    )
    replace(index, old, new, "version lookbehind")

    # Class static block -> static field.  The field must be followed by ';'
    # because the next token in the minified class body is '#e'.
    replace(
        index,
        "static{iQ(this,`OrderedDict`)}#e;",
        "static __wk40=iQ(this,`OrderedDict`);#e;",
        "OrderedDict static block",
    )

    text = index.read_text()
    if "(?<=" in text or "(?<!" in text or "static{" in text:
        raise SystemExit(f"{index.name}: unsupported syntax still present")


def patch_markdown(assets: Path) -> None:
    md = next(assets.glob("Markdown-*.js"), None)
    if md is None:
        raise SystemExit("Markdown-*.js not found")
    old = "/(?<=^|\\s|\\p{P}|\\p{S})([-.\\w+]+)@([-\\w]+(?:\\.[-\\w]+)+)/gu"
    new = "/(?:^|\\s|\\p{P}|\\p{S})([-.\\w+]+)@([-\\w]+(?:\\.[-\\w]+)+)/gu"
    replace(md, old, new, "email lookbehind")


def patch_plain_runs(assets: Path) -> None:
    plain = next(assets.glob("plainRuns-*.js"), None)
    if plain is None:
        raise SystemExit("plainRuns-*.js not found")
    replace(plain, "ce=/(?<=\\n)/", "ce=/\\n/", "line split lookbehind")


def patch_editor_colors(assets: Path) -> None:
    for pattern in ("MonacoEditor-*.js", "editor.worker-*.js"):
        for path in assets.glob(pattern):
            text = path.read_text()
            if "(?<=['\"\\s])" not in text:
                continue
            path.write_text(text.replace("(?<=['\"\\s])", "(?:['\"\\s])"))
            global REPLACEMENTS
            REPLACEMENTS += 1


def patch_static_blocks(assets: Path) -> None:
    """Convert simple class static blocks to static fields.

    Only handles `static{fn(this, `Name`)}`; anything more complex is left
    alone and will be reported by the verification step when loaded.
    """
    simple = re.compile(r"static\{([A-Za-z_$][\w$]*)\(this,(`[^`]*`)\)\}")
    field = re.compile(r"static (__wk40=[A-Za-z_$][\w$]*\(this,`[^`]*`\))(?!#|;|\})")
    for path in assets.glob("*.js"):
        text = path.read_text(errors="replace")
        text, n = simple.subn(r"static __wk40=\1(this,\2);", text)
        text, m = field.subn(r"static \1;", text)
        if n or m:
            path.write_text(text)
            global REPLACEMENTS
            REPLACEMENTS += n + m


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-web-assets.py /path/to/ThinkRail")
    root = Path(sys.argv[1])
    assets = root / "Resources/app/views/web/assets"
    if not assets.is_dir():
        raise SystemExit(f"assets directory not found: {assets}")

    patch_index(assets)
    patch_markdown(assets)
    patch_plain_runs(assets)
    patch_editor_colors(assets)
    patch_static_blocks(assets)
    print(f"patched {REPLACEMENTS} web-asset construct(s) under {assets}")


if __name__ == "__main__":
    main()

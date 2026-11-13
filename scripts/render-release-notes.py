#!/usr/bin/env python3
"""Render the shared compat-release notes template.

All app/workflow-specific prose lives in release/apps/<id>/*.md so release
bodies stay consistent across Tauri, Wails and Electrobun apps.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} GB"


def assets_table(directory: Path) -> str:
    files = sorted(p for p in directory.rglob("*") if p.is_file())
    if not files:
        return "_无产物_"
    rows = ["| 文件 | 大小 |", "|------|------|"]
    for p in files:
        rows.append(f"| `{p.name}` | {human_size(p.stat().st_size)} |")
    return "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--assets-dir", required=True, type=Path)
    ap.add_argument("--upstream-body", type=Path)
    ap.add_argument("--app-id", required=True)
    ap.add_argument("--display-name", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--upstream-url", required=True)
    ap.add_argument("--upstream-tag", required=True)
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--install-file", required=True, type=Path)
    ap.add_argument("--diff-file", required=True, type=Path)
    ap.add_argument("--check-file", required=True, type=Path)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--commit", default="")
    args = ap.parse_args()

    upstream = ""
    if args.upstream_body and args.upstream_body.is_file():
        upstream = read(args.upstream_body)
    if not upstream:
        upstream = "_上游未发布 Release Notes。_"

    mapping = {
        "APP_ID": args.app_id,
        "DISPLAY_NAME": args.display_name,
        "VERSION": args.version,
        "UPSTREAM_URL": args.upstream_url,
        "UPSTREAM_TAG": args.upstream_tag,
        "RUNTIME_DEPS": args.runtime,
        "UPSTREAM_BODY": upstream,
        "INSTALL_NOTES": read(args.install_file),
        "BUILD_DIFF": read(args.diff_file),
        "CHECK_NOTES": read(args.check_file),
        "ASSETS_TABLE": assets_table(args.assets_dir),
    }

    text = args.template.read_text(encoding="utf-8")
    # Two passes: app note files may themselves contain {{VERSION}}.
    for _ in range(2):
        for key, value in mapping.items():
            text = text.replace("{{" + key + "}}", value)

    for key in mapping:
        if "{{" + key + "}}" in text:
            raise SystemExit(f"unresolved placeholder: {key}")

    provenance = {
        "app": args.app_id,
        "version": args.version,
        "upstream_tag": args.upstream_tag,
        "run_id": args.run_id,
        "commit": args.commit,
        "schema": 1,
    }
    text = text.rstrip() + "\n\n<!-- compat-release " + json.dumps(
        provenance, ensure_ascii=False, separators=(",", ":")
    ) + " -->\n"

    args.out.write_text(text, encoding="utf-8")
    print(f"wrote {args.out} ({len(text)} bytes)")


if __name__ == "__main__":
    main()

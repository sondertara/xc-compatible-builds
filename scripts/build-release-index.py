#!/usr/bin/env python3
"""Build the per-application release index (README table + release/index.json).

GitHub Releases are a flat, chronological list: there is no native "group by
application" or per-application "latest" marker.  We use the tag convention
``<app-id>-v<semver>`` as the grouping key and compute the latest *stable*
version per application from semver, not from GitHub's single global "Latest"
flag.

Usage:
  GH_TOKEN=... python3 scripts/build-release-index.py --repo owner/repo
  python3 scripts/build-release-index.py --repo owner/repo --check
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parent.parent
START = "<!-- releases:start -->"
END = "<!-- releases:end -->"
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")

ARCH_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("x86_64", ("amd64", "x86_64", "x64")),
    ("aarch64", ("aarch64", "arm64")),
)
METADATA_SUFFIXES = (".sha256", ".sha256sum", ".txt", ".sig", ".asc", ".json")


def run(args: list[str], env: dict | None = None) -> str:
    return subprocess.check_output(args, text=True, env=env).strip()


def load_apps() -> list[dict]:
    registry = yaml.safe_load((ROOT / "configs/apps.yml").read_text(encoding="utf-8"))
    apps = []
    for entry in registry.get("apps", []):
        cfg = yaml.safe_load((ROOT / entry["config"]).read_text(encoding="utf-8"))
        apps.append(
            {
                "id": entry["id"],
                "display_name": cfg.get("display_name") or entry["id"],
                "upstream": (cfg.get("upstream_repo") or "").strip(),
            }
        )
    return apps


def fetch_releases(repo: str) -> list[dict]:
    out = run(
        [
            "gh",
            "release",
            "list",
            "--repo",
            repo,
            "--limit",
            "200",
            "--json",
            "tagName,name,publishedAt,isPrerelease,isDraft",
        ]
    )
    return json.loads(out)


def fetch_assets(repo: str, tag: str) -> dict:
    out = run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "assets,url,publishedAt,name",
        ]
    )
    return json.loads(out)


def semver(tag: str, app_id: str) -> tuple | None:
    prefix = f"{app_id}-"
    if not tag.startswith(prefix):
        return None
    m = SEMVER.match(tag[len(prefix) :])
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    # Stable releases sort ahead of a prerelease with the same numeric version.
    return (int(major), int(minor), int(patch), 0 if pre is None else 1, pre or "")


def version_of(tag: str) -> str:
    return tag.split("-", 1)[1].lstrip("v")


def _is_package_asset(name: str) -> bool:
    low = name.lower()
    if "sha256" in low or low.endswith(METADATA_SUFFIXES):
        return False
    return True


def _asset_priority(name: str) -> tuple[int, str]:
    low = name.lower()
    if "installer" in low:
        return (0, low)
    if low.endswith(".appimage"):
        return (1, low)
    if low.endswith((".deb", ".rpm")):
        return (2, low)
    if low.endswith((".tar.gz", ".tgz", ".tar.xz", ".tar.zst")):
        return (3, low)
    return (4, low)


def pick_arch_downloads(repo: str, tag: str, assets: list[dict]) -> list[tuple[str, str]]:
    """Return ordered `(arch_label, download_url)` pairs for primary deliverables."""
    picked: list[tuple[str, str]] = []
    for arch_label, tokens in ARCH_TOKENS:
        matches = [
            a
            for a in assets
            if _is_package_asset(a.get("name", ""))
            and any(tok in a["name"].lower() for tok in tokens)
        ]
        if not matches:
            continue
        matches.sort(key=lambda a: _asset_priority(a["name"]))
        best = matches[0]
        dl_url = best.get("url") or (
            f"https://github.com/{repo}/releases/download/{quote(tag)}/{quote(best['name'])}"
        )
        picked.append((arch_label, dl_url))
    return picked


def format_app_cell(app: dict) -> str:
    name = app["display_name"]
    upstream = app.get("upstream", "")
    if upstream:
        return f"[**{name}**](https://github.com/{upstream})"
    return f"**{name}**"


def render_table(repo: str, apps: list[dict], releases: list[dict]) -> str:
    lines = [
        START,
        "",
        "| 应用 | 最新版本 | 发布日期 | 下载 |",
        "| :--- | :--- | :--- | :--- |",
    ]
    history_parts: list[str] = []

    for app in apps:
        candidates = []
        for rel in releases:
            if rel.get("isDraft"):
                continue
            sv = semver(rel["tagName"], app["id"])
            if sv:
                candidates.append((sv, rel))
        candidates.sort(key=lambda x: x[0], reverse=True)

        app_cell = format_app_cell(app)
        if not candidates:
            lines.append(f"| {app_cell} | 未发布 | — | — |")
            continue

        _, rel = candidates[0]
        tag = rel["tagName"]
        meta = fetch_assets(repo, tag)
        date = (meta.get("publishedAt") or rel.get("publishedAt") or "")[:10]
        rel_url = meta.get("url") or f"https://github.com/{repo}/releases/tag/{tag}"

        dl_links = [
            f"[{arch_label}]({dl_url})"
            for arch_label, dl_url in pick_arch_downloads(repo, tag, meta.get("assets", []))
        ]
        dl_links.append(f"[Release]({rel_url})")
        lines.append(
            f"| {app_cell} | `{version_of(tag)}` | {date} | {' · '.join(dl_links)} |"
        )

        if len(candidates) > 1:
            older_links = [
                f"[`{version_of(old['tagName'])}`](https://github.com/{repo}/releases/tag/{old['tagName']})"
                for _, old in candidates[1:]
            ]
            history_parts.append(f"**{app['display_name']}** {' · '.join(older_links)}")

    if history_parts:
        lines.extend(["", "- **历史版本**：" + "；".join(history_parts)])

    lines.extend(["", END])
    return "\n".join(lines)


def render_json(repo: str, apps: list[dict], releases: list[dict]) -> dict:
    # Use the newest release timestamp instead of "now" so a daily refresh
    # does not create an empty commit when nothing changed.
    latest_at = max(
        (r.get("publishedAt") or "" for r in releases if not r.get("isDraft")),
        default="",
    )
    data = {
        "schema": 1,
        "repository": repo,
        "latest_release_at": latest_at or None,
        "apps": [],
    }
    for app in apps:
        versions = []
        for rel in releases:
            if rel.get("isDraft"):
                continue
            if semver(rel["tagName"], app["id"]):
                versions.append(
                    {
                        "version": version_of(rel["tagName"]),
                        "tag": rel["tagName"],
                        "published_at": rel.get("publishedAt"),
                        "release_url": f"https://github.com/{repo}/releases/tag/{rel['tagName']}",
                    }
                )
        versions.sort(
            key=lambda v: semver(v["tag"], app["id"]) or (0, 0, 0, None),
            reverse=True,
        )
        entry = {"id": app["id"], "display_name": app["display_name"], "versions": versions}
        if versions:
            meta = fetch_assets(repo, versions[0]["tag"])
            entry["latest"] = {
                **versions[0],
                "assets": [a["name"] for a in meta.get("assets", [])],
            }
        data["apps"].append(entry)
    return data


def replace_block(text: str, block: str) -> str:
    start = text.index(START)
    end = text.index(END) + len(END)
    return text[:start] + block + text[end:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/repo")
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--json-out", default="release/index.json")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    apps = load_apps()
    releases = fetch_releases(args.repo)
    table = render_table(args.repo, apps, releases)
    data = render_json(args.repo, apps, releases)

    readme_path = ROOT / args.readme
    old_readme = readme_path.read_text(encoding="utf-8")
    new_readme = replace_block(old_readme, table)
    json_path = ROOT / args.json_out
    new_json = json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    changed = []
    if old_readme != new_readme:
        changed.append(str(readme_path))
        if not args.check:
            readme_path.write_text(new_readme, encoding="utf-8")
    if not json_path.exists() or json_path.read_text(encoding="utf-8") != new_json:
        changed.append(str(json_path))
        if not args.check:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            json_path.write_text(new_json, encoding="utf-8")

    if args.check:
        if changed:
            print("release index is stale: " + ", ".join(changed), file=sys.stderr)
            return 1
        print("release index is up to date")
    else:
        print("updated: " + (", ".join(changed) if changed else "nothing"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

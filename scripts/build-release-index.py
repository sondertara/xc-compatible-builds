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

import yaml

ROOT = Path(__file__).resolve().parent.parent
START = "<!-- releases:start -->"
END = "<!-- releases:end -->"
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$")


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
                "upstream": cfg.get("upstream_repo", ""),
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


def archs(assets: list[dict]) -> str:
    found = set()
    for a in assets:
        n = a["name"].lower()
        if any(x in n for x in ("amd64", "x86_64", "x64")):
            found.add("x86_64")
        if any(x in n for x in ("aarch64", "arm64")):
            found.add("aarch64")
    order = ["x86_64", "aarch64"]
    return " / ".join(x for x in order if x in found) or "—"


def render_table(repo: str, apps: list[dict], releases: list[dict]) -> str:
    lines = [
        START,
        "> 本表由 `.github/workflows/release-index.yml` 自动生成，请勿手工编辑。",
        "",
        "| 应用 | 最新兼容版本 | 发布时间 | 架构 | 下载 | 历史 |",
        "|------|--------------|----------|------|------|------|",
    ]
    history_rows = []
    for app in apps:
        candidates = []
        for rel in releases:
            if rel.get("isDraft"):
                continue
            sv = semver(rel["tagName"], app["id"])
            if sv:
                candidates.append((sv, rel))
        candidates.sort(key=lambda x: x[0], reverse=True)
        if not candidates:
            lines.append(
                f"| {app['display_name']} | 未发布 | — | — | — | — |"
            )
            continue
        sv, rel = candidates[0]
        meta = fetch_assets(repo, rel["tagName"])
        date = (meta.get("publishedAt") or rel.get("publishedAt") or "")[:10]
        url = meta.get("url") or f"https://github.com/{repo}/releases/tag/{rel['tagName']}"
        history = f"https://github.com/{repo}/releases?q={app['id']}-"
        lines.append(
            f"| {app['display_name']} | `{version_of(rel['tagName'])}` | {date} | "
            f"{archs(meta.get('assets', []))} | [Release]({url}) | [全部]({history}) |"
        )
        for _, old in candidates:
            history_rows.append(
                f"| {app['display_name']} | `{version_of(old['tagName'])}` | "
                f"{(old.get('publishedAt') or '')[:10]} | "
                f"[Release](https://github.com/{repo}/releases/tag/{old['tagName']}) |"
            )

    lines.extend(
        [
            "",
            "<details>",
            "<summary>全部兼容版本（按应用分组）</summary>",
            "",
            "| 应用 | 版本 | 发布时间 | 下载 |",
            "|------|------|----------|------|",
        ]
    )
    lines.extend(history_rows or ["| — | — | — | — |"])
    lines.extend(["", "</details>", "", END])
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

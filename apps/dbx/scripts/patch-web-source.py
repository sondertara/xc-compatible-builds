#!/usr/bin/env python3
"""Apply desktop-mode UI source patches to upstream DBX web frontend before Vite build.

Why source-level patching instead of faking `window.__TAURI_INTERNALS__` or `DBX_DISABLE_PASSWORD=1`:
- `dbx-desktop` wraps upstream's official static `dbx-web-bin` over HTTP/WebSocket, so the
  frontend MUST keep using the Web HTTP transport (`!isTauriRuntime()`).
- `dbx-desktop` keeps per-launch random 32-byte `DBX_PASSWORD` enabled (`DBX_DISABLE_PASSWORD=0`)
  so loopback ports remain authenticated against other local processes and `WebMcpRuntime` stays
  active, while pre-injecting `dbx_session` into WebKitGTK so the user never sees a login gate.
- Consequently, Web-only authentication chrome (the top-right toolbar "Logout" icon and the
  Settings -> "Security" password/captcha/logout tab) has no meaning in a single-user desktop
  window and would trap the user on `/login` if clicked.

Patches applied idempotently (fail-closed if upstream source drifts):
1. `apps/desktop/src/App.vue`:
   - Force `:show-logout="false"` on `<AppToolbar>` and turn `handleWebLogout` into a no-op guard.
2. `apps/desktop/src/components/editor/EditorSettingsDialog.vue`:
   - Omit the Web-only `"security"` category from `settingsCategoryNav` (which also hides all
     `"security"` entries from `settingsSearchEntries` via `visibleCategories`) and guard its
     `<section>` and `<DialogFooter>` branches off while keeping template bindings intact.
3. `apps/desktop/src/lib/settings/settingsSearch.ts`:
   - Map `resolveSettingsCategory("security")` to `"appearance"` so any persisted/deep-linked
     tab state lands on a populated tab.

Usage:
  patch-web-source.py --self-test
  patch-web-source.py /path/to/dbx-src
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PatchRule:
    rel_path: str
    description: str
    old: str
    new: str
    expected_occurrences: int = 1


PATCH_RULES: tuple[PatchRule, ...] = (
    PatchRule(
        rel_path="apps/desktop/src/App.vue",
        description="hide top-right toolbar Web logout button in desktop wrapper",
        old=':show-logout="!isDesktop && needsAuth"',
        new=':show-logout="false"',
        expected_occurrences=1,
    ),
    PatchRule(
        rel_path="apps/desktop/src/components/editor/EditorSettingsDialog.vue",
        description="hide Web security tab from settings category navigation",
        old='...(isWeb ? [{ value: "security" as const, label: t("settings.securityTab") }] : []),',
        new='...(false ? [{ value: "security" as const, label: t("settings.securityTab") }] : []),',
        expected_occurrences=1,
    ),
    PatchRule(
        rel_path="apps/desktop/src/components/editor/EditorSettingsDialog.vue",
        description="disable Web security section and footer rendering in settings dialog",
        old="activeSettingsTab === 'security' && isWeb",
        new="activeSettingsTab === 'security' && false",
        expected_occurrences=2,
    ),
    PatchRule(
        rel_path="apps/desktop/src/lib/settings/settingsSearch.ts",
        description="redirect retired/hidden security settings tab to appearance",
        old='if (initialTab === "sqlFile") return "editor";',
        new='if (initialTab === "sqlFile") return "editor";\n  if (initialTab === "security") return "appearance";',
        expected_occurrences=1,
    ),
)


def apply_rule(text: str, rule: PatchRule) -> tuple[str, bool]:
    """Apply a single PatchRule idempotently, or raise ValueError on upstream drift."""
    old_count = text.count(rule.old)
    new_count = text.count(rule.new)

    # Note: for rules where `rule.old` is a substring of `rule.new` (such as appending a line),
    # check `new_count` first so re-running on an already-patched tree stays idempotent.
    if rule.old in rule.new and new_count == rule.expected_occurrences:
        return text, False

    if old_count == rule.expected_occurrences:
        return text.replace(rule.old, rule.new), True

    if old_count == 0 and new_count == rule.expected_occurrences:
        return text, False

    raise ValueError(
        f"patch drift in {rule.rel_path} ({rule.description}): "
        f"expected {rule.expected_occurrences} occurrence(s) of target snippet, "
        f"found old={old_count}, new={new_count}"
    )


def patch_source_tree(src_root: Path) -> list[str]:
    """Apply all desktop UI source patches under src_root. Returns modified relative paths."""
    by_file: dict[str, list[PatchRule]] = {}
    for rule in PATCH_RULES:
        by_file.setdefault(rule.rel_path, []).append(rule)

    modified: list[str] = []
    for rel_path, rules in by_file.items():
        file_path = src_root / rel_path
        if not file_path.is_file():
            raise FileNotFoundError(f"missing upstream source file: {file_path}")

        original = file_path.read_text(encoding="utf-8")
        updated = original
        changed_file = False
        for rule in rules:
            updated, changed = apply_rule(updated, rule)
            changed_file = changed_file or changed

        if changed_file:
            file_path.write_text(updated, encoding="utf-8")
            modified.append(rel_path)

    return modified


def run_self_test() -> None:
    with tempfile.TemporaryDirectory(prefix="dbx-patch-src-test-") as tmp:
        root = Path(tmp)
        app_vue = root / "apps/desktop/src/App.vue"
        settings_vue = root / "apps/desktop/src/components/editor/EditorSettingsDialog.vue"
        search_ts = root / "apps/desktop/src/lib/settings/settingsSearch.ts"

        app_vue.parent.mkdir(parents=True, exist_ok=True)
        settings_vue.parent.mkdir(parents=True, exist_ok=True)
        search_ts.parent.mkdir(parents=True, exist_ok=True)

        app_vue.write_text(
            '<AppToolbar\n  :show-logout="!isDesktop && needsAuth"\n  @logout="handleWebLogout"\n/>\n',
            encoding="utf-8",
        )
        settings_vue.write_text(
            "\n".join(
                [
                    'const nav = [',
                    '  ...(isWeb ? [{ value: "security" as const, label: t("settings.securityTab") }] : []),',
                    '];',
                    '<section v-else-if="activeSettingsTab === \'security\' && isWeb"></section>',
                    '<DialogFooter v-else-if="activeSettingsTab === \'security\' && isWeb"></DialogFooter>',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        search_ts.write_text(
            'export function resolveSettingsCategory(initialTab?: string) {\n'
            '  if (initialTab === "sqlFile") return "editor";\n'
            '  return "appearance";\n'
            '}\n',
            encoding="utf-8",
        )

        # 1. First pass modifies all 3 files.
        changed1 = patch_source_tree(root)
        assert len(changed1) == 3, f"expected 3 modified files, got {changed1}"
        assert ':show-logout="false"' in app_vue.read_text(encoding="utf-8")
        assert '!isDesktop && needsAuth' not in app_vue.read_text(encoding="utf-8")
        assert "activeSettingsTab === 'security' && isWeb" not in settings_vue.read_text(encoding="utf-8")
        assert settings_vue.read_text(encoding="utf-8").count("activeSettingsTab === 'security' && false") == 2
        assert 'if (initialTab === "security") return "appearance";' in search_ts.read_text(encoding="utf-8")

        # 2. Second pass is strictly idempotent.
        changed2 = patch_source_tree(root)
        assert changed2 == [], f"expected idempotent second run, got {changed2}"

        # 3. Upstream drift fails closed.
        app_vue.write_text("<AppToolbar :show-logout=\"someNewExpression\" />\n", encoding="utf-8")
        try:
            patch_source_tree(root)
        except ValueError as exc:
            assert "App.vue" in str(exc)
        else:
            raise AssertionError("expected ValueError when upstream snippet drifts")

    print("patch-web-source.py self-test: OK")


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--self-test":
        run_self_test()
        return 0

    if len(argv) != 2 or argv[1].startswith("-"):
        print("Usage: patch-web-source.py (--self-test | /path/to/dbx-src)", file=sys.stderr)
        return 2

    src_root = Path(argv[1]).resolve()
    modified = patch_source_tree(src_root)
    print(
        f"patch-web-source.py: verified desktop UI source patches in {src_root} "
        f"(modified {len(modified)} file(s): {', '.join(modified) if modified else 'already patched'})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

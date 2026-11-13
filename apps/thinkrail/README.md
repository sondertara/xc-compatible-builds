# ThinkRail (Electrobun) — WebKitGTK 4.0 compat assets

ThinkRail ships a prebuilt app tree. Only `libNativeWrapper.so` is rebuilt:
Electrobun 2.0.5's native wrapper compiled against WebKitGTK 4.0 (2.38.x) and
libsoup2.4 so the tree runs on Kylin V10 (glibc 2.31, `libwebkit2gtk-4.0-37`).

## Files

- `electrobun-2.0.5-webkit40.patch`
  - `webkit_web_view_evaluate_javascript` → `webkit_web_view_run_javascript`
    (the former is 4.1-only).
  - four `g_memdup2` → `g_memdup` (glib 2.64 has no `g_memdup2`).
  - libsoup2 cookie expiry branches (`SoupDate` vs `GDateTime`).
  - Target: Electrobun **2.0.5**; paths are relative to the tarball's
    `electrobun-2.0.5/package/` root.
- `third_party/webgpu.h`
  - Dawn header pinned to electrobun-dawn 0.2.7. Do **not** replace with
    `webgpu-headers` main; the wrapper includes `dawn/webgpu.h` and expects the
    Dawn layout.

## Bumping Electrobun / ThinkRail

1. Bump `electrobun_version` in `.github/workflows/thinkrail-compat.yml` (and
   the CEF version if Electrobun changed it).
2. Rebase the patch onto the new `package/src/native/linux/nativeWrapper.cpp`;
   keep the hunk contexts small.
3. Re-run CI. The workflow fails closed if any official C export is missing, if
   a 4.1-only symbol remains, or if GLIBC/GLIBCXX exceeds 2.31/3.4.28.


## Web bundle compatibility patch

WebKitGTK 4.0 ships JavaScriptCore 2.38, which does not parse two syntaxes
used by ThinkRail's current frontend bundle:

- regex lookbehind (`(?<=` / `(?<!`) — e.g. the version regex, Markdown email
  autolink, Monaco color regex and diff line splitting;
- class static blocks (`class X { static { ... } }`) — e.g. `OrderedDict`.

`patch-web-assets.py` rewrites these to equivalent capture-group / static-field
forms and runs in CI before packaging. Without it the window opens but the
webview stays blank (the whole bundle fails to parse). The script is
version-scoped in behaviour: it reports how many replacements it made and fails
if the entry bundle still contains lookbehind/static-block syntax.

## Bundled runtime libraries

Kylin V10 does not provide `libayatana-appindicator3-1`. CI copies the focal
runtime sonames (`libayatana-appindicator3.so.1`, `libayatana-indicator3.so.7`,
`libdbusmenu-glib.so.4`, `libdbusmenu-gtk3.so.4`) into `ThinkRail/bin/` as
regular files, so the wrapper resolves them via the launcher's
`LD_LIBRARY_PATH`. No symlinks are shipped.

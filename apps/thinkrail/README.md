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

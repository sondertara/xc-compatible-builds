# DBX (MyGO Sidecar) — Kylin V10 compat assets

Instead of rebuilding Tauri 2 + `webkit2gtk-4.1` in a 3-hour custom base image,
this target wraps upstream's official **fully-static musl `dbx-web-bin`** in a
thin pure-Go [`shared/mygo-sidecar`](../../shared/mygo-sidecar) desktop shell:

- **Zero C toolchain / zero GLIBC version lock**: `dbx-desktop` is built with
  `CGO_ENABLED=0` and `dlopen`s the host Kylin V10 `libwebkit2gtk-4.0.so.37`
  (`2.38.x`) and GTK3 at runtime via `purego`.
- **Official musl backend**: `libexec/dbx-web-bin` comes directly from upstream
  `DBX_<ver>_<x64|arm64>-browser-static.tar.gz` (no ELF interpreter, zero
  shared library dependencies).
- **Zero `/tmp` extraction**: `dbx-desktop` executes `libexec/dbx-web-bin`
  in-place, avoiding `/tmp noexec` failures and cold-start binary unpacking.

## Files

- `adapter.go` + `cmd/dbx-desktop/main.go`:
  - Allocates an ephemeral loopback port (`127.0.0.1:<port>` — never `0`, because
    `dbx-web` reads `DBX_PORT` separately for HTTP and its Redis PubSub server).
  - Generates a 32-byte random hex password per launch (`DBX_PASSWORD`, with
    `DBX_DISABLE_PASSWORD=0` and `DBX_WEB_CAPTCHA=off`), performs `POST /api/auth/login`,
    verifies `/api/auth/check`, and pre-injects the `dbx_session` HttpOnly cookie into
    WebKitGTK before opening the window.
  - Routes WebKitGTK through an in-process ephemeral `LocalGateway` (`webkit_website_data_manager_set_network_proxy_settings`)
    onto the permanent portless virtual origin `http://com-dbx-desktop.localhost/`, keeping
    100% ephemeral random TCP ports while natively preserving WebKit's `localStorage`,
    `IndexedDB` (`dbx-app-state` SQL tabs/drafts/editor state), `WebSocket`, and
    `crypto.subtle` Secure Context across restarts, plus enforcing a single-instance desktop lock.
  - Isolates persistence under `${XDG_DATA_HOME:-~/.local/share}/com.dbx.desktop/data`.
  - Automatically mounts bundled `share/dbx/dist` (or `dist`) beside the binary via
    `DBX_STATIC_DIR`, overriding `dbx-web-bin`'s embedded frontend assets.
  - Injects ES2023/ES2024 JS runtime polyfills (`toSorted`, `toReversed`, `toSpliced`,
    `with`, `Object.groupBy`, `Map.groupBy`, `Promise.withResolvers`) via MyGO preload
    script for JavaScriptCore `2.38.6`.
  - Supports `-smoke-test` (headless sidecar launch, login handshake, and `/` HTTP check)
    and `-ui-smoke <dir>` (real WebKitGTK rendering under `xvfb-run` capturing
    `screenshot.png` + `ui-smoke-report.json` runtime JS/DOM/auth telemetry).
- `scripts/build-web-dist.sh` + `scripts/patch-web-source.py` + `scripts/patch-web-assets.py`:
  - Shallow-clones upstream `t8y2/dbx@<tag>`, applies fail-closed desktop UI source patches
    (hiding the Web-only top-right toolbar logout button and Settings `security` tab), and
    runs the frontend-only Vite build with `build.target = ["es2020", "safari15.4"]` and
    `cssTarget = "safari15.4"` (zero Rust rebuild).
  - Deterministically lowers unguarded `oklch()` / `color-mix()` / `@media` range syntax
    while injecting per-theme `--dbx-compat-<token>-rgb` variables and preserving
    `@supports` progressive enhancement, and silences the desktop "upgrade your browser" banner.
- `scripts/verify-web-compat.py`:
  - Crawls served (`--bin`, optionally with `--static-dir`, or `--url`) or extracted
    (`--dir`) frontend JS and CSS assets, fails closed on JSC 2.38 parse-time JS blockers
    (`(?<=`, `(?<!` regex literals, `static {` blocks) outside strings/templates/comments,
    and checks unguarded CSS `color-mix()`, `oklch()`, and `@media` range syntax
    (hard gate with `--strict-css`).
- `installer.sh`:
  - Self-extracting XDG user installer creating `~/.local/share/com.dbx.desktop/app`
    (including `bin/dbx-desktop`, `libexec/dbx-web-bin`, and `share/dbx/dist`),
    `~/.local/share/applications/DBX.desktop`, and `uninstall`.

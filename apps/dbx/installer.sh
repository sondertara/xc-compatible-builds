#!/usr/bin/env bash
# DBX Kylin self-extracting installer.
#
# Installs the MyGO desktop shell + static musl dbx-web-bin sidecar into
# standard XDG directories on Kylin V10 (glibc 2.31 / WebKitGTK 4.0):
#   <XDG_DATA_HOME>/com.dbx.desktop/app/bin/dbx-desktop
#   <XDG_DATA_HOME>/com.dbx.desktop/app/libexec/dbx-web-bin
#   <XDG_DATA_HOME>/com.dbx.desktop/app/share/dbx/dist
#   <XDG_DATA_HOME>/applications/DBX.desktop
#   <XDG_DATA_HOME>/com.dbx.desktop/uninstall
#
# Usage:
#   ./installer [--quiet] [--no-launch]
#   ./installer --uninstall [--quiet]

set -euo pipefail

IDENTIFIER="com.dbx.desktop"
NAME="DBX"
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
ROOT="$XDG_DATA_HOME/$IDENTIFIER"
APP_DIR="$ROOT/app"
DESKTOP_DIR="$XDG_DATA_HOME/applications"
DESKTOP="$DESKTOP_DIR/$NAME.desktop"

MODE="install"
QUIET=0
NO_LAUNCH=0

say() { [ "$QUIET" = 1 ] || printf '%s\n' "$*"; }
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'USAGE'
DBX Kylin installer

Usage:
  ./installer [--quiet] [--no-launch]
  ./installer --uninstall [--quiet]

Installs to:
  ~/.local/share/com.dbx.desktop/app
and creates:
  ~/.local/share/applications/DBX.desktop
  ~/.local/share/com.dbx.desktop/uninstall
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --uninstall) MODE="uninstall"; shift ;;
        --quiet|-q) QUIET=1; shift ;;
        --no-launch) NO_LAUNCH=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown option: $1" ;;
    esac
done

uninstall_app() {
    rm -rf "$APP_DIR"
    rm -f "$ROOT/uninstall" "$DESKTOP"
    rmdir "$ROOT" 2>/dev/null || true
    say "DBX removed from $APP_DIR"
}

if [ "$MODE" = "uninstall" ]; then
    uninstall_app
    exit 0
fi

PAYLOAD_LINE=$(awk '/^__PAYLOAD_BELOW__$/{print NR; exit}' "$0")
[ -n "$PAYLOAD_LINE" ] || die "installer payload is missing (run the packaged installer, not this raw script)"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/dbx-installer.XXXXXX")
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT HUP INT TERM

tail -n +$((PAYLOAD_LINE + 1)) "$0" | tar -xzf - -C "$TMP"
[ -x "$TMP/DBX/bin/dbx-desktop" ] || die "installer payload is corrupt (missing bin/dbx-desktop)"
[ -x "$TMP/DBX/libexec/dbx-web-bin" ] || die "installer payload is corrupt (missing libexec/dbx-web-bin)"
[ -f "$TMP/DBX/share/dbx/dist/index.html" ] || die "installer payload is corrupt (missing share/dbx/dist/index.html)"

say "Installing DBX to $APP_DIR..."
mkdir -p "$ROOT" "$DESKTOP_DIR"
rm -rf "$APP_DIR"
mv "$TMP/DBX" "$APP_DIR"
chmod 755 "$APP_DIR/bin/dbx-desktop" "$APP_DIR/libexec/dbx-web-bin"

cat >"$ROOT/uninstall" <<UNINSTALL
#!/bin/sh
set -eu
rm -rf "$APP_DIR"
rm -f "$ROOT/uninstall" "$DESKTOP"
rmdir "$ROOT" 2>/dev/null || true
echo "DBX removed (data directory, if present, kept at $ROOT/data)."
UNINSTALL
chmod 755 "$ROOT/uninstall"

cat >"$DESKTOP" <<DESKTOP_ENTRY
[Desktop Entry]
Version=1.0
Type=Application
Name=DBX
Comment=Multi-Database Client (Kylin MyGO Sidecar build)
Exec=$APP_DIR/bin/dbx-desktop
Icon=$APP_DIR/share/icons/dbx.png
Terminal=false
Categories=Development;Database;
StartupWMClass=com.dbx.desktop
DESKTOP_ENTRY
chmod 644 "$DESKTOP"

if [ "$NO_LAUNCH" = 0 ] && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
    setsid "$APP_DIR/bin/dbx-desktop" >/dev/null 2>&1 &
fi

say "Installed. Launcher: $APP_DIR/bin/dbx-desktop"
say "Uninstall: $ROOT/uninstall"
exit 0
__PAYLOAD_BELOW__

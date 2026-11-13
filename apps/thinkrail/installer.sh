#!/usr/bin/env bash
# ThinkRail Kylin self-extracting installer.
#
# Reproduces the official Electrobun install layout without running the
# official installer binary, which requires glibc 2.34+ and cannot start on
# Kylin V10 (glibc 2.31):
#   <XDG_DATA_HOME>/ai.thinkrail.app/stable/app/bin/launcher
#   <XDG_DATA_HOME>/applications/ThinkRail.desktop
#   <XDG_DATA_HOME>/ai.thinkrail.app/stable/uninstall
#
# Usage:
#   ./installer [--quiet] [--no-launch]
#   ./installer --uninstall [--quiet]

set -euo pipefail

IDENTIFIER="ai.thinkrail.app"
NAME="ThinkRail"
CHANNEL="stable"
XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
ROOT="$XDG_DATA_HOME/$IDENTIFIER/$CHANNEL"
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
ThinkRail Kylin installer

Usage:
  ./installer [--quiet] [--no-launch]
  ./installer --uninstall [--quiet]

Installs to:
  ~/.local/share/ai.thinkrail.app/stable/app
and creates:
  ~/.local/share/applications/ThinkRail.desktop
  ~/.local/share/ai.thinkrail.app/stable/uninstall
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

if [ "$MODE" = "uninstall" ]; then
    rm -rf "$ROOT"
    rm -f "$DESKTOP"
    say "ThinkRail removed from $ROOT"
    exit 0
fi

# The payload is a gzip tar appended after the marker line.
PAYLOAD_LINE=$(awk '/^__PAYLOAD_BELOW__$/{print NR; exit}' "$0")
[ -n "$PAYLOAD_LINE" ] || die "installer payload is missing (run the packaged installer, not this raw script)"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/thinkrail-installer.XXXXXX")
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT HUP INT TERM

tail -n +$((PAYLOAD_LINE + 1)) "$0" | tar -xzf - -C "$TMP"
[ -x "$TMP/ThinkRail/bin/launcher" ] || die "installer payload is corrupt"

say "Installing ThinkRail to $APP_DIR..."
mkdir -p "$ROOT" "$DESKTOP_DIR"
rm -rf "$APP_DIR"
mv "$TMP/ThinkRail" "$APP_DIR"
chmod 755 "$APP_DIR/bin/launcher" "$APP_DIR/bin/bun" "$APP_DIR/bin/libNativeWrapper.so" 2>/dev/null || true

# Keep the same metadata/layout the official installer produces.
cat >"$ROOT/.electrobun-uninstall.json" <<JSON
{"identifier":"$IDENTIFIER","channel":"$CHANNEL","name":"$NAME"}
JSON

cat >"$ROOT/uninstall" <<UNINSTALL
#!/bin/sh
set -eu
rm -rf "$ROOT"
rm -f "$DESKTOP"
echo "ThinkRail removed."
UNINSTALL
chmod 755 "$ROOT/uninstall"

mkdir -p "$HOME/.config/$NAME" "$ROOT/WebKit" "$ROOT/preferences" "$ROOT/routes"

cat >"$DESKTOP" <<DESKTOP_ENTRY
[Desktop Entry]
Version=1.0
Type=Application
Name=ThinkRail
Comment=Agentic IDE (Kylin compatible build)
Exec=$APP_DIR/bin/launcher
Icon=$APP_DIR/Resources/appIcon.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=ThinkRail
DESKTOP_ENTRY
chmod 644 "$DESKTOP"

if [ "$NO_LAUNCH" = 0 ] && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
    setsid "$APP_DIR/bin/launcher" >/dev/null 2>&1 &
fi

say "Installed. Launcher: $APP_DIR/bin/launcher"
say "Uninstall: $ROOT/uninstall"
exit 0
__PAYLOAD_BELOW__

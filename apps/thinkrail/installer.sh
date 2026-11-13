#!/usr/bin/env bash
# ThinkRail Kylin installer wrapper around the official Electrobun installer.
#
# The official installer owns the layout, desktop entry, uninstaller and update
# metadata. This wrapper runs it unchanged, then swaps in the WebKitGTK 4.0
# build of libNativeWrapper.so that Kylin V10 needs.
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

The official ThinkRail installer runs first. The Kylin WebKitGTK 4.0
libNativeWrapper.so is then copied into the installed app tree.
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
    if [ -x "$ROOT/uninstall" ]; then
        "$ROOT/uninstall" --quiet || "$ROOT/uninstall" || true
    fi
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
[ -x "$TMP/installer" ] || die "official installer payload is corrupt"
[ -f "$TMP/libNativeWrapper.so" ] || die "Kylin wrapper payload is corrupt"

say "Running the official ThinkRail installer..."
if [ "$QUIET" = 1 ]; then
    if ! DISPLAY= WAYLAND_DISPLAY= "$TMP/installer" --quiet >"$TMP/install.log" 2>&1; then
        cat "$TMP/install.log" >&2
        die "official installer failed"
    fi
else
    if ! DISPLAY= WAYLAND_DISPLAY= "$TMP/installer" --quiet 2>&1 | sed 's/^/  /'; then
        die "official installer failed"
    fi
fi

# The official installer may ignore XDG_DATA_HOME; fall back to its real path.
if [ ! -x "$APP_DIR/bin/launcher" ] && [ -x "$HOME/.local/share/$IDENTIFIER/$CHANNEL/app/bin/launcher" ]; then
    ROOT="$HOME/.local/share/$IDENTIFIER/$CHANNEL"
    APP_DIR="$ROOT/app"
    DESKTOP="$HOME/.local/share/applications/$NAME.desktop"
fi
[ -x "$APP_DIR/bin/launcher" ] || die "official installer did not produce $APP_DIR/bin/launcher"

install -m 755 "$TMP/libNativeWrapper.so" "$APP_DIR/bin/libNativeWrapper.so"
say "Installed the WebKitGTK 4.0 wrapper into $APP_DIR/bin"

if [ ! -f "$DESKTOP" ]; then
    mkdir -p "$DESKTOP_DIR"
    cat >"$DESKTOP" <<DESKTOP_ENTRY
[Desktop Entry]
Type=Application
Name=ThinkRail
Comment=Agentic IDE (Kylin compatible build)
Exec=$APP_DIR/bin/launcher
Icon=$APP_DIR/Resources/app/icon.png
Terminal=false
Categories=Development;IDE;
StartupWMClass=ThinkRail
DESKTOP_ENTRY
    chmod 644 "$DESKTOP"
fi

if [ "$NO_LAUNCH" = 0 ] && { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
    setsid "$APP_DIR/bin/launcher" >/dev/null 2>&1 &
fi

say "ThinkRail installed. Launcher: $APP_DIR/bin/launcher"
say "Uninstall: $ROOT/uninstall"
exit 0
__PAYLOAD_BELOW__

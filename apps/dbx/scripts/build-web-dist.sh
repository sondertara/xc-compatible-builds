#!/usr/bin/env bash
# Build DBX frontend from upstream source with Safari 15.4 / WebKitGTK 2.38.6 target
# and run deterministic CSS lowering + strict JSC/CSS compatibility verification.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TAG=""
SRC_DIR=""
OUT_DIR=""
REPO="https://github.com/t8y2/dbx.git"

usage() {
    cat <<'USAGE'
Usage: build-web-dist.sh (--tag <vX.Y.Z> | --src <dir>) --out <dir> [--repo <url>]

Options:
  --tag <vX.Y.Z>   Upstream release tag/branch to shallow-clone into /tmp
  --src <dir>      Existing upstream DBX source checkout directory
  --out <dir>      Output directory for patched dist/ assets (overwritten)
  --repo <url>     Upstream git URL (default: https://github.com/t8y2/dbx.git)
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --tag) TAG="${2:-}"; shift 2 ;;
        --src) SRC_DIR="${2:-}"; shift 2 ;;
        --out) OUT_DIR="${2:-}"; shift 2 ;;
        --repo) REPO="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$OUT_DIR" ]; then
    echo "Error: --out is required" >&2
    usage >&2
    exit 2
fi
if [ -n "$TAG" ] && [ -n "$SRC_DIR" ]; then
    echo "Error: --tag and --src are mutually exclusive" >&2
    usage >&2
    exit 2
fi
if [ -z "$TAG" ] && [ -z "$SRC_DIR" ]; then
    echo "Error: one of --tag or --src is required" >&2
    usage >&2
    exit 2
fi

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dbx-web-build.XXXXXX")"
TMP_VITE_CONFIG=""
cleanup() {
    if [ -n "$TMP_VITE_CONFIG" ] && [ -f "$TMP_VITE_CONFIG" ]; then
        rm -f "$TMP_VITE_CONFIG"
    fi
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT HUP INT TERM

if [ -n "$TAG" ]; then
    echo "Shallow cloning $REPO ($TAG) into $WORK_DIR/src..."
    git clone --depth 1 --branch "$TAG" "$REPO" "$WORK_DIR/src"
    SRC_DIR="$WORK_DIR/src"
else
    SRC_DIR="$(cd "$SRC_DIR" && pwd)"
fi

if [ ! -f "$SRC_DIR/apps/desktop/vite.config.ts" ]; then
    echo "Error: apps/desktop/vite.config.ts not found in $SRC_DIR" >&2
    exit 1
fi

STORE_ARGS=()
if [ -z "${CI:-}" ]; then
    # Keep temporary pnpm store & caches inside /tmp so host /home disk stays untouched.
    STORE_ARGS=(--store-dir "$WORK_DIR/pnpm-store")
    export XDG_CACHE_HOME="$WORK_DIR/xdg-cache"
    export XDG_STATE_HOME="$WORK_DIR/xdg-state"
    export PNPM_HOME="$WORK_DIR/pnpm-home"
fi

TMP_VITE_CONFIG="$SRC_DIR/apps/desktop/vite.kylin.config.ts"
cat > "$TMP_VITE_CONFIG" <<'EOF'
import { defineConfig, mergeConfig, type ConfigEnv } from "vite";
import baseConfig from "./vite.config";

export default defineConfig(async (env: ConfigEnv) => {
  const resolved = typeof baseConfig === "function" ? await baseConfig(env) : baseConfig;
  return mergeConfig(resolved, {
    build: {
      target: ["es2020", "safari15.4"],
      cssTarget: "safari15.4",
    },
  });
});
EOF

(
    cd "$SRC_DIR"
    export HUSKY=0
    export COREPACK_ENABLE_STRICT=0
    export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
    PNPM_FLAGS=(
        --config.package-manager-strict=false
        --config.manage-package-manager-versions=false
    )

    pnpm install --frozen-lockfile "${PNPM_FLAGS[@]}" "${STORE_ARGS[@]}"
    node scripts/clean-desktop-dist.mjs
    pnpm "${PNPM_FLAGS[@]}" exec vite build --config apps/desktop/vite.kylin.config.ts
)

if [ ! -f "$SRC_DIR/dist/index.html" ]; then
    echo "Error: expected $SRC_DIR/dist/index.html after Vite build" >&2
    exit 1
fi

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
cp -a "$SRC_DIR/dist/." "$OUT_DIR/"

python3 "$SCRIPT_DIR/patch-web-assets.py" --self-test
python3 "$SCRIPT_DIR/patch-web-assets.py" "$OUT_DIR"
python3 "$SCRIPT_DIR/verify-web-compat.py" --self-test
python3 "$SCRIPT_DIR/verify-web-compat.py" --dir "$OUT_DIR" --strict-css

echo "Built and verified WebKitGTK 4.0 compatible DBX frontend at $OUT_DIR"

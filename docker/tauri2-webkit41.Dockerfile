# 定制构建镜像：Ubuntu 20.04 focal (glibc 2.31) + 官方 webkit2gtk-4.1
#
# Tauri 2.x (wry) 要求 webkit2gtk-4.1 (>=2.40)。
# Debian bullseye 只有 4.0 (2.38)，需源码编；Ubuntu 20.04 focal (同为 glibc 2.31)
# 的 universe 源直接提供 libwebkit2gtk-4.1-dev (>=2.40)，apt 即可，无需源码编译 glib/webkit。
#
# 由 .github/workflows/build-image.yml 在 CI 构建（x64 + arm64），推送到 ghcr.io

FROM ubuntu:focal

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils git python3 python3-pip \
    build-essential pkg-config ninja-build ccache file locales tzdata sudo \
    libwebkit2gtk-4.1-dev libjavascriptcoregtk-4.1-dev \
    libgtk-3-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev \
    libsecret-1-dev xdg-utils patchelf \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8 || true

# 自检：glibc 必须 2.31；webkit 必须是 4.1 API 且 >= 2.40；且 .so 的 GLIBC 符号 <= 2.31
RUN set -eu \
    && ldd --version | head -1 | grep -q 2.31 \
    && WK_VER=$(pkg-config --modversion webkit2gtk-4.1) \
    && echo "webkit2gtk-4.1: $WK_VER" \
    && [ "$(printf "2.40\n$WK_VER" | sort -V | tail -1)" != "2.40" ] \
    && SO=$(ls /usr/lib/x86_64-linux-gnu/libwebkit2gtk-4.1.so.0* 2>/dev/null | head -1 || ls /usr/lib/aarch64-linux-gnu/libwebkit2gtk-4.1.so.0* 2>/dev/null | head -1 || ls /usr/lib/*/libwebkit2gtk-4.1.so.0* 2>/dev/null | head -1) \
    && MAXSYM=$(strings "$SO" | grep -oE "GLIBC_[0-9.]+" | sort -Vu | tail -1 | cut -d_ -f2) \
    && echo "libwebkit2gtk-4.1.so max GLIBC symbol: $MAXSYM" \
    && [ "$(printf "2.31\n$MAXSYM" | sort -V | tail -1)" = "2.31" ] \
    && echo "OK: glibc 2.31 + webkit2gtk-4.1 (symbols <= 2.31) ready"

# 定制构建镜像：glibc 2.31 基底 + WebKitGTK 2.40 (webkit2gtk-4.1 API)
#
# !! 为什么需要 !!
# Tauri 2.x (wry) 要求 pkg-config 能找到 webkit2gtk-4.1 (WebKitGTK >= 2.40)，
# 而 glibc 2.31 的公开源（Debian bullseye / Ubuntu focal）里最高只有 2.38 (4.0 API)。
# 已实测确认：deb.debian.org 的 bullseye main/security/updates 均无 4.1 包。
# 因此在 bullseye 基底上源码编译 glib 2.72 + libsoup3 + WebKitGTK 2.40.5。
#
# !! 不要在本地构建 !!
# 由 .github/workflows/build-image.yml 在 CI 构建（x64 + arm64 原生 runner），
# 推送到 ghcr.io 作为缓存，首次约 2~3 小时，仅需一次。

FROM debian:bullseye

ENV DEBIAN_FRONTEND=noninteractive \
    PKG_CONFIG_PATH=/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/aarch64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig:/usr/local/share/pkgconfig

# ---- 基础工具 + WebKitGTK/Tauri 编译依赖 ----
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils git python3 python3-pip \
    build-essential pkg-config ninja-build ccache file locales tzdata sudo \
    # glib 构建依赖
    meson libffi-dev zlib1g-dev libpcre2-dev libmount-dev \
    # webkit 构建依赖
    cmake libxml2-dev libxslt1-dev libsqlite3-dev libgnutls28-dev \
    libicu-dev libgcrypt20-dev libtasn1-dev \
    libgtk-3-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    libhyphen-dev libwebp-dev libjpeg62-turbo-dev libpng-dev \
    libavif-dev libopus-dev libwoff-dev \
    libegl1-mesa-dev libgles2-mesa-dev libgl1-mesa-dri \
    # libsoup3 硬依赖（meson.build 无条件要求，缺失即 setup 失败）
    libnghttp2-dev libpsl-dev libbrotli-dev \
    # Tauri 运行/链接依赖
    libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev \
    libsecret-1-dev xdg-utils patchelf \
    && rm -rf /var/lib/apt/lists/* \
    && locale-gen en_US.UTF-8 || true

# cmake 升级（bullseye 3.18，WebKit 2.40 需要 >= 3.20）
RUN pip3 install --no-cache-dir --upgrade cmake meson

# ---- 1. glib 2.72（WebKit 2.40 需要 >= 2.70，bullseye 只有 2.66）----
# --libdir=lib：统一装到 /usr/local/lib（避免多架构路径在 pkg-config/ldconfig 间不一致）
RUN curl -fsSL https://download.gnome.org/sources/glib/2.72/glib-2.72.4.tar.xz | tar xJ -C /tmp \
    && cd /tmp/glib-2.72.4 \
    && meson setup _build --buildtype=release --libdir=lib -Dtests=false -Dselinux=disabled -Dman=false \
    && ninja -C _build install \
    && ldconfig && rm -rf /tmp/glib-2.72.4 \
    && pkg-config --modversion glib-2.0 | grep -q 2.72 \
    && echo "glib 2.72 OK"

# ---- 2. libsoup3（webkit 4.1 API 依赖，bullseye 无此包；3.2 系列最高 3.2.3）----
RUN curl -fsSL https://download.gnome.org/sources/libsoup/3.2/libsoup-3.2.3.tar.xz | tar xJ -C /tmp \
    && cd /tmp/libsoup-3.2.3 \
    && meson setup _build --buildtype=release --libdir=lib -Dtests=false -Dvapi=disabled -Dgssapi=disabled -Ddocs=disabled \
    && ninja -C _build install \
    && ldconfig && rm -rf /tmp/libsoup-3.2.3 \
    && pkg-config --modversion libsoup-3.0 \
    && echo "libsoup3 OK"

# ---- 3. WebKitGTK 2.40.5（提供 webkit2gtk-4.1 API；2.40 系列最高小版本）----
# 关闭不必要的组件控制编译时长与依赖面
RUN curl -fsSL https://webkitgtk.org/releases/webkitgtk-2.40.5.tar.xz | tar xJ -C /tmp \
    && cd /tmp/webkitgtk-2.40.5 \
    && cmake -B _build -GNinja -DCMAKE_BUILD_TYPE=Release -DPORT=GTK \
       -DCMAKE_INSTALL_PREFIX=/usr/local \
       -DENABLE_DOCUMENTATION=OFF \
       -DENABLE_MINIBROWSER=OFF \
       -DENABLE_INTROSPECTION=OFF \
       -DENABLE_API_TESTS=OFF \
       -DENABLE_BUBBLEWRAP_SANDBOX=OFF \
       -DENABLE_GAMEPAD=OFF \
       -DENABLE_WEBDRIVER=OFF \
       -DUSE_SOUP2=OFF \
       -DUSE_WPE_RENDERER=OFF \
       -DUSE_JXL=OFF \
       -DUSE_SKIA=OFF \
       -DUSE_LD_GOLD=OFF \
    && ninja -C _build -j"$(nproc)" install \
    && ldconfig && rm -rf /tmp/webkitgtk-2.40.5 \
    && pkg-config --modversion webkit2gtk-4.1 \
    && echo "webkit2gtk-4.1 OK"

# 自检：glibc 必须 2.31；webkit 必须是 4.1 API 且 >= 2.40；
# 且编译产物 .so 的 GLIBC 符号 <= 2.31（麒麟桌面 V10 硬约束，超限即构建失败）
RUN set -eu \
    && ldd --version | head -1 | grep -q 2.31 \
    && [ "$(printf "2.40\n$(pkg-config --modversion webkit2gtk-4.1)" | sort -V | tail -1)" != "2.40" ] \
    && SO=$(ls /usr/local/lib/libwebkit2gtk-4.1.so.0* | head -1) \
    && MAXSYM=$(strings "$SO" | grep -oE "GLIBC_[0-9.]+" | sort -Vu | tail -1 | cut -d_ -f2) \
    && echo "libwebkit2gtk-4.1.so max GLIBC symbol: $MAXSYM" \
    && [ "$(printf "2.31\n$MAXSYM" | sort -V | tail -1)" = "2.31" ] \
    && echo "OK: glibc 2.31 + webkit2gtk-4.1 (symbols <= 2.31) ready"

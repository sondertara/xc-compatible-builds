# 定制构建镜像：glibc 2.31 基底 + WebKitGTK 2.44 (webkit2gtk-4.1 API)
#
# Tauri 2.x (wry) 要求 webkit2gtk-4.1 (>=2.40) 且需要完整 cookie API (get_all_cookies 等)。
# glibc 2.31 的发行版（bullseye/focal）官方源只有 webkit2gtk-4.0 (2.38) 或 4.1 太旧 (2.40.5 缺 cookie API)。
# 因此在 bullseye 基底上源码编译 glib 2.72 + libsoup3 + WebKitGTK 2.44.x。
#
# 由 .github/workflows/build-image.yml 在 CI 构建（x64 + arm64 原生 runner），
# 推送到 ghcr.io 作为缓存，首次约 2~3 小时，仅需一次。

FROM debian:bullseye

ENV DEBIAN_FRONTEND=noninteractive \
    PKG_CONFIG_PATH=/usr/local/lib/x86_64-linux-gnu/pkgconfig:/usr/local/lib/aarch64-linux-gnu/pkgconfig:/usr/local/lib/pkgconfig:/usr/local/share/pkgconfig \
    LIBRARY_PATH=/usr/local/lib:/usr/local/lib/x86_64-linux-gnu:/usr/local/lib/aarch64-linux-gnu

# bullseye 已是 oldoldstable；deb.debian.org 的 bullseye-security 索引仍指向
# 已被清理的 pool 文件（404），apt 会整体失败。archive.debian.org 保留完整
# bullseye/bullseye-security 包，因此这里固定到归档源。
RUN rm -f /etc/apt/sources.list.d/* \
    && printf 'deb http://archive.debian.org/debian bullseye main\n' > /etc/apt/sources.list \
    && printf 'deb http://archive.debian.org/debian-security bullseye-security main\n' >> /etc/apt/sources.list

# ---- 基础工具 + WebKitGTK/Tauri 编译依赖 ----
# 注意：apt 参数列表内不能夹注释——Dockerfile 的反斜杠续行会把注释交给
# shell，第一个 # 之后整段包名都会被当成注释丢掉（曾导致 pip3 缺失）。
# 注释统一写在 RUN 之前。
# - ruby/ruby-dev, gperf/bison/flex/perl/gettext: WebKit 代码生成工具
# - unifdef: WebKit OptionsGTK.cmake 配置阶段强制要求
# - meson/libffi/zlib/libpcre2/libmount: glib 构建依赖
# - cmake/libxml2/...: WebKit 构建依赖
# - libcairo/fontconfig/freetype/harfbuzz/epoxy/gbm/drm/lcms2: OptionsGTK 强制 find_package
# - libxcomposite/xdamage/xrender/xt: X11 target 强制依赖
# - libenchant-2: SPELLCHECK
# - libsystemd: JOURNALD_LOG
# - libnghttp2/libpsl/libbrotli: libsoup3 硬依赖
# - libayatana-appindicator3/librsvg/xdo/ssl/secret: Tauri 运行/链接依赖
RUN apt-get -o Acquire::Check-Valid-Until=false update && apt-get install -y --no-install-recommends \
    ca-certificates curl xz-utils git python3 python3-pip \
    build-essential pkg-config ninja-build ccache file locales tzdata sudo \
    ruby ruby-dev \
    gperf bison flex perl gettext \
    unifdef \
    meson libffi-dev zlib1g-dev libpcre2-dev libmount-dev \
    cmake libxml2-dev libxslt1-dev libsqlite3-dev libgnutls28-dev \
    libicu-dev libgcrypt20-dev libtasn1-dev \
    libgtk-3-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    libhyphen-dev libwebp-dev libjpeg62-turbo-dev libpng-dev \
    libavif-dev libopus-dev libwoff-dev \
    libegl1-mesa-dev libgles2-mesa-dev libgl1-mesa-dri \
    libcairo2-dev libfontconfig1-dev libfreetype6-dev libharfbuzz-dev \
    libepoxy-dev libgbm-dev libdrm-dev liblcms2-dev \
    libxcomposite-dev libxdamage-dev libxrender-dev libxt-dev \
    libenchant-2-dev \
    libsystemd-dev \
    libnghttp2-dev libpsl-dev libbrotli-dev \
    libayatana-appindicator3-dev librsvg2-dev libxdo-dev libssl-dev \
    libsecret-1-dev xdg-utils patchelf

# locale 单独执行：不要用 || true 掩盖 apt 失败（缺包会导致后面 pip3 都找不到）
RUN rm -rf /var/lib/apt/lists/* && locale-gen en_US.UTF-8

# cmake 升级（bullseye 3.18，WebKit 2.44 需要 >= 3.20 且 < 4.0；4.x 已移除旧语法）
# 使用默认 PyPI；cmake 3.28.x 是最后支持 Python 3.6 的系列
RUN pip3 install --no-cache-dir --upgrade \
    "cmake>=3.24,<3.29" meson

# ---- 1. glib 2.72（WebKit 2.44 需要 >= 2.70，bullseye 只有 2.66）----
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
    && meson setup _build --buildtype=release --libdir=lib -Dtests=false -Dvapi=disabled -Dgssapi=disabled -Ddocs=disabled -Dtls_check=false \
    && ninja -C _build install \
    && ldconfig && rm -rf /tmp/libsoup-3.2.3 \
    && pkg-config --modversion libsoup-3.0 \
    && echo "libsoup3 OK"

# ---- 3. WebKitGTK 2.44.4（提供 webkit2gtk-4.1 API；2.44 系列有完整 cookie API）----
# 启用必要组件，关闭不必要的组件控制编译时长与依赖面
# 2021 版 bullseye ca-certificates 对 webkitgtk.org 当前证书链可能缺根证书，
# 而镜像里没有更近的 CA 包。用 -k 关闭 TLS 校验，但立刻用固定 SHA256 校验内容；
# 同时把 curl 与 tar 拆成两步，避免管道掩盖 curl 的真实退出码。
RUN curl -k -fsSL --retry 3 --retry-delay 5 \
        https://webkitgtk.org/releases/webkitgtk-2.44.4.tar.xz \
        -o /tmp/webkitgtk-2.44.4.tar.xz \
    && echo "2ce4ec1b78413035037aba8326b31ed72696626b7bea7bace5e46ac0d8cbe796  /tmp/webkitgtk-2.44.4.tar.xz" | sha256sum -c - \
    && tar xJf /tmp/webkitgtk-2.44.4.tar.xz -C /tmp \
    && rm -f /tmp/webkitgtk-2.44.4.tar.xz \
    && cd /tmp/webkitgtk-2.44.4 \
    && cmake -B _build -GNinja -DCMAKE_BUILD_TYPE=Release -DPORT=GTK \
       -DCMAKE_INSTALL_PREFIX=/usr/local \
       -DENABLE_DOCUMENTATION=OFF \
       -DENABLE_MINIBROWSER=OFF \
       -DENABLE_INTROSPECTION=OFF \
       -DENABLE_API_TESTS=OFF \
       -DENABLE_BUBBLEWRAP_SANDBOX=OFF \
       -DENABLE_GAMEPAD=OFF \
       -DENABLE_WEBDRIVER=OFF \
       -DENABLE_WAYLAND_TARGET=OFF \
       -DENABLE_SPELLCHECK=ON \
       -DENABLE_SPEECH_SYNTHESIS=OFF \
       -DENABLE_JOURNALD_LOG=ON \
       -DENABLE_MEDIA_STREAM=OFF \
       -DENABLE_MEDIA_RECORDER=OFF \
       -DENABLE_WEB_RTC=OFF \
       -DUSE_OPENJPEG=OFF \
       -DUSE_AVIF=OFF \
       -DUSE_SOUP2=OFF \
       -DUSE_JPEGXL=OFF \
       -DUSE_SKIA=OFF \
       -DUSE_LD_GOLD=OFF \
    && ninja -C _build -j"$(nproc)" install \
    && ldconfig && rm -rf /tmp/webkitgtk-2.44.4 \
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
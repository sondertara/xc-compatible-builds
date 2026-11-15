# ThinkRail（Electrobun）— 麒麟 V10 兼容适配说明

ThinkRail 上游直接发布预编译应用目录树。本适配方案保留上游主体产物，仅针对麒麟桌面版 V10（`glibc 2.31`、`libwebkit2gtk-4.0-37 / 2.38.x`、`libsoup-2.4`）重编替换 Electrobun 原生桥接库 `libNativeWrapper.so`，并对前端产物执行 JSC 2.38 语法转换。

## 目录结构与职责

- `electrobun-2.0.5-webkit40.patch`（面向 Electrobun `2.0.5` `package/` 根目录）：
  - 将 `4.1` 专属接口 `webkit_web_view_evaluate_javascript` 替换为 `webkit_web_view_run_javascript`；
  - 将 4 处 `g_memdup2` 替换为 `g_memdup`（兼容 `glib 2.64`）；
  - 适配 `libsoup-2.4` Cookie 过期时间结构（`SoupDate` 替代 `GDateTime`）。
- `third_party/webgpu.h`：
  - 锁定 `electrobun-dawn 0.2.7` 对应的 Dawn 头文件布局（请勿替换为通用 `webgpu-headers` 主干版本）。
- `patch-web-assets.py`：
  - WebKitGTK 4.0 内置的 JavaScriptCore 2.38 不支持正则后行断言（`(?<=` / `(?<!`）与 `class static {}` 静态初始化块，否则会导致整个入口 Bundle 解析失败白屏；
  - 构建阶段自动将其等价改写为捕获组与静态字段形式，若改写后仍残留不兼容语法则直接阻断构建。
- `installer.sh`：
  - 用户态自解压安装脚本，安装至官方同款路径 `~/.local/share/ai.thinkrail.app/stable/app` 并注册桌面启动项与卸载脚本。

## 随包运行库说明

麒麟桌面版 V10 默认源未提供 `libayatana-appindicator3-1`。CI 构建阶段会从 Ubuntu 20.04（focal）提取 `libayatana-appindicator3.so.1`、`libayatana-indicator3.so.7`、`libdbusmenu-glib.so.4`、`libdbusmenu-gtk3.so.4` 实体文件放入 `ThinkRail/bin/`，由启动器通过 `LD_LIBRARY_PATH` 就近加载。

## 上游升级指引

1. 若上游升级了 Electrobun 版本，同步更新 `.github/workflows/thinkrail-compat.yml` 中的 `electrobun_version`（及对应的 CEF 版本号）。
2. 基于新版 `package/src/native/linux/nativeWrapper.cpp` 重新对齐补丁并命名为对应版本。
3. 触发 CI 验证：流水线会自动核对官方全部 173 个 C 导出符号、零 `4.1` 符号残留、`NEEDED` 动态库依赖以及 `GLIBC <= 2.31` / `GLIBCXX <= 3.4.28` 上限。

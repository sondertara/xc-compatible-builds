# xc-compatible-builds — 麒麟 V10 桌面应用兼容构建仓库

面向 **银河麒麟桌面版 V10**（以及 Ubuntu 20.04、Debian 11、统信 UOS 等 `glibc 2.31` + `WebKitGTK 4.0 / 2.38.x` 同代 Linux 桌面环境），提供上游现代跨平台桌面软件的 **`x86_64` 与 `aarch64` 双架构兼容发行包**，并按周自动巡检上游更新。

针对不同上游架构提供 **四条技术适配路线**（**MyGO Web Sidecar**、**Go / Wails v2**、**Electrobun Wrapper 替换**、**Tauri 2 定制镜像**），在优先复用系统原生 GTK3 / WebKitGTK 4.0 的前提下消除高版本 `GLIBC`、`webkit2gtk-4.1` 与现代前端引擎语法壁垒。

---

## 兼容版本下载

<!-- releases:start -->

| 应用 | 最新版本 | 发布日期 | 下载 |
| :--- | :--- | :--- | :--- |
| [**DBX**](https://github.com/t8y2/dbx) | `0.6.71` | 2026-11-16 | [x86_64](https://github.com/sondertara/xc-compatible-builds/releases/download/dbx-v0.6.71/DBX-0.6.71-linux-amd64-installer.tar.gz) · [aarch64](https://github.com/sondertara/xc-compatible-builds/releases/download/dbx-v0.6.71/DBX-0.6.71-linux-aarch64-installer.tar.gz) · [Release](https://github.com/sondertara/xc-compatible-builds/releases/tag/dbx-v0.6.71) |
| [**GoNavi**](https://github.com/Syngnat/GoNavi) | `1.2.2` | 2026-11-12 | [x86_64](https://github.com/sondertara/xc-compatible-builds/releases/download/gonavi-v1.2.2/GoNavi-1.2.2-Linux-Amd64.tar.gz) · [aarch64](https://github.com/sondertara/xc-compatible-builds/releases/download/gonavi-v1.2.2/GoNavi-1.2.2-Linux-Arm64.tar.gz) · [Release](https://github.com/sondertara/xc-compatible-builds/releases/tag/gonavi-v1.2.2) |
| [**ThinkRail**](https://github.com/JetBrains/thinkrail) | `0.4.0` | 2026-11-13 | [x86_64](https://github.com/sondertara/xc-compatible-builds/releases/download/thinkrail-v0.4.0/ThinkRail-0.4.0-linux-amd64-installer.tar.gz) · [aarch64](https://github.com/sondertara/xc-compatible-builds/releases/download/thinkrail-v0.4.0/ThinkRail-0.4.0-linux-aarch64-installer.tar.gz) · [Release](https://github.com/sondertara/xc-compatible-builds/releases/tag/thinkrail-v0.4.0) |

- **历史版本**：**DBX** [`0.6.70`](https://github.com/sondertara/xc-compatible-builds/releases/tag/dbx-v0.6.70)；**GoNavi** [`0.9.7`](https://github.com/sondertara/xc-compatible-builds/releases/tag/gonavi-v0.9.7) · [`0.9.4`](https://github.com/sondertara/xc-compatible-builds/releases/tag/gonavi-v0.9.4)

<!-- releases:end -->

> 发布标签统一采用 `<app-id>-v<semver>`（如 `dbx-v0.6.70`），机器可读版本清单同步输出于 [`release/index.json`](release/index.json)。

---

## 安装与运行

### 系统环境基线

- **CPU 架构**：`x86_64`（`amd64`）或 `aarch64`（`arm64`）
- **基础运行库**：`glibc >= 2.31`、`libgtk-3-0`、`libwebkit2gtk-4.0-37`（`2.38.x`，麒麟桌面版 V10 / Ubuntu 20.04 默认预装）

### 安装方式

各应用 Release 页面按软件形态提供以下产物（详见对应 Release 说明）：

1. **XDG 用户态安装器（推荐，如 DBX / ThinkRail）**
   无需 `root` 权限，自动完成应用目录部署、启动器桌面图标（`~/.local/share/applications/*.desktop`）注册及专属 `uninstall` 卸载脚本生成：
   ```bash
   tar -xzf <App>-<version>-linux-<arch>-installer.tar.gz
   ./installer
   ```
2. **免安装便携包（`*-kylin.tar.gz` 或单包归档）**
   解压后直接运行目录内主程序，不写入桌面菜单项：
   ```bash
   # DBX
   tar -xzf DBX-<version>-linux-<arch>-kylin.tar.gz && ./DBX/bin/dbx-desktop

   # ThinkRail
   tar -xzf ThinkRail-<version>-linux-<arch>-kylin.tar.gz && ./ThinkRail/bin/launcher

   # GoNavi
   tar -xzf GoNavi-<version>-Linux-<Amd64|Arm64>.tar.gz && ./GoNavi
   ```
3. **产物校验**
   每个 Release 均附带 SHA-256 校验文件，下载后可执行 `sha256sum -c SHA256SUMS-<arch>.txt`（或对应 `.sha256` 文件）核验完整性。

---

## 四条兼容适配路线

并非所有跨平台桌面应用都需要耗时数小时重编整套 WebKitGTK。本仓库按上游架构特征采用最小侵入路径：

| 适配路线 | 适用场景 | 核心实现机制 | 配置与说明 |
| :--- | :--- | :--- | :--- |
| **MyGO Web Sidecar** | 上游提供纯静态/独立 Web 后端的应用 | `ubuntu:20.04` 构建 `CGO_ENABLED=0` 纯 Go 外壳运行时 `dlopen` 系统 GTK3 + WebKitGTK 4.0；通过引擎代理将固定源 `http://<app>.localhost/` 路由至随机端口后端，前端降级适配 Safari 15.4 | [`dbx.yml`](configs/dbx.yml) · [`apps/dbx`](apps/dbx/README.md) · [`mygo-sidecar`](shared/mygo-sidecar/README.md) |
| **Go / Wails v2** | Wails v2 桌面应用 | `ubuntu:20.04` 下清理上游针对 Ubuntu 22.04+ 硬编码的 `webkit2_41` 标签，按 `go.mod` 锁定 Wails CLI 直接链接 WebKitGTK 4.0 原生编译 | [`gonavi.yml`](configs/gonavi.yml) |
| **Electrobun Wrapper** | 发布预编译产物树的 Electrobun 应用 | `ubuntu:20.04` 下仅针对 WebKitGTK 4.0 + `libsoup-2.4` 重编替换 `libNativeWrapper.so`；转换前端 JSC 2.38 语法并随包附带 `libayatana-appindicator3` | [`thinkrail.yml`](configs/thinkrail.yml) · [`apps/thinkrail`](apps/thinkrail/README.md) |
| **Tauri 2 / AppImage** | 强耦合 `wry`（需 `webkit2gtk-4.1`）且无独立 Web 后端的应用 | 在 `debian:bullseye`（`glibc 2.31`）定制镜像内预编译 `WebKitGTK 2.44.4` + `libsoup 3.2.3` 并缓存至 GHCR 复用 | [`template.yml`](configs/template.yml) · [`Dockerfile`](docker/tauri2-webkit41.Dockerfile) |

---

## 质量门禁体系

每次 CI 构建在打包发布前均执行多层硬性校验，任一环节不达标即阻断发布（Fail-Closed）：

- **构建基底预检**：强制校验构建容器 `glibc == 2.31`，防止高版本符号混入。
- **ELF 符号与依赖审计**：扫描产出的可执行文件与随包 `.so`，确保最高 `GLIBC` 版本需求 ≤ `2.31`；Electrobun 路径额外核对全部官方 C 导出符号及无 `4.1` 专属符号残留；MyGO Sidecar 路径校验外壳零高版本 GLIBC 依赖与后端纯静态零外部动态库。
- **前端引擎静态门禁（JSC 2.38 / Safari 15.4）**：扫描全部 JS/CSS 产物，拦截会导致 WebKitGTK 2.38 白屏或样式失效的正则后行断言（`(?<=` / `(?<!`）、`class static {}` 块，以及未受 `@supports` 保护的裸 `oklch()`、`color-mix()` 与 `@media` 范围语法。
- **实机级无头渲染冒烟（UI Smoke）**：在 `ubuntu:20.04` 容器中启动 `xvfb-run` + 真实 `WebKitGTK 4.0 (2.38.x)` 加载应用，校验虚拟源路由、免密会话注入、DOM 挂载元素数、零运行时 JS 异常，并自动导出实机渲染截图留档。
- **安装器闭环自测**：在隔离 `HOME` 下完整执行静默安装 → 启动探针校验 → 卸载清理验证。

---

## 仓库结构与自动化流水线

### 目录规范

应用声明清单、单应用专属资产与跨应用通用底座严格分层（详见 [`apps/README.md`](apps/README.md)）：

```text
.
├── configs/
│   ├── apps.yml                 # 全局注册表：参与上游版本自动巡检的应用列表
│   ├── template.yml             # Tauri 2 路线声明模板
│   └── <id>.yml                 # 单应用配置清单（上游仓库、构建类型、入口 workflow）
├── shared/
│   └── mygo-sidecar/            # 跨应用复用的纯 Go WebKitGTK 4.0 桌面外壳与虚拟源网关底座
├── apps/
│   └── <id>/                    # 单应用专属适配器、源码/前端补丁、随包头文件与构建脚本
├── release/
│   ├── notes-template.md        # 统一 Release Notes 渲染模板
│   ├── index.json               # 自动生成的按应用分组版本索引（JSON）
│   └── apps/<id>/               # 单应用 Release 文案片段（install.md / diff.md / check.md）
├── docker/
│   └── tauri2-webkit41.Dockerfile # Tauri 2 路线专用的 glibc 2.31 + WebKitGTK 2.44.4 基底镜像
├── scripts/
│   ├── render-release-notes.py  # 合并上游更新日志与兼容说明生成 Release 正文
│   └── build-release-index.py   # 刷新 README 版本表与 release/index.json
└── .github/workflows/           # CI/CD 巡检、构建、发布与索引同步工作流
```

### 工作流职责一览

| 工作流文件 | 职责说明 | 触发方式 |
|------------|----------|----------|
| `upstream-watch.yml` | 遍历 `configs/apps.yml` 比对上游最新 Release；若本仓尚无对应 `<id>-<tag>` 资产则自动派发构建 | 每周一 `02:00 UTC` / 手动 / `push`（仅 Dry-Run） |
| `release-index.yml` | 汇总全部 `<id>-v<semver>` Release，自动更新本页版本索引表与 `release/index.json` | 各应用发版后联动调用 / 每日定时 / 手动 |
| `dbx-compat.yml` | DBX（MyGO Sidecar + 官方静态 `dbx-web-bin` + WebKitGTK 4.0 前端降级与 UI 冒烟）构建发布 | 手动 / Upstream Watch 派发 |
| `gonavi-build.yml` | GoNavi（Go / Wails v2 + WebKitGTK 4.0）双架构编译发布 | 手动 / Upstream Watch 派发 |
| `thinkrail-compat.yml` | ThinkRail（Electrobun `libNativeWrapper.so` WebKitGTK 4.0 重编与前端补丁）打包发布 | 手动 / Upstream Watch 派发 |
| `app-build.yml` | 通用 Tauri 2 应用构建入口，读取 `configs/<id>.yml` 调用复用工作流 | 手动 / Upstream Watch 派发 |
| `reusable-appimage.yml` | 可供本仓或外部仓库通过 `workflow_call` 复用的 Tauri 2 双架构构建流水线 | `workflow_call` |
| `build-image.yml` | 双架构原生编译 `tauri2-webkit41` 定制镜像并推送至 GHCR（首次约 2–3 小时/架构，按需复用） | 手动 / Dockerfile 变更 |

### 接入新应用步骤

1. **选择适配路线**：在 `configs/<id>.yml` 创建清单并指定 `workflow:`（Tauri 复制 `configs/template.yml`，Wails / Electrobun / MyGO Sidecar 分别参考 `gonavi.yml` / `thinkrail.yml` / `dbx.yml`）。
2. **放置专属资产**：应用补丁、脚本或 Sidecar 适配器放入 `apps/<id>/`；跨应用通用能力放入 `shared/`。
3. **补充发布说明**：在 `release/apps/<id>/` 下提供 `install.md`、`diff.md`、`check.md`（支持 `{{VERSION}}` 占位符，发版时自动拉取上游 Release Notes 合并渲染）。
4. **注册自动跟踪**：将应用加入 `configs/apps.yml`（`auto_track: true`），先通过 **Upstream Watch**（勾选 `dry_run`）验证版本发现，再触发正式构建。

### 外部仓库复用（Tauri 2 / AppImage）

外部 Tauri 2 仓库可直接调用本仓的可复用工作流（需先准备好兼容基底镜像）：

```yaml
jobs:
  build:
    uses: sondertara/xc-compatible-builds/.github/workflows/reusable-appimage.yml@main
    with:
      app_id: my-tauri-app
      upstream_repo: owner/my-tauri-app
      container_image: ghcr.io/sondertara/xc-compat-tauri2:amd64
      container_image_arm64: ghcr.io/sondertara/xc-compat-tauri2:aarch64
    secrets: inherit
```

---

## 注意事项与已知边界

- **应用内在线自更新**：兼容包针对麒麟底层运行库做了二进制或前端降级适配；若触发上游内置的在线自动更新，可能会被上游原版二进制覆盖（如 Electrobun 的 `libNativeWrapper.so`），建议通过本仓库最新安装包直接覆盖升级。
- **实机验证原则**：CI 中的 ABI 符号审计与容器 Xvfb 渲染冒烟已覆盖绝大多数启动崩溃与白屏问题，但首次接入新桌面框架时仍建议在麒麟 V10 实机核验输入法、系统托盘与文件对话框交互。
- **GitHub 定时任务休眠机制**：公开仓库连续 60 天无提交活动时，GitHub 会自动暂停 `schedule` 定时巡检；如遇暂停，在 Actions 页面点击 **Enable workflow** 即可恢复。

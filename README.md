# xc-compatible-builds — 麒麟 V10 兼容构建仓库

> 目标：让官方基于新系统构建的桌面软件能在**麒麟桌面版 V10（glibc 2.31）**上运行。
> 原理：在 glibc 2.31 基底容器内重新编译，产物向下精确匹配。
>
> 这里**不只支持 Rust/Tauri**。Tauri 只是其中一条路径；Go/Wails、Electrobun
> 等非 Rust 桌面应用同样可以接入，见下面的“三条构建路径”。

## 三条构建路径

| 路径 | 适用应用 | 构建方式 | 参考 |
|------|----------|----------|------|
| **Tauri / AppImage** | Tauri 2（wry，需要 WebKitGTK 4.1） | 定制镜像 + `app-build.yml` → `reusable-appimage.yml` | `configs/dbx.yml` |
| **Wails / Go** | Wails v2（WebKitGTK 4.0） | `ubuntu:20.04` + `gonavi-build.yml` | `configs/gonavi.yml` |
| **Electrobun / 预编译树** | Electrobun（替换 `libNativeWrapper.so`） | 重编译 wrapper + `thinkrail-compat.yml` | `configs/thinkrail.yml` |

不是所有应用都能套 `template.yml`。Tauri 之外的路径需要自己的入口 workflow，
但都通过同一个清单字段 `workflow:` 接入每周巡检。

> **Wails 的坑**：上游 `wails.json` 可能把 `build:tags` 钉在 `webkit2_41`
> （面向 Ubuntu 22.04+）。`gonavi-build.yml` 在构建前清空该字段，否则 focal
> 的 WebKitGTK 4.0 头文件会编译失败；Wails CLI 版本则从 `go.mod` 读取。

## 工作流一览

| 工作流 | 作用 | 触发 |
|--------|------|------|
| **Upstream Watch** | 遍历 `configs/apps.yml`，查上游最新 Release，按需 dispatch 对应构建 | 手动 / 每周一 02:00 UTC / push（仅 dry-run） |
| **Build Compat Image** | 构建定制镜像（bullseye + 源码编译 WebKitGTK 4.1），推 ghcr | 手动 / Dockerfile 变更 |
| **App Build** | Tauri/AppImage 单软件构建，读 `configs/<id>.yml` | 手动 / 被 Upstream Watch dispatch |
| **GoNavi Compat Build** | Go/Wails 单软件构建（WebKitGTK 4.0） | 手动 / 被 Upstream Watch dispatch |
| **ThinkRail Kylin compat** | Electrobun wrapper 重编译 + 打包 | 手动 / 被 Upstream Watch dispatch |
| **Reusable — AppImage** | 给本仓库 `app-build.yml` 或外部仓库复用的 Tauri 构建 | `workflow_call` |

### 自动跟踪是怎么工作的

1. 在 `configs/apps.yml` 注册应用；`configs/<id>.yml` 里写 `workflow:`（默认
   `app-build.yml`）和 `build_type:`。
2. 每周一 `upstream-watch.yml` 读取上游 `releases/latest`，把 tag 规范成 `vX.Y.Z`。
3. 如果本仓库已有 `<id>-<tag>` Release 且**至少有一个资产**，跳过；否则 dispatch
   对应 workflow，并传入 `version=<tag>`。
4. `app-build.yml` 额外接收 `app_id`；专用 workflow（GoNavi/ThinkRail）从自己的
   `upstream_repo`/config 推导其余参数。

> **为什么旧版看起来失效了**：GitHub 会对 60 天无仓库活动的公开仓库自动禁用
> `schedule`，原 `compat-build.yml` 和 `gonavi-compat.yml` 都变成了
> `disabled_inactivity`。本次把入口改名为 `upstream-watch.yml` /
> `gonavi-build.yml` 以重新激活；如果以后再次被禁用，在 Actions 页面点
> “Enable workflow” 即可。push 触发的 run 只做 dry-run，不会真的开构建。

## 目录规范

清单与构建资产分开，避免把某个应用的补丁/头文件堆在仓库根部：

```
configs/<id>.yml          # 清单：上游、镜像、artifact、workflow
configs/apps.yml          # 注册表：参与自动跟踪的应用
apps/<id>/                # 仅该应用使用的资产
├── patches/*.patch       # 按上游版本命名
├── third_party/**        # vendored 头文件/构建输入
└── scripts/**            # 可选
.github/workflows/<id>-compat.yml  # GitHub 要求 workflow 必须在这里
```

详见 [apps/README.md](apps/README.md)。新增应用不要把文件放回顶层
`patches/` 或 `third_party/`。

## 快速开始（Tauri：dbx）

1. Actions → **Build Compat Image** → Run workflow（首次约 2~3h/架构）
2. Actions → **App Build** → `app_id=dbx`，version 留空
3. 产物在 Release（tag 如 `dbx-v0.5.91`），命名与官方一致
4. 麒麟实机：`chmod +x DBX_*.AppImage && ./DBX_*.AppImage`

## 新增一个应用

1. 选路径：Tauri → 复制 `configs/template.yml`；Wails → 参考 `configs/gonavi.yml`；
   Electrobun/预编译树 → 参考 `configs/thinkrail.yml`。
2. 在 `configs/apps.yml` 注册（`auto_track: true` 参与每周巡检）。
3. 应用专属补丁/头文件放 `apps/<id>/`。
4. 跑一次 **Upstream Watch**（可先勾 `dry_run`）确认能发现版本，再手动触发构建。

## 定制镜像（仅 Tauri 4.1 路径需要）

Tauri 2.x（wry）要求 `webkit2gtk-4.1`（WebKitGTK ≥ 2.40，libsoup3 API）。
glibc 2.31 的公共 Debian/Ubuntu 源里没有这个包，因此定制镜像 =
`debian:bullseye`（glibc 2.31）+ 源码编译 glib 2.72.4 / libsoup 3.2.3 /
**WebKitGTK 2.44.4**，全部在 CI 完成（`build-image.yml`，双架构原生 runner），
**无需本地 Docker**。

> 旧 README 写的 2.40.6 已过时；以 `docker/tauri2-webkit41.Dockerfile` 的
> 2.44.4 为准。首次构建约 2~3 小时/架构，仅需一次，之后复用 ghcr 镜像。

## Release 文案模板

所有应用共用一套 Release 结构和上游更新记录：

- `release/notes-template.md`：兼容说明、上游 Release Notes、安装、差异、产物、校验；
- `release/apps/<id>/{install,diff,check}.md`：应用自己的说明，支持 `{{VERSION}}`；
- `scripts/render-release-notes.py`：渲染模板并附加 provenance 注释。

Tauri、Wails、Electrobun 的 release job 都会先 `gh api` 拉取上游 Release 的
`body`，再渲染同一模板，标题统一为 `<应用> <版本> (Kylin V10 / glibc 2.31)`。
新增应用只需补 `release/apps/<id>/` 三个 Markdown 文件。

## 质量闸门

- 预检：容器 glibc ≤ 2.31（超出直接 fail）
- Tauri 路径：预检 `webkit2gtk-4.1` ≥ 2.40
- 后检：主程序/捆绑 `.so` 的 GLIBC 符号需求 ≤ 2.31
- ThinkRail：替换后的 wrapper 校验官方 173 个导出符号、无 4.1-only 符号、
  `NEEDED` 为 `libwebkit2gtk-4.0.so.37` + `libsoup-2.4.so.1`、GLIBC/GLIBCXX 上限
- 产物附 `.sha256`；Release tag 带 app 前缀，避免多软件重名互覆

## 供其他仓库直接调用

仅适用于 Tauri/AppImage：

```yaml
jobs:
  build:
    uses: sondertara/xc-compatible-builds/.github/workflows/reusable-appimage.yml@main
    with:
      app_id: dbx
      upstream_repo: t8y2/dbx
      container_image: ghcr.io/sondertara/xc-compat-tauri2:amd64
      container_image_arm64: ghcr.io/sondertara/xc-compat-tauri2:aarch64
    secrets: inherit
```

## 已知边界

- Tauri 路径依赖定制镜像；镜像首次源码编译约 2~3h/架构
- Go/Wails 路径直接用 `ubuntu:20.04` + `libwebkit2gtk-4.0-dev`，无需定制镜像
- Electrobun 路径只替换 `libNativeWrapper.so`；上游自动更新会覆盖它
- 三条路径都需要在麒麟实机冒烟一次；GLIBC 扫描不能替代运行时验证
- 自动巡检使用 GitHub `schedule`；公开仓库 60 天无活动会被禁用，需要重新 Enable

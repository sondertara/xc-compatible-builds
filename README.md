# xc-compatible-builds — 麒麟 V10 兼容构建仓库

> 目标：让官方基于新系统构建的桌面软件（AppImage）能在**麒麟桌面版 V10（glibc 2.31）**上运行。
> 原理：在 glibc 2.31 基底（debian:bullseye）容器内重新编译，产物向下精确匹配。

## 工作流一览

| 工作流 | 作用 | 触发 |
|--------|------|------|
| **Build Compat Image** | 构建定制镜像（bullseye + 源码编译 webkit2gtk-2.40.6/4.1 API），推 ghcr | 手动 / Dockerfile 变更 |
| **App Build** | 单软件兼容构建（读 `configs/<id>.yml`） | 手动 / 被批量调度触发 |
| **Compat Build — All Apps** | 遍历 `configs/apps.yml`，检查上游新版本并逐个触发 App Build | 手动 / 每周一 02:00 UTC |

## ⚠️ 为什么 Tauri 2 应用需要定制镜像（重要事实）

Tauri 2.x（wry）要求 `webkit2gtk-4.1`（WebKitGTK ≥ 2.40，libsoup3 API）。
**glibc 2.31 的所有公共 Debian/Ubuntu 源里都没有这个包**（已实测逐源确认）：

| 源 | webkit 最高版本 | API |
|----|----------------|-----|
| bullseye main | 2.38 | 仅 4.0 |
| bullseye-security | 2.38 安全回移 | 仅 4.0 |
| bullseye-updates / focal 同理 | — | 仅 4.0 |

> 网上可见的 bullseye webkit 2.50.x 属于付费 ELTS 源，公共镜像不分发。

因此定制镜像 = `debian:bullseye`（glibc 2.31）+ 源码编译 glib 2.72 / libsoup3 / WebKitGTK 2.40.6，
全部在 CI 完成（`build-image.yml`，双架构原生 runner），**无需本地 Docker**。
首次构建约 2~3 小时/架构，仅需一次，之后 App Build 直接复用 ghcr 缓存镜像。

## 快速开始（dbx 为例）

1. **跑一次镜像构建**：Actions → `Build Compat Image` → Run workflow（等 2~3h）
2. **触发软件构建**：Actions → `App Build` → Run workflow → `app_id=dbx`，version 留空
3. 产物出现在本仓库 Release（tag 如 `dbx-v0.5.91`），命名与官方一致（`DBX_0.5.91_amd64.AppImage`）
4. 麒麟实机验证：`chmod +x DBX_*.AppImage && ./DBX_*.AppImage`

## 新增一个软件（2 步）

1. `cp configs/template.yml configs/myapp.yml`，按注释填写
2. 在 `configs/apps.yml` 注册（`auto_track: true` 参与每周自动跟踪）

## 质量闸门（已内置）

- 预检：容器 glibc ≤ 2.31（超出直接 fail，防止产物在麒麟起不来）
- 预检：webkit2gtk-4.1 ≥ 2.40（Tauri 2 最低要求；缺失给出修复指引）
- 后检：主程序 + 捆绑 `.so` 全部 GLIBC 符号需求 ≤ 2.31，超出直接 fail（不是 warning）
- 产物附 `.sha256`；Release tag 带 app 前缀（`<app>-<上游tag>`）避免多软件重名互覆

## 供其他仓库直接调用

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

- webkit 镜像首次源码编译可能因依赖版本差异需微调（Dockerfile 已关闭视频以外的大部分可选组件；报错把日志发回即可）
- AppImage 捆绑 webkit/gtk 运行库，目标机只需 X11（麒麟 UKUI 默认具备）
- GLIBC 符号扫描覆盖绝大多数场景，最稳妥仍在麒麟实机冒烟一次

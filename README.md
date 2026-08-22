# xc-compatible-builds — 麒麟 V10 兼容构建仓库

> 目标：让官方基于新系统构建的桌面软件（AppImage）能在**麒麟桌面版 V10（glibc 2.31）**上运行。
> 原理：在 **Debian 11 bullseye（glibc 同为 2.31）** 容器内重新编译，产物向下精确匹配。

## 为什么是 debian:bullseye（方案核心）

| 硬指标 | bullseye | 说明 |
|--------|----------|------|
| glibc | **2.31** | 与麒麟桌面 V10 完全一致 |
| libwebkit2gtk-4.1-dev | **2.50.x**（Debian LTS 持续更新） | Tauri 2 / wry 要求 ≥ 2.40 ✓ |
| 镜像可用性 | Docker Hub 公开镜像 | 无需自制镜像、无需源码编译 WebKit |

> 曾评估过的替代路线：`ubuntu:20.04` 同为 glibc 2.31，但其源内 WebKitGTK 最高 2.38
> 且仅有 4.0 API（Tauri 2 需要 4.1），必须源码编译 WebKitGTK 2.40+（1~2 小时、易碎），
> 已弃用。bullseye 的 LTS 源直接提供 4.1 API 的 2.50 版本，apt 一步到位。

构建前置自检（已内置到工作流）：
- 容器 glibc ≤ 2.31（超过即 fail，防止产物在麒麟上起不来）
- `webkit2gtk-4.1` 存在且 ≥ 2.40（wry 最低要求）
- 产物主程序及捆绑 `.so` 的 GLIBC 符号需求 ≤ 2.31（超出即 fail，不是 warning）

## 目录结构

```
.github/workflows/
├── reusable-appimage.yml   # 核心可复用构建逻辑（双架构、GLIBC 校验、Release 发布）
├── app-build.yml           # 单软件通用入口（手动触发 / 被批量调度触发）
└── compat-build.yml        # 批量调度（每周一 02:00 UTC 检查各软件上游新版本）
configs/
├── apps.yml                # 注册表（参与定时批量构建的软件列表）
├── dbx.yml                 # dbx（t8y2/dbx）配置
└── template.yml            # 新软件模板
```

> 批量任务通过 `gh workflow run` 逐个触发 App Build，而非 matrix 直调 reusable
> workflow —— GitHub Actions 不允许 `uses:` 的 job 定义 matrix。

## 新增一个软件（2 步）

1. `cp configs/template.yml configs/myapp.yml`，按注释填写
2. 在 `configs/apps.yml` 注册：

```yaml
apps:
  - id: dbx
  - id: myapp        # 新增
    auto_track: true # 是否参与定时自动跟踪
```

## Release 规则

- 产物发布到**本仓库**的 Release，tag 格式 `<app-id>-<上游tag>`（如 `dbx-v0.5.91`），
  避免不同软件上游 tag 重名互相覆盖
- 每个产物附 `.sha256` 校验
- 产物命名与官方一致（如 `DBX_0.5.91_amd64.AppImage`）

## 供其他仓库直接调用

```yaml
jobs:
  build:
    uses: sondertara/xc-compatible-builds/.github/workflows/reusable-appimage.yml@main
    with:
      app_id: dbx
      upstream_repo: t8y2/dbx
    secrets: inherit
```

## 麒麟实机验证建议

1. 下载 AppImage → `chmod +x DBX_*.AppImage` → 终端启动
2. 确认窗口渲染、数据库连接、中文输入（fcitx）正常
3. 若提示缺库，把 `ldd` / 报错输出发回，可针对性在 build_deps 补充

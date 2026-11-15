# 应用专属资产规范（`apps/`）

`configs/<id>.yml` 只保存声明式元数据（上游仓库、构建类型、产物命名、入口 `workflow:`）；仅服务于单个应用的源码补丁、适配器与构建脚本统一归档在 `apps/<id>/` 下：

```text
apps/<id>/
├── README.md              # 该应用的适配方案、补丁点与升级注意事项
├── patches/*.patch        # 绑定上游具体版本的源码补丁（可选）
├── third_party/**         # 随包头文件或固定版本构建输入（可选）
└── scripts/**             # 应用专属前端构建、兼容性转换与静态门禁脚本（可选）
```

## 维护约定

1. **配置与逻辑分离**：编译参数、源码补丁与兼容性校验脚本放在应用工作流或 `apps/<id>/scripts/` 中，保持 `configs/<id>.yml` 纯净可读。
2. **补丁显式绑定版本与失败阻断**：`.patch` 文件名需包含目标版本号（如 `electrobun-2.0.5-webkit40.patch`），脚本补丁需在目标片段漂移时直接报错退出（Fail-Closed），避免上游升级后静默漏打补丁。
3. **公共能力下沉至 `shared/`**：禁止在仓库根目录堆放零散 `patches/` 或 `third_party/`；真正跨应用复用的宿主底座（如 [`shared/mygo-sidecar/`](../shared/mygo-sidecar/README.md)）放入 `shared/` 并保持与具体业务应用解耦。
4. **工作流统一调度**：GitHub Actions 入口文件位于 `.github/workflows/`，通过 `configs/<id>.yml` 的 `workflow:` 字段与 `upstream-watch.yml` 自动巡检对接。

## 已接入应用一览

| 应用 ID | 构建类型 (`build_type`) | 入口工作流 | 专属资产目录内容 |
|---------|-------------------------|------------|------------------|
| `dbx` | `mygo-sidecar` | `dbx-compat.yml` | [`apps/dbx/`](dbx/README.md)：`adapter.go`、`cmd/dbx-desktop/`、`assets/`、`installer.sh`、`scripts/` |
| `gonavi` | `go-wails` | `gonavi-build.yml` | 无额外补丁目录（工作流内清理 `webkit2_41` 标签直编） |
| `thinkrail` | `prebuilt-wrapper` | `thinkrail-compat.yml` | [`apps/thinkrail/`](thinkrail/README.md)：`electrobun-2.0.5-webkit40.patch`、`patch-web-assets.py`、`third_party/`、`installer.sh` |

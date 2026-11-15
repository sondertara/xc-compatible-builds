# DBX（MyGO Sidecar）— 麒麟 V10 兼容适配说明

DBX 采用 **纯 Go 桌面外壳（[`shared/mygo-sidecar`](../../shared/mygo-sidecar/README.md)）+ 上游官方纯静态 `musl` 后端 `dbx-web-bin`** 方案，无需编译耗时的 `webkit2gtk-4.1` 定制镜像即可在麒麟桌面版 V10（`glibc 2.31`、`WebKitGTK 4.0 / 2.38.x`）原生运行：

- **零 C 工具链 / 零 GLIBC 版本锁**：`dbx-desktop` 以 `CGO_ENABLED=0` 纯静态编译，运行时通过 `purego` 动态加载宿主系统的 GTK3 与 `libwebkit2gtk-4.0.so.37`（或 `4.1`），天然支持系统中文输入法与原生文件保存对话框。
- **官方纯静态后端原地执行**：`libexec/dbx-web-bin` 直接取自上游 `DBX_<ver>_<x64|arm64>-browser-static.tar.gz`，无动态链接器与外部 `.so` 依赖；直接原地 `exec` 启动并绑定进程组回收，不释放到 `/tmp`（规避 `/tmp noexec` 限制）。
- **固定虚拟源 + 全随机端口**：通过 WebKit 网络代理将固定无端口源 `http://com-dbx-desktop.localhost/` 转发至本地随机端口网关与 `dbx-web-bin`，兼顾零端口冲突、跨重启完整保留 WebKit 原生 `localStorage` / `IndexedDB`（SQL 标签页草稿、编辑器与布局状态）及 `crypto.subtle` 安全上下文。

## 目录结构与职责

- `adapter.go` + `cmd/dbx-desktop/main.go`：
  - 动态分配空闲回环端口并写入 `DBX_PORT`（`dbx-web` 需复用同一端口配置初始化 HTTP 与内部组件，不可传 `0`）。
  - 每次启动生成 32 字节高熵随机 `DBX_PASSWORD`（保持 `DBX_DISABLE_PASSWORD=0`、`DBX_WEB_CAPTCHA=off` 以保护本机回环接口并维持 `WebMcpRuntime` 可用），自动完成 `/api/auth/login` 与 `/api/auth/check` 握手后将 `dbx_session` HttpOnly Cookie 预注入 WebKitGTK。
  - 默认将后端数据库与配置隔离存储于 `${XDG_DATA_HOME:-~/.local/share}/com.dbx.desktop/data`。
  - 自动探测并挂载随包前端目录 `share/dbx/dist`（`DBX_STATIC_DIR`），覆盖 `dbx-web-bin` 内嵌前端资源。
  - 实现 `ValidateUISmoke`，在 CI `-ui-smoke` 阶段强制断言页面运行于 `http://com-dbx-desktop.localhost/` 且未落入密码登录页。
- `scripts/build-web-dist.sh` + `scripts/patch-web-source.py` + `scripts/patch-web-assets.py`：
  - 浅克隆上游 `t8y2/dbx@<tag>`，在编译前执行幂等且 Fail-Closed 的桌面化源码补丁（移除单机桌面端无意义的顶栏右侧“退出登录”按钮及设置页 Web“安全/密码/验证码”页签）。
  - 以 `build.target = ["es2020", "safari15.4"]` 与 `cssTarget = "safari15.4"` 编译前端产物（无需编译 Rust）。
  - 对编译后 CSS 执行确定性降级：注入多主题 `--dbx-compat-<token>-rgb` 变量，将裸 `oklch()`、`color-mix()` 与 `@media` 范围语法转换为 WebKitGTK 2.38 原生支持的 `rgba()` / `#rrggbb` / `min-width` 语法（保留 `@supports` 渐进增强），并静默桌面端“浏览器版本过旧”提示条。
- `scripts/verify-web-compat.py`：
  - 扫描静态目录（`--dir`）或实机服务响应（`--bin` / `--url`）中的全部 JS/CSS 资产，对 JSC 2.38 解析期阻断语法（正则 `(?<=` / `(?<!`、`static {}` 块）及裸现代 CSS（`--strict-css`）执行硬门禁检查。
- `installer.sh`：
  - XDG 标准自解压安装器，部署 `~/.local/share/com.dbx.desktop/app`、`~/.local/share/applications/DBX.desktop` 与一键卸载脚本 `~/.local/share/com.dbx.desktop/uninstall`。

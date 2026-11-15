# `shared/mygo-sidecar` — 通用纯 Go WebKitGTK 桌面外壳底座

`shared/mygo-sidecar` 为自带独立 Web 服务端（特别是纯静态二进制后端）的应用提供零 C 依赖（`CGO_ENABLED=0`）的 Linux 桌面宿主框架，直接适配银河麒麟 V10 等搭载 `GTK3 + WebKitGTK 4.0 (2.38.x)` 的系统环境。

## 核心能力与职责边界

本包保持**与具体业务应用完全解耦**（零应用硬编码），任何应用只需实现 `sidecar.Adapter` 接口即可接入：

- **子进程生命周期托管（`proc.go`）**：独立进程组（`Setpgid`）+ Linux 父进程退出信号（`Pdeathsig: SIGTERM`）+ 优雅超时回收，杜绝孤儿后端进程残留。
- **单实例桌面锁与系统托盘（`runner.go`）**：正常桌面模式下自动启用单实例锁，二次点击应用图标自动唤醒并聚焦已有主窗口；内置原生托盘菜单与文件下载保存对话框（`mygo.Dialog.Save`）。
- **固定 `.localhost` 虚拟源与引擎级代理（`gateway.go`、`webkit_proxy_linux.go`）**：
  - 后端与进程内 `LocalGateway` 均使用 `127.0.0.1:0` 随机空闲端口，彻底消除固定端口冲突；
  - 通过 `purego` 调用 `webkit_website_data_manager_set_network_proxy_settings`（兼容 `4.1` 与 `4.0`），将 `http://<app-id>.localhost/` 的 HTTP 与 `ws://` WebSocket 流量透明转发至当次随机端口后端；
  - WebKit 看到的 Origin 恒定不变且属于 RFC 6761 安全上下文（`isSecureContext === true`），无需任何 JS Hack 即可跨重启 100% 保留原生 `localStorage`、`IndexedDB`、`Cookie` 与 `window.crypto.subtle`。
- **WebKitGTK 2.38 运行时垫片（`polyfills.go`）**：通过 `PreloadScript` 为 JavaScriptCore 2.38 补齐 ES2023/ES2024 标准方法（`Array/TypedArray` 的 `toSorted`/`toReversed`/`toSpliced`/`with`、`Object.groupBy`、`Map.groupBy`、`Promise.withResolvers`）。
- **自动化冒烟与实机截图探针（`ui_smoke.go`）**：内置 `-smoke-test`（无头握手探针）与 `-ui-smoke <dir>`（配合 `xvfb-run` 采集真实 WebKitGTK 渲染 PNG 截图、DOM 规模及运行时 JS 错误报告，并支持应用自定义 `UISmokeValidator` 断言）。

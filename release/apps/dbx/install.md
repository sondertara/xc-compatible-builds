**推荐：安装到当前用户标准目录**

```bash
tar -xzf DBX-{{VERSION}}-linux-<arch>-installer.tar.gz
./installer
```

安装后会写入：

- `~/.local/share/com.dbx.desktop/app`（含 `bin/dbx-desktop`、`libexec/dbx-web-bin` 与经 WebKitGTK 4.0 兼容构建/补丁的前端资源 `share/dbx/dist`）
- `~/.local/share/applications/DBX.desktop`
- `~/.local/share/com.dbx.desktop/uninstall`

数据目录默认隔离保存在 `~/.local/share/com.dbx.desktop/data`。

**便携运行（不写桌面项）**

```bash
tar -xzf DBX-{{VERSION}}-linux-<arch>-kylin.tar.gz
./DBX/bin/dbx-desktop
```

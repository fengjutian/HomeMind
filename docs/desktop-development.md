# 桌面端本地运行

HomeMind 桌面端位于 `desktop/`，使用 Wails v3 构建。桌面端源码开发时，推荐分别启动 HomeMind 后端和 Wails 桌面壳。

## 前置依赖

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- Go 1.25+
- Wails v3 `v3.0.0-beta.13`

在 PowerShell 中安装 Wails：

```powershell
go install github.com/wailsapp/wails/v3/cmd/wails3@v3.0.0-beta.13
$env:Path += ";$(go env GOPATH)\bin"
```

确认命令可用：

```powershell
go version
wails3 version
```

## 启动桌面端

### 1. 启动 HomeMind 后端

在仓库根目录执行：

```powershell
uv sync
uv run octop run --reload --host 127.0.0.1 --port 8088
```

保持该终端运行。

### 2. 启动 Wails 桌面壳

新开一个 PowerShell 窗口，执行：

```powershell
cd D:\github\HomeMind\desktop\src
$env:OCTOP_DESKTOP_URL = "http://127.0.0.1:8088"
wails3 dev
```

Wails 会打开桌面窗口并加载本地 HomeMind 服务。

如果仓库不在 `D:\github\HomeMind`，请将 `cd` 后的路径替换为实际仓库路径。

## 仅运行 Web 控制台

如果不需要桌面壳，可在仓库根目录执行：

```powershell
uv run octop run
```

然后访问：

```text
http://127.0.0.1:8088
```

## 注意事项

- 源码开发时应设置 `OCTOP_DESKTOP_URL`，让桌面壳连接已启动的本地后端。
- 未设置 `OCTOP_DESKTOP_URL` 时，桌面程序会尝试使用 `~/.octop/portable/` 中的绿色运行时，或使用随正式桌面安装包附带的运行时。本地源码环境通常没有这些文件。
- 默认端口是 `8088`。如果修改后端端口，必须同步修改 `OCTOP_DESKTOP_URL`。
- 如果 `wails3` 无法识别，请重新打开终端，或确认 `$(go env GOPATH)\bin` 已加入 `PATH`。

桌面端构建、打包和绿色运行时的详细说明参见 [`desktop/README.md`](../desktop/README.md)。

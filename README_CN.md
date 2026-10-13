<p align="center"><img src="docs/assets/readme-banner-zh.png" alt="HomeMind" width="720" /></p>

<p align="center">
  <strong>开源、自托管、面向家庭的多用户 AI 中枢。</strong><br />
  让每位家人拥有自己的助手，让知识、设备与自动化在清晰权限下协同工作。
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" /></a>
  <a href="https://github.com/fengjutian/HomeMind/releases"><img alt="Release" src="https://img.shields.io/github/v/release/fengjutian/HomeMind?include_prereleases" /></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-2f7d5b" /></a>
  <a href="https://github.com/fengjutian/HomeMind/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/fengjutian/HomeMind?style=social" /></a>
</p>

<p align="center">
  <a href="README.md">English</a> · <b>中文</b> ·
  <a href="docs/homemind-user-guide.md">使用指南</a> ·
  <a href="docs/api.md">API</a> ·
  <a href="CONTRIBUTING.md">参与贡献</a>
</p>

---

## HomeMind 是什么

HomeMind 是一个运行在你自己设备上的家庭 AI 平台。它把多用户账号、专业 AI 助手、家庭知识、文件、日历、任务、连接器和智能设备组织在同一个可控空间中。

它不是一个全家共用、什么都能看到的聊天机器人。每位成员拥有独立账号、助手和私人上下文；只有明确放入家庭空间或被授权的资源才会共享。

HomeMind 构建在 [Octop](https://github.com/TencentCloud/Octop) 运行时之上：Octop 提供多 Agent、模型、工具、连接器、消息渠道和自动化基础能力；HomeMind 在其上增加家庭、成员、空间、资产、相册、日历、任务、记忆、设备和审批等家庭领域能力。

```text
网页 / 桌面端 / CLI / 消息平台
                │
                ▼
       身份、家庭与权限边界
                │
                ▼
         专家或专家团队
                │
      ┌─────────┼─────────┐
      ▼         ▼         ▼
    技能      知识库     连接器 / 设备
      └─────────┼─────────┘
                ▼
       回复、文件、任务或审批
```

## 核心能力

| | 能力 | 说明 |
|---|---|---|
| 👨‍👩‍👧‍👦 | 家庭原生 | 家庭、成员、关系、共享空间和权限是核心数据模型 |
| 🤖 | 多专家协作 | 从内置模板创建专业助手，也可组成团队分工完成复杂任务 |
| 🧠 | 可控记忆 | 私人记忆与家庭记忆分层保存，候选记忆可由成员审核 |
| 📚 | 知识与文件 | 家庭文件、照片、相册、知识库和专家工作区各司其职 |
| 🔌 | 开放连接 | 通过连接器、MCP、插件、技能、HTTP API 和 Bridge 扩展 |
| 🏠 | 智能家居 | 通过 Home Assistant 接入设备，高风险操作进入审批流程 |
| ⏰ | 主动自动化 | 家庭任务、日历、提醒与专家定时任务使用统一服务器时区 |
| 🔒 | 自托管 | 默认 SQLite、无需外部队列；也支持 PostgreSQL 和远程工作区 |

## 可以用它做什么

- 为家人分别创建学习助手、生活管家、健康助手、编程专家或 PPT 演示专家。
- 用家庭知识库回答“只基于我们自己的资料”的问题。
- 从邮箱、文档、网盘、会议、Notion 等服务读取信息并生成结果。
- 整理家庭照片、文件、日历、提醒、任务和长期记忆。
- 通过 Home Assistant 查询设备状态，在审批后控制灯光、空调或窗帘。
- 让专家每天、每周或在指定时间主动汇总、提醒和执行任务。
- 从网页、桌面端、CLI、飞书、企业微信等入口访问同一组专家。
- 通过 API 或 MCP 将家庭能力接入自己的应用和自动化系统。

## 快速开始

### 环境

- Windows、macOS 或 Linux
- 安装脚本会通过 `uv` 创建隔离的 Python 3.12 环境
- 云端模型需要对应 API Key；本地模型可使用 Ollama

### 安装

macOS / Linux：

```bash
curl -fsSL https://finnie-1258344699.cos.ap-guangzhou.myqcloud.com/octop/install.sh | bash
```

Windows PowerShell：

```powershell
irm https://finnie-1258344699.cos.ap-guangzhou.myqcloud.com/octop/install.ps1 | iex
```

也可以从 [GitHub Releases](https://github.com/fengjutian/HomeMind/releases/latest) 下载桌面客户端。

### 初始化并运行

```bash
homemind init
homemind run
```

打开 <http://127.0.0.1:8088>，根据向导完成数据库、管理员和模型配置。

允许局域网访问：

```bash
homemind run --host 0.0.0.0 --port 8088
```

> 公网部署前请配置 HTTPS、防火墙和强密码，不要直接暴露未加密的服务。

### 从源码运行

```bash
git clone https://github.com/fengjutian/HomeMind.git
cd HomeMind
uv sync
uv run homemind run --reload
```

### Docker

```bash
docker compose -f docker/docker-compose.yml up -d
```

默认端口为 `8088`，数据卷应持久化 `~/.octop/`（容器示例中为 `/data/.octop`）。完整部署参数见 [配置参考](docs/configuration.md)。

## 第一次使用

建议从最小闭环开始：

1. 在 **管理 → 模型** 中配置并测试一个模型。
2. 创建家庭并邀请或绑定家庭成员。
3. 从 **专家 → 内置专家** 创建一个专家。
4. 在对话页完成一个不调用外部服务的简单任务。
5. 接入一个最常用的知识库或连接器。
6. 需要设备控制时，再连接 Home Assistant。
7. 手动验证稳定后，再启用定时任务与主动通知。
8. 完成配置后立即创建备份。

完整步骤、界面路径和排障方法见 **[HomeMind 项目使用与接入指南](docs/homemind-user-guide.md)**。

## 如何接入

### 模型

通过预设或 OpenAI 兼容接口接入云模型，也可以连接本地 Ollama。不同专家可以使用不同模型。

### 外部服务

内置连接器覆盖文档、会议、邮箱、网盘、知识库、任务、旅行、地图、法律和企业信息等场景。已有 MCP Server 可作为自定义连接器加入。

### 智能家居

```text
小米 / 华为 / Matter / 其他设备
                │
                ▼
         Home Assistant
                │
                ▼
    HomeMind 权限、事务与审批
```

HomeMind 不要求保存厂商账号密码。详细步骤和设备风险分级见 [智能家居接入指南](docs/smart-home-setup.md)。

### 外部项目

- HTTP API：`/api/*` 与 `/api/homemind/*`
- OpenAPI：`/api/openapi.json`
- Scalar 文档：启用后访问 `/api/docs`
- HomeMind MCP：`POST /api/homemind/mcp`
- IDE / 编码 Agent：`homemind acp --agent <agent-id>`

外部调用必须使用正式身份令牌；家庭作用域与权限由服务端校验，不应通过请求参数伪造用户身份。

## 系统结构

```text
dashboard/               React + TypeScript 控制台
website/                 HomeMind 官网
src/homemind/
  api/                   家庭 HTTP API
  infra/family/          家庭成员、设备、资产、记忆、日历与任务
  infra/db/              HomeMind 独立迁移与仓储
  device_runtime/        家庭设备端运行时
  skills/                家庭技能包
  tools/                 家庭 MCP 工具
src/octop/
  api/                   通用 HTTP 接口
  infra/agents/          专家、团队、模型、记忆与工作区
  infra/connectors/      Connector、OAuth 与 MCP 网关
  infra/gateway/         Web / IM 消息处理
  cli/                   命令行管理
tests/                   单元与集成测试
```

HomeMind 与 Octop 运行时在同一 FastAPI 进程中组合，不需要 Redis 或外部消息队列。默认控制面使用 SQLite，也支持 PostgreSQL。

## 数据与安全

默认数据目录是 `~/.octop/`：

```text
~/.octop/
├── config.json
├── octop.db
├── secrets/
├── agents/
├── plugins/
├── logs/
└── homemind/exports/
```

- 每位成员使用独立账号；家庭共享不等于公开全部私人对话。
- 凭据加密保存，专家只使用明确授权的连接器和工具。
- 门锁、摄像头和安防面板等高风险设备默认只读。
- 敏感写操作可进入工具审批或家庭事务审批。
- 公网访问应启用 TLS，并限制 CORS、反向代理和防火墙规则。
- 升级前执行 `homemind backup`，并把备份保存在另一位置。

安全问题请参阅 [SECURITY.md](SECURITY.md)，配置项参阅 [配置参考](docs/configuration.md)。

## 开发

```bash
uv sync
make install-hooks
make all
```

前端开发：

```bash
cd dashboard
npm install
npm run dev
```

常用检查：

```bash
make format-all
make lint
make typecheck
uv run pytest -m "not live"
cd dashboard && npx tsc -b
```

提交前的默认质量门槛是 `make all` 通过。不要直接编辑 `src/octop/dashboard/`，它是由 `dashboard/` 构建生成的产物。

## 文档

| 文档 | 内容 |
|------|------|
| [HomeMind 使用与接入指南](docs/homemind-user-guide.md) | 安装、家庭、专家、模型、连接器、设备、API 和排障 |
| [Octop 基础用户指南](docs/user-guide.md) | 通用运行时与控制台功能 |
| [配置参考](docs/configuration.md) | `config.json` 与环境变量 |
| [CLI 参考](docs/cli.md) | 命令行完整说明 |
| [API 参考](docs/api.md) | HTTP API |
| [智能家居接入](docs/smart-home-setup.md) | Home Assistant 与设备安全规则 |
| [专家团队](docs/expert-teams.md) | 多专家协作 |
| [ACP](docs/acp.md) | IDE 与编码 Agent 集成 |
| [架构](docs/architecture.md) | 系统结构与边界 |

## 路线图

- 完善家庭记忆、照片、文件、日历和任务之间的自动关联。
- 扩展经过安全评审的智能家居设备类型与审批策略。
- 增加更多可审计、最小权限的连接器和家庭场景模板。
- 改善桌面端、移动端与家庭设备 Runtime 的协同体验。
- 让自动化从“定时执行”发展为可解释、可撤销的情境协作。

路线图会随实际使用反馈调整，不构成版本承诺。

## 参与贡献

欢迎提交 Issue、文档改进和 Pull Request。请先阅读 [CONTRIBUTING.md](CONTRIBUTING.md)，并遵循仓库中的 `AGENTS.md`、模块边界与测试要求。

## 相关项目

- [Octop](https://github.com/TencentCloud/Octop) — 多用户、多 Agent 自托管运行时
- [Octop Harness](https://github.com/TencentCloud/octop-harness) — Agent 执行与工具运行时
- [Octop Gateway](https://github.com/TencentCloud/octop-gateway) — 多平台消息网关
- [Octop Memory](https://github.com/TencentCloud/octop-memory) — 分层记忆系统
- [Octop Browser](https://github.com/TencentCloud/octop-browser) — 浏览器自动化

## 许可证

本项目采用 [MIT License](LICENSE)。

<p align="center"><img src="website/favicon.svg" alt="HomeMind logo" width="72" /></p>

<h1 align="center">HomeMind</h1>

<p align="center">
  <strong>An open-source, self-hosted, multi-user AI hub built for families.</strong><br />
  Give every family member a personal assistant while keeping knowledge, devices, and automation under clear permissions.
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white" /></a>
  <a href="https://github.com/fengjutian/HomeMind/releases"><img alt="Release" src="https://img.shields.io/github/v/release/fengjutian/HomeMind?include_prereleases" /></a>
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-2f7d5b" /></a>
  <a href="https://github.com/fengjutian/HomeMind/stargazers"><img alt="GitHub stars" src="https://img.shields.io/github/stars/fengjutian/HomeMind?style=social" /></a>
</p>

<p align="center">
  <b>English</b> · <a href="README_CN.md">中文</a> ·
  <a href="docs/homemind-user-guide.md">User guide (Chinese)</a> ·
  <a href="docs/api.md">API</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

---

## What is HomeMind?

HomeMind is a family AI platform that runs on infrastructure you control. It brings multi-user accounts, specialized AI agents, household knowledge, files, calendars, tasks, connectors, and smart-home devices into one governed space.

It is not a single shared chatbot where everyone sees everything. Each member has a separate account, assistants, and private context. Information becomes shared only when it is placed in a family space or explicitly authorized.

HomeMind is built on the [Octop](https://github.com/TencentCloud/Octop) runtime. Octop supplies the multi-agent runtime, model routing, tools, connectors, messaging channels, and automation. HomeMind adds family domains such as households, members, spaces, assets, albums, calendars, tasks, memories, devices, and approvals.

```text
Web / Desktop / CLI / Messaging
                │
                ▼
       Identity and family scope
                │
                ▼
          Agent or agent team
                │
      ┌─────────┼─────────┐
      ▼         ▼         ▼
   Skills   Knowledge   Connectors / Devices
      └─────────┼─────────┘
                ▼
     Replies, files, tasks, or approvals
```

## Highlights

| | Capability | What it means |
|---|---|---|
| 👨‍👩‍👧‍👦 | Family-native | Households, members, relationships, spaces, and permissions are first-class concepts |
| 🤖 | Multi-agent | Create specialists from templates or organize them into collaborative teams |
| 🧠 | Controlled memory | Keep personal and family memory separate, with reviewable memory candidates |
| 📚 | Knowledge and files | Family files, photos, albums, knowledge bases, and agent workspaces have distinct roles |
| 🔌 | Open integration | Extend through connectors, MCP, plugins, skills, HTTP APIs, and Bridge |
| 🏠 | Smart home | Connect devices through Home Assistant and route risky actions through approval |
| ⏰ | Proactive automation | Combine family tasks, calendars, reminders, and scheduled agent jobs |
| 🔒 | Self-hosted | SQLite by default, no external queue required; PostgreSQL and remote workspaces supported |

## What can it do?

- Give each family member a study companion, household assistant, health helper, coding expert, or presentation specialist.
- Answer questions grounded in private family knowledge bases.
- Read and act on email, documents, drives, meetings, Notion, and other connected services.
- Organize family photos, files, calendars, reminders, tasks, and long-term memories.
- Read smart-device state and, after policy checks, control lights, climate, or covers through Home Assistant.
- Run daily or weekly summaries and reminders with scheduled agent jobs.
- Reach the same agents from the web, desktop, CLI, and supported messaging platforms.
- Integrate family capabilities into other applications through HTTP or MCP.

## Quick start

### Requirements

- Windows, macOS, or Linux
- The installer creates an isolated Python 3.12 environment through `uv`
- Cloud models require provider credentials; local models can use Ollama

### Install

macOS / Linux:

```bash
curl -fsSL https://finnie-1258344699.cos.ap-guangzhou.myqcloud.com/octop/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://finnie-1258344699.cos.ap-guangzhou.myqcloud.com/octop/install.ps1 | iex
```

Desktop packages are available from [GitHub Releases](https://github.com/fengjutian/HomeMind/releases/latest).

### Initialize and run

```bash
homemind init
homemind run
```

Open <http://127.0.0.1:8088> and complete the database, administrator, and model setup wizard.

For LAN access:

```bash
homemind run --host 0.0.0.0 --port 8088
```

> Configure TLS, firewall rules, and a strong password before exposing HomeMind to the public internet.

### Run from source

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

The default port is `8088`. Persist `~/.octop/` (mapped to `/data/.octop` in the container examples). See the [configuration reference](docs/configuration.md) for deployment settings.

## First-use path

Start with one small, verifiable workflow:

1. Configure and test one model under **Admin → Models**.
2. Create a household and invite or bind its members.
3. Create one agent from **Experts → Built-in Experts**.
4. Complete a simple conversation without external services.
5. Add one useful knowledge base or connector.
6. Connect Home Assistant only if device access is needed.
7. Enable schedules and proactive notifications after manual use is stable.
8. Create a backup after the initial setup.

The complete operational guide is available in Chinese: **[HomeMind usage and integration guide](docs/homemind-user-guide.md)**.

## Integration paths

### Models

Use built-in provider presets, any compatible API, or a local Ollama server. Different agents may use different models.

### External services

The connector catalog covers documents, meetings, email, cloud drives, knowledge, tasks, travel, maps, legal data, and company information. Existing MCP servers can be added as custom connectors.

### Smart home

```text
Xiaomi / Huawei / Matter / other devices
                  │
                  ▼
           Home Assistant
                  │
                  ▼
      HomeMind policy and approvals
```

HomeMind does not need to store vendor account passwords. See the [smart-home setup guide](docs/smart-home-setup.md) for device mapping and risk rules.

### External applications

- HTTP API: `/api/*` and `/api/homemind/*`
- OpenAPI schema: `/api/openapi.json`
- Scalar docs: `/api/docs` when enabled
- HomeMind MCP endpoint: `POST /api/homemind/mcp`
- IDE and coding-agent integration: `homemind acp --agent <agent-id>`

External clients authenticate with a real user token. Family scope and permissions are enforced on the server and must never be supplied as trusted user arguments.

## Architecture

```text
dashboard/               React + TypeScript dashboard
website/                 HomeMind product website
src/homemind/
  api/                   Family HTTP API
  infra/family/          Members, devices, assets, memory, calendar, tasks
  infra/db/              HomeMind migrations and repositories
  device_runtime/        Runtime for paired family devices
  skills/                Family skill packages
  tools/                 Family MCP tools
src/octop/
  api/                   Generic HTTP surface
  infra/agents/          Agents, teams, models, memory, workspaces
  infra/connectors/      Connectors, OAuth, and MCP gateway
  infra/gateway/         Web and IM message processing
  cli/                   Command-line administration
tests/                   Unit and integration tests
```

HomeMind and the Octop runtime are composed into one FastAPI process. Redis and external message queues are not required. The control plane uses SQLite by default and can use PostgreSQL.

## Data and security

Runtime data defaults to `~/.octop/`:

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

- Give every member a separate account; household membership does not expose all private chats.
- Credentials are encrypted, and agents receive only explicitly enabled connectors and tools.
- Locks, cameras, and security panels are read-only by default.
- Sensitive writes can require tool approval or a family transaction approval.
- Public deployments should use TLS, restricted CORS, a reverse proxy, and firewall rules.
- Run `homemind backup` before upgrades and keep a copy outside the runtime host.

Read [SECURITY.md](SECURITY.md) and the [configuration reference](docs/configuration.md) before a production deployment.

## Development

Required runtimes:

- Python 3.12
- Node.js 20 (npm 10)
- [uv](https://docs.astral.sh/uv/)

Install the locked backend and frontend dependencies:

```bash
uv sync
cd dashboard && npm ci && cd ..
```

Start the development services with the cross-platform launcher (macOS, Linux, or Windows):

```bash
python3 start-dev.py              # backend + frontend + desktop
python3 start-dev.py web          # backend + frontend
python3 start-dev.py backend      # backend only
python3 start-dev.py frontend     # frontend only
python3 start-dev.py desktop      # backend + frontend + desktop
```

The `desktop` and `all` targets additionally require Wails v3 (`wails3`) in `PATH`. The launcher reuses a backend already listening on port `8088`, chooses an available frontend port starting at `5173`, and stops the processes it started when you press Ctrl+C.

Install the Git hooks and run the full project checks:

```bash
make install-hooks
make all
```

Frontend development:

```bash
cd dashboard
npm ci
npm run dev
```

Useful checks:

```bash
make format-all
make lint
make typecheck
uv run pytest -m "not live"
cd dashboard && npx tsc -b
```

`make all` is the default shipping bar. Edit dashboard sources under `dashboard/`; do not edit the generated `src/octop/dashboard/` artifact directly.

## Documentation

| Document | Scope |
|----------|-------|
| [HomeMind usage and integration guide](docs/homemind-user-guide.md) | Setup, families, agents, models, connectors, devices, API, troubleshooting |
| [Octop user guide](docs/user-guide.md) | Generic runtime and dashboard features |
| [Configuration](docs/configuration.md) | `config.json` and environment variables |
| [CLI reference](docs/cli.md) | Command-line administration |
| [API reference](docs/api.md) | HTTP APIs |
| [Smart-home setup](docs/smart-home-setup.md) | Home Assistant and device safety |
| [Agent teams](docs/expert-teams.md) | Multi-agent collaboration |
| [ACP](docs/acp.md) | IDE and coding-agent integration |
| [Architecture](docs/architecture.md) | Runtime structure and boundaries |

## Roadmap

- Deepen links between family memory, photos, files, calendars, and tasks.
- Expand safely reviewed smart-home capabilities and approval policies.
- Add more auditable, least-privilege connectors and household templates.
- Improve coordination between desktop, mobile, and paired-device runtimes.
- Evolve schedules into explainable and reversible context-aware assistance.

The roadmap changes with real-world feedback and is not a release commitment.

## Contributing

Issues, documentation improvements, and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) and follow the repository's `AGENTS.md`, module boundaries, and verification requirements.

## Related projects

- [Octop](https://github.com/TencentCloud/Octop) — self-hosted multi-user, multi-agent runtime
- [Octop Harness](https://github.com/TencentCloud/octop-harness) — agent execution and tools
- [Octop Gateway](https://github.com/TencentCloud/octop-gateway) — multi-platform message gateway
- [Octop Memory](https://github.com/TencentCloud/octop-memory) — layered memory
- [Octop Browser](https://github.com/TencentCloud/octop-browser) — browser automation

## License

HomeMind is released under the [MIT License](LICENSE).

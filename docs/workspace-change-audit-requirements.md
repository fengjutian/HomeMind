# AI 对话与工作区文件变更审计需求

## 1. 文档信息

| 项目 | 内容 |
|---|---|
| 状态 | 草案 |
| 适用产品 | HomeMind / Octop |
| 目标版本 | 待排期 |
| 文档范围 | AI 对话、工具调用及 Agent 工作区文件变更审计 |
| 非目标 | 本文不规定具体前端视觉稿，不替代备份与灾难恢复方案 |

## 2. 背景

系统目前具备以下能力：

- 对话正文保存在 checkpoint／对话历史中，可以按用户、Agent 和 thread 查询，并可继续原会话。
- trajectory 默认记录近期 AI 执行轨迹，包括工具名称、工具参数及工具结果。
- `usage_log` 记录模型和 token 用量。
- `audit_log` 记录登录、用户管理、Agent 管理及部分系统操作。

现有能力适合恢复对话和排查近期执行过程，但不能完整回答以下审计问题：

- 谁在什么时间要求哪个 Agent 执行了什么任务？
- AI 调用了哪个工具，调用是否成功？
- 哪个动作新增、覆盖、移动或删除了哪些文件？
- 文件修改前后分别是什么状态，具体差异是什么？
- 一次文件变更属于哪个用户消息、AI 回合和工具调用？
- 审计记录是否被删除、截断或篡改？

因此需要在现有对话历史、trajectory 和管理审计之外，增加统一的 AI 对话与工作区文件变更审计能力。

## 3. 目标

### 3.1 核心目标

1. 建立从用户、会话、回合、工具调用到文件变更的完整关联链路。
2. 记录工作区文件新增、覆盖、移动、重命名和删除操作。
3. 支持查看文件修改前后哈希、元数据及文本差异。
4. 同时覆盖 AI 工具、Dashboard、HTTP API 和系统内部任务触发的工作区变更。
5. 提供按权限查询、筛选和导出审计记录的能力。
6. 审计保留策略独立于普通对话历史，避免删除 thread 时自动丢失合规记录。
7. 在控制存储成本和敏感信息泄露风险的前提下，支持长期追溯。

### 3.2 非目标

- 不将审计记录作为 Agent 的长期记忆或提示词上下文。
- 不保证仅依靠审计日志即可恢复整个工作区；完整恢复仍由备份系统负责。
- 不记录工作区之外任意主机文件的内容。
- 不默认永久保存所有文件全文。
- 不替代 Git；已使用 Git 的工作区仍可将 Git 作为更完整的版本管理工具。

## 4. 术语

| 术语 | 定义 |
|---|---|
| Thread | 一段可继续的用户与 AI 对话 |
| Turn | 一次用户输入及其触发的 AI 执行过程 |
| Tool call | AI 或系统发起的一次工具调用 |
| Workspace change | 对 Agent 工作区文件或目录产生状态变化的操作 |
| Audit event | 只追加的结构化审计事件 |
| Snapshot | 某一时点文件内容或可恢复内容的引用 |
| Diff | 修改前后内容的差异表示，文本文件优先使用 unified diff |
| Actor | 发起动作的用户、Agent、系统任务或管理员 |

## 5. 用户角色与权限

### 5.1 角色

- **普通用户**：查看自己拥有的 Agent 和 thread 的对话及文件审计。
- **Agent 所有者**：查看所属 Agent 的完整审计记录。
- **管理员／审计员**：按授权范围跨用户查询和导出审计记录。
- **系统任务**：写入审计事件，不直接读取审计内容。

### 5.2 权限规则

1. 普通用户不得读取其他用户的审计记录。
2. Agent 所有权校验沿用现有“加载 Agent → 校验 owner，管理员可绕过”的规则。
3. 查看 diff、快照和导出文件属于敏感操作，应单独记录到管理 `audit_log`。
4. 审计 API 不得通过 `thread_id`、`event_id` 或 `change_id` 绕过用户和 Agent 范围校验。
5. 删除审计记录、调整保留策略和执行恢复操作仅允许管理员或明确授权的角色执行。

## 6. 功能需求

### 6.1 对话审计

系统应能按用户、Agent、thread 和时间范围查询：

- 用户输入正文及附件元数据；
- AI 回复正文；
- 消息创建时间、完成状态及错误状态；
- 使用的模型、推理模式及 token 用量；
- turn 标识和消息顺序；
- 与该 turn 关联的工具调用和文件变更数量。

对话审计读取现有对话历史，不要求复制一份完整消息正文到文件变更表。但在长期合规模式下，应保存必要的不可变消息快照或内容哈希，使 thread 被业务删除后仍能证明审计事件对应的原始输入。

### 6.2 工具调用审计

每次工具调用至少记录：

- `tool_call_id`；
- `user_id`、`agent_id`、`thread_id`、`turn_id`；
- 工具名称和来源；
- 调用开始与结束时间；
- 执行状态：`started`、`succeeded`、`failed`、`cancelled`；
- 经过脱敏和大小限制处理的参数；
- 经过脱敏和大小限制处理的结果或错误；
- 与该调用关联的文件变更 ID 列表。

trajectory 可继续用于实时展示和近期分析，但长期审计不能完全依赖其“最近 50 个用户回合”和单字段约 64,000 字符的限制。

### 6.3 文件变更操作

必须审计以下操作：

| 操作 | 说明 |
|---|---|
| `create` | 新建文件 |
| `overwrite` | 覆盖已有文件 |
| `append` | 追加文件内容；若底层无法区分，可按 `overwrite` 记录 |
| `delete` | 删除文件 |
| `mkdir` | 新建目录 |
| `delete_tree` | 删除目录树 |
| `move` | 移动文件或目录 |
| `rename` | 同一父目录内重命名 |
| `upload` | 上传文件到工作区 |
| `restore` | 从审计快照或备份恢复文件 |

以下入口必须进入统一审计链路：

- AI 使用的工作区写入类工具；
- Dashboard 工作区文件编辑、上传、删除、移动和新建目录；
- Workspace HTTP API；
- 技能、专家发布、团队任务等系统内部工作区写入；
- 定时任务或其他自动化流程触发的工作区变更。

### 6.4 文件变更事件内容

每条文件变更事件至少包含：

```text
change_id
timestamp
actor_type / actor_id
user_id / agent_id
thread_id / turn_id / tool_call_id
source
operation
path / destination
before_exists / after_exists
before_size / after_size
before_hash / after_hash
before_mime / after_mime
diff_kind / diff_ref
before_snapshot_ref / after_snapshot_ref
status / error_code / error_message
request_id / correlation_id
```

要求：

1. `path` 和 `destination` 使用规范化后的工作区相对路径，不保存依赖本机的绝对路径。
2. `before_hash` 和 `after_hash` 使用明确版本的安全哈希算法，初始建议 SHA-256。
3. 操作失败时也应记录事件，但不得伪造 `after_*` 状态。
4. 同一次批量写入允许产生一个父事件和多个子变更事件。
5. 无 thread 的 Dashboard 或系统操作允许 `thread_id`、`turn_id` 为空，但必须保留 actor 和 request/correlation 标识。

### 6.5 文本 diff

1. UTF-8 文本文件优先生成 unified diff。
2. diff 必须包含文件路径、必要上下文和增删行。
3. diff 生成失败不应阻止原文件操作，但事件状态必须标记 `diff_unavailable` 并记录原因。
4. 超过配置阈值的文本文件只保存哈希、大小和受限摘要，diff 转存到独立对象或不生成。
5. 必须限制单条 diff 的持久化大小，避免大文件造成数据库膨胀。
6. 展示 diff 时应支持按行查看新增、删除和未变内容，并明确指出内容是否截断。

### 6.6 二进制文件

二进制文件默认不生成文本 diff，记录：

- MIME 类型；
- 修改前后大小；
- 修改前后哈希；
- 可选的安全元数据，如图片尺寸；
- 可选快照引用。

不得将二进制内容直接编码进数据库 JSON 字段。

### 6.7 快照与恢复

快照策略应支持以下模式：

| 模式 | 行为 |
|---|---|
| `metadata_only` | 仅保存元数据和哈希 |
| `diff` | 文本文件保存受限 diff |
| `snapshot` | 保存修改前和／或修改后内容引用 |

恢复能力如在首期提供，应满足：

1. 恢复前展示目标路径、当前哈希和待恢复版本哈希。
2. 当前文件已变化时必须提示冲突，不得静默覆盖。
3. 恢复本身产生新的 `restore` 审计事件，不修改历史事件。
4. 删除文件的恢复必须校验父目录和工作区路径边界。
5. 远程 BackendWorkspace 与本地工作区使用相同语义，不按 backend 类型在 API 层分叉。

### 6.8 查询与筛选

审计列表至少支持以下条件：

- 时间范围；
- 用户；
- Agent；
- thread；
- turn；
- 工具名称；
- 操作类型；
- 路径前缀；
- 执行状态；
- 是否具有 diff 或快照。

列表默认按时间倒序，使用稳定游标分页，不允许无上限返回全部记录。

### 6.9 导出

支持管理员或授权用户导出：

- JSONL：用于机器处理和长期归档；
- JSON：用于小范围完整导出；
- 可选的人类可读报告。

导出文件必须包含查询条件、导出时间、导出人和 schema 版本。导出操作自身写入管理 `audit_log`。

## 7. 数据模型建议

### 7.1 `workspace_change_log`

建议新增资源表，使用整数代理主键和公开字符串 ID：

```sql
workspace_change_log (
  id                    INTEGER PRIMARY KEY,
  change_id             TEXT NOT NULL UNIQUE,
  ts                    INTEGER NOT NULL,
  actor_type            TEXT NOT NULL,
  actor_id              TEXT,
  user_id               INTEGER,
  agent_id              TEXT NOT NULL,
  thread_id             TEXT,
  turn_id               TEXT,
  tool_call_id          TEXT,
  source                TEXT NOT NULL,
  operation             TEXT NOT NULL,
  path                  TEXT NOT NULL,
  destination           TEXT,
  before_exists         INTEGER NOT NULL,
  after_exists          INTEGER NOT NULL,
  before_size           INTEGER,
  after_size            INTEGER,
  before_hash           TEXT,
  after_hash            TEXT,
  before_mime           TEXT,
  after_mime            TEXT,
  diff_kind             TEXT,
  diff_ref              TEXT,
  before_snapshot_ref   TEXT,
  after_snapshot_ref    TEXT,
  status                TEXT NOT NULL,
  error_code            TEXT,
  error_message         TEXT,
  request_id            TEXT,
  correlation_id        TEXT,
  metadata_json         TEXT,
  integrity_hash        TEXT,
  schema_version        INTEGER NOT NULL
)
```

实现时必须同时提供 SQLite 和 PostgreSQL migration。根据当前仓库规则，HomeMind 未发布的 schema 工作应折入现有最高 migration，而 Octop 主库 migration 应根据其发布状态选择折入或新增版本，不得仅以本文示例决定编号。

### 7.2 大内容存储

diff 和快照不宜直接无限写入主表。建议使用内容寻址存储：

- 以内容 SHA-256 作为对象键或去重键；
- 主表只保存引用、大小、编码、压缩算法和加密元数据；
- 小 diff 可内联，大 diff 和快照放入独立存储；
- 引用计数或垃圾回收必须服从审计保留策略。

## 8. 采集架构

### 8.1 统一审计入口

所有工作区变更应通过 domain/infra 层的统一服务，例如：

```text
调用方
  → WorkspaceChangeService
    → 读取变更前状态
    → 执行 BackendWorkspace 操作
    → 读取变更后状态
    → 计算哈希／diff／快照
    → 持久化审计事件
```

不得只在 FastAPI router 中实现审计规则，否则 AI 工具和内部任务会绕过审计。Router 只负责身份解析、参数校验和错误映射。

### 8.2 上下文传播

用户消息进入处理器时创建或沿用以下上下文：

```text
request_id
correlation_id
user_id
agent_id
thread_id
turn_id
tool_call_id
source
```

该上下文必须传播到工作区写入层。对于没有对话上下文的 Dashboard 操作，使用 HTTP request ID 作为关联入口。

### 8.3 一致性

1. 文件操作成功、审计写入失败时不得静默忽略；必须产生错误指标和高优先级运行日志。
2. 首期可采用“文件操作优先、审计最终一致”，但必须记录可重试任务或失败队列。
3. 合规模式可配置为“审计不可用则拒绝变更”，具体策略由管理员明确开启。
4. 不得因 diff 或快照生成失败而错误报告文件操作失败；文件状态与审计状态应分别表达。

## 9. API 需求

建议提供以下 owner-scoped API：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/agents/{agent_id}/audit/conversations` | 查询对话审计摘要 |
| `GET` | `/api/agents/{agent_id}/threads/{thread_id}/audit` | 查询 thread 的对话、工具及文件关联事件 |
| `GET` | `/api/agents/{agent_id}/workspace/changes` | 查询文件变更列表 |
| `GET` | `/api/agents/{agent_id}/workspace/changes/{change_id}` | 查询变更详情 |
| `GET` | `/api/agents/{agent_id}/workspace/changes/{change_id}/diff` | 获取受权 diff |
| `GET` | `/api/agents/{agent_id}/workspace/changes/export` | 导出审计记录 |
| `POST` | `/api/agents/{agent_id}/workspace/changes/{change_id}/restore` | 可选：恢复指定版本 |

所有路由必须提供明确的 OpenAPI `summary`、响应模型、字段说明和错误码，并在 `/api/docs` 中可读。

## 10. Dashboard 需求

### 10.1 对话审计视图

- 在 thread 详情中展示用户消息、AI 回复、工具调用和文件变更的关联时间线。
- 工具调用可展开查看受限参数、结果、耗时和状态。
- 文件变更展示操作、路径、发起者和成功／失败状态。
- 从文件变更可定位到对应用户消息和 AI turn。

### 10.2 文件变更视图

- 提供 Agent 级文件变更列表和筛选器。
- 详情页展示前后哈希、大小、MIME、路径及关联上下文。
- 文本 diff 使用并排或统一视图展示。
- 明确标识“内容已截断”“未保存快照”“二进制文件无文本 diff”。
- 恢复操作必须二次确认，并展示覆盖风险。

### 10.3 管理视图

- 配置审计开关、保留期限、diff 上限及快照策略。
- 查看审计采集失败、存储占用和待清理数据。
- 管理员跨用户查询必须显示明确的高权限提示。

## 11. 安全与隐私

### 11.1 敏感信息处理

以下内容不得默认原样进入审计参数、结果、diff 或快照：

- API key、token、密码、Cookie、Authorization header；
- `.env`、认证文件和系统定义的敏感路径内容；
- 连接器凭证；
- 私钥和证书私钥；
- 用户配置的其他敏感模式。

必须在持久化之前脱敏，而不是仅在 UI 展示时遮挡。

### 11.2 加密

- 审计数据库和快照存储应支持静态加密。
- 远程对象存储快照使用受控凭证和最小权限。
- 导出文件应支持加密或由管理员在受控环境下载。

### 11.3 完整性

首期至少为每条事件保存规范化内容哈希。增强模式可使用哈希链或签名：

```text
integrity_hash = HASH(previous_hash + canonical_event_payload)
```

完整性校验失败必须可观测且不得被普通用户关闭。

## 12. 保留与删除策略

### 12.1 独立保留

审计保留策略必须独立于：

- thread 删除；
- checkpoint 清理；
- trajectory 最近回合裁剪；
- Agent 普通缓存清理。

### 12.2 建议默认值

默认值需由产品和合规负责人最终确认，建议起点：

- 结构化审计事件：180 天；
- 文本 diff：90 天；
- 内容快照：30 天或关闭；
- 元数据和哈希可比正文保留更久。

### 12.3 删除行为

1. 保留期限到期后由后台任务分批清理。
2. 清理操作本身写入管理审计。
3. 法律保留（legal hold）开启时，相关记录不得被自动清理。
4. 用户删除请求与合规留存冲突时，系统应按部署方政策处理，并保留处理依据。
5. 删除 Agent 或 thread 时应提示审计数据是否继续保留。

## 13. 配置建议

建议配置项：

```json
{
  "workspace_audit_enabled": true,
  "workspace_audit_failure_policy": "allow_with_alert",
  "workspace_audit_retention_days": 180,
  "workspace_audit_diff_retention_days": 90,
  "workspace_audit_snapshot_mode": "diff",
  "workspace_audit_max_inline_diff_bytes": 65536,
  "workspace_audit_max_snapshot_bytes": 10485760
}
```

配置必须经过现有 `OctopConfig` 和管理设置入口，不得在业务代码散落读取环境变量。面向用户的配置说明需同时提供中英文文案。

## 14. 可观测性

至少提供以下指标：

- 审计事件写入总数及失败数；
- 按 operation/source/status 分类的事件数；
- diff 和快照生成耗时；
- 审计数据库及对象存储占用；
- 被截断、脱敏或跳过的内容数量；
- 待重试和永久失败任务数量；
- 查询与导出延迟。

严重审计失败应写入结构化运行日志，并可被部署方告警系统采集。

## 15. 性能要求

1. 元数据审计不应使普通小文件写入的 P95 延迟增加超过 100 ms；具体目标需基准测试确认。
2. 大文件哈希、diff 和快照允许异步完成，列表中显示 `pending` 状态。
3. 查询必须使用索引，至少覆盖 `agent_id + ts`、`thread_id + ts`、`tool_call_id`、`path + ts`。
4. diff 和快照读取不得阻塞主事件列表。
5. 批量操作不得为每个文件创建独立数据库连接。

## 16. 兼容性与迁移

- 新功能默认不得破坏现有对话、trajectory 和 Workspace API。
- 历史文件变更无法可靠补录时，不得伪造审计数据。
- 可将现有 trajectory 工具事件标记为 `legacy` 并有限展示，但需明确它们不具备完整文件前后状态。
- SQLite 和 PostgreSQL 必须行为一致。
- Windows 和 POSIX 路径使用 `pathlib` 语义和工作区相对路径进行测试。
- 远程、Docker 和本地 BackendWorkspace 必须使用同一 domain 服务。

## 17. 验收标准

### 17.1 核心验收

1. 用户在对话中要求 AI 新建文本文件后，可以从 thread 审计视图看到用户消息、工具调用和 `create` 事件。
2. AI 覆盖文本文件后，可以看到修改前后 SHA-256 和 unified diff。
3. AI 删除文件后，可以看到删除前哈希、大小及可用的快照状态。
4. 用户通过 Dashboard 编辑、上传、移动和删除文件时，也产生同格式事件。
5. 管理员可按 Agent、thread、路径、操作和时间筛选并导出 JSONL。
6. 普通用户访问他人审计记录返回授权错误，不泄漏记录是否存在。
7. 删除 thread 后，按配置保留的文件审计仍可查询，并明确原 thread 已删除。
8. trajectory 被裁剪后，长期文件审计仍然存在。
9. 敏感字段在落库前完成脱敏。
10. 审计写入失败时产生指标和错误日志；合规阻断模式下文件变更被拒绝。

### 17.2 测试要求

- Repo 单元测试：SQLite/PostgreSQL CRUD、分页、筛选和级联行为。
- Service 单元测试：create/overwrite/delete/move、失败路径、diff 截断和脱敏。
- API 集成测试：鉴权、owner/admin 范围、导出和恢复冲突。
- Gateway 测试：tool call 与 turn/thread/change 的关联。
- Dashboard 测试：列表、详情、diff、权限错误和截断提示。
- 跨平台测试：Windows/POSIX 路径、禁止越界、目录操作。
- 性能测试：大文件、批量文件和高频工具调用。
- 最终以 `make all` 通过为交付门槛，Dashboard 变更额外执行 TypeScript 检查和生产构建。

## 18. 分阶段交付建议

### 阶段 1：结构化元数据审计

- 新增数据表和统一 domain service；
- 覆盖 Workspace API 与 AI 工具写入；
- 记录 actor、关联 ID、操作、路径、大小和哈希；
- 提供基础查询 API；
- 暂不提供快照恢复。

### 阶段 2：diff 与 Dashboard

- 文本 unified diff；
- 对话／工具／文件关联时间线；
- Agent 文件变更列表和详情；
- JSONL 导出；
- 保留与清理任务。

### 阶段 3：快照、恢复与完整性增强

- 内容寻址快照；
- 冲突安全恢复；
- legal hold；
- 哈希链或数字签名；
- 合规阻断模式和高级告警。

## 19. 待确认问题

在进入实现设计前，需要产品、架构和合规负责人确认：

1. 默认保留期限以及不同部署版本是否允许自定义。
2. 是否默认保存文本 diff，是否默认保存文件快照。
3. 单文件、单 diff 和单次导出的大小上限。
4. 删除用户、Agent 或 thread 后的审计留存政策。
5. 哪些角色可以跨用户查询和导出。
6. 是否需要不可篡改存储、哈希链或外部审计归档。
7. 首期是否提供恢复能力。
8. 审计写入失败时默认继续文件操作还是阻断。
9. 是否需要审计 Agent 工作区之外的受控资产存储变更。
10. 是否将对话正文复制到长期审计存储，还是只保留消息引用和哈希。


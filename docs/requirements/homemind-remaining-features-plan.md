# HomeMind 剩余功能实现计划（MiniMax 执行版）

> 本文基于当前仓库实现编写，供 MiniMax 分阶段开发。执行前必须完整阅读根目录 `AGENTS.md`；其约束高于本文。

## 1. 当前基线与目标

以下能力已经存在，本计划禁止重复实现：

- Family、Member、Relationship、Space 与权限模型。
- Family Asset、扫描、metadata、hash、相册和照片预览。
- `SCAN/METADATA/THUMBNAIL/VISION/EMBEDDING/FACE_MATCH/REINDEX` 持久化任务。
- 照片理解、向量检索、人脸候选确认/拒绝。
- Family Memory、候选审核、生命周期和维护任务。
- Family Transaction 的审批、执行、验证、补偿与审计框架。
- Device 配对、心跳、命令租约和文件 Runtime。
- TXT、Markdown、文本型 PDF、DOCX 的解析、分块、Embedding、检索和引用。
- 家庭导入导出、实时 Dashboard 事件和基础可观测性。

本轮目标按优先级分为六条主线：

1. 家庭日历、提醒与通知中心。
2. 扩充可审批的 Transaction 动作。
3. 将 Family Task 升级为可调度、可由 Agent 执行的任务系统。
4. OCR 与 Knowledge Pipeline 产品化。
5. Home Assistant/MQTT 智能家居接入。
6. 原生移动端 API 契约与 PWA 第一阶段；原生壳另立项目后再做。

## 2. 开发与交付原则

- 一次只执行一个阶段，不允许把六条主线一次性实现。
- 先写或补失败测试，再做最小实现。
- 领域逻辑放在 `src/homemind/infra/`；router 只做 HTTP 校验、调用和错误映射。
- HomeMind 可以依赖 Octop，禁止 Octop Core 反向依赖 HomeMind。
- 数据库同时支持 SQLite/PostgreSQL；迁移必须提供 `.sql` 与 `.pg.sql` 配对。
- 先确认当前 HomeMind schema 版本是否已发布；未发布变更折叠进当前迁移，已发布才新增版本。
- 公共资源使用字符串 public ID，内部自增 `id` 只作 PK；外键引用 public ID。
- 用户可见字符串进入中英文 i18n。
- 时间展示与调度使用服务端 `default_timezone`，数据库持久化 UTC 时间戳。
- 外部模型、OCR、日历和智能家居凭据只保存在现有 secrets/provider 体系，不写入普通配置、日志和审计正文。
- 所有高风险动作继续执行 Permission → Approval → Execute → Verify → Audit。
- 异步函数不得执行阻塞 I/O；使用 `asyncio.to_thread` 或现有 executor 模式。
- 每阶段必须运行定向测试；最终交付标准为 `make all` 通过。

## 3. 阶段 0：现状冻结与契约测试

### 3.1 代码审计

先阅读：

- `src/homemind/infra/family/tasks.py`
- `src/homemind/infra/db/repos/family_tasks.py`
- `src/homemind/api/routers/tasks.py`
- `src/homemind/infra/family/events.py`
- `src/homemind/infra/family/transactions.py`
- `src/homemind/infra/family/transaction_actions/`
- `src/homemind/infra/family/knowledge.py`
- `src/homemind/infra/family/knowledge_parsers.py`
- `src/homemind/infra/family/device_runtime.py`
- `src/homemind/device_runtime/`
- `dashboard/src/pages/FamilyTasks/`
- `dashboard/src/pages/FamilyTimeline/`
- `dashboard/src/pages/FamilyApprovals/`

建立一份仅供实施使用的功能矩阵，记录每项能力的表、repo、manager、API、UI、权限、审计和测试。发现已有能力时复用，不创建平行实现。

### 3.2 回归基线

执行：

```bash
uv run pytest tests/unit/homemind -q
uv run pytest tests/integration/test_homemind_family_api.py -q
uv run pytest tests/integration/test_homemind_device_api.py -q
cd dashboard && npx tsc -b
```

记录通过数、跳过数和失败原因。基线本身失败时先判断是否与本计划有关；不要顺手修改无关代码。

### 3.3 契约保护

增加测试保证：

- 已有家庭 API 路径和响应字段不被后续迁移破坏。
- Family Task 旧客户端仍可只提交 title/description/due_at。
- Transaction 原有 action 名保持可用。
- 文本型 PDF/DOCX 解析结果不因 OCR 改造退化。
- Device Runtime 文件能力不因智能家居 adapter 改造退化。

## 4. 阶段 1：家庭日历与提醒

### 4.1 领域模型

新增两个资源，避免把日历事件硬塞进现有 `FamilyEvent`：

`FamilyCalendar`：

- `id`：内部 PK。
- `calendar_id`：公共 ULID。
- `family_id`。
- `name`、`description`、`color`。
- `timezone`：缺省继承服务端 timezone，允许日历级覆盖。
- `visibility`、`space_id`。
- `created_by`、`created_at`、`updated_at`。

`FamilyCalendarEvent`：

- `id`、`event_id`、`calendar_id`、`family_id`。
- `title`、`description`、`location`。
- `starts_at`、`ends_at`、`all_day`。
- `timezone`。
- `recurrence_rule`：存规范化 RFC 5545 RRULE，不保存自然语言。
- `recurrence_until`。
- `source_type/source_id`：关联手工、task、external calendar。
- `status`：`CONFIRMED/CANCELLED`。
- `version`：乐观并发控制。
- 审计字段。

不要预先展开无限重复事件。查询窗口内按 RRULE 计算 occurrence，并设置最大窗口和最大条数。

### 4.2 Reminder 模型

新增 `FamilyReminder`：

- `reminder_id`、`family_id`。
- `target_type`：`TASK/CALENDAR_EVENT/APPROVAL/DEVICE`。
- `target_id`。
- `recipient_member_id` 或 `recipient_user_id`。
- `remind_at`。
- `channel`：首版只实现 `IN_APP`。
- `status`：`PENDING/CLAIMED/SENT/FAILED/CANCELLED`。
- `lease_owner/lease_expires_at`。
- `attempt_count/last_error`。
- `dedupe_key` 唯一约束。

重复事件的 reminder 不批量预生成多年数据；只滚动生成未来一段窗口，例如 30 天。

### 4.3 Repo 与 Manager

建议新增：

- `src/homemind/infra/db/repos/family_calendars.py`
- `src/homemind/infra/db/repos/family_reminders.py`
- `src/homemind/infra/family/calendar.py`
- `src/homemind/infra/family/reminders.py`

Manager 负责：

- 家庭/空间权限检查。
- 时区转换和 RRULE 校验。
- occurrence 查询。
- reminder 创建、取消、claim、发送、重试和 stale lease 恢复。
- Task due_at 变更时同步 reminder。
- Calendar event 更新/取消时使旧 reminder 失效。

### 4.4 Reminder Runner

在 `HomeMindServer` 生命周期内启动单个后台 runner：

1. 启动时恢复过期 lease。
2. 定时 claim 到期 reminder。
3. 通过现有家庭事件总线推送用户级通知。
4. 写入持久化通知记录。
5. 成功后标记 `SENT`；失败指数退避，超过上限标记 `FAILED`。
6. stop 时优雅退出，不取消已提交的数据库状态更新。

禁止直接从 runner 调用 Dashboard；只能发布领域事件。

### 4.5 API

新增薄路由：

- `POST/GET /api/homemind/families/{family_id}/calendars`
- `GET/PATCH/DELETE /.../calendars/{calendar_id}`
- `POST/GET /.../calendar-events`
- `GET/PATCH/DELETE /.../calendar-events/{event_id}`
- `GET /.../calendar-occurrences?from=&to=`
- `POST/GET/PATCH/DELETE /.../reminders`

请求与响应使用明确 Pydantic 模型；时间字段说明 UTC/时区语义；每个 route 有 summary。

### 4.6 Dashboard

实现：

- 月/周/日三种基础视图；首版可优先月视图与 agenda 列表。
- 创建/编辑普通和重复事件。
- 服务端 timezone 显示。
- Task 截止时间可一键生成提醒。
- Timeline 合并展示 calendar occurrence，但不能复制成 FamilyEvent 数据。
- 移动端宽度下使用 agenda，不强行显示桌面月历。

### 4.7 测试

- SQLite/PostgreSQL 迁移配对。
- DST 跨越、全天事件、跨午夜事件。
- RRULE 日/周/月重复和截止时间。
- occurrence 查询上限。
- reminder 去重、lease、恢复和重试。
- 成员被移除后的通知隔离。
- 普通成员/管理员权限。
- Dashboard 时区与响应式测试。

验收：创建一个每周家庭事件，在正确服务端时区生成 occurrence，并只向有权限成员发送一次站内提醒。

## 5. 阶段 2：持久化通知中心

### 5.1 Notification 模型

新增 `FamilyNotification`：

- `notification_id`、`family_id`、`user_id`。
- `type`：task due、approval waiting、device offline、calendar reminder 等稳定枚举。
- `title_key/body_key` 与结构化参数；不要只保存已经翻译的中文或英文。
- `target_type/target_id`。
- `severity`。
- `read_at`、`created_at`、`expires_at`。
- `dedupe_key`。

实时推送只是加速路径，数据库记录才是通知真相源。

### 5.2 通知偏好

新增用户级家庭通知偏好：

- 按通知类型启停。
- 免打扰时间段。
- 时区。
- 首版 channel 只开放 `IN_APP`；外部通道字段不要提前伪装成已支持。

### 5.3 触发器

接入：

- Reminder 到期。
- Transaction 等待审批。
- 审批通过/拒绝。
- Device 离线超过阈值。
- Asset job 完成/失败。
- Knowledge 索引失败。

触发必须幂等，统一走 `NotificationManager.emit()`；不要在各 router 直接 INSERT。

### 5.4 API 与 UI

API：列表、未读数、标记单条已读、全部已读、删除过期通知、偏好读取/修改。

UI：顶部通知入口、未读徽标、按类型筛选、跳转目标、全部已读、偏好页。继续复用实时事件更新未读数。

测试：去重、权限、过期清理、免打扰、并发已读、实时事件丢失后的数据库恢复。

## 6. 阶段 3：扩充 Transaction 动作

### 6.1 动作清单

按以下顺序扩展 `FamilyActionRegistry`：

低风险或易验证：

- `task.update`
- `task.cancel`
- `event.update`
- `event.delete`
- `memory.update`
- `memory.deprecate`
- `album.create`
- `album.add_asset`
- `album.remove_asset`

高风险：

- `device.command`
- `calendar.create_event`
- `calendar.update_event`
- `calendar.cancel_event`
- `knowledge.reindex`
- `asset.batch_move`
- `asset.move_to_trash`

不要把 Family/Member/Permission 管理开放给 Agent，除非另一次安全评审明确批准。

### 6.2 每个 Handler 的固定结构

每个 action handler 必须实现：

1. `validate`：schema、资源存在性和 family ownership。
2. `preview`：返回用户能理解的变更摘要。
3. `permission`：从动作和目标计算风险，不接受模型自行声明风险。
4. `execute`：带幂等键执行。
5. `verify`：从真实数据源重新读取并验证结果。
6. `compensate`：仅在安全且确定时补偿；不能安全补偿则进入 `NEEDS_REVIEW`。
7. `audit`：结构化记录，不包含秘密或文档正文。

### 6.3 Device command 特殊规则

- Transaction 审批后只负责向设备队列提交命令，不同步等待设备完成。
- Transaction 状态增加异步执行关联，或建立 transaction ↔ device command 映射。
- 设备结果回报后再完成 Verify。
- lease 过期、设备离线和未知结果进入 `NEEDS_REVIEW`，不得误标成功。

### 6.4 批量动作

- preview 必须列出数量、目标空间和抽样项目。
- item 级结果持久化，支持部分失败。
- 超过配置阈值强制审批。
- 默认不永久删除，只移动到 `.homemind-trash`。
- 重试只处理失败项。

### 6.5 测试

为每种 action 测试成功、拒绝、越权、幂等、验证失败和补偿。继续扩展 `tests/unit/homemind/test_transaction_*`，不要另造平行事务测试框架。

## 7. 阶段 4：Family Task 调度与 Agent 执行

### 7.1 扩展 Task 数据模型

兼容现有字段，新增：

- `task_type`：`MANUAL/AGENT/DEVICE`。
- `priority`。
- `schedule_at`。
- `recurrence_rule`。
- `agent_id`。
- `transaction_id`。
- `parent_task_id`。
- `depends_on`：建议使用独立依赖表，不存 JSON。
- `attempt_count/max_attempts`。
- `lease_owner/lease_expires_at`。
- `started_at/completed_at`。
- `result_summary/last_error`。
- `version`。

旧任务默认 `MANUAL`，迁移不得改变旧状态。

### 7.2 状态机

明确合法迁移：

```text
TODO → SCHEDULED → IN_PROGRESS → DONE
                      ├────────→ FAILED → TODO（重试）
                      └────────→ WAITING_APPROVAL → IN_PROGRESS
任意未终态 → CANCELLED
```

状态迁移必须在 repo 层使用条件更新保证并发安全，不能先读后无条件写。

### 7.3 Scheduler

实现数据库持久化 scheduler：

- claim 到期且依赖已满足的任务。
- stale lease 恢复。
- 单个 family/agent 并发上限。
- 指数退避和最大尝试次数。
- 重复任务只生成下一次 occurrence。
- 服务重启不丢任务。
- 取消任务后不得再次 claim。

优先复用 Octop cron 的稳定调度原语；但 HomeMind 任务状态和领域规则保留在 HomeMind，不复制 cron 表。

### 7.4 Agent 执行

- 校验 agent 属于有权用户并处于可运行状态。
- 为任务创建明确 thread/session，记录 task ID。
- 注入 Family Context，但只包含调用者可访问数据。
- Agent 输出计划时，高风险动作进入 Family Transaction，不直接执行。
- 最终摘要写回 task；详细轨迹继续由 Octop history 管理。
- 超时不能简单标失败；先取消运行并记录是否可能存在未确认副作用。

### 7.5 子任务与依赖

首版只支持有向无环依赖：

- 创建时检查环。
- 父任务完成不自动代表子任务完成，反向由所有必需子任务聚合。
- 依赖失败时下游进入 `BLOCKED` 或保持不可 claim，并明确原因。

### 7.6 UI

- 看板：待办、计划中、执行中、等待审批、完成、失败。
- 任务详情：调度、负责人/Agent、依赖、执行日志摘要、关联审批。
- 重试、取消、暂停重复任务。
- 手工任务继续保持简单创建体验。

### 7.7 测试

状态机、并发 claim、重启恢复、依赖环、重复规则、审批恢复、Agent 超时、权限变化、取消竞态和 Windows/Linux 一致性。

## 8. 阶段 5：OCR 与 Knowledge Pipeline 产品化

### 8.1 OCR Provider 抽象

定义最小协议：

```python
class OcrProvider(Protocol):
    name: str
    def recognize_pdf(self, path: Path) -> list[OcrPage]: ...
```

`OcrPage` 至少包含页码、文本和可选置信度。首个 provider 选择一个明确方案：本地 OCR 或 OpenAI-compatible 外部 OCR；不要同时开发多个。

### 8.2 隐私边界

- 本地 OCR 默认可用。
- 外部 OCR 必须增加家庭隐私开关和 external processing audit。
- 外呼前显示 provider、数据类别和用途。
- 不记录整页 OCR 文本到普通日志。

### 8.3 异步索引任务

文档索引不能长期占用 HTTP 请求：

- 新增 `KNOWLEDGE_INDEX` 持久化 job，或复用已有资产 job 框架并扩展清晰类型。
- item 绑定 asset ID。
- 持久化 parser/OCR/embedding 配置版本。
- 支持暂停、恢复、取消、失败重试。
- 文件 hash 未变化时跳过。
- OCR、解析、分块、Embedding 分阶段记录进度。

### 8.4 格式扩展顺序

1. 扫描版 PDF OCR。
2. HTML。
3. XLSX。
4. PPTX。
5. EML/邮件归档。

每种格式必须保留可验证 locator：页码、sheet/cell、slide、邮件主题/附件等。没有可靠定位时不要宣称支持引用。

### 8.5 自动更新

- 资产扫描发现 hash 变化后，只将知识文档标记 stale 并入队，不在扫描事务内解析。
- 资产删除后清理 document/chunks。
- 权限变化无需重建向量，但查询时必须实时过滤。
- parser version 变化触发受控重建。

### 8.6 Dashboard

Files 页面展示：索引状态、格式、chunk 数、provider、最后成功时间、错误、重新索引和移除索引。搜索结果支持打开文件并定位页码/段落。

### 8.7 测试

扫描 PDF、混合文本/OCR PDF、损坏文件、超大文件、OCR 超时、外部隐私拒绝、hash 幂等、parser 升级、权限撤销和引用定位。

## 9. 阶段 6：Home Assistant 与 MQTT

### 9.1 边界

首版只接入 Home Assistant REST/WebSocket 和通用 MQTT。Matter、门锁、摄像头和医疗设备不在本阶段。

### 9.2 Adapter 接口

定义：

- `probe()`
- `list_entities()`
- `get_state(entity_id)`
- `preview_command(command)`
- `execute_command(command, idempotency_key)`
- `verify_command(command)`

Adapter 位于 HomeMind infra，不放入 API。凭据通过 connector/secret 体系引用。

### 9.3 实体映射

新增家庭设备与外部实体映射：provider、external entity ID、capabilities、last state、last seen。外部 ID 不作为 HomeMind 公共主键。

### 9.4 风险策略

- 读取状态：低风险。
- 灯光/普通开关：可按家庭策略决定是否审批。
- 温控、门锁、安防、摄像头：默认禁止；门锁和摄像头本阶段不实现。
- 所有写命令通过 `device.command` Transaction。
- Agent 不能提交任意 service/topic，只能使用 catalog 中的结构化 command。

### 9.5 MQTT 安全

- topic 必须匹配管理员配置的 allowlist。
- payload 使用 schema 校验和大小限制。
- 禁止通配 publish。
- TLS 校验默认开启。
- 日志隐藏用户名、密码和敏感 payload。

### 9.6 测试

使用 fake HA/MQTT server：连接失败、状态同步、allowlist、审批、幂等、验证、设备离线、恶意 topic 和跨家庭隔离。

## 10. 阶段 7：移动体验

### 10.1 本阶段范围

先交付响应式 PWA 和稳定移动 API，不在 Python 仓库中仓促新增 React Native/Flutter 工程。

### 10.2 PWA

- Home、Family、Photos、Tasks、Approvals、Notifications 在 360px 宽度可操作。
- 支持添加到主屏幕。
- 缓存静态壳，不缓存家庭隐私数据响应。
- 离线时只显示明确的不可用状态，不展示可能过期的敏感数据。
- 文件上传支持手机照片选择。
- 审批按钮避免误触，显示动作预览。

### 10.3 移动 API 契约

- 使用现有 JWT/refresh 体系。
- 增加分页游标，避免移动端一次加载全部照片/通知。
- 上传支持断点续传放到后续；首版限制大小并提供进度。
- 通知 push token、APNs/FCM 属于原生客户端阶段，本阶段只保留设计文档，不新增无效表。

### 10.4 原生客户端立项门槛

只有 PWA 验收后再单独决策 React Native/Flutter。立项前先确定：代码仓库、签名、推送、Secure Storage、相册权限、后台上传和发布渠道。

## 11. 端到端验收场景

### 场景 A：家庭行程

创建重复日历事件 → 生成提醒 → 通知中心收到一条通知 → 点击进入事件 → 修改时间 → 旧提醒取消、新提醒生成。

### 场景 B：Agent 家庭任务

创建计划任务 → Scheduler claim → Agent 生成整理照片计划 → Transaction 等待审批 → 用户批准 → 设备 Runtime 执行移动 → Verify 成功 → Task 完成并通知用户。

### 场景 C：扫描合同问答

登记扫描版 PDF → OCR job → 分块/Embedding → 查询保修期 → 回答引用具体页码 → 撤销文件权限后相同用户无法再检索。

### 场景 D：智能家居

读取 Home Assistant 灯状态 → Agent 提议关灯 → 根据家庭策略进入审批 → 执行结构化命令 → 重新读取状态验证 → 写审计和通知。

## 12. 实施批次

1. 批次一：阶段 0、阶段 1。
2. 批次二：阶段 2、阶段 3 的低风险动作。
3. 批次三：阶段 3 高风险动作、阶段 4。
4. 批次四：阶段 5。
5. 批次五：阶段 6。
6. 批次六：阶段 7 与全部端到端验收。

每个批次完成后必须停下汇报，等待确认，不自动进入下一批。

## 13. 最终验证

```bash
uv run pytest tests/unit/homemind -q
uv run pytest tests/integration -x -q
cd dashboard && npx tsc -b
cd dashboard && npm run lint
make all
```

涉及 API 时检查 `/api/docs`；涉及 i18n 时运行对应 i18n 测试；涉及 Dashboard 时运行 `make build-frontend`，不得直接编辑 `src/octop/dashboard/`。

## 14. MiniMax 每阶段汇报格式

1. 本阶段结论。
2. 实际修改文件及用途。
3. 数据库和 API 兼容性。
4. 权限、隐私和审计处理。
5. 实际执行的验证命令与完整结果摘要。
6. 未完成项、风险或阻塞。
7. 下一阶段建议，不擅自扩大范围。

## 15. 交给 MiniMax 的第一条指令

```text
请完整阅读根目录 AGENTS.md 和 docs/requirements/homemind-remaining-features-plan.md。
本次只执行“阶段 0：现状冻结与契约测试”，不要实现阶段 1 及之后功能。

先审计计划列出的现有模块，确认当前 schema 版本、已实现能力和基线测试状态；发现计划与代码不一致时，以代码和 AGENTS.md 为准并明确报告，不要创建重复实现。

然后补充必要的兼容性契约测试，保证后续日历、通知和任务迁移不会破坏现有 Family Task、Transaction、Knowledge 和 Device Runtime API。只修改与阶段 0 直接相关的测试或最小测试辅助代码，不做生产功能开发。

完成后运行阶段 0 的验证命令，并严格按第 14 节格式汇报。不要提交或推送。
```

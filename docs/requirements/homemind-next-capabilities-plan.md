# HomeMind 下一阶段能力实施计划（MiniMax 执行版）

> 范围：大文件断点续传、华为/小米智能家居兼容、AgentTeams 产品化、Bridge 跨实例稳定化。
> 本文是实施计划，不代表对应能力已经交付。执行前必须完整阅读根目录 `AGENTS.md`；其约束高于本文。

## 1. 总体原则与关键决策

1. 一次只实施一个阶段；每阶段独立提交评审，不把四条主线揉成一个大改动。
2. 先补契约测试，再做最小实现；每阶段结束运行定向测试，整条主线结束运行 `make all`。
3. HomeMind 只依赖 Octop，不允许 Octop Core 反向导入 `homemind`。
4. API router 只负责鉴权、校验和错误映射；状态机、重试、权限、审计属于 `infra/`。
5. 所有数据库变更同时提供 SQLite 与 PostgreSQL 迁移，并更新 HomeMind schema 水位测试。
6. 所有用户可见文本进入中英文 i18n；所有时间使用服务端 `default_timezone`，持久化 UTC。
7. 不允许通过抓包、私有 token 提取、逆向协议或模拟官方 App 登录接入华为/小米。
8. 华为/小米采用三层兼容策略：
   - 第一层：复用现有 Home Assistant adapter，由 Home Assistant 负责厂商生态接入。
   - 第二层：Matter 设备仍通过 Home Assistant Matter integration 暴露，不在 HomeMind 内实现 Matter controller。
   - 第三层：只有取得厂商正式开放平台权限、稳定 API 文档和测试账号后，才增加厂商直连 adapter。
9. 门锁、摄像头、安防撤防、燃气阀等高风险设备不在本轮开放写控制；首版只读状态，后续需单独安全评审。
10. 不改变既有上传、Home Assistant、MQTT、Team、Bridge API 的兼容行为；新增能力使用新端点或向后兼容字段。

## 2. 交付顺序与依赖

建议按以下顺序执行：

1. 阶段 0：基线审计与契约冻结。
2. 阶段 1–4：大文件断点续传。
3. 阶段 5–8：智能家居统一模型及华为/小米兼容。
4. 阶段 9–12：AgentTeams 产品化。
5. 阶段 13–16：Bridge Phase 4。
6. 阶段 17：跨主线端到端验收。

断点续传先做，因为 Bridge 远程附件和未来设备文件同步都应复用同一上传会话协议。智能家居、AgentTeams 和 Bridge 在领域上互不依赖，可以在不同分支开发，但不得共用未合并迁移编号。

## 3. 阶段 0：基线审计与契约冻结

### 3.1 审计范围

- 上传：`src/octop/api/routers/uploads.py`、`src/octop/api/routers/workspace.py`、`src/octop/api/common/attachments.py`、`dashboard/src/api/request.ts`。
- 智能家居：`src/homemind/infra/family/smart_home*.py`、`src/homemind/api/routers/smart_home.py`、相关 repo、migration 和测试。
- Team：`src/octop/infra/agents/teams/`、`src/octop/api/routers/teams.py`、`dashboard/src/pages/Experts/components/Team*.tsx`、`docs/expert-teams.md`。
- Bridge：`src/octop/infra/bridge/`、`src/octop/api/routers/bridge.py`、`dashboard/src/pages/Settings/Bridge/`、`docs/bridge.md`。

### 3.2 输出

新增一份只用于实施的功能矩阵，逐项记录：现有 repo、manager、API、UI、权限、审计、测试和缺口。先确认当前迁移水位及未发布迁移能否折叠，不得机械新增版本。

### 3.3 契约测试

- 现有 multipart 上传仍能使用，且继续执行 `max_upload_bytes` 限制。
- 现有 Home Assistant/MQTT provider 创建、刷新实体、预览命令行为不变。
- Team CRUD、成员关系、协调者执行的响应结构不变。
- Bridge 的 `bridge:{connection_id}:{agent_id}` 映射、现有 tunnel policy 和本地路由不变。

验证：

```bash
uv run pytest tests/unit/api/test_upload_limit.py -q
uv run pytest tests/unit/homemind/test_smart_home.py tests/unit/homemind/test_smart_home_manager.py -q
uv run pytest tests/unit/agents/test_team_service.py tests/unit/gateway/test_global_processor_team.py -q
uv run pytest tests/unit/bridge -q
```

## 4. 主线一：大文件断点续传

### 阶段 1：定义上传会话协议与持久化模型

采用服务端管理的 upload session，不直接实现完整 tus 协议。协议必须支持刷新页面、进程重启、重复分片和最终校验。

新增资源建议：

`UploadSession`：

- `upload_id`：公共 ULID。
- `owner_user_id`、`agent_id`；家庭资产上传时另存 `family_id`。
- `purpose`：`CHAT_ATTACHMENT | WORKSPACE_FILE | FAMILY_ASSET | KNOWLEDGE_DOCUMENT`。
- `filename`、`relative_target`、`mime_type`。
- `total_bytes`、`chunk_size`、`expected_sha256`。
- `received_bytes`、`status`：`OPEN | ASSEMBLING | COMPLETED | ABORTED | EXPIRED | FAILED`。
- `expires_at`、`created_at`、`updated_at`、`completed_at`。
- `final_resource_id`、`last_error`。

`UploadPart`：

- `upload_id`、`part_number`、`offset`、`size`、`sha256`、`created_at`。
- 唯一约束 `(upload_id, part_number)`；同时拒绝 offset 区间重叠。

文件布局：临时分片只能位于 Octop 数据目录的专用 staging 根，例如 `uploads/staging/{upload_id}/`；最终文件仍走现有 workspace/attachment/knowledge/family asset 服务。客户端不得提供绝对 staging path。

必须定义：默认分片大小、最小/最大分片、会话 TTL、单用户并发会话数、单文件最大值和全局 staging 配额。不要把限制硬编码在 router。

### 阶段 2：领域服务与 HTTP API

新增 domain service，负责：

1. 创建会话并校验目标权限和总大小。
2. 查询已接收分片，返回缺失区间。
3. 写入分片：先写临时文件，再 fsync/关闭，再原子登记；同一 part 重传且 hash 相同返回幂等成功，不同则 `409`。
4. 完成上传：独占 claim，按顺序流式合并，校验总大小和 SHA-256，再调用现有目标服务落盘。
5. 取消、过期清理、失败重试和进程重启恢复。
6. 完成后删除分片；失败保留到 TTL，便于继续上传。

建议 API：

```text
POST   /api/uploads/sessions
GET    /api/uploads/sessions/{upload_id}
PUT    /api/uploads/sessions/{upload_id}/parts/{part_number}
POST   /api/uploads/sessions/{upload_id}/complete
DELETE /api/uploads/sessions/{upload_id}
```

`PUT part` 使用原始二进制 body，并要求 `Content-Length`、`X-Chunk-SHA256` 和服务端已知 offset；不要用 multipart 包裹每个分片。状态查询返回 `received_parts` 或压缩后的 `missing_ranges`，不得为十万分片返回无界数组。

兼容要求：保留现有 multipart API；小文件继续走旧路径，大于前端阈值或网络恢复时自动走 session API。

安全要求：

- 每次请求校验 owner、agent/family/knowledge 权限。
- 文件名只作为显示元数据；目标路径使用现有 workspace path normalizer。
- 拒绝超配额、越界 offset、重复区间、长度不符和 hash 不符。
- 合并过程中不得一次性把文件读入内存。
- 日志不得打印文件内容、token 或完整本地路径。

### 阶段 3：Dashboard 上传器

在 `dashboard/src/api/` 增加通用 resumable uploader：

- 默认并发 3 个分片，可取消；并发数先做常量，不增加设置页。
- IndexedDB 仅保存 `upload_id`、文件指纹、目标、分片状态；不保存文件内容。
- 文件指纹至少包含 name、size、lastModified；若浏览器重新选择文件，再验证首尾采样 hash 或完整 hash。
- 页面刷新后提示用户重新选择原文件，再从服务端缺失区间续传。
- 显示整体进度、上传速度、剩余时间、暂停、继续、取消和失败原因。
- 网络断开采用有上限的指数退避；`401/403/409/413` 不盲目重试。
- 小文件保持现有 XHR 上传体验。

首批接入顺序：Family Asset/照片 → chat attachment → knowledge document → workspace binary。每接一个入口都补前端测试，不一次性替换所有上传调用。

### 阶段 4：上传清理、Bridge 复用与验收

- 启动后台清理器处理 `EXPIRED/ABORTED/COMPLETED` 残留 staging 文件。
- 添加指标：活动会话、接收字节、失败/校验失败、恢复次数、清理字节。
- Bridge 不复制上传状态机；远程上传应把 session API 经 tunnel 转发到文件最终所在实例。
- Bridge tunnel 的单帧仍有限制，大分片应继续切成 transport frame，但应用层 part 只在对端确认完整后才算成功。

验收场景：

1. 上传 2 GB 稀疏测试文件，进程内存不随文件大小线性增长。
2. 上传 35% 后刷新页面，重新选文件后只发送缺失分片。
3. 上传中重启服务，状态恢复并最终 hash 一致。
4. 同一分片重复提交幂等；篡改分片被拒绝。
5. 两个用户不能读取、写入或完成彼此的会话。
6. 本地与 Bridge 远程附件均能续传，最终文件只落在目标实例。
7. 会话过期后数据库与 staging 文件都被清理。

定向验证：

```bash
uv run pytest tests/unit/uploads tests/unit/bridge -q
uv run pytest tests/integration -k "upload or bridge" -q
cd dashboard && npm test -- --run
cd dashboard && npx tsc -b
```

## 5. 主线二：华为、小米及更广泛智能家居兼容

### 阶段 5：统一设备能力模型

不要为每个品牌复制一套业务逻辑。扩展现有 smart-home adapter 接口，统一输出：

- `DeviceDescriptor`：厂商、型号、固件、连接方式、在线状态、房间、设备标识。
- `CapabilityDescriptor`：`switch`、`light`、`brightness`、`color_temperature`、`climate`、`sensor`、`curtain`、`vacuum`、`air_purifier`、`scene` 等。
- `PropertyDescriptor`：类型、单位、读写性、范围、枚举和值时间戳。
- `CommandDescriptor`：结构化参数 schema、风险等级、是否需要审批、验证方式。
- `DeviceState`：规范化属性值，同时保留 adapter namespaced raw metadata 供诊断，不把 raw payload 暴露给 Agent。

补充 provider 健康字段：`last_sync_at`、`last_success_at`、`last_error_code`、`reauth_required`、`rate_limited_until`。实体必须用稳定复合键去重，刷新不得因名称变化创建重复设备。

命令继续走现有 `device.command` Transaction：Permission → Approval → Execute → Verify → Audit。禁止增加绕过 transaction 的“立即执行”HTTP 路由。

### 阶段 6：Home Assistant/Matter 兼容增强

这是华为/小米兼容的首选交付路径：

1. 根据 Home Assistant domain 和 attributes 映射统一能力，而不是根据品牌名写特例。
2. 覆盖 `light`、`switch`、`sensor`、`binary_sensor`、`climate`、`cover`、`fan`、`vacuum`、`humidifier`、`scene`。
3. 保存 HA `device_id`、`entity_id`、manufacturer、model、area；一个物理设备的多个 entity 在 UI 中聚合展示。
4. 支持 Home Assistant WebSocket 增量状态事件；断线后指数退避重连并做一次全量 reconcile。
5. 增加 adapter capability discovery；Matter、Xiaomi Home、华为相关 HA integration 暴露的实体都走同一映射。
6. UI 显示“来源：Home Assistant / Matter / Xiaomi / Huawei”以及是否本地控制；来源只用于展示，不改变权限模型。

测试使用录制后脱敏的 HA fixture，不能要求 CI 连接真实家庭网络。至少覆盖实体重命名、离线、删除、重复事件、重连和未知 domain 降级为只读实体。

### 阶段 7：小米兼容

交付顺序：

1. **正式支持路径**：文档和向导指导用户先在 Home Assistant 配置 Xiaomi Home/Matter，再由 HomeMind 连接 HA。
2. 增加 Xiaomi 设备映射 fixture，覆盖灯、插座、空气净化器、风扇、扫地机器人、窗帘和传感器。
3. 地区信息仅作为 provider metadata 展示；凭据留在 Home Assistant 或现有 encrypted secrets，不复制到普通配置。
4. 若实施直连，必须先提交单独 ADR，证明使用小米正式开放平台、列出 OAuth scope、区域、限流、refresh token 和账号注销流程；没有正式权限则该子阶段标记 `BLOCKED`，不得换用非官方库绕过。
5. 扫地机器人地图、摄像头流和门锁写控制不在本轮范围；机器人可支持启动/暂停/回充等低至中风险目录命令，仍需家庭策略控制。

### 阶段 8：华为兼容

交付顺序：

1. **正式支持路径**：优先让支持 Matter 或已接入 Home Assistant 的鸿蒙智联设备通过 HA adapter 出现。
2. 建立 Huawei manufacturer/model alias 与通用 capability fixture，但不根据营销名称硬编码 command。
3. 若申请到 HarmonyOS Connect 合作伙伴/云 API 权限，再新增 `HUAWEI_HARMONYOS_CONNECT` adapter；凭据、签名、region、project/device scope 使用现有 secrets/provider 体系。
4. 直连 adapter 必须实现 token 轮换、时钟偏差、签名失败、限流、设备分页、增量事件或轮询退避，并保存厂商 request id 便于审计排障。
5. 未取得官方权限时，UI 不展示不可用的“华为直连”选项，只展示 Home Assistant/Matter 路径说明。
6. 门锁、摄像头、安防系统首版只读；不接入视频流、不保存生物识别数据、不允许 Agent 解锁或撤防。

智能家居验收：

1. 同一物理设备的多个 HA entity 聚合为一个 HomeMind 设备。
2. 小米灯、插座、净化器、扫地机器人和窗帘能读取状态并生成受约束命令。
3. 支持 Matter 的华为/小米设备可经 HA 正常发现、同步与控制。
4. HA 断线重连后状态收敛，无重复实体、无命令重放。
5. 高风险命令无法绕过审批；未知命令、任意 service/topic 被拒绝。
6. provider 凭据不出现在日志、API response、审计正文或导出包。

验证：

```bash
uv run pytest tests/unit/homemind/test_smart_home.py tests/unit/homemind/test_smart_home_manager.py -q
uv run pytest tests/integration -k "smart_home" -q
uv run pytest tests/unit/homemind/test_family_transactions.py -q
cd dashboard && npx tsc -b
```

## 6. 主线三：AgentTeams 产品化

### 阶段 9：明确执行模型和持久化边界

先审计现有 Team service/manager/jobs，不重写协调器。补齐并持久化以下概念：

- `team_run`：一次用户目标的执行实例。
- `team_step`：DAG 节点，包含 assignee、depends_on、输入摘要、状态、attempt、deadline。
- `team_artifact`：步骤输出引用，只存资源 id/path/摘要，不复制大内容进数据库。
- `team_event`：追加式状态事件，用于 UI 恢复和审计。

状态建议：run 为 `PLANNING | WAITING_CONFIRMATION | RUNNING | PAUSED | SUCCEEDED | FAILED | CANCELLED`；step 为 `PENDING | READY | RUNNING | WAITING_HUMAN | SUCCEEDED | FAILED | SKIPPED | CANCELLED`。

必须规定：

- 只有 coordinator 能拆 DAG；成员不能递归创建无界 Team run。
- 默认最大成员数、最大并行 step、最大 DAG 深度、最大总 step、最大运行时间和 token 预算。
- 同一 step 使用幂等 execution key，服务重启不得重复派发已完成工作。
- 失败默认停止依赖链；仅显式声明可重试的 step 才自动重试。

### 阶段 10：计划确认、调度与恢复

1. coordinator 先产生结构化 DAG，并通过 Pydantic/domain validator 校验无环、成员存在、依赖存在和预算可行。
2. 默认进入 `WAITING_CONFIRMATION`；只有无副作用且用户策略允许时才可自动开始。
3. 调度器只 claim `READY` step，尊重并发上限和取消信号。
4. 成员输出必须包含结构化 handoff：摘要、artifact refs、未解决问题和建议下一步。
5. 服务重启后从数据库恢复；超时 lease 回到可重试或失败状态，不能永远 `RUNNING`。
6. 用户可暂停、继续、取消整个 run；取消要传播到尚未开始的 step，并尽力取消正在运行的 harness 调用。
7. 人工确认、工具审批和 AskUser 等 HITL 状态必须映射为 `WAITING_HUMAN`，恢复后继续原 step。

### 阶段 11：权限、成本和可观测性

- 每个成员继续以发起用户身份执行，并再次校验该用户对 agent、knowledge、connector、workspace 的权限。
- coordinator 不能把隐藏资源通过 handoff 泄露给无权成员。
- run 级预算包括 token、模型费用估算、墙钟时间和最大工具调用数；达到硬上限立即停止新 step。
- 记录 run/step 耗时、token、重试、失败原因、成员利用率和关键事件。
- 审计记录谁创建计划、谁确认、哪些 agent 执行、访问哪些资源、最终状态；不得记录 secret 或完整敏感 prompt。
- 增加 run 级 trace/correlation id，并贯穿 gateway、team manager、harness 和事件流。

### 阶段 12：API 与 Dashboard

建议 API：

```text
POST /api/teams/{team_id}/runs
GET  /api/teams/{team_id}/runs
GET  /api/teams/{team_id}/runs/{run_id}
POST /api/teams/{team_id}/runs/{run_id}/confirm
POST /api/teams/{team_id}/runs/{run_id}/pause
POST /api/teams/{team_id}/runs/{run_id}/resume
POST /api/teams/{team_id}/runs/{run_id}/cancel
GET  /api/teams/{team_id}/runs/{run_id}/events
```

UI 至少提供：DAG/列表双视图、步骤状态、负责人、依赖、耗时、预算、实时输出摘要、人工等待提示、暂停/继续/取消、失败节点重试。页面刷新后必须从持久化状态恢复，不依赖仅存在于浏览器内存的事件。

验收：

1. 线性、并行、汇聚三类 DAG 正确调度。
2. 环依赖、未知成员、越权资源和超预算计划在执行前被拒绝。
3. coordinator 或服务重启后不丢 run、不重复完成 step。
4. 一个并行 step 失败时，依赖节点不执行，无关分支按策略继续或停止。
5. HITL 暂停后可从原 step 恢复。
6. 取消在限定时间内停止新调度，UI 最终状态一致。

验证：

```bash
uv run pytest tests/unit/agents/test_team_service.py tests/unit/gateway/test_global_processor_team.py -q
uv run pytest tests/integration -k "team" -q
cd dashboard && npm test -- --run
cd dashboard && npx tsc -b
```

## 7. 主线四：Bridge 跨实例连接 Phase 4

### 阶段 13：协议版本、连接状态机与多连接稳定性

在现有 Phase 0–3 上增量实现，不创建第二套 bridge：

- 明确定义状态：`DISCONNECTED | CONNECTING | AUTHENTICATING | ONLINE | DEGRADED | REAUTH_REQUIRED | INCOMPATIBLE | DISABLED`。
- hello/ack 携带 protocol version、Octop version、能力位、最大 frame、压缩能力和实例 id。
- 协议 major 不兼容直接拒绝；minor 通过能力协商降级。
- 同一 `connection_id` 双向同时 dial 时使用确定性规则保留一条连接，关闭另一条；规则基于实例 id，不基于到达时序。
- 每个 connection 独立重连、退避、熔断和队列，单一坏节点不得拖垮其他连接。
- 限制每用户连接数、每连接在途请求数、队列字节和单帧大小。

### 阶段 14：token 刷新与凭据生命周期

1. access token 到期前带随机抖动刷新；刷新请求 single-flight，避免并发风暴。
2. refresh 失败分为临时错误、凭据撤销和协议错误；只有临时错误重试。
3. 必要时使用加密保存的密码重新登录；若管理员禁用密码回退，则进入 `REAUTH_REQUIRED`。
4. token 更新使用 compare-and-swap 或版本字段，避免双连接覆盖新 token。
5. 用户改密码、注销连接或删除 connection 后，立即关闭 WS、取消在途请求并清除加密凭据。
6. UI 显示“需要重新认证”，允许重新输入凭据，不把服务端错误堆栈返回浏览器。

### 阶段 15：path policy、安全与大流量

- 将 tunnel policy 改为显式 method + path template + capability allow-list；默认拒绝。
- 拒绝认证、用户管理、Bridge 管理、备份恢复、系统升级和任意本地文件路径。
- path rewrite 前后各做一次校验，防止编码、双斜杠、`..`、大小写和 query 绕过。
- header 使用 allow-list；去除本地 JWT、cookie、hop-by-hop headers 和未知 `X-Forwarded-*`。
- 每个 tunnel request 绑定连接 owner、远端身份、deadline、最大响应字节和审计 id。
- 实现公平多路复用：控制帧、turn 流、HTTP、小文件和大上传分别限流，2 GB 上传不能饿死 ping 或聊天 token。
- 复用主线一 upload session；应用分片再切 transport frame，支持 cancel、timeout 和断线恢复。

### 阶段 16：可观测性、管理面与故障演练

指标：

- 连接状态、在线时长、重连次数、认证刷新结果。
- tunnel 请求数、延迟、状态码、超时、取消、在途数和传输字节。
- turn 首 token 延迟、中断次数、丢弃帧和 backpressure 时间。
- 每连接队列深度、限流和熔断状态。

日志必须带 `connection_id`、request/turn id 和方向，但不得包含密码、token、附件正文或完整敏感 query。Dashboard 增加最近错误、协议版本、能力、最后在线、重连状态和“复制诊断摘要”；不直接显示 secret。

故障演练：

1. 两端同时 dial，只保留一条稳定连接。
2. 网络抖动、断网 10 分钟、服务重启后自动恢复，且不会产生重连风暴。
3. access token 过期时在途 turn 不被重复执行；无法刷新时进入重新认证状态。
4. 协议 major 不兼容给出明确升级提示。
5. 恶意 path/header、超大 frame、慢速发送方和无界响应被拒绝或限流。
6. 多个远端节点并行使用，其中一个故障不影响其他节点和本地 agent。
7. 远程历史、附件、预览、聊天取消与浏览器 handoff 保持 Phase 0–3 行为。

验证：

```bash
uv run pytest tests/unit/bridge -q
uv run pytest tests/integration -k "bridge" -q
cd dashboard && npm test -- --run
cd dashboard && npx tsc -b
```

## 8. 阶段 17：跨主线端到端验收

必须搭建两个临时 Octop/HomeMind 实例，不依赖生产账号：

1. 实例 A 通过 Bridge 使用实例 B 的远程 expert。
2. 从 A 向 B 断点续传大附件，中断并重启两端后恢复；B 中 hash 正确，A 不保留最终副本。
3. Team 中一个本地成员与一个远程成员协作。若当前产品决定不允许跨实例 Team，必须在计划校验阶段明确拒绝并给出可理解错误，不能运行到中途失败。
4. Home Assistant fake server 向 HomeMind 推送小米/Matter 设备状态；Team 只能生成目录内命令，高风险设备保持只读。
5. 所有审批、命令、Team step 和 Bridge tunnel 都可用 correlation id 串联审计，但任何日志均不含凭据。
6. Windows 与 Linux 均运行核心测试；路径、临时目录和文件锁测试不得假定 POSIX。

最终验证：

```bash
make format-all
make lint
make typecheck
uv run pytest -m "not live"
cd dashboard && npx tsc -b
make build-frontend
make all
```

## 9. 明确不在本轮范围

- 自研 Matter controller、Thread border router、Zigbee/Z-Wave coordinator。
- 非官方小米/华为账号登录、token 提取、局域网私有协议逆向。
- 摄像头视频流、人脸识别门禁、门锁解锁、安防撤防、医疗设备控制。
- 上传文件级去重、跨用户秒传、P2P 上传和对象存储 multipart 直传优化。
- AgentTeams 自动修改自身 prompt/skill、无上限递归 delegation、跨实例共享完整 workspace。
- Bridge 浏览器直连远端、双边复制完整聊天数据库或绕过远端鉴权。

## 10. MiniMax 每阶段汇报格式

每次只执行一个阶段，并按以下格式汇报：

1. 本阶段结论。
2. 实际修改文件。
3. 数据库/API/状态机变化。
4. 权限、安全与隐私检查。
5. 新增或更新的测试。
6. 执行的验证命令及完整结果摘要。
7. 未解决问题和是否阻塞下一阶段。
8. 下一阶段建议；不得擅自扩大范围。

第一条执行提示词：

```text
请执行 docs/requirements/homemind-next-capabilities-plan.md 的“阶段 0：基线审计与契约冻结”。
先完整阅读根目录 AGENTS.md 和阶段 0 指定文件。只做只读审计、功能矩阵和必要的兼容性契约测试；
不要提前实现断点续传、厂商 adapter、Team run 或 Bridge Phase 4。发现计划与代码不一致时以代码为准，
明确报告并最小化修改。完成后运行阶段 0 的验证命令，按第 10 节格式汇报，不提交、不推送。
```

# HomeMind 下一阶段能力 · 阶段 0 基线审计与功能矩阵

> 对应 `docs/requirements/homemind-next-capabilities-plan.md` 的**阶段 0**。
> 本文只用于实施：记录基线现状与缺口，**不代表任何后续能力已交付**。
> 审计日期：2026-10-08。审计方式：只读代码审计 + 基线测试。
> 冲突时以代码为准；本文只做记录，不改行为。

## 0. 执行摘要（先看这段）

计划文档的 §1 前提是「在既有能力上扩展」。审计结论相反：**四条主线中有三条的目标链路目前根本不可达或不存在**。若按原文进入实现，会在不可达链路上开工。

| 主线 | 计划假设 | 实际基线 | 阻塞级别 |
|------|----------|----------|----------|
| 大文件断点续传 | 复用现有上传服务 | 现有 multipart **全量入内存**，且 `workspace/upload` 无大小上限 | 可开工（需先修基线） |
| 华为/小米兼容 | 扩展既有 adapter 接口 | smart_home router **未挂载**，`secret_ref` **无解析实现**，adapter 从未装配 | **阻塞**，须先接线 |
| AgentTeams 产品化 | 不重写协调器，加固持久化 | **无协调器、无 DAG、无 run/step 持久化**；现有是提示词驱动 | **阻塞**，等于新建 |
| Bridge Phase 4 | 在 Phase 0–3 上增量 | 协议版本为单整数全等比较；8 态状态机 0 态存在 | 可开工（需裁决迁移） |

另有两项**跨主线阻塞项**需先决策：

1. **迁移编号**：`020_skill_copy_policy`（octop）与 `024_transaction_device_await`（homemind）均已随 1.0.2b6 发布，**无未发布迁移可折叠**。三条主线都必须开新号（octop `021`+、homemind `025`+），且**不得共用编号空间**。
2. **运行中的 venv 是过期的**：`octop` 以非可编辑方式安装为 **1.0.1**（`.venv/Lib/site-packages/octop`），源码树已是 1.0.2b6。`uv run pytest` 才是正确入口（详见 §6.1）。

## 1. 环境与基线验证

### 1.1 基线测试结果（阶段 0 §3.3 四条命令）

| 命令 | 结果 |
|------|------|
| `tests/unit/api/test_upload_limit.py` + `test_smart_home.py` + `test_smart_home_manager.py` | **55 passed**（14.5s） |
| `tests/unit/agents/test_team_service.py` + `tests/unit/gateway/test_global_processor_team.py` | **56 passed**（11.8s） |
| `tests/unit/bridge` | **71 passed**（8.9s） |

结论：四条主线的现有测试面全绿，可作为兼容冻结基线。

### 1.2 迁移水位

| 包 | 当前水位 | 最高迁移 | 断言方式 | 折叠可能性 |
|----|----------|----------|----------|-----------|
| `src/octop` | **v20** | `020_skill_copy_policy.sql` | **字面量** `assert v == 20`（`tests/unit/db/test_db_pool.py:102`，另在 `:184,323,348,383,466,672` 重复） | 无未发布迁移，**不可折叠** |
| `src/homemind` | **v24** | `024_transaction_device_await.sql` | **派生式** `== max(_discover("sqlite"))`（`tests/unit/homemind/test_family_calendar.py:119-124`） | 无未发布迁移，**不可折叠**；新增迁移无需改数字 |

HomeMind 另有方言配对断言 `tests/unit/homemind/test_migration_pairing.py:33-40`。

## 2. 主线一：大文件断点续传（阶段 1–4）

### 2.1 现状

- 唯一 chat 附件端点 `POST /api/agents/{agent_id}/upload`（`src/octop/api/routers/uploads.py:59-83`），响应 6 字段 `{path, workspace_path, url, access_url, filename, media_type}`，无 `size`/`file_id`。
- 限制三层：`server.services.config.max_upload_bytes`（`uploads.py:68-72`）→ `read_upload_capped`（`api/common/upload_limit.py:32-54`）→ `validate_inbound_size`（`infra/gateway/media/inbound_store.py:322-330`）。超限映射 `ATTACHMENT_TOO_LARGE → 413`（`infra/errors.py:75,205`）。
- 落盘目的地 5 处：chat `inbound/`、workspace 二进制、workspace zip 导入、knowledge `{kb_id}/`、family asset（**仅本地扫描 `assets/scan`，无 HTTP 上传入口**）。
- 前端：`dashboard/src/api/request.ts:552-589` XHR + `onprogress`，**仅百分比**，无速度/暂停/续传；限制来自 `useServerUploadLimit()`（读 `GET /api/settings/upload`）。
- **无任何分片/续传机制**：全仓 `resumable|part_number|upload_session|tus` 仅命中计划文档自身与无关项。

### 2.2 功能矩阵

| 能力 | 现状 | 缺口 |
|------|------|------|
| chat 附件上传 | 可用 | 无 size 回显、无审计 |
| 大小限制 | HTTP 层已生效（本次已补契约测试） | 全量入内存（`upload_limit.py:54` `b"".join(chunks)`），不满足 2 GB 验收 |
| `workspace/upload` | `api/routers/workspace.py:304-327` | **完全没有 `max_upload_bytes` 校验**，`await file.read()` 无界 |
| knowledge 上传 | 有 ACL + 专用错误码 | 全量入内存 |
| family 资产上传 | **无 HTTP 入口** | 计划 §3.3 的首批目标不存在 |
| staging 根 | `PathLayout` 无 `uploads` 属性 | 待新增 |
| 上传指标 / 审计 | 无 | 待新增 |

### 2.3 计划与代码不一致

1. 「小文件继续走旧路径」隐含旧路径内存安全 —— 实测全量入内存，旧路径本身承载不了大文件。
2. 计划 §4 首批接入 `Family Asset/照片` —— **服务端无 family 上传 API**。
3. 「每次请求校验 owner」 —— `save_attachment` 显式 `del owner_id`（`api/common/attachments.py:61`），落盘无 owner 记录。
4. 「目标路径用现有 workspace path normalizer」 —— `workspace_api_path`（`api/common/workspace.py:94-99`）仅做 `/`→相对路径，**不做 `..` 收敛**；真正收敛在下游 `_workspace_io_path`。
5. `expected_sha256` 无既有校验可复用。
6. **chat 上传用 `owner_only=False`（`uploads.py:67`），`workspace/upload` 用 `owner_only=True`（`workspace.py:316`）** —— 两条上传路径权限强度不一致，非同一基线。

## 3. 主线二：华为/小米及广泛智能家居兼容（阶段 5–8）

### 3.1 现状

- Adapter 契约 `SmartHomeAdapter` 是 `runtime_checkable Protocol`（`infra/family/smart_home.py:136-186`）：`probe/list_entities/get_state/commands_for/preview_command/execute_command/verify_command`。**无 free-form `call_service`**（安全上是好事）。
- 仅两个 adapter：`HomeAssistantAdapter`（`smart_home_adapters.py:107`，纯 REST 轮询）、`MqttSmartHomeAdapter`（`:305`，需外部注入 `_publisher`）。命令目录 `HA_COMMANDS` 仅 light/switch/scene/climate 7 条，`MQTT_COMMANDS` 仅 light/switch 5 条。
- 持久化：`022_smart_home.sql` 两表。实体去重键 `(provider_id, external_entity_id)`（`:61-62`）—— **改 friendly_name 不产生重复**（name 不在键内）。
- 高风险按 HA domain 判定：`BLOCKED = {lock, camera, alarm_control_panel} ∪ READ_ONLY_DOMAINS`，`HIGH = {climate, cover, fan, humidifier}`，写前直接拒绝（`smart_home.py:216-230`）。命令目录刻意不含 lock/camera。
- `device.command` Transaction 完备（`transaction_actions/highrisk.py:51-157`，含 `AWAITING_DEVICE` + 审计），**但只认 `family_devices.device_id`，与 `smart_entities` 无任何映射**。

### 3.2 功能矩阵

| 能力 | 现状 | 缺口 |
|------|------|------|
| HTTP API | `api/routers/smart_home.py` 9 端点已实现 | **未在 `api/app.py` 挂载**，全仓只被自身引用 → 死代码 |
| 连接装配 | `adapter_factory` 形参存在（`smart_home.py`） | 生产路径恒传 `None`（`smart_home.py` router `:116-120`），`_adapter()` 恒返回 `None`（`smart_home_manager.py:277-282`） |
| `secret_ref` 解析 | 只存引用名 | **全仓无解析实现**，`src/homemind` 不引用 `SecretRepo` |
| adapter execute/verify | 方法已定义 | **无任何生产调用方**；写入链路不可达 |
| 去重 | name 变更安全 | **entity_id 变更产生新行且旧行不清理**，`sync_entities` 无 reconcile/剪枝 |
| 设备聚合 | 无 | 一个物理设备多 entity 永远多行 |
| provider 健康 | `last_seen_at` + `last_error` | `record_probe` 成功失败都写 `last_seen_at`，不是「最后成功」；缺 `last_success_at`/`last_error_code`/`reauth_required`/`rate_limited_until`；无 PATCH 路由 |
| HA 传输 | 一次性 REST 轮询 | 无 WebSocket、无退避重连、无全量 reconcile、无脱敏 fixture |
| 测试 | 30 adapter 单测 + 24 manager 单测 | HTTP 零覆盖、无 integration 测试 |

### 3.3 计划与代码不一致

1. 计划 §5「扩展现有 adapter 接口」隐含链路可用 —— 实际 **adapter 从未被生产装配**。
2. 计划 §5「命令继续走现有 `device.command` Transaction」暗示已接通 —— 实际需**新增接线**，`DeviceCommandHandler` 不认识 smart_entities。
3. 计划 §3.3 契约测试「现有 provider 创建/刷新/预览行为不变」—— HTTP 面未挂载，**不存在可冻结的 API 契约**。
4. 计划 §6 要求覆盖 10 个 domain —— 现有目录仅 4 个；`cover`/`fan`/`humidifier` 被标 HIGH 却无命令项。
5. 计划 §5 提到 `RISK_MEDIUM` —— `smart_home.py:51` 定义了常量，但 `risk_for` **永不返回它**。
6. 计划 §7「凭据留在现有 encrypted secrets」—— 现状是**根本没接**，而非「已有」。
7. 计划 §5 的验证命令 `uv run pytest tests/integration -k "smart_home"` **恒空跑**（无匹配文件）。

## 4. 主线三：AgentTeams 产品化（阶段 9–12）

### 4.1 现状

- **无独立 team 表**。团队 = `agents.kind='team'` 的行（`016_agent_teams.sql:4`）；成员名单存在**主持人工作区文件** `{workspace}/.octop/manifest.json`（`teams/service.py:22,152-168`）。
- **全仓 grep `team_run|team_step|team_artifact|team_event` 零命中。**
- 协调者是**一个 LLM 主持人，不是调度器**：分工逻辑写在 `template/AGENTS.md:9-20` 的提示词里。派工 = 主持人调 `ask_agent`（`team_manager.py:1590-1609`），成员执行 = 单个 `async for chunk in stream(...)`（`:334`），收口 = inbox 回调（`:1536-1574`）。**没有依赖表达、没有汇聚、没有 DAG 校验。**
- 在途状态只在 `TeamJobTracker` 两个内存 dict（`teams/jobs.py:16-19`），文件头自述「Lost on process restart」（`:11-13`）。`docs/expert-teams.md:162` 把 inbox 持久化列为非目标。
- **无任何 `/runs` 端点**（全仓 grep `/runs` in `api/routers` 零命中）。

### 4.2 功能矩阵

| 能力 | 现状 | 缺口 |
|------|------|------|
| team_run/step/artifact/event 持久化 | 无 | 全部 |
| DAG 构建 | 无（提示词自由拆解） | 全部 |
| 调度器 | 无（inbox 按 callee 并行） | READY claim、并发上限 |
| 确认门 `WAITING_CONFIRMATION` | 无 | 全部 |
| HITL → `WAITING_HUMAN` | 有 turn 级 HITL（`processor.py:1146`） | 未映射到 step 状态，无查询 API |
| pause/resume/cancel | 无（取消只停主持人本轮） | 全部 |
| 幂等 execution key / lease | 无 | 重启重复派发风险 |
| 成员上限 | 无（仅 `TEAM_MIN_MEMBERS=2`） | `TEAM_MAX_MEMBERS` |
| 并行/深度/总 step 上限 | 无 | 全部 |
| run 级 token/时间/工具预算 | 无（仅 per-user 全局配额） | 全部 |
| 派工时权限复检 | **仅编制写入时**（`service.py:198`） | 派发路径无复检 |
| handoff 资源泄露防护 | 无 | `template/AGENTS.md:31` 反而要求「把材料复制进派工正文」 |
| team 审计 | 仅 `agent.stream.error` | 规划/确认/执行/资源/终态全缺 |
| secret 脱敏 | 无（仅 6000 字截断） | 缺 |
| Dashboard run/DAG 视图 | 无（仅编制管理 + 聊天气泡） | 全部 |

### 4.3 计划与代码不一致

1. 计划 §9「不重写协调器」—— **协调器不存在**。产品化等于新建调度层，不是改造。
2. 计划 §9「同一 step 使用幂等 execution key」—— 当前无任何等价物可加固。
3. 计划 §11「每个成员**继续**以发起用户身份执行」—— 用户身份只是**透传**（`team_manager.py:1556`），权限**从未**在派发时校验；成员工具集用的是**成员自己**的配置（`manager.py:3042-3053` 回落到 `row.user_id`）。
4. 计划 §11「coordinator 不能泄露隐藏资源」与 `template/AGENTS.md:31` 的现有准则**直接冲突**，须改写准则文本。
5. 计划 §12 新增 `/api/teams/{team_id}/runs/*` 与 `GET /api/teams/{team_id}` **存在路由顺序冲突风险**：参数段会吞掉静态段 `/runs`，须显式注册顺序或改前缀。
6. 新 step 模型与现有 `team_wrapup` 气泡模型并存会产生两套渲染路径。

### 4.4 冻结的既有 API 契约

```
GET    /api/teams              → TeamRecord[]
POST   /api/teams              → TeamRecord
GET    /api/teams/template     → {name, content}[]
GET    /api/teams/{team_id}    → TeamRecord
PATCH  /api/teams/{team_id}    → TeamRecord
DELETE /api/teams/{team_id}    → 204
GET    /api/agents             → team 行附加 member_ids
```

`TeamRecord` 15 字段、`members[]` 8 字段（`teams/service.py:271-301`）、错误码 `TEAM_NOT_FOUND(404)` / `TEAM_MEMBERS_TOO_FEW(400)` / `TEAM_MEMBER_INVALID(400)` / `TEAM_MEMBER_BUSY(409)` / `TEAM_NOT_SHAREABLE(400)`（`infra/errors.py:266-270`）—— **本阶段已用集成测试冻结，见 §6.2**。

## 5. 主线四：Bridge Phase 4（阶段 13–16）

### 5.1 现状

- 协议版本是**单整数** `_BRIDGE_PROTOCOL_VERSION = 1`（`infra/bridge/ids.py:11-13`），hello 帧 6 字段（`manager.py:759-767`），**全等比较**（`:787, :843`）—— minor 不匹配也直接断连。无 capability/max frame/压缩/实例 id 协商。
- 持久化 status 仅 4 个自由字符串：`disconnected` / `connecting` / `connected` / `error`（`019_bridge_connections.sql:15`，写入点 `manager.py:381,429,442,451,468,649,667,709,803,880,928`）。**计划要求的 8 态全部不存在。**
- 双向同时 dial：`if old is not None and old is not session: await old.close()`（`manager.py:911-915`）—— **到达时序依赖（last-writer-wins），非实例 id 确定性裁决**。
- token：Fernet 加密列 `credential_blob` + `access_token_blob` + `token_expires_at`。**无 refresh token 端点**；`_ensure_peer_token` 固定 60s 阈值**无 single-flight、无抖动、无错误分类**（`manager.py:722-748`）；`update_credentials` 是无条件 `COALESCE` 覆盖（`repos/bridge_connections.py:156-173`）—— **双连接可互相覆盖新 token**。
- tunnel policy 是 **method + path 正则 allow-list + default deny**（`tunnel_policy.py:53-96`），已正确拒绝 `/api/auth/**`、`/api/users*`、`/api/bridge/**`、`/api/admin/**`、backup/restore、setup（测试 `tests/unit/bridge/test_tunnel_policy.py:74`）。header 是 **denylist 6 项**（`http_tunnel.py:19-28`）而非 allow-list；**`X-Forwarded-*` 未剥离**，代理侧 cookie 未剔除（`bridge_proxy.py:181-202`）。
- 无帧优先级/配额；出站 `max_size=32MB` 硬编码（`manager.py:756`）；`_pending` 无上限（`transport.py:60`）；单请求超时硬编码 120s。
- **指标 0，审计 0**。Dashboard 仅显示 status/auto_reconnect/last_error/专家数，且错误文案直出 `row.last_error`。
- 远程附件上传 = **整包 base64 单帧**（`transport.py:165-166`），受 32MB 帧上限与 `max_upload_mb` 双重约束。无分片/续传/cancel。

### 5.2 功能矩阵

| 能力 | 现状 | Phase 4 缺口 |
|------|------|-------------|
| 协议版本 | 单整数全等比较 | major/minor + capability 协商 |
| 状态机 | 4 个自由字符串 | 8 态 + enum |
| 双向 dial 冲突 | last-writer-wins | 实例 id 确定性裁决 |
| 重连退避 | 每连接独立 supervisor，5 次后**永久熔断**（`manager.py:41-42,674-687`），硬编码不可配 | 半开恢复、抖动、可配 |
| 连接排队 | 无上限 | 在途上限、队列字节上限、公平调度 |
| token 刷新 | 到期重登 | single-flight、抖动、错误分类、CAS |
| path policy | 正则 allow-list + default deny | method+template+capability 显式模型、rewrite 前后二次校验 |
| header | denylist | allow-list，剥离 cookie / `X-Forwarded-*` |
| 多路复用公平性 | 按 `type` 前缀分帧 | 控制帧/turn/HTTP/大文件分通道限流 |
| 指标 / 审计 / trace id | 全 0 | 全部 |
| `bridge:{cid}:{aid}` 映射 | 已实现且本地路由不受影响（`bridge_proxy.py:220-224`，测试 `test_bridge_proxy_local.py:47,106`） | **勿改语义** |

### 5.3 计划与代码不一致

1. `docs/bridge.md:54-58` 的模块表列出 `connections.py`/`router.py`/`chat_bridge.py` —— **三个文件均不存在**，实际是 `manager.py` + `api/middleware/bridge_proxy.py` + `api/routers/chat/ws.py`。
2. `docs/bridge.md:121-127` 描述的 `chunks` / `tunnel.cancel` 帧 —— **未实现**（`transport.py` 无 cancel 分支）。
3. `docs/bridge.md:195` Phase 0 声称的「隧道 ping/health」—— infra/bridge 无 ping/pong 实现。
4. `docs/bridge.md:98` 列出的 `refresh` 列 —— `019` 表无该列。
5. 计划 §7「聊天取消保持 Phase 0–3 行为」—— 代码显式返回 `"cancel not supported on bridge yet"`（`chat/ws.py:245-247`）。
6. 计划 §3.2「不得机械新增版本」对 octop **不适用**：`019` 已发布且已含全部现有列，Phase 4 字段只能落 `021_*`。

### 5.4 冻结的既有 API 契约（20 条，摘要）

管理面 12 条（`api/routers/bridge.py`）+ 只读代理 6 条（providers/resolved、active-model、knowledge-bases、knowledge-bases/capability、browser/env-status、browser/harness-sessions、browser handoff）+ WS 2 条（`/api/bridge/ws`、`browser-stream/ws`）+ chat WS 1 条（`/api/agents/bridge:{cid}:{aid}/chat/ws`）。`BridgeConnection` 12 字段（`manager.py:211-226`）。

## 6. 本阶段新增的兼容性契约测试

### 6.1 环境注意事项（重要）

`octop` 在 `.venv` 中是**非可编辑安装的 1.0.1 拷贝**，源码树已是 1.0.2b6。直接用 `.venv\Scripts\python.exe -m pytest` 会导入旧包，出现 `ModuleNotFoundError: octop.infra.agents.teams`。同时，若已有 `homemind run` 开发服务器在跑，`uv run pytest` 会被 uv 环境锁阻塞（本次实测空转 6 分钟无输出）。

**Windows locale 陷阱**：本机默认编码是 GBK。若测试用 `Path.read_text()`（不显式指定 `encoding=`）读取含非 ASCII 注释的 SQL 文件，会抛 `UnicodeDecodeError: 'gbk' codec`。这会让 `tests/unit/db` 出现约 19 个**与本次改动无关**的假失败。加 `PYTHONUTF8=1` 即恢复。本地验证必须带该变量：

```powershell
$env:PYTHONPATH="src"; $env:PYTHONUTF8="1"
.\.venv\Scripts\python.exe -m pytest <paths> -q -p no:cacheprovider
```

正确入口仍是 **`uv run pytest`**。本次因上述两个原因改用：

```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m pytest <paths> -q -p no:cacheprovider
```

### 6.2 新增测试

| 文件 | 用例 | 冻结的契约 |
|------|------|-----------|
| `tests/integration/test_team_api_contract.py`（新增） | 8 | `TeamRecord` 15 字段 + `members[]` 8 字段精确 key 集；CRUD 响应；`member_ids` 全量替换语义；`TEAM_MEMBERS_TOO_FEW` / `TEAM_MEMBER_INVALID`（含 `details.member_agent_ids`）/ `TEAM_NOT_FOUND`；非 team agent 不可当 team 寻址；**跨用户 403 FORBIDDEN**；`GET /api/agents` 的 `member_ids`；`/api/teams/template` 只返 `.md` |
| `tests/integration/test_upload_api.py`（追加 3 例） | 3 | multipart 在 HTTP 层仍强制 `max_upload_bytes` → 413 `ATTACHMENT_TOO_LARGE` + `details.max_mb`；限额内仍 200；`GET /api/settings/upload` 广告值与实际强制值一致 |

结果：新增 **11 passed**（8 + 3），原上传用例 3 例仍绿。

## 7. 阶段 0 未解决项与下一阶段建议

### 7.1 需先决策的阻塞项

1. **smart_home 是否补挂载 router + 何时实现 `secret_ref` 解析？** 不先接线，阶段 5–8 会在不可达链路上开工。（建议：作为阶段 5 的第一个子任务显式承接，而不是隐含前提。）
2. **octop 迁移是否接受新增 `021_*`？** 三条主线都需要新迁移，`019`/`020`/`016` 均已发布不可折叠。需同步改 `tests/unit/db/test_db_pool.py` 的 6 处字面量断言。
3. **`PROTOCOL_VERSION` 从单整数升到 `major.minor` 的过渡期？** 会破坏现网互通；双边都升级前需保持旧行为。
4. **`AGENTS.md` §5 列出 `api/errors.py`，该文件不存在**（实际错误信封在 `api/app.py:77-93`）。属文档漂移，建议单独提交修正，本阶段未改。

### 7.2 建议的下一阶段

**阶段 1：定义上传会话协议与持久化模型。** 理由：它是四条主线里唯一不存在「可达性争议」的（现有 multipart 链路虽内存不安全，但可用），且 Bridge 远程附件、设备文件同步都要复用同一上传会话协议，先做收益最大。开工前建议先补两个基线修复（各自独立、向后兼容）：`workspace/upload` 缺失的 `max_upload_bytes` 校验，以及 chat / workspace 两条上传路径的 `owner_only` 强度对齐。
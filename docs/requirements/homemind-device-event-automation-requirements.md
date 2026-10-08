# HomeMind 设备事件总线与家庭自动化需求

**状态：** Draft  
**适用范围：** HomeMind 智能家居、家庭设备运行时、Family Transaction  
**目标版本：** 待排期  
**依赖：** 统一设备能力模型、现有 Family Transaction、Home Assistant/MQTT adapter  
**非目标：** 自研 Matter Controller、替代 Home Assistant、引入外部消息队列

---

## 1. 背景

HomeMind 已经具备以下基础能力：

- 通过 Home Assistant 和 MQTT adapter 获取智能家居实体状态；
- 通过设备运行时管理手机、电脑等配对设备及其声明的 capability；
- 通过 Family Transaction 完成权限判断、人工审批、执行、验证和审计；
- 通过 `FamilyEventBus` 向已授权的 Dashboard 用户推送家庭通知事件。

当前缺少的是一条可靠、可恢复的设备事件处理链。智能家居实体主要通过主动刷新同步，设备状态变化尚不能稳定地驱动家庭规则；现有 `FamilyEventBus` 面向实时 UI 通知，事件不持久化，也不承担规则执行、断线补偿或重放职责。

本需求在不改变现有安全边界的前提下增加：

1. provider 增量事件接入；
2. 标准化、持久化且可去重的设备事件日志；
3. 设备当前状态投影；
4. 最小可用的“触发器—条件—动作”家庭自动化；
5. 自动化动作与 Family Transaction 的强制集成；
6. 自动化执行的恢复、限流、防循环、审计和可观测性。

## 2. 产品原则

### 2.1 HomeMind 是安全编排层，不是设备协议中枢

Home Assistant、MQTT broker 或设备运行时仍是设备通信与原始状态的来源。HomeMind 负责把外部状态标准化，并在家庭权限、策略和审批约束下编排动作。

### 2.2 状态变化不等于执行授权

任何设备事件都只能触发一次自动化评估，不能直接调用 adapter。所有产生副作用的动作必须通过现有 Family Transaction：

```text
Device Event
    ↓
Automation Evaluation
    ↓
Permission / Risk Policy
    ↓
Family Transaction
    ↓
Approval（如需要）
    ↓
Execute → Verify → Audit
```

不得新增绕过 Transaction 的“立即执行”、自由 HA service call 或自由 MQTT publish 路径。

### 2.3 至少一次接收，效果至多一次

provider 断线重连、进程崩溃和消费者超时可能导致同一个事件被重复接收，因此事件处理采用至少一次语义。去重键、自动化运行键和 Transaction 幂等键必须保证同一逻辑事件不会重复产生副作用。

### 2.4 本地优先、无外部必需服务

事件日志、状态投影和自动化队列使用现有 SQLite/PostgreSQL 数据库实现。首版不依赖 Kafka、Redis、RabbitMQ 或云端规则引擎。

### 2.5 安全失败

无法确认事件身份、状态新鲜度、权限、风险或动作结果时，系统应停止执行并留下可诊断状态，不能默认放行。

## 3. 范围

### 3.1 本期范围

- Home Assistant WebSocket 增量状态事件；
- MQTT allow-list 内的订阅事件；
- HomeMind 配对设备的在线、离线、状态和命令结果事件；
- 标准设备事件信封与持久化事件日志；
- 当前状态投影和 provider 断线后的全量 reconcile；
- 状态、数值阈值、在线状态和时间触发器；
- 设备状态、时间段、家庭模式等确定性条件；
- 目录化设备命令、通知和低风险家庭任务动作；
- 自动化草稿、启用、禁用、手动试跑和执行历史；
- Transaction 审批、执行、验证和审计链路；
- Dashboard 管理和运行状态展示。

### 3.2 明确不在本期范围

- 任意 Python、JavaScript、Jinja 或 Shell 脚本；
- 用户提交任意 SQL、HA service、MQTT topic 或未登记 HTTP URL；
- 复杂 DAG、循环、并行分支、等待节点和长流程编排；
- 基于 LLM 的非确定性触发条件；
- 跨家庭自动化；
- 跨 HomeMind 实例的全局事件总线；
- 自研 Matter、Zigbee、Z-Wave 或 Thread 协议栈；
- 摄像头视频流、门锁开锁、安防撤防、燃气阀写控制；
- 对外提供通用 webhook 自动化平台。

## 4. 术语与边界

| 术语 | 定义 |
|---|---|
| Provider event | Home Assistant、MQTT 或设备运行时产生的原始事件。 |
| Device event | 经 HomeMind 校验、标准化、持久化后的设备领域事件。 |
| State projection | 每个设备属性当前已知值及其版本、新鲜度和来源。 |
| Automation | 一个由触发器、条件和有序动作组成的家庭规则。 |
| Automation run | 某条自动化针对某个触发事件的一次评估和执行实例。 |
| Action run | Automation run 中单个动作的执行实例。 |
| Notification event | 经现有 `FamilyEventBus` 推送给 Dashboard 的短生命周期 UI 事件。 |

设备事件日志与现有 `FamilyEventBus` 必须职责分离：

- 设备事件日志持久化、可恢复、供状态投影和自动化消费；
- `FamilyEventBus` 继续负责按家庭权限向在线用户推送通知；
- 领域服务不得直接操作 WebSocket；
- 自动化执行不得依赖某个 Dashboard 用户在线。

## 5. 总体架构

```text
Home Assistant WS ─┐
MQTT subscription ─┼─→ Provider Adapter ─→ Normalizer ─→ Device Event Log
Device Runtime ────┘                              │              │
                                                 │              ├─→ State Projector
                                                 │              └─→ Automation Matcher
                                                 │                       │
                                      reconnect reconcile               ↓
                                                                    Run Planner
                                                                         │
                                                       ┌─────────────────┴──────────────┐
                                                       │                                │
                                                no side effect                 Family Transaction
                                                       │                                │
                                                       └────────→ Audit / Notification ←┘
```

### 5.1 模块边界

建议新增或扩展的领域模块均位于 `src/homemind/infra/`：

- `family/device_events.py`：事件信封、标准化校验和写入服务；
- `family/device_event_runner.py`：claim、投影、匹配和恢复；
- `family/automations.py`：自动化 CRUD、校验和权限；
- `family/automation_runner.py`：运行状态机和动作调度；
- `db/repos/device_events.py`：设备事件与消费游标 SQL；
- `db/repos/automations.py`：规则、运行和动作运行 SQL；
- `api/routers/automations.py`：薄 HTTP 适配层。

具体文件划分可在实施时调整，但必须保持：API 不拥有规则评估、状态机、重试、风险计算或 SQL。

## 6. 标准设备事件模型

### 6.1 事件信封

每条持久化设备事件至少包含：

| 字段 | 类型 | 要求 |
|---|---|---|
| `event_id` | string | HomeMind 公共 ULID，唯一。 |
| `family_id` | string | 必填，用于权限和分区。 |
| `provider_id` | string/null | 外部 provider 事件必填；本地系统事件可为空。 |
| `device_id` | string | HomeMind 统一设备 ID。 |
| `entity_id` | string/null | 具体实体 ID；设备级在线事件可为空。 |
| `event_type` | enum | 见 6.2。 |
| `property` | string/null | 状态变化对应的规范属性名。 |
| `old_value` | JSON/null | 已知旧值；不能确定时为空。 |
| `new_value` | JSON/null | 新值。 |
| `unit` | string/null | 规范单位。 |
| `source_event_id` | string/null | provider 提供的原始事件 ID。 |
| `source_sequence` | string/null | provider 序列号或游标。 |
| `dedupe_key` | string | provider、设备、原始 ID/序列或稳定内容组成的去重键。 |
| `occurred_at` | timestamp | 事件在来源处发生的 UTC 时间。 |
| `received_at` | timestamp | HomeMind 接收的 UTC 时间。 |
| `correlation_id` | string/null | 与命令、Transaction、自动化 run 串联。 |
| `causation_id` | string/null | 直接导致本事件的 event/run/transaction ID。 |
| `quality` | enum | `GOOD | STALE | UNKNOWN | ERROR`。 |
| `raw_ref` | string/null | 可选诊断数据引用；不得直接暴露给 Agent。 |

数据库只存必要且经过裁剪的诊断元数据。凭据、完整摄像头内容、二进制数据和任意原始 payload 不进入事件正文。

### 6.2 首版事件类型

- `device.discovered`
- `device.removed`
- `device.online`
- `device.offline`
- `device.property.changed`
- `device.command.completed`
- `device.command.failed`
- `provider.connected`
- `provider.disconnected`
- `provider.reconciled`

事件类型必须集中枚举。未知事件可保存为诊断记录，但不得触发自动化，除非代码和规则 schema 已明确支持。

### 6.3 顺序和时间语义

- 不承诺不同 provider 间的全局顺序；
- 同一 provider、device、property 尽可能按来源序列处理；
- `occurred_at` 不能替代处理顺序，来源时间可能偏移；
- 投影更新必须比较来源序列或版本，旧事件不能覆盖新状态；
- 无可靠序列时使用 `received_at + event_id` 提供稳定处理顺序；
- 所有时间持久化为 UTC，Dashboard 使用服务器 `default_timezone` 展示。

### 6.4 去重

- `(family_id, dedupe_key)` 唯一；
- 有稳定 `source_event_id` 时优先使用；
- HA 状态事件可使用 provider、entity、last_updated 和规范状态摘要；
- MQTT 事件在无消息 ID 时使用 provider、topic、payload hash 和受限时间桶；
- 设备命令结果使用 `command_id + terminal_status`；
- 重复事件返回既有 `event_id`，不得再次创建 Automation run。

## 7. Provider 接入

### 7.1 Home Assistant

首版使用 Home Assistant WebSocket API订阅状态变化：

1. 使用 secret store 中的引用获取凭据；
2. 完成认证后订阅受支持事件；
3. 将 HA `device_id`、`entity_id`、domain、area 和 attributes 映射到统一设备模型；
4. 连接断开后使用有抖动的指数退避重连；
5. 重连成功后执行一次全量状态 reconcile；
6. reconcile 只生成真实差异事件，不能把全部实体当作新变化重复触发；
7. 实体重命名、删除和设备多实体聚合不得生成重复物理设备；
8. 日志不记录 token、Authorization header 或完整敏感 payload。

### 7.2 MQTT

- 只能订阅 provider 配置中明确允许的 topic；
- 首版不接受 `#` 全局通配；是否允许受约束的单级 `+` 必须在实施前明确；
- 每个 topic 必须绑定一个声明式 payload mapping；
- mapping 只能做字段选择、类型转换、单位转换和枚举映射，不执行脚本；
- retained 消息默认用于初始化投影，不应自动触发有副作用的自动化；
- 如用户显式允许 retained 触发，UI 必须给出醒目风险提示，且仍执行所有去重与审批规则；
- 超大、无效或不符合 schema 的消息被拒绝并计数，不进入自动化。

### 7.3 HomeMind 设备运行时

- 复用现有设备身份和 capability 声明；
- 在线/离线事件根据服务端心跳规则产生，不信任设备自行声明的家庭身份；
- 命令结果事件复用现有命令 ID 和 Transaction 关联；
- 被撤销或禁用的设备不得继续发布可触发自动化的事件。

### 7.4 Provider 健康状态

provider 至少记录：

- `connection_status`；
- `last_event_at`；
- `last_sync_at`；
- `last_success_at`；
- `last_error_code`；
- `reauth_required`；
- `rate_limited_until`；
- `reconnect_attempts`。

错误正文只能保存稳定错误码和经过脱敏的摘要。

## 8. 持久化事件日志与状态投影

### 8.1 可靠写入

事件写入、去重登记和待消费状态必须在同一数据库事务内完成。若 provider 状态游标需要推进，只有事件写入成功后才能提交游标。

首版允许使用数据库队列表实现，不要求通用消息代理。消费者通过 lease claim 取得待处理事件：

- claim 使用条件更新避免两个 worker 同时处理；
- lease 超时后可恢复；
- 已完成事件不重复匹配自动化；
- 连续失败进入 `DEAD_LETTER`，不无限快速重试；
- 人工重新处理必须保留原事件和操作审计。

### 8.2 状态投影

每个规范属性至少保存：

- `device_id`、`entity_id`、`property`；
- 当前值、单位和质量；
- 来源 provider；
- 来源版本/序列；
- `occurred_at`、`received_at`、`projected_at`；
- 最近事件 ID。

投影是查询优化和自动化条件输入，不取代 provider 的真实状态。执行关键动作前，Transaction handler 仍应按现有 verify 机制读取设备结果。

### 8.3 保留策略

- 当前投影长期保留；
- 事件明细默认保留 30 天，具体默认值放在领域配置常量而非 router；
- 与 Transaction、审批、失败运行或审计关联的事件不得在引用仍有效时提前删除；
- 清理按批次执行，避免长事务；
- 家庭导出默认包含规则定义、运行摘要和审计引用，不包含无限量原始事件历史；
- 删除家庭时按现有家庭资源级联规则清理。

## 9. 自动化模型

### 9.1 Automation

自动化至少包含：

| 字段 | 说明 |
|---|---|
| `automation_id` | 公共 ULID。 |
| `family_id` | 所属家庭。 |
| `name` / `description` | 用户可见名称和说明。 |
| `enabled` | 是否参与匹配。 |
| `version` | 每次修改递增，用于运行快照和并发更新。 |
| `trigger_json` | 一个首版支持的触发器。 |
| `conditions_json` | 按顺序求值的条件数组，首版全部为 AND。 |
| `actions_json` | 1–10 个有序动作。 |
| `execution_policy_json` | 冷却、并发、审批等策略。 |
| `created_by` / `updated_by` | 用户 ID。 |
| `created_at` / `updated_at` | UTC。 |

首版每条规则只支持一个触发器。多个触发器通过创建多条规则表达，避免过早引入复杂布尔规则树。

### 9.2 触发器

#### 状态变化

```json
{
  "type": "device_property",
  "device_id": "dev_...",
  "property": "power",
  "operator": "changed_to",
  "value": "on"
}
```

支持：

- `changed`
- `changed_to`
- `changed_from`
- `equals`
- `not_equals`

#### 数值阈值

支持 `above`、`below` 和区间。阈值触发必须使用边沿语义：只有从阈值一侧跨到另一侧才触发，持续高于阈值的重复上报不反复执行。

可选支持 `for_seconds`，表示状态持续满足指定时间后才触发。持续计时必须持久化，服务重启后可恢复；若首个实施阶段无法可靠恢复，则不得先暴露该字段。

#### 在线状态

- `device_online`
- `device_offline`
- `provider_disconnected`

必须支持防抖，例如离线持续 60 秒才触发，避免网络抖动产生通知风暴。

#### 时间触发

- 固定本地时间；
- 星期选择；
- 使用服务器 `default_timezone`；
- 夏令时行为必须在实现和测试中固定：不存在的本地时间跳过，重复时间至多执行一次。

时间触发可复用 cron 基础设施的调度思想，但自动化业务状态仍归 HomeMind 领域管理，不把设备规则塞入普通用户 cron prompt。

### 9.3 条件

首版支持：

- 设备属性比较；
- 设备在线/离线；
- 本地时间段和星期；
- 家庭模式，例如 `HOME | AWAY | SLEEP | VACATION`；
- 触发事件质量必须为 `GOOD`；
- 属性新鲜度不超过指定秒数。

条件必须是确定性、无副作用求值。条件读取同一数据库快照中的状态投影；若必要状态缺失或过期，结果为“不满足”，并记录稳定原因码，不能猜测。

### 9.4 动作

首版动作类型：

1. `device.command`：只允许选择目标设备目录中已声明的命令；
2. `family.notification`：向指定家庭成员或管理员发送通知；
3. 已存在于 Family Action Registry 且明确标记为可自动化的低风险动作。

动作定义保存目标公共 ID、命令名和符合 schema 的参数。不得保存自由 provider service/topic。

### 9.5 执行策略

每条规则至少支持：

- `cooldown_seconds`：冷却期内忽略重复触发；
- `max_concurrent_runs`：首版默认且最大为 1；
- `on_running`：首版支持 `DROP | QUEUE_ONE`；
- `stop_on_action_failure`：首版固定为 true；
- `approval_mode`：`FOLLOW_POLICY | ALWAYS_REQUIRE`，不得提供 `NEVER_REQUIRE` 来覆盖风险策略；
- `enabled_window`：可选生效时间范围。

## 10. 自动化运行状态机

### 10.1 Automation run

```text
MATCHED
   ↓
EVALUATING ──条件不满足──→ SKIPPED
   ↓
PLANNED
   ↓
RUNNING ──产生审批──→ WAITING_APPROVAL
   ↑                       │
   └────审批通过────────────┘
   │
   ├──全部动作完成────────→ COMPLETED
   ├──动作失败────────────→ FAILED
   ├──审批拒绝/过期────────→ CANCELLED
   └──管理员取消──────────→ CANCELLED
```

Run 状态：

- `MATCHED`
- `EVALUATING`
- `SKIPPED`
- `PLANNED`
- `RUNNING`
- `WAITING_APPROVAL`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

每个 run 保存自动化版本和规则快照摘要。规则后续修改不得改变已经开始的 run。

### 10.2 Action run

Action run 状态：

- `PENDING`
- `PLANNING_TRANSACTION`
- `WAITING_APPROVAL`
- `AWAITING_DEVICE`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

Action run 保存 `transaction_id`、`device_command_id`、稳定错误码和尝试次数。审批由 Transaction 拥有，Automation 只投影等待状态，不创建第二套审批记录。

### 10.3 幂等键

建议规则：

```text
automation_run_key = automation_id + automation_version + trigger_event_id
action_key = automation_run_id + action_index
transaction_idempotency_key = "automation:" + action_key
```

数据库唯一约束必须阻止同一触发事件重复创建 run 或 Transaction。

### 10.4 崩溃恢复

- worker 使用有期限 lease；
- 服务启动后回收超时的 `EVALUATING/PLANNED/RUNNING` run；
- 已存在 `transaction_id` 的 Action run 只能查询/恢复 Transaction，不能重新 plan；
- Transaction 已完成时同步完成 Action run；
- Transaction 处于不确定结果时，run 进入失败待检查，不能再次发送非幂等命令；
- 恢复过程产生指标和审计，不向用户重复发送相同通知。

## 11. 审批与风险

### 11.1 强制复用 Family Transaction

自动化创建者的身份不能永久授权未来动作。每次动作触发时必须基于当前状态重新计算：

- 创建者/服务主体是否仍属于家庭；
- 自动化是否仍启用；
- 目标设备是否仍属于家庭；
- capability 和 command 是否仍存在；
- 当前家庭权限策略；
- command 风险和设备安全标签；
- 参数是否仍符合当前 schema。

### 11.2 风险计算

风险至少由以下因素共同决定：

```text
最终风险 = domain 安全下限
         + command 风险
         + 目标设备安全标签
         + 参数范围
         + 家庭策略
         + 执行上下文
```

domain 的禁止级别不可被 command、规则创建者或 LLM 降低。门锁、摄像头、安防等现有 blocked domain 继续禁止写操作。

### 11.3 审批体验

审批内容至少展示：

- 自动化名称；
- 触发原因及发生时间；
- 目标设备和房间；
- 将执行的命令和结构化参数；
- 风险级别；
- 后续剩余动作数量；
- 审批有效期；
- “仅批准本次”，首版不提供从审批页永久放行规则。

审批拒绝或过期后，当前 run 取消；未来新事件仍可创建新的 run，除非规则被禁用。

## 12. 防循环、风暴和资源限制

### 12.1 因果链

由自动化命令引发的后续设备状态事件必须携带可恢复的 `correlation_id/causation_id`。provider 不支持透传时，可在受限时间窗内依据设备、属性、期望值和命令 ID建立关联，但只能作为诊断推断，不能伪造确定因果。

### 12.2 循环保护

- 同一 correlation 链最多允许固定深度，默认建议 8；
- 同一自动化不得由自己在同一因果链产生的事件再次触发，除非未来明确引入受控反馈规则；
- A→B→A 循环通过最近规则链检测；
- 达到限制时终止新 run，记录 `AUTOMATION_LOOP_BLOCKED`；
- 用户可在运行历史中看到被阻断原因。

### 12.3 配额

首版应定义并测试：

- 每家庭启用自动化最大数量；
- 每条规则动作最大数量；
- 每家庭每分钟触发和执行上限；
- 单 provider 每秒事件接收上限；
- 单事件 payload 最大字节数；
- 事件 backlog 最大值和降级行为；
- dead-letter 最大保留数量。

具体数值由实施阶段结合现有配置体系确定，不允许散落在 router 或 UI 中。

## 13. 权限、安全与隐私

### 13.1 权限

- 家庭管理员：创建、编辑、启用、禁用、删除和手动运行自动化；
- 普通成员：默认只查看自己有权访问的自动化摘要和相关通知；
- 是否允许普通成员创建低风险自动化留作产品决策，首版默认不允许；
- 私密空间设备或状态只能用于有权访问该空间的规则和用户；
- 自动化不能成为跨空间推断或泄露状态的通道。

### 13.2 凭据与敏感数据

- provider 凭据只通过 secret reference 获取；
- 日志、事件、run、审计和 API response 不包含 token、密码和 Authorization header；
- 原始 payload 进入持久化前必须经过 allow-list 投影；
- 摄像头图像、音频和生物特征不得作为通用事件字段；
- 通知正文不得默认暴露私密空间的完整状态。

### 13.3 输入校验

- trigger、condition、action 使用有判别字段的 Pydantic 模型；
- 拒绝未知字段或采用明确版本兼容策略，不能静默误解；
- 所有 ID 在启用和执行时分别校验家庭归属；
- 数字拒绝 NaN/Infinity；
- 字符串、数组深度、JSON 大小有上限；
- 单位转换只使用内置映射；
- 禁止模板表达式和动态代码执行。

## 14. API 需求

建议新增：

```text
POST   /api/families/{family_id}/automations
GET    /api/families/{family_id}/automations
GET    /api/families/{family_id}/automations/{automation_id}
PATCH  /api/families/{family_id}/automations/{automation_id}
DELETE /api/families/{family_id}/automations/{automation_id}

POST   /api/families/{family_id}/automations/{automation_id}/enable
POST   /api/families/{family_id}/automations/{automation_id}/disable
POST   /api/families/{family_id}/automations/{automation_id}/dry-run
POST   /api/families/{family_id}/automations/{automation_id}/run

GET    /api/families/{family_id}/automation-runs
GET    /api/families/{family_id}/automation-runs/{run_id}
POST   /api/families/{family_id}/automation-runs/{run_id}/cancel

GET    /api/families/{family_id}/devices/{device_id}/state
GET    /api/families/{family_id}/device-events
```

### 14.1 API 约束

- 所有路由给出 `summary`、必要 `description` 和 typed `response_model`；
- 写接口支持乐观并发版本，旧版本更新返回 `409`；
- 列表接口分页，事件列表必须要求时间范围或有限 page size；
- `dry-run` 只评估触发器/条件、解析动作目录并返回预计风险，绝不创建 Transaction 或执行动作；
- 手动 `run` 创建一个标记为 `MANUAL` 的合成触发上下文，仍走权限、审批和 Transaction；
- 事件查询返回规范字段，不返回未经筛选的 provider raw payload；
- 非管理员审计视图继续遵守现有裁剪规则。

### 14.2 错误语义

新增稳定错误码时，同步更新：

- HomeMind/Octop backend i18n 的 `en`、`zh`；
- Dashboard `apiErrors`；
- 错误码一致性测试。

至少覆盖：规则无效、目标不存在、能力变更、循环阻断、限流、状态过期、provider 离线和 run 冲突。

## 15. Dashboard 需求

### 15.1 自动化列表

显示：

- 名称、启用状态；
- 触发器摘要；
- 动作摘要；
- 最近运行结果和时间；
- 是否存在等待审批；
- provider/设备不可用警告。

### 15.2 创建与编辑

首版使用结构化表单，不提供自由文本 DSL：

1. 选择触发设备和属性；
2. 选择操作符和阈值；
3. 添加可选条件；
4. 从目标设备命令目录选择动作；
5. 配置冷却和审批模式；
6. 展示自然语言摘要及风险预览；
7. 保存为草稿或启用。

规则引用的设备/能力失效时，编辑页明确标记并禁止启用，不能静默删除动作。

### 15.3 运行详情

展示：

- 触发事件摘要；
- 条件逐项结果；
- 动作时间线；
- Transaction 和审批状态；
- 验证结果；
- 跳过、失败、限流或循环阻断原因；
- correlation ID 的用户可复制短标识，不展示敏感内部数据。

### 15.4 实时更新

自动化 run 状态可通过现有 `FamilyEventBus` 推送新的通知事件类型。页面刷新后必须从持久化 API 恢复完整状态，不能只依赖 WebSocket 内存消息。

## 16. 可观测性与审计

### 16.1 指标

至少增加：

- provider 接收事件数、无效事件数、去重数；
- event backlog、最老待处理事件年龄；
- reconcile 次数、耗时和差异数；
- 自动化匹配、跳过、完成、失败、取消数；
- 等待审批数量和等待时长；
- 动作执行/验证耗时；
- 重试、lease 回收、dead-letter 数；
- 循环阻断和限流数。

指标标签不得包含 `family_id`、`device_id` 等无界高基数字段。

### 16.2 日志

结构化日志携带有限的：

- `provider_kind`；
- event/run/transaction/correlation ID 的短标识；
- 稳定状态和错误码；
- 耗时和尝试次数。

不记录凭据、完整 payload、家庭成员隐私状态、绝对本地路径或审批正文。

### 16.3 审计

审计至少记录：

- 谁创建、修改、启用、禁用、删除规则；
- 哪个事件匹配了哪一版本规则；
- 条件结果摘要；
- 创建了哪些 Transaction；
- 谁审批或拒绝；
- 动作、验证和最终状态；
- 手动重试、取消和 dead-letter 重放。

## 17. 数据库需求

建议资源表：

- `homemind_device_events`
- `homemind_device_event_consumers`
- `homemind_device_state_projection`
- `homemind_automations`
- `homemind_automation_runs`
- `homemind_automation_action_runs`

必须遵循仓库资源 ID 规范：整数 surrogate PK 加公共字符串 ID；子表外键引用公共字符串 ID，不引用整数 PK。

数据库变更要求：

- 同时提供 SQLite 和 PostgreSQL migration；
- 实施前确认当前 migration 是否已发布，未发布变更按仓库规则折叠；
- 唯一约束覆盖事件去重、run 去重和 action 幂等；
- 高频 claim、家庭时间查询、设备属性投影和 run 状态建立必要索引；
- SQLite claim 设计不得依赖 PostgreSQL 独有锁语义；
- migration helper 与 canonical SQL 等价且幂等；
- 更新 schema version 测试。

## 18. 性能与可靠性目标

首版目标：

- 正常负载下，事件接收到状态投影完成的 P95 小于 2 秒；
- 无审批的低风险动作，从事件接收到 Transaction 创建的 P95 小于 3 秒；
- 单实例至少稳定处理每秒 100 条短设备事件，持续 10 分钟不丢失；
- 服务重启后 60 秒内恢复未完成消费和自动化 run；
- 重复投递不产生重复 Transaction；
- provider 断线 10 分钟后恢复，状态最终收敛且无重放命令；
- 一个 provider 的故障不阻塞其他 provider 和非设备家庭功能。

这里的“不丢失”指已被 HomeMind 数据库确认接收的事件；provider 在断线期间未保留、且自身无法补发的事件不在保证范围内。

## 19. 测试与验收

### 19.1 单元测试

- 各 provider payload 到标准事件的映射；
- 事件去重和乱序投影；
- trigger 操作符、阈值边沿、时间和新鲜度条件；
- 规则 schema 和家庭归属校验；
- 冷却、并发、QUEUE_ONE 和配额；
- run/action 状态机全部合法与非法转换；
- Transaction 幂等键和审批映射；
- 循环检测和因果链深度；
- lease 超时、恢复和 dead-letter；
- 权限、私密空间和审计裁剪；
- i18n key parity；
- SQLite/PostgreSQL repo contract。

### 19.2 集成测试

使用录制后脱敏的 fake provider，不依赖 CI 访问真实家庭网络：

1. HA fake WebSocket 推送状态变化，规则匹配并创建 Transaction；
2. 重复推送同一 HA 事件，只产生一个 run；
3. HA 断线重连后 reconcile 收敛，不重复执行命令；
4. MQTT retained 初始化投影但默认不触发动作；
5. 高风险动作停在审批，批准后执行并验证；
6. 拒绝或审批过期后不执行；
7. 服务在事件持久化、Transaction 创建和命令等待阶段分别重启，均正确恢复；
8. A 规则动作导致 B 事件，correlation 可串联；A→B→A 循环被阻断；
9. 删除、禁用设备或撤销成员权限后，旧规则不能继续执行；
10. 普通成员不能读取管理员审计或私密空间事件。

### 19.3 Dashboard 测试

- 结构化规则创建、编辑、启停；
- 无效设备/能力的失效展示；
- dry-run 风险摘要；
- 等待审批和运行时间线；
- WebSocket 重连去重；
- 刷新页面后从持久化状态恢复；
- 中英文文案；
- 使用服务器时区展示时间。

### 19.4 建议验证命令

实施时根据最终文件位置补充精确测试路径，最低要求：

```bash
uv run pytest tests/unit/homemind -k "device_event or automation or smart_home or transaction" -q
uv run pytest tests/integration -k "device_event or automation or smart_home" -q
uv run pytest tests/unit/i18n -q
cd dashboard && npm test -- --run
cd dashboard && npx tsc -b
make build-frontend
make all
```

## 20. 分阶段交付

### 阶段 A：事件基础与状态投影

- 冻结事件信封和去重规则；
- 建立事件日志、consumer lease 和投影表；
- 接入 HA WebSocket；
- 实现重连 reconcile；
- 只更新状态和指标，不执行自动化。

**验收：** 断线、重复、乱序和重启测试全部通过，当前状态最终一致。

### 阶段 B：只读自动化评估

- 自动化 CRUD 和结构化校验；
- 状态/阈值/在线/时间触发器；
- 条件求值；
- dry-run、运行历史；
- 动作只生成计划，不执行副作用。

**验收：** 同一录制事件集重复运行产生相同匹配与条件结果。

### 阶段 C：Transaction 动作与审批

- `device.command` 等目录化动作；
- Transaction 幂等关联；
- 审批、设备等待和验证状态投影；
- Dashboard 审批和运行时间线。

**验收：** 所有副作用都能追溯到 Transaction；任何路径均不能绕过权限和审批。

### 阶段 D：MQTT、恢复和防风暴

- MQTT 声明式 mapping；
- 冷却、并发、限流和循环检测；
- dead-letter、人工诊断与受控重放；
- 完整指标、保留和清理任务。

**验收：** 压力、断线、重启和循环故障演练通过。

## 21. 发布与回滚

- migration 先于新 worker 启用；
- 初次发布默认不启用任何已有家庭规则；
- provider 增量订阅可独立开关，关闭后回退到现有主动同步；
- 自动化 runner 可独立停止，停止不影响状态查询和手动设备控制；
- 回滚代码前先停止新 run claim，允许已创建 Transaction 按现有机制完成或进入人工检查；
- 不删除事件和 run 数据来实现回滚；
- schema 回滚不依赖破坏性降级迁移。

## 22. 待确认决策

以下决策在实施对应阶段前必须明确，不应由开发者静默选择：

1. 普通家庭成员是否可以创建仅含低风险动作的自动化；
2. MQTT 是否允许单级 `+` 通配订阅；
3. 事件默认保留期和家庭级存储配额；
4. 家庭模式由手动设置、成员在家状态还是独立 provider 提供；
5. 时间触发是否首版支持日出/日落；
6. `for_seconds` 持续条件是否进入首版；
7. 通知动作的可选接收人范围；
8. 自动化失败后是否只通知管理员，还是通知规则创建者与管理员。

未确认项不得降低安全默认值：普通成员无创建权、MQTT 无通配、retained 不触发副作用、失败通知管理员。

## 23. 完成定义

本需求完成必须同时满足：

1. provider 状态变化可可靠进入持久化事件日志并更新状态投影；
2. 重复、乱序、断线和重启不会造成状态倒退或重复动作；
3. 用户可通过结构化 UI 创建、预览、启停和观察自动化；
4. 每个有副作用的动作均通过 Family Transaction；
5. 高风险动作不能绕过审批，blocked domain 始终不可写；
6. 自动化具备冷却、配额、循环保护和崩溃恢复；
7. 权限、隐私、凭据和审计边界通过单元及集成测试；
8. SQLite、PostgreSQL、Windows 和 Linux 核心测试均通过；
9. API 文档、后端/前端 i18n 和用户可见错误完整；
10. `make all` 绿色，Dashboard 构建与类型检查通过。

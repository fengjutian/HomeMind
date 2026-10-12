# HomeMind MQTT 物联网设备接入产品需求文档

**状态：** Draft  
**范围：** 家庭模块 / 智能家居 / MQTT 设备接入  
**目标版本：** 待排期  
**更新日期：** 2026-10-12

关联文档：

- docs/smart-home-setup.md
- docs/requirements/homemind-device-event-automation-requirements.md
- docs/requirements/homemind-next-capabilities-plan.md

---

## 1. 产品摘要

HomeMind 当前可通过 Home Assistant 同步智能家居实体，后端也已有 MQTT Provider、Topic 白名单和命令发布适配器基础，但尚未形成用户可用的 MQTT 闭环：Dashboard 无法配置 Broker，系统不能从 Zigbee2MQTT、蓝牙网关或通用 MQTT 设备发现并导入设备，也不能持续订阅状态并稳定映射为 HomeMind 统一设备。

本需求建设一条独立于 Home Assistant 的 MQTT 接入路径，使用户可将小米 Zigbee/BLE 设备、自研硬件及其他 MQTT 设备接入 HomeMind，并复用现有家庭权限、Family Transaction、审批、验证、审计和可观测性体系。

首期交付：

1. 配置和管理 MQTT Broker；
2. 通过 Zigbee2MQTT 发现并导入设备；
3. 通过声明式映射接入通用 MQTT 设备；
4. 持续订阅状态、在线状态和命令结果；
5. 映射到 HomeMind 统一设备能力模型；
6. 仅通过目录化命令和 Family Transaction 控制设备；
7. 在智能家居页面展示连接、设备和诊断状态。

MQTT 是通信协议，不是小米云 API。本需求不支持通过 MQTT 登录米家账号，也不保证所有小米设备兼容。小米音箱、摄像头及仅支持米家云的 Wi-Fi 设备不在首期保证范围。

## 2. 背景与问题

### 2.1 用户问题

- 用户已有 Mosquitto 和 Zigbee2MQTT，但没有 Home Assistant；
- 用户只需接入少量传感器、开关或灯，不愿部署完整家庭中枢；
- 用户使用 ESP32、树莓派或自研边缘网关；
- 用户要求局域网运行，不依赖厂商云；
- 用户希望 HomeMind 成为设备编排和家庭 AI 的主要入口。

### 2.2 当前缺口

- Dashboard 只有“添加 Home Assistant”；
- MQTT Provider 没有完整创建、编辑、测试和凭据轮换流程；
- Broker TLS、认证、重连与订阅缺少产品化配置；
- MQTT Adapter 没有通用设备发现和实体清单；
- 没有 Zigbee2MQTT 设备目录解析与导入；
- 没有 Topic/Payload 到标准属性的映射 UI；
- 没有状态订阅、retained 初始化和离线判断闭环；
- 用户无法区分 Broker 已连接、设备已发现、设备可控制；
- 命令发布缺少端到端回执、验证和错误解释。

## 3. 产品定位与原则

HomeMind 是统一设备模型、安全编排和智能交互层，不是 MQTT Broker，也不实现 Zigbee、BLE、Thread 或 Z-Wave 无线协议栈。

设备链路：

    小米 Zigbee 设备 → Zigbee2MQTT ┐
    小米 BLE 设备 → BLE 网关 ──────┤
    ESP32/自研设备 → MQTT Client ───┼→ MQTT Broker → HomeMind
    其他协议设备 → 边缘网关 ────────┘

产品原则：

1. 本地优先，不依赖外部必需云服务；
2. 不接收或保存米家、华为等厂商账号密码；
3. 未完成身份、能力和命令映射的设备默认只读；
4. 映射只能进行字段选择、类型转换、枚举和有限单位换算；
5. 所有写操作必须经过 Family Transaction；
6. 品牌和来源不改变权限及风险；
7. 收到状态不代表获得控制权，发布成功不代表设备已执行；
8. Broker 或服务重启后能够自动恢复连接、订阅和状态；
9. Secret、完整敏感 Payload 和家庭标识不得进入 Agent 上下文。

## 4. 目标与非目标

### 4.1 目标

| 目标 | 成功标准 |
|---|---|
| 连接现有 Broker | 用户 5 分钟内完成配置、测试和保存 |
| 导入 Zigbee2MQTT 设备 | 可预览能力并选择性导入 |
| 接入通用 MQTT 设备 | 无需写代码即可完成声明式映射 |
| 状态实时同步 | 正常网络下 P95 不超过 2 秒 |
| 控制安全可审计 | 所有写命令经过权限、审批、验证和审计 |
| 故障可诊断 | 能区分连接、订阅、映射、设备和命令故障 |
| 多家庭隔离 | 家庭之间不能交叉订阅、查看或发布 |

### 4.2 非目标

- 直接登录米家或华为智慧生活；
- 抓包、逆向、Token 提取或模拟厂商 App；
- 自研 Zigbee、BLE、Thread、Matter 或 Z-Wave 协议栈；
- 提供公共 Broker；
- 提供任意 Topic 浏览器、自由发布器或调试终端；
- 运行用户提交的 Python、JavaScript、Jinja、Shell 或正则脚本；
- 摄像头视频、录像、云台、麦克风或人脸识别；
- 音箱播放、TTS、小爱会话和唤醒；
- 门锁解锁、报警撤防、燃气阀写控制；
- 保证所有小米品牌设备兼容。

## 5. 用户与场景

### 5.1 角色

| 角色 | 核心诉求 | 权限边界 |
|---|---|---|
| 家庭管理员 | 配置 Broker、导入设备、维护映射 | 可管理接入；高风险动作仍受策略约束 |
| 普通成员 | 查看和控制获准设备 | 不可查看凭据或修改映射 |
| 集成开发者 | 接入自研设备 | 遵循 MQTT 契约，不获得家庭管理权 |
| 运维人员 | 定位连接和消息故障 | 仅查看脱敏诊断 |
| Family Agent | 查询状态、规划目录内命令 | 不可看到 Secret、自由 Topic 或原始 Payload |

### 5.2 核心场景

**小米 Zigbee 传感器：** 用户已有 Mosquitto 和 Zigbee2MQTT，HomeMind 读取设备目录，展示温湿度计、门磁和按钮，用户选择后导入并实时查看状态。

**Zigbee 灯和插座：** HomeMind 根据 Zigbee2MQTT exposes 元数据识别开关、亮度和色温。控制时创建 Family Transaction，发布命令并等待状态回报验证。

**自研 ESP32：** 用户填写状态 Topic，将 JSON 中的 temperature、humidity 和 online 映射为标准属性；没有命令 Topic 时以只读方式导入。

**自研继电器：** 用户声明状态 Topic、命令 Topic、开关 Payload 和验证条件，HomeMind 提供 turn_on 和 turn_off。

**断线恢复：** Broker 重启后页面显示重连中；恢复后重新订阅，使用 retained 状态校准，但不因此触发自动化副作用。

## 6. 兼容范围

### 6.1 首期保证范围

| 来源 | 类型 | 能力 |
|---|---|---|
| Zigbee2MQTT | sensor / binary_sensor | 状态、在线、电量，只读 |
| Zigbee2MQTT | light | 开关、亮度、色温 |
| Zigbee2MQTT | switch / plug | 开关；功率、电量只读 |
| Zigbee2MQTT | cover | 开、关、停止、位置，按高风险审批 |
| Zigbee2MQTT | fan / climate | 映射到现有目录的能力 |
| 通用 MQTT | sensor / binary_sensor | 声明式状态映射 |
| 通用 MQTT | light / switch | 声明式状态与命令映射 |

### 6.2 小米设备矩阵

| 类别 | 推荐桥接 | 承诺 |
|---|---|---|
| Zigbee 温湿度计、门磁、按钮 | Zigbee2MQTT | 型号受支持时可导入 |
| Zigbee 灯、开关、插座 | Zigbee2MQTT | 按 exposes 映射 |
| BLE 广播设备 | BLE→MQTT 网关 | 通过通用映射接入 |
| 部分扫地机器人 | 设备端 MQTT 固件/桥 | 仅支持目录内命令 |
| 米家 Wi-Fi 家电 | 专用桥或 Home Assistant | 不保证直接 MQTT |
| 小米音箱 | 无稳定标准 MQTT 桥 | 不支持音频控制 |
| 米家摄像头 | 状态桥可选 | 不支持视频与控制 |
| 门锁、报警器 | 可显示只读状态 | 禁止写控制 |

兼容性由稳定 Topic、设备唯一标识、可解析 Payload、标准能力映射、明确验证方式和风险策略共同决定，不按品牌名称决定。

## 7. 页面和用户流程

### 7.1 信息架构

    硬件 / 智能家居
    ├── 接入方式：Home Assistant、MQTT Broker
    ├── 智能设备：全部、待导入、离线、映射异常
    └── 运行诊断：连接、订阅、最近事件、最近命令

### 7.2 添加 Broker

入口：硬件 → 添加接入方式 → MQTT Broker。

| 字段 | 必填 | 说明 |
|---|---|---|
| 连接名称 | 是 | 家庭内可读名称 |
| Broker 地址 | 是 | mqtt、mqtts 或 WebSocket 地址 |
| 端口 | 条件必填 | 默认按协议推导 |
| Client ID | 否 | 默认安全生成 |
| 用户名、密码 | 否 | Secret Store 保存，密码不回显 |
| TLS 校验 | 是 | 默认开启 |
| CA、客户端证书和私钥 | 否 | 自签名或双向 TLS |
| 接入模式 | 是 | Zigbee2MQTT / 通用 MQTT |
| 基础 Topic | 条件必填 | Zigbee2MQTT 默认 zigbee2mqtt |

要求：

- 测试连接只验证网络、TLS 和认证，不保存；
- 成功后展示协议版本和往返耗时；
- 密码和私钥保存后不得返回；
- 修改地址或凭据后必须重新测试；
- 删除前展示受影响设备数，删除不得发布设备命令。

### 7.3 Zigbee2MQTT 导入

流程：连接 Broker → 读取 Bridge 目录 → 展示兼容性 → 选择设备 → 预览能力和风险 → 导入 → 建立显式订阅 → 获取状态。

预览展示名称、脱敏 IEEE 地址、厂商、型号、设备类型、支持状态、标准域、属性、命令和风险。

- 默认不自动导入全部设备；
- 已导入设备不得重复创建；
- Friendly Name 改名不改变身份；
- IEEE 地址不进入 Agent 视图；
- 来源删除先标记，不因单次读取失败立即清理；
- 清理映射必须由用户确认。

### 7.4 通用设备映射

用户依次填写设备信息、标准类型、明确状态 Topic、样例 Payload、属性字段路径、类型、单位、枚举、availability Topic、可选命令 Topic/Payload 和验证条件。

界面不提供脚本编辑器。样例捕获只能在用户主动开启的短时、单 Topic 会话中进行，结束后清理未保存原文。

## 8. 功能需求

### 8.1 Provider 生命周期

状态包括 DRAFT、TESTING、CONNECTING、CONNECTED、RECONNECTING、FAILED、REAUTH_REQUIRED、DISABLED 和 DELETED。

- 同一 Provider 仅一个活动连接；
- 禁用立即断开并取消订阅；
- 使用带抖动的指数退避；
- 认证失败停止快速重试；
- TLS 错误使用独立错误码；
- 手动重试不能产生并发连接；
- 各 Provider 故障隔离。

### 8.2 Topic 安全

- 拒绝空 Topic、控制字符和非法编码；
- 用户配置不得包含 # 或 +；
- Zigbee2MQTT 根据设备目录生成具体 Topic；
- 协议所需 Bridge Topic 必须由代码模板生成；
- 发布 Topic 必须同时属于 Provider 和设备映射白名单；
- 禁止向 Broker 管理和保留命名空间发布；
- 删除映射后立即取消订阅和发布权限。

### 8.3 Payload 与映射

- 支持 UTF-8、JSON、数字、布尔和有限枚举；
- 默认单条最大 64 KiB；
- 限制 JSON 深度与字段数；
- 不处理图片、音视频和任意二进制；
- 字段路径只允许键和固定数组索引；
- 单位换算使用代码白名单，不允许输入公式；
- 无效消息计数但不覆盖最后有效状态；
- 原始 Payload 默认不持久化。

属性映射包含标准属性、字段路径、数据类型、来源/标准单位、枚举映射、空值策略和新鲜度。

### 8.4 身份与能力

身份优先使用 Zigbee IEEE 地址、MQTT Discovery identifier、用户声明稳定 ID，最后才使用 Provider 与状态 Topic 组合。名称和房间不能作为身份。

优先映射现有域：light、switch、scene、climate、cover、fan、vacuum、humidifier、sensor、binary_sensor。未知类型为 unknown 且只读；不得根据 Payload 中某个字符串自动授予写权限。

### 8.5 命令

- Topic 在保存映射时确定，运行时不得由 Agent 提供；
- 参数只能进入预定义字段；
- retain 默认为 false，QoS 首期支持 0 和 1；
- 命令带幂等键；
- Broker 接收发布不等于设备成功；
- 必须通过状态、回执或受控超时验证；
- 超时为失败或未知，不能伪报成功；
- toggle 等非幂等命令不得盲目重试。

### 8.6 状态订阅

    MQTT 消息
      → Topic 白名单
      → 大小和 Schema 校验
      → 声明式映射
      → 持久化设备事件
      → 状态投影
      → UI / 自动化

- 至少一次接收，逻辑效果至多一次；
- 无来源 ID 时用 Provider、Topic、Payload 摘要和时间桶去重；
- retained 只初始化状态，默认不触发副作用；
- 重连后重新订阅并校准；
- 旧消息不能覆盖新状态；
- 时间以 UTC 持久化、服务器时区展示；
- availability/LWT 优先判断离线，否则使用新鲜度；
- Broker 断线和单设备离线必须分开显示。

### 8.7 Zigbee2MQTT

- 检测并展示版本；
- 解析设备目录和 exposes；
- 未知能力只读且不阻断已知能力；
- 解析 availability、linkquality、电量和标准属性；
- 支持设备改名、新增、移除、重新配对和网关重启；
- 不读取或展示 Zigbee 网络密钥；
- 首期不承担 Permit Join，提供跳转说明；
- 版本不兼容时给出稳定错误码和升级建议。

## 9. Dashboard 要求

Provider 卡片展示名称、脱敏主机、连接/订阅状态、设备数量、重连次数、最后连接、最后事件、最后同步、最近错误，以及发现、映射、测试、重连、编辑、禁用和删除操作。

设备列表展示名称、房间、型号、厂商、来源、在线状态、最后事件、主要状态、能力、风险、映射健康、最近命令耗时和失败原因；支持按来源、房间、类型、在线和映射状态筛选。

必须明确区分：未配置、测试中、已连接无设备、待导入、正常、Broker 断线、认证失败、TLS 失败、订阅失败、解析失败、映射不完整、设备离线、等待审批、执行中、验证超时和命令失败。

引导必须说明：

- MQTT 不是米家账号登录；
- Zigbee2MQTT 需要 Broker、协调器和设备重新配对；
- 重新配对可能影响原米家联动；
- MQTT 不适合传输摄像头视频；
- 不确定时可继续使用 Home Assistant。

所有文案必须进入中英文 i18n。

## 10. 权限、安全与隐私

仅家庭管理员可管理 Provider、发现设备和编辑映射。普通成员按家庭策略查看和控制；Agent 只能使用授权后的标准状态和命令目录。任何角色都不能自由发布 Topic。

- 密码、私钥和证书 Secret 保存到 Secret Store；
- API 不返回已有 Secret；
- 修改普通字段不得清空凭据；
- 审计凭据替换行为但不记录值；
- 推荐 mqtts，明文连接显示风险提示；
- TLS 校验默认开启，禁止静默接受坏证书；
- Broker 地址执行 SSRF 和 DNS 重绑定防护；
- 连接、读取、发布均有超时和限流；
- lock、camera、alarm_control_panel、燃气阀和未分类安防设备只读；
- 摄像头 Payload 不保存图像、视频 URL、访问 Token 或人脸数据。

## 11. 数据与 API 概念

MQTT Provider 需要表达家庭、类型、名称、Broker、凭据引用、TLS 引用、Client ID、模式、基础 Topic、白名单和可观测性。

设备映射需要表达 Provider、来源设备 ID、HomeMind Device ID、来源版本、状态/availability Topic、属性映射、命令映射、版本、校验状态和最近错误。

迁移要求：

- 当前 HomeMind 最高迁移为 026 且尚未发布，实施时应折叠进 026；
- SQLite/PostgreSQL 迁移成对；
- 不改变 Home Assistant 行为；
- 旧 MQTT Provider 数据不足时标记需补充配置，不自动连接；
- 不向普通表写明文凭据。

API 至少支持 Provider CRUD/测试/重连、发现任务、候选设备、能力预览、批量导入、映射 CRUD/校验/预览、命令预览和 Transaction 查询。所有接口校验家庭所有权、使用类型模型、提供 OpenAPI 说明、返回稳定 HomeMindErrorCode，并保持 Router 薄层。

## 12. 可观测性

Provider 指标：

- 连接和订阅状态；
- 连接耗时、重连和连续失败次数；
- 最后连接、断开、事件和成功处理时间；
- 订阅 Topic 数和消息速率；
- 无效、超大、限流、映射失败计数；
- 最近稳定错误码和可用时的 Broker RTT。

设备指标：

- 在线状态、最后事件和状态新鲜度；
- 最近映射结果；
- 最近命令状态；
- 排队、发布、回执和总耗时；
- 失败原因和验证结果。

结构化日志仅记录 Provider ID、Device ID、Mapping ID、Correlation ID 和错误码，不记录 Secret 或完整敏感 Payload。审计覆盖 Provider、凭据、导入、映射和命令全过程。

## 13. 非功能需求

首期单实例目标：

- 20 个家庭，每家庭 5 个 Provider；
- 每 Provider 500 台设备、5,000 个显式 Topic；
- 稳态 200 条消息/秒；
- 峰值 1,000 条/秒持续 10 秒时服务可用；
- 状态更新 P95 不超过 2 秒；
- 不含审批等待的命令发布 P95 不超过 1 秒；
- 所有容量有服务端硬上限。

可靠性要求：

- 服务重启后恢复已启用 Provider；
- Broker 断线 10 分钟后恢复无需重建映射；
- 重连不产生重复订阅；
- retained 不重复触发副作用；
- 非幂等动作不重复；
- 单 Provider 故障不影响其他家庭和 Agent；
- Windows/Linux、SQLite/PostgreSQL 行为一致。

## 14. 验收标准

### 14.1 Broker

1. 可配置 mqtt 和 mqtts；
2. 正确凭据可测试和保存；
3. 密码、DNS、超时、TLS 错误分别解释；
4. Secret 不通过 API 返回；
5. 禁用后连接和订阅停止；
6. 服务重启后自动恢复。

### 14.2 设备

1. 可读取 Zigbee2MQTT 目录并批量导入；
2. 改名不产生重复设备；
3. 新设备进入待导入；
4. 单次扫描失败不误删设备；
5. 未知能力只读；
6. 通用 JSON 可映射为标准属性；
7. 无效和超大消息不覆盖有效状态；
8. 删除映射后取消订阅与发布权限。

### 14.3 状态与命令

1. 状态按延迟目标更新；
2. availability 正确更新在线状态；
3. Broker 断线与设备离线分开显示；
4. 重连后校准状态；
5. retained 不触发副作用；
6. Agent 不能提交任意 Topic；
7. 命令经过权限、审批、执行、验证和审计；
8. 门锁、摄像头和报警器无写命令；
9. 设备不回报时不能显示成功；
10. 跨家庭访问被拒绝。

## 15. 测试策略

单元测试覆盖 URL/TLS、Topic、Payload 限制、字段映射、exposes 映射、风险目录、去重、retained、Secret 脱敏和家庭权限。

集成测试使用临时 Broker 或 Fake Broker，覆盖 MQTT 3.1.1/5、认证、TLS、Broker 重启、消息风暴、设备目录、改名、移除、QoS、重复投递、命令验证及多家庭隔离。

前端测试覆盖 Broker 表单、凭据不回显、设备发现、批量导入、映射向导、健康状态、错误恢复、中英文和移动端。

发布门槛遵循 AGENTS.md：make all 绿色，并额外完成前端类型检查和生产构建。

## 16. 分阶段交付

1. **阶段 0：契约与安全基线。** 冻结数据、错误码、Topic/Payload 和 Secret 规则。
2. **阶段 1：Broker 产品化。** 连接 CRUD、测试、TLS、重连、指标和 Provider UI。
3. **阶段 2：Zigbee2MQTT 只读导入。** 目录、预览、显式订阅、传感器状态。
4. **阶段 3：通用声明式映射。** 样例捕获、属性映射、availability 和诊断。
5. **阶段 4：安全命令。** light/switch 命令、Transaction、验证、幂等与审计。
6. **阶段 5：事件自动化。** 持久事件、retained 隔离、防风暴和运行历史。

每阶段必须独立验证，不得提前开放下一阶段写能力。

## 17. 成功指标

- Provider 创建和首次连接成功率；
- 中位配置耗时；
- 发现设备数、导入率；
- 导入 24 小时在线率；
- 消息映射成功率；
- 命令成功率、失败率和 P95 耗时；
- 重连成功率和平均恢复时间；
- 未知能力只读比例；
- 支持问题中连接、映射、离线和命令失败的分布。

指标不得包含凭据、完整 Payload、家庭成员信息或摄像头内容。

## 18. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 用户误以为 MQTT 可登录米家 | 向导明确说明 |
| Zigbee2MQTT 版本变化 | 版本探测、兼容解析、未知字段忽略 |
| Topic 过宽 | 禁止用户通配并生成显式订阅 |
| 恶意 Payload | 大小、深度、速率限制，不保存原文 |
| 发布成功但设备未执行 | 强制状态或回执验证 |
| 非幂等重复动作 | 幂等键，禁止盲目重试 |
| Broker 单点故障 | 重连、状态区分和部署指引 |
| 重新配对影响米家 | 导入前提示 |
| 自签 TLS 困难 | 支持自定义 CA，不默认关闭验证 |
| 消息阻塞主服务 | 全异步 I/O、限流和 Provider 隔离 |

## 19. 待确认事项

1. 首期是否支持 MQTT WebSocket；
2. MQTT 5 与 3.1.1 的支持范围；
3. Provider 的设备、Topic 和速率默认上限；
4. Zigbee2MQTT 最低版本；
5. 是否支持非 JSON 纯文本；
6. 是否支持受控 Home Assistant MQTT Discovery；
7. 命令验证超时默认值；
8. 是否允许非回环地址使用明文 MQTT；
9. 双向 TLS 是否首期必交；
10. BLE 网关是否指定首选实现；
11. 是否发布设备接入 SDK 和示例固件。

未确认事项不得降低安全默认值：不得开放自由 Topic、不得记录凭据、不得允许未知设备写控制、不得绕过 Family Transaction。

## 20. 产品完成判定

完成后 HomeMind 应同时支持：

    已有完整家庭中枢：设备 → Home Assistant → HomeMind
    已有 MQTT/边缘网关：设备 → MQTT Broker → HomeMind

MQTT 路径的价值不是宣称支持所有小米设备，而是让开放协议设备、Zigbee2MQTT/BLE 网关设备和自研硬件无需部署 Home Assistant，也能安全进入 HomeMind 的家庭设备、权限、审批、自动化和 Agent 体系。

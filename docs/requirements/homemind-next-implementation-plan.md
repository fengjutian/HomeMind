# HomeMind 后续功能实现计划（MiniMax 执行版）

> 目标：在不扩张 V0.1 产品边界的前提下，补齐 HomeMind 当前可确认的实现缺口，并形成可演示、可恢复、可测试的家庭 AI 闭环。
>
> 基线：以当前仓库代码为准。执行前必须先阅读根目录 `AGENTS.md`，其约束高于本文。

## 1. 本轮范围

本轮按以下优先级交付：

1. 补齐持久化资产任务：`THUMBNAIL`、`VISION`、`EMBEDDING`、`FACE_MATCH`、`REINDEX`。
2. 完成资产任务创建、配置持久化、后台执行、失败重试和 UI 管理闭环。
3. 提供一个可实际运行的 NAS/PC 文件 Runtime，打通设备配对、心跳、命令执行与回报。
4. 实现家庭文件 Knowledge Pipeline 的最小闭环：解析、分块、索引、检索和引用。
5. 打通三个端到端 Demo，并补齐文档、可观测性和回归测试。

明确不在本轮实现：家庭机器人、24 小时摄像头、智能门锁、医疗决策、金融交易、大规模 IoT、自研 LLM、自研 Agent Framework、Kafka、Neo4j、独立向量数据库和复杂云同步。不要为这些能力预留抽象或配置项。

## 2. 全局开发规则

MiniMax 执行每个阶段时必须遵守：

- 先阅读相关实现和测试，列出假设，再开始修改。
- 每次只实现一个阶段，保持提交可回滚；未经要求不要提交或推送。
- 领域逻辑放在 `src/homemind/infra/`，HTTP 路由只负责校验、调用和错误映射。
- HomeMind 可以依赖 Octop，Octop Core 不得反向依赖 HomeMind。
- 同步数据库同时支持 SQLite 和 PostgreSQL。新增迁移必须提供 `.sql` 与 `.pg.sql` 配对文件，并保持语义一致。
- 不在 API、日志、任务 cursor 或审计记录中保存模型 API Key、设备 Token 等明文秘密；只保存 provider/device 的公共 ID。
- 所有文件访问都必须先解析为已登记的家庭资产或设备允许的根目录，不接受未经约束的客户端绝对路径。
- 高风险写操作必须继续经过 Permission → Approval → Execute → Verify → Audit。
- 异步函数中不得直接执行阻塞文件、图片或网络操作；使用 `asyncio.to_thread` 或当前项目已有的 executor 模式。
- 用户可见文案必须进入中英文 i18n，不在组件或领域层新增硬编码文案。
- 每阶段完成后先运行定向测试，最终运行 `make all`；前端变更额外运行 `cd dashboard && npx tsc -b`。

## 3. 阶段 A：固定基线与契约

### A1. 建立功能矩阵

先核对以下文件：

- `src/homemind/infra/db/repos/asset_jobs.py`
- `src/homemind/infra/family/asset_jobs.py`
- `src/homemind/infra/family/asset_job_runner.py`
- `src/homemind/infra/family/asset_job_handlers.py`
- `src/homemind/infra/family/photo_intelligence.py`
- `src/homemind/infra/family/thumbnails.py`
- `src/homemind/infra/family/search_indexer.py`
- `src/homemind/api/routers/asset_jobs.py`
- `dashboard/src/pages/Family/AssetsPanel.tsx`
- `dashboard/src/pages/Family/PhotoIntelligencePanel.tsx`

输出一张内部矩阵，逐项记录：输入、持久化配置、执行器、幂等键、输出、权限、重试行为和现有测试。不要把矩阵做成运行时代码。

### A2. 先写失败测试

在 `tests/unit/homemind/test_asset_jobs_persistence.py` 基础上增加测试，证明当前五种任务没有 handler。然后为后续目标写测试骨架：

- 每种任务能被创建、执行并完成。
- 服务重启后可以从数据库恢复任务配置。
- 重复执行同一 item 不产生重复数据或重复副作用。
- 单项失败不阻塞同批其他项。
- 缺少 provider、模型、资产或文件时产生明确的 item error。
- 普通家庭成员不能创建、暂停、恢复、取消或重试管理型任务。
- 任务不能读取其他家庭的资产。

验收：新测试失败的原因必须是功能尚未实现，而不是 fixture、导入或数据库错误。

## 4. 阶段 B：修正资产任务数据契约

当前 `AssetJobCreateBody` 只有 `job_type/source_id/paths`，不足以在重启后恢复 AI 任务。先完成契约，再实现 handler。

### B1. 持久化非敏感任务配置

优先复用 `homemind_asset_jobs.cursor_json`，将其明确为版本化 job payload；如果现有 cursor 还承担扫描游标职责，则新增 `config_json` 列，不能混用两个语义。

推荐配置结构：

```json
{
  "version": 1,
  "vision_provider_id": 12,
  "vision_model": "model-name",
  "embedding_provider_id": 13,
  "embedding_model": "embedding-model",
  "geocoder": "nominatim",
  "thumbnail_width": 512,
  "thumbnail_height": 512,
  "thumbnail_format": "webp"
}
```

要求：

- API Key 继续从 Octop provider repo 动态读取，禁止复制到 HomeMind 表。
- 配置在任务创建时做完整校验，worker 执行时再次检查引用是否仍有效。
- 配置 JSON 使用 Pydantic 模型解析，不在 handler 内散落字典索引。
- 未知版本、未知字段策略和必填字段错误应返回稳定的 HomeMind 错误码。

如果新增列：

- 修改当前未发布迁移，或按仓库发布状态新增下一号迁移；先确认版本是否已经发布，不要自行猜测。
- 同时修改 SQLite/PostgreSQL 迁移。
- 更新 `src/homemind/infra/db/migrate.py` 的兼容逻辑与迁移版本测试。

### B2. 按任务类型播种 item

重构任务创建逻辑：

- `SCAN`：输入 asset source，流式枚举源文件路径。
- `METADATA`：输入已登记 asset ID；路径从数据库资产记录获得。
- `THUMBNAIL`：输入图片 asset ID。
- `VISION`：输入图片 asset ID，并要求 vision provider/model。
- `EMBEDDING`：输入图片 asset ID，并要求 embedding provider/model。
- `FACE_MATCH`：输入图片 asset ID；家庭必须存在有效的成员参考照片。
- `REINDEX`：输入 asset ID，或由 family scope 流式播种全部可见资产；不要把整个家庭数据塞进单个 item。

API 不再允许 AI 任务仅凭任意绝对路径执行。`paths` 只保留给兼容的扫描入口；其余任务使用 `asset_ids`。如果需要保持已有客户端兼容，应在一个版本周期内接受旧字段并返回弃用提示，而不是悄悄改变含义。

### B3. API 与 OpenAPI

修改 `src/homemind/api/routers/asset_jobs.py`：

- 使用判别联合或按任务类型严格验证的请求模型。
- 所有路由保留清晰的 `summary`、`description`、typed response model。
- 创建任务仍返回 `202`。
- 对空任务定义一致行为：建议创建后直接标记 `COMPLETED`，进度为 100%，并记录 0 items。
- 错误使用 HomeMind/Octop 错误体系，不直接向用户返回裸 `ValueError`。

### B4. 本阶段测试

- SQLite 与 PostgreSQL SQL 语义测试。
- 请求模型的合法/非法组合参数化测试。
- 跨家庭 asset ID 拒绝测试。
- provider 被删除或禁用后的执行失败测试。
- 重启后 config 解析测试。

验收命令：

```bash
uv run pytest tests/unit/homemind/test_asset_jobs_persistence.py -q
uv run pytest tests/unit/homemind/test_migration_pairing.py -q
```

## 5. 阶段 C：补齐五种资产 Job Handler

所有 handler 放在 `src/homemind/infra/family/asset_job_handlers.py` 或按职责拆成同目录小模块。不要让 runner 包含业务实现。

### C1. THUMBNAIL

实现步骤：

1. 通过 `item.asset_id` 加载资产并验证属于 job family。
2. 只处理受支持的图片 MIME/扩展名；非图片标记 `SKIPPED`，不是失败。
3. 复用 `ThumbnailService`，不要另写图片缩放逻辑。
4. 缓存路径必须基于 asset ID、内容 hash、尺寸和格式，原文件内容变化后必须失效。
5. Pillow 解码错误仅使当前 item 失败。
6. 重复执行命中缓存并成功返回。

测试：缓存命中、内容变更、损坏图片、非图片、越权资产、无 Pillow 环境。

### C2. VISION

实现步骤：

1. 从 job config 加载 provider ID 和 model。
2. 经 provider repo 获取配置，并构造 `OpenAICompatibleVisionProvider`。
3. 复用 `PhotoIntelligenceService.analyze`，不得复制 HTTP 调用代码。
4. 调用前执行家庭隐私策略检查；禁止外部视觉时 item 失败且写入可识别错误。
5. 保存描述、objects、scenes、faces、provider provenance 和分析时间。
6. 使用内容 hash + provider + model 判断是否需要重跑。
7. 外部调用必须进入 external processing audit。

测试：隐私拒绝、provider 禁用、超时、非法 JSON、重复执行、图片内容变化、审计记录。

### C3. EMBEDDING

实现步骤：

1. 从 job config 恢复 embedding provider/model。
2. 复用 `OpenAICompatibleEmbeddingProvider` 和现有 photo intelligence/indexer。
3. 校验向量非空、值可转为 float、维度稳定。
4. 同时更新照片智能记录和统一搜索索引需要的 embedding provenance。
5. 模型或维度改变时替换旧向量，不混用不同模型进行相似度比较。
6. 经隐私开关和外部处理审计。

测试：维度变化、空向量、不同模型隔离、内容 hash 幂等、隐私拒绝、审计记录。

### C4. FACE_MATCH

实现步骤：

1. 加载家庭成员的参考照片，只允许读取同一家庭且用户有权访问的资产。
2. 没有参考照片时将 item 标记 `SKIPPED`，并提供明确原因。
3. 复用现有 `FaceRecognitionProvider` 协议和视觉 provider 实现。
4. 限制参考照片数量，按现有 provider 上限分批或选择高质量参考图。
5. 只保存达到阈值的结果；低置信度结果保留为待确认候选，不能自动确认为家庭成员。
6. 增加确认/拒绝候选的领域方法和 API；确认行为写审计。
7. 人脸数据按敏感数据处理，必须检查家庭隐私策略。

如果当前 schema 不能表达“候选/已确认/已拒绝”，先添加最小迁移和 repo；不要把状态塞进无结构描述文本。

测试：无参考图、跨家庭参考图、低置信度、确认、拒绝、隐私关闭、重复识别。

### C5. REINDEX

实现步骤：

1. 复用 `FamilySearchIndexer.index_asset`；不在 handler 中拼搜索文档。
2. 每个 item 只处理一个实体，确保可重试。
3. 对已删除资产清除对应索引记录。
4. family 全量重建先流式播种 item，再逐项 upsert；完成后清理此次快照中已不存在的孤儿索引。
5. 清理操作必须限定 `family_id`，避免跨家庭误删。

测试：新建、更新、删除、重复运行、两个家庭隔离、中途重启恢复。

### C6. Handler 工厂与 runner

调整 `build_handler` 和 `AssetJobRunner`：

- 根据 job row 而不是仅 `job_type` 构造 handler，使其能读取 job config。
- 注入 provider repo、photo intelligence、thumbnail service、search indexer 等现有服务。
- runner 启动时恢复 stale job；单个不可构造的 job 标记失败，不影响 worker 循环。
- 任务结束发出已有的 progress/completed/failed 事件，供 Dashboard 实时刷新。
- 日志包含 job ID/type/family ID，但不记录文件内容、API Key 或人脸特征。

本阶段验收：七种 job type 都不再落入 `NotImplementedError`。

## 6. 阶段 D：资产任务 Dashboard

优先在现有 Family/Photos 页面复用组件，不新增重复导航。

实现：

- 新增 typed API：创建任务、列表、详情、items、暂停、恢复、取消、重试。
- 展示任务类型、状态、进度、成功/跳过/失败数和错误摘要。
- 失败项支持分页/筛选，不一次加载全部 items。
- 管理员可发起扫描、元数据刷新、缩略图、视觉分析、Embedding、人脸匹配和重建索引。
- 根据任务类型显示必要 provider/model 字段。
- 普通成员只读；按钮权限与后端权限保持一致，但不能依赖前端作为安全边界。
- 接收家庭事件后局部刷新，不做高频全页轮询；无事件连接时使用有上限的退避轮询。
- 中英文文案同步加入 locale 文件。

建议文件：

- `dashboard/src/api/modules/homeMindFamily.ts`
- `dashboard/src/pages/Family/AssetsPanel.tsx`
- `dashboard/src/pages/Family/PhotoIntelligencePanel.tsx`
- 新建 `dashboard/src/components/family/AssetJobsPanel.tsx`（仅当两个页面都要复用）

测试：请求参数、状态渲染、权限按钮、失败筛选、事件刷新、错误提示。

验收命令：

```bash
cd dashboard && npx tsc -b
cd dashboard && npm run lint
```

## 7. 阶段 E：NAS/PC 文件 Runtime 最小实现

目标不是开发完整桌面产品，而是提供一个可部署、无 UI 的 Runtime，证明 HomeMind 能安全地在登记设备上执行文件任务。

### E1. 运行形态

新增独立入口，建议：

```bash
homemind device-runtime run --server https://host --pairing-code CODE --root /data/family
```

代码放在 `src/homemind/runtime/` 或 `src/homemind/device_runtime/`；不得放入 API router。配置保存在 HomeMind 自己的数据目录，Token 使用权限受限文件或系统凭据存储，日志不得输出 Token。

### E2. 配对与生命周期

实现顺序：

1. 使用一次性 pairing code 注册设备。
2. 安全保存 device token 和 device ID。
3. 周期心跳，报告 runtime version、能力和健康状态。
4. 长轮询领取命令；后续有必要再增加 WebSocket，不要一开始双协议。
5. 领取后立即 ACK 为 `RUNNING`。
6. 执行完成后报告 `SUCCEEDED/FAILED` 和结构化摘要。
7. 支持 Token 轮换和撤销；401 后停止执行，不无限重试。
8. 崩溃恢复时依赖 server lease，Runtime 不自行猜测命令成功。

### E3. 第一批命令

仅实现：

- `filesystem.list`
- `filesystem.stat`
- `filesystem.hash`
- `filesystem.scan`
- `filesystem.mkdir`
- `filesystem.move`
- `filesystem.copy`

删除操作暂不直接实现；如必须支持，只能移动到设备根目录内的 `.homemind-trash`，并经过审批。

安全要求：

- 所有路径解析后必须位于配置的 root 内。
- 拒绝 `..` 越界、符号链接逃逸、Windows junction/reparse point 逃逸和大小写绕过。
- Windows/Linux/macOS 使用 `pathlib` 与平台测试，不硬编码 `/`。
- 写操作先生成预览，审批后执行，再校验目标状态并上报验证结果。
- 命令必须幂等；move/copy 使用 command ID 或预期 hash 防止重复执行。

### E4. 打包与运维

- 提供前台运行命令和健康检查。
- systemd/Windows service/NAS 容器脚本可以后续分开交付；本阶段至少提供 Docker 示例。
- 增加 graceful shutdown，正在执行的命令完成或在 lease 到期后可恢复。
- 增加 runtime 版本兼容检查，拒绝高于自身协议版本的未知命令。

测试：fake server 契约测试、路径逃逸、Token 撤销、lease、重复命令、网络断开、跨平台路径。

## 8. 阶段 F：家庭文件 Knowledge Pipeline

只实现可用的最小版本，不引入独立向量数据库。

### F1. 支持格式

第一批只支持：

- `.txt`
- `.md`
- 文本型 `.pdf`
- `.docx`

扫描版 PDF 的 OCR 放到后续子阶段；不要让 OCR 阻塞首个可用版本。

### F2. 数据模型

新增最小实体：

- knowledge document：关联 `family_id`、`asset_id`、content hash、parser version、状态和错误。
- knowledge chunk：关联 document ID、序号、文本、页码/段落等定位信息。
- embedding provenance：model、dimensions、version、content hash。

公共 ID 使用字符串，内部自增 ID 仅作 PK；子表外键指向公共字符串 ID，遵守仓库资源表约定。

### F3. Pipeline

实现步骤：

1. 从已授权的家庭 asset 读取文件，不接受调用者任意路径。
2. 按格式调用解析器，限制单文件大小、页数和解析时间。
3. 规范化文本但保留页码/标题/段落定位。
4. 使用稳定、可测试的分块策略；chunk ID 由 document + parser version + chunk index 派生或稳定生成。
5. 保存全文索引。
6. 配置 embedding 时生成向量；未配置时全文搜索仍可用。
7. 文件 hash 未变则跳过；变化时原子替换旧 chunks。
8. 文件删除时清理 document/chunks/index。
9. 搜索结果返回 asset ID、文件名、chunk 摘要和定位信息。
10. Agent 回答必须保留引用，不把检索内容误写成家庭事实记忆。

### F4. 隐私与权限

- 外部 embedding 必须经过家庭隐私开关并记录 external processing audit。
- 搜索结果在查询时再次执行家庭与资产可见性过滤。
- 导出家庭数据时包含文档元数据和可选正文，但继续排除凭据。
- 成员失去访问权后不能依赖旧索引继续检索内容。

### F5. UI

在 Files 页面增加：索引状态、最后处理时间、错误、重新索引和“在家庭知识中搜索”。不要复制 Octop Knowledge Base 整套 UI。

测试：四种格式、坏文件、超大文件、更新、删除、权限变化、无 embedding 降级、引用定位。

## 9. 阶段 G：三个端到端 Demo

### Demo 1：家庭问答

数据准备：家庭、两名成员、关系、事件、记忆和一份家庭文档。

流程：用户提问 → resolve active family → family context → 搜索事件/记忆/文档 → 返回带来源答案。

验收：另一个无权限用户不能得到任何家庭数据；回答能指出来自事件、记忆还是文件。

### Demo 2：家庭照片搜索

流程：登记照片目录 → SCAN → METADATA → THUMBNAIL → VISION → EMBEDDING → 搜索“去年日本旅行在海边的照片”。

验收：任务可暂停和恢复；服务重启不丢进度；搜索结果可预览原图或缩略图；禁用外部视觉后不发生外呼。

### Demo 3：AI 整理照片

流程：搜索候选照片 → 生成整理计划 → 显示移动/建相册/重复标记预览 → 管理员审批 → NAS/PC Runtime 执行 → hash/路径验证 → 写审计。

验收：默认不删除原文件；拒绝审批不执行；重复提交不重复移动；执行失败可进入 `NEEDS_REVIEW` 或安全补偿状态。

Demo 应实现为自动化 integration tests 加一份人工演示说明，而不是只录视频或只写文字步骤。

## 10. 阶段 H：可观测性、文档与发布收口

补充指标：

- 各 job type 创建、成功、失败、跳过和耗时。
- provider 调用次数、失败和延迟，不记录请求正文。
- Runtime 在线设备、命令排队、审批等待、执行成功/失败、lease 回收。
- Knowledge 文档/分块数量、解析失败、索引延迟。

补充文档：

- 更新 `docs/api.md` 的 HomeMind API。
- 新增设备 Runtime 部署和配对说明。
- 新增照片 Pipeline 和隐私开关说明。
- 新增 Knowledge Pipeline 支持格式与限制。
- 在 README 中只声明已通过验收的能力，不能提前把计划写成已发布功能。

最终检查：

```bash
uv run pytest tests/unit/homemind -q
uv run pytest tests/integration/test_homemind_family_api.py -q
uv run pytest tests/integration/test_homemind_device_api.py -q
cd dashboard && npx tsc -b
make all
```

若 `make all` 因环境外部依赖失败，必须记录准确命令、错误和已经通过的定向测试；不能写“应该通过”。

## 11. 推荐实施批次

### 批次 1：资产任务可用

- 阶段 A、B、C1、C5。
- 交付缩略图与重建索引。
- 风险较低，可先验证数据契约和 worker 机制。

### 批次 2：AI 照片流水线

- C2、C3、C4、D。
- 交付视觉、Embedding、人脸候选和任务 UI。
- 必须重点验证隐私、审计、限流和恢复。

### 批次 3：设备执行闭环

- 阶段 E。
- 先交付文件读取类命令，再交付审批保护的写命令。

### 批次 4：文件知识化

- 阶段 F。
- 先全文检索，再增加 embedding；OCR 单独作为后续增量。

### 批次 5：产品验收

- 阶段 G、H。
- 三个 Demo、全量测试、文档和发布检查。

## 12. MiniMax 每次任务的输出格式

每完成一个阶段，回复必须包含：

1. 实现结论。
2. 修改文件及各自作用。
3. 数据库/API 兼容性影响。
4. 安全与隐私处理。
5. 实际运行的测试命令和结果。
6. 未完成项或阻塞项。
7. 下一阶段建议，但不要擅自开始范围外工作。

推荐首条指令：

```text
请先完整阅读 AGENTS.md 和 docs/requirements/homemind-next-implementation-plan.md。
本次只执行“阶段 A：固定基线与契约”，不要实现阶段 B 以后内容。
先检查当前代码和测试，陈述假设，然后补充能准确暴露五种缺失 asset job handler 的失败测试。
修改必须最小化；完成后运行计划中阶段 A 的定向测试，并按第 12 节格式汇报。
```


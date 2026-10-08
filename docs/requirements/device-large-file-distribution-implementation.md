# HomeMind 异步设备大文件分发实施说明

状态：实施草案  
目标读者：负责实现的 MiniMax 编码代理、代码评审者和测试人员  
目标规模：单个约 5 GiB 的视频或普通文件  

## 1. 实施目标

为已配对的 HomeMind 异步设备提供可靠的大文件拉取能力。设备从 HomeMind 获取一次下载任务和短期授权，然后通过 HTTP Range 分块并发下载，支持暂停、断点续传、完整性校验、令牌刷新、失败重试和最终回执。

首版采用“HomeMind 控制面授权，资源节点数据面传输”的结构：

```text
设备运行时
  │ 设备长期令牌
  ▼
HomeMind 控制面
  │ 鉴权、资源授权、创建任务、签发短期凭证
  ▼
下载清单
  │
  ├─ 本地资源：HomeMind 专用 Range 端点
  └─ 对象存储：短期签名 URL，具备能力时启用
```

首版必须交付本地资源的完整闭环。对象存储直传通过接口抽象预留，可以在第二阶段实现。不得为了追求理论速度引入 P2P、设备互换分片、NAT 打洞或自建 CDN。

## 2. 当前实现基线

实现前必须阅读并核对以下位置：

- `src/homemind/api/routers/families.py`：现有 `GET /{family_id}/assets/{asset_id}/content` 使用 `FileResponse` 返回本地资源。
- `src/homemind/infra/family/assets.py`：`FamilyAssetManager.local_content_path()` 仅接受 `file://` 且已索引的文件。
- `src/homemind/api/routers/runtime.py`：设备运行时以 Bearer 设备令牌访问心跳、命令和回执接口。
- `src/homemind/infra/family/device_runtime.py`：设备令牌认证、轮换、撤销与命令租约逻辑。
- `src/homemind/infra/db/migrations/`：HomeMind 迁移当前包含 `024`，开始编码前必须确认它是否已经发布。
- `docs/requirements/homemind-next-capabilities-plan.md`：上传会话的分片、散列和恢复设计。下载可以复用概念，不得复用上传状态机或 staging 文件。

当前家庭资源接口面向登录用户，不能直接作为设备分发协议。设备下载需要独立的设备权限校验、任务状态、短期数据面凭证和进度回执。

## 3. 范围和明确排除项

### 3.1 首版范围

1. 已配对设备通过设备令牌创建或领取下载任务。
2. 支持服务器本地 `file://` 家庭资源。
3. 支持 `HEAD` 和单区间 `Range` 请求。
4. 支持多个单区间请求并发下载同一个文件。
5. 支持断点续传、资源版本检查和完整文件 SHA-256 校验。
6. 支持任务状态、进度上报、短期凭证刷新、完成和失败回执。
7. 支持设备撤销、家庭权限变化、资源删除和任务过期。
8. 提供 SQLite 与 PostgreSQL 等价迁移、领域服务、API、单元测试和集成测试。

### 3.2 首版不做

- 不做多区间 `multipart/byteranges` 响应；一次请求只接受一个 Range。
- 不做设备间 P2P、局域网发现、NAT 打洞或 BitTorrent 协议。
- 不做边下边转码；文件按原始字节分发。
- 不由 HomeMind 自动复制用户原文件到新的媒体库。
- 不在数据库保存分片完成位图；位图属于设备本地状态。
- 不让设备通过普通用户 JWT 下载。
- 不在下载 URL 中携带设备长期令牌。
- 不要求首版支持远程 S3、COS、OSS、OBS 资源，但领域接口必须允许后续增加。

## 4. 不可变设计原则

1. 设备身份来源只能是 Bearer 设备令牌，不能从请求体接受 `device_id` 后直接信任。
2. 每个传输任务固定绑定一个设备、一个家庭和一个资源。
3. 数据面使用随机、短期、可撤销的下载凭证；数据库只保存其哈希。
4. 资源版本由大小、修改时间和内容散列共同描述，续传前必须验证版本没有变化。
5. 数据库提交任务后再返回下载清单；进度回执失败不能破坏文件下载。
6. 下载端点按区间流式读取，内存占用不能随文件大小增长。
7. 所有路径都由服务端从资源记录解析，客户端不能提交任意本地路径。
8. 家庭资源权限、设备状态和凭证状态必须在数据请求时再次校验，不能只在任务创建时校验一次。
9. HomeMind 领域逻辑放在 `src/homemind/infra/`；router 只负责 HTTP 参数、鉴权入口和错误映射。
10. Octop Core 不得导入 `homemind`。

## 5. 下载流程

### 5.1 标准流程

```text
1. 设备轮询或接收资源下载命令
2. POST /api/device-runtime/transfers
3. 服务端认证设备并验证该资源属于同一家庭且可读
4. 服务端创建或返回幂等传输任务
5. 服务端签发短期数据面凭证并返回 manifest
6. 设备 HEAD 数据端点，核对大小、ETag 和 Range 能力
7. 设备按 16 MiB 划分区间，默认并发 4 路下载
8. 设备每 5 秒或每增加 5% 上报一次聚合进度
9. 设备校验文件 SHA-256，原子重命名临时文件
10. POST complete 上报结果
```

### 5.2 恢复流程

设备本地保存任务元数据和完成分片位图。进程重启后：

1. 调用任务查询接口。
2. 若短期凭证过期，调用 refresh。
3. 重新执行 `HEAD`。
4. 只有 `asset_id`、`size_bytes`、`etag`、`sha256` 和 `chunk_size` 均匹配时才能复用本地临时文件。
5. 只请求未完成分片。
6. 若资源版本变化，删除或隔离旧临时文件，从零开始。

### 5.3 状态机

```text
PENDING ──首次 manifest──> ACTIVE
ACTIVE  ──完成回执──────> COMPLETED
ACTIVE  ──可重试失败────> ACTIVE
ACTIVE  ──不可恢复失败──> FAILED
PENDING/ACTIVE ──设备撤销、资源删除或人工取消──> CANCELLED
PENDING/ACTIVE ──超过任务 TTL──> EXPIRED
```

终态为 `COMPLETED`、`FAILED`、`CANCELLED` 和 `EXPIRED`。终态请求必须幂等。`COMPLETED` 不因重复 complete 回执变回其他状态。

## 6. 数据模型

### 6.1 迁移策略

先确认 HomeMind `024` 是否尚未发布：

- 如果 `024` 尚未发布到任何需要兼容的数据库，按仓库规则将本功能折叠进 `024` 的 SQLite 与 PostgreSQL 迁移。
- 如果 `024` 已发布，新增下一对编号迁移。
- 不得只添加 SQLite 或只添加 PostgreSQL。
- 更新迁移配对和 schema 水位测试，不手工假设最新版本号。

### 6.2 `homemind_asset_transfers`

建议字段：

| 字段 | SQLite | PostgreSQL | 说明 |
| --- | --- | --- | --- |
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | identity bigint | 内部代理主键 |
| `transfer_id` | `TEXT NOT NULL UNIQUE` | `TEXT NOT NULL UNIQUE` | 公共 ULID |
| `family_id` | `TEXT NOT NULL` | `TEXT NOT NULL` | 外键到家庭公共 ID |
| `asset_id` | `TEXT NOT NULL` | `TEXT NOT NULL` | 外键到家庭资源公共 ID |
| `device_id` | `TEXT NOT NULL` | `TEXT NOT NULL` | 外键到家庭设备公共 ID |
| `request_key` | `TEXT NOT NULL` | `TEXT NOT NULL` | 设备提供的幂等键 |
| `status` | `TEXT NOT NULL` | `TEXT NOT NULL` | 状态机值 |
| `source_kind` | `TEXT NOT NULL` | `TEXT NOT NULL` | 首版为 `LOCAL_FILE` |
| `size_bytes` | `INTEGER NOT NULL` | `BIGINT NOT NULL` | 创建任务时的文件大小 |
| `sha256` | `TEXT NOT NULL` | `TEXT NOT NULL` | 完整文件散列 |
| `source_mtime_ns` | `INTEGER` | `BIGINT` | 本地文件版本信息 |
| `etag` | `TEXT NOT NULL` | `TEXT NOT NULL` | 不透明资源版本 |
| `chunk_size` | `INTEGER NOT NULL` | `INTEGER NOT NULL` | manifest 建议分片大小 |
| `bytes_reported` | `INTEGER NOT NULL DEFAULT 0` | `BIGINT NOT NULL DEFAULT 0` | 设备最后报告进度 |
| `last_progress_at` | `INTEGER` | `BIGINT` | UTC epoch seconds |
| `expires_at` | `INTEGER NOT NULL` | `BIGINT NOT NULL` | 任务过期时间 |
| `completed_at` | `INTEGER` | `BIGINT` | 完成时间 |
| `failure_code` | `TEXT` | `TEXT` | 稳定错误码 |
| `failure_detail` | `TEXT` | `TEXT` | 长度受限且不得含令牌和路径 |
| `created_at` | `INTEGER NOT NULL` | `BIGINT NOT NULL` | UTC epoch seconds |
| `updated_at` | `INTEGER NOT NULL` | `BIGINT NOT NULL` | UTC epoch seconds |

约束和索引：

- 唯一约束 `(device_id, request_key)`。
- 索引 `(device_id, status, created_at)`。
- 索引 `(family_id, asset_id, status)`。
- `bytes_reported` 必须处于 `0..size_bytes`。
- 子表外键必须引用公共字符串 ID，不能引用父表整数代理主键。

### 6.3 `homemind_asset_transfer_tokens`

建议字段：

| 字段 | 说明 |
| --- | --- |
| `id` | 整数代理主键 |
| `token_id` | 公共 ULID |
| `transfer_id` | 传输公共 ID |
| `token_hash` | 使用项目既有安全哈希方式生成的指纹 |
| `expires_at` | 凭证过期时间 |
| `revoked_at` | 主动撤销时间 |
| `created_at` | 创建时间 |

每次 refresh 撤销旧凭证并创建新凭证。明文凭证仅在创建响应中出现一次，不得记录到日志或数据库。

## 7. Repo 与领域服务

### 7.1 建议文件

```text
src/homemind/infra/db/repos/asset_transfers.py
src/homemind/infra/family/asset_transfers.py
src/homemind/api/routers/asset_transfers.py
tests/unit/homemind/test_asset_transfer_repo.py
tests/unit/homemind/test_asset_transfers.py
tests/unit/homemind/test_asset_transfer_api.py
tests/integration/test_asset_transfer_contract.py
```

按仓库实际 `SharedServices` 组织方式注册 repo 和 manager，不在 router 中直接实例化 repo。

### 7.2 Repo 职责

Repo 只包含 SQL 和行映射，至少提供：

```python
create(...)
get(transfer_id)
get_by_request_key(device_id, request_key)
list_for_device(device_id, status=None, limit=...)
activate(transfer_id, now)
advance_progress(transfer_id, bytes_reported, now)
complete(transfer_id, now)
fail(transfer_id, code, detail, now)
cancel_for_asset(asset_id, now)
cancel_for_device(device_id, now)
expire_before(now, limit)
issue_token(...)
resolve_active_token(token_hash, now)
revoke_tokens(transfer_id, now)
```

`advance_progress` 使用单调更新，较小的 `bytes_reported` 不得覆盖较大的值。终态更新需要条件 SQL，防止并发回执把状态改回活动态。

### 7.3 Manager 职责

`AssetTransferManager` 负责：

- 通过已认证设备推导 `device_id` 和 `family_id`。
- 获取资源并验证家庭一致性和设备读取权限。
- 使用 `FamilyAssetManager.local_content_path()` 解析受控本地路径。
- 在执行文件 stat 和散列时使用 `asyncio.to_thread()` 或执行器，不能阻塞事件循环。
- 创建幂等任务并生成 manifest。
- 签发、刷新和验证短期数据面凭证。
- 再次校验设备未撤销、资源仍存在、资源版本未变化。
- 处理进度、完成、失败、取消和过期。
- 记录低基数指标和不含敏感信息的审计事件。

不要把设备身份认证逻辑复制一份。应从现有 `DeviceRuntimeManager` 提取或复用一个返回已认证设备的公共领域方法；该方法仍应保证设备只能操作自身任务。

## 8. 资源版本与散列

### 8.1 SHA-256 来源

家庭资源已有可信 `content_hash` 且算法为 SHA-256 时直接复用。若缺失或算法不确定：

1. 以 1 MiB 或更大块流式读取文件。
2. 在工作线程中计算 SHA-256。
3. 不把整个文件读入内存。
4. 将结果更新到资源元数据或传输记录，避免每次任务重复计算。

若 5 GiB 文件首次计算散列耗时明显，创建任务可以返回 `PREPARING` 并由后台作业完成。首版若选择同步准备，必须设置超时和可观察指标，且 API 文档应说明可能耗时。

### 8.2 ETag

ETag 由服务端生成，不直接暴露本地路径：

```text
etag = quoted(base64url(sha256(asset_id | size_bytes | mtime_ns | sha256)))
```

HTTP 返回带双引号的强 ETag。资源 stat 或散列任一变化都生成新 ETag。

## 9. API 契约

设备运行时 API 使用现有设备 Bearer 令牌。数据面接口只使用短期传输凭证，不接受普通用户 JWT 作为替代。

### 9.1 创建传输任务

```http
POST /api/device-runtime/transfers
Authorization: Bearer <device-token>
Content-Type: application/json

{
  "asset_id": "01...",
  "request_key": "device-generated-uuid"
}
```

成功返回 `201`；幂等重试返回同一任务，可使用 `200`，但契约测试必须固定行为：

```json
{
  "transfer_id": "01...",
  "status": "ACTIVE",
  "asset": {
    "asset_id": "01...",
    "name": "family-video.mp4",
    "mime_type": "video/mp4",
    "size_bytes": 5368709120,
    "sha256": "hex...",
    "etag": "\"opaque-version\""
  },
  "download": {
    "url": "/api/device-runtime/transfers/01.../content",
    "token": "one-time-visible-secret",
    "expires_at": 1791432000,
    "chunk_size": 16777216,
    "max_concurrency": 4,
    "accept_ranges": true
  }
}
```

### 9.2 查询任务

```http
GET /api/device-runtime/transfers/{transfer_id}
Authorization: Bearer <device-token>
```

只能查询属于当前已认证设备的任务。响应不返回仍有效的旧明文下载凭证。

### 9.3 刷新下载凭证

```http
POST /api/device-runtime/transfers/{transfer_id}/refresh
Authorization: Bearer <device-token>
```

只允许 `PENDING` 或 `ACTIVE` 任务刷新。刷新后旧凭证立即失效。返回新的数据面凭证和相同资源 manifest；如果资源版本已变化，返回稳定冲突错误并将旧任务取消或失败。

### 9.4 聚合进度

```http
POST /api/device-runtime/transfers/{transfer_id}/progress
Authorization: Bearer <device-token>
Content-Type: application/json

{
  "bytes_downloaded": 1073741824
}
```

进度只允许单调增加，不要求服务端保存分片位图。重复或较小进度返回当前值，不报服务器错误。

### 9.5 完成与失败

```http
POST /api/device-runtime/transfers/{transfer_id}/complete
Authorization: Bearer <device-token>

{
  "size_bytes": 5368709120,
  "sha256": "hex..."
}
```

服务端核对 manifest 后标记完成并撤销所有数据面凭证。重复完成必须幂等。

```http
POST /api/device-runtime/transfers/{transfer_id}/fail
Authorization: Bearer <device-token>

{
  "code": "HASH_MISMATCH",
  "detail": "bounded diagnostic text"
}
```

失败详情限制长度，去除 URL 查询参数、令牌和本地路径。

### 9.6 数据面 HEAD

```http
HEAD /api/device-runtime/transfers/{transfer_id}/content
Authorization: Transfer <short-lived-token>
```

必须返回：

```http
200 OK
Content-Length: 5368709120
Content-Type: video/mp4
Accept-Ranges: bytes
ETag: "opaque-version"
Cache-Control: private, no-store
X-Content-Type-Options: nosniff
```

### 9.7 数据面 GET

完整请求可返回 `200`，但设备客户端必须优先使用 Range。单区间请求示例：

```http
GET /api/device-runtime/transfers/{transfer_id}/content
Authorization: Transfer <short-lived-token>
Range: bytes=0-16777215
If-Match: "opaque-version"
```

成功响应：

```http
206 Partial Content
Content-Length: 16777216
Content-Range: bytes 0-16777215/5368709120
Accept-Ranges: bytes
ETag: "opaque-version"
Content-Type: video/mp4
Cache-Control: private, no-store
X-Content-Type-Options: nosniff
```

## 10. Range 解析和响应规则

实现一个独立、可单元测试的 Range 解析器，不把复杂逻辑写在 router 中。

### 10.1 支持形式

- `bytes=0-99`
- `bytes=100-`
- `bytes=-100`

### 10.2 拒绝形式

- 非 `bytes` 单位。
- 多区间，例如 `bytes=0-99,200-299`。
- 非数字、负数语义错误、起点大于终点。
- 起点超出文件末尾。
- 解析后长度为零。

不可满足的合法 Range 返回 `416`，并包含：

```http
Content-Range: bytes */5368709120
```

多区间首版统一返回 `416` 或明确的 `400`，选择后用契约测试锁定。建议使用 `416`。

### 10.3 流式读取

- 使用普通文件句柄 `seek(start)` 后循环读取固定大小块，建议每块 256 KiB 至 1 MiB。
- 生成器累计输出不得超过选定区间长度。
- 客户端断开时尽快关闭文件句柄。
- 文件读取属于阻塞 I/O；不能直接在 async 事件循环中长时间读。可使用 Starlette 的文件响应能力，或以线程池桥接受控迭代器。
- 即使当前 Starlette `FileResponse` 支持 Range，也要用专用服务和契约测试固定所需行为，不能依赖未验证的隐式版本特性。
- 禁止 `Path.read_bytes()`、`await upload.read()` 一次性读取完整文件。

## 11. 短期凭证设计

### 11.1 凭证格式

使用至少 256 bit 的密码学随机值，例如 `secrets.token_urlsafe(32)`。数据库保存项目既有方式产生的哈希或 HMAC 指纹，不保存明文。

### 11.2 默认时限

- 数据面凭证 TTL：10 分钟。
- 传输任务 TTL：24 小时无进度后过期。
- 只要任务仍为 `ACTIVE`，设备可凭长期设备令牌刷新数据面凭证。

### 11.3 每次数据请求校验

每个 HEAD 或 GET 至少校验：

1. 凭证存在、未过期、未撤销。
2. 传输任务处于 `PENDING` 或 `ACTIVE`。
3. 设备凭证未被撤销，设备仍属于对应家庭。
4. 资源仍存在且仍属于同一家庭。
5. 本地文件仍在允许路径内，且 stat 与任务版本一致。
6. `If-Match` 存在时必须与任务 ETag 一致，否则返回 `412`。

如果每个 Range 都访问多张表带来压力，可以加入最长 5 秒的正向认证缓存，但撤销动作必须主动清除缓存。首版可先不缓存，以正确性优先。

## 12. 设备客户端实现要求

设备端可以是后续独立项目，但服务端契约测试应提供参考算法。

### 12.1 本地文件

- 最终路径旁创建 `.part` 临时文件。
- 预分配到 `size_bytes`，磁盘空间不足时在下载前失败。
- 每个 worker 只写自己负责的 offset 区间。
- Windows 和 POSIX 均使用支持随机写入的文件 API，不依赖稀疏文件语义。
- 全部校验成功后原子替换最终文件。

### 12.2 本地状态

状态文件至少保存：

```json
{
  "transfer_id": "01...",
  "asset_id": "01...",
  "size_bytes": 5368709120,
  "sha256": "hex...",
  "etag": "\"opaque-version\"",
  "chunk_size": 16777216,
  "completed_chunks": [0, 1, 2, 9]
}
```

状态文件更新使用临时文件加原子替换，避免掉电后 JSON 损坏。每个分片写入并刷盘后才能标记完成。

### 12.3 并发与重试

默认值：

| 参数 | 默认值 |
| --- | ---: |
| 分片大小 | 16 MiB |
| 并发数 | 4 |
| 单分片最大尝试次数 | 5 |
| 请求连接超时 | 10 秒 |
| 单次读取超时 | 30 秒 |
| 进度上报间隔 | 5 秒或增长 5% |

只对连接失败、超时、`429` 和 `5xx` 执行有上限的指数退避。`401`、`403`、`404`、`412` 和 `416` 不盲目重试：

- `401`：使用设备长期令牌刷新一次数据面凭证。
- `403`：停止并报告权限失败。
- `404`：停止并报告资源不存在。
- `412`：资源版本变化，废弃本地续传状态。
- `416`：重新获取 manifest；若仍不一致则停止。

### 12.4 完整性

首版以完整文件 SHA-256 为最终判定。若后续需要每个分片即时校验，可在 manifest 增加分片 Merkle tree 或分片散列列表；不要在首版为 320 个分片强制增加大型数据库子表。

## 13. 对象存储扩展接口

不要在首版本地文件实现中硬编码“URL 一定是 HomeMind API”。定义数据源描述：

```python
@dataclass(frozen=True)
class TransferSource:
    kind: Literal["LOCAL_FILE", "SIGNED_URL"]
    url: str
    headers: dict[str, str]
    expires_at: int
    supports_range: bool
```

建议领域接口：

```python
class TransferSourceProvider(Protocol):
    async def prepare_source(
        self,
        *,
        asset: FamilyAssetRow,
        transfer: AssetTransferRow,
    ) -> TransferSource: ...
```

首版只实现 `LocalFileTransferSourceProvider`。未来对象存储实现应生成短期签名 URL，让设备直接访问对象存储，HomeMind 不代理字节。不要把 Octop Agent workspace 的 `BackendWorkspace` 强行当作家庭资源存储；两者权限和资源生命周期不同。

## 14. 清理和生命周期

后台清理任务定期处理：

- `PENDING` 或 `ACTIVE` 且超过任务 TTL 的任务改为 `EXPIRED`。
- 所有终态任务撤销数据面凭证。
- 传输记录按审计保留期清理；建议至少保留 30 天，具体由配置决定。
- 资源被删除索引时取消其非终态传输。
- 设备被撤销或移出家庭时取消其非终态传输。

首版不删除家庭资源原文件。设备端 `.part` 文件由设备自己的 24 小时清理策略负责。

## 15. 配置和限流

优先使用少量服务端配置，不为每项参数增加 Dashboard 设置页。建议配置：

| 配置 | 默认值 |
| --- | ---: |
| `transfer_chunk_size_mb` | 16 |
| `transfer_max_concurrency` | 4 |
| `transfer_token_ttl_seconds` | 600 |
| `transfer_idle_ttl_seconds` | 86400 |
| `transfer_max_active_per_device` | 3 |
| `transfer_read_block_kb` | 512 |

验证合理边界，不能允许零、负数或导致内存和文件句柄失控的值。若不需要运维调整，可先使用领域常量；不要提前增加无价值配置。

限流至少覆盖：

- 单设备创建任务频率。
- 单设备活动任务数。
- 单任务并发 Range 请求数。
- 整个服务进程同时打开的下载文件句柄数。
- 可选的每设备带宽上限。

超过限制返回 `429` 和 `Retry-After`，不要排队无限等待。

## 16. 错误码和国际化

新增稳定错误语义，具体枚举位置遵循 HomeMind 现有错误体系：

```text
ASSET_TRANSFER_NOT_FOUND
ASSET_TRANSFER_FORBIDDEN
ASSET_TRANSFER_EXPIRED
ASSET_TRANSFER_TERMINAL
ASSET_TRANSFER_TOKEN_INVALID
ASSET_TRANSFER_SOURCE_UNAVAILABLE
ASSET_TRANSFER_SOURCE_CHANGED
ASSET_TRANSFER_RANGE_INVALID
ASSET_TRANSFER_LIMIT_EXCEEDED
ASSET_TRANSFER_HASH_MISMATCH
```

所有返回用户或设备管理 UI 的消息进入后端中英文 i18n。若错误码会显示在 Dashboard，同步 `dashboard/src/locales/{en,zh}.json` 的 `apiErrors`，并运行 i18n 键一致性测试。

## 17. 指标、日志和审计

### 17.1 指标

至少增加：

- `asset_transfer_created_total`
- `asset_transfer_completed_total`
- `asset_transfer_failed_total`
- `asset_transfer_bytes_served_total`
- `asset_transfer_range_requests_total`
- `asset_transfer_active`
- `asset_transfer_token_refresh_total`
- `asset_transfer_hash_mismatch_total`
- Range 响应延迟直方图，若现有指标设施支持。

指标标签只能使用低基数状态或来源类型，不能使用 `transfer_id`、`device_id`、文件名或 URL。

### 17.2 日志

允许记录公共 ID、状态、字节数、区间长度和耗时。禁止记录：

- 明文设备令牌或数据面凭证。
- 带凭证的完整 URL。
- 家庭资源原始本地绝对路径。
- 文件内容或用户隐私元数据。

### 17.3 审计

创建、完成、失败、取消、设备撤销导致的终止以及管理员手工取消需要审计事件。单个 Range 请求不逐条进入审计日志，避免产生海量记录。

## 18. 安全检查清单

- 数据端点不能接受任意 `path` 查询参数。
- 使用常量时间方式比较凭证哈希。
- 设备长期令牌不能进入数据面 URL。
- 凭证刷新后旧凭证立即失效。
- 删除或撤销设备后，已签发凭证立即失效。
- 防止符号链接或文件替换造成 TOCTOU 越界；解析路径后验证受控来源，并在打开后核对 stat。
- `Content-Disposition` 文件名使用现有安全构造工具，不能直接拼 header。
- 返回 `X-Content-Type-Options: nosniff`。
- 不允许 Range 计算整数溢出或超大长度分配。
- 限制同一任务的并发请求，避免文件句柄耗尽。
- 错误响应不泄漏资源是否属于其他家庭。

## 19. 测试要求

### 19.1 Range 解析单元测试

覆盖：

- 完整范围、开放结尾、后缀范围。
- 第一个字节、最后一个字节和完整文件。
- 空文件。
- 起点等于文件大小。
- 起点大于终点。
- 非数字、负号错误、空 Range。
- 多区间拒绝。
- 极大整数不溢出。

### 19.2 领域测试

覆盖：

- 同一 `request_key` 幂等返回同一任务。
- 两个设备使用相同 `request_key` 不冲突。
- 跨家庭资源被拒绝且不泄漏存在性。
- 撤销设备不能创建、刷新或读取任务。
- 进度单调增加。
- 所有终态操作幂等。
- 资源大小、mtime 或散列变化后拒绝续传。
- 过期清理撤销凭证。

### 19.3 API 契约测试

覆盖：

- HEAD 的 `Content-Length`、`ETag`、`Accept-Ranges`。
- GET 完整文件返回 `200`。
- Range 返回 `206` 和精确字节。
- 后缀 Range 和开放结尾 Range。
- 无效 Range 返回 `416` 和正确 `Content-Range`。
- `If-Match` 不一致返回 `412`。
- 过期、撤销和伪造凭证被拒绝。
- 普通用户 JWT 不能替代传输凭证访问数据端点。
- 文件大于测试进程内存预算时仍按流式路径运行；测试可使用稀疏或生成文件，Windows 不依赖稀疏语义断言。

### 19.4 恢复和并发测试

1. 下载 35% 后停止，再次请求只拉取缺失区间。
2. 四个并发 Range 写入不同 offset，最终 SHA-256 一致。
3. 服务重启后任务和进度仍可查询。
4. 凭证过期后刷新继续下载。
5. 下载过程中资源发生变化，后续 Range 被拒绝。
6. 多个设备同时下载不会互相读到对方任务。
7. 客户端断开后服务端文件句柄被释放。

### 19.5 跨平台要求

- 所有文件测试使用 `tmp_path` 和 `pathlib.Path`。
- 不硬编码 `/tmp`、`/root` 或盘符。
- 不依赖 POSIX chmod、symlink 或稀疏文件语义；需要时按仓库规则标记平台跳过。
- 测试设备环境设置独立 `OCTOP_HOME`，不写真实用户目录。

## 20. 实施顺序

### 步骤 0：冻结契约

1. 阅读设备运行时、家庭资源、SharedServices、错误和迁移代码。
2. 确认 `024` 是否已发布，决定折叠或新建迁移。
3. 写 Range、API、状态机和错误码契约测试骨架。

验证：测试能够因缺少实现而稳定失败，且失败点符合契约。

### 步骤 1：迁移和 Repo

1. 添加双数据库迁移。
2. 实现传输与凭证 repo。
3. 接入 HomeMind 服务容器。
4. 添加行映射、幂等、单调进度和条件状态更新测试。

验证：

```bash
uv run pytest tests/unit/homemind/test_migration_pairing.py -q
uv run pytest tests/unit/homemind/test_asset_transfer_repo.py -q
```

### 步骤 2：领域服务

1. 实现设备认证复用入口。
2. 实现资源授权、版本、散列和 manifest。
3. 实现短期凭证生命周期。
4. 实现任务状态和过期清理。

验证：

```bash
uv run pytest tests/unit/homemind/test_asset_transfers.py -q
```

### 步骤 3：Range 数据面

1. 实现纯 Range 解析器。
2. 实现 HEAD、完整 GET 和单 Range GET。
3. 实现流式读取、断开清理和响应安全头。
4. 补授权、版本变化和边界测试。

验证：

```bash
uv run pytest tests/unit/homemind/test_asset_transfer_api.py -q
```

### 步骤 4：控制面 API

1. 实现创建、查询、刷新、进度、完成和失败端点。
2. 添加 Pydantic 模型、字段说明、OpenAPI summary 和 description。
3. 注册 router 并检查 `/api/docs`。
4. 添加完整契约测试。

验证：

```bash
uv run pytest tests/integration/test_asset_transfer_contract.py -q
```

### 步骤 5：运行时命令集成

如果业务要求 HomeMind 主动让设备拉取资源，在现有设备命令 payload 中增加资源引用或 `transfer_id`。设备通过命令得知任务后仍调用控制面获取短期凭证。不要把明文下载凭证持久化到命令 payload。

验证：现有设备命令租约、ack、重试和结果回执测试保持通过，并补资源下载命令测试。

### 步骤 6：指标、清理和文档

1. 增加低基数指标。
2. 接入定期过期清理。
3. 更新 `docs/api.md` 和配置文档。
4. 补中英文错误文本及 i18n 测试。

验证：

```bash
uv run pytest tests/unit/i18n -q
uv run pytest tests/unit/homemind -q
```

### 步骤 7：端到端验收

使用受控测试文件执行：

1. 单连接完整下载。
2. 四路并行下载。
3. 中断后续传。
4. 服务重启后续传。
5. 凭证刷新。
6. 资源变更拒绝。
7. 设备撤销即时失效。

最终验证：

```bash
make all
```

## 21. 完成定义

以下条件全部满足才可标记实现完成：

- 5 GiB 级文件通过流式 Range 路径下载，服务端内存不随文件大小线性增长。
- 设备能够并发下载并在进程重启后只补缺失分片。
- 完整文件 SHA-256 验证成功后才报告完成。
- 设备撤销、资源删除、任务过期和凭证刷新行为已测试。
- 跨家庭、跨设备和伪造凭证访问均被拒绝。
- SQLite 与 PostgreSQL 迁移成对，Linux 与 Windows 测试约束满足。
- API 文档可读，错误码具备中英文文本。
- 定向测试和 `make all` 全绿。
- 未顺手重构无关家庭资源、上传或设备命令代码。

## 22. 后续增强顺序

首版稳定后按收益排序：

1. 对象存储短期签名 URL，避免 HomeMind 消耗公网出口。
2. 局域网家庭存储节点直连，并保留 HomeMind 兜底来源。
3. 分片散列或 Merkle tree，实现分片级即时完整性校验。
4. 自适应并发，根据吞吐、错误率和网络类型动态调整。
5. CDN 或边缘缓存，服务大量异地设备。

P2P 和 NAT 打洞只有在对象存储与局域网直连仍无法满足成本目标时再评估，且必须单独完成威胁模型、隐私评审和跨网络兼容性验证。

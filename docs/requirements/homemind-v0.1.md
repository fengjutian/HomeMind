# HomeMind

## 基于 TencentCloud/Octop 的家庭私有 AI 中枢

**Version:** v0.1
**Project:** HomeMind
**Upstream:** TencentCloud/Octop
**定位:** Family Private AI Hub
**技术路线:** Octop + Python/FastAPI + React/TypeScript + SQLite/PostgreSQL + Local Runtime

---

# 1. 项目概述

## 1.1 产品名称

**HomeMind**

中文定位：

> 家庭私有 AI 中枢

完整产品描述：

> **HomeMind 是基于 TencentCloud/Octop 构建的家庭私有 AI 中枢，通过连接家庭成员、家庭关系、照片、文件、事件、长期记忆、任务和设备，让 AI 能够理解家庭上下文，并在用户授权下执行家庭事务。**

HomeMind 不重新开发一套 AI Agent 平台。

HomeMind 使用 Octop 作为底层 AI Agent 基础设施，在其之上增加家庭领域能力。

---

# 2. 产品愿景

传统家庭数字化系统是分散的：

```text
照片 → 手机相册
文件 → NAS
日历 → Calendar
聊天 → 微信
任务 → Todo
设备 → 智能家居 App
知识 → 浏览器 / 文档
记忆 → 人的大脑
```

用户必须自己理解这些系统。

HomeMind 的目标是反过来：

```text
                    HomeMind
                       │
        ┌──────────────┼──────────────┐
        │              │              │
      家庭成员       家庭资产       家庭记忆
        │              │              │
      关系           照片/文件       事件/事实
        │              │              │
        └──────────────┼──────────────┘
                       │
                  Family Context
                       │
                    AI Agent
                       │
              ┌────────┴────────┐
              │                 │
             Think             Act
              │                 │
             Plan            Execute
              │                 │
              └────────┬────────┘
                       │
                    Verify
                       │
                     Memory
```

最终让用户可以：

> **用自然语言管理整个家庭的数字世界。**

---

# 3. 产品定位

## 3.1 HomeMind 不是什么

HomeMind 不是：

* ChatGPT 套壳
* 普通 AI 聊天软件
* NAS
* 家庭网盘
* 相册 App
* 家庭知识库
* 智能家居控制器
* Todo App

这些都是能力组成部分，而不是产品本身。

---

# 4. 核心定位

HomeMind 的核心是：

> **Family Context + AI Agent + Private Data + Permission + Execution**

即：

```text
家庭上下文
+
家庭数据
+
长期记忆
+
AI Agent
+
权限
+
执行
```

---

# 5. 核心用户价值

用户不需要知道：

* 文件在哪里
* 哪个相册
* 哪个目录
* 哪个日历
* 哪个家庭成员
* 哪个事件
* 哪个知识库
* 哪个设备

只需要表达：

> “帮我找去年日本旅行的照片。”

或者：

> “去年春节我们去了哪里？”

或者：

> “把日本旅行照片整理一下，重复的不要删除。”

HomeMind 负责理解、搜索、规划和执行。

---

# 6. 核心产品闭环

HomeMind 的核心闭环：

```text
User
 ↓
Intent
 ↓
Family Context
 ↓
Memory / Assets / Events
 ↓
Agent
 ↓
Plan
 ↓
Permission
 ↓
Confirmation
 ↓
Execute
 ↓
Verify
 ↓
Memory
```

其中任何涉及数据修改、删除、发送、外部操作的动作，都必须进入权限控制。

---

# 7. 产品核心概念

HomeMind 建立以下领域对象：

```text
Family
Member
Relationship
Space
Asset
Event
Memory
Task
Device
Permission
Context
```

关系：

```text
Family
│
├── Members
│   └── Relationships
│
├── Spaces
│
├── Assets
│   ├── Photos
│   ├── Videos
│   ├── Documents
│   └── Audio
│
├── Events
│
├── Memories
│
├── Tasks
│
└── Devices
```

---

# 8. 用户体系

HomeMind 不替换 Octop User。

继续使用 Octop 的用户系统。

关系：

```text
Octop User
     │
     │ 0..N
     ▼
FamilyMembership
     │
     ▼
Family
     │
     ▼
FamilyMember
```

一个用户可以：

```text
个人空间
家庭空间
其他家庭
未来企业空间
```

因此建议保留 Workspace 概念：

```text
PERSONAL
FAMILY
TEAM
ENTERPRISE
```

---

# 9. Family

Family 是 HomeMind 的顶层业务实体。

```text
Family
```

字段：

```text
id
name
avatar
owner_user_id
timezone
locale
created_at
updated_at
```

示例：

```json
{
  "id": "family_001",
  "name": "封家",
  "owner_user_id": "user_001",
  "timezone": "Asia/Shanghai",
  "locale": "zh-CN"
}
```

---

# 10. Family Member

家庭成员不一定拥有 HomeMind 登录账号。

```text
FamilyMember
```

字段：

```text
id
family_id
user_id
display_name
role
avatar
birthday
status
created_at
updated_at
```

例如：

```text
爸爸 → user_001
妈妈 → user_002
儿子 → NULL
爷爷 → NULL
```

这样 AI 才能理解完整家庭。

---

# 11. Family Relationship

关系：

```text
爸爸 → 妈妈 = SPOUSE
爸爸 → 儿子 = PARENT
儿子 → 爸爸 = CHILD
爷爷 → 爸爸 = PARENT
```

数据模型：

```text
FamilyRelationship
```

字段：

```text
id
family_id
from_member_id
to_member_id
relationship_type
```

关系类型：

```text
SPOUSE
PARENT
CHILD
SIBLING
GRANDPARENT
GRANDCHILD
OTHER
```

---

# 12. Family Space

空间是权限和资产管理的基础。

默认：

```text
Shared
Dad
Mom
Child
Archive
```

例如：

```text
Family
│
├── Shared
│   ├── Photos
│   ├── Documents
│   └── Travel
│
├── Dad
│   └── Private
│
├── Mom
│   └── Private
│
└── Archive
```

Space 类型：

```text
SHARED
PRIVATE
ARCHIVE
```

---

# 13. Family Asset

所有家庭数字资产统一进入 Asset 模型。

支持：

```text
PHOTO
VIDEO
DOCUMENT
AUDIO
NOTE
LINK
EMAIL
OTHER
```

模型：

```text
FamilyAsset
```

字段：

```text
id
family_id
space_id
asset_type
name
uri
mime_type
size_bytes
hash
created_at
indexed_at
metadata_json
created_by
visibility
status
```

---

# 14. 存储原则

数据库不保存大文件。

数据库只保存：

```text
metadata
URI
hash
index
embedding
permissions
relationships
```

实际文件可以位于：

```text
Local Disk
NAS
Object Storage
PC Runtime
Mobile Runtime
```

例如：

```text
FamilyAsset
    │
    └── uri
         │
         ├── file:///home/user/photos/a.jpg
         ├── smb://nas/family/a.jpg
         └── s3://bucket/family/a.jpg
```

---

# 15. Photo Intelligence

照片是 V0.1 的核心能力。

照片进入系统：

```text
Photo
 ↓
Hash
 ↓
EXIF
 ↓
Timestamp
 ↓
GPS
 ↓
Vision Model
 ↓
People
 ↓
Objects
 ↓
Location
 ↓
Event
 ↓
Embedding
```

照片可以得到：

```text
时间
地点
人物
物体
场景
事件
语义描述
Embedding
```

---

# 16. 照片搜索

用户：

> 找出我们一家人在京都樱花树下的照片。

系统解析：

```text
Family = 当前家庭
People = 家庭成员
Location = Kyoto
Object = Cherry Blossom
Event = Japan Trip
AssetType = PHOTO
```

搜索：

```text
Structured Filter
        +
Keyword Search
        +
Vector Search
        +
Image Similarity
        +
Rerank
```

---

# 17. Family Event

家庭事件是连接时间、人物和资产的核心。

例如：

```text
春节
生日
日本旅行
家庭聚餐
孩子毕业
搬家
装修
```

模型：

```text
FamilyEvent
```

字段：

```text
id
family_id
event_type
title
start_at
end_at
location
description
metadata_json
created_by
created_at
```

---

# 18. Event 与 Asset

一个事件可以包含多个资产：

```text
Japan Trip
│
├── 1283 Photos
├── 37 Videos
├── Travel Plan
├── Hotel Documents
└── Memories
```

因此 Event 是家庭数据的重要聚合节点。

---

# 19. Family Memory

Memory 与 Knowledge 必须严格区分。

Knowledge：

```text
日本签证要求
京都旅游攻略
保险条款
```

Memory：

```text
我们去年去了日本
妈妈喜欢京都
爸爸喜欢摄影
孩子不喜欢早起
```

Knowledge 是外部或文档知识。

Memory 是家庭长期上下文。

---

# 20. Memory 类型

```text
FACT
PREFERENCE
EVENT
RELATIONSHIP
HABIT
DECISION
EXPERIENCE
```

数据：

```text
FamilyMemory
```

字段：

```text
id
family_id
subject_type
subject_id
content
memory_type
importance
confidence
visibility
source_type
source_id
created_at
updated_at
expires_at
```

---

# 21. Memory 生命周期

AI 不应该看到一句话就永久记忆。

采用：

```text
Candidate
 ↓
Extract
 ↓
Validate
 ↓
Rank
 ↓
Store
 ↓
Update
 ↓
Decay
 ↓
Archive
```

例如：

```text
用户：
我特别喜欢京都。

Memory Candidate：
USER_PREFERENCE

confidence = 0.85
importance = 0.72
```

重复出现以后再提高置信度。

---

# 22. Family Context

Family Context 是 HomeMind 的核心技术。

它负责把：

```text
Current User
+
Family
+
Current Member
+
Relationships
+
Current Space
+
Permissions
+
Relevant Memories
+
Relevant Events
+
Relevant Assets
```

组合为 Agent 当前上下文。

例如：

```json
{
  "family_id": "family_001",
  "member_id": "member_dad",
  "space": "shared",
  "related_members": [
    "member_mom",
    "member_child"
  ],
  "active_events": [
    "japan_trip_2025"
  ],
  "permissions": [
    "photo.read",
    "file.read",
    "task.create"
  ]
}
```

---

# 23. Context Resolver

Context Resolver 负责解决自然语言中的家庭语义。

例如：

> “我妈去年生日拍的照片。”

解析：

```text
我妈
 ↓
Relationship Resolver
 ↓
Mother

去年
 ↓
Time Resolver

生日
 ↓
Event Resolver

照片
 ↓
Asset Resolver
```

最终：

```text
Family
→ Mother
→ Birthday Event
→ Last Year
→ Photos
```

---

# 24. Family Agent

HomeMind 不重新开发 Agent Framework。

直接使用 Octop Agent。

增加：

```text
Family Context Provider
Family Memory Provider
Family Asset Tools
Family Event Tools
Family Task Tools
Family Permission Tools
```

Agent：

```text
User
 ↓
Octop Agent
 ↓
Family Context
 ↓
Family Tools
 ↓
Octop Runtime
```

---

# 25. Family Tools

第一版：

```text
family.list_members
family.get_member

family.search_memory
family.create_memory

family.search_assets
family.get_asset

family.list_events
family.create_event

family.list_tasks
family.create_task

family.get_devices
family.get_device
```

---

# 26. Filesystem Tools

```text
filesystem.list
filesystem.search
filesystem.read
filesystem.copy
filesystem.move
filesystem.rename
filesystem.delete
```

风险分级：

```text
READ
LOW RISK

CREATE
MEDIUM

MOVE
HIGH

RENAME
HIGH

DELETE
CRITICAL
```

---

# 27. 权限模型

采用：

```text
Octop Permission
        ↓
Family Permission
        ↓
Runtime Permission
```

结果：

```text
ALLOW
DENY
REQUIRE_CONFIRMATION
```

例如：

```text
读取共享照片
→ ALLOW

读取妈妈私人文件
→ DENY

删除照片
→ REQUIRE_CONFIRMATION

创建家庭相册
→ ALLOW

发送邮件
→ REQUIRE_CONFIRMATION
```

---

# 28. Agent Transaction

V0.1 不立即修改 Octop Core。

先实现：

```text
Family Transaction
```

流程：

```text
Plan
 ↓
Permission Check
 ↓
Approval
 ↓
Execute
 ↓
Verify
 ↓
Commit Memory
```

例如：

```text
用户：
整理日本旅行照片。

Agent：
发现 1283 张照片。

计划：
1. 按时间分类
2. 按地点分类
3. 识别重复
4. 创建相册
5. 不删除原文件

是否执行？
```

用户：

```text
确认
```

才执行。

---

# 29. Family Task

家庭任务：

```text
买牛奶
预约酒店
准备旅行
整理照片
孩子报名
缴费
```

Task 状态：

```text
TODO
IN_PROGRESS
WAITING_APPROVAL
DONE
FAILED
CANCELLED
```

---

# 30. Family Device

V0.1 只建立统一设备模型。

设备：

```text
PC
NAS
Phone
Tablet
Server
```

模型：

```text
FamilyDevice
```

字段：

```text
id
family_id
name
device_type
platform
status
address
capabilities
last_seen
```

---

# 31. Runtime

HomeMind 长期采用：

```text
HomeMind Server
       │
       ├── PC Runtime
       ├── NAS Runtime
       ├── Android Runtime
       └── Future Runtime
```

Server 负责：

```text
Agent
Memory
Family
Context
Permission
Task
```

Runtime 负责：

```text
Filesystem
Camera
Microphone
Browser
Notification
Local Model
Device APIs
```

---

# 32. MCP

HomeMind 原生支持 MCP。

家庭能力可以暴露：

```text
family.search_assets
family.search_memory
family.list_events
family.create_task
family.list_members
```

未来：

```text
MCP
│
├── Calendar
├── Email
├── NAS
├── Browser
├── Cloud Drive
├── Smart Home
└── External Services
```

---

# 33. Skill

HomeMind 可以提供家庭 Skills：

```text
family-photo-organizer
family-trip-planner
family-document-manager
family-memory
family-calendar
family-shopping
```

例如：

```text
family-photo-organizer
```

流程：

```text
扫描
 ↓
识别
 ↓
分类
 ↓
去重
 ↓
事件聚类
 ↓
建立相册
```

---

# 34. 前端

保持 Octop 原有 React + TypeScript Dashboard。

新增：

```text
dashboard/src/pages/family/
dashboard/src/components/family/
dashboard/src/api/modules/family/
```

构建后的：

```text
src/octop/dashboard/
```

仍然是构建产物，不直接修改。

---

# 35. 前端导航

```text
Home
Chat

Family
Photos
Files
Timeline
Tasks

Memory
Knowledge

Agents
MCP
Skills
Workflow

Settings
```

---

# 36. Home 页面

首页重点显示家庭状态：

```text
┌──────────────────────────────┐
│ HomeMind                     │
│                              │
│ 你好，爸爸                   │
│                              │
│ 今日                         │
│                              │
│ 3 个任务     2 个事件        │
│                              │
│ 家庭动态                     │
│                              │
│ 日本旅行                     │
│ 1283 张照片                  │
│                              │
│ 最近记忆                     │
│                              │
│ AI Assistant                 │
│                              │
│ “今天想让我帮你做什么？”     │
└──────────────────────────────┘
```

---

# 37. Family 页面

包含：

```text
成员
关系
空间
权限
设备
设置
```

关系可以图形化：

```text
          爷爷
            │
          爸爸 ─── 妈妈
            │
        ┌───┴───┐
       儿子     女儿
```

---

# 38. Photos 页面

功能：

```text
全部照片
人物
地点
时间
事件
相册
重复照片
AI Search
```

搜索框支持自然语言：

```text
找出去年日本旅行的照片
```

```text
找妈妈在京都拍的樱花
```

```text
找出全家人的合照
```

---

# 39. Files 页面

支持：

```text
PDF
Word
Excel
PPT
TXT
Markdown
图片
Audio
Video
```

后续进入 Knowledge Pipeline：

```text
File
 ↓
Parser
 ↓
Chunk
 ↓
Embedding
 ↓
Knowledge
```

---

# 40. Timeline

时间线：

```text
2025
│
├── 春节
├── 日本旅行
├── 孩子生日
├── 家庭聚餐
└── 装修

2026
│
├── 春节
├── 家庭旅行
└── ...
```

Event 下关联：

```text
Photos
Videos
Documents
Tasks
Memories
Members
Locations
```

---

# 41. Memory 页面

用户必须可以查看 AI 记住了什么。

例如：

```text
家庭事实
├── 家庭成员 4 人
├── 爸爸喜欢摄影
└── 妈妈喜欢旅行

家庭经历
├── 2025 日本旅行
└── 2025 春节聚会

家庭偏好
├── 喜欢周末旅行
└── 不喜欢早起
```

用户可以：

```text
Edit
Delete
Disable
Change Visibility
```

---

# 42. Backend 目录

建议结构：

```text
src/octop/
│
├── infra/
│   │
│   ├── agents/
│   ├── memory/
│   ├── users/
│   ├── db/
│   │
│   └── family/
│       │
│       ├── __init__.py
│       │
│       ├── models/
│       │   ├── family.py
│       │   ├── member.py
│       │   ├── relationship.py
│       │   ├── space.py
│       │   ├── asset.py
│       │   ├── event.py
│       │   ├── memory.py
│       │   ├── task.py
│       │   └── device.py
│       │
│       ├── repos/
│       │   ├── family_repo.py
│       │   ├── member_repo.py
│       │   ├── asset_repo.py
│       │   ├── event_repo.py
│       │   ├── memory_repo.py
│       │   └── task_repo.py
│       │
│       ├── services/
│       │   ├── family_service.py
│       │   ├── member_service.py
│       │   ├── asset_service.py
│       │   ├── event_service.py
│       │   ├── memory_service.py
│       │   ├── context_service.py
│       │   └── permission_service.py
│       │
│       ├── tools/
│       │   ├── family_tools.py
│       │   ├── asset_tools.py
│       │   ├── photo_tools.py
│       │   ├── memory_tools.py
│       │   └── task_tools.py
│       │
│       └── context/
│           ├── family_context.py
│           ├── context_builder.py
│           └── context_resolver.py
```

---

# 43. API Router

```text
src/octop/api/routers/family/
│
├── __init__.py
├── families.py
├── members.py
├── relationships.py
├── spaces.py
├── assets.py
├── events.py
├── memories.py
├── tasks.py
└── devices.py
```

原则：

```text
Router
 ↓
Service
 ↓
Repository
 ↓
Database
```

Router 不直接操作数据库。

---

# 44. API

## Family

```http
POST   /api/families
GET    /api/families
GET    /api/families/{family_id}
PUT    /api/families/{family_id}
DELETE /api/families/{family_id}
```

## Members

```http
GET    /api/families/{id}/members
POST   /api/families/{id}/members
PUT    /api/families/{id}/members/{member_id}
DELETE /api/families/{id}/members/{member_id}
```

## Relationships

```http
GET    /api/families/{id}/relationships
POST   /api/families/{id}/relationships
DELETE /api/families/{id}/relationships/{relationship_id}
```

## Assets

```http
GET  /api/families/{id}/assets
POST /api/families/{id}/assets/index
GET  /api/families/{id}/assets/{asset_id}
```

## Events

```http
GET  /api/families/{id}/events
POST /api/families/{id}/events
GET  /api/families/{id}/events/{event_id}
```

## Memories

```http
GET    /api/families/{id}/memories
POST   /api/families/{id}/memories
PUT    /api/families/{id}/memories/{memory_id}
DELETE /api/families/{id}/memories/{memory_id}
```

## Tasks

```http
GET  /api/families/{id}/tasks
POST /api/families/{id}/tasks
PUT  /api/families/{id}/tasks/{task_id}
```

---

# 45. Database

核心表：

```text
families
family_members
family_memberships
family_relationships
family_spaces
family_assets
family_events
family_memories
family_tasks
family_devices
family_permissions
```

---

# 46. Family SQL

```sql
CREATE TABLE families (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    avatar TEXT,
    timezone TEXT DEFAULT 'Asia/Shanghai',
    locale TEXT DEFAULT 'zh-CN',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

---

# 47. Member SQL

```sql
CREATE TABLE family_members (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    user_id TEXT NULL,
    display_name TEXT NOT NULL,
    role TEXT,
    avatar TEXT,
    birthday DATE NULL,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);
```

---

# 48. Relationship SQL

```sql
CREATE TABLE family_relationships (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    from_member_id TEXT NOT NULL,
    to_member_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL
);
```

---

# 49. Asset SQL

```sql
CREATE TABLE family_assets (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    space_id TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    name TEXT,
    uri TEXT NOT NULL,
    mime_type TEXT,
    size_bytes INTEGER,
    hash TEXT,
    created_at TIMESTAMP,
    indexed_at TIMESTAMP,
    metadata_json TEXT,
    created_by TEXT,
    visibility TEXT DEFAULT 'FAMILY',
    status TEXT DEFAULT 'ACTIVE'
);
```

---

# 50. Event SQL

```sql
CREATE TABLE family_events (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    start_at TIMESTAMP,
    end_at TIMESTAMP,
    location TEXT,
    description TEXT,
    metadata_json TEXT,
    created_by TEXT,
    created_at TIMESTAMP NOT NULL
);
```

---

# 51. Search Architecture

第一版不引入复杂基础设施。

优先：

```text
Existing DB
+
Existing Octop Memory
+
Metadata
+
Keyword
+
Vector
```

搜索流程：

```text
Query
 ↓
Intent Parser
 ↓
Family Context
 ↓
Structured Filter
 ↓
Keyword Search
 ↓
Vector Search
 ↓
Rerank
 ↓
Result
```

---

# 52. AI Provider

模型必须 Provider 化。

```text
LLM Provider
├── DeepSeek
├── MiniMax
├── OpenAI-compatible
└── Local Model
```

Vision：

```text
Vision Provider
```

Embedding：

```text
Embedding Provider
```

ASR：

```text
Speech Recognition Provider
```

TTS：

```text
Speech Synthesis Provider
```

HomeMind 不绑定单一模型。

---

# 53. Local-first

家庭数据默认：

```text
Local
```

优先处理：

```text
家庭文件
家庭照片
家庭记忆
家庭事件
家庭关系
```

云模型只处理必要的信息。

未来支持：

```text
Local LLM
Cloud LLM
Hybrid
```

---

# 54. 数据隐私

核心原则：

> **家庭数据属于家庭，不属于 AI。**

AI 只是被授权的数据使用者。

设计：

```text
Data Owner
 ↓
Family
 ↓
Permission
 ↓
Agent
```

不能：

```text
Agent
 ↓
无限访问家庭数据
```

---

# 55. 敏感数据分级

建议：

```text
PUBLIC
FAMILY
PRIVATE
SENSITIVE
```

例如：

```text
家庭旅行照片
→ FAMILY

个人照片
→ PRIVATE

财务文件
→ SENSITIVE
```

---

# 56. AI 数据边界

默认：

```text
家庭私有数据
→ 不自动上传云端
```

如果必须使用云端模型：

```text
Local
 ↓
Data Minimization
 ↓
Permission
 ↓
Cloud LLM
```

未来增加：

```text
Cloud Processing Policy
```

让用户决定：

```text
Never
Ask Every Time
Allowed
```

---

# 57. 安全设计

重点保护：

```text
Filesystem
Credentials
Private Photos
Family Documents
External APIs
```

所有高风险工具必须经过：

```text
Permission
+
Approval
```

尤其：

```text
delete
shell
send
purchase
payment
external API mutation
```

---

# 58. Logging

记录 Agent Action：

```text
who
when
what
why
tool
target
result
approval
```

例如：

```json
{
  "user": "user_001",
  "family": "family_001",
  "tool": "filesystem.rename",
  "target": "/photos/a.jpg",
  "approval": true,
  "result": "success"
}
```

---

# 59. Audit Log

家庭管理员可以查看：

```text
AI 做过什么？
```

例如：

```text
10:31
AI 搜索了 1283 张照片

10:34
AI 创建了日本旅行相册

10:35
用户批准移动 32 个文件

10:36
操作完成
```

---

# 60. Upstream Fork

HomeMind 必须 Fork：

```text
TencentCloud/Octop
        ↓
HomeMind
```

远程：

```text
origin
→ HomeMind

upstream
→ TencentCloud/Octop
```

本地：

```bash
git remote add upstream https://github.com/TencentCloud/Octop.git
```

同步：

```bash
git fetch upstream
git merge upstream/main
```

---

# 61. Git 分支

```text
main
│
├── feature/family
├── feature/family-assets
├── feature/family-memory
├── feature/family-context
├── feature/family-agent
└── feature/family-runtime
```

HomeMind 修改尽量集中：

```text
infra/family
api/routers/family
dashboard/pages/family
tests/family
```

---

# 62. 不应该修改的区域

尽量不要直接修改：

```text
Octop Agent Core
Octop User Core
Octop MCP Core
Octop Skill Core
Octop Workflow Core
Octop CLI
```

除非确实需要扩展点。

优先采用：

```text
Adapter
Provider
Plugin
Service
Tool
```

实现。

---

# 63. Runtime 策略

当前 Octop 仓库已经包含 desktop 等运行能力，因此 V0.1 不应该重新制造一套完整 Desktop Runtime。

第一阶段：

```text
Octop Desktop
+
HomeMind Family
```

后续再扩展：

```text
HomeMind Runtime
```

形成：

```text
Server
 ↓
Runtime
 ↓
Device
```

---

# 64. Android Runtime

长期支持：

```text
HomeMind Android
```

能力：

```text
Photo
Camera
Microphone
Files
Local AI
Notifications
```

例如：

> 帮我把今天手机拍的家庭照片同步到家庭照片库。

Android Runtime：

```text
Camera Roll
 ↓
Hash
 ↓
Metadata
 ↓
Upload / Local Sync
 ↓
FamilyAsset
```

---

# 65. NAS Runtime

NAS 是家庭私有 AI 的重要节点。

```text
NAS
│
├── Family Photos
├── Family Videos
├── Documents
└── Backup
```

HomeMind：

```text
NAS Scanner
 ↓
Index
 ↓
Metadata
 ↓
Embedding
 ↓
Family Search
```

---

# 66. V0.1 核心场景

第一版只完成三个完整场景。

## 场景一：家庭问答

用户：

> 去年我们去了哪里？

系统：

```text
时间解析
 ↓
Family Event
 ↓
Location
 ↓
Memory
```

返回：

```text
2025 日本旅行

东京
京都
大阪
```

---

# 67. 场景二：家庭照片搜索

用户：

> 找出我们一家人在京都樱花树下的照片。

系统：

```text
Family
 ↓
People
 ↓
Event
 ↓
Location
 ↓
Object
 ↓
Semantic Search
```

返回照片。

---

# 68. 场景三：AI 整理照片

用户：

> 把日本旅行照片整理一下，重复照片标出来，不要删除。

Agent：

```text
找到：
1283 张

发现：
47 组疑似重复

计划：
1. 创建日本旅行相册
2. 分类
3. 标记重复
4. 不删除原始文件
```

用户：

```text
确认
```

执行。

---

# 69. V0.1 明确不做

暂时不做：

```text
家庭机器人
24 小时摄像头
智能门锁
自动金融交易
医疗决策
大规模 IoT
自研 LLM
自研 Agent Framework
Neo4j
Kafka
独立 Vector DB
复杂云同步
```

原因：

> V0.1 的目标不是做完整家庭操作系统，而是证明“AI 理解家庭并能执行家庭任务”这一核心闭环。

---

# 70. 开发阶段

## Phase 0：Fork

```text
Fork Octop
 ↓
Clone
 ↓
origin/upstream
 ↓
固定基线
 ↓
Baseline Test
```

---

## Phase 1：Family Foundation

实现：

```text
Family
Member
Membership
Relationship
Space
Permission
```

---

## Phase 2：Asset

实现：

```text
Asset
File
Photo
Hash
Metadata
Indexer
```

---

## Phase 3：Memory

实现：

```text
FamilyMemory
FamilyEvent
FamilyContext
ContextResolver
```

---

## Phase 4：Agent

实现：

```text
Family Tools
Memory Tools
Asset Tools
Task Tools
Permission Tools
```

---

## Phase 5：Search

实现：

```text
Family Search
Photo Search
Memory Search
Event Search
```

---

## Phase 6：Transaction

实现：

```text
Plan
Permission
Approval
Execute
Verify
```

---

## Phase 7：UI

实现：

```text
Home
Family
Photos
Files
Timeline
Tasks
Memory
```

---

# 71. 测试

新增：

```text
tests/family/
│
├── test_family.py
├── test_members.py
├── test_relationships.py
├── test_spaces.py
├── test_assets.py
├── test_events.py
├── test_memory.py
├── test_context.py
├── test_permissions.py
├── test_agent_tools.py
└── test_transactions.py
```

---

# 72. 必须测试的安全场景

例如：

```text
爸爸访问共享照片
→ PASS

爸爸访问妈妈私人文件
→ DENY

AI 删除照片
→ REQUIRE_CONFIRMATION

用户拒绝
→ MUST NOT DELETE

AI 移动文件
→ Audit Log

权限过期
→ DENY
```

---

# 73. Upstream Regression

每次同步 Octop：

```bash
git fetch upstream
git merge upstream/main
```

然后：

```bash
make all
uv run pytest
cd dashboard
npx tsc -b
```

同时执行：

```text
Octop Tests
+
HomeMind Tests
```

确保 HomeMind 不破坏上游功能。

---

# 74. 第一版验收标准

V0.1 必须做到：

### Family

```text
创建家庭
添加成员
建立关系
创建空间
```

### Asset

```text
扫描照片
读取 metadata
计算 hash
建立索引
```

### Memory

```text
建立家庭记忆
查询家庭记忆
修改记忆
删除记忆
```

### Context

```text
识别家庭成员
识别关系
识别事件
识别权限
```

### Agent

```text
搜索家庭照片
搜索家庭事件
搜索家庭记忆
创建任务
```

### Permission

```text
允许读取
拒绝访问
高风险操作确认
```

### Transaction

```text
Plan
Approval
Execute
Verify
```

---

# 75. 最终产品形态

最终 HomeMind：

```text
                         HomeMind
                             │
             ┌───────────────┼───────────────┐
             │               │               │
          Family          Memory           Assets
             │               │               │
        Members          Events          Photos/Files
        Relations         Facts              │
        Spaces            Preferences        │
             │               │               │
             └───────────────┼───────────────┘
                             │
                       Family Context
                             │
                         Octop Agent
                             │
                 ┌───────────┴───────────┐
                 │                       │
               Think                    Act
                 │                       │
               Plan                  Execute
                 │                       │
                 └───────────┬───────────┘
                             │
                         Permission
                             │
                          Approval
                             │
                          Runtime
                             │
              ┌──────────────┼──────────────┐
              │              │              │
             PC             NAS           Android
```

---

# 76. HomeMind 的长期方向

V0.1：

```text
家庭 AI
```

V0.5：

```text
家庭数据中枢
```

V1.0：

```text
家庭 Agent Platform
```

长期：

```text
                    HomeMind
                       │
          ┌────────────┼────────────┐
          │            │            │
        Memory       Assets       Devices
          │            │            │
          └────────────┼────────────┘
                       │
                  Family Context
                       │
                     Agent
                       │
          ┌────────────┼────────────┐
          │            │            │
        Search       Planning     Action
          │            │            │
          └────────────┼────────────┘
                       │
                    Runtime
                       │
          ┌────────────┼────────────┐
          │            │            │
         PC           NAS         Mobile
```

最终目标不是：

> “做一个家庭版 ChatGPT。”

而是：

> **建立一个属于家庭自己的 AI Context Layer。**

AI 不只是回答问题，而是逐渐理解：

```text
谁是我的家人
他们之间是什么关系
我们经历过什么
我们拥有些什么
文件在哪里
照片在哪里
过去发生过什么
现在正在做什么
接下来需要做什么
哪些事情 AI 可以做
哪些事情必须征得同意
```

最终形成：

```text
                    Family Intelligence
                            │
          ┌─────────────────┼─────────────────┐
          │                 │                 │
       Memory             Context           Assets
          │                 │                 │
          └─────────────────┼─────────────────┘
                            │
                           AI
                            │
                    Understand → Plan
                            │
                         Permission
                            │
                         Execute
                            │
                          Verify
                            │
                          Remember
```

这就是 HomeMind 的核心产品闭环。

---

# 77. 项目第一阶段交付物

第一阶段不要追求大量功能，最终交付以下内容：

```text
01  HomeMind GitHub Fork
02  upstream 同步机制
03  Family Domain
04  Family Database Migration
05  Family API
06  Family UI
07  Family Asset Indexer
08  Photo Metadata Pipeline
09  Family Memory
10  Family Context
11  Family Agent Tools
12  Permission Layer
13  Approval Flow
14  Audit Log
15  三个完整 Demo
```

三个 Demo：

```text
Demo 1
“去年我们去了哪里？”

Demo 2
“找出我们一家人在京都樱花树下的照片。”

Demo 3
“把日本旅行照片整理一下，重复的标出来，不要删除。”
```

只要这三个场景完整跑通：

```text
理解
 ↓
搜索
 ↓
记忆
 ↓
规划
 ↓
授权
 ↓
执行
 ↓
验证
```

HomeMind V0.1 的技术闭环就完成了。

---

# 78. 开发原则

最后确定 10 条原则：

```text
1. Fork，不复制代码重新建项目

2. Octop 是上游，HomeMind 是下游产品

3. 尽量不修改 Octop Core

4. Family 是 HomeMind 的核心 Domain

5. FamilyMember 不强制绑定 Octop User

6. Memory 与 Knowledge 分离

7. 数据与 AI 模型解耦

8. AI 与 Runtime 解耦

9. 所有高风险操作必须 Permission + Approval

10. 每次执行都必须 Verify，并记录 Audit
```

**HomeMind 的技术路线最终确定为：**

```text
TencentCloud/Octop
        │
        │ Fork
        ▼
     HomeMind
        │
        ├── Octop Agent
        ├── Octop Memory
        ├── Octop MCP
        ├── Octop Skill
        ├── Octop Workflow
        └── Octop User
                 │
                 ▼
          Family Domain
                 │
        ┌────────┼────────┐
        │        │        │
      Assets   Memory   Events
        │        │        │
        └────────┼────────┘
                 │
          Family Context
                 │
              Agent
                 │
        Plan → Permission
                 │
             Approval
                 │
             Execute
                 │
              Verify
                 │
              Memory
```


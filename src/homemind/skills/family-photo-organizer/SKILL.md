---
name: family-photo-organizer
description: 用于把家庭照片按人物、地点、事件整理成相册。必须先生成非破坏性的整理计划并请用户确认，再通过 Family Transaction 应用相册关联；绝不移动或删除原始文件。Use to group family photos into albums by person, place, or event — always plan first, confirm, then apply through a Family Transaction; never move or delete originals.
metadata:
  octop:
    emoji: "📷"
    label:
      zh: "家庭相册整理"
      en: "Family Photo Organizer"
    summary:
      zh: "按人物、地点、事件整理家庭照片,先出计划再确认。"
      en: "Organize family photos by person, place, or event with a confirmable plan."
---

# 家庭相册整理

整理照片是**破坏性操作的高风险场景**:原始照片属于家庭共同财产,整理只能新增关联,不能改动文件本身。

## 强制流程

1. **确认 active family。** 没有 active family 时立刻停止,不要默认选第一个家庭。
2. **只读地了解照片。** 调用 `family.search_assets` 取得资产及其人物、地点、时间信息。不调用任何写工具。
3. **生成整理计划。** 输出一份计划:每个相册的名称、成员照片数量、判断依据。计划里**不包含任何文件移动**。
4. **请用户确认。** 用普通文字把计划列给用户,等待明确同意。
5. **通过 Transaction 应用。** 用 `family.plan_transaction` 提交相册关联。审批通过后由事务系统执行。
6. **验证结果。** 重新读取相册,确认关联生效。

## 硬性禁止

- 禁止调用 `filesystem.move`、`filesystem.rename`、`filesystem.delete`。
- 禁止直接写入资产表,或跳过 `family.plan_transaction` 自行落库。
- 禁止在没有 active family 或用户未确认时继续。

## 可用工具

| 用途 | 工具 |
|------|------|
| 读取家庭 | `family.get_devices` 之前的 `family.list_members` / `family.search_assets` |
| 搜索照片 | `family.search_assets` |
| 事件信息 | `family.list_events` |
| 应用计划 | `family.plan_transaction` |

所有工具的权限与 active family 校验由 MCP Server 强制执行,技能本身不得绕过。

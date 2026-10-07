---
name: family-shopping
description: 用于维护家庭购物清单:按家庭成员汇总需求、生成采购计划,并通过 Family Transaction 创建任务。Use to maintain a family shopping list — aggregate needs per member, draft a plan, and create tasks through a Family Transaction.
metadata:
  octop:
    emoji: "🛒"
    label:
      zh: "家庭采购"
      en: "Family Shopping"
    summary:
      zh: "按成员汇总需求,生成采购计划并提交任务。"
      en: "Aggregate needs per member and submit shopping tasks."
---

# 家庭采购

## 强制流程

1. **确认 active family**,没有就停止。
2. **汇总需求。** `family.list_tasks` 找未完成的相关任务;询问用户还需要买什么。
3. **合并同类项。** 按品类合并,标注哪些是"已有存货可能不必买"。
4. **生成计划。** 清单 + 预估数量 + 可选供应商。
5. **提交。** 用 `family.plan_transaction` 创建采购任务。

## 硬性禁止

- 禁止代替用户下单或联系商家。
- 禁止把购物清单当成正式记忆写入家庭记忆库。
- 禁止在没有 active family 时继续。
- 不得假设权限校验已通过;MCP Server 会逐次校验 active family 与权限。

## 可用工具

| 用途 | 工具 |
|------|------|
| 现有任务 | `family.list_tasks` |
| 成员偏好 | `family.list_members` |
| 提交采购任务 | `family.plan_transaction` |

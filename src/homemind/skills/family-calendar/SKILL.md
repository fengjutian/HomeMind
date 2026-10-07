---
name: family-calendar
description: 用于查询与创建家庭日历事件、待办和提醒:读取按家庭时区分组,写入一律通过 Family Transaction。Use to read and create family calendar events, todos, and reminders — reads grouped by family timezone, writes always through a Family Transaction.
metadata:
  octop:
    emoji: "📅"
    label:
      zh: "家庭日历"
      en: "Family Calendar"
    summary:
      zh: "按家庭时区查询与创建事件和待办。"
      en: "Read and create family events and todos in the family timezone."
---

# 家庭日历

家庭成员分布在不同时区,所有时间展示必须使用**家庭时区**而不是浏览器本地时区。

## 强制流程

1. **确认 active family**,没有就停止。
2. **读取。** `family.list_events` 与 `family.list_tasks`。
3. **按家庭时区解释时间。** 用户说"下周三下午"时,按家庭时区换算,不要按服务器 UTC 或浏览器时区。
4. **写入。** `family.plan_transaction` 创建事件或任务。
5. **冲突提示。** 新事件与既有安排重叠时明确指出。

## 硬性禁止

- 禁止用浏览器本地时区解释家庭时间。
- 禁止绕过事务直接写日历表。
- 禁止把事件状态直接改成 COMPLETED。

## 可用工具

| 用途 | 工具 |
|------|------|
| 读取事件 | `family.list_events` |
| 读取任务 | `family.list_tasks` |
| 创建事件 | `family.plan_transaction` |

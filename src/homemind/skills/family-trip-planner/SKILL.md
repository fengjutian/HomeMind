---
name: family-trip-planner
description: 用于规划家庭出行:结合成员、事件和照片历史生成行程草案,并通过 Family Transaction 创建日历事件与任务。Use to plan family trips — combine members, events, and photo history into a draft itinerary, then create calendar events and tasks through a Family Transaction.
metadata:
  octop:
    emoji: "✈️"
    label:
      zh: "家庭出行规划"
      en: "Family Trip Planner"
    summary:
      zh: "结合家庭成员与历史规划出行,并提交日历事件。"
      en: "Plan family trips from members and history, submitting calendar events."
---

# 家庭出行规划

出行牵涉所有人的时间,一次冲突的安排会影响整家人。

## 强制流程

1. **确认 active family**,没有就停止。
2. **了解同行人。** `family.list_members` 拿到成员,注意有没有儿童、老人或过敏成员。
3. **查历史。** `family.list_events` 看有没有过去的同类出行;`family.search_assets` 看去过的地方的照片。
4. **产出草案。** 逐日行程、每日的家庭偏好、需要注意的事项。
5. **提交日历事件。** 用 `family.plan_transaction` 创建事件与任务,让 Family Manager 审批。
6. **不要替用户预订。** 本技能只产出计划,不执行任何外部预订。

## 硬性禁止

- 禁止在没有确认成员构成的情况下给出逐人日程。
- 禁止绕过事务直接写日历。
- 禁止代替用户联系外部服务或下单。

## 可用工具

| 用途 | 工具 |
|------|------|
| 成员 | `family.list_members` |
| 历史事件 | `family.list_events` |
| 历史照片 | `family.search_assets` |
| 写入计划 | `family.plan_transaction` |

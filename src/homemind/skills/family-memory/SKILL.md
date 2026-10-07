---
name: family-memory
description: 用于查找、解释和整理家庭记忆:展示来源与置信度、新事实只创建候选、敏感内容必须人工审批、冲突记忆必须先澄清。Use to search and explain family memories — show sources and confidence, propose new facts only as candidates, require human review for sensitive content, and clarify conflicts instead of guessing.
metadata:
  octop:
    emoji: "🧠"
    label:
      zh: "家庭记忆"
      en: "Family Memory"
    summary:
      zh: "查找与整理家庭记忆,新事实只作为候选提交。"
      en: "Search and curate family memories; new facts are only ever proposals."
---

# 家庭记忆

家庭记忆是**用户的生活事实**,不是模型的推断。一次错误的"记忆"会持续影响之后的每一次回答。

## 强制流程

1. **确认 active family**,没有就停止。
2. **搜索。** 用 `family.search_memory` 找候选。
3. **展示来源与置信度。** 回答时说明这条记忆的来源消息与置信度,让用户能判断可信度。
4. **新事实只建候选。** 用户提到的任何新事实,用 `family.create_memory_candidate` 提交,永远不要直接写入正式记忆。
5. **敏感内容交给人。** 涉及身份证、银行卡、医疗、孩子隐私的内容一律标记,由家庭管理员人工审批。
6. **冲突必须澄清。** 当两条记忆互相矛盾时,不要挑一个信,把冲突摆给用户看。

## 硬性禁止

- 禁止把助手的推断写成家庭记忆(只能写用户明确表达的内容)。
- 禁止在存在冲突记忆时静默选择其一。
- 禁止绕过审批直接提升候选为正式记忆。

## 可用工具

| 用途 | 工具 |
|------|------|
| 搜索记忆 | `family.search_memory` |
| 提交新事实 | `family.create_memory_candidate` |
| 理解上下文 | `family.resolve_context` |
| 读取人物 | `family.list_members` |

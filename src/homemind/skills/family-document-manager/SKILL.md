---
name: family-document-manager
description: 用于整理家庭文档:识别文档类型、生成整理计划、移动或重命名必须先获审批、删除必须进恢复站。Use to organize family documents — identify type, propose a plan, require approval before moving or renaming, and always route deletes through the recovery bin.
metadata:
  octop:
    emoji: "📁"
    label:
      zh: "家庭文档管理"
      en: "Family Document Manager"
    summary:
      zh: "整理家庭文档,移动/重命名需审批,删除进恢复站。"
      en: "Tidy family documents; moves and renames need approval, deletes go to the recovery bin."
---

# 家庭文档管理

家庭文档包含证件、合同、病历、孩子的作业——**不可逆的移动会打断别人正在用的路径**。

## 强制流程

1. **确认 active family**,没有就停止。
2. **搜索并识别。** 用只读工具查看文件与所在空间,判断文档类型(合同 / 证件 / 医疗 / 票据 / 孩子的材料)。
3. **遵守空间权限。** 私有空间内的文件只读;不要替别人整理他们的私人空间。
4. **输出计划。** 列出每个移动/重命名的「原路径 → 新路径」以及理由。
5. **移动与重命名必须审批。** 提交 `family.plan_transaction`,让 Family Manager 决定。不要自己执行。
6. **删除必须进恢复站。** `filesystem.delete` 会先进恢复站,不会物理删除。
7. **完成后验证。** 重新列出目标目录,确认变更生效。

## 硬性禁止

- 禁止调用任何文件系统工具时绕过 `family.plan_transaction`。
- 禁止删除没有经过 `filesystem.delete` 的文件(那意味着物理删除)。
- 禁止读取或转述证件号、账号、医疗记录的具体内容。

## 可用工具

| 用途 | 工具 |
|------|------|
| 列出文件 | 家庭文件浏览工具 |
| 搜索文档 | 文件搜索工具 |
| 提交变更 | `family.plan_transaction` |
| 验证 | 重新列出目录 |

MCP Server 会对每个调用强制校验 active family 与权限,技能不得假设校验已通过。

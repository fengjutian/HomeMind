import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Form,
  Input,
  InputNumber,
  List,
  Modal,
  Popconfirm,
  Select,
  Slider,
  Space,
  Tag,
  Typography,
} from "antd";
import {
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyMemory,
} from "../../api/modules/homeMindFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface MemoryPanelProps {
  familyId: string;
  onMemoriesChanged?: () => void | Promise<void>;
}

const MEMORY_TYPES = [
  "FACT",
  "PREFERENCE",
  "EVENT",
  "RELATIONSHIP",
  "HABIT",
  "DECISION",
  "EXPERIENCE",
] as const;

const VISIBILITIES = ["PUBLIC", "FAMILY", "PRIVATE", "SENSITIVE"] as const;

interface MemoryFormValues {
  content: string;
  memory_type: string;
  importance: number;
  confidence: number;
  visibility: string;
  expires_at?: number;
}

function visibilityColor(v: string): string {
  switch (v) {
    case "PUBLIC":
      return "green";
    case "FAMILY":
      return "blue";
    case "PRIVATE":
      return "gold";
    case "SENSITIVE":
      return "red";
    default:
      return "default";
  }
}

export default function MemoryPanel({
  familyId,
  onMemoriesChanged,
}: MemoryPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const timezone = useServerTimezone();
  const [memories, setMemories] = useState<FamilyMemory[]>([]);
  const [loading, setLoading] = useState(false);
  const [ftsQuery, setFtsQuery] = useState("");
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<FamilyMemory | null>(null);
  const [form] = Form.useForm<MemoryFormValues>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await homeMindFamilyApi.listMemories(familyId);
      setMemories(rows);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  useEffect(() => {
    void load();
  }, [load]);

  const notifyChanged = useCallback(async () => {
    if (!onMemoriesChanged) return;
    try {
      await onMemoriesChanged();
    } catch {
      // parent owns its own error display
    }
  }, [onMemoriesChanged]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      memory_type: "FACT",
      importance: 0.5,
      confidence: 0.8,
      visibility: "FAMILY",
    });
    setEditorOpen(true);
  };

  const openEdit = (memory: FamilyMemory) => {
    setEditing(memory);
    form.setFieldsValue({
      content: memory.content,
      memory_type: memory.memory_type,
      importance: memory.importance,
      confidence: memory.confidence,
      visibility: memory.visibility,
      expires_at: memory.expires_at ?? undefined,
    });
    setEditorOpen(true);
  };

  const submit = async (values: MemoryFormValues) => {
    try {
      if (editing) {
        await homeMindFamilyApi.updateMemory(familyId, editing.id, {
          content: values.content,
          memory_type: values.memory_type,
          importance: values.importance,
          confidence: values.confidence,
          visibility: values.visibility as
            | "PUBLIC"
            | "FAMILY"
            | "PRIVATE"
            | "SENSITIVE",
          expires_at: values.expires_at ?? null,
        });
        message.success(t("family.memoryUpdated", "记忆已更新"));
      } else {
        await homeMindFamilyApi.createMemory(familyId, {
          content: values.content,
          memory_type: values.memory_type,
          importance: values.importance,
          confidence: values.confidence,
          visibility: values.visibility as
            | "PUBLIC"
            | "FAMILY"
            | "PRIVATE"
            | "SENSITIVE",
          expires_at: values.expires_at,
        });
        message.success(t("family.memoryCreated", "记忆已创建"));
      }
      setEditorOpen(false);
      await load();
      await notifyChanged();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const remove = async (memory: FamilyMemory) => {
    try {
      await homeMindFamilyApi.deleteMemory(familyId, memory.id);
      message.success(t("family.memoryDeleted", "记忆已删除"));
      await load();
      await notifyChanged();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const runFts = async () => {
    const q = ftsQuery.trim();
    if (!q) {
      await load();
      return;
    }
    setLoading(true);
    try {
      const rows = await homeMindFamilyApi.searchMemoriesFts(familyId, q, 50);
      setMemories(rows);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className={styles.singleColumn}>
      <Card
        title={t("family.memories", "家庭记忆")}
        className={styles.panelCard}
        extra={
          <Space>
            <Button
              type="text"
              loading={loading}
              icon={<RefreshCw size={16} />}
              onClick={load}
            />
            <Button
              type="primary"
              icon={<Plus size={16} />}
              onClick={openCreate}
            >
              {t("family.createMemory", "新增记忆")}
            </Button>
          </Space>
        }
      >
        <Space.Compact style={{ width: "100%", marginBottom: 16 }}>
          <Input
            allowClear
            placeholder={t(
              "family.memoryFtsPlaceholder",
              "FTS5 关键词（标题/内容/标签）",
            )}
            value={ftsQuery}
            onChange={(e) => setFtsQuery(e.target.value)}
            onPressEnter={runFts}
          />
          <Button
            type="primary"
            icon={<Search size={16} />}
            loading={loading}
            onClick={runFts}
          >
            {t("common.search", "搜索")}
          </Button>
        </Space.Compact>

        <List
          dataSource={memories}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(memory) => (
            <List.Item
              actions={[
                <Button
                  key="edit"
                  type="text"
                  icon={<Pencil size={16} />}
                  onClick={() => openEdit(memory)}
                >
                  {t("common.edit", "编辑")}
                </Button>,
                <Popconfirm
                  key="delete"
                  title={t("family.deleteMemory", "删除该记忆？")}
                  okText={t("family.confirm", "确认")}
                  cancelText={t("family.cancel", "取消")}
                  onConfirm={() => void remove(memory)}
                >
                  <Button type="text" danger icon={<Trash2 size={16} />}>
                    {t("common.delete", "删除")}
                  </Button>
                </Popconfirm>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space wrap>
                    <Tag color="purple">
                      <Sparkles size={12} /> {memory.memory_type}
                    </Tag>
                    <Tag color={visibilityColor(memory.visibility)}>
                      {memory.visibility}
                    </Tag>
                    <Tag color="cyan">
                      {t("family.importance", "重要度")}：
                      {memory.importance.toFixed(2)}
                    </Tag>
                    {memory.expires_at !== null && (
                      <Tag color="orange">
                        {t("family.expiresAt", "过期")}：
                        {formatServerDateTime(memory.expires_at, timezone)}
                      </Tag>
                    )}
                  </Space>
                }
                description={
                  <Space direction="vertical" size={4}>
                    <Typography.Text>{memory.content}</Typography.Text>
                    <Typography.Text type="secondary">
                      {t("family.createdAt", "创建时间")}：
                      {formatServerDateTime(memory.created_at, timezone)}
                    </Typography.Text>
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>

      <Modal
        open={editorOpen}
        title={
          editing
            ? t("family.editMemory", "编辑记忆")
            : t("family.createMemory", "新增记忆")
        }
        onCancel={() => setEditorOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Form<MemoryFormValues> form={form} layout="vertical" onFinish={submit}>
          <Form.Item
            name="content"
            label={t("family.memoryContent", "记忆内容")}
            rules={[{ required: true, min: 1, max: 10000 }]}
          >
            <Input.TextArea
              rows={4}
              placeholder={t(
                "family.memoryContentPlaceholder",
                "例如：外婆喜欢茉莉花茶，每周日下午固定在家泡茶。",
              )}
            />
          </Form.Item>
          <Form.Item
            name="memory_type"
            label={t("family.memoryType", "类型")}
            rules={[{ required: true }]}
          >
            <Select
              options={MEMORY_TYPES.map((value) => ({
                value,
                label: t(`family.memoryType.${value.toLowerCase()}`, value),
              }))}
            />
          </Form.Item>
          <Form.Item
            name="visibility"
            label={t("family.memoryVisibility", "可见性")}
            rules={[{ required: true }]}
          >
            <Select
              options={VISIBILITIES.map((value) => ({
                value,
                label: t(`family.visibility.${value.toLowerCase()}`, value),
              }))}
            />
          </Form.Item>
          <Form.Item
            name="importance"
            label={t("family.importance", "重要度")}
            rules={[{ type: "number", min: 0, max: 1 }]}
          >
            <Slider min={0} max={1} step={0.05} />
          </Form.Item>
          <Form.Item
            name="confidence"
            label={t("family.confidence", "置信度")}
            rules={[{ type: "number", min: 0, max: 1 }]}
          >
            <Slider min={0} max={1} step={0.05} />
          </Form.Item>
          <Form.Item
            name="expires_at"
            label={t("family.memoryExpiresAt", "过期时间（可选，毫秒时间戳）")}
          >
            <InputNumber
              style={{ width: "100%" }}
              placeholder={t(
                "family.memoryExpiresPlaceholder",
                "如 1735689600000；留空=永不过期",
              )}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<Plus size={16} />}>
            {editing
              ? t("common.save", "保存")
              : t("common.create", "创建")}
          </Button>
        </Form>
      </Modal>
    </div>
  );
}

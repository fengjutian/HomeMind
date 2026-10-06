import { useCallback, useEffect, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  DatePicker,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyEvent,
} from "../../api/modules/homeMindFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface EventsPanelProps {
  familyId: string;
  onEventsChanged?: () => void | Promise<void>;
}

const EVENT_TYPE_PRESETS = [
  "BIRTHDAY",
  "ANNIVERSARY",
  "TRIP",
  "SCHOOL",
  "MEDICAL",
  "HOLIDAY",
  "MEETING",
  "CUSTOM",
] as const;

interface EventFormValues {
  event_type: string;
  title: string;
  range: [Dayjs, Dayjs];
  location?: string;
  description?: string;
}

function presetLabel(preset: string): string {
  switch (preset) {
    case "BIRTHDAY":
      return "生日";
    case "ANNIVERSARY":
      return "纪念日";
    case "TRIP":
      return "旅行";
    case "SCHOOL":
      return "学校";
    case "MEDICAL":
      return "医疗";
    case "HOLIDAY":
      return "节日";
    case "MEETING":
      return "聚会";
    default:
      return preset;
  }
}

export default function EventsPanel({
  familyId,
  onEventsChanged,
}: EventsPanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const timezone = useServerTimezone();
  const [events, setEvents] = useState<FamilyEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<FamilyEvent | null>(null);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [form] = Form.useForm<EventFormValues>();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await homeMindFamilyApi.listEvents(familyId);
      setEvents(rows);
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
    if (!onEventsChanged) return;
    try {
      await onEventsChanged();
    } catch {
      // parent owns its own error display
    }
  }, [onEventsChanged]);

  const visibleEvents = useMemo(() => {
    if (!typeFilter) return events;
    return events.filter((row) => row.event_type === typeFilter);
  }, [events, typeFilter]);

  const typeOptions = useMemo(() => {
    const set = new Set(events.map((row) => row.event_type));
    return Array.from(set).sort();
  }, [events]);

  const openCreate = () => {
    setEditing(null);
    form.resetFields();
    form.setFieldsValue({
      event_type: "CUSTOM",
      range: [dayjs(), dayjs().add(1, "hour")],
    });
    setEditorOpen(true);
  };

  const openEdit = (event: FamilyEvent) => {
    setEditing(event);
    form.setFieldsValue({
      event_type: event.event_type,
      title: event.title,
      range: [dayjs(event.start_at), dayjs(event.end_at)],
      location: event.location ?? undefined,
      description: event.description ?? undefined,
    });
    setEditorOpen(true);
  };

  const submit = async (values: EventFormValues) => {
    const [start, end] = values.range;
    const startAt = start.valueOf();
    const endAt = end.valueOf();
    if (endAt < startAt) {
      message.error(t("family.eventRangeInvalid", "结束时间不能早于开始时间"));
      return;
    }
    try {
      if (editing) {
        await homeMindFamilyApi.updateEvent(familyId, editing.id, {
          event_type: values.event_type,
          title: values.title,
          start_at: startAt,
          end_at: endAt,
          location: values.location ?? null,
          description: values.description ?? "",
        });
        message.success(t("family.eventUpdated", "事件已更新"));
      } else {
        await homeMindFamilyApi.createEvent(familyId, {
          event_type: values.event_type,
          title: values.title,
          start_at: startAt,
          end_at: endAt,
          location: values.location,
          description: values.description ?? "",
        });
        message.success(t("family.eventCreated", "事件已创建"));
      }
      setEditorOpen(false);
      await load();
      await notifyChanged();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  const remove = async (event: FamilyEvent) => {
    try {
      await homeMindFamilyApi.deleteEvent(familyId, event.id);
      message.success(t("family.eventDeleted", "事件已删除"));
      await load();
      await notifyChanged();
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <div className={styles.singleColumn}>
      <Card
        title={t("family.events", "家庭事件")}
        className={styles.panelCard}
        extra={
          <Space>
            <Select
              allowClear
              size="small"
              placeholder={t("family.eventTypeFilter", "按类型筛选")}
              value={typeFilter ?? undefined}
              onChange={(value) => setTypeFilter(value ?? null)}
              style={{ width: 160 }}
              options={typeOptions.map((value) => ({
                value,
                label: t(`family.eventType.${value.toLowerCase()}`, value),
              }))}
            />
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
              {t("family.createEvent", "新增事件")}
            </Button>
          </Space>
        }
      >
        <List
          dataSource={visibleEvents}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(event) => (
            <List.Item
              actions={[
                <Button
                  key="edit"
                  type="text"
                  icon={<Pencil size={16} />}
                  onClick={() => openEdit(event)}
                >
                  {t("common.edit", "编辑")}
                </Button>,
                <Popconfirm
                  key="delete"
                  title={t("family.deleteEvent", "删除该事件？")}
                  okText={t("family.confirm", "确认")}
                  cancelText={t("family.cancel", "取消")}
                  onConfirm={() => void remove(event)}
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
                    <span>{event.title}</span>
                    <Tag color="blue">{event.event_type}</Tag>
                    {event.location && (
                      <Tag color="geekblue">
                        {t("family.eventLocation", "地点")}：
                        {event.location}
                      </Tag>
                    )}
                  </Space>
                }
                description={
                  <Space direction="vertical" size={4}>
                    <Typography.Text type="secondary">
                      {formatServerDateTime(event.start_at, timezone)} →
                      {formatServerDateTime(event.end_at, timezone)}
                    </Typography.Text>
                    {event.description && (
                      <Typography.Text>{event.description}</Typography.Text>
                    )}
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
            ? t("family.editEvent", "编辑事件")
            : t("family.createEvent", "新增事件")
        }
        onCancel={() => setEditorOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Form<EventFormValues> form={form} layout="vertical" onFinish={submit}>
          <Form.Item
            name="event_type"
            label={t("family.eventType", "事件类型")}
            rules={[{ required: true }]}
          >
            <Select
              showSearch
              options={EVENT_TYPE_PRESETS.map((value) => ({
                value,
                label: t(
                  `family.eventType.${value.toLowerCase()}`,
                  presetLabel(value),
                ),
              }))}
            />
          </Form.Item>
          <Form.Item
            name="title"
            label={t("family.eventTitle", "标题")}
            rules={[{ required: true, min: 1, max: 200 }]}
          >
            <Input placeholder={t("family.eventTitlePlaceholder", "如：爷爷生日")} />
          </Form.Item>
          <Form.Item
            name="range"
            label={t("family.eventRange", "起止时间")}
            rules={[{ required: true }]}
          >
            <DatePicker.RangePicker showTime style={{ width: "100%" }} />
          </Form.Item>
          <Form.Item name="location" label={t("family.eventLocation", "地点")}>
            <Input placeholder={t("family.eventLocationPlaceholder", "可选")} />
          </Form.Item>
          <Form.Item
            name="description"
            label={t("family.eventDescription", "描述")}
          >
            <Input.TextArea rows={3} maxLength={5000} showCount />
          </Form.Item>
          <Button type="primary" htmlType="submit" icon={<Plus size={16} />}>
            {editing ? t("common.save", "保存") : t("common.create", "创建")}
          </Button>
        </Form>
      </Modal>
    </div>
  );
}

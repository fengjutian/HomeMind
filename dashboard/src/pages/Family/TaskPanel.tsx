import { useMemo, useState } from "react";
import {
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
import { Check, Pencil, Plus, RotateCcw, Trash2, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyMember,
  type FamilyTask,
} from "../../api/modules/homeMindFamily";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface TaskPanelProps {
  familyId: string;
  members: FamilyMember[];
  tasks: FamilyTask[];
  timezone: string;
  reload: () => Promise<void>;
}

interface TaskFormValues {
  title: string;
  description?: string;
  assigned_member_id?: string;
  due_at?: Dayjs;
  status?: string;
}

const statuses = [
  "TODO",
  "IN_PROGRESS",
  "WAITING_APPROVAL",
  "DONE",
  "FAILED",
  "CANCELLED",
];

export default function TaskPanel({
  familyId,
  members,
  tasks,
  timezone,
  reload,
}: TaskPanelProps) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<string>();
  const [editing, setEditing] = useState<FamilyTask | null>(null);
  const [editForm] = Form.useForm<TaskFormValues>();
  const visibleTasks = useMemo(
    () => (status ? tasks.filter((task) => task.status === status) : tasks),
    [status, tasks],
  );
  const memberName = (memberId: string | null) =>
    memberId
      ? members.find((member) => member.id === memberId)?.display_name ??
        memberId
      : "";

  const updateStatus = async (task: FamilyTask, nextStatus: string) => {
    await homeMindFamilyApi.updateTask(familyId, task.id, {
      status: nextStatus,
    });
    await reload();
  };

  return (
    <Card className={styles.panelCard}>
      <Form
        className={styles.quickForm}
        layout="inline"
        onFinish={async (values: TaskFormValues) => {
          await homeMindFamilyApi.createTask(familyId, {
            ...values,
            due_at: values.due_at?.unix(),
          });
          await reload();
        }}
      >
        <Form.Item name="title" rules={[{ required: true }]}>
          <Input placeholder={t("family.taskTitle", "要完成的事情")} />
        </Form.Item>
        <Form.Item name="assigned_member_id">
          <Select
            allowClear
            className={styles.inlineSelect}
            placeholder={t("family.assignee", "负责人")}
            options={members.map((member) => ({
              value: member.id,
              label: member.display_name,
            }))}
          />
        </Form.Item>
        <Form.Item name="due_at">
          <DatePicker showTime placeholder={t("family.dueAt", "截止时间")} />
        </Form.Item>
        <Button icon={<Plus size={16} />} type="primary" htmlType="submit">
          {t("common.create", "创建")}
        </Button>
      </Form>

      <Select
        allowClear
        className={styles.taskFilter}
        placeholder={t("family.allTaskStatuses", "全部任务状态")}
        value={status}
        onChange={setStatus}
        options={statuses.map((value) => ({
          value,
          label: t(`family.taskStatus.${value}`, value),
        }))}
      />
      <List
        dataSource={visibleTasks}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
        renderItem={(task) => (
          <List.Item
            actions={[
              task.status !== "DONE" ? (
                <Button
                  key="done"
                  type="text"
                  icon={<Check size={15} />}
                  onClick={() => updateStatus(task, "DONE")}
                >
                  {t("family.completeTask", "完成")}
                </Button>
              ) : (
                <Button
                  key="reopen"
                  type="text"
                  icon={<RotateCcw size={15} />}
                  onClick={() => updateStatus(task, "TODO")}
                >
                  {t("family.reopenTask", "重新打开")}
                </Button>
              ),
              task.status !== "CANCELLED" && (
                <Button
                  key="cancel"
                  type="text"
                  icon={<X size={15} />}
                  onClick={() => updateStatus(task, "CANCELLED")}
                >
                  {t("family.cancelTask", "取消")}
                </Button>
              ),
              <Button
                key="edit"
                type="text"
                icon={<Pencil size={15} />}
                onClick={() => {
                  editForm.setFieldsValue({
                    title: task.title,
                    description: task.description,
                    status: task.status,
                    assigned_member_id: task.assigned_member_id ?? undefined,
                    due_at: task.due_at ? dayjs.unix(task.due_at) : undefined,
                  });
                  setEditing(task);
                }}
              />,
              <Popconfirm
                key="delete"
                title={t("family.deleteTaskConfirm", "确定删除该任务吗？")}
                onConfirm={async () => {
                  await homeMindFamilyApi.deleteTask(familyId, task.id);
                  await reload();
                }}
              >
                <Button type="text" danger icon={<Trash2 size={15} />} />
              </Popconfirm>,
            ].filter(Boolean)}
          >
            <List.Item.Meta
              title={task.title}
              description={
                <Space wrap>
                  <Tag>
                    {t(`family.taskStatus.${task.status}`, task.status)}
                  </Tag>
                  {task.assigned_member_id && (
                    <Typography.Text type="secondary">
                      {memberName(task.assigned_member_id)}
                    </Typography.Text>
                  )}
                  {task.due_at && (
                    <Typography.Text type="secondary">
                      {formatServerDateTime(task.due_at, timezone)}
                    </Typography.Text>
                  )}
                  {task.description && (
                    <Typography.Text type="secondary">
                      {task.description}
                    </Typography.Text>
                  )}
                </Space>
              }
            />
          </List.Item>
        )}
      />

      <Modal
        title={t("family.editTask", "编辑任务")}
        open={editing !== null}
        onCancel={() => setEditing(null)}
        onOk={() => editForm.submit()}
      >
        <Form
          form={editForm}
          layout="vertical"
          onFinish={async (values: TaskFormValues) => {
            if (!editing) return;
            await homeMindFamilyApi.updateTask(familyId, editing.id, {
              ...values,
              due_at: values.due_at?.unix() ?? null,
            });
            setEditing(null);
            await reload();
          }}
        >
          <Form.Item
            name="title"
            label={t("family.taskTitleLabel", "任务标题")}
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="description" label={t("family.description", "说明")}>
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item
            name="assigned_member_id"
            label={t("family.assignee", "负责人")}
          >
            <Select
              allowClear
              options={members.map((member) => ({
                value: member.id,
                label: member.display_name,
              }))}
            />
          </Form.Item>
          <Form.Item name="due_at" label={t("family.dueAt", "截止时间")}>
            <DatePicker showTime className={styles.fullWidth} />
          </Form.Item>
          <Form.Item name="status" label={t("family.taskStatusLabel", "状态")}>
            <Select
              options={statuses.map((value) => ({
                value,
                label: t(`family.taskStatus.${value}`, value),
              }))}
            />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}

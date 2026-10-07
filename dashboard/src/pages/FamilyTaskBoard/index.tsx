/**
 * Family task board (Stage 4).
 *
 * The same tasks the plain to-do list shows, arranged by the states the
 * scheduler actually uses. Each column answers a question a family
 * really asks: what is waiting, what is running, what needs me.
 *
 * `WAITING_APPROVAL` is deliberately the widest column — it is the one
 * state only a human can clear, so the board makes it impossible to
 * miss.
 */
import { useCallback, useState } from "react";
import {
  App,
  Badge,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import dayjs from "dayjs";
import { useTranslation } from "react-i18next";

import {
  familyTaskSchedulerApi,
  type ScheduledTask,
  type TaskBoard,
} from "../../api/modules/familyTaskScheduler";
import FamilyPageShell, {
  QueryError,
  useFamilyQuery,
} from "../../components/family/FamilyPageShell";
import { useActiveFamily } from "../../hooks/useActiveFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatMessageTime } from "../../utils/formatMessageTime";

const { Text } = Typography;

type ColumnKey = keyof TaskBoard;

const COLUMNS: { key: ColumnKey; labelKey: string }[] = [
  { key: "todo", labelKey: "familyTaskBoard.todo" },
  { key: "scheduled", labelKey: "familyTaskBoard.scheduled" },
  { key: "in_progress", labelKey: "familyTaskBoard.inProgress" },
  { key: "waiting_approval", labelKey: "familyTaskBoard.waitingApproval" },
  { key: "blocked", labelKey: "familyTaskBoard.blocked" },
  { key: "failed", labelKey: "familyTaskBoard.failed" },
  { key: "done", labelKey: "familyTaskBoard.done" },
];

export default function FamilyTaskBoard() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const timezone = useServerTimezone();
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<ScheduledTask | null>(null);

  const fetcher = useCallback((id: string) => familyTaskSchedulerApi.board(id), []);
  const { data, loading, error, reload } = useFamilyQuery(familyId, fetcher);

  const act = async (
    run: () => Promise<unknown>,
    successKey: string,
  ): Promise<void> => {
    try {
      await run();
      message.success(t(successKey, "done"));
      reload();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <FamilyPageShell
      title={t("familyTaskBoard.title", "家庭任务看板")}
      actions={
        <Button type="primary" onClick={() => setCreating(true)}>
          {t("familyTaskBoard.newTask", "新建任务")}
        </Button>
      }
    >
      <>
        <QueryError error={error} />
        <Row gutter={[12, 12]}>
          {COLUMNS.map((column) => (
            <Col key={column.key} xs={24} sm={12} lg={8} xl={6}>
              <BoardColumn
                title={t(column.labelKey, column.key)}
                tasks={data?.[column.key] ?? []}
                loading={loading && !data}
                timezone={timezone}
                onSelect={setSelected}
                onCancel={
                  familyId
                    ? (task) =>
                        void act(
                          () => familyTaskSchedulerApi.cancel(familyId, task.id),
                          "familyTaskBoard.cancelled",
                        )
                    : undefined
                }
                onRetry={
                  familyId
                    ? (task) =>
                        void act(
                          () => familyTaskSchedulerApi.retry(familyId, task.id),
                          "familyTaskBoard.retried",
                        )
                    : undefined
                }
              />
            </Col>
          ))}
        </Row>
        <CreateTaskModal
          open={creating}
          onClose={() => setCreating(false)}
          onCreated={reload}
        />
        <TaskDetailModal
          task={selected}
          timezone={timezone}
          onClose={() => setSelected(null)}
        />
      </>
    </FamilyPageShell>
  );
}

function BoardColumn({
  title,
  tasks,
  loading,
  timezone,
  onSelect,
  onCancel,
  onRetry,
}: {
  title: string;
  tasks: ScheduledTask[];
  loading: boolean;
  timezone: string;
  onSelect: (task: ScheduledTask) => void;
  onCancel?: (task: ScheduledTask) => void;
  onRetry?: (task: ScheduledTask) => void;
}) {
  const { t } = useTranslation();
  return (
    <Card size="small" loading={loading} title={<Badge count={tasks.length}>{title}</Badge>}>
      {tasks.length === 0 ? (
        <Text type="secondary">{t("familyTaskBoard.empty", "空")}</Text>
      ) : (
        tasks.map((task) => (
          <div key={task.id} style={{ marginBottom: 8 }}>
            <div style={{ cursor: "pointer" }} onClick={() => onSelect(task)}>
              <Text strong>{task.title}</Text>
            </div>
            <Space size={4} wrap>
              <Tag>{task.task_type}</Tag>
              {task.agent_id ? (
                <Tooltip title={t("familyTaskBoard.agent", "执行 Agent")}>
                  <Tag color="blue">{task.agent_id}</Tag>
                </Tooltip>
              ) : null}
              {task.recurrence_rule ? <Tag color="cyan">{task.recurrence_rule}</Tag> : null}
              {task.schedule_at !== null ? (
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {formatMessageTime(task.schedule_at * 1000, timezone)}
                </Text>
              ) : null}
              {task.attempt_count > 0 ? (
                <Tag color="orange">
                  {t("familyTaskBoard.attempts", "尝试")} {task.attempt_count}/
                  {task.max_attempts}
                </Tag>
              ) : null}
            </Space>
            {task.last_error ? (
              <Text type="danger" style={{ fontSize: 12 }}>
                {task.last_error}
              </Text>
            ) : null}
            <Space size={4}>
              {onCancel && !["DONE", "CANCELLED"].includes(task.status) ? (
                <Popconfirm
                  title={t("familyTaskBoard.cancelConfirm", "取消这个任务？")}
                  onConfirm={() => onCancel(task)}
                >
                  <Button size="small" type="text">
                    {t("familyTaskBoard.cancel", "取消")}
                  </Button>
                </Popconfirm>
              ) : null}
              {onRetry && ["FAILED", "BLOCKED"].includes(task.status) ? (
                <Button size="small" type="text" onClick={() => onRetry(task)}>
                  {t("familyTaskBoard.retry", "重试")}
                </Button>
              ) : null}
            </Space>
          </div>
        ))
      )}
    </Card>
  );
}

function CreateTaskModal({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [form] = Form.useForm();

  const submit = async () => {
    if (familyId === null) return;
    const values = await form.validateFields();
    try {
      await familyTaskSchedulerApi.create(familyId, {
        title: values.title,
        description: values.description ?? "",
        task_type: values.task_type ?? "DEVICE",
        agent_id: values.agent_id ?? null,
        schedule_at: values.schedule_at ? values.schedule_at.valueOf() : null,
        recurrence_rule: values.recurrence_rule || null,
        priority: values.priority ?? 0,
        max_attempts: values.max_attempts ?? 3,
      });
      message.success(t("familyTaskBoard.created", "已创建"));
      form.resetFields();
      onCreated();
      onClose();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <Modal
      open={open}
      title={t("familyTaskBoard.newTask", "新建任务")}
      onCancel={onClose}
      onOk={() => void submit()}
      okText={t("familyTaskBoard.create", "创建")}
      destroyOnHidden
    >
      <Form form={form} layout="vertical" initialValues={{ task_type: "DEVICE", max_attempts: 3 }}>
        <Form.Item name="title" label={t("familyTaskBoard.title_", "标题")} rules={[{ required: true }]}>
          <Input maxLength={200} />
        </Form.Item>
        <Form.Item name="description" label={t("familyTaskBoard.description", "说明")}>
          <Input.TextArea rows={2} maxLength={5000} />
        </Form.Item>
        <Form.Item name="task_type" label={t("familyTaskBoard.taskType", "类型")}>
          <Select
            options={[
              { value: "DEVICE", label: t("familyTaskBoard.typeDevice", "设备/系统") },
              { value: "AGENT", label: t("familyTaskBoard.typeAgent", "由 Agent 执行") },
            ]}
          />
        </Form.Item>
        <Form.Item
          noStyle
          shouldUpdate={(prev, next) => prev.task_type !== next.task_type}
        >
          {({ getFieldValue }) =>
            getFieldValue("task_type") === "AGENT" ? (
              <Form.Item
                name="agent_id"
                label={t("familyTaskBoard.agent", "执行 Agent")}
                rules={[{ required: true }]}
              >
                <Input placeholder={t("familyTaskBoard.agentHint", "Agent ID")} />
              </Form.Item>
            ) : null
          }
        </Form.Item>
        <Form.Item name="schedule_at" label={t("familyTaskBoard.scheduleAt", "执行时间")}>
          <DatePicker showTime style={{ width: "100%" }} />
        </Form.Item>
        <Form.Item
          name="recurrence_rule"
          label={t("familyTaskBoard.recurrence", "重复规则")}
          extra={t("familyTaskBoard.recurrenceHint", "RFC 5545 RRULE；每次运行只生成下一个")}
        >
          <Select
            allowClear
            options={[
              { value: "FREQ=DAILY", label: t("familyTaskBoard.daily", "每天") },
              { value: "FREQ=WEEKLY", label: t("familyTaskBoard.weekly", "每周") },
              { value: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", label: t("familyTaskBoard.weekdays", "工作日") },
              { value: "FREQ=MONTHLY", label: t("familyTaskBoard.monthly", "每月") },
            ]}
          />
        </Form.Item>
        <Space>
          <Form.Item name="priority" label={t("familyTaskBoard.priority", "优先级")}>
            <InputNumber min={-100} max={100} />
          </Form.Item>
          <Form.Item name="max_attempts" label={t("familyTaskBoard.maxAttempts", "最大尝试")}>
            <InputNumber min={1} max={20} />
          </Form.Item>
        </Space>
      </Form>
    </Modal>
  );
}

function TaskDetailModal({
  task,
  timezone,
  onClose,
}: {
  task: ScheduledTask | null;
  timezone: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const { familyId } = useActiveFamily();
  const [attempts, setAttempts] = useState<
    Awaited<ReturnType<typeof familyTaskSchedulerApi.listAttempts>> | null
  >(null);

  const fetcher = useCallback(
    (taskId: string) => familyTaskSchedulerApi.listAttempts(familyId as string, taskId),
    [familyId],
  );
  const { data, loading } = useFamilyQuery(task?.id ?? null, fetcher);

  return (
    <Modal
      open={task !== null}
      title={task?.title ?? ""}
      onCancel={onClose}
      footer={null}
      destroyOnHidden
      afterOpenChange={(isOpen) => setAttempts(isOpen ? (data ?? null) : null)}
    >
      {task ? (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Space size={4} wrap>
            <Tag>{task.status}</Tag>
            <Tag>{task.task_type}</Tag>
            {task.agent_id ? <Tag color="blue">{task.agent_id}</Tag> : null}
            {task.priority !== 0 ? <Tag color="gold">P{task.priority}</Tag> : null}
            {task.version > 1 ? <Tag>v{task.version}</Tag> : null}
          </Space>
          {task.description ? <Text>{task.description}</Text> : null}
          {task.result_summary ? (
            <Text type="secondary">{task.result_summary}</Text>
          ) : null}
          {task.last_error ? <Text type="danger">{task.last_error}</Text> : null}
          {task.transaction_id ? (
            <Text type="secondary">
              {t("familyTaskBoard.transaction", "关联事务")}: {task.transaction_id}
            </Text>
          ) : null}
          {task.schedule_at !== null ? (
            <Text type="secondary">
              {t("familyTaskBoard.scheduleAt", "执行时间")}:{" "}
              {dayjs(task.schedule_at * 1000).format("YYYY-MM-DD HH:mm")}
            </Text>
          ) : null}
          {task.started_at !== null ? (
            <Text type="secondary">
              {t("familyTaskBoard.startedAt", "开始")}:{" "}
              {formatMessageTime(task.started_at * 1000, timezone)}
            </Text>
          ) : null}
          {task.completed_at !== null ? (
            <Text type="secondary">
              {t("familyTaskBoard.completedAt", "完成")}:{" "}
              {formatMessageTime(task.completed_at * 1000, timezone)}
            </Text>
          ) : null}
          <Card
            size="small"
            title={t("familyTaskBoard.attemptsTitle", "执行记录")}
            loading={loading}
          >
            {(attempts ?? []).length === 0 ? (
              <Text type="secondary">{t("familyTaskBoard.noAttempts", "还没有执行过")}</Text>
            ) : (
              (attempts ?? []).map((attempt) => (
                <div key={attempt.id}>
                  <Text strong>#{attempt.attempt_number}</Text>{" "}
                  <Tag>{attempt.status}</Tag>
                  {attempt.error ? (
                    <Text type="danger" style={{ fontSize: 12 }}>
                      {" "}
                      {attempt.error}
                    </Text>
                  ) : null}
                </div>
              ))
            )}
          </Card>
        </Space>
      ) : null}
    </Modal>
  );
}
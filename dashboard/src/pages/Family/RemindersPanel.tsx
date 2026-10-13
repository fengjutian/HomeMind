import { useCallback, useEffect, useState } from "react";
import {
  App,
  Button,
  Card,
  DatePicker,
  Empty,
  Form,
  Input,
  List,
  Select,
  Space,
  Statistic,
  Tag,
} from "antd";
import type { Dayjs } from "dayjs";
import { BellPlus, X } from "lucide-react";

import {
  homeMindFamilyApi,
  type FamilyMember,
  type FamilyReminder,
} from "../../api/modules/homeMindFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface Props {
  familyId: string;
  members: FamilyMember[];
}
interface FormValues {
  target_type: string;
  target_id: string;
  remind_at: Dayjs;
  recipient_member_id?: string;
}

export default function RemindersPanel({ familyId, members }: Props) {
  const { message } = App.useApp();
  const timezone = useServerTimezone();
  const [form] = Form.useForm<FormValues>();
  const [rows, setRows] = useState<FamilyReminder[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [status, setStatus] = useState<string>();
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextRows, summary] = await Promise.all([
        homeMindFamilyApi.listReminders(familyId, status),
        homeMindFamilyApi.reminderSummary(familyId),
      ]);
      setRows(nextRows);
      setCounts(summary.counts);
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message, status]);

  useEffect(() => void load(), [load]);

  return (
    <Card className={styles.panelCard} title="提醒管理">
      <div className={styles.summaryGrid}>
        {(["PENDING", "SENT", "FAILED", "CANCELLED"] as const).map((key) => (
          <Card size="small" key={key}>
            <Statistic title={key} value={counts[key] ?? 0} />
          </Card>
        ))}
      </div>
      <Form<FormValues>
        form={form}
        layout="inline"
        className={styles.quickForm}
        initialValues={{ target_type: "TASK" }}
        onFinish={async (values) => {
          await homeMindFamilyApi.createReminder(familyId, {
            ...values,
            remind_at: values.remind_at.unix(),
          });
          message.success("提醒已创建");
          form.resetFields(["target_id", "remind_at", "recipient_member_id"]);
          await load();
        }}
      >
        <Form.Item name="target_type" rules={[{ required: true }]}>
          <Select
            style={{ width: 150 }}
            options={["TASK", "CALENDAR_EVENT", "APPROVAL", "DEVICE"].map(
              (value) => ({ value, label: value }),
            )}
          />
        </Form.Item>
        <Form.Item name="target_id" rules={[{ required: true }]}>
          <Input placeholder="关联对象 ID" />
        </Form.Item>
        <Form.Item name="remind_at" rules={[{ required: true }]}>
          <DatePicker showTime placeholder="提醒时间" />
        </Form.Item>
        <Form.Item name="recipient_member_id">
          <Select
            allowClear
            style={{ width: 160 }}
            placeholder="全部家庭成员"
            options={members.map((member) => ({
              value: member.id,
              label: member.display_name,
            }))}
          />
        </Form.Item>
        <Button type="primary" htmlType="submit" icon={<BellPlus size={16} />}>
          创建提醒
        </Button>
      </Form>
      <Select
        allowClear
        value={status}
        onChange={setStatus}
        placeholder="全部状态"
        style={{ width: 180, marginBlock: 16 }}
        options={["PENDING", "CLAIMED", "SENT", "FAILED", "CANCELLED"].map(
          (value) => ({ value, label: value }),
        )}
      />
      <List
        loading={loading}
        dataSource={rows}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无提醒"
            />
          ),
        }}
        renderItem={(row) => (
          <List.Item
            actions={
              row.status === "PENDING" || row.status === "CLAIMED"
                ? [
                    <Button
                      key="cancel"
                      type="text"
                      danger
                      icon={<X size={15} />}
                      onClick={async () => {
                        await homeMindFamilyApi.cancelReminder(
                          familyId,
                          row.id,
                        );
                        await load();
                      }}
                    >
                      取消
                    </Button>,
                  ]
                : undefined
            }
          >
            <List.Item.Meta
              title={
                <Space>
                  <span>{row.target_type}</span>
                  <Tag>{row.status}</Tag>
                </Space>
              }
              description={
                <Space wrap>
                  <span>{row.target_id}</span>
                  <span>{formatServerDateTime(row.remind_at, timezone)}</span>
                  {row.last_error && <span>{row.last_error}</span>}
                </Space>
              }
            />
          </List.Item>
        )}
      />
    </Card>
  );
}

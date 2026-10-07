/**
 * Approvals page (Stage 8).
 *
 * Groups transactions by their lifecycle state so a manager can see
 * at a glance what is waiting, what is running, and what already
 * finished — the Stage 4 governance view.
 */
import { useCallback, useMemo, useState } from "react";
import { App, Button, Card, Descriptions, Empty, Modal, Popconfirm, Space, Table, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import { useActiveFamily } from "../../hooks/useActiveFamily";
import { homeMindFamilyApi, type FamilyTransaction } from "../../api/modules/homeMindFamily";
import FamilyPageShell, {
  QueryError,
  useFamilyQuery,
} from "../../components/family/FamilyPageShell";

const { Text } = Typography;

// The spec asks for six distinct buckets. ``CANCELLED`` and
// ``COMPENSATED`` are separated from ``COMPLETED`` on purpose: a
// rolled-back write looks identical to a successful one in a merged
// list, and that is exactly the distinction a manager needs.
const GROUPS: Record<string, string[]> = {
  WAITING: ["WAITING_APPROVAL"],
  RUNNING: ["APPROVED", "EXECUTING", "VERIFYING", "COMPENSATING"],
  COMPLETED: ["COMPLETED"],
  FAILED: ["FAILED", "FAILED_REQUIRES_REVIEW"],
  COMPENSATED: ["COMPENSATED"],
  CANCELLED: ["CANCELLED", "REJECTED", "DENIED"],
};

const STATUS_COLORS: Record<string, string> = {
  WAITING_APPROVAL: "warning",
  APPROVED: "processing",
  EXECUTING: "processing",
  VERIFYING: "processing",
  COMPLETED: "success",
  COMPENSATED: "success",
  FAILED: "error",
  FAILED_REQUIRES_REVIEW: "error",
  REJECTED: "default",
  CANCELLED: "default",
  DENIED: "default",
};

export default function FamilyApprovals() {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [group, setGroup] = useState("WAITING");
  const [detail, setDetail] = useState<FamilyTransaction | null>(null);

  const transactionsFetcher = useCallback(
    (id: string) => homeMindFamilyApi.listTransactions(id),
    [],
  );
  const approvalsFetcher = useCallback(
    (id: string) => homeMindFamilyApi.listApprovals(id, "PENDING"),
    [],
  );
  const transactions = useFamilyQuery(familyId, transactionsFetcher);
  const approvals = useFamilyQuery(familyId, approvalsFetcher);

  const rows = useMemo(() => {
    const wanted = GROUPS[group] ?? [];
    return (transactions.data ?? []).filter((row) =>
      wanted.includes(row.status),
    );
  }, [transactions.data, group]);

  const pendingApprovals = approvals.data ?? [];

  const decide = async (
    approvalId: string,
    decision: "approve" | "reject",
  ) => {
    if (familyId === null) return;
    try {
      await homeMindFamilyApi.decideApproval(familyId, approvalId, decision);
      message.success(t(`family.approvals.${decision}d`, "已处理"));
      approvals.reload();
      transactions.reload();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <FamilyPageShell
      title={t("family.approvals.title", "审批")}
      actions={
        <Space>
          {Object.keys(GROUPS).map((key) => (
            <Tag
              key={key}
              color={group === key ? "blue" : "default"}
              style={{ cursor: "pointer" }}
              onClick={() => setGroup(key)}
            >
              {t(`family.approvals.group.${key}`, key)}{" "}
              {key === "WAITING" ? pendingApprovals.length : rows.length}
            </Tag>
          ))}
        </Space>
      }
    >
      <>
      <QueryError error={transactions.error} />

      {group === "WAITING" && pendingApprovals.length > 0 ? (
        <Card size="small" title={t("family.approvals.pending", "待审批")}>
          <Table
            size="small"
            rowKey="id"
            pagination={false}
            dataSource={pendingApprovals}
            columns={[
              {
                title: t("family.approvals.action", "动作"),
                dataIndex: "action",
                render: (value: string) => <Tag>{value}</Tag>,
              },
              {
                title: t("family.approvals.requester", "发起人"),
                dataIndex: "requested_by",
              },
              {
                title: t("family.approvals.expires", "过期时间"),
                dataIndex: "expires_at",
                render: (value: number | null) =>
                  value === null ? "-" : new Date(value * 1000).toLocaleString(),
              },
              {
                title: t("family.approvals.actions", "操作"),
                width: 180,
                render: (_value, row) => (
                  <Space size={4}>
                    <Popconfirm
                      title={t("family.approvals.confirmApprove", "确认批准?")}
                      onConfirm={() => void decide(row.id, "approve")}
                    >
                      <Button size="small" type="primary">
                        {t("family.approvals.approve", "批准")}
                      </Button>
                    </Popconfirm>
                    <Popconfirm
                      title={t("family.approvals.confirmReject", "确认拒绝?")}
                      onConfirm={() => void decide(row.id, "reject")}
                    >
                      <Button size="small" danger>
                        {t("family.approvals.reject", "拒绝")}
                      </Button>
                    </Popconfirm>
                  </Space>
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <Card size="small" style={{ marginTop: 12 }}>
        <Table
          size="small"
          rowKey="id"
          loading={transactions.loading}
          pagination={false}
          dataSource={rows}
          locale={{ emptyText: <Empty description={t("family.approvals.empty", "暂无记录")} /> }}
          columns={[
            {
              title: t("family.approvals.action", "动作"),
              dataIndex: "action",
              render: (value: string) => <Tag>{value}</Tag>,
            },
            {
              title: t("family.approvals.status", "状态"),
              dataIndex: "status",
              width: 180,
              render: (value: string) => (
                <Tag color={STATUS_COLORS[value] ?? "default"}>{value}</Tag>
              ),
            },
            {
              title: t("family.approvals.error", "错误"),
              dataIndex: "error",
              render: (value: string | null) =>
                value ? <Text type="danger">{value}</Text> : "-",
            },
            {
              title: t("family.approvals.detail", "详情"),
              width: 90,
              render: (_value, row) => (
                <Button size="small" onClick={() => setDetail(row)}>
                  {t("family.approvals.view", "查看")}
                </Button>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={detail !== null}
        title={t("family.approvals.detailTitle", "事务详情")}
        footer={null}
        onCancel={() => setDetail(null)}
        width={720}
      >
        {detail ? (
          <Descriptions
            size="small"
            column={1}
            bordered
            items={[
              { key: "id", label: t("family.approvals.id", "ID"), children: detail.id },
              {
                key: "action",
                label: t("family.approvals.action", "动作"),
                children: detail.action,
              },
              {
                key: "status",
                label: t("family.approvals.status", "状态"),
                children: (
                  <Tag color={STATUS_COLORS[detail.status] ?? "default"}>
                    {detail.status}
                  </Tag>
                ),
              },
              {
                key: "preview",
                label: t("family.approvals.preview", "预览"),
                children: <pre>{_pretty(detail.preview_json ?? detail.payload_json)}</pre>,
              },
              {
                key: "result",
                label: t("family.approvals.result", "执行结果"),
                children: <pre>{_pretty(detail.result_json)}</pre>,
              },
              {
                key: "verification",
                label: t("family.approvals.verification", "校验"),
                children: <pre>{_pretty(detail.verification_json)}</pre>,
              },
              {
                key: "error",
                label: t("family.approvals.error", "错误"),
                children: detail.error ?? "-",
              },
              {
                key: "retry",
                label: t("family.approvals.retryable", "可重试"),
                children: detail.status === "FAILED" ||
                  detail.status === "FAILED_REQUIRES_REVIEW" ? "是" : "否",
              },
            ]}
          />
        ) : null}
      </Modal>
      </>
    </FamilyPageShell>
  );
}

function _pretty(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") {
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return value;
    }
  }
  return JSON.stringify(value, null, 2);
}

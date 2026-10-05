import { useCallback, useEffect, useState } from "react";
import { App, Button, Card, Empty, List, Space, Tag, Typography } from "antd";
import { Check, RefreshCw, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  homeMindFamilyApi,
  type FamilyApproval,
  type FamilyAudit,
  type FamilyTransaction,
} from "../../api/modules/homeMindFamily";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import styles from "./index.module.less";

interface GovernancePanelProps {
  familyId: string;
}

const statusColor = (status: string) => {
  if (["APPROVED", "COMPLETED", "SUCCESS"].includes(status)) return "green";
  if (["REJECTED", "DENIED", "FAILED"].includes(status)) return "red";
  return "gold";
};

export default function GovernancePanel({ familyId }: GovernancePanelProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const timezone = useServerTimezone();
  const [approvals, setApprovals] = useState<FamilyApproval[]>([]);
  const [transactions, setTransactions] = useState<
    Record<string, FamilyTransaction>
  >({});
  const [audit, setAudit] = useState<FamilyAudit[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [pending, approved, rejected, nextAudit] = await Promise.all([
        homeMindFamilyApi.listApprovals(familyId, "PENDING"),
        homeMindFamilyApi.listApprovals(familyId, "APPROVED"),
        homeMindFamilyApi.listApprovals(familyId, "REJECTED"),
        homeMindFamilyApi.listAudit(familyId),
      ]);
      const rows = [...pending, ...approved, ...rejected].sort(
        (a, b) => b.created_at - a.created_at,
      );
      setApprovals(rows);
      setAudit(nextAudit);
      const details = await Promise.all(
        rows.map((row) =>
          homeMindFamilyApi.getTransaction(familyId, row.transaction_id),
        ),
      );
      setTransactions(Object.fromEntries(details.map((row) => [row.id, row])));
    } catch (error) {
      message.error(error instanceof Error ? error.message : String(error));
    } finally {
      setLoading(false);
    }
  }, [familyId, message]);

  useEffect(() => void load(), [load]);

  const decide = async (
    approval: FamilyApproval,
    decision: "approve" | "reject",
  ) => {
    await homeMindFamilyApi.decideApproval(familyId, approval.id, decision);
    message.success(
      decision === "approve"
        ? t("family.approved", "操作已批准")
        : t("family.rejected", "操作已拒绝"),
    );
    await load();
  };

  return (
    <div className={styles.twoColumnGrid}>
      <Card
        title={t("family.approvals", "操作审批")}
        className={styles.panelCard}
        extra={
          <Button
            type="text"
            loading={loading}
            icon={<RefreshCw size={16} />}
            onClick={load}
          />
        }
      >
        <List
          dataSource={approvals}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(approval) => {
            const transaction = transactions[approval.transaction_id];
            return (
              <List.Item
                actions={
                  approval.status === "PENDING"
                    ? [
                        <Button
                          key="approve"
                          type="text"
                          icon={<Check size={16} />}
                          onClick={() => decide(approval, "approve")}
                        >
                          {t("family.approve", "批准")}
                        </Button>,
                        <Button
                          key="reject"
                          type="text"
                          danger
                          icon={<X size={16} />}
                          onClick={() => decide(approval, "reject")}
                        >
                          {t("family.reject", "拒绝")}
                        </Button>,
                      ]
                    : undefined
                }
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <span>
                        {transaction?.action ?? approval.transaction_id}
                      </span>
                      <Tag color={statusColor(approval.status)}>
                        {approval.status}
                      </Tag>
                    </Space>
                  }
                  description={
                    <Space direction="vertical" size={2}>
                      <Typography.Text type="secondary">
                        {formatServerDateTime(approval.created_at, timezone)}
                      </Typography.Text>
                      {transaction?.payload_json && (
                        <Typography.Text code ellipsis>
                          {transaction.payload_json}
                        </Typography.Text>
                      )}
                      {transaction?.error && (
                        <Typography.Text type="danger">
                          {transaction.error}
                        </Typography.Text>
                      )}
                    </Space>
                  }
                />
              </List.Item>
            );
          }}
        />
      </Card>

      <Card
        title={t("family.auditLog", "审计日志")}
        className={styles.panelCard}
      >
        <List
          dataSource={audit}
          locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
          renderItem={(entry) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space>
                    <span>{entry.action}</span>
                    <Tag color={statusColor(entry.result)}>{entry.result}</Tag>
                  </Space>
                }
                description={
                  <Space direction="vertical" size={2}>
                    <Typography.Text type="secondary">
                      {formatServerDateTime(entry.created_at, timezone)}
                    </Typography.Text>
                    {entry.target && (
                      <Typography.Text>{entry.target}</Typography.Text>
                    )}
                    {entry.approval && <Tag>{entry.approval}</Tag>}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  );
}

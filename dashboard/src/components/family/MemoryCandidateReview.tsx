/**
 * Memory candidate review (Stage 3).
 *
 * Extracted as a reusable component rather than living inside a page so
 * the `/family` overview panel and the standalone `/family-memory`
 * page can share one implementation — the spec's "不要复制现有 Panel
 * 代码" rule. It owns the four-view filter, the sensitive marker, and
 * the approve / reject / merge / archive / restore actions; the caller
 * only supplies the family id.
 */
import { useCallback, useMemo, useState } from "react";
import {
  App,
  Button,
  Card,
  Empty,
  Input,
  Modal,
  Progress,
  Segmented,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import { AlertTriangle } from "lucide-react";

import { homeMindFamilyApi } from "../../api/modules/homeMindFamily";
import {
  homeMindPagesApi,
  type MemoryCandidate,
} from "../../api/modules/homeMindPages";
import { useActiveFamily } from "../../hooks/useActiveFamily";
import { QueryError, useFamilyQuery } from "./FamilyPageShell";

const { Text } = Typography;

const SENSITIVE_TYPES = new Set(["IDENTITY", "FINANCIAL", "HEALTH", "MINOR"]);
const SENSITIVE_VISIBILITY = new Set(["SENSITIVE", "RESTRICTED"]);

/** Identity / medical / financial / minor data never auto-approves. */
export function isSensitiveCandidate(candidate: MemoryCandidate): boolean {
  return (
    SENSITIVE_TYPES.has(candidate.memory_type.toUpperCase()) ||
    SENSITIVE_VISIBILITY.has(candidate.visibility.toUpperCase())
  );
}

export interface MemoryCandidateReviewProps {
  /** Show the view filter in the card header. Off when the host owns it. */
  showViewFilter?: boolean;
}

export default function MemoryCandidateReview({
  showViewFilter = true,
}: MemoryCandidateReviewProps) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const { familyId } = useActiveFamily();
  const [view, setView] = useState("PENDING");
  const [mergeTarget, setMergeTarget] = useState<MemoryCandidate | null>(null);
  const [mergeMemoryId, setMergeMemoryId] = useState("");

  const fetcher = useCallback(
    (id: string) => homeMindPagesApi.listMemoryCandidates(id),
    [],
  );
  const memoriesFetcher = useCallback(
    (id: string) => homeMindFamilyApi.listMemories(id),
    [],
  );
  const { data, loading, error, reload } = useFamilyQuery(familyId, fetcher);
  const memories = useFamilyQuery(familyId, memoriesFetcher);

  const visible = useMemo(() => {
    const rows = data ?? [];
    if (view === "PENDING") return rows.filter((row) => row.status === "PENDING");
    if (view === "CONFLICT")
      return rows.filter((row) => row.status === "CONFLICTED");
    if (view === "ARCHIVED") return rows.filter((row) => row.status === "ARCHIVED");
    return rows.filter((row) => row.status === "APPROVED");
  }, [data, view]);

  const act = async (label: string, run: (id: string) => Promise<unknown>) => {
    if (familyId === null) return;
    try {
      await run(familyId);
      message.success(label);
      reload();
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    }
  };

  const filter = (
    <Segmented
      value={view}
      onChange={(value) => setView(String(value))}
      options={[
        { label: t("family.memory.pending", "待确认"), value: "PENDING" },
        { label: t("family.memory.confirmed", "已确认"), value: "APPROVED" },
        { label: t("family.memory.conflicted", "冲突"), value: "CONFLICT" },
        { label: t("family.memory.archived", "已归档"), value: "ARCHIVED" },
      ]}
    />
  );

  return (
    <>
      <Card
        size="small"
        title={t("family.memory.title", "家庭记忆")}
        extra={showViewFilter ? filter : null}
      >
        <QueryError error={error} />
        <Table<MemoryCandidate>
          size="small"
          rowKey="id"
          loading={loading}
          dataSource={visible}
          locale={{
            emptyText: <Empty description={t("family.memory.empty", "暂无记忆")} />,
          }}
          columns={[
            {
              title: t("family.memory.content", "内容"),
              dataIndex: "content",
              render: (value: string, row) => (
                <Space>
                  {isSensitiveCandidate(row) ? (
                    <Tooltip
                      title={t(
                        "family.memory.sensitiveHint",
                        "敏感内容,需人工确认",
                      )}
                    >
                      <AlertTriangle size={14} color="#d4380d" />
                    </Tooltip>
                  ) : null}
                  <Text>{value}</Text>
                </Space>
              ),
            },
            {
              title: t("family.memory.type", "类型"),
              dataIndex: "memory_type",
              width: 140,
              render: (value: string, row) => (
                <Space direction="vertical" size={0}>
                  <Tag>{value}</Tag>
                  {row.visibility === "SENSITIVE" ? (
                    <Tag color="volcano">SENSITIVE</Tag>
                  ) : null}
                </Space>
              ),
            },
            {
              title: t("family.memory.importance", "重要度/置信度"),
              width: 180,
              render: (_value, row) => (
                <Space direction="vertical" size={0}>
                  <Progress
                    percent={Math.round(row.importance * 100)}
                    size="small"
                    format={(value) => `重要 ${value}`}
                  />
                  <Progress
                    percent={Math.round(row.confidence * 100)}
                    size="small"
                    status="exception"
                    format={(value) => `置信 ${value}`}
                  />
                </Space>
              ),
            },
            {
              title: t("family.memory.status", "状态"),
              dataIndex: "status",
              width: 110,
              render: (value: string) => <Tag>{value}</Tag>,
            },
            {
              title: t("family.memory.actions", "操作"),
              width: 240,
              render: (_value, row) => {
                if (view !== "PENDING") {
                  if (row.status === "APPROVED") {
                    return (
                      <Button
                        size="small"
                        onClick={() =>
                          act("已归档", (id) =>
                            homeMindPagesApi.archiveMemory(id, row.id),
                          )
                        }
                      >
                        {t("family.memory.archive", "归档")}
                      </Button>
                    );
                  }
                  if (row.status === "ARCHIVED") {
                    return (
                      <Button
                        size="small"
                        onClick={() =>
                          act("已恢复", (id) =>
                            homeMindPagesApi.restoreMemory(id, row.id),
                          )
                        }
                      >
                        {t("family.memory.restore", "恢复")}
                      </Button>
                    );
                  }
                  return null;
                }
                return (
                  <Space size={4}>
                    <Button
                      size="small"
                      type="primary"
                      onClick={() =>
                        act("已批准", (id) =>
                          homeMindPagesApi.approveMemoryCandidate(id, row.id),
                        )
                      }
                    >
                      {t("family.memory.approve", "批准")}
                    </Button>
                    <Button
                      size="small"
                      danger
                      onClick={() =>
                        act("已拒绝", (id) =>
                          homeMindPagesApi.rejectMemoryCandidate(id, row.id),
                        )
                      }
                    >
                      {t("family.memory.reject", "拒绝")}
                    </Button>
                    <Button size="small" onClick={() => setMergeTarget(row)}>
                      {t("family.memory.merge", "合并")}
                    </Button>
                  </Space>
                );
              },
            },
          ]}
        />
      </Card>

      <Modal
        open={mergeTarget !== null}
        title={t("family.memory.mergeTitle", "合并到已有记忆")}
        onCancel={() => {
          setMergeTarget(null);
          setMergeMemoryId("");
        }}
        onOk={() => {
          if (mergeTarget === null || !mergeMemoryId.trim()) return;
          act("已合并", (id) =>
            homeMindPagesApi.mergeMemoryCandidate(
              id,
              mergeTarget.id,
              mergeMemoryId.trim(),
            ),
          );
          setMergeTarget(null);
          setMergeMemoryId("");
        }}
      >
        <Input
          value={mergeMemoryId}
          placeholder={t("family.memory.targetId", "目标记忆 ID")}
          onChange={(event) => setMergeMemoryId(event.target.value)}
        />
        <div style={{ marginTop: 8 }}>
          <Text type="secondary">
            {t("family.memory.mergeHint", "已有记忆:")}{" "}
            {(memories.data ?? []).map((memory) => memory.id).join(", ") || "-"}
          </Text>
        </div>
      </Modal>
    </>
  );
}

import {
  Alert,
  Badge,
  Button,
  Card,
  Checkbox,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Input,
  List,
  Select,
  Space,
  Spin,
  Statistic,
  Tag,
} from "antd";
import { RefreshCw, RotateCcw } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  auditApi,
  type AuditKind,
  type ThreadAuditItem,
  type ThreadAuditPage,
} from "../../../api/modules/audit";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";

interface Props {
  threadId: string | null;
  open: boolean;
  onClose: () => void;
  standalone?: boolean;
}

const ALL_KINDS: AuditKind[] = [
  "message",
  "model_call",
  "tool_call",
  "workspace_change",
];

function payloadText(item: ThreadAuditItem): string {
  const payload = item.payload;
  if (item.kind === "workspace_change") {
    return String(payload.path ?? "");
  }
  if (
    item.kind === "trajectory" ||
    item.kind === "model_call" ||
    item.kind === "tool_call"
  ) {
    return String(payload.summary ?? payload.kind ?? "");
  }
  const content = payload.content;
  return typeof content === "string"
    ? content
    : JSON.stringify(content ?? payload);
}

function contentNotice(item: ThreadAuditItem): string | null {
  if (item.kind !== "workspace_change") return null;
  const metadata = item.payload.metadata as Record<string, unknown> | undefined;
  if (metadata?.content_redacted) return "redacted";
  if (metadata?.content_truncated) return "truncated";
  if (item.payload.diff_kind === "binary") return "binary";
  return null;
}

export default function ConversationAuditDrawer({
  threadId,
  open,
  onClose,
  standalone = false,
}: Props) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const timeZone = useServerTimezone();
  const [page, setPage] = useState<ThreadAuditPage>();
  const [items, setItems] = useState<ThreadAuditItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(false);
  const [kinds, setKinds] = useState<AuditKind[]>(ALL_KINDS);
  const [turnId, setTurnId] = useState("");
  const [toolCallId, setToolCallId] = useState("");
  const [status, setStatus] = useState<string>();
  const [sinceTs, setSinceTs] = useState<number>();
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [filterResetKey, setFilterResetKey] = useState(0);
  const [pendingNew, setPendingNew] = useState<ThreadAuditPage>();
  const [selected, setSelected] = useState<ThreadAuditItem>();
  const newestIdRef = useRef<string>();

  const filters = useMemo(
    () => ({
      kinds,
      turnId: turnId || undefined,
      toolCallId: toolCallId || undefined,
      status,
      sinceTs,
      limit: 50,
    }),
    [kinds, sinceTs, status, toolCallId, turnId],
  );

  const load = useCallback(async () => {
    if (!threadId) return;
    setLoading(true);
    setError(false);
    try {
      const next = await auditApi.getThreadAudit(threadId, filters);
      setPage(next);
      setItems(next.timeline);
      newestIdRef.current = next.timeline.at(-1)?.id;
      setPendingNew(undefined);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [filters, threadId]);

  useEffect(() => {
    if (open) void load();
  }, [load, open]);

  useEffect(() => {
    if (!open || !autoRefresh || !threadId) return;
    const timer = window.setInterval(() => {
      void auditApi
        .getThreadAudit(threadId, filters)
        .then((next) => {
          const newest = next.timeline.at(-1)?.id;
          if (newest && newest !== newestIdRef.current) setPendingNew(next);
        })
        .catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, filters, open, threadId]);

  const loadMore = async () => {
    if (!threadId || !page?.next_before_ts) return;
    setLoadingMore(true);
    try {
      const older = await auditApi.getThreadAudit(threadId, {
        ...filters,
        beforeTs: page.next_before_ts,
      });
      setItems((current) => [...older.timeline, ...current]);
      setPage((current) =>
        current
          ? {
              ...current,
              has_more: older.has_more,
              next_before_ts: older.next_before_ts,
            }
          : older,
      );
    } finally {
      setLoadingMore(false);
    }
  };

  const acceptNew = () => {
    if (!pendingNew) return;
    setPage(pendingNew);
    setItems(pendingNew.timeline);
    newestIdRef.current = pendingNew.timeline.at(-1)?.id;
    setPendingNew(undefined);
  };

  const resetFilters = () => {
    setKinds(ALL_KINDS);
    setTurnId("");
    setToolCallId("");
    setStatus(undefined);
    setSinceTs(undefined);
    setFilterResetKey((value) => value + 1);
  };

  return (
    <Drawer
      title={t("chat.audit.title")}
      open={open}
      onClose={onClose}
      width={standalone || isMobile ? "100%" : "80vw"}
      getContainer={standalone ? false : undefined}
      rootStyle={standalone ? { position: "absolute" } : undefined}
      destroyOnHidden
    >
      {!threadId ? (
        <Empty description={t("chat.audit.selectThread")} />
      ) : (
        <Space direction="vertical" size="middle" style={{ width: "100%" }}>
          {page && (
            <Space wrap>
              <Card size="small">
                <Statistic
                  title={t("chat.audit.model")}
                  value={page.summary.model || "-"}
                />
              </Card>
              <Card size="small">
                <Statistic
                  title={t("chat.audit.totalTokens")}
                  value={page.summary.total_tokens}
                />
              </Card>
              <Card size="small">
                <Statistic
                  title={t("chat.audit.inputOutput")}
                  value={`${page.summary.input_tokens} / ${page.summary.output_tokens}`}
                />
              </Card>
              <Card size="small">
                <Statistic
                  title={t("chat.audit.calls")}
                  value={page.summary.model_calls}
                />
              </Card>
              <Card size="small">
                <Statistic
                  title={t("chat.audit.status")}
                  value={
                    page.summary.has_errors
                      ? t("chat.audit.error")
                      : page.summary.status
                  }
                />
              </Card>
            </Space>
          )}
          <Space wrap>
            <Checkbox.Group
              options={ALL_KINDS.map((value) => ({
                label: t(`chat.audit.kind.${value}`),
                value,
              }))}
              value={kinds}
              onChange={(values) => {
                if (values.length > 0) setKinds(values as AuditKind[]);
              }}
            />
            <DatePicker
              key={filterResetKey}
              showTime
              onChange={(value) => setSinceTs(value?.unix())}
              placeholder={t("chat.audit.since")}
            />
            <Input
              allowClear
              value={turnId}
              onChange={(event) => setTurnId(event.target.value)}
              placeholder="turn_id"
            />
            <Input
              allowClear
              value={toolCallId}
              onChange={(event) => setToolCallId(event.target.value)}
              placeholder="tool_call_id"
            />
            <Select
              allowClear
              value={status}
              onChange={setStatus}
              placeholder={t("chat.audit.status")}
              options={["complete", "succeeded", "failed", "error"].map(
                (value) => ({ value }),
              )}
            />
            <Checkbox
              checked={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.checked)}
            >
              {t("chat.audit.autoRefresh")}
            </Checkbox>
            <Button icon={<RefreshCw size={14} />} onClick={() => void load()}>
              {t("common.refresh")}
            </Button>
            <Button icon={<RotateCcw size={14} />} onClick={resetFilters}>
              {t("chat.audit.resetFilters")}
            </Button>
            <Tag>{t("chat.audit.resultCount", { count: items.length })}</Tag>
          </Space>
          {pendingNew && (
            <Alert
              type="info"
              showIcon
              message={t("chat.audit.newEvents")}
              action={
                <Button size="small" onClick={acceptNew}>
                  {t("chat.audit.showNew")}
                </Button>
              }
            />
          )}
          {error ? (
            <Alert
              type="error"
              message={t("chat.audit.loadError")}
              action={
                <Button onClick={() => void load()}>{t("chat.retry")}</Button>
              }
            />
          ) : loading ? (
            <Spin />
          ) : (
            <List
              dataSource={items}
              locale={{ emptyText: t("chat.audit.empty") }}
              renderItem={(item) => {
                const notice = contentNotice(item);
                const diff =
                  item.kind === "workspace_change" &&
                  typeof item.payload.diff_text === "string"
                    ? item.payload.diff_text
                    : null;
                return (
                  <List.Item
                    onClick={() => setSelected(item)}
                    style={{ cursor: "pointer" }}
                  >
                    <List.Item.Meta
                      title={
                        <Space wrap>
                          <Tag>{t(`chat.audit.kind.${item.kind}`)}</Tag>
                          {item.status && (
                            <Badge
                              status={
                                item.status === "failed" ||
                                item.status === "error"
                                  ? "error"
                                  : "success"
                              }
                              text={item.status}
                            />
                          )}
                          <span>{payloadText(item)}</span>
                        </Space>
                      }
                      description={
                        <div>
                          <Space wrap>
                            <span>
                              {formatServerDateTime(item.ts, timeZone)}
                            </span>
                            {item.turn_id && <code>{item.turn_id}</code>}
                            {item.tool_call_id && (
                              <code>{item.tool_call_id}</code>
                            )}
                            {notice && (
                              <Tag color="warning">
                                {t(`chat.audit.notice.${notice}`)}
                              </Tag>
                            )}
                          </Space>
                          {diff && (
                            <pre
                              style={{
                                maxHeight: 240,
                                overflow: "auto",
                                whiteSpace: "pre-wrap",
                              }}
                            >
                              {diff}
                            </pre>
                          )}
                        </div>
                      }
                    />
                  </List.Item>
                );
              }}
            />
          )}
          {page?.has_more && (
            <Button block loading={loadingMore} onClick={() => void loadMore()}>
              {t("chat.audit.loadMore")}
            </Button>
          )}
        </Space>
      )}
      <Drawer
        title={t("chat.audit.detail")}
        open={selected != null}
        onClose={() => setSelected(undefined)}
        width={isMobile ? "100%" : 680}
      >
        {selected && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label={t("chat.audit.eventId")}>
                <code>{selected.id}</code>
              </Descriptions.Item>
              <Descriptions.Item label={t("chat.audit.eventType")}>
                {t(`chat.audit.kind.${selected.kind}`)}
              </Descriptions.Item>
              <Descriptions.Item label={t("chat.audit.status")}>
                {selected.status || "-"}
              </Descriptions.Item>
              <Descriptions.Item label="turn_id">
                <code>{selected.turn_id || "-"}</code>
              </Descriptions.Item>
              <Descriptions.Item label="tool_call_id">
                <code>{selected.tool_call_id || "-"}</code>
              </Descriptions.Item>
              <Descriptions.Item label={t("chat.audit.time")}>
                {formatServerDateTime(selected.ts, timeZone)}
              </Descriptions.Item>
            </Descriptions>
            <pre
              style={{
                maxHeight: "65vh",
                overflow: "auto",
                whiteSpace: "pre-wrap",
              }}
            >
              {JSON.stringify(selected.payload, null, 2)}
            </pre>
          </Space>
        )}
      </Drawer>
    </Drawer>
  );
}

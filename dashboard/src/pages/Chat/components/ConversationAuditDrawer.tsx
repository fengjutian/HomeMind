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
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import {
  auditApi,
  type AuditKind,
  type ThreadAuditItem,
  type ThreadAuditPage,
} from "../../../api/modules/audit";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { useTheme } from "../../../context/ThemeContext";
import { formatServerDateTime } from "../../../utils/formatMessageTime";

const MonacoEditor = lazy(() => import("@monaco-editor/react"));

interface Props {
  threadId: string | null;
}

const ALL_KINDS: AuditKind[] = [
  "message",
  "model_call",
  "tool_call",
  "trajectory",
  "workspace_change",
];

const PREVIEW_LIMIT = 320;

export function auditItemPreview(item: ThreadAuditItem): string {
  const payload = item.payload;
  let text: string;
  if (item.kind === "workspace_change") {
    text = String(payload.path ?? "");
  } else if (
    item.kind === "trajectory" ||
    item.kind === "model_call" ||
    item.kind === "tool_call"
  ) {
    text = String(payload.summary ?? payload.kind ?? "");
  } else {
    const content = payload.content;
    text =
      typeof content === "string"
        ? content
        : JSON.stringify(content ?? payload);
  }
  return text.length > PREVIEW_LIMIT
    ? `${text.slice(0, PREVIEW_LIMIT).trimEnd()}…`
    : text;
}

export function mergeAuditItems(
  current: ThreadAuditItem[],
  incoming: ThreadAuditItem[],
): ThreadAuditItem[] {
  const byId = new Map(current.map((item) => [item.id, item]));
  for (const item of incoming) byId.set(item.id, item);
  return [...byId.values()].sort(
    (left, right) => left.ts - right.ts || left.id.localeCompare(right.id),
  );
}

function contentNotice(item: ThreadAuditItem): string | null {
  if (item.kind !== "workspace_change") return null;
  const metadata = item.payload.metadata as Record<string, unknown> | undefined;
  if (metadata?.content_redacted) return "redacted";
  if (metadata?.content_truncated) return "truncated";
  if (item.payload.diff_kind === "binary") return "binary";
  return null;
}

export default function ConversationAuditDrawer({ threadId }: Props) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const { isDark } = useTheme();
  const timeZone = useServerTimezone();
  const [page, setPage] = useState<ThreadAuditPage>();
  const [items, setItems] = useState<ThreadAuditItem[]>([]);
  const [loading, setLoading] = useState(false);
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
  const [pageIndex, setPageIndex] = useState(0);
  const [pageCursors, setPageCursors] = useState<(number | undefined)[]>([
    undefined,
  ]);
  const newestIdRef = useRef<string>();
  const loadRequestRef = useRef(0);

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
    const requestId = ++loadRequestRef.current;
    if (!threadId) {
      setPage(undefined);
      setItems([]);
      return;
    }
    setLoading(true);
    setError(false);
    try {
      const next = await auditApi.getThreadAudit(threadId, {
        ...filters,
        beforeTs: pageCursors[pageIndex],
      });
      if (requestId !== loadRequestRef.current) return;
      setPage(next);
      setItems(next.timeline);
      if (pageIndex === 0) newestIdRef.current = next.timeline.at(-1)?.id;
      setPendingNew(undefined);
    } catch {
      if (requestId === loadRequestRef.current) setError(true);
    } finally {
      if (requestId === loadRequestRef.current) setLoading(false);
    }
  }, [filters, pageCursors, pageIndex, threadId]);

  useEffect(() => {
    setSelected(undefined);
    setPendingNew(undefined);
    newestIdRef.current = undefined;
    setPageIndex(0);
    setPageCursors([undefined]);
  }, [threadId]);

  useEffect(() => {
    setPageIndex(0);
    setPageCursors([undefined]);
  }, [kinds, sinceTs, status, toolCallId, turnId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh || !threadId) return;
    let active = true;
    const timer = window.setInterval(() => {
      void auditApi
        .getThreadAudit(threadId, filters)
        .then((next) => {
          if (!active) return;
          const newest = next.timeline.at(-1)?.id;
          if (newest && newest !== newestIdRef.current) setPendingNew(next);
        })
        .catch(() => undefined);
    }, 5000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [autoRefresh, filters, threadId]);

  const showNextPage = () => {
    if (!page?.next_before_ts) return;
    const nextIndex = pageIndex + 1;
    setPageCursors((current) => {
      const next = current.slice(0, nextIndex);
      next[nextIndex] = page.next_before_ts ?? undefined;
      return next;
    });
    setPageIndex(nextIndex);
  };

  const acceptNew = () => {
    if (!pendingNew) return;
    setItems(pendingNew.timeline);
    setPage(pendingNew);
    setPageIndex(0);
    setPageCursors([undefined]);
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
    <>
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
              options={[
                "pending",
                "running",
                "complete",
                "succeeded",
                "failed",
                "error",
                "cancelled",
                "timeout",
              ].map((value) => ({ value }))}
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
            <Tag>
              {t("chat.audit.pageResultCount", {
                page: pageIndex + 1,
                count: items.length,
              })}
            </Tag>
            <Space.Compact>
              <Button
                size="small"
                disabled={loading || pageIndex === 0}
                onClick={() => setPageIndex((current) => current - 1)}
              >
                {t("chat.audit.previousPage")}
              </Button>
              <Button
                size="small"
                disabled={loading || !page?.has_more}
                onClick={showNextPage}
              >
                {t("chat.audit.nextPage")}
              </Button>
            </Space.Compact>
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
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        setSelected(item);
                      }
                    }}
                    role="button"
                    tabIndex={0}
                    aria-label={`${t(
                      `chat.audit.kind.${item.kind}`,
                    )} ${auditItemPreview(item)}`}
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
                          <span>{auditItemPreview(item)}</span>
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
            <Suspense fallback={<Spin />}>
              <MonacoEditor
                height="65vh"
                language="json"
                theme={isDark ? "vs-dark" : "light"}
                value={JSON.stringify(selected.payload, null, 2)}
                options={{
                  readOnly: true,
                  domReadOnly: true,
                  automaticLayout: true,
                  minimap: { enabled: false },
                  lineNumbers: "on",
                  folding: true,
                  wordWrap: "on",
                  scrollBeyondLastLine: false,
                  formatOnPaste: true,
                  contextmenu: true,
                  fontSize: 13,
                  tabSize: 2,
                }}
              />
            </Suspense>
          </Space>
        )}
      </Drawer>
    </>
  );
}

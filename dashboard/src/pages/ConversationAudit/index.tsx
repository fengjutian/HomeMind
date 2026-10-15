import { Select, Space } from "antd";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useParams, useSearchParams } from "react-router-dom";
import {
  octopThreadsApi,
  type OctopThread,
} from "../../api/modules/octopThreads";
import { useAgent } from "../../context/AgentContext";
import PageShell from "../../layouts/PageShell";
import ConversationAuditDrawer from "../Chat/components/ConversationAuditDrawer";

export default function ConversationAuditPage() {
  const { t } = useTranslation();
  const { agents, activeAgentId } = useAgent();
  const { agentId: routeAgentId, threadId: routeThreadId } = useParams<{
    agentId: string;
    threadId: string;
  }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const [threads, setThreads] = useState<OctopThread[]>([]);
  const [loadingThreads, setLoadingThreads] = useState(false);
  const agentId =
    routeAgentId ||
    searchParams.get("agent") ||
    activeAgentId ||
    agents[0]?.agent_id ||
    "";
  const threadId = routeThreadId || searchParams.get("thread") || "";

  useEffect(() => {
    let cancelled = false;
    if (!agentId) {
      setThreads([]);
      return () => undefined;
    }
    setLoadingThreads(true);
    void octopThreadsApi
      .list(agentId, 200)
      .then((rows) => {
        if (!cancelled) setThreads(rows);
      })
      .finally(() => {
        if (!cancelled) setLoadingThreads(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  const agentOptions = useMemo(
    () => agents.map((agent) => ({ value: agent.agent_id, label: agent.name })),
    [agents],
  );
  const threadOptions = useMemo(
    () =>
      threads.map((thread) => ({
        value: thread.thread_id,
        label: thread.title || thread.thread_id,
      })),
    [threads],
  );
  const selectors = (
    <Space wrap>
      <Select
        value={agentId || undefined}
        options={agentOptions}
        onChange={(value) => setSearchParams({ agent: value })}
        placeholder={t("chat.audit.selectAgent")}
        style={{ minWidth: 180 }}
      />
      <Select
        value={threadId || undefined}
        options={threadOptions}
        loading={loadingThreads}
        disabled={!agentId}
        onChange={(value) => setSearchParams({ agent: agentId, thread: value })}
        placeholder={t("chat.audit.selectConversation")}
        showSearch
        optionFilterProp="label"
        style={{ minWidth: 240 }}
      />
    </Space>
  );

  return (
    <PageShell
      title={t("chat.audit.title")}
      actions={routeAgentId ? undefined : selectors}
      fill
    >
      <ConversationAuditDrawer threadId={threadId || null} />
    </PageShell>
  );
}

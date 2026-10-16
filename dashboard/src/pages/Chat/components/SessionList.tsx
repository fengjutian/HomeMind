import { memo, useCallback, useMemo, useState, useRef, useEffect } from "react";
import type { DragEvent, ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Dropdown } from "antd";
import type { MenuProps } from "antd";
import {
  Pencil,
  MoreHorizontal,
  Trash2,
  Pin,
  PinOff,
  MessageSquarePlus,
  Search,
  GitFork,
  Eye,
  EyeOff,
  GripVertical,
} from "lucide-react";
import type { Session } from "../hooks/useSessions";
import type { OctopAgent } from "../../../context/AgentContext";
import { isAgentChatReady } from "../../../utils/agentError";
import { confirmDeleteConversation } from "../utils/deleteConversation";
import { isImeComposing } from "../../../utils/ime";
import { ExpertIcon } from "../../Experts/components/iconForName";
import { useHiddenSharedExperts } from "../hooks/useHiddenSharedExperts";
import SessionChannelIcon from "./SessionChannelIcon";
import SessionWorkStatusIcon from "./SessionWorkStatusIcon";
import SharedExpertHint from "./SharedExpertHint";
import RemoteExpertHint from "./RemoteExpertHint";
import TeamChatBadge from "./TeamChatBadge";
import {
  useExpertWorkStatuses,
  useSessionWorkIds,
  type ExpertWorkStatus,
} from "../hooks/useSessionWorkIds";
import {
  resolveSessionWorkStatus,
  type SessionWorkStatus,
} from "../utils/sessionWorkStatus";
import {
  mergeExpertOrder,
  moveExpert,
  readExpertOrder,
  writeExpertOrder,
} from "../utils/expertOrder";
import styles from "../index.module.less";

function AgentUnreadBadge({ count }: { count: number }) {
  const { t } = useTranslation();
  if (!count || count <= 0) return null;
  return (
    <span
      className={styles.agentUnreadBadge}
      aria-label={t("chat.unreadMessages", "未读消息")}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

function ExpertTaskIndicator({ status }: { status?: ExpertWorkStatus }) {
  const { t } = useTranslation();
  if (!status) return null;
  const label =
    status === "waiting"
      ? t("chat.expertTaskWaiting", "任务需要确认")
      : status === "interrupted"
      ? t("chat.expertTaskInterrupted", "任务已中断")
      : t("chat.expertTaskWorking", "任务正常运行");
  return (
    <span
      className={`${styles.agentTaskPulse} ${
        status === "waiting"
          ? styles.agentTaskPulseWaiting
          : status === "interrupted"
          ? styles.agentTaskPulseInterrupted
          : ""
      }`}
      aria-label={label}
      title={label}
    />
  );
}

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  workStatus?: SessionWorkStatus;
  onSelect: (id: string) => void;
  onDelete: (id: string, compact: boolean) => void | Promise<boolean | void>;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  forkDisabled?: boolean;
  forkDisabledHint?: string;
}

const SessionItem = memo(function SessionItem({
  session,
  isActive,
  workStatus = "idle",
  onSelect,
  onDelete,
  onRename,
  onPin,
  onFork,
  forkDisabled,
  forkDisabledHint,
}: SessionItemProps) {
  const { t } = useTranslation();
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(session.name);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isEditing) setEditValue(session.name);
  }, [session.name, isEditing]);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const commitEdit = useCallback(() => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== session.name) {
      onRename(session.id, trimmed);
    } else {
      setEditValue(session.name);
    }
    setIsEditing(false);
  }, [editValue, session.name, session.id, onRename]);

  const itemForkDisabled = Boolean(forkDisabled) || !session.hasActivity;
  const itemForkHint = !session.hasActivity
    ? t("chat.forkNoAssistant")
    : forkDisabledHint;

  const menuItems: MenuProps["items"] = [
    {
      key: "pin",
      label: session.pinned
        ? t("chat.unpin", "取消置顶")
        : t("chat.pin", "置顶"),
      icon: session.pinned ? <PinOff size={14} /> : <Pin size={14} />,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        onPin(session.id, !session.pinned);
      },
    },
    {
      key: "fork",
      label: t("chat.fork", "分叉"),
      icon: <GitFork size={14} />,
      disabled: itemForkDisabled,
      title: itemForkDisabled && itemForkHint ? itemForkHint : undefined,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        onFork(session.id);
      },
    },
    {
      key: "rename",
      label: t("common.rename"),
      icon: <Pencil size={14} />,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        setIsEditing(true);
      },
    },
    {
      key: "delete",
      label: t("common.delete", "Delete"),
      icon: <Trash2 size={14} />,
      danger: true,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        confirmDeleteConversation(t, (compact) => {
          void onDelete(session.id, compact);
        });
      },
    },
  ];

  return (
    <div
      className={`${styles.sessionRow} ${
        isActive ? styles.sessionRowActive : ""
      } ${session.pinned ? styles.sessionRowPinned : ""}`}
      onClick={() => {
        if (!isEditing) onSelect(session.id);
      }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" && !isEditing) onSelect(session.id);
      }}
    >
      <span className={styles.sessionRowLead}>
        {workStatus !== "idle" ? (
          <SessionWorkStatusIcon status={workStatus} />
        ) : (
          <SessionChannelIcon
            channelType={session.channelType}
            size={12}
            className={styles.sessionRowIcon}
          />
        )}
      </span>
      {isEditing ? (
        <input
          ref={inputRef}
          className={styles.sessionNameInput}
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            if (isImeComposing(e)) return;
            if (e.key === "Enter") commitEdit();
            if (e.key === "Escape") {
              setEditValue(session.name);
              setIsEditing(false);
            }
          }}
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <>
          <span className={styles.sessionRowTitle}>{session.name}</span>
          {session.pinned ? (
            <span
              className={styles.sessionRowPinIndicator}
              title={t("chat.unpin")}
            >
              <Pin size={12} strokeWidth={2} />
            </span>
          ) : null}
          <Dropdown
            menu={{ items: menuItems }}
            trigger={["click"]}
            placement="bottomRight"
          >
            <button
              type="button"
              className={styles.sessionRowMore}
              aria-label={t("common.more", "More")}
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal size={15} />
            </button>
          </Dropdown>
        </>
      )}
    </div>
  );
});

interface AgentCardProps {
  agent: OctopAgent;
  sessions: Session[];
  activeId: string | null;
  searchQuery: string;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onFetchAllSessions: () => void;
  onSelect: (sessionId: string, agentId: string) => void;
  onNewChat: (agentId: string) => void;
  onDelete: (id: string, compact: boolean) => void | Promise<boolean | void>;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  activeForkDisabled?: boolean;
  activeForkDisabledHint?: string;
  onHide?: () => void;
  liveWorkingIds: ReadonlySet<string>;
  dragHandle?: ReactNode;
  workStatus?: ExpertWorkStatus;
}

function ActiveAgentCard({
  agent,
  sessions,
  activeId,
  searchQuery,
  hasMore,
  loadingMore,
  onLoadMore,
  onFetchAllSessions,
  onSelect,
  onNewChat,
  onDelete,
  onRename,
  onPin,
  onFork,
  activeForkDisabled,
  activeForkDisabledHint,
  onHide,
  liveWorkingIds,
  dragHandle,
  workStatus,
}: AgentCardProps) {
  const { t } = useTranslation();
  const accent = agent.color || "#6366f1";

  const filteredSessions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => s.name.toLowerCase().includes(q));
  }, [sessions, searchQuery]);

  const fetchAllRequestedRef = useRef(false);
  useEffect(() => {
    const searching = Boolean(searchQuery.trim());
    if (!searching) {
      fetchAllRequestedRef.current = false;
      return;
    }
    if (fetchAllRequestedRef.current) return;
    fetchAllRequestedRef.current = true;
    onFetchAllSessions();
  }, [searchQuery, onFetchAllSessions]);

  const showExpandMore = hasMore && !searchQuery.trim();
  const sessionsEnabled = isAgentChatReady(agent.state);

  return (
    <div
      className={styles.agentCardActive}
      style={{
        background: `${accent}08`,
        borderColor: `${accent}18`,
      }}
    >
      <div className={styles.agentCardProfile}>
        <div
          className={styles.agentCardAvatar}
          style={{
            color: accent,
            background: `${accent}14`,
            boxShadow: `0 0 0 1px ${accent}22`,
          }}
        >
          <ExpertIcon
            iconUrl={agent.icon_url}
            iconName={agent.icon_name}
            size={agent.icon_url?.trim() ? 28 : 16}
          />
        </div>
        <div className={styles.agentCardInfo}>
          <div className={styles.agentCardNameRow}>
            {dragHandle}
            <div className={styles.agentNameCluster}>
              <div className={styles.agentCardName}>{agent.name}</div>
              <ExpertTaskIndicator status={workStatus} />
              <TeamChatBadge agent={agent} />
              <SharedExpertHint agent={agent} />
              <RemoteExpertHint agent={agent} />
            </div>
            <AgentUnreadBadge count={agent.unread_count ?? 0} />
            <button
              type="button"
              className={styles.agentNewChatBtn}
              aria-label={t("chatWelcome.newChat")}
              title={t("chatWelcome.newChat")}
              onClick={(e) => {
                e.stopPropagation();
                onNewChat(agent.agent_id);
              }}
            >
              <MessageSquarePlus size={14} strokeWidth={1.75} aria-hidden />
            </button>
            {onHide ? (
              <button
                type="button"
                className={styles.agentHideBtn}
                aria-label={t("chat.expertHide")}
                title={t("chat.expertHide")}
                onClick={(e) => {
                  e.stopPropagation();
                  onHide();
                }}
              >
                <EyeOff size={14} aria-hidden />
              </button>
            ) : null}
          </div>
          {agent.description ? (
            <div className={styles.agentCardDesc}>{agent.description}</div>
          ) : (
            <div className={styles.agentCardDescMuted}>
              {t("chat.agentNoDescription", "暂无描述")}
            </div>
          )}
        </div>
      </div>

      <div className={styles.agentCardSessions}>
        {!sessionsEnabled ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.agentNotRunningHint")}
          </div>
        ) : sessions.length === 0 ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.noSessionsYet", "直接发消息即可开始对话")}
          </div>
        ) : filteredSessions.length === 0 ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.noSearchResults", "没有匹配的会话")}
          </div>
        ) : (
          <>
            {filteredSessions.map((s) => (
              <SessionItem
                key={s.id}
                session={s}
                isActive={activeId === s.id}
                workStatus={resolveSessionWorkStatus(s, liveWorkingIds)}
                onSelect={(id) => onSelect(id, agent.agent_id)}
                onDelete={onDelete}
                onRename={onRename}
                onPin={onPin}
                onFork={onFork}
                forkDisabled={
                  activeId === s.id ? activeForkDisabled : undefined
                }
                forkDisabledHint={
                  activeId === s.id ? activeForkDisabledHint : undefined
                }
              />
            ))}
            {showExpandMore ? (
              <button
                type="button"
                className={styles.sessionLoadMore}
                onClick={onLoadMore}
                disabled={loadingMore}
              >
                {loadingMore
                  ? t("common.loading")
                  : t("chat.expandMore", "展开更多")}
              </button>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

interface AgentRowProps {
  agent: OctopAgent;
  onSelect: () => void;
  onNewChat?: () => void;
  onHide?: () => void;
  onUnhide?: () => void;
  dragHandle?: ReactNode;
  workStatus?: ExpertWorkStatus;
}

function InactiveAgentRow({
  agent,
  onSelect,
  onNewChat,
  onHide,
  onUnhide,
  dragHandle,
  workStatus,
}: AgentRowProps) {
  const { t } = useTranslation();
  const accent = agent.color || "#6366f1";

  return (
    <div className={styles.agentRowWrap}>
      <div
        className={styles.agentRow}
        onClick={onSelect}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onSelect();
          }
        }}
      >
        <div
          className={styles.agentRowAvatar}
          style={{ color: accent, background: `${accent}12` }}
        >
          <ExpertIcon
            iconUrl={agent.icon_url}
            iconName={agent.icon_name}
            size={agent.icon_url?.trim() ? 26 : 14}
          />
        </div>
        <div className={styles.agentRowInfo}>
          <div className={styles.agentRowNameRow}>
            {dragHandle}
            <div className={styles.agentNameCluster}>
              <div className={styles.agentRowName}>{agent.name}</div>
              <ExpertTaskIndicator status={workStatus} />
              <TeamChatBadge agent={agent} />
              <SharedExpertHint agent={agent} />
              <RemoteExpertHint agent={agent} />
            </div>
            <AgentUnreadBadge count={agent.unread_count ?? 0} />
            {onNewChat ? (
              <button
                type="button"
                className={styles.agentNewChatBtn}
                aria-label={t("chatWelcome.newChat")}
                title={t("chatWelcome.newChat")}
                onClick={(e) => {
                  e.stopPropagation();
                  onNewChat();
                }}
              >
                <MessageSquarePlus size={14} strokeWidth={1.75} aria-hidden />
              </button>
            ) : null}
          </div>
          <div className={styles.agentRowDesc}>{agent.description || "—"}</div>
        </div>
      </div>
      {onHide ? (
        <button
          type="button"
          className={styles.agentHideBtn}
          aria-label={t("chat.expertHide")}
          title={t("chat.expertHide")}
          onClick={onHide}
        >
          <EyeOff size={14} aria-hidden />
        </button>
      ) : null}
      {onUnhide ? (
        <button
          type="button"
          className={styles.agentHideBtn}
          aria-label={t("chat.expertUnhide")}
          title={t("chat.expertUnhide")}
          onClick={onUnhide}
        >
          <Eye size={14} aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

interface SessionListProps {
  agents: OctopAgent[];
  sessions: Session[];
  activeId: string | null;
  activeAgentId: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onFetchAllSessions: () => void;
  onSelect: (sessionId: string, agentId: string) => void;
  onAgentSelect: (agentId: string) => void;
  onNewChat: (agentId: string) => void;
  onDelete: (id: string, compact: boolean) => void | Promise<boolean | void>;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  activeForkDisabled?: boolean;
  activeForkDisabledHint?: string;
}

export default function SessionList({
  agents,
  sessions,
  activeId,
  activeAgentId,
  hasMore,
  loadingMore,
  onLoadMore,
  onFetchAllSessions,
  onSelect,
  onAgentSelect,
  onNewChat,
  onDelete,
  onRename,
  onPin,
  onFork,
  activeForkDisabled,
  activeForkDisabledHint,
}: SessionListProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const liveWorkingIds = useSessionWorkIds();
  const expertWorkStatuses = useExpertWorkStatuses(activeAgentId, sessions);
  const [searchQuery, setSearchQuery] = useState("");
  const [showingHidden, setShowingHidden] = useState(false);
  const [expertOrder, setExpertOrder] = useState<string[]>(() =>
    readExpertOrder(window.localStorage),
  );
  const [draggedAgentId, setDraggedAgentId] = useState<string | null>(null);
  const [dropTargetId, setDropTargetId] = useState<string | null>(null);
  const { filterVisible, pickHidden, hide, unhide, canHide } =
    useHiddenSharedExperts();

  const hiddenAgents = useMemo(() => {
    const hidden = [...pickHidden(agents)].sort((a, b) => b.id - a.id);
    const order = mergeExpertOrder(
      expertOrder,
      hidden.map((agent) => agent.agent_id),
    );
    const index = new Map(order.map((id, position) => [id, position]));
    return hidden.sort(
      (a, b) =>
        (index.get(a.agent_id) ?? Number.MAX_SAFE_INTEGER) -
        (index.get(b.agent_id) ?? Number.MAX_SAFE_INTEGER),
    );
  }, [agents, pickHidden, expertOrder]);

  const sortedAgents = useMemo(() => {
    const visible = filterVisible(agents, {
      keepAgentIds: activeAgentId ? [activeAgentId] : [],
    });
    const byCreation = [...visible].sort((a, b) => b.id - a.id);
    const order = mergeExpertOrder(
      expertOrder,
      byCreation.map((agent) => agent.agent_id),
    );
    const index = new Map(order.map((id, position) => [id, position]));
    return byCreation.sort(
      (a, b) =>
        (index.get(a.agent_id) ?? Number.MAX_SAFE_INTEGER) -
        (index.get(b.agent_id) ?? Number.MAX_SAFE_INTEGER),
    );
  }, [agents, filterVisible, activeAgentId, expertOrder]);

  const handleDrop = useCallback(
    (targetId: string) => {
      if (!draggedAgentId) return;
      const current = mergeExpertOrder(
        expertOrder,
        sortedAgents.map((agent) => agent.agent_id),
      );
      const next = moveExpert(current, draggedAgentId, targetId);
      setExpertOrder(next);
      writeExpertOrder(window.localStorage, next);
      setDraggedAgentId(null);
      setDropTargetId(null);
    },
    [draggedAgentId, expertOrder, sortedAgents],
  );

  const dragHandle = useCallback(
    (agentId: string) => (
      <span
        className={styles.agentDragHandle}
        draggable
        role="button"
        tabIndex={0}
        aria-label={t("chat.reorderExpert", "拖拽排序专家")}
        title={t("chat.reorderExpert", "拖拽排序专家")}
        onDragStart={(event: DragEvent<HTMLSpanElement>) => {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", agentId);
          setDraggedAgentId(agentId);
        }}
        onDragEnd={() => {
          setDraggedAgentId(null);
          setDropTargetId(null);
        }}
      >
        <GripVertical size={13} aria-hidden />
      </span>
    ),
    [t],
  );

  // Leave the hidden-only view once nothing remains hidden.
  const viewingHidden = showingHidden && hiddenAgents.length > 0;
  const agentsToRender = viewingHidden ? hiddenAgents : sortedAgents;

  const expandedAgentId = useMemo(
    () =>
      viewingHidden ? null : activeAgentId ?? sortedAgents[0]?.agent_id ?? null,
    [activeAgentId, sortedAgents, viewingHidden],
  );
  const activeAgent = useMemo(
    () => sortedAgents.find((a) => a.agent_id === expandedAgentId) ?? null,
    [sortedAgents, expandedAgentId],
  );
  const showSessions = isAgentChatReady(activeAgent?.state);

  return (
    <div className={styles.sessionList}>
      {showSessions ? (
        <div className={styles.sessionSearchWrap}>
          <Search
            size={14}
            className={styles.sessionSearchIcon}
            strokeWidth={2}
          />
          <input
            type="search"
            className={styles.sessionSearchInput}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder={t("chat.searchSessions", "搜索会话")}
            aria-label={t("chat.searchSessions", "搜索会话")}
          />
        </div>
      ) : null}

      {agents.length === 0 ? (
        <div className={styles.sessionEmptyAgents}>
          <p className={styles.sessionEmptyAgentsText}>
            {t("chat.noAgentsHint")}
          </p>
          <button
            type="button"
            className={styles.sessionEmptyAgentsLink}
            onClick={() => navigate("/experts")}
          >
            {t("chat.createExpert")}
          </button>
        </div>
      ) : (
        <div className={styles.sessionItems}>
          {viewingHidden
            ? hiddenAgents.map((agent) => (
                <InactiveAgentRow
                  key={agent.agent_id}
                  agent={agent}
                  onSelect={() => {
                    unhide(agent.agent_id);
                    setShowingHidden(false);
                    onAgentSelect(agent.agent_id);
                  }}
                  onUnhide={() => unhide(agent.agent_id)}
                  workStatus={expertWorkStatuses.get(agent.agent_id)}
                />
              ))
            : agentsToRender.map((agent) => {
                const expanded = agent.agent_id === expandedAgentId;
                const content = expanded ? (
                  <ActiveAgentCard
                    agent={agent}
                    sessions={sessions}
                    activeId={activeId}
                    searchQuery={searchQuery}
                    hasMore={hasMore}
                    loadingMore={loadingMore}
                    onLoadMore={onLoadMore}
                    onFetchAllSessions={onFetchAllSessions}
                    onSelect={onSelect}
                    onNewChat={onNewChat}
                    onDelete={onDelete}
                    onRename={onRename}
                    onPin={onPin}
                    onFork={onFork}
                    activeForkDisabled={activeForkDisabled}
                    activeForkDisabledHint={activeForkDisabledHint}
                    onHide={
                      canHide(agent) ? () => hide(agent.agent_id) : undefined
                    }
                    liveWorkingIds={liveWorkingIds}
                    dragHandle={dragHandle(agent.agent_id)}
                    workStatus={expertWorkStatuses.get(agent.agent_id)}
                  />
                ) : (
                  <InactiveAgentRow
                    agent={agent}
                    onSelect={() => onAgentSelect(agent.agent_id)}
                    onNewChat={() => onNewChat(agent.agent_id)}
                    onHide={
                      canHide(agent) ? () => hide(agent.agent_id) : undefined
                    }
                    dragHandle={dragHandle(agent.agent_id)}
                    workStatus={expertWorkStatuses.get(agent.agent_id)}
                  />
                );
                return (
                  <div
                    key={agent.agent_id}
                    className={`${styles.draggableAgentItem} ${
                      draggedAgentId === agent.agent_id
                        ? styles.draggableAgentItemDragging
                        : ""
                    } ${
                      dropTargetId === agent.agent_id
                        ? styles.draggableAgentItemTarget
                        : ""
                    }`}
                    onDragOver={(event) => {
                      if (!draggedAgentId || draggedAgentId === agent.agent_id)
                        return;
                      event.preventDefault();
                      event.dataTransfer.dropEffect = "move";
                      setDropTargetId(agent.agent_id);
                    }}
                    onDragLeave={(event) => {
                      if (
                        !event.currentTarget.contains(
                          event.relatedTarget as Node,
                        )
                      ) {
                        setDropTargetId((current) =>
                          current === agent.agent_id ? null : current,
                        );
                      }
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      handleDrop(agent.agent_id);
                    }}
                  >
                    {content}
                  </div>
                );
              })}
          {hiddenAgents.length > 0 ? (
            <button
              type="button"
              className={styles.expertHiddenToggle}
              onClick={() => setShowingHidden((v) => !v)}
            >
              {viewingHidden
                ? t("chat.expertListShowVisible")
                : t("chat.expertListHidden", { count: hiddenAgents.length })}
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

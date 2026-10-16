import { useEffect, useState } from "react";
import { useRef } from "react";
import type { Session } from "./useSessions";
import { onStreamEvent } from "./chatStore";

export type ExpertWorkStatus = "working" | "waiting" | "interrupted";

/** Thread ids whose turn is streaming in this tab (start/resume until end). */
export function useSessionWorkIds(): ReadonlySet<string> {
  const [ids, setIds] = useState<ReadonlySet<string>>(() => new Set());

  useEffect(() => {
    return onStreamEvent((event) => {
      setIds((prev) => {
        const busy = event.kind !== "streamEnd";
        if (busy === prev.has(event.sessionId)) return prev;
        const next = new Set(prev);
        if (busy) next.add(event.sessionId);
        else next.delete(event.sessionId);
        return next;
      });
    });
  }, []);

  return ids;
}

/** Cross-expert task status retained while the user switches between experts. */
export function useExpertWorkStatuses(
  activeAgentId: string | null,
  sessions: Session[],
): ReadonlyMap<string, ExpertWorkStatus> {
  const [sessionStatuses, setSessionStatuses] = useState<
    ReadonlyMap<string, ExpertWorkStatus>
  >(() => new Map());
  const sessionOwnersRef = useRef(new Map<string, string>());
  const activeAgentIdRef = useRef(activeAgentId);
  activeAgentIdRef.current = activeAgentId;

  useEffect(() => {
    if (!activeAgentId) return;
    for (const session of sessions) {
      sessionOwnersRef.current.set(session.id, activeAgentId);
    }
    setSessionStatuses((previous) => {
      const next = new Map(previous);
      for (const session of sessions) {
        if (session.awaitingUser) next.set(session.id, "waiting");
        else if (session.turnActive) next.set(session.id, "working");
        else if (next.get(session.id) !== "interrupted")
          next.delete(session.id);
      }
      return next;
    });
  }, [activeAgentId, sessions]);

  useEffect(
    () =>
      onStreamEvent((event) => {
        const owner =
          event.agentId ||
          sessionOwnersRef.current.get(event.sessionId) ||
          activeAgentIdRef.current;
        if (owner) sessionOwnersRef.current.set(event.sessionId, owner);

        setSessionStatuses((previous) => {
          const next = new Map(previous);
          if (event.kind !== "streamEnd") {
            next.set(event.sessionId, "working");
          } else if (event.outcome === "waiting") {
            next.set(event.sessionId, "waiting");
          } else if (event.outcome === "interrupted") {
            next.set(event.sessionId, "interrupted");
          } else if (next.get(event.sessionId) !== "interrupted") {
            next.delete(event.sessionId);
          }
          return next;
        });
      }),
    [],
  );

  const result = new Map<string, ExpertWorkStatus>();
  for (const [sessionId, status] of sessionStatuses) {
    const owner = sessionOwnersRef.current.get(sessionId);
    if (!owner) continue;
    const current = result.get(owner);
    if (!current || statusPriority(status) > statusPriority(current)) {
      result.set(owner, status);
    }
  }
  return result;
}

function statusPriority(status: ExpertWorkStatus): number {
  if (status === "interrupted") return 3;
  if (status === "waiting") return 2;
  return 1;
}

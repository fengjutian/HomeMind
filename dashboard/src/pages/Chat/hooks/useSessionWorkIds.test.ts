import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useExpertWorkStatuses, useSessionWorkIds } from "./useSessionWorkIds";
import type { Session } from "./useSessions";
import type { StreamEvent } from "./chatStore";

let listener: ((event: StreamEvent) => void) | null = null;

vi.mock("./chatStore", () => ({
  onStreamEvent: (fn: (event: StreamEvent) => void) => {
    listener = fn;
    return () => {
      listener = null;
    };
  },
}));

describe("useSessionWorkIds", () => {
  afterEach(() => {
    listener = null;
  });

  it("tracks stream start and end for a thread", () => {
    const { result } = renderHook(() => useSessionWorkIds());
    expect(result.current.has("thr_1")).toBe(false);

    act(() => {
      listener?.({ kind: "streamStart", sessionId: "thr_1" });
    });
    expect(result.current.has("thr_1")).toBe(true);

    act(() => {
      listener?.({ kind: "streamResume", sessionId: "thr_1" });
    });
    expect(result.current.has("thr_1")).toBe(true);

    act(() => {
      listener?.({ kind: "streamEnd", sessionId: "thr_1" });
    });
    expect(result.current.has("thr_1")).toBe(false);
  });

  it("retains task status on the owning expert after switching experts", () => {
    const workingSession = {
      id: "thr_1",
      name: "Work",
      threadId: "thr_1",
      updatedAt: null,
      channelType: "dashboard",
      turnActive: true,
    } satisfies Session;
    const { result, rerender } = renderHook(
      ({ agentId, sessions }: { agentId: string; sessions: Session[] }) =>
        useExpertWorkStatuses(agentId, sessions),
      { initialProps: { agentId: "agent-a", sessions: [workingSession] } },
    );

    expect(result.current.get("agent-a")).toBe("working");
    rerender({ agentId: "agent-b", sessions: [] });
    expect(result.current.get("agent-a")).toBe("working");

    act(() => {
      listener?.({
        kind: "streamEnd",
        sessionId: "thr_1",
        outcome: "waiting",
      });
    });
    expect(result.current.get("agent-a")).toBe("waiting");

    act(() => {
      listener?.({
        kind: "streamEnd",
        sessionId: "thr_1",
        outcome: "interrupted",
      });
    });
    expect(result.current.get("agent-a")).toBe("interrupted");
  });
});

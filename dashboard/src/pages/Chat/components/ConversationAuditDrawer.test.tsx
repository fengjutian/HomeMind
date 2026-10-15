import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConversationAuditDrawer from "./ConversationAuditDrawer";

const getThreadAudit = vi.fn();

vi.mock("../../../api/modules/audit", () => ({
  auditApi: { getThreadAudit: (...args: unknown[]) => getThreadAudit(...args) },
}));
vi.mock("../../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "Asia/Shanghai",
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe("ConversationAuditDrawer", () => {
  beforeEach(() => {
    getThreadAudit.mockReset();
    getThreadAudit.mockResolvedValue({
      thread_id: "T1",
      agent_id: "A1",
      summary: {
        model: "openai:gpt",
        status: "ready",
        has_errors: false,
        input_tokens: 12,
        output_tokens: 8,
        total_tokens: 20,
        model_calls: 1,
      },
      timeline: [
        {
          id: "workspace:C1",
          kind: "workspace_change",
          ts: 1_700_000_000,
          status: "succeeded",
          turn_id: "turn-1",
          tool_call_id: "call-1",
          payload: {
            path: "notes.txt",
            diff_kind: "binary",
            metadata: {},
          },
        },
      ],
      has_more: true,
      next_before_ts: 1_700_000_000,
    });
  });

  it("renders summary, classified event, identifiers, and content notice", async () => {
    render(<ConversationAuditDrawer threadId="T1" />);

    await waitFor(() => expect(getThreadAudit).toHaveBeenCalled());
    expect(screen.getByText("openai:gpt")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    expect(screen.getByText("turn-1")).toBeInTheDocument();
    expect(screen.getByText("call-1")).toBeInTheDocument();
    expect(screen.getByText("chat.audit.notice.binary")).toBeInTheDocument();
    expect(screen.getByText("chat.audit.loadMore")).toBeInTheDocument();

    fireEvent.click(screen.getByText("notes.txt"));
    expect(screen.getByText("chat.audit.detail")).toBeInTheDocument();
    expect(screen.getByText("workspace:C1")).toBeInTheDocument();
  });
});

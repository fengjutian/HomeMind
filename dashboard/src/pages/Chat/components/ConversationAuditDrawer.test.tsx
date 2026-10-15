import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ConversationAuditDrawer, {
  auditItemPreview,
  mergeAuditItems,
} from "./ConversationAuditDrawer";

const getThreadAudit = vi.fn();

vi.mock("../../../api/modules/audit", () => ({
  auditApi: { getThreadAudit: (...args: unknown[]) => getThreadAudit(...args) },
}));
vi.mock("../../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "Asia/Shanghai",
}));
vi.mock("../../../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));
vi.mock("@monaco-editor/react", () => ({
  default: ({
    language,
    value,
    options,
  }: {
    language: string;
    value: string;
    options: { readOnly?: boolean };
  }) => (
    <textarea
      aria-label="audit-json-editor"
      data-language={language}
      data-read-only={String(options.readOnly)}
      readOnly
      value={value}
    />
  ),
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
    expect(screen.getByText("chat.audit.kind.trajectory")).toBeInTheDocument();
    expect(screen.getByText("chat.audit.loadMore")).toBeInTheDocument();

    fireEvent.click(screen.getByText("notes.txt"));
    expect(screen.getByText("chat.audit.detail")).toBeInTheDocument();
    expect(screen.getByText("workspace:C1")).toBeInTheDocument();
    const editor = await screen.findByLabelText("audit-json-editor");
    expect(editor).toHaveAttribute("data-language", "json");
    expect(editor).toHaveAttribute("data-read-only", "true");
    expect(editor).toHaveValue(
      JSON.stringify(
        {
          path: "notes.txt",
          diff_kind: "binary",
          metadata: {},
        },
        null,
        2,
      ),
    );
  });

  it("truncates long timeline payloads but keeps the full detail payload", () => {
    const content = "x".repeat(500);
    const item = {
      id: "message:1",
      kind: "message" as const,
      ts: 1,
      payload: { content },
    };

    expect(auditItemPreview(item)).toHaveLength(321);
    expect(auditItemPreview(item).endsWith("…")).toBe(true);
  });

  it("merges refreshed events without dropping loaded history", () => {
    const oldItem = {
      id: "message:old",
      kind: "message" as const,
      ts: 1,
      payload: {},
    };
    const currentItem = {
      id: "message:current",
      kind: "message" as const,
      ts: 2,
      payload: { content: "before" },
    };
    const updatedItem = {
      ...currentItem,
      payload: { content: "after" },
    };
    const newItem = {
      id: "message:new",
      kind: "message" as const,
      ts: 3,
      payload: {},
    };

    expect(
      mergeAuditItems([oldItem, currentItem], [updatedItem, newItem]),
    ).toEqual([oldItem, updatedItem, newItem]);
  });
});

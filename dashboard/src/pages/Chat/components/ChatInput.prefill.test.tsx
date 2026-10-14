import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { createRef } from "react";

vi.mock("../../../hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("../../../hooks/useSlashCommands", () => ({
  useSlashCommands: () => ({
    commands: [],
    labelFor: (cmd: string) => cmd,
  }),
}));

vi.mock("../../../hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({
    recording: false,
    transcribing: false,
    realtime: false,
    start: vi.fn(),
    stop: vi.fn(),
    toggle: vi.fn(),
  }),
}));

vi.mock("../../../hooks/useKeepInVisualViewport", () => ({
  useKeepInVisualViewport: () => undefined,
}));

vi.mock("../../../hooks/viewport", () => ({
  isPwaDisplay: () => false,
  needsComposerVisualViewportFix: () => false,
}));

vi.mock("../hooks/useChatAttachments", () => ({
  useChatAttachments: () => ({
    attachments: [],
    uploading: false,
    dragOver: false,
    fileInputRef: { current: null },
    handleFileSelect: vi.fn(),
    handleFileChange: vi.fn(),
    removeAttachment: vi.fn(),
    clearAttachments: vi.fn(),
    restoreAttachments: vi.fn(),
    handlePaste: vi.fn(),
    handleDragEnter: vi.fn(),
    handleDragLeave: vi.fn(),
    handleDragOver: vi.fn(),
    handleDrop: vi.fn(),
  }),
}));

vi.mock("../hooks/chatStore", () => ({
  consumePendingPrefillAttachments: () => [],
  readInputDraft: (agentId: string) =>
    agentId === "agent-2" ? "target expert draft" : "",
  writeInputDraft: vi.fn(),
}));

vi.mock("./ChatInputPreviewBar", () => ({
  default: () => null,
}));

vi.mock("./ChatInputActionsRow", () => ({
  default: ({
    onSubmit,
    canSend,
  }: {
    onSubmit: () => void;
    canSend: boolean;
  }) => (
    <button type="button" disabled={!canSend} onClick={onSubmit}>
      send
    </button>
  ),
}));

vi.mock("./ChatQueuedMessages", () => ({
  default: () => null,
}));

vi.mock("./SlashCommandMenu", () => ({
  default: () => null,
}));

vi.mock("./MentionPickerMenu", () => ({
  default: () => null,
  buildMentionItems: () => [],
}));

vi.mock("../hooks/useSlashMentionInput", () => ({
  useSlashMentionInput: ({
    text,
    setText,
    onSubmitRef,
  }: {
    text: string;
    setText: (v: string) => void;
    onSubmitRef: { current: () => void };
  }) => ({
    slashMenuOpen: false,
    slashMenuIndex: 0,
    setSlashMenuIndex: vi.fn(),
    mentionMenuOpen: false,
    mentionMenuIndex: 0,
    setMentionMenuIndex: vi.fn(),
    mentionQuery: "",
    mentionAgents: [],
    slashMenuFlat: [],
    slashMenuGroups: [],
    slashPickerGroups: [],
    slashMenuItems: [],
    mentionItems: [],
    filesLoading: false,
    filesError: null,
    runSlashCommand: vi.fn(),
    matchSlashCommand: () => null,
    handleMentionSelect: vi.fn(),
    handleSlashSelect: vi.fn(),
    handleTextChange: setText,
    handleKeyDown: (e: { key: string; preventDefault: () => void }) => {
      if (e.key === "Enter" && !e.preventDefault) return;
      void text;
      void onSubmitRef;
    },
  }),
}));

import ChatInput, {
  LONG_TEXT_CHARACTER_THRESHOLD,
  LONG_TEXT_LINE_THRESHOLD,
  isLongComposerText,
  type ChatInputHandle,
} from "./ChatInput";

beforeEach(() => {
  vi.clearAllMocks();
});

describe("long composer text", () => {
  it("collapses by character count or line count", () => {
    expect(
      isLongComposerText("a".repeat(LONG_TEXT_CHARACTER_THRESHOLD - 1)),
    ).toBe(false);
    expect(isLongComposerText("a".repeat(LONG_TEXT_CHARACTER_THRESHOLD))).toBe(
      true,
    );
    expect(
      isLongComposerText(
        Array(LONG_TEXT_LINE_THRESHOLD).fill("line").join("\n"),
      ),
    ).toBe(true);
  });

  it("keeps long text collapsed while allowing an appended sentence", () => {
    const longText = `preview from the beginning ${"a".repeat(
      LONG_TEXT_CHARACTER_THRESHOLD,
    )}`;
    const onSend = vi.fn();

    render(
      <ChatInput
        onSend={onSend}
        onCancel={vi.fn()}
        onNewChat={vi.fn()}
        isStreaming={false}
        initialText={longText}
        agentId="agent-1"
        threadId="thread-1"
      />,
    );

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea.value).toBe("");
    expect(screen.getByText(longText)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "chat.longText.expand" }),
    ).toBeNull();

    fireEvent.change(textarea, {
      target: { value: "Please focus on security." },
    });

    fireEvent.click(screen.getByRole("button", { name: "send" }));
    expect(onSend).toHaveBeenCalledWith(
      `${longText}\nPlease focus on security.`,
      undefined,
    );
  });

  it("removes collapsed text from the composer", () => {
    render(
      <ChatInput
        onSend={vi.fn()}
        onCancel={vi.fn()}
        onNewChat={vi.fn()}
        isStreaming={false}
        initialText={"a".repeat(LONG_TEXT_CHARACTER_THRESHOLD)}
        agentId="agent-1"
        threadId="thread-1"
      />,
    );

    fireEvent.click(screen.getByTestId("long-text-remove"));

    expect(screen.queryByTestId("long-text-card")).toBeNull();
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe("");
  });
});

describe("ChatInput prefill clear-on-send", () => {
  it("does not restore stale initialText after send (skill-card prefill path)", async () => {
    // Mirrors welcome skill/quick-card flow:
    // 1) setPrefillText fills the composer imperatively while parent initialText
    //    is still ""
    // 2) parent keeps the prefill in a ref and re-passes it as initialText on
    //    the next render (e.g. after send updates chat state)
    // 3) composer must stay empty after send — not re-filled from that prop
    const onSend = vi.fn();
    const onCancel = vi.fn();
    const onNewChat = vi.fn();
    const inputRef = createRef<ChatInputHandle>();

    const { rerender } = render(
      <ChatInput
        ref={inputRef}
        onSend={onSend}
        onCancel={onCancel}
        onNewChat={onNewChat}
        isStreaming={false}
        initialText=""
        agentId="agent-1"
        threadId="thread-1"
      />,
    );

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    const prefill = "请填写收件人：";

    act(() => {
      inputRef.current?.setPrefillText(prefill);
    });
    expect(textarea.value).toBe(prefill);

    fireEvent.click(screen.getByRole("button", { name: "send" }));
    expect(onSend).toHaveBeenCalledWith(prefill, undefined);
    expect(textarea.value).toBe("");

    // Parent re-render still holding the prefill string in initialText
    rerender(
      <ChatInput
        ref={inputRef}
        onSend={onSend}
        onCancel={onCancel}
        onNewChat={onNewChat}
        isStreaming={true}
        initialText={prefill}
        agentId="agent-1"
        threadId="thread-1"
      />,
    );

    expect(textarea.value).toBe("");
  });

  it("clears parent prefill via onComposerCleared so edited text cannot be overwritten", () => {
    const onSend = vi.fn();
    const onCancel = vi.fn();
    const onNewChat = vi.fn();
    const inputRef = createRef<ChatInputHandle>();
    let parentPrefill = "";

    const { rerender } = render(
      <ChatInput
        ref={inputRef}
        onSend={onSend}
        onCancel={onCancel}
        onNewChat={onNewChat}
        isStreaming={false}
        initialText=""
        onComposerCleared={() => {
          parentPrefill = "";
        }}
        agentId="agent-1"
        threadId="thread-1"
      />,
    );

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    const prefill = "请填写收件人：";
    parentPrefill = prefill;

    act(() => {
      inputRef.current?.setPrefillText(prefill);
    });
    fireEvent.change(textarea, {
      target: { value: `${prefill}\n张三` },
    });

    fireEvent.click(screen.getByRole("button", { name: "send" }));
    expect(onSend).toHaveBeenCalledWith(`${prefill}\n张三`, undefined);
    expect(textarea.value).toBe("");
    expect(parentPrefill).toBe("");

    rerender(
      <ChatInput
        ref={inputRef}
        onSend={onSend}
        onCancel={onCancel}
        onNewChat={onNewChat}
        isStreaming={true}
        initialText={parentPrefill}
        onComposerCleared={() => {
          parentPrefill = "";
        }}
        agentId="agent-1"
        threadId="thread-1"
      />,
    );

    expect(textarea.value).toBe("");
  });

  it("restores the target agent draft instead of carrying stale prefill", () => {
    const props = {
      onSend: vi.fn(),
      onCancel: vi.fn(),
      onNewChat: vi.fn(),
      isStreaming: false,
      initialText: "previous expert prompt",
      threadId: null,
    };
    const { rerender } = render(<ChatInput {...props} agentId="agent-1" />);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea.value).toBe("previous expert prompt");

    rerender(<ChatInput {...props} agentId="agent-2" />);

    expect(textarea.value).toBe("target expert draft");
  });

  it("sends immediately in a team room while a turn is still streaming", () => {
    const onSend = vi.fn();
    const onQueue = vi.fn();
    render(
      <ChatInput
        onSend={onSend}
        onQueue={onQueue}
        onCancel={vi.fn()}
        onNewChat={vi.fn()}
        isStreaming
        isTeam
        agentId="team-1"
        threadId="thread-1"
      />,
    );
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "second question" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));
    expect(onSend).toHaveBeenCalledWith("second question", undefined);
    expect(onQueue).not.toHaveBeenCalled();
  });
});

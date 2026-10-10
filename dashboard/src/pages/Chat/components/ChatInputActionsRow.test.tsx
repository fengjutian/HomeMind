import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { ResolvedModel } from "../../../api/types";
import ChatInputActionsRow from "./ChatInputActionsRow";

// `isSttAvailable()` runs at module load, so the browser APIs it probes must
// exist before this module graph is imported.
vi.hoisted(() => {
  vi.stubGlobal("MediaRecorder", class {});
  Object.defineProperty(navigator, "mediaDevices", {
    value: { getUserMedia: () => Promise.resolve() },
    configurable: true,
  });
  return true;
});

vi.mock("./ContextWindowRing", () => ({
  default: () => null,
}));

const models: ResolvedModel[] = [
  {
    provider_id: 1,
    provider_name: "Provider",
    provider_kind: "openai",
    model: "compact-model",
    name: "Compact Model",
    context_window: 128_000,
  },
];

const baseProps = {
  isMobile: false,
  isStreaming: false,
  canSend: false,
  text: "",
  polishing: false,
  uploading: false,
  recording: false,
  transcribing: false,
  availableModels: models,
  onModelChange: vi.fn(),
  onConversationModeChange: vi.fn(),
  onHitlPolicyChange: vi.fn(),
  slashPickerGroups: null,
  slashMenuItems: [],
  onSlashShortcutSelect: vi.fn(),
  onFileSelect: vi.fn(),
  onNewChat: vi.fn(),
  onPolish: vi.fn(),
  onToggleVoice: vi.fn(),
  onCancel: vi.fn(),
  onSubmit: vi.fn(),
};

describe("ChatInputActionsRow plus menu", () => {
  it("keeps approval, shortcuts, and attachments on the toolbar", () => {
    render(
      <MemoryRouter>
        <ChatInputActionsRow {...baseProps} />
      </MemoryRouter>,
    );

    expect(screen.getByTestId("composer-plus")).toBeInTheDocument();
    expect(screen.getByTestId("hitl-policy-picker")).toBeInTheDocument();
    expect(screen.getByLabelText("快捷指令")).toBeInTheDocument();
    expect(screen.getByLabelText("Upload attachment")).toBeInTheDocument();
    expect(
      screen.queryByTestId("conversation-mode-picker"),
    ).not.toBeInTheDocument();
  });

  it("hides the approval picker when HITL policy cannot be changed", () => {
    render(
      <MemoryRouter>
        <ChatInputActionsRow {...baseProps} onHitlPolicyChange={undefined} />
      </MemoryRouter>,
    );

    expect(screen.queryByTestId("hitl-policy-picker")).not.toBeInTheDocument();
    expect(screen.getByLabelText("快捷指令")).toBeInTheDocument();
  });

  it("switches models from the bottom toolbar", async () => {
    const onModelChange = vi.fn();
    render(
      <MemoryRouter>
        <ChatInputActionsRow {...baseProps} onModelChange={onModelChange} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByTestId("composer-model-picker"));
    const modelLabels = await screen.findAllByText(/Compact Model/);
    fireEvent.click(modelLabels[modelLabels.length - 1]);

    expect(onModelChange).toHaveBeenCalledWith("Provider/compact-model");
  });

  it("opens a flyout beside the plus menu instead of a window drawer", async () => {
    render(
      <MemoryRouter>
        <ChatInputActionsRow {...baseProps} />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByTestId("composer-plus"));

    await waitFor(() => {
      expect(document.querySelector(".ant-popover")).toBeInTheDocument();
    });
    expect(screen.getByText("Model")).toBeInTheDocument();
    expect(screen.queryByTestId("composer-plus-panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByText("Model"));

    const panel = await screen.findByTestId("composer-plus-panel");
    expect(panel.querySelector("img")).not.toBeNull();
    const menu = document.querySelector("[class*='plusFlyoutMenu']");
    if (menu && menu.getBoundingClientRect().height > 0) {
      expect(Number.parseFloat(getComputedStyle(panel).maxHeight)).toBe(
        Math.round(menu.getBoundingClientRect().height),
      );
    }
    expect(document.querySelector(".ant-drawer-content")).toBeNull();
    expect(document.querySelector(".ant-popover")).toBeInTheDocument();
    expect(screen.getByText("Model")).toBeInTheDocument();
  });
});

function renderRow(overrides: Record<string, unknown> = {}) {
  const handlers = {
    onVoiceStart: vi.fn(),
    onVoiceStop: vi.fn(),
    onToggleVoice: vi.fn(),
  };
  const view = render(
    <MemoryRouter>
      <ChatInputActionsRow
        isMobile={false}
        isStreaming={false}
        canSend={false}
        text=""
        polishing={false}
        uploading={false}
        recording={false}
        transcribing={false}
        slashPickerGroups={null}
        slashMenuItems={[]}
        onSlashShortcutSelect={vi.fn()}
        onFileSelect={vi.fn()}
        onNewChat={vi.fn()}
        onPolish={vi.fn()}
        onCancel={vi.fn()}
        onSubmit={vi.fn()}
        {...handlers}
        {...overrides}
      />
    </MemoryRouter>,
  );
  const mic = view.container
    .querySelector("svg.lucide-mic")
    ?.closest("button") as HTMLButtonElement;
  return { ...view, ...handlers, mic };
}

describe("ChatInputActionsRow voice button", () => {
  it("dictates while held down when realtime STT is on", () => {
    const { mic, onVoiceStart, onVoiceStop, onToggleVoice } = renderRow({
      voiceHoldToTalk: true,
    });

    fireEvent.pointerDown(mic);
    expect(onVoiceStart).toHaveBeenCalledTimes(1);

    fireEvent.pointerUp(mic);
    expect(onVoiceStop).toHaveBeenCalledTimes(1);

    // A plain click must not toggle the recorder in hold-to-talk mode.
    fireEvent.click(mic);
    expect(onToggleVoice).not.toHaveBeenCalled();
    expect(onVoiceStart).toHaveBeenCalledTimes(1);
  });

  it("finishes when the pointer slides off the button", () => {
    const { mic, onVoiceStop } = renderRow({ voiceHoldToTalk: true });

    fireEvent.pointerDown(mic);
    fireEvent.pointerLeave(mic);
    fireEvent.pointerUp(mic);

    expect(onVoiceStop).toHaveBeenCalledTimes(1);
  });

  it("keeps click-to-toggle when realtime STT is off", () => {
    const { mic, onToggleVoice, onVoiceStart } = renderRow();

    fireEvent.click(mic);
    expect(onToggleVoice).toHaveBeenCalledTimes(1);

    fireEvent.pointerDown(mic);
    expect(onVoiceStart).not.toHaveBeenCalled();
  });

  it("stays available while a reply is still streaming", () => {
    const { mic } = renderRow({ voiceHoldToTalk: true, isStreaming: true });

    expect(mic).not.toBeDisabled();
  });
});

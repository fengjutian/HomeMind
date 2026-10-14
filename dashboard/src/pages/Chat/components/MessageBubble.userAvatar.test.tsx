import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MessageBubble from "./MessageBubble";
import type { ChatMessage } from "../hooks/useChat";
import type { OctopUser } from "../../../api/modules/auth";
import { LONG_TEXT_CHARACTER_THRESHOLD } from "../utils/longText";

const currentUser: Partial<OctopUser> = {
  username: "ada",
  display_name: "Ada",
  avatar_icon: "business",
  avatar_url: null,
};

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => currentUser,
}));

vi.mock("../../../hooks/useAuthImageSrc", () => ({
  useAuthImageSrc: (url: string) => ({
    src: url || "",
    loadState: url ? "ready" : "idle",
  }),
}));

vi.mock("../../../context/VoiceOutputContext", () => ({
  useVoiceOutputContext: () => ({ speakingId: null, speak: vi.fn() }),
}));

vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    activeAgent: { agent_id: "host", name: "助手", icon_name: "bot" },
    agents: [],
  }),
}));

function userMessage(extra: Partial<ChatMessage> = {}): ChatMessage {
  return {
    id: extra.id ?? "u1",
    role: "user",
    content: extra.content ?? "hello there",
    status: "done",
    timestamp: Date.now(),
    ...extra,
  };
}

describe("MessageBubble user avatar", () => {
  it("renders a sent long user message as a collapsed card", () => {
    const content = `long message ${"a".repeat(LONG_TEXT_CHARACTER_THRESHOLD)}`;
    render(<MessageBubble message={userMessage({ content })} showAvatar />);

    expect(screen.getByTestId("user-long-text-card")).toBeInTheDocument();
    expect(screen.getByText(content)).toBeInTheDocument();
  });

  it("shows the account preset icon instead of name initials", () => {
    currentUser.avatar_icon = "doctor";
    currentUser.avatar_url = null;
    render(<MessageBubble message={userMessage()} showAvatar />);

    const sender = screen.getByLabelText("Ada");
    expect(sender.querySelector("img")).toBeTruthy();
    expect(sender.querySelector("svg")).toBeNull();
    expect(sender).not.toHaveTextContent("A");
  });

  it("keeps the Lucide default when no account icon is chosen", () => {
    currentUser.avatar_icon = null;
    currentUser.avatar_url = null;
    render(<MessageBubble message={userMessage()} showAvatar />);

    const sender = screen.getByLabelText("Ada");
    expect(sender.querySelector("svg")).toBeTruthy();
    expect(sender.querySelector("img")).toBeNull();
  });

  it("shows the uploaded account photo when present", () => {
    currentUser.avatar_icon = "user";
    currentUser.avatar_url = "/api/users/7/avatar";
    render(<MessageBubble message={userMessage()} showAvatar />);

    const sender = screen.getByLabelText("Ada");
    expect(sender.querySelector("img")).toHaveAttribute(
      "src",
      "/api/users/7/avatar",
    );
    expect(sender.querySelector("svg")).toBeNull();
  });
});

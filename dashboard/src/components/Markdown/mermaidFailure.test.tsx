import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Markdown from "./index";

vi.mock("./mermaidLoader", () => ({
  loadMermaid: vi.fn(async () => ({
    parse: vi.fn(async () => {
      throw new Error("invalid diagram");
    }),
    render: vi.fn(),
  })),
}));

describe("Mermaid parse failure", () => {
  it("offers to regenerate only the failed diagram", async () => {
    const onRegenerateMermaid = vi.fn();
    const code = "flowchart TD\nA[broken";

    render(
      <Markdown
        content={`\`\`\`mermaid\n${code}\n\`\`\``}
        onRegenerateMermaid={onRegenerateMermaid}
      />,
    );

    const button = await waitFor(() =>
      screen.getByRole("button", { name: "chat.regenerateMermaid" }),
    );
    fireEvent.click(button);

    expect(onRegenerateMermaid).toHaveBeenCalledOnce();
    expect(onRegenerateMermaid).toHaveBeenCalledWith(code);
  });
});

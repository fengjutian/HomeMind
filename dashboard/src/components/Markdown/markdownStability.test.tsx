import { render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import Markdown from "./index";

const editorInstances = vi.hoisted(() => ({ count: 0 }));

vi.mock("@monaco-editor/react", () => ({
  default: function MockEditor({ value }: { value: string }) {
    const [instance] = useState(() => ++editorInstances.count);
    return <code data-editor-instance={instance}>{value}</code>;
  },
}));

describe("Markdown renderer stability", () => {
  it("keeps a completed code editor mounted across parent rerenders", async () => {
    const content = "```python\nprint('stable')\n```";
    const view = render(<Markdown content={content} style={{ opacity: 1 }} />);

    await screen.findByText("print('stable')");
    const editor = document.querySelector("[data-editor-instance]");
    expect(editor).not.toBeNull();
    const instance = editor!.getAttribute("data-editor-instance");

    view.rerender(<Markdown content={content} style={{ opacity: 1 }} />);

    await waitFor(() => {
      expect(document.querySelector("[data-editor-instance]")).toHaveAttribute(
        "data-editor-instance",
        instance,
      );
    });
    expect(editorInstances.count).toBe(1);
  });
});

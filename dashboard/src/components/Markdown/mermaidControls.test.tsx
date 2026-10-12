import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import Markdown from "./index";

const mermaidMocks = vi.hoisted(() => ({
  parse: vi.fn(async () => true),
  render: vi.fn(async () => ({
    svg: '<svg viewBox="0 0 800 400"><text data-testid="diagram-label">Diagram</text></svg>',
  })),
}));

vi.mock("./mermaidLoader", () => ({
  loadMermaid: vi.fn(async () => ({
    parse: mermaidMocks.parse,
    render: mermaidMocks.render,
  })),
}));

describe("Mermaid controls", () => {
  it("restores the diagram height after leaving and re-entering", async () => {
    const content = "```mermaid\nflowchart TD\nA-->B\n```";
    const first = render(<Markdown content={content} />);

    await screen.findByText("Diagram");
    expect(screen.getByText("Diagram").closest("div")).toHaveStyle({
      aspectRatio: "800 / 400",
    });

    first.unmount();
    render(<Markdown content={content} />);

    await screen.findByText("Diagram");
    expect(screen.getByText("Diagram").closest("div")).toHaveStyle({
      aspectRatio: "800 / 400",
    });
  });

  it("zooms the rendered diagram within its limits", async () => {
    render(<Markdown content={"```mermaid\nflowchart TD\nA-->B\n```"} />);

    await screen.findByText("Diagram");
    const zoomIn = screen.getByRole("button", { name: "chat.mermaidZoomIn" });
    const zoomOut = screen.getByRole("button", {
      name: "chat.mermaidZoomOut",
    });

    fireEvent.click(zoomIn);
    expect(screen.getByText("125%")).toBeInTheDocument();
    fireEvent.click(zoomOut);
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("keeps the diagram instance and zoom across parent rerenders", async () => {
    const content = "```mermaid\nflowchart TD\nA-->B\n```";
    const rendersBefore = mermaidMocks.render.mock.calls.length;
    const view = render(<Markdown content={content} style={{ opacity: 1 }} />);

    await screen.findByText("Diagram");
    expect(mermaidMocks.render).toHaveBeenCalledTimes(rendersBefore + 1);
    fireEvent.click(screen.getByRole("button", { name: "chat.mermaidZoomIn" }));
    expect(screen.getByText("125%")).toBeInTheDocument();

    view.rerender(<Markdown content={content} style={{ opacity: 1 }} />);

    expect(screen.getByText("125%")).toBeInTheDocument();
    await waitFor(() =>
      expect(mermaidMocks.render).toHaveBeenCalledTimes(rendersBefore + 1),
    );
  });

  it("selects the diagram and toggles fullscreen", async () => {
    const addRange = vi.fn();
    const removeAllRanges = vi.fn();
    vi.spyOn(window, "getSelection").mockReturnValue({
      addRange,
      removeAllRanges,
    } as unknown as Selection);

    render(<Markdown content={"```mermaid\nflowchart TD\nA-->B\n```"} />);
    await screen.findByText("Diagram");

    fireEvent.click(
      screen.getByRole("button", { name: "chat.mermaidSelectAll" }),
    );
    expect(removeAllRanges).toHaveBeenCalledOnce();
    expect(addRange).toHaveBeenCalledOnce();

    const fullscreen = screen.getByRole("button", {
      name: "chat.mermaidFullscreen",
    });
    const block = fullscreen.closest("div[class]")?.parentElement;
    const requestFullscreen = vi.fn(async () => {});
    Object.defineProperty(block, "requestFullscreen", {
      configurable: true,
      value: requestFullscreen,
    });
    fireEvent.click(fullscreen);
    await waitFor(() => expect(requestFullscreen).toHaveBeenCalledOnce());
  });
});

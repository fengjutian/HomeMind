import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import {
  codeEditorHeight,
  HighlightedCode,
  monacoLanguageFor,
} from "./syntaxHighlight";

vi.mock("@monaco-editor/react", () => ({
  default: ({
    language,
    theme,
    value,
  }: {
    language: string;
    theme: string;
    value: string;
  }) => (
    <code data-language={language} data-theme={theme}>
      {value}
    </code>
  ),
}));

describe("HighlightedCode", () => {
  it("renders completed Vue code with the Monaco editor", async () => {
    render(
      <HighlightedCode
        language="vue"
        code={'<div v-if="shipping_type">Example</div>'}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText(/shipping_type/)).toHaveAttribute(
        "data-language",
        "html",
      );
      expect(screen.getByText(/shipping_type/)).toHaveAttribute(
        "data-theme",
        "vs-dark",
      );
    });
  });

  it("keeps streaming code in a lightweight pre block", () => {
    const { container } = render(
      <HighlightedCode language="dart" code="final value = 1;" plain />,
    );

    expect(container.querySelector("pre code")).toHaveTextContent(
      "final value = 1;",
    );
  });
});

describe("Monaco code block configuration", () => {
  it.each([
    ["vue", "html"],
    ["tsx", "typescript"],
    ["jsx", "javascript"],
    ["bash", "shell"],
    ["dart", "dart"],
  ])("maps %s to %s", (input, expected) => {
    expect(monacoLanguageFor(input)).toBe(expected);
  });

  it("grows with content and caps long blocks", () => {
    expect(codeEditorHeight("one line")).toBe(60);
    expect(codeEditorHeight("a\nb\nc\nd")).toBe(100);
    expect(codeEditorHeight(Array(100).fill("line").join("\n"))).toBe(520);
  });
});

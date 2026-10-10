import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import FileViewer from "./FileViewer";

vi.mock("./CodeEditor", () => ({
  default: ({
    language,
    readOnly,
    value,
  }: {
    language?: string;
    readOnly?: boolean;
    value: string;
  }) => (
    <div
      data-testid="code-editor"
      data-language={language}
      data-readonly={String(readOnly)}
    >
      {value}
    </div>
  ),
}));

vi.mock("./DocumentPreview", () => ({ default: () => null }));
vi.mock("./MediaPreview", () => ({ default: () => null }));

describe("FileViewer", () => {
  it("uses read-only Monaco for source files in view mode", () => {
    render(
      <FileViewer
        path="Form.vue"
        editMode={false}
        value="<template><div /></template>"
        onChange={vi.fn()}
      />,
    );

    const editor = screen.getByTestId("code-editor");
    expect(editor).toHaveAttribute("data-readonly", "true");
    expect(editor).toHaveTextContent("<template><div /></template>");
  });
});

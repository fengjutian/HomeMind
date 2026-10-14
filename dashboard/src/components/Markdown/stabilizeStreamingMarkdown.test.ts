import { describe, expect, it } from "vitest";
import {
  repairOrphanedDirectoryTreeFence,
  stabilizeStreamingMarkdown,
} from "./stabilizeStreamingMarkdown";

describe("stabilizeStreamingMarkdown", () => {
  it("leaves complete fences alone", () => {
    const src = "before\n```js\nconst x = 1\n```\nafter";
    expect(stabilizeStreamingMarkdown(src)).toBe(src);
  });

  it("closes an open fence so trailing prose is not swallowed", () => {
    const src = "intro\n```python\nprint(1";
    expect(stabilizeStreamingMarkdown(src)).toBe(
      "intro\n```python\nprint(1\n```",
    );
  });

  it("handles tilde fences", () => {
    const src = "~~~\ncode";
    expect(stabilizeStreamingMarkdown(src)).toBe("~~~\ncode\n~~~");
  });

  it("is a no-op for empty content", () => {
    expect(stabilizeStreamingMarkdown("")).toBe("");
  });
});

describe("repairOrphanedDirectoryTreeFence", () => {
  it("restores a missing opening fence before a directory tree", () => {
    const src = [
      "## Project layout",
      "",
      "services/",
      "├── api/",
      "└── repository/",
      "```",
      "",
      "---",
      "",
      "## Request flow",
    ].join("\n");

    expect(repairOrphanedDirectoryTreeFence(src)).toBe(
      [
        "## Project layout",
        "",
        "```text",
        "services/",
        "├── api/",
        "└── repository/",
        "```",
        "",
        "---",
        "",
        "## Request flow",
      ].join("\n"),
    );
  });

  it("does not reinterpret an ordinary unclosed code fence", () => {
    const src = "Intro\n```python\nprint('hello')";
    expect(repairOrphanedDirectoryTreeFence(src)).toBe(src);
  });

  it("does not touch complete fenced Markdown", () => {
    const src = "```text\nroot/\n├── a/\n└── b/\n```";
    expect(repairOrphanedDirectoryTreeFence(src)).toBe(src);
  });
});

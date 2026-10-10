import { describe, expect, it } from "vitest";

import { getEditorLanguage } from "./editorLanguage";

describe("getEditorLanguage", () => {
  it("uses HTML syntax highlighting for Vue single-file components", () => {
    expect(getEditorLanguage("components/Form.vue")).toBe("html");
  });
});

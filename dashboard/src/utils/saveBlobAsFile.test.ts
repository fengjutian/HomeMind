import { afterEach, describe, expect, it, vi } from "vitest";

import { shareDownloadLink } from "./downloadLink";
import { saveBlobAsFile } from "./saveBlobAsFile";

vi.mock("./downloadLink", () => ({
  shareDownloadLink: vi.fn(),
}));

afterEach(() => {
  vi.mocked(shareDownloadLink).mockReset();
  vi.restoreAllMocks();
});

describe("saveBlobAsFile", () => {
  it("uses a download anchor in the browser", async () => {
    const click = vi.fn();
    const createElement = vi.spyOn(document, "createElement");
    createElement.mockImplementation(
      () =>
        ({
          href: "",
          download: "",
          click,
          style: {},
        }) as unknown as HTMLElement,
    );
    vi.spyOn(document.body, "appendChild").mockImplementation((node) => node);
    vi.spyOn(document.body, "removeChild").mockImplementation((node) => node);
    URL.createObjectURL = vi.fn(() => "blob:save");
    URL.revokeObjectURL = vi.fn();

    await saveBlobAsFile(new Blob(["x"]), "file.bin");
    expect(click).toHaveBeenCalled();
    createElement.mockRestore();
  });

  it("hands in-app browsers a download link instead of a blob", async () => {
    vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) MicroMessenger/8.0.50",
    );
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click");
    const blob = new Blob(["x"]);

    await saveBlobAsFile(blob, "report.docx");
    expect(shareDownloadLink).toHaveBeenCalledWith(blob, "report.docx");
    expect(click).not.toHaveBeenCalled();
  });
});

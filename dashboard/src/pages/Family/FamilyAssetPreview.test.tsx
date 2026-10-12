import { fireEvent, render, screen } from "@testing-library/react";
import { App } from "antd";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FamilyAssetPreview from "./FamilyAssetPreview";

const { getAssetContentUrl, getAssetThumbnailUrl } = vi.hoisted(() => ({
  getAssetContentUrl: vi.fn(
    () => "https://server.test/video.mp4?access_token=short",
  ),
  getAssetThumbnailUrl: vi.fn(
    () => "https://server.test/thumb.webp?access_token=short",
  ),
}));

vi.mock("antd", async (importOriginal) => {
  await importOriginal<typeof import("antd")>();
  const React = await import("react");
  const container =
    (tag: string) =>
    ({ children, ...props }: { children?: ReactNode }) =>
      React.createElement(tag, props, children);
  const AppMock = Object.assign(container("div"), {
    useApp: () => ({ message: { error: vi.fn() } }),
  });
  return {
    App: AppMock,
    Button: container("button"),
    Image: container("img"),
    Modal: ({ children, open }: { children?: ReactNode; open?: boolean }) =>
      open ? React.createElement("div", null, children) : null,
    Progress: ({ percent }: { percent: number }) =>
      React.createElement("span", null, `${percent}%`),
    Space: container("div"),
    Spin: ({ children }: { children?: ReactNode }) =>
      React.createElement("div", null, children),
    Typography: { Text: container("span") },
  };
});

vi.mock("../../api/modules/homeMindFamily", () => ({
  homeMindFamilyApi: { getAssetContentUrl, getAssetThumbnailUrl },
}));

describe("FamilyAssetPreview", () => {
  beforeEach(() => {
    getAssetContentUrl.mockClear();
    getAssetThumbnailUrl.mockClear();
  });

  it("gives the video element a direct range-capable URL", () => {
    render(
      <App>
        <FamilyAssetPreview
          familyId="family-1"
          asset={{ id: "video-1", name: "trip.mp4", asset_type: "VIDEO" }}
        />
      </App>,
    );

    fireEvent.click(screen.getByRole("button", { name: "trip.mp4" }));
    expect(document.querySelector("video")).toHaveAttribute(
      "src",
      "https://server.test/video.mp4?access_token=short",
    );
    expect(getAssetContentUrl).toHaveBeenCalledWith("family-1", "video-1");
  });
});

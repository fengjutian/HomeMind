import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { App } from "antd";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FamilyAssetPreview from "./FamilyAssetPreview";

const { getAssetContent, getAssetContentUrl, getAssetThumbnailUrl } =
  vi.hoisted(() => ({
    getAssetContent: vi.fn(() => Promise.resolve(new Blob(["document"]))),
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

vi.mock("@videojs/react/video", async () => {
  const React = await import("react");
  return {
    Video: (props: React.VideoHTMLAttributes<HTMLVideoElement>) =>
      React.createElement("video", props),
    VideoPlayer: ({ children }: { children?: ReactNode }) =>
      React.createElement("div", null, children),
    VideoSkin: ({ children }: { children?: ReactNode }) =>
      React.createElement("div", null, children),
  };
});

vi.mock("@videojs/react/audio", async () => {
  const React = await import("react");
  return {
    Audio: (props: React.AudioHTMLAttributes<HTMLAudioElement>) =>
      React.createElement("audio", props),
    AudioPlayer: ({ children }: { children?: ReactNode }) =>
      React.createElement("div", null, children),
    AudioSkin: ({ children }: { children?: ReactNode }) =>
      React.createElement("div", null, children),
  };
});

vi.mock("../../api/modules/homeMindFamily", () => ({
  homeMindFamilyApi: {
    getAssetContent,
    getAssetContentUrl,
    getAssetThumbnailUrl,
  },
}));

vi.mock("../../components/DocumentPreviewCore", () => ({
  default: ({ filename }: { filename: string }) => (
    <div data-testid="document-preview">{filename}</div>
  ),
}));

describe("FamilyAssetPreview", () => {
  beforeEach(() => {
    getAssetContentUrl.mockClear();
    getAssetThumbnailUrl.mockClear();
    getAssetContent.mockClear();
  });

  it("gives the video element a direct range-capable URL", async () => {
    render(
      <App>
        <FamilyAssetPreview
          familyId="family-1"
          asset={{ id: "video-1", name: "trip.mp4", asset_type: "VIDEO" }}
        />
      </App>,
    );

    fireEvent.click(screen.getByRole("button", { name: "trip.mp4" }));
    await waitFor(() =>
      expect(document.querySelector("video")).toHaveAttribute(
        "src",
        "https://server.test/video.mp4?access_token=short",
      ),
    );
    expect(getAssetContentUrl).toHaveBeenCalledWith("family-1", "video-1");
  });

  it("opens the shared document preview for modern Office files", () => {
    render(
      <App>
        <FamilyAssetPreview
          familyId="family-1"
          asset={{
            id: "document-1",
            name: "plan.docx",
            asset_type: "DOCUMENT",
          }}
        />
      </App>,
    );

    fireEvent.click(screen.getByRole("button", { name: "Preview plan.docx" }));

    expect(screen.getByTestId("document-preview")).toHaveTextContent(
      "plan.docx",
    );
    expect(
      screen.getByRole("button", { name: "Download plan.docx" }),
    ).toBeInTheDocument();
  });

  it("offers download without preview for legacy Word files", () => {
    render(
      <App>
        <FamilyAssetPreview
          familyId="family-1"
          asset={{
            id: "document-2",
            name: "legacy.doc",
            asset_type: "DOCUMENT",
          }}
        />
      </App>,
    );

    expect(
      screen.queryByRole("button", { name: "Preview legacy.doc" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Download legacy.doc" }),
    ).toBeInTheDocument();
  });
});

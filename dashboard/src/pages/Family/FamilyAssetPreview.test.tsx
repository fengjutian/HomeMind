import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { App } from "antd";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FamilyAssetPreview from "./FamilyAssetPreview";

const { getAssetContent } = vi.hoisted(() => ({
  getAssetContent: vi.fn(),
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
    Spin: container("div"),
    Typography: { Text: container("span") },
  };
});

vi.mock("../../api/modules/homeMindFamily", () => ({
  homeMindFamilyApi: { getAssetContent },
}));

describe("FamilyAssetPreview", () => {
  beforeEach(() => getAssetContent.mockReset());

  it("shows byte progress while a video is transferred", async () => {
    let reportProgress: ((loaded: number, total: number) => void) | undefined;
    getAssetContent.mockImplementation(
      (
        _familyId: string,
        _assetId: string,
        onProgress?: (loaded: number, total: number) => void,
      ) => {
        reportProgress = onProgress;
        return new Promise<Blob>(() => undefined);
      },
    );

    render(
      <App>
        <FamilyAssetPreview
          familyId="family-1"
          asset={{ id: "video-1", name: "trip.mp4", asset_type: "VIDEO" }}
        />
      </App>,
    );

    fireEvent.click(screen.getByRole("button", { name: "trip.mp4" }));
    await waitFor(() => expect(reportProgress).toBeDefined());
    act(() => reportProgress?.(5 * 1024 * 1024, 10 * 1024 * 1024));

    expect(await screen.findByText("50%")).toBeInTheDocument();
    expect(screen.getByText("5.00 MB / 10.0 MB")).toBeInTheDocument();
  });
});

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const getServerAddress = vi.fn();

vi.mock("../../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));

vi.mock("../../api/modules/auth", () => ({
  authApi: {
    getAuthStatus: () => Promise.resolve({ setup_required: false }),
    getOauthStatus: () => Promise.resolve({ providers: [] }),
    getCaptcha: () => Promise.resolve({ provider: "none" }),
    getServerAddress: () => getServerAddress(),
  },
}));

vi.mock("../../utils/locale", () => ({
  applyGuestLocale: () => Promise.resolve(),
  applyUserLocale: () => Promise.resolve(),
}));

vi.mock("./CaptchaField", () => ({
  default: () => null,
}));

import LoginPage from "./index";
import { buildScanUrl } from "./LoginQrPanel";

/** Minimal stand-in for the fields buildScanUrl reads. */
function loc(href: string) {
  const url = new URL(href);
  return {
    hostname: url.hostname,
    port: url.port,
    protocol: url.protocol,
    origin: url.origin,
  } as Location;
}

function renderLogin() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

describe("buildScanUrl", () => {
  it("points at the dev server, not the page's own port", () => {
    // Opened through the API on 8088 — the phone still needs the dev server.
    expect(buildScanUrl("192.168.2.191", loc("http://192.168.2.191:8088/"))).toBe(
      `http://192.168.2.191:${DEV_SERVER_PORT}`,
    );
  });

  it("keeps the LAN host when the page is on loopback", () => {
    expect(buildScanUrl("192.168.2.191", loc("http://localhost:5173/"))).toBe(
      `http://192.168.2.191:${DEV_SERVER_PORT}`,
    );
  });

  it("falls back to the current host when no LAN address is known", () => {
    expect(buildScanUrl(null, loc("http://localhost:8088/"))).toBe(
      `http://localhost:${DEV_SERVER_PORT}`,
    );
  });
});

describe("Login LAN QR code", () => {
  // Each render would otherwise stack on the previous one, and getByText would
  // match the earlier panel instead of this one.
  afterEach(() => {
    cleanup();
  });

  it("renders the detected LAN address on the dev port", async () => {
    getServerAddress.mockResolvedValue({
      url: "http://192.168.2.191:8088",
      port: 8088,
      host: "192.168.2.191",
      is_lan: true,
    });

    const { container } = renderLogin();

    // The panel must never blank out on a detection hiccup.
    await waitFor(() => {
      expect(screen.getByTestId("login-qr-panel")).toBeInTheDocument();
    });
    expect(container.textContent).toContain(
      `http://192.168.2.191:${DEV_SERVER_PORT}`,
    );
  });

  it("falls back to the current host when detection fails", async () => {
    getServerAddress.mockRejectedValue(new Error("offline"));

    const { container } = renderLogin();

    await waitFor(() => {
      expect(screen.getByTestId("login-qr-panel")).toBeInTheDocument();
    });
    expect(container.textContent).toContain(window.location.hostname);
  });
});
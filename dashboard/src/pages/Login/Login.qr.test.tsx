import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

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

function renderLogin() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
}

describe("Login LAN QR code", () => {
  it("encodes the LAN address reported by the server", async () => {
    getServerAddress.mockResolvedValue({
      url: "http://192.168.1.23:8088",
      port: 8088,
      host: "192.168.1.23",
      is_lan: true,
    });

    renderLogin();

    // A QR code only helps if the phone can open the URL, so the LAN address
    // must be shown as text next to it.
    await waitFor(() => {
      expect(screen.getByText("http://192.168.1.23:8088")).toBeInTheDocument();
    });
  });

  it("falls back to the current origin when detection fails", async () => {
    getServerAddress.mockRejectedValue(new Error("offline"));

    renderLogin();

    // Never leave the QR area blank on a failure — an unrendered panel would
    // silently hide the address from the user.
    await waitFor(() => {
      expect(screen.getByTestId("login-qr-panel")).toBeInTheDocument();
    });
    expect(screen.getByText(window.location.origin)).toBeInTheDocument();
  });
});

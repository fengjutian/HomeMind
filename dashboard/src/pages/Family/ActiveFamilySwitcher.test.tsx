import { render, waitFor } from "@testing-library/react";
import { App } from "antd";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ActiveFamilySwitcher from "./ActiveFamilySwitcher";

const { useActiveFamily } = vi.hoisted(() => ({
  useActiveFamily: vi.fn(),
}));

vi.mock("../../hooks/useActiveFamily", () => ({ useActiveFamily }));

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (_key: string, fallback: string) => fallback }),
}));

describe("ActiveFamilySwitcher", () => {
  beforeEach(() => useActiveFamily.mockReset());

  it("does not clear the parent selection while active family is loading", async () => {
    const onChange = vi.fn();
    useActiveFamily.mockReturnValue({
      familyId: null,
      loading: true,
      setFamily: vi.fn(),
      clear: vi.fn(),
      refresh: vi.fn(),
    });

    render(
      <App>
        <ActiveFamilySwitcher families={[]} onChange={onChange} />
      </App>,
    );

    await waitFor(() => expect(onChange).not.toHaveBeenCalled());
  });
});

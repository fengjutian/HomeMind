import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../request", () => ({ request }));

import { homemindActiveFamilyApi } from "./homemindActiveFamily";

describe("homemindActiveFamilyApi", () => {
  beforeEach(() => request.mockReset());

  it("lets the shared request layer add the /api prefix", async () => {
    request.mockResolvedValue({ family_id: null });

    await homemindActiveFamilyApi.get();

    expect(request).toHaveBeenCalledWith("/homemind/me/active-family");
  });
});

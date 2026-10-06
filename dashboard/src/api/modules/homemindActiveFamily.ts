import { request } from "../request";

const root = "/api/homemind/me/active-family";

export interface ActiveFamilyResponse {
  family_id: string | null;
}

export const homemindActiveFamilyApi = {
  get: () => request<ActiveFamilyResponse>(root),
  set: (familyId: string) =>
    request<ActiveFamilyResponse>(root, {
      method: "PUT",
      body: JSON.stringify({ family_id: familyId }),
    }),
  clear: () =>
    request<ActiveFamilyResponse>(root, { method: "DELETE" }),
};

export type ActiveFamilyApi = typeof homemindActiveFamilyApi;

import { request } from "../request";

export interface HomeMindFamily {
  id: string;
  name: string;
  avatar: string | null;
  owner_user_id: number;
  timezone: string;
  locale: string;
  created_at: number;
  updated_at: number;
}

export interface FamilyMember {
  id: string;
  family_id: string;
  user_id: number | null;
  display_name: string;
  role: string;
  avatar: string | null;
  birthday: string | null;
  status: string;
}

export interface FamilyAlbum {
  id: string;
  family_id: string;
  name: string;
  description: string;
  cover_asset_id: string | null;
  created_at: number;
}

export interface FamilyTask {
  id: string;
  family_id: string;
  title: string;
  description: string;
  status: string;
  assigned_member_id: string | null;
  due_at: number | null;
  created_at: number;
}

export interface FamilyMemory {
  id: string;
  family_id: string;
  content: string;
  memory_type: string;
  importance: number;
  visibility: string;
  status: string;
  created_at: number;
}

export interface FamilySearchResult {
  kind: string;
  id: string;
  title: string;
  snippet: string;
  score: number;
  metadata: Record<string, unknown>;
}

const root = "/homemind/families";
const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});

export const homeMindFamilyApi = {
  listFamilies: () => request<HomeMindFamily[]>(root),
  createFamily: (body: { name: string; timezone?: string; locale?: string }) =>
    request<HomeMindFamily>(root, json(body)),
  listMembers: (familyId: string) =>
    request<FamilyMember[]>(`${root}/${familyId}/members`),
  createMember: (familyId: string, body: { display_name: string; role: string }) =>
    request<FamilyMember>(`${root}/${familyId}/members`, json(body)),
  listAlbums: (familyId: string) =>
    request<FamilyAlbum[]>(`${root}/${familyId}/albums`),
  createAlbum: (familyId: string, body: { name: string; description?: string }) =>
    request<FamilyAlbum>(`${root}/${familyId}/albums`, json(body)),
  listTasks: (familyId: string) =>
    request<FamilyTask[]>(`${root}/${familyId}/tasks`),
  createTask: (familyId: string, body: { title: string; description?: string }) =>
    request<FamilyTask>(`${root}/${familyId}/tasks`, json(body)),
  listMemories: (familyId: string, query = "") =>
    request<FamilyMemory[]>(
      `${root}/${familyId}/memories${query ? `?query=${encodeURIComponent(query)}` : ""}`,
    ),
  createMemory: (familyId: string, content: string) =>
    request<FamilyMemory>(
      `${root}/${familyId}/memories`,
      json({
        subject_type: "FAMILY",
        content,
        memory_type: "FACT",
        importance: 0.5,
        confidence: 0.8,
        visibility: "FAMILY",
        source_type: "MANUAL",
      }),
    ),
  search: (familyId: string, query: string) =>
    request<FamilySearchResult[]>(
      `${root}/${familyId}/search?query=${encodeURIComponent(query)}`,
    ),
};

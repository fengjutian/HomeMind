import { request, requestBlob } from "../request";

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
  subject_type: string;
  subject_id: string | null;
  content: string;
  memory_type: string;
  importance: number;
  confidence: number;
  visibility: string;
  source_type: string;
  source_id: string | null;
  expires_at: number | null;
  status: string;
  created_at: number;
  updated_at: number;
}

export interface FamilySearchResult {
  kind: string;
  id: string;
  title: string;
  snippet: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface FamilyRelationship {
  id: string;
  family_id: string;
  from_member_id: string;
  to_member_id: string;
  relationship_type: string;
  created_at: number;
  updated_at: number;
}

export interface FamilyInvite {
  id: string;
  family_id: string;
  role: string;
  display_name: string;
  created_by: number;
  created_at: number;
  expires_at: number;
  redeemed_at: number | null;
  redeemed_by: number | null;
}

export interface FamilyInviteCreateResponse {
  invite_id: string;
  token: string;
  expires_at: number;
}

export interface FamilyInviteRedeemResponse {
  member_id: string;
  family_id: string;
  role: string;
  display_name: string;
}

export interface FamilyDevice {
  id: string;
  family_id: string;
  name: string;
  device_type: string;
  platform: string | null;
  status: string;
  address: string | null;
  capabilities: string[];
  last_seen: number | null;
  created_at: number;
  updated_at: number;
  // Stage 5: the device's authorized filesystem root and the runtime
  // build it last reported on a heartbeat.
  root_path?: string | null;
  runtime_version?: string | null;
}

export interface FamilyDevicePairingResponse {
  code: string;
  expires_at: number;
  device_name: string;
  device_type: string;
}

export interface FamilyDeviceCommand {
  id: string;
  device_id: string;
  family_id: string;
  capability: string;
  payload: Record<string, unknown>;
  requested_by: number;
  transaction_id: string | null;
  expires_at: number;
  status: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: number;
  updated_at: number;
}

export interface FamilyEvent {
  id: string;
  family_id: string;
  event_type: string;
  title: string;
  start_at: number;
  end_at: number;
  location: string | null;
  description: string;
  created_at: number;
  updated_at: number;
}

export interface FamilyAsset {
  id: string;
  family_id: string;
  asset_type: string;
  name: string;
  uri: string;
  mime_type: string;
  size_bytes: number;
  captured_at: number | null;
  status: string;
}

export interface FamilySpace {
  id: string;
  family_id: string;
  name: string;
  space_type: string;
  owner_member_id: string | null;
  created_at: number;
  updated_at: number;
}

export interface FamilyPermission {
  id: string;
  family_id: string;
  subject_member_id: string | null;
  space_id: string | null;
  action: string;
  effect: string;
  expires_at: number | null;
  created_at: number;
  updated_at: number;
}

export interface FamilyAssetSource {
  id: string;
  family_id: string;
  space_id: string | null;
  directory_uri: string;
  recursive: boolean;
  visibility: string;
  status: string;
  last_scanned_at: number | null;
}

export interface FamilyAssetScanResult {
  source_id: string;
  indexed: number;
  unchanged: number;
  skipped: number;
  failed: number;
  missing: number;
  asset_ids: string[];
  errors: string[];
}

export interface PhotoIntelligence {
  asset_id: string;
  family_id: string;
  description: string;
  objects: string[];
  scenes: string[];
  faces: Array<Record<string, unknown>>;
  location_name: string | null;
  perceptual_hash: string | null;
  has_embedding: boolean;
  analyzed_at: number;
}

export interface SimilarPhoto {
  asset_id: string;
  hamming_distance: number;
}

export interface FamilyApproval {
  id: string;
  transaction_id: string;
  family_id: string;
  status: string;
  requested_by: number;
  decided_by: number | null;
  reason: string | null;
  created_at: number;
  decided_at: number | null;
}

export interface FamilyTransaction {
  id: string;
  family_id: string;
  requested_by: number;
  action: string;
  payload_json: string;
  status: string;
  result_json: string | null;
  error: string | null;
  created_at: number;
  updated_at: number;
  // Stage-4 additions: the approval page renders preview, verification
  // and the lease fields from the same row.
  preview_json?: string | null;
  verification_json?: string | null;
  attempt_count?: number;
  approved_at?: number | null;
  executed_at?: number | null;
  verified_at?: number | null;
  cancelled_at?: number | null;
  lease_expires_at?: number | null;
}

export interface FamilyAudit {
  id: string;
  family_id: string;
  user_id: number;
  transaction_id: string | null;
  action: string;
  target: string | null;
  result: string;
  approval: string | null;
  detail_json: string;
  created_at: number;
}

export interface PhotoProvider {
  id: number;
  name: string;
  enabled: boolean;
  models: Array<{ id: string; name?: string }>;
}

export interface FamilyFilesystemEntry {
  path: string;
  kind: "file" | "directory";
  size_bytes: number | null;
}

export interface FilesystemMutationResult {
  transaction: FamilyTransaction;
  approval: FamilyApproval | null;
}

const root = "/homemind/families";
const json = (body: unknown): RequestInit => ({
  method: "POST",
  body: JSON.stringify(body),
});
const mutate = (method: "PATCH" | "DELETE", body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

export const homeMindFamilyApi = {
  listPhotoProviders: () => request<PhotoProvider[]>("/providers"),
  listFamilies: () => request<HomeMindFamily[]>(root),
  createFamily: (body: { name: string; timezone?: string; locale?: string }) =>
    request<HomeMindFamily>(root, json(body)),
  updateFamily: (
    familyId: string,
    body: Partial<Pick<HomeMindFamily, "name" | "timezone" | "locale">>,
  ) => request<HomeMindFamily>(`${root}/${familyId}`, mutate("PATCH", body)),
  deleteFamily: (familyId: string) =>
    request<void>(`${root}/${familyId}`, mutate("DELETE")),
  listMembers: (familyId: string) =>
    request<FamilyMember[]>(`${root}/${familyId}/members`),
  createMember: (
    familyId: string,
    body: { display_name: string; role: string },
  ) => request<FamilyMember>(`${root}/${familyId}/members`, json(body)),
  updateMember: (
    familyId: string,
    memberId: string,
    body: Partial<
      Pick<FamilyMember, "display_name" | "role" | "birthday" | "status">
    >,
  ) =>
    request<FamilyMember>(
      `${root}/${familyId}/members/${memberId}`,
      mutate("PATCH", body),
    ),
  deleteMember: (familyId: string, memberId: string) =>
    request<void>(`${root}/${familyId}/members/${memberId}`, mutate("DELETE")),
  listRelationships: (familyId: string) =>
    request<FamilyRelationship[]>(`${root}/${familyId}/relationships`),
  createRelationship: (
    familyId: string,
    body: Pick<
      FamilyRelationship,
      "from_member_id" | "to_member_id" | "relationship_type"
    >,
  ) =>
    request<FamilyRelationship>(
      `${root}/${familyId}/relationships`,
      json(body),
    ),
  deleteRelationship: (familyId: string, relationshipId: string) =>
    request<void>(
      `${root}/${familyId}/relationships/${relationshipId}`,
      mutate("DELETE"),
    ),
  listInvites: (familyId: string, includeRedeemed: boolean = false) =>
    request<FamilyInvite[]>(
      `${root}/${familyId}/invites${
        includeRedeemed ? "?include_redeemed=true" : ""
      }`,
    ),
  createInvite: (
    familyId: string,
    body: {
      display_name: string;
      role?: string;
      ttl_seconds?: number;
    },
  ) =>
    request<FamilyInviteCreateResponse>(
      `${root}/${familyId}/invites`,
      json(body),
    ),
  revokeInvite: (familyId: string, inviteId: string) =>
    request<void>(
      `${root}/${familyId}/invites/${inviteId}`,
      mutate("DELETE"),
    ),
  redeemInvite: (body: { token: string }) =>
    request<FamilyInviteRedeemResponse>(
      `${root}/invites/redeem`,
      json(body),
    ),
  listDevices: (familyId: string) =>
    request<FamilyDevice[]>(`${root}/${familyId}/devices`),
  getDevice: (familyId: string, deviceId: string) =>
    request<FamilyDevice>(`${root}/${familyId}/devices/${deviceId}`),
  createDevicePairing: (
    familyId: string,
    body: {
      name: string;
      device_type: string;
      platform?: string;
      capabilities?: string[];
    },
  ) =>
    request<FamilyDevicePairingResponse>(
      `${root}/${familyId}/devices`,
      json(body),
    ),
  updateDevice: (
    familyId: string,
    deviceId: string,
    body: {
      name?: string;
      platform?: string;
      capabilities?: string[];
    },
  ) =>
    request<FamilyDevice>(
      `${root}/${familyId}/devices/${deviceId}`,
      mutate("PATCH", body),
    ),
  deleteDevice: (familyId: string, deviceId: string) =>
    request<void>(
      `${root}/${familyId}/devices/${deviceId}`,
      mutate("DELETE"),
    ),
  rotateDeviceToken: (familyId: string, deviceId: string) =>
    request<{ token: string }>(
      `${root}/${familyId}/devices/${deviceId}/rotate-token`,
      { method: "POST" },
    ),
  revokeDeviceToken: (familyId: string, deviceId: string) =>
    request<void>(
      `${root}/${familyId}/devices/${deviceId}/revoke-token`,
      { method: "POST" },
    ),
  listDeviceCommands: (familyId: string, deviceId: string) =>
    request<FamilyDeviceCommand[]>(
      `${root}/${familyId}/devices/${deviceId}/commands`,
    ),
  enqueueDeviceCommand: (
    familyId: string,
    deviceId: string,
    body: {
      capability: string;
      payload?: Record<string, unknown>;
      expires_in_seconds?: number;
      transaction_id?: string;
    },
  ) =>
    request<FamilyDeviceCommand>(
      `${root}/${familyId}/devices/${deviceId}/commands`,
      json(body),
    ),
  listSpaces: (familyId: string) =>
    request<FamilySpace[]>(`${root}/${familyId}/spaces`),
  createSpace: (
    familyId: string,
    body: { name: string; space_type: string; owner_member_id?: string },
  ) => request<FamilySpace>(`${root}/${familyId}/spaces`, json(body)),
  deleteSpace: (familyId: string, spaceId: string) =>
    request<void>(`${root}/${familyId}/spaces/${spaceId}`, mutate("DELETE")),
  listPermissions: (familyId: string) =>
    request<FamilyPermission[]>(`${root}/${familyId}/permissions`),
  createPermission: (
    familyId: string,
    body: {
      subject_member_id?: string;
      space_id?: string;
      action: string;
      effect: string;
    },
  ) => request<FamilyPermission>(`${root}/${familyId}/permissions`, json(body)),
  deletePermission: (familyId: string, permissionId: string) =>
    request<void>(
      `${root}/${familyId}/permissions/${permissionId}`,
      mutate("DELETE"),
    ),
  listAlbums: (familyId: string) =>
    request<FamilyAlbum[]>(`${root}/${familyId}/albums`),
  createAlbum: (
    familyId: string,
    body: { name: string; description?: string },
  ) => request<FamilyAlbum>(`${root}/${familyId}/albums`, json(body)),
  listAlbumAssets: (familyId: string, albumId: string) =>
    request<string[]>(`${root}/${familyId}/albums/${albumId}/assets`),
  addAlbumAsset: (familyId: string, albumId: string, assetId: string) =>
    request<void>(
      `${root}/${familyId}/albums/${albumId}/assets`,
      json({ asset_id: assetId }),
    ),
  removeAlbumAsset: (familyId: string, albumId: string, assetId: string) =>
    request<void>(
      `${root}/${familyId}/albums/${albumId}/assets/${assetId}`,
      mutate("DELETE"),
    ),
  listAssets: (
    familyId: string,
    filters?: { query?: string; asset_type?: string; space_id?: string },
  ) => {
    const params = new URLSearchParams();
    if (filters?.query) params.set("query", filters.query);
    if (filters?.asset_type) params.set("asset_type", filters.asset_type);
    if (filters?.space_id) params.set("space_id", filters.space_id);
    const suffix = params.size ? `?${params.toString()}` : "";
    return request<FamilyAsset[]>(`${root}/${familyId}/assets${suffix}`);
  },
  scanAssets: (
    familyId: string,
    body: {
      directory: string;
      space_id?: string;
      recursive: boolean;
      visibility: string;
    },
  ) =>
    request<FamilyAssetScanResult>(
      `${root}/${familyId}/assets/scan`,
      json(body),
    ),
  listAssetSources: (familyId: string) =>
    request<FamilyAssetSource[]>(`${root}/${familyId}/asset-sources`),
  rescanAssetSource: (familyId: string, sourceId: string) =>
    request<FamilyAssetScanResult>(
      `${root}/${familyId}/asset-sources/${sourceId}/scan`,
      json({}),
    ),
  listDuplicateAssets: (familyId: string) =>
    request<FamilyAsset[][]>(`${root}/${familyId}/assets/duplicates`),
  deleteAssetIndex: (familyId: string, assetId: string) =>
    request<void>(`${root}/${familyId}/assets/${assetId}`, mutate("DELETE")),
  getAssetContent: (familyId: string, assetId: string) =>
    requestBlob(`${root}/${familyId}/assets/${assetId}/content`),
  analyzePhotoLocal: (familyId: string, assetId: string) =>
    request<PhotoIntelligence>(
      `${root}/${familyId}/photos/${assetId}/analyze-local`,
      json({}),
    ),
  analyzePhoto: (
    familyId: string,
    assetId: string,
    body: {
      vision_provider_id: number;
      vision_model: string;
      embedding_provider_id?: number;
      embedding_model?: string;
      reverse_geocode: boolean;
      recognize_faces: boolean;
    },
  ) =>
    request<PhotoIntelligence>(
      `${root}/${familyId}/photos/${assetId}/analyze`,
      json(body),
    ),
  setFaceReference: (familyId: string, assetId: string, memberId: string) =>
    request<void>(`${root}/${familyId}/photos/${assetId}/face-reference`, {
      method: "PUT",
      body: JSON.stringify({ member_id: memberId }),
    }),
  deleteFaceReference: (familyId: string, assetId: string) =>
    request<void>(
      `${root}/${familyId}/photos/${assetId}/face-reference`,
      mutate("DELETE"),
    ),
  listSimilarPhotos: (familyId: string, assetId: string, maxDistance = 8) =>
    request<SimilarPhoto[]>(
      `${root}/${familyId}/photos/${assetId}/similar?max_distance=${maxDistance}`,
    ),
  listApprovals: (familyId: string, status: string) =>
    request<FamilyApproval[]>(
      `${root}/${familyId}/approvals?status=${encodeURIComponent(status)}`,
    ),
  decideApproval: (
    familyId: string,
    approvalId: string,
    decision: "approve" | "reject",
    reason?: string,
  ) =>
    request<FamilyTransaction>(
      `${root}/${familyId}/approvals/${approvalId}/${decision}`,
      json({ reason }),
    ),
  listTransactions: (familyId: string, status?: string) =>
    request<FamilyTransaction[]>(
      status
        ? `${root}/${familyId}/transactions?status=${encodeURIComponent(status)}`
        : `${root}/${familyId}/transactions`,
    ),
  getTransaction: (familyId: string, transactionId: string) =>
    request<FamilyTransaction>(
      `${root}/${familyId}/transactions/${transactionId}`,
    ),
  listAudit: (familyId: string) =>
    request<FamilyAudit[]>(`${root}/${familyId}/audit-log`),
  listDirectory: (familyId: string, sourceId: string, path = ".") =>
    request<FamilyFilesystemEntry[]>(
      `${root}/${familyId}/filesystem?source_id=${encodeURIComponent(
        sourceId,
      )}&path=${encodeURIComponent(path)}`,
    ),
  searchDirectory: (
    familyId: string,
    sourceId: string,
    query: string,
    path = ".",
  ) =>
    request<FamilyFilesystemEntry[]>(
      `${root}/${familyId}/filesystem/search?source_id=${encodeURIComponent(
        sourceId,
      )}&path=${encodeURIComponent(path)}&query=${encodeURIComponent(query)}`,
    ),
  readFile: (familyId: string, sourceId: string, path: string) =>
    request<{ path: string; content: string }>(
      `${root}/${familyId}/filesystem/read?source_id=${encodeURIComponent(
        sourceId,
      )}&path=${encodeURIComponent(path)}`,
    ),
  mutateFile: (
    familyId: string,
    body: {
      action:
        | "filesystem.copy"
        | "filesystem.move"
        | "filesystem.rename"
        | "filesystem.delete";
      source_id: string;
      path: string;
      destination?: string;
    },
  ) =>
    request<FilesystemMutationResult>(
      `${root}/${familyId}/filesystem/actions`,
      json(body),
    ),
  listTasks: (familyId: string) =>
    request<FamilyTask[]>(`${root}/${familyId}/tasks`),
  createTask: (
    familyId: string,
    body: {
      title: string;
      description?: string;
      assigned_member_id?: string;
      due_at?: number;
    },
  ) => request<FamilyTask>(`${root}/${familyId}/tasks`, json(body)),
  updateTask: (
    familyId: string,
    taskId: string,
    body: Partial<
      Pick<
        FamilyTask,
        "title" | "description" | "status" | "assigned_member_id" | "due_at"
      >
    >,
  ) =>
    request<FamilyTask>(
      `${root}/${familyId}/tasks/${taskId}`,
      mutate("PATCH", body),
    ),
  deleteTask: (familyId: string, taskId: string) =>
    request<void>(`${root}/${familyId}/tasks/${taskId}`, mutate("DELETE")),
  listEvents: (familyId: string) =>
    request<FamilyEvent[]>(`${root}/${familyId}/events`),
  createEvent: (
    familyId: string,
    body: {
      event_type: string;
      title: string;
      start_at: number;
      end_at: number;
      location?: string;
      description?: string;
      metadata?: Record<string, unknown>;
    },
  ) => request<FamilyEvent>(`${root}/${familyId}/events`, json(body)),
  updateEvent: (
    familyId: string,
    eventId: string,
    body: Partial<{
      event_type: string;
      title: string;
      start_at: number;
      end_at: number;
      location: string | null;
      description: string;
      metadata: Record<string, unknown>;
    }>,
  ) =>
    request<FamilyEvent>(
      `${root}/${familyId}/events/${eventId}`,
      mutate("PATCH", body),
    ),
  deleteEvent: (familyId: string, eventId: string) =>
    request<void>(`${root}/${familyId}/events/${eventId}`, mutate("DELETE")),
  listMemories: (familyId: string, query = "") =>
    request<FamilyMemory[]>(
      `${root}/${familyId}/memories${
        query ? `?query=${encodeURIComponent(query)}` : ""
      }`,
    ),
  createMemory: (
    familyId: string,
    body: {
      content: string;
      subject_type?: "FAMILY" | "MEMBER" | "EVENT" | "ASSET";
      subject_id?: string;
      memory_type?: string;
      importance?: number;
      confidence?: number;
      visibility?: "PUBLIC" | "FAMILY" | "PRIVATE" | "SENSITIVE";
      source_type?: string;
      expires_at?: number;
    },
  ) =>
    request<FamilyMemory>(
      `${root}/${familyId}/memories`,
      json({
        subject_type: "FAMILY",
        memory_type: "FACT",
        importance: 0.5,
        confidence: 0.8,
        visibility: "FAMILY",
        source_type: "MANUAL",
        ...body,
      }),
    ),
  updateMemory: (
    familyId: string,
    memoryId: string,
    body: Partial<{
      content: string;
      memory_type: string;
      importance: number;
      confidence: number;
      visibility: "PUBLIC" | "FAMILY" | "PRIVATE" | "SENSITIVE";
      expires_at: number | null;
      status: "ACTIVE" | "DISABLED" | "ARCHIVED";
    }>,
  ) =>
    request<FamilyMemory>(
      `${root}/${familyId}/memories/${memoryId}`,
      mutate("PATCH", body),
    ),
  deleteMemory: (familyId: string, memoryId: string) =>
    request<void>(
      `${root}/${familyId}/memories/${memoryId}`,
      mutate("DELETE"),
    ),
  searchMemoriesFts: (familyId: string, query: string, limit = 20) =>
    request<FamilyMemory[]>(
      `${root}/${familyId}/memories/search?q=${encodeURIComponent(query)}&limit=${limit}`,
    ),
  search: (familyId: string, query: string) =>
    request<FamilySearchResult[]>(
      `${root}/${familyId}/search?query=${encodeURIComponent(query)}`,
    ),
};

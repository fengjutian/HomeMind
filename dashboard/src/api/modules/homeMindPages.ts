/**
 * API module for the standalone HomeMind pages (Stage 8).
 *
 * Every page reads the active family from `useActiveFamily`, so
 * switching families in the header re-renders all of them without any
 * per-page bookkeeping.
 */
import { request } from "../request";

const root = "/api/homemind/families";

export interface DashboardSummary {
  family_id: string;
  family_name: string;
  timezone: string;
  member_count: number;
  today_task_count: number;
  open_task_count: number;
  pending_approval_count: number;
  pending_memory_candidate_count: number;
  upcoming_events: {
    id: string;
    title: string;
    event_type: string;
    start_at: number;
    end_at: number;
    location: string | null;
  }[];
  recent_memories: {
    id: string;
    content: string;
    memory_type: string;
    importance: number;
  }[];
  recent_assets: {
    id: string;
    name: string;
    asset_type: string;
    captured_at: number | null;
  }[];
  devices: { total: number; online: number; offline: number };
  recent_jobs: {
    id: string;
    job_type: string;
    status: string;
    total_items: number;
    processed_items: number;
    failed_items: number;
  }[];
}

export interface AssetJob {
  job_id: string;
  family_id: string;
  job_type: string;
  status: string;
  total_items: number;
  processed_items: number;
  succeeded_items: number;
  skipped_items: number;
  failed_items: number;
  progress_percent: number;
  error_summary: string | null;
}

export interface AssetJobItem {
  item_id: string;
  asset_id: string | null;
  source_path: string;
  status: string;
  attempt_count: number;
  error: string | null;
}

/**
 * A proposed face match awaiting a manager's decision.
 *
 * `status` stays PENDING until someone confirms or rejects it. A pending
 * candidate is a suggestion, not an identity, and the UI must present it
 * that way — no auto-accept, whatever the confidence.
 */
export interface FaceCandidate {
  candidate_id: string;
  family_id: string;
  asset_id: string;
  member_id: string;
  confidence: number;
  status: "PENDING" | "CONFIRMED" | "REJECTED";
  decided_by: number | null;
  decided_at: number | null;
  created_at: number;
}

/** Non-sensitive job configuration. Never carries an API key. */
export interface AssetJobConfig {
  vision_provider_id?: number;
  vision_model?: string;
  embedding_provider_id?: number;
  embedding_model?: string;
  geocoder?: string;
  thumbnail_width?: number;
  thumbnail_height?: number;
  thumbnail_format?: "webp" | "jpeg";
}

export const ASSET_JOB_TYPES = [
  "SCAN",
  "METADATA",
  "THUMBNAIL",
  "VISION",
  "EMBEDDING",
  "FACE_MATCH",
  "REINDEX",
] as const;

/** Job types that operate on registered assets rather than raw paths. */
export const ASSET_SCOPED_JOB_TYPES: readonly string[] = [
  "METADATA",
  "THUMBNAIL",
  "VISION",
  "EMBEDDING",
  "FACE_MATCH",
  "REINDEX",
];

/** Job types whose handler must receive a provider + model to run. */
export const PROVIDER_BACKED_JOB_TYPES: readonly string[] = [
  "VISION",
  "EMBEDDING",
];

export interface MemoryCandidate {
  id: string;
  family_id: string;
  subject_type: string;
  subject_id: string | null;
  content: string;
  memory_type: string;
  importance: number;
  confidence: number;
  visibility: string;
  status: string;
  created_at: number;
  reviewed_at: number | null;
  rejection_reason: string | null;
  merged_into: string | null;
}

export interface MemoryEvidence {
  id: string;
  candidate_id: string;
  memory_id: string | null;
  source_type: string;
  source_id: string | null;
  observed_at: number;
  confidence_delta: number;
}

export interface IndexedSearchHit {
  kind: string;
  id: string;
  title: string;
  snippet: string;
  score: number;
  matched_by: string[];
  highlights: string[];
  metadata: Record<string, unknown>;
}

export interface IndexedSearchResponse {
  items: IndexedSearchHit[];
  next_cursor: string | null;
  has_more: boolean;
  total_candidates: number;
}

function mutate(method: "POST" | "PATCH" | "DELETE", body?: unknown) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  } as RequestInit;
}

function qs(params: Record<string, string | number | string[] | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) {
      value.forEach((item) => search.append(key, item));
    } else {
      search.set(key, String(value));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

export const homeMindPagesApi = {
  dashboardSummary: (familyId: string) =>
    request<DashboardSummary>(`${root}/${familyId}/dashboard-summary`),

  // --- asset jobs (Stage 6) ---
  listAssetJobs: (familyId: string, status?: string) =>
    request<AssetJob[]>(`${root}/${familyId}/asset-jobs${qs({ status })}`),
  getAssetJob: (familyId: string, jobId: string) =>
    request<AssetJob>(`${root}/${familyId}/asset-jobs/${jobId}`),
  listAssetJobItems: (
    familyId: string,
    jobId: string,
    opts: { status?: string; limit?: number } = {},
  ) =>
    request<AssetJobItem[]>(
      `${root}/${familyId}/asset-jobs/${jobId}/items${qs(opts)}`,
    ),
  createAssetJob: (
    familyId: string,
    body: {
      job_type: string;
      source_id?: string;
      paths?: string[];
      asset_ids?: string[];
      config?: AssetJobConfig;
    },
  ) => request<AssetJob>(`${root}/${familyId}/asset-jobs`, mutate("POST", body)),
  pauseAssetJob: (familyId: string, jobId: string) =>
    request<AssetJob>(
      `${root}/${familyId}/asset-jobs/${jobId}/pause`,
      mutate("POST"),
    ),
  resumeAssetJob: (familyId: string, jobId: string) =>
    request<AssetJob>(
      `${root}/${familyId}/asset-jobs/${jobId}/resume`,
      mutate("POST"),
    ),
  cancelAssetJob: (familyId: string, jobId: string) =>
    request<AssetJob>(
      `${root}/${familyId}/asset-jobs/${jobId}/cancel`,
      mutate("POST"),
    ),
  retryAssetJob: (familyId: string, jobId: string) =>
    request<AssetJob>(
      `${root}/${familyId}/asset-jobs/${jobId}/retry`,
      mutate("POST"),
    ),
  assetThumbnailUrl: (
    familyId: string,
    assetId: string,
    width = 320,
    height = 320,
  ) =>
    `${root}/${familyId}/assets/${assetId}/thumbnail?width=${width}&height=${height}`,

  // --- face candidates (Stage C4) ---
  listFaceCandidates: (
    familyId: string,
    status: "PENDING" | "CONFIRMED" | "REJECTED" = "PENDING",
  ) =>
    request<FaceCandidate[]>(
      `${root}/${familyId}/face-candidates${qs({ status })}`,
    ),
  confirmFaceCandidate: (familyId: string, candidateId: string) =>
    request<FaceCandidate>(
      `${root}/${familyId}/face-candidates/${candidateId}/confirm`,
      mutate("POST"),
    ),
  rejectFaceCandidate: (familyId: string, candidateId: string) =>
    request<FaceCandidate>(
      `${root}/${familyId}/face-candidates/${candidateId}/reject`,
      mutate("POST"),
    ),

  // --- memory candidates (Stage 3) ---
  listMemoryCandidates: (familyId: string, status?: string) =>
    request<MemoryCandidate[]>(
      `${root}/${familyId}/memory-candidates${qs({ status })}`,
    ),
  getMemoryCandidate: (familyId: string, candidateId: string) =>
    request<MemoryCandidate>(
      `${root}/${familyId}/memory-candidates/${candidateId}`,
    ),
  approveMemoryCandidate: (familyId: string, candidateId: string) =>
    request<{ candidate: MemoryCandidate; memory_id: string }>(
      `${root}/${familyId}/memory-candidates/${candidateId}/approve`,
      mutate("POST"),
    ),
  rejectMemoryCandidate: (
    familyId: string,
    candidateId: string,
    reason?: string,
  ) =>
    request<MemoryCandidate>(
      `${root}/${familyId}/memory-candidates/${candidateId}/reject`,
      mutate("POST", { reason }),
    ),
  mergeMemoryCandidate: (
    familyId: string,
    candidateId: string,
    targetMemoryId: string,
  ) =>
    request<{ candidate: MemoryCandidate; memory_id: string }>(
      `${root}/${familyId}/memory-candidates/${candidateId}/merge`,
      mutate("POST", { target_memory_id: targetMemoryId }),
    ),
  listMemoryEvidence: (familyId: string, memoryId: string) =>
    request<MemoryEvidence[]>(`${root}/${familyId}/memories/${memoryId}/evidence`),
  archiveMemory: (familyId: string, memoryId: string) =>
    request<MemoryCandidate>(
      `${root}/${familyId}/memories/${memoryId}/archive`,
      mutate("POST"),
    ),
  restoreMemory: (familyId: string, memoryId: string) =>
    request<MemoryCandidate>(
      `${root}/${familyId}/memories/${memoryId}/restore`,
      mutate("POST"),
    ),

  // --- indexed search (Stage 7) ---
  indexedSearch: (
    familyId: string,
    params: {
      query?: string;
      kinds?: string[];
      cursor?: string;
      limit?: number;
      visibility?: string;
    } = {},
  ) => request<IndexedSearchResponse>(`${root}/${familyId}/search/indexed${qs(params)}`),
};

export const JOB_STATUS_LABELS: Record<string, string> = {
  PENDING: "待处理",
  RUNNING: "进行中",
  PAUSED: "已暂停",
  COMPLETED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
};

export const JOB_STATUS_COLORS: Record<string, string> = {
  PENDING: "default",
  RUNNING: "processing",
  PAUSED: "warning",
  COMPLETED: "success",
  FAILED: "error",
  CANCELLED: "default",
};

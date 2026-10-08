import { request } from "../request";

/** Where the assembled file is delivered. Mirrors the server enum. */
export type UploadPurpose = "CHAT_ATTACHMENT" | "WORKSPACE_FILE";

export interface CreateSessionInput {
  filename: string;
  total_bytes: number;
  purpose: UploadPurpose;
  agent_id: string;
  relative_target?: string | null;
  mime_type?: string | null;
  total_sha256?: string | null;
  chunk_size?: number | null;
}

export interface UploadSession {
  upload_id: string;
  status: string;
  purpose: string;
  filename: string;
  mime_type: string | null;
  total_bytes: number;
  chunk_size: number;
  received_bytes: number;
  expires_at: number;
  expected_sha256: string | null;
  agent_id: string | null;
  family_id: string | null;
  final_resource_id: string | null;
  part_count: number;
  missing_ranges: { offset: number; size: number }[];
  missing_truncated: boolean;
}

export interface CompletedUpload {
  upload_id: string;
  status: string;
  filename: string;
  media_type: string;
  storage: "workspace" | "blob";
  size: number;
  path: string;
  workspace_path: string;
  url: string;
  access_url: string;
}

export function createUploadSession(
  input: CreateSessionInput,
): Promise<UploadSession> {
  return request<UploadSession>("/uploads/sessions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function getUploadSession(uploadId: string): Promise<UploadSession> {
  return request<UploadSession>(
    `/uploads/sessions/${encodeURIComponent(uploadId)}`,
  );
}

/**
 * PUT one part as a raw binary body.
 *
 * No multipart wrapper: the server reads the body as a byte stream and checks
 * `Content-Length` plus `X-Chunk-SHA256` against the session's chunk geometry.
 */
export function putUploadPart(
  uploadId: string,
  partNumber: number,
  blob: Blob,
  sha256: string,
): Promise<UploadSession> {
  return request<UploadSession>(
    `/uploads/sessions/${encodeURIComponent(uploadId)}/parts/${partNumber}`,
    {
      method: "PUT",
      body: blob,
      headers: {
        // buildHeaders defaults to JSON; override so binary bodies stay binary.
        "Content-Type": "application/octet-stream",
        "X-Chunk-SHA256": sha256,
        "Content-Length": String(blob.size),
      },
    },
  );
}

export function completeUploadSession(
  uploadId: string,
): Promise<CompletedUpload> {
  return request<CompletedUpload>(
    `/uploads/sessions/${encodeURIComponent(uploadId)}/complete`,
    { method: "POST" },
  );
}

export function cancelUploadSession(uploadId: string): Promise<void> {
  return request<void>(`/uploads/sessions/${encodeURIComponent(uploadId)}`, {
    method: "DELETE",
  });
}

export const uploadSessionsApi = {
  createUploadSession,
  getUploadSession,
  putUploadPart,
  completeUploadSession,
  cancelUploadSession,
};

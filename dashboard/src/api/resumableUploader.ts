import {
  cancelUploadSession,
  completeUploadSession,
  createUploadSession,
  getUploadSession,
  putUploadPart,
  type CompletedUpload,
  type CreateSessionInput,
  type UploadPurpose,
  type UploadSession,
} from "./modules/uploadSessions";

/**
 * Resumable uploader for the server-managed upload-session API (plan phase 3).
 *
 * Design notes:
 * - Everything the transport needs is injected, so the state machine is unit
 *   testable without a network, a browser, or IndexedDB.
 * - IndexedDB stores **session metadata only** (upload id, file fingerprint,
 *   target). File bytes are never persisted: after a reload the user re-picks
 *   the file and we resume from the server's missing ranges.
 * - A part PUT is idempotent server-side, so re-sending a part that survived a
 *   crash is always safe.
 */

/** Files at or below this size keep the existing single-request XHR path. */
export const RESUMABLE_THRESHOLD_BYTES = 8 * 1024 * 1024;

/** Concurrent part uploads. A constant for now; no settings page. */
export const PART_CONCURRENCY = 3;

const MAX_BACKOFF_MS = 30_000;
const BASE_BACKOFF_MS = 500;
const MAX_PART_ATTEMPTS = 5;

/** Statuses that must never be retried blindly — retrying cannot help. */
const NON_RETRYABLE_STATUS = new Set([401, 403, 404, 409, 413]);

export type UploadStatus =
  | "idle"
  | "preparing"
  | "uploading"
  | "paused"
  | "finalizing"
  | "done"
  | "error"
  | "cancelled";

export interface UploadProgress {
  uploadId: string | null;
  status: UploadStatus;
  bytesSent: number;
  totalBytes: number;
  percent: number;
  bytesPerSecond: number;
  etaSeconds: number | null;
  error: string | null;
}

export interface UploadTarget {
  purpose: UploadPurpose;
  agentId: string;
  relativeTarget?: string | null;
}

export interface FileFingerprint {
  name: string;
  size: number;
  lastModified: number;
}

export interface PendingUpload {
  uploadId: string;
  fingerprint: FileFingerprint;
  target: UploadTarget;
  createdAt: number;
}

export interface ResumableTransport {
  create(input: CreateSessionInput): Promise<UploadSession>;
  status(uploadId: string): Promise<UploadSession>;
  putPart(
    uploadId: string,
    partNumber: number,
    blob: Blob,
    sha256: string,
  ): Promise<UploadSession>;
  complete(uploadId: string): Promise<CompletedUpload>;
  cancel(uploadId: string): Promise<void>;
}

export interface SessionStore {
  save(entry: PendingUpload): Promise<void>;
  load(): Promise<PendingUpload[]>;
  remove(uploadId: string): Promise<void>;
}

export interface ResumableOptions {
  transport?: ResumableTransport;
  store?: SessionStore;
  concurrency?: number;
  thresholdBytes?: number;
  digest?: (blob: Blob) => Promise<string>;
  sleep?: (ms: number) => Promise<void>;
  now?: () => number;
}

const defaultTransport: ResumableTransport = {
  create: createUploadSession,
  status: getUploadSession,
  putPart: putUploadPart,
  complete: completeUploadSession,
  cancel: cancelUploadSession,
};

/**
 * `request()` surfaces failures as a plain Error whose message embeds the
 * status ("Request failed: 409 Conflict - ..."), so recover it from there
 * rather than changing the shared error type every caller depends on.
 */
function statusFromError(err: unknown): number | null {
  const match = /Request failed: (\d{3})/.exec(
    err instanceof Error ? err.message : String(err),
  );
  return match ? Number(match[1]) : null;
}

export function isRetryable(err: unknown): boolean {
  const status = statusFromError(err);
  if (status !== null) return !NON_RETRYABLE_STATUS.has(status);
  // Unknown errors are almost always transport-level (offline, DNS, abort).
  return true;
}

async function defaultDigest(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  const hash = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(hash))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** In-memory fallback so a browser without IndexedDB still uploads. */
export class MemorySessionStore implements SessionStore {
  private entries = new Map<string, PendingUpload>();

  async save(entry: PendingUpload): Promise<void> {
    this.entries.set(entry.uploadId, entry);
  }
  async load(): Promise<PendingUpload[]> {
    return [...this.entries.values()];
  }
  async remove(uploadId: string): Promise<void> {
    this.entries.delete(uploadId);
  }
}

function fingerprintOf(file: File): FileFingerprint {
  return { name: file.name, size: file.size, lastModified: file.lastModified };
}

export function fingerprintMatches(a: FileFingerprint, b: FileFingerprint): boolean {
  return a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;
}

export class ResumableUpload {
  private readonly file: File;
  private readonly target: UploadTarget;
  private readonly transport: ResumableTransport;
  private readonly store: SessionStore;
  private readonly concurrency: number;
  private readonly thresholdBytes: number;
  private readonly digest: (blob: Blob) => Promise<string>;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly now: () => number;

  private listeners = new Set<(p: UploadProgress) => void>();
  private session: UploadSession | null = null;
  private uploadedBytes = 0;
  private startedAt = 0;
  private pauseRequested = false;
  private pauseGate: (() => void) | null = null;
  private cancelRequested = false;
  private current: UploadProgress;

  constructor(file: File, target: UploadTarget, options: ResumableOptions = {}) {
    this.file = file;
    this.target = target;
    this.transport = options.transport ?? defaultTransport;
    this.store = options.store ?? new MemorySessionStore();
    this.concurrency = Math.max(1, options.concurrency ?? PART_CONCURRENCY);
    this.thresholdBytes = options.thresholdBytes ?? RESUMABLE_THRESHOLD_BYTES;
    this.digest = options.digest ?? defaultDigest;
    this.sleep = options.sleep ?? defaultSleep;
    this.now = options.now ?? (() => Date.now());
    this.current = {
      uploadId: null,
      status: "idle",
      bytesSent: 0,
      totalBytes: file.size,
      percent: 0,
      bytesPerSecond: 0,
      etaSeconds: null,
      error: null,
    };
  }

  /** True when this file is too big for the single-request path. */
  get needsSession(): boolean {
    return this.file.size > this.thresholdBytes;
  }

  onProgress(listener: (p: UploadProgress) => void): () => void {
    this.listeners.add(listener);
    listener(this.current);
    return () => this.listeners.delete(listener);
  }

  get progress(): UploadProgress {
    return this.current;
  }

  pause(): void {
    if (this.current.status === "uploading") {
      this.pauseRequested = true;
      this.emit({ status: "paused" });
    }
  }

  /** Release a paused upload. Safe to call when not paused. */
  resume(): void {
    if (!this.pauseRequested) return;
    this.pauseRequested = false;
    this.releasePauseGate();
    if (this.current.status === "paused") this.emit({ status: "uploading" });
  }

  /**
   * Block while paused. Uses a gate rather than polling so a paused upload
   * parks instead of spinning on timers.
   */
  private waitWhilePaused(): Promise<void> {
    if (!this.pauseRequested) return Promise.resolve();
    return new Promise<void>((resolve) => {
      this.pauseGate = resolve;
    });
  }

  private releasePauseGate(): void {
    const release = this.pauseGate;
    this.pauseGate = null;
    release?.();
  }

  async cancel(): Promise<void> {
    this.cancelRequested = true;
    // Unblock parked workers so they observe the cancel immediately.
    this.releasePauseGate();
    if (this.session) {
      await this.transport.cancel(this.session.upload_id).catch(() => undefined);
      await this.store.remove(this.session.upload_id).catch(() => undefined);
    }
    this.emit({ status: "cancelled", percent: 0 });
  }

  /**
   * Upload the file, resuming a previous session when one matches.
   *
   * `resumeFrom` is the metadata recovered from the session store; when its
   * fingerprint matches this file we re-ask the server which ranges are still
   * missing instead of starting over.
   */
  async start(resumeFrom?: PendingUpload | null): Promise<CompletedUpload> {
    this.startedAt = this.now();
    this.cancelRequested = false;
    this.pauseRequested = false;
    this.emit({ status: "preparing", percent: 0, error: null });

    try {
      this.session = await this.openSession(resumeFrom ?? null);
      const uploadId = this.session.upload_id;
      await this.store.save({
        uploadId,
        fingerprint: fingerprintOf(this.file),
        target: this.target,
        createdAt: this.startedAt,
      });

      await this.uploadMissingParts(uploadId);
      if (this.cancelRequested) {
        this.emit({ status: "cancelled", percent: 0 });
        throw new CancelledError();
      }

      this.emit({ status: "finalizing" });
      const done = await this.transport.complete(uploadId);
      await this.store.remove(uploadId).catch(() => undefined);
      this.emit({ status: "done", percent: 100, bytesSent: this.file.size });
      return done;
    } catch (err) {
      if (err instanceof CancelledError) throw err;
      const status = this.cancelRequested ? "cancelled" : "error";
      this.emit({ status, error: err instanceof Error ? err.message : String(err) });
      throw err;
    }
  }

  private async openSession(resumeFrom: PendingUpload | null): Promise<UploadSession> {
    if (
      resumeFrom &&
      fingerprintMatches(resumeFrom.fingerprint, fingerprintOf(this.file)) &&
      resumeFrom.target.agentId === this.target.agentId
    ) {
      try {
        const existing = await this.transport.status(resumeFrom.uploadId);
        if (existing.status === "OPEN") {
          this.uploadedBytes = existing.received_bytes;
          this.emit({
            uploadId: existing.upload_id,
            bytesSent: existing.received_bytes,
            status: "uploading",
          });
          return existing;
        }
      } catch {
        // Session gone or not ours — fall through and start a new one.
      }
    }

    const created = await this.transport.create({
      filename: this.file.name,
      total_bytes: this.file.size,
      purpose: this.target.purpose,
      agent_id: this.target.agentId,
      relative_target: this.target.relativeTarget ?? null,
      mime_type: this.file.type || null,
    });
    this.uploadedBytes = 0;
    this.emit({ uploadId: created.upload_id, bytesSent: 0, status: "uploading" });
    return created;
  }

  /** Part numbers still needed, derived from the session's missing ranges. */
  private pendingPartNumbers(session: UploadSession): number[] {
    const { chunk_size: chunk, total_bytes: total } = session;
    const numbers: number[] = [];
    for (const range of session.missing_ranges) {
      const first = Math.floor(range.offset / chunk) + 1;
      const last = Math.ceil((range.offset + range.size) / chunk);
      for (let n = first; n <= last; n += 1) {
        if ((n - 1) * chunk < total) numbers.push(n);
      }
    }
    return [...new Set(numbers)].sort((a, b) => a - b);
  }

  private async uploadMissingParts(uploadId: string): Promise<void> {
    const session = this.session;
    if (!session) return;
    const queue = this.pendingPartNumbers(session);
    if (queue.length === 0) return;

    let cursor = 0;
    const worker = async (): Promise<void> => {
      while (cursor < queue.length) {
        if (this.cancelRequested) return;
        await this.waitWhilePaused();
        if (this.cancelRequested) return;
        const partNumber = queue[cursor++];
        await this.sendPart(uploadId, partNumber, session.chunk_size);
      }
    };
    await Promise.all(
      Array.from({ length: Math.min(this.concurrency, queue.length) }, worker),
    );
  }

  private async sendPart(
    uploadId: string,
    partNumber: number,
    chunk: number,
  ): Promise<void> {
    const start = (partNumber - 1) * chunk;
    const blob = this.file.slice(start, Math.min(start + chunk, this.file.size));
    const sha256 = await this.digest(blob);

    let attempt = 0;
    for (;;) {
      try {
        await this.transport.putPart(uploadId, partNumber, blob, sha256);
        this.uploadedBytes += blob.size;
        this.emitProgress();
        return;
      } catch (err) {
        attempt += 1;
        if (attempt >= MAX_PART_ATTEMPTS || !isRetryable(err) || this.cancelRequested) {
          throw err;
        }
        const backoff = Math.min(BASE_BACKOFF_MS * 2 ** (attempt - 1), MAX_BACKOFF_MS);
        await this.sleep(backoff);
      }
    }
  }

  private emitProgress(): void {
    const elapsed = Math.max(1, this.now() - this.startedAt);
    const bytesPerSecond = (this.uploadedBytes / elapsed) * 1000;
    const remaining = Math.max(0, this.file.size - this.uploadedBytes);
    this.emit({
      uploadId: this.session?.upload_id ?? null,
      status: this.pauseRequested ? "paused" : "uploading",
      bytesSent: this.uploadedBytes,
      percent: this.file.size ? Math.round((this.uploadedBytes / this.file.size) * 100) : 0,
      bytesPerSecond: Math.round(bytesPerSecond),
      etaSeconds: bytesPerSecond > 0 ? Math.round(remaining / bytesPerSecond) : null,
      error: null,
    });
  }

  private emit(patch: Partial<UploadProgress>): void {
    this.current = { ...this.current, ...patch };
    for (const listener of this.listeners) listener(this.current);
  }
}

export class CancelledError extends Error {
  constructor() {
    super("upload cancelled");
    this.name = "CancelledError";
  }
}

/** Open uploads remembered from a previous page load, newest first. */
export async function listPendingUploads(
  store: SessionStore,
): Promise<PendingUpload[]> {
  const entries = await store.load();
  return entries.sort((a, b) => b.createdAt - a.createdAt);
}
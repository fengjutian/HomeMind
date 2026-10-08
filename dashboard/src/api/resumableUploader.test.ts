import { describe, expect, it, vi } from "vitest";
import {
  CancelledError,
  isRetryable,
  MemorySessionStore,
  PART_CONCURRENCY,
  ResumableUpload,
  RESUMABLE_THRESHOLD_BYTES,
  fingerprintMatches,
  type PendingUpload,
  type ResumableTransport,
  type UploadSession,
} from "./resumableUploader";
import type { CompletedUpload, CreateSessionInput } from "./modules/uploadSessions";

const CHUNK = 1024;

function makeFile(size: number, name = "big.bin", lastModified = 1_700_000_000_000) {
  const blob = new Blob([new Uint8Array(size).fill(7)]);
  return new File([blob], name, { lastModified, type: "application/octet-stream" });
}

function session(overrides: Partial<UploadSession> = {}): UploadSession {
  const total = overrides.total_bytes ?? 3 * CHUNK;
  const chunk = overrides.chunk_size ?? CHUNK;
  const missing: { offset: number; size: number }[] = [];
  for (let start = 0; start < total; start += chunk) {
    missing.push({ offset: start, size: Math.min(chunk, total - start) });
  }
  return {
    upload_id: "upload-1",
    status: "OPEN",
    purpose: "CHAT_ATTACHMENT",
    filename: "big.bin",
    mime_type: "application/octet-stream",
    total_bytes: total,
    chunk_size: chunk,
    received_bytes: 0,
    expires_at: 0,
    expected_sha256: null,
    agent_id: "ag1",
    family_id: null,
    final_resource_id: null,
    part_count: 0,
    missing_ranges: missing,
    missing_truncated: false,
    ...overrides,
  };
}

const done: CompletedUpload = {
  upload_id: "upload-1",
  status: "COMPLETED",
  filename: "big.bin",
  media_type: "application/octet-stream",
  storage: "blob",
  size: 3 * CHUNK,
  path: "upload-1",
  workspace_path: "upload-1",
  url: "/api/uploads/blobs/upload-1",
  access_url: "/api/uploads/blobs/upload-1",
};

interface Recorder {
  transport: ResumableTransport;
  putPart: ReturnType<typeof vi.fn>;
  created: CreateSessionInput[];
}

function fakeTransport(over: Partial<ResumableTransport> = {}): Recorder {
  const created: CreateSessionInput[] = [];
  const putPart = vi.fn(async () => session());
  const transport: ResumableTransport = {
    create: vi.fn(async (input: CreateSessionInput) => {
      created.push(input);
      return session({ filename: input.filename, total_bytes: input.total_bytes });
    }),
    status: vi.fn(async () => session()),
    putPart,
    complete: vi.fn(async () => done),
    cancel: vi.fn(async () => undefined),
    ...over,
  };
  return { transport, putPart, created };
}

const target = { purpose: "CHAT_ATTACHMENT" as const, agentId: "ag1" };

// Digest is deterministic per part so assertions can count bytes.
const digest = vi.fn(async () => "d".repeat(64));
const sleep = vi.fn(async () => undefined);
const now = () => 1_000;

function build(
  file: File,
  transport: ResumableTransport,
  store = new MemorySessionStore(),
  extra: Partial<ConstructorParameters<typeof ResumableUpload>[2]> = {},
) {
  return new ResumableUpload(file, target, {
    transport,
    store,
    digest,
    sleep,
    now,
    ...extra,
  });
}

/** Await until the upload has an id, i.e. the session exists. */
async function untilSession(upload: ResumableUpload): Promise<void> {
  for (let i = 0; i < 50 && !upload.progress.uploadId; i += 1) {
    await Promise.resolve();
  }
}

describe("threshold", () => {
  it("keeps small files on the legacy single-request path", () => {
    const small = makeFile(1024);
    expect(build(small, fakeTransport().transport).needsSession).toBe(false);
  });

  it("switches to the session API above the threshold", () => {
    const large = makeFile(RESUMABLE_THRESHOLD_BYTES + 1);
    expect(build(large, fakeTransport().transport).needsSession).toBe(true);
  });

  it("uses 3 concurrent parts by default", () => {
    expect(PART_CONCURRENCY).toBe(3);
  });
});

describe("happy path", () => {
  it("creates a session, sends every missing part, then completes", async () => {
    const { transport, putPart, created } = fakeTransport();
    const result = await build(makeFile(3 * CHUNK), transport).start();

    expect(created).toHaveLength(1);
    expect(created[0]).toMatchObject({
      filename: "big.bin",
      total_bytes: 3 * CHUNK,
      purpose: "CHAT_ATTACHMENT",
      agent_id: "ag1",
    });
    expect(putPart).toHaveBeenCalledTimes(3);
    expect(putPart.mock.calls.map((c) => c[1])).toEqual([1, 2, 3]);
    expect(result.status).toBe("COMPLETED");
    expect(transport.complete).toHaveBeenCalledWith("upload-1");
  });

  it("slices each part from the right offset with the right length", async () => {
    const { transport, putPart } = fakeTransport();
    await build(makeFile(2 * CHUNK + 5), transport).start();
    const blobs = putPart.mock.calls.map((c) => c[2] as Blob);
    expect(blobs.map((b) => b.size)).toEqual([CHUNK, CHUNK, 5]);
  });

  it("reports 100 percent and clears the pending session on success", async () => {
    const store = new MemorySessionStore();
    const upload = build(makeFile(3 * CHUNK), fakeTransport().transport, store);
    const seen: number[] = [];
    upload.onProgress((p) => seen.push(p.percent));
    await upload.start();
    expect(seen.at(-1)).toBe(100);
    expect(await store.load()).toHaveLength(0);
  });
});

describe("resume", () => {
  it("continues from the server missing ranges instead of resending", async () => {
    const partial = session({
      received_bytes: CHUNK,
      missing_ranges: [{ offset: CHUNK, size: 2 * CHUNK }],
    });
    const { transport, putPart } = fakeTransport({ status: vi.fn(async () => partial) });
    const resumeFrom: PendingUpload = {
      uploadId: "upload-1",
      fingerprint: { name: "big.bin", size: 3 * CHUNK, lastModified: 1_700_000_000_000 },
      target,
      createdAt: 1,
    };

    await build(makeFile(3 * CHUNK), transport).start(resumeFrom);

    expect(transport.create).not.toHaveBeenCalled();
    expect(putPart.mock.calls.map((c) => c[1])).toEqual([2, 3]);
  });

  it("ignores a remembered session whose fingerprint does not match", async () => {
    const { transport } = fakeTransport();
    const stale: PendingUpload = {
      uploadId: "old",
      fingerprint: { name: "other.bin", size: 10, lastModified: 1 },
      target,
      createdAt: 1,
    };
    await build(makeFile(3 * CHUNK), transport).start(stale);
    expect(transport.create).toHaveBeenCalledTimes(1);
  });

  it("starts a new session when the remembered one is gone", async () => {
    const { transport } = fakeTransport({
      status: vi.fn(async () => {
        throw new Error("Request failed: 404 Not Found");
      }),
    });
    const resumeFrom: PendingUpload = {
      uploadId: "gone",
      fingerprint: { name: "big.bin", size: 3 * CHUNK, lastModified: 1_700_000_000_000 },
      target,
      createdAt: 1,
    };
    await build(makeFile(3 * CHUNK), transport).start(resumeFrom);
    expect(transport.create).toHaveBeenCalledTimes(1);
  });

  it("matches fingerprints on name, size and lastModified", () => {
    const base = { name: "a.bin", size: 10, lastModified: 5 };
    expect(fingerprintMatches(base, { ...base })).toBe(true);
    expect(fingerprintMatches(base, { ...base, size: 11 })).toBe(false);
    expect(fingerprintMatches(base, { ...base, name: "b.bin" })).toBe(false);
    expect(fingerprintMatches(base, { ...base, lastModified: 6 })).toBe(false);
  });
});

describe("retry policy", () => {
  it("does not retry 401/403/404/409/413", () => {
    for (const code of [401, 403, 404, 409, 413]) {
      expect(isRetryable(new Error(`Request failed: ${code} Boom`))).toBe(false);
    }
  });

  it("retries 5xx and transport errors", () => {
    expect(isRetryable(new Error("Request failed: 500 Server Error"))).toBe(true);
    expect(isRetryable(new Error("Request failed: 429 Too Many"))).toBe(true);
    expect(isRetryable(new TypeError("Failed to fetch"))).toBe(true);
  });

  it("retries a transient part failure with capped exponential backoff", async () => {
    sleep.mockClear();
    let calls = 0;
    const putPart = vi.fn(async () => {
      calls += 1;
      if (calls < 3) throw new Error("Request failed: 503 Unavailable");
      return session();
    });
    const { transport } = fakeTransport({ putPart });
    // Serial parts keep the retry sequence deterministic.
    await build(makeFile(3 * CHUNK), transport, new MemorySessionStore(), {
      concurrency: 1,
    }).start();

    expect(putPart).toHaveBeenCalledTimes(5); // part1 x3 (2 retries), then 2 and 3
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([500, 1000]);
  });

  it("gives up on a non-retryable part failure and surfaces the reason", async () => {
    const putPart = vi.fn(async () => {
      throw new Error("Request failed: 413 Payload Too Large");
    });
    const { transport } = fakeTransport({ putPart });
    await expect(
      build(makeFile(3 * CHUNK), transport, new MemorySessionStore(), {
        concurrency: 1,
      }).start(),
    ).rejects.toThrow(/413/);
    expect(putPart).toHaveBeenCalledTimes(1);
  });
});

describe("control", () => {
  it("cancel stops the run and tells the server to drop the session", async () => {
    const { transport } = fakeTransport();
    const upload = build(makeFile(6 * CHUNK), transport);
    const promise = upload.start();
    await untilSession(upload);
    await upload.cancel();
    await expect(promise).rejects.toBeInstanceOf(CancelledError);
    expect(transport.cancel).toHaveBeenCalledWith("upload-1");
  });

  it("pause parks the upload and resume finishes it", async () => {
    const { transport, putPart } = fakeTransport();
    const upload = build(makeFile(6 * CHUNK), transport);
    const statuses: string[] = [];
    upload.onProgress((p) => statuses.push(p.status));

    const promise = upload.start();
    await untilSession(upload);
    upload.pause();
    expect(statuses).toContain("paused");

    upload.resume();
    await promise;
    expect(putPart).toHaveBeenCalledTimes(6);
    expect(statuses.at(-1)).toBe("done");
  });
});

describe("progress", () => {
  it("computes speed and ETA from elapsed time", async () => {
    let clock = 0;
    const upload = new ResumableUpload(makeFile(4 * CHUNK), target, {
      transport: fakeTransport().transport,
      store: new MemorySessionStore(),
      digest,
      sleep,
      now: () => (clock += 500),
    });
    const snapshots: { bps: number; eta: number | null }[] = [];
    upload.onProgress((p) =>
      snapshots.push({ bps: p.bytesPerSecond, eta: p.etaSeconds }),
    );
    await upload.start();
    const mid = snapshots.find((s) => s.bps > 0);
    expect(mid).toBeDefined();
    expect(mid!.eta).toBeGreaterThanOrEqual(0);
  });
});
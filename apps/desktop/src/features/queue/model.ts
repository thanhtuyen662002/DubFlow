import type { JobStatus, JobStatusState } from "../job_status/status.ts";
import { isDubbingOptions, type DubbingOptions } from "../voices/model.ts";

export const QUEUE_SCHEMA_VERSION = 1 as const;
export const DEFAULT_QUEUE_LIMIT = 10_000;

export type QueueJob = {
  id: string;
  sourcePath: string;
  displayName: string;
  createdAt: string;
  status: JobStatus;
  outputPath: string | null;
  dubbing: DubbingOptions;
};

export type QueueSnapshot = {
  schema_version: typeof QUEUE_SCHEMA_VERSION;
  selected_job_id: string | null;
  jobs: QueueJob[];
};

export type QueueSummary = {
  total: number;
  queued: number;
  active: number;
  waiting: number;
  completed: number;
  failed: number;
};

export type SnapshotStorage = {
  read(): string | null;
  write(serialized: string): void;
};

export type QueueClock = () => string;
export type QueueIdFactory = () => string;

function newQueueJobId(): string {
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16));
  return `job-${Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("")}`;
}

export type QueueListener = (snapshot: QueueSnapshot) => void;

const ACTIVE_STATES = new Set<JobStatusState>([
  "RUNNING",
  "RETRYING",
  "RECOVERED",
  "WORKER_LOST",
]);

const WAITING_STATES = new Set<JobStatusState>([
  "WAITING_RESOURCE",
  "WAITING_EXTERNAL",
  "BLOCKED_NEEDS_ACTION",
]);

const TERMINAL_STATES = new Set<JobStatusState>(["COMPLETED", "FAILED"]);

function emptyStatus(): JobStatus {
  return {
    schema_version: 1,
    state: "QUEUED",
    reason: "queued",
    message: "Queued",
    checkpoint_id: null,
    progress: {
      completed_units: "0",
      total_units: null,
      heartbeat_sequence: "0",
    },
    retry: {
      attempt: "0",
      max_attempts: "3",
      condition_fingerprint: null,
    },
    resource: {
      kind: null,
      held: false,
      release_requested: false,
    },
  };
}

function cloneSnapshot(snapshot: QueueSnapshot): QueueSnapshot {
  return JSON.parse(JSON.stringify(snapshot)) as QueueSnapshot;
}

function isDecimalString(value: unknown, maxLength: number): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= maxLength &&
    /^(0|[1-9][0-9]*)$/.test(value)
  );
}

function isJobStatus(value: unknown): value is JobStatus {
  if (!value || typeof value !== "object") return false;
  const status = value as Partial<JobStatus>;
  if (status.schema_version !== 1 || typeof status.reason !== "string" || !status.reason) {
    return false;
  }
  if (typeof status.message !== "string" || !status.message) return false;
  if (
    ![
      "QUEUED",
      "RUNNING",
      "WAITING_RESOURCE",
      "WAITING_EXTERNAL",
      "RETRYING",
      "WORKER_LOST",
      "RECOVERED",
      "BLOCKED_NEEDS_ACTION",
      "COMPLETED",
      "FAILED",
    ].includes(status.state as string)
  ) {
    return false;
  }
  if (status.checkpoint_id !== null && typeof status.checkpoint_id !== "string") return false;
  const progress = status.progress;
  const retry = status.retry;
  const resource = status.resource;
  if (!progress || !retry || !resource) return false;
  if (
    !isDecimalString(progress.completed_units, 20) ||
    (progress.total_units !== null && !isDecimalString(progress.total_units, 20)) ||
    !isDecimalString(progress.heartbeat_sequence, 20)
  ) {
    return false;
  }
  if (!isDecimalString(retry.attempt, 10) || !isDecimalString(retry.max_attempts, 10)) {
    return false;
  }
  if (retry.condition_fingerprint !== null && typeof retry.condition_fingerprint !== "string") {
    return false;
  }
  if (
    resource.kind !== null &&
    !["GPU", "CPU", "DISK", "NETWORK", "WORKER_SLOT"].includes(resource.kind)
  ) {
    return false;
  }
  return typeof resource.held === "boolean" && typeof resource.release_requested === "boolean";
}

function isQueueJob(value: unknown): value is QueueJob {
  if (!value || typeof value !== "object") return false;
  const job = value as Partial<QueueJob>;
  return (
    typeof job.id === "string" && job.id.length > 0 &&
    typeof job.sourcePath === "string" && job.sourcePath.length > 0 &&
    typeof job.displayName === "string" && job.displayName.length > 0 &&
    typeof job.createdAt === "string" && !Number.isNaN(Date.parse(job.createdAt)) &&
    isJobStatus(job.status) &&
    (job.outputPath === null || typeof job.outputPath === "string") &&
    (job.dubbing === undefined || isDubbingOptions(job.dubbing))
  );
}

export function parseQueueSnapshot(serialized: string | null): QueueSnapshot | null {
  if (!serialized) return null;
  try {
    const parsed: unknown = JSON.parse(serialized);
    if (!parsed || typeof parsed !== "object") return null;
    const snapshot = parsed as Partial<QueueSnapshot>;
    if (
      snapshot.schema_version !== QUEUE_SCHEMA_VERSION ||
      (snapshot.selected_job_id !== null && typeof snapshot.selected_job_id !== "string") ||
      !Array.isArray(snapshot.jobs) ||
      snapshot.jobs.length > DEFAULT_QUEUE_LIMIT ||
      !snapshot.jobs.every(isQueueJob)
    ) {
      return null;
    }
    const ids = new Set(snapshot.jobs.map((job) => job.id));
    if (snapshot.selected_job_id !== null && !ids.has(snapshot.selected_job_id)) return null;
    if (ids.size !== snapshot.jobs.length) return null;
    const restored = cloneSnapshot(snapshot as QueueSnapshot);
    for (const job of restored.jobs) job.dubbing ??= { enabled: false, voiceId: null };
    return restored;
  } catch {
    return null;
  }
}

export function serializeQueueSnapshot(snapshot: QueueSnapshot): string {
  const parsed = parseQueueSnapshot(JSON.stringify(snapshot));
  if (!parsed) throw new Error("Cannot serialize an invalid queue snapshot");
  return JSON.stringify(parsed);
}

export function summarizeQueue(snapshot: QueueSnapshot): QueueSummary {
  const summary: QueueSummary = {
    total: snapshot.jobs.length,
    queued: 0,
    active: 0,
    waiting: 0,
    completed: 0,
    failed: 0,
  };
  for (const job of snapshot.jobs) {
    if (job.status.state === "QUEUED") summary.queued += 1;
    else if (ACTIVE_STATES.has(job.status.state)) summary.active += 1;
    else if (WAITING_STATES.has(job.status.state)) summary.waiting += 1;
    else if (job.status.state === "COMPLETED") summary.completed += 1;
    else if (job.status.state === "FAILED") summary.failed += 1;
  }
  return summary;
}

export function createQueueJob(
  id: string,
  sourcePath: string,
  createdAt: string,
): QueueJob {
  const trimmedPath = sourcePath.trim();
  if (!trimmedPath) throw new Error("A local source path is required");
  if (!id.trim()) throw new Error("A stable job id is required");
  if (Number.isNaN(Date.parse(createdAt))) throw new Error("createdAt must be an ISO timestamp");
  const normalizedPath = trimmedPath.replaceAll("\\", "/");
  const name = normalizedPath.split("/").filter(Boolean).at(-1) ?? normalizedPath;
  return {
    id,
    sourcePath: trimmedPath,
    displayName: name,
    createdAt,
    status: emptyStatus(),
    outputPath: null,
    dubbing: { enabled: false, voiceId: null },
  };
}

export class QueueController {
  private snapshot: QueueSnapshot;
  private readonly listeners = new Set<QueueListener>();
  private readonly storage: SnapshotStorage;
  private readonly clock: QueueClock;
  private readonly queueLimit: number;
  private readonly idFactory: QueueIdFactory;

  constructor(
    storage: SnapshotStorage,
    clock: QueueClock = () => new Date().toISOString(),
    queueLimit = DEFAULT_QUEUE_LIMIT,
    idFactory: QueueIdFactory = newQueueJobId,
  ) {
    this.storage = storage;
    this.clock = clock;
    this.queueLimit = queueLimit;
    this.idFactory = idFactory;
    const restored = parseQueueSnapshot(storage.read());
    this.snapshot = restored ?? {
      schema_version: QUEUE_SCHEMA_VERSION,
      selected_job_id: null,
      jobs: [],
    };
  }

  subscribe(listener: QueueListener): () => void {
    this.listeners.add(listener);
    listener(cloneSnapshot(this.snapshot));
    return () => this.listeners.delete(listener);
  }

  getSnapshot(): QueueSnapshot {
    return cloneSnapshot(this.snapshot);
  }

  getSummary(): QueueSummary {
    return summarizeQueue(this.snapshot);
  }

  addPaths(paths: readonly string[]): string[] {
    const existing = new Set(this.snapshot.jobs.map((job) => job.sourcePath));
    const added: string[] = [];
    for (const path of paths) {
      const trimmed = path.trim();
      if (!trimmed || existing.has(trimmed)) continue;
      if (this.snapshot.jobs.length >= this.queueLimit) {
        throw new Error(`Queue limit of ${this.queueLimit} jobs reached`);
      }
      const job = createQueueJob(this.allocateId(), trimmed, this.clock());
      this.snapshot.jobs.push(job);
      existing.add(trimmed);
      added.push(job.id);
    }
    if (added.length > 0) {
      this.snapshot.selected_job_id ??= added[0];
      this.commit();
    }
    return added;
  }

  selectJob(jobId: string | null): void {
    if (jobId !== null && !this.snapshot.jobs.some((job) => job.id === jobId)) {
      throw new Error(`Unknown job: ${jobId}`);
    }
    this.snapshot.selected_job_id = jobId;
    this.commit();
  }

  updateStatus(jobId: string, status: JobStatus, outputPath?: string | null): void {
    const job = this.requireJob(jobId);
    if (!isJobStatus(status)) throw new Error("Invalid job status");
    job.status = cloneSnapshot({
      schema_version: QUEUE_SCHEMA_VERSION,
      selected_job_id: null,
      jobs: [{ ...job, status }],
    }).jobs[0].status;
    if (outputPath !== undefined) job.outputPath = outputPath;
    this.commit();
  }

  retryJob(jobId: string): string {
    const job = this.requireJob(jobId);
    if (!TERMINAL_STATES.has(job.status.state) && job.status.state !== "BLOCKED_NEEDS_ACTION") {
      throw new Error("Only terminal or blocked jobs can be retried");
    }
    if (this.snapshot.jobs.length >= this.queueLimit) throw new Error(`Queue limit of ${this.queueLimit} jobs reached`);
    // The durable original remains terminal with its original options/artifacts.
    // An explicit rerun is a distinct executable job, including its voice.
    const retry = createQueueJob(this.allocateId(), job.sourcePath, this.clock());
    retry.dubbing = { ...job.dubbing };
    retry.status = {
      ...emptyStatus(),
      reason: "manual_retry",
      message: "Đang chờ xử lý bản mới",
    };
    this.snapshot.jobs.push(retry);
    this.snapshot.selected_job_id = retry.id;
    this.commit();
    return retry.id;
  }

  setDubbing(jobId: string, options: DubbingOptions): void {
    const job = this.requireJob(jobId);
    if (job.status.state !== "QUEUED") throw new Error("Requeue the job before changing its voice");
    if (!isDubbingOptions(options)) throw new Error("Invalid dubbing options");
    job.dubbing = { ...options };
    this.commit();
  }

  removeJob(jobId: string): void {
    this.requireJob(jobId);
    this.snapshot.jobs = this.snapshot.jobs.filter((job) => job.id !== jobId);
    if (this.snapshot.selected_job_id === jobId) {
      this.snapshot.selected_job_id = this.snapshot.jobs[0]?.id ?? null;
    }
    this.commit();
  }

  private requireJob(jobId: string): QueueJob {
    const job = this.snapshot.jobs.find((candidate) => candidate.id === jobId);
    if (!job) throw new Error(`Unknown job: ${jobId}`);
    return job;
  }

  private allocateId(): string {
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const id = this.idFactory();
      if (typeof id !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(id)) throw new Error("Invalid generated job ID");
      if (!this.snapshot.jobs.some((job) => job.id === id)) return id;
    }
    throw new Error("Could not allocate an independent job ID");
  }

  private commit(): void {
    const serialized = serializeQueueSnapshot(this.snapshot);
    this.storage.write(serialized);
    const published = cloneSnapshot(this.snapshot);
    for (const listener of this.listeners) listener(published);
  }
}

export class MemorySnapshotStorage implements SnapshotStorage {
  private value: string | null;

  constructor(value: string | null = null) {
    this.value = value;
  }

  read(): string | null {
    return this.value;
  }

  write(serialized: string): void {
    this.value = serialized;
  }
}

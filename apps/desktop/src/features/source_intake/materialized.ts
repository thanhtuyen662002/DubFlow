/** Only native source_downloaded events may enter the local processing queue.
 * Call this with the ordered events returned by source_service_events_v1.
 * This module does not enumerate/download, infer acceptance or rewrite paths.
 */
import { utf8Bytes } from "./model.ts";

export type NativeObservation = { sequence: number; value: unknown };
export type MaterializedSource = {
  receipt_key: string;
  scan_id: string;
  identity_key: string;
  media_path: string;
  sha256: string;
  size_bytes: number;
};
export type MaterializedQueue = {
  addPaths(paths: readonly string[]): string[];
};
export type MaterializedBatch = {
  queued: Array<{ source: MaterializedSource; job_ids: string[] }>;
  already_present: MaterializedSource[];
  rejected: Array<{ sequence: number; code: "SOURCE_MATERIALIZED_INVALID" | "LOCAL_QUEUE_REJECTED" }>;
};
const CONTROL = /\p{Cc}/u;
function text(value: unknown, max: number): value is string {
  return typeof value === "string" && value.length > 0 && utf8Bytes(value) <= max && !CONTROL.test(value);
}
function absoluteFile(value: string): boolean {
  // Preserve even Windows verbatim paths exactly as returned by the native
  // service. This is shape validation, not a replacement for native hashing.
  return value.startsWith("/") || /^[A-Za-z]:[\\/]/u.test(value) || /^\\\\[^\\]+\\[^\\]+\\/u.test(value);
}
export function materializedSource(observation: NativeObservation): MaterializedSource | null {
  if (observation === null || typeof observation !== "object") throw new Error("SOURCE_MATERIALIZED_INVALID");
  const value = observation.value;
  if (value === null || typeof value !== "object" || !("event" in value) || value.event !== "source_downloaded") return null;
  const item = value as Record<string, unknown>;
  if (!Number.isSafeInteger(observation.sequence) || observation.sequence < 1
      || !text(item.scan_id, 128) || !/^[A-Za-z0-9_-]+$/u.test(item.scan_id)
      || !text(item.identity_key, 1024) || !text(item.media_path, 4096) || !absoluteFile(item.media_path)
      || item.media_path.trim() !== item.media_path // QueueController trims; refuse rather than silently change a native path.
      || typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/u.test(item.sha256)
      || !Number.isSafeInteger(item.size_bytes) || (item.size_bytes as number) <= 0) {
    throw new Error("SOURCE_MATERIALIZED_INVALID");
  }
  return { scan_id: item.scan_id, identity_key: item.identity_key, media_path: item.media_path,
    sha256: item.sha256, size_bytes: item.size_bytes as number,
    receipt_key: JSON.stringify([item.scan_id, item.identity_key, item.sha256, item.media_path]) };
}

/** QueueController.addPaths is the persistent path-deduplication boundary.
 * Replay therefore does not add another row, including after queue reopen.
 * Never keep a separate volatile "delivered" flag before durable queue commit.
 * Queue errors are reported per item and may be explicitly replayed/reconciled.
 */
export function enqueueMaterializedSources(observations: readonly NativeObservation[], queue: MaterializedQueue): MaterializedBatch {
  const result: MaterializedBatch = { queued: [], already_present: [], rejected: [] };
  for (const observation of observations) {
    let source: MaterializedSource | null;
    try { source = materializedSource(observation); }
    catch { result.rejected.push({ sequence: observation?.sequence ?? 0, code: "SOURCE_MATERIALIZED_INVALID" }); continue; }
    if (!source) continue;
    try {
      const jobIds = queue.addPaths([source.media_path]);
      if (jobIds.length > 0) result.queued.push({ source, job_ids: jobIds });
      else result.already_present.push(source);
    } catch {
      result.rejected.push({ sequence: observation.sequence, code: "LOCAL_QUEUE_REJECTED" });
    }
  }
  return result;
}

/** One consumer for the host's destructive event drain. Share this pump across
 * source views; it returns all events for status rendering and applies only
 * downloaded receipts to the existing idempotent local queue.
 */
export function createSourceEventPump(queue: MaterializedQueue, invoke: (command: "source_service_events_v1") => Promise<NativeObservation[]>) {
  let inFlight: Promise<{ events: NativeObservation[]; materialized: MaterializedBatch }> | null = null;
  return {
    poll(): Promise<{ events: NativeObservation[]; materialized: MaterializedBatch }> {
      if (inFlight) return inFlight;
      const pending = (async () => {
        const events = await invoke("source_service_events_v1");
        return { events, materialized: enqueueMaterializedSources(events, queue) };
      })();
      inFlight = pending;
      const clear = () => { if (inFlight === pending) inFlight = null; };
      void pending.then(clear, clear);
      return pending;
    },
  };
}

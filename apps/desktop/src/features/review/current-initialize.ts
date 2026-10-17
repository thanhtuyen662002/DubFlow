import { readReviewPayload, type ProductionSnapshot, type CurrentReviewSnapshot } from "./wire.ts";

export type CurrentInitializeRequest = { schema_version: 1; kind: "review_initialize_current"; confirm: true; job_id: string };
const fields = (raw: unknown, keys: readonly string[]): raw is Record<string, unknown> => !!raw && typeof raw === "object" && !Array.isArray(raw) &&
  Object.keys(raw).length === keys.length && keys.every(k => Object.hasOwn(raw, k));

export function currentInitializeRequest(snapshot: ProductionSnapshot): CurrentInitializeRequest {
  const original = readReviewPayload(snapshot);
  if (original.schema_version !== 2 || original.revision !== 0 || original.last_plan !== null) throw new Error("initialization requires an unedited original snapshot");
  return { schema_version: 1, kind: "review_initialize_current", confirm: true, job_id: original.job_id };
}

export function readCurrentInitializeRequest(snapshot: ProductionSnapshot, raw: unknown): CurrentInitializeRequest {
  const expected = currentInitializeRequest(snapshot);
  if (!fields(raw, ["schema_version", "kind", "confirm", "job_id"]) || raw.schema_version !== 1 || raw.kind !== expected.kind || raw.confirm !== true || raw.job_id !== expected.job_id) {
    throw new Error("explicit initialization must refer only to the bound original job");
  }
  return expected;
}

/** The host-held export pin and absent-current native binding authorize this
 * operation. No browser snapshot/hash/path may supply initialization authority. */
export function readInitializedCurrent(previous: ProductionSnapshot, request: CurrentInitializeRequest, raw: unknown): CurrentReviewSnapshot {
  readCurrentInitializeRequest(previous, request);
  if (!fields(raw, ["schema_version", "kind", "job_id", "session_id", "snapshot"]) || raw.schema_version !== 1 || raw.kind !== "review_desktop_initialized_current" || raw.job_id !== previous.job_id ||
    typeof raw.session_id !== "string" || !/^[A-Za-z0-9-]{16,64}$/.test(raw.session_id)) throw new Error("initialized result belongs to another native session");
  const next = readReviewPayload(raw.snapshot);
  if (next.schema_version !== 3 || next.job_id !== previous.job_id || next.current_token.revision !== "0" || next.last_plan !== null) throw new Error("initialization changed original revision or plan");
  const projection = { schema_version: 2, job_id: next.job_id, revision: 0, time_base: next.time_base, findings: next.findings, last_plan: next.last_plan };
  if (JSON.stringify(readReviewPayload(projection)) !== JSON.stringify(readReviewPayload(previous))) throw new Error("initialization changed original source, evidence, cues or preview");
  return next;
}

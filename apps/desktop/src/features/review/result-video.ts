import { readReviewPayload, type ReviewPayload } from "./wire.ts";
import { sameCurrentToken, type NativeCurrentToken } from "./session.ts";

export type ResultVideoExpected = { kind: "original"; revision: 0 } |
  { kind: "current"; current_token: NativeCurrentToken };
export type ResultVideoRequest = { schema_version: 1; kind: "review_open_result_request"; job_id: string; expected: ResultVideoExpected };
export type ResultVideoLaunch = {
  schema_version: 1; kind: "review_result_video_launch_requested"; job_id: string; session_id: string; request_id: string;
  expected: ResultVideoExpected; role: "final_video"; content_sha256: string; size_bytes: string;
  launch_requested: true; managed_playback: false; playback_verified: false;
};
const fields = (raw: unknown, keys: readonly string[]): raw is Record<string, unknown> => !!raw &&
  typeof raw === "object" && !Array.isArray(raw) && Object.keys(raw).length === keys.length && keys.every(k => Object.hasOwn(raw, k));
const hash = (v: unknown) => typeof v === "string" && /^sha256:[0-9a-f]{64}$/.test(v);
const id = (v: unknown) => typeof v === "string" && /^[A-Za-z0-9-]{16,64}$/.test(v);
function closedRequest(raw: unknown): ResultVideoRequest {
  if (!fields(raw, ["schema_version", "kind", "job_id", "expected"]) || raw.schema_version !== 1 || raw.kind !== "review_open_result_request" ||
    typeof raw.job_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(raw.job_id)) throw new Error("invalid result video request");
  const expected = raw.expected;
  if (fields(expected, ["kind", "revision"]) && expected.kind === "original" && expected.revision === 0) return structuredClone(raw) as ResultVideoRequest;
  if (!fields(expected, ["kind", "current_token"]) || expected.kind !== "current" ||
    !fields(expected.current_token, ["job_id", "revision", "snapshot_sha256", "graph_sha256", "original_manifest_sha256"])) throw new Error("invalid result video expected identity");
  const token = expected.current_token;
  if (token.job_id !== raw.job_id || typeof token.revision !== "string" || !/^(0|[1-9][0-9]{0,18})$/.test(token.revision) ||
    BigInt(token.revision) > 9223372036854775807n || !hash(token.snapshot_sha256) || !hash(token.graph_sha256) || !hash(token.original_manifest_sha256)) {
    throw new Error("invalid result video current token");
  }
  return structuredClone(raw) as ResultVideoRequest;
}
function sameExpected(expected: ResultVideoExpected, raw: unknown): boolean {
  return expected.kind === "original" ? fields(raw, ["kind", "revision"]) && raw.kind === "original" && raw.revision === 0 :
    fields(raw, ["kind", "current_token"]) && raw.kind === "current" && sameCurrentToken(expected.current_token, raw.current_token);
}

/** This request carries identity only; no browser path grants media access. */
export function resultVideoRequest(raw: ReviewPayload): ResultVideoRequest {
  const snapshot = readReviewPayload(raw);
  if (snapshot.schema_version === 1 || snapshot.schema_version === 2 && snapshot.revision !== 0) throw new Error("result video requires original0 or complete current identity");
  return closedRequest({ schema_version: 1, kind: "review_open_result_request", job_id: snapshot.job_id,
    expected: snapshot.schema_version === 3 ? { kind: "current", current_token: snapshot.current_token } : { kind: "original", revision: 0 } });
}
export function readResultVideoRequest(snapshot: ReviewPayload, raw: unknown): ResultVideoRequest {
  const expected = resultVideoRequest(snapshot), request = closedRequest(raw);
  if (request.job_id !== expected.job_id || !sameExpected(expected.expected, request.expected)) throw new Error("result video request is stale or foreign");
  return request;
}
/** A bound OS launch acknowledgement never proves player startup or playback. */
export function readResultVideoLaunch(request: ResultVideoRequest, sessionId: string, requestId: string, raw: unknown): ResultVideoLaunch {
  const expected = closedRequest(request);
  if (!id(sessionId) || !id(requestId) || !fields(raw, ["schema_version", "kind", "job_id", "session_id", "request_id", "expected", "role", "content_sha256", "size_bytes", "launch_requested", "managed_playback", "playback_verified"]) ||
    raw.schema_version !== 1 || raw.kind !== "review_result_video_launch_requested" || raw.job_id !== expected.job_id ||
    raw.session_id !== sessionId || raw.request_id !== requestId || !sameExpected(expected.expected, raw.expected) || raw.role !== "final_video" ||
    !hash(raw.content_sha256) || typeof raw.size_bytes !== "string" || !/^[1-9][0-9]{0,11}$/.test(raw.size_bytes) ||
    BigInt(raw.size_bytes) > 137438953472n || raw.launch_requested !== true || raw.managed_playback !== false || raw.playback_verified !== false) {
    throw new Error("unbound result video launch acknowledgement");
  }
  return structuredClone(raw) as ResultVideoLaunch;
}

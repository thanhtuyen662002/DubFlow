import { previewProductionCorrection, readReviewPayload, type ProductionCorrection, type ProductionPlan,
  type ProductionSnapshot, type CurrentReviewSnapshot, type ReviewPayload } from "./wire.ts";

export type ReviewCueRequest = { job_id: string; revision: number; finding_id: string; cue_id: string };
export type NativeCurrentToken = CurrentReviewSnapshot["current_token"];
export type CurrentCueRequest = { job_id: string; current_token: NativeCurrentToken; finding_id: string; cue_id: string };
export type ReviewPreviewRequest = ReviewCueRequest | CurrentCueRequest;
export type ReviewCorrectionRequest = ReviewCueRequest & { correction: ProductionCorrection };

export function sameCurrentToken(expected: NativeCurrentToken, raw: unknown): boolean {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return false;
  const value = raw as Record<string, unknown>;
  const keys = ["job_id", "revision", "snapshot_sha256", "graph_sha256", "original_manifest_sha256"] as const;
  return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key) && value[key] === expected[key]);
}

export function samePreviewRequest(expected: ReviewPreviewRequest, raw: unknown): boolean {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return false;
  const value = raw as Record<string, unknown>;
  const keys = "current_token" in expected ? ["job_id", "current_token", "finding_id", "cue_id"] : ["job_id", "revision", "finding_id", "cue_id"];
  return Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key)) &&
    value.job_id === expected.job_id && value.finding_id === expected.finding_id && value.cue_id === expected.cue_id &&
    ("current_token" in expected ? sameCurrentToken(expected.current_token, value.current_token) : value.revision === expected.revision);
}

export function previewRequest(payload: ReviewPayload, findingId: string): ReviewPreviewRequest {
  if (payload.schema_version === 1) throw new Error("legacy review cannot authorize audio");
  const item = payload.findings.find(finding => finding.finding_id === findingId);
  if (!item?.cue_id || !item.range) throw new Error("finding has no canonical cue mapping");
  if (payload.schema_version === 3) {
    if (payload.mode !== "b2") throw new Error("B1 has no synthesized cue audio");
    return { job_id: payload.job_id, current_token: { ...payload.current_token }, finding_id: item.finding_id, cue_id: item.cue_id };
  }
  return { job_id: payload.job_id, revision: payload.revision, finding_id: item.finding_id, cue_id: item.cue_id };
}

/** Backend mutations still require their own durable CAS and artifact validation. */
export function correctionRequest(payload: ReviewPayload, correction: ProductionCorrection): ReviewCorrectionRequest {
  previewProductionCorrection(payload, correction);
  if (payload.schema_version !== 2) throw new Error("legacy review is display-only");
  const item = payload.findings.find(finding => finding.finding_id === correction.finding_id)!;
  return { job_id: payload.job_id, revision: payload.revision, finding_id: item.finding_id, cue_id: item.cue_id!,
    correction: { finding_id: item.finding_id,
      ...(correction.translation === undefined ? {} : { translation: correction.translation.trim() }),
      ...(correction.voice === undefined ? {} : { voice: correction.voice.trim() }) } };
}

/** A response may only acknowledge this edit; it cannot switch jobs or rewrite other cues. */
export function acceptCorrectionResponse(previous: ProductionSnapshot, request: ReviewCorrectionRequest,
  raw: unknown, expectedPlan: ProductionPlan): ProductionSnapshot {
  const expectedRequest = correctionRequest(previous, request.correction);
  if (JSON.stringify(expectedRequest) !== JSON.stringify(request)) throw new Error("correction request changed");
  const expected = structuredClone(previous);
  expected.revision++;
  expected.last_plan = previewProductionCorrection(previous, request.correction);
  if (JSON.stringify(expected.last_plan) !== JSON.stringify(expectedPlan)) throw new Error("correction plan changed");
  for (const item of expected.findings) {
    if (item.cue_id !== request.cue_id) continue;
    if (request.correction.translation !== undefined) item.translated_text = request.correction.translation;
    if (request.correction.voice !== undefined) item.voice = request.correction.voice;
  }
  const next = readReviewPayload(raw);
  if (next.schema_version !== 2 || JSON.stringify(next) !== JSON.stringify(expected)) {
    throw new Error("correction response is stale or does not match the requested cue");
  }
  return next;
}

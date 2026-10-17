import type { CurrentReviewSnapshot, ProductionCorrection, ProductionPlan } from "./wire.ts";
import { sameCurrentToken, type NativeCurrentToken } from "./session.ts";

export type CurrentPlanRequest = { job_id: string; current_token: NativeCurrentToken; finding_id: string; cue_id: string;
  changes: { translation?: string; voice?: string } };
export type CurrentCorrectionPlan = CurrentPlanRequest & { schema_version: 1; kind: "review_current_correction_plan";
  mode: "b1" | "b2"; plan: ProductionPlan; candidate_revision: string; candidate_snapshot_sha256: string;
  read_only: true; processing_release_admitted: false; active_graph_published: false; execution_ready: false };
const keys = (v: unknown, expected: readonly string[]): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v) &&
  Object.keys(v).length === expected.length && expected.every(k => Object.hasOwn(v, k));

export function currentPlanRequest(snapshot: CurrentReviewSnapshot, correction: ProductionCorrection): CurrentPlanRequest {
  if (Object.keys(correction).some(k => !["finding_id", "translation", "voice"].includes(k))) throw new Error("unsupported current correction");
  const item = snapshot.findings.find(f => f.finding_id === correction.finding_id);
  if (!item?.cue_id || !item.range) throw new Error("current correction has no canonical cue");
  const changes: CurrentPlanRequest["changes"] = {};
  if (correction.translation !== undefined) {
    const text = correction.translation;
    if (typeof text !== "string" || text !== text.trim() || !text || [...text].length > 8192 || /[\u0000-\u001f]/.test(text)) throw new Error("invalid current translation");
    if (text !== item.translated_text) changes.translation = text;
  }
  if (correction.voice !== undefined) {
    const voice = correction.voice;
    if (snapshot.mode !== "b2" || typeof voice !== "string" || !/^[A-Za-z0-9._:-]{1,256}$/.test(voice)) throw new Error("B1 cannot assign synthesized voice or voice is invalid");
    if (voice !== item.voice) changes.voice = voice;
  }
  if (!Object.keys(changes).length) throw new Error("current correction must change content");
  return { job_id: snapshot.job_id, current_token: { ...snapshot.current_token }, finding_id: item.finding_id, cue_id: item.cue_id, changes };
}

export function sameCurrentPlanRequest(expected: CurrentPlanRequest, raw: unknown): boolean {
  const changeKeys = Object.keys(expected.changes);
  if (!changeKeys.length || changeKeys.length > 2 || changeKeys.some(k => !["translation", "voice"].includes(k)) ||
    Object.entries(expected.changes).some(([k, v]) => typeof v !== "string" || !v || v !== v.trim() ||
      [...v].length > (k === "translation" ? 8192 : 256) || /[\u0000-\u001f]/.test(v) ||
      k === "voice" && !/^[A-Za-z0-9._:-]{1,256}$/.test(v))) return false;
  if (!keys(raw, ["job_id", "current_token", "finding_id", "cue_id", "changes"]) || !keys(raw.changes, Object.keys(expected.changes))) return false;
  return raw.job_id === expected.job_id && raw.finding_id === expected.finding_id && raw.cue_id === expected.cue_id &&
    sameCurrentToken(expected.current_token, raw.current_token) &&
    Object.entries(expected.changes).every(([k, v]) => (raw.changes as Record<string, unknown>)[k] === v);
}

/** Validate the native proposal without manufacturing a published next token. */
export function readCurrentPlan(snapshot: CurrentReviewSnapshot, request: CurrentPlanRequest, raw: unknown): CurrentCorrectionPlan {
  const fields = ["schema_version", "kind", "job_id", "current_token", "finding_id", "cue_id", "changes", "mode", "plan", "candidate_revision", "candidate_snapshot_sha256", "read_only", "processing_release_admitted", "active_graph_published", "execution_ready"];
  if (!keys(raw, fields) || raw.schema_version !== 1 || raw.kind !== "review_current_correction_plan" || raw.mode !== snapshot.mode ||
    raw.read_only !== true || raw.processing_release_admitted !== false || raw.active_graph_published !== false || raw.execution_ready !== false) throw new Error("unsupported native correction plan");
  const binding = { job_id: raw.job_id, current_token: raw.current_token, finding_id: raw.finding_id, cue_id: raw.cue_id, changes: raw.changes };
  const expected = currentPlanRequest(snapshot, { finding_id: request.finding_id, ...request.changes });
  if (!sameCurrentPlanRequest(expected, request) || !sameCurrentPlanRequest(request, binding)) throw new Error("current plan token/cue/changes differ");
  if (typeof raw.candidate_revision !== "string" || !/^[1-9][0-9]{0,18}$/.test(raw.candidate_revision) ||
    BigInt(raw.candidate_revision) !== BigInt(request.current_token.revision) + 1n || BigInt(raw.candidate_revision) > (1n << 63n) - 1n ||
    typeof raw.candidate_snapshot_sha256 !== "string" || !/^sha256:[0-9a-f]{64}$/.test(raw.candidate_snapshot_sha256)) throw new Error("invalid native proposed snapshot identity");
  const plan = raw.plan;
  const stages = snapshot.mode === "b1" ? ["translation", "subtitles", "render", "qc", "export"] :
    request.changes.translation !== undefined ? ["translation", "subtitles", "tts", "mix", "render", "qc", "export"] : ["tts", "mix", "render", "qc", "export"];
  if (!keys(plan, ["finding_id", "stages_to_rerun", "preserved_artifacts", "replacement_artifacts", "retain_previous_until_validated", "requires_full_manual_review"]) ||
    plan.finding_id !== request.finding_id || plan.retain_previous_until_validated !== true || plan.requires_full_manual_review !== false ||
    JSON.stringify(plan.stages_to_rerun) !== JSON.stringify(stages) || JSON.stringify(plan.preserved_artifacts) !== JSON.stringify(["asr", "ocr"]) ||
    JSON.stringify(plan.replacement_artifacts) !== JSON.stringify(stages.map(s => "replacement:" + s))) throw new Error("native correction plan scope changed");
  return structuredClone(raw) as CurrentCorrectionPlan;
}

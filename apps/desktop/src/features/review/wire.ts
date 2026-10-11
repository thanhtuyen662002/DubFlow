/** Public review artifacts. This boundary does not execute or persist edits. */
import type { ReviewFinding, ReviewReason, TickRange } from "./model.ts";

export type ProductionFinding = Omit<ReviewFinding, "range" | "confidence"> & {
  range: TickRange | null;
  confidence: string | null;
  cue_id: string | null;
  warning: string | null;
  confidence_source: string | null;
};
export type ProductionPlan = {
  finding_id: string;
  stages_to_rerun: string[];
  preserved_artifacts: string[];
  replacement_artifacts: string[];
  retain_previous_until_validated: true;
  requires_full_manual_review: false;
};
export type ProductionSnapshot = {
  schema_version: 2;
  job_id: string;
  revision: number;
  time_base: { numerator: string; denominator: string };
  findings: ProductionFinding[];
  last_plan: ProductionPlan | null;
};
export type LegacyPayload = { schema_version: 1; findings: ReviewFinding[] };
export type ReviewPayload = LegacyPayload | ProductionSnapshot;
export type ProductionCorrection = { finding_id: string; translation?: string; voice?: string };

const REASONS: ReviewReason[] = ["ASR_OCR_CONFLICT", "SPEAKER_UNCERTAINTY", "TRANSLATION_ANOMALY",
  "TTS_FAILURE", "INPAINT_RISK", "SYNC_QC_WARNING"];
const I64_MAX = (1n << 63n) - 1n;
const FINDING_KEYS = ["finding_id", "reason", "range", "source_text", "translated_text", "speaker",
  "voice", "output_preview", "confidence", "affected_stages"];

function record(value: unknown, keys: readonly string[], name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(name + " must be an object");
  const result = value as Record<string, unknown>;
  if (Object.keys(result).length !== keys.length || keys.some(key => !Object.hasOwn(result, key))) {
    throw new Error(name + " has missing or unsupported fields");
  }
  return result;
}

function text(value: unknown, limit: number, name: string, nonempty = true): string {
  if (typeof value !== "string" || value.length > limit * 2 || [...value].length > limit || (nonempty && (!value.trim() || /[\u0000-\u001f]/.test(value)))) {
    throw new Error(name + " is invalid");
  }
  return value;
}

function id(value: unknown, name: string): string {
  const result = text(value, 256, name);
  if (!/^[A-Za-z0-9._:-]{1,256}$/.test(result)) throw new Error(name + " is invalid");
  return result;
}

function optionalText(value: unknown, limit: number, name: string, legacy = false): string | null {
  return value === null ? null : text(value, limit, name, !legacy);
}

function stages(value: unknown, name: string, legacy = false): string[] {
  if (!Array.isArray(value) || !value.length || value.length > 64) throw new Error(name + " must be a bounded array");
  const result = value.map(item => text(item, 256, name, !legacy));
  if (result.some(item => !item.length) || (!legacy && new Set(result).size !== result.length)) throw new Error(name + " is invalid");
  return result;
}

function ticks(value: unknown, legacy = false): string {
  const result = text(value, legacy ? 20 : 19, "ticks");
  if (!(legacy ? /^(0|-?[1-9][0-9]*)$/ : /^(0|[1-9][0-9]*)$/).test(result) || (!legacy && BigInt(result) > I64_MAX)) {
    throw new Error("ticks must be canonical integer strings");
  }
  return result;
}

function range(value: unknown, legacy = false): TickRange | null {
  if (value === null && !legacy) return null;
  const raw = record(value, ["start_ticks", "end_ticks"], "range");
  const result = { start_ticks: ticks(raw.start_ticks, legacy), end_ticks: ticks(raw.end_ticks, legacy) };
  if (!legacy && BigInt(result.end_ticks) <= BigInt(result.start_ticks)) throw new Error("range must be positive");
  return result;
}

function finding(value: unknown, legacy: boolean): ProductionFinding | ReviewFinding {
  const raw = record(value, legacy ? FINDING_KEYS : [...FINDING_KEYS, "cue_id", "warning", "confidence_source"], "finding");
  if (typeof raw.reason !== "string" || !REASONS.includes(raw.reason as ReviewReason)) throw new Error("reason is invalid");
  const confidence = raw.confidence === null && !legacy ? null : raw.confidence;
  if (confidence !== null && (typeof confidence !== "string" || confidence.length > (legacy ? 64 * 1024 * 1024 : 128) || /[\u0000-\u001f]/.test(confidence) || !/^(0|0\.[0-9]+|1(?:\.0+)?)$/.test(confidence))) throw new Error("confidence is invalid");
  const common = { finding_id: id(raw.finding_id, "finding_id"), reason: raw.reason as ReviewReason,
    range: range(raw.range, legacy), source_text: text(raw.source_text, 8192, "source_text", !legacy),
    translated_text: text(raw.translated_text, 8192, "translated_text", !legacy),
    speaker: optionalText(raw.speaker, 256, "speaker", legacy), voice: optionalText(raw.voice, 256, "voice", legacy),
    output_preview: optionalText(raw.output_preview, 4096, "output_preview", legacy), confidence: confidence as string | null,
    affected_stages: stages(raw.affected_stages, "affected_stages", legacy) };
  if (legacy) return common as ReviewFinding;
  const cue_id = raw.cue_id === null ? null : id(raw.cue_id, "cue_id");
  const confidence_source = optionalText(raw.confidence_source, 256, "confidence_source");
  if (cue_id !== null && common.range === null) throw new Error("bound cue requires an exact range");
  if (confidence === null && confidence_source !== null) throw new Error("unknown confidence cannot claim a measurement");
  return { ...common, cue_id, warning: optionalText(raw.warning, 4096, "warning"), confidence_source };
}

function plan(value: unknown): ProductionPlan | null {
  if (value === null) return null;
  const raw = record(value, ["finding_id", "stages_to_rerun", "preserved_artifacts", "replacement_artifacts",
    "retain_previous_until_validated", "requires_full_manual_review"], "plan");
  if (raw.retain_previous_until_validated !== true || raw.requires_full_manual_review !== false) {
    throw new Error("plan must retain valid artifacts until validation");
  }
  return { finding_id: id(raw.finding_id, "plan finding_id"), stages_to_rerun: stages(raw.stages_to_rerun, "stages_to_rerun"),
    preserved_artifacts: stages(raw.preserved_artifacts, "preserved_artifacts"), replacement_artifacts: stages(raw.replacement_artifacts, "replacement_artifacts"),
    retain_previous_until_validated: true, requires_full_manual_review: false };
}

export function readReviewPayload(value: unknown): ReviewPayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("review payload is invalid");
  const version = (value as Record<string, unknown>).schema_version;
  if (version !== 1 && version !== 2) throw new Error("unsupported review schema_version");
  const raw = record(value, version === 1 ? ["schema_version", "findings"] :
    ["schema_version", "job_id", "revision", "time_base", "findings", "last_plan"], "snapshot");
  if (!Array.isArray(raw.findings) || raw.findings.length > 100000) throw new Error("findings must be a bounded array");
  const findings = raw.findings.map(item => finding(item, version === 1));
  if (new Set(findings.map(item => item.finding_id)).size !== findings.length) throw new Error("duplicate finding_id");
  if (version === 1) return { schema_version: 1, findings: findings as ReviewFinding[] };
  if (typeof raw.revision !== "number" || !Number.isSafeInteger(raw.revision) || raw.revision < 0) {
    throw new Error("revision cannot be represented exactly; reload with a compatible reader");
  }
  const base = record(raw.time_base, ["numerator", "denominator"], "time_base");
  const numerator = ticks(base.numerator), denominator = ticks(base.denominator);
  let left = BigInt(numerator), right = BigInt(denominator);
  if (left === 0n || right === 0n) throw new Error("time_base must be positive");
  while (right) [left, right] = [right, left % right];
  if (left !== 1n) throw new Error("time_base must be reduced");
  const last_plan = plan(raw.last_plan);
  if (last_plan && !findings.some(item => item.finding_id === last_plan.finding_id)) throw new Error("plan references an unknown finding");
  const result: ProductionSnapshot = { schema_version: 2, job_id: id(raw.job_id, "job_id"), revision: raw.revision,
    time_base: { numerator, denominator }, findings: findings as ProductionFinding[], last_plan };
  const cues = new Map<string, string>();
  for (const item of result.findings) {
    if (item.cue_id === null) continue;
    const scope = JSON.stringify([item.range, item.source_text, item.translated_text, item.speaker, item.voice]);
    if (cues.has(item.cue_id) && cues.get(item.cue_id) !== scope) throw new Error("findings disagree on canonical cue mapping");
    cues.set(item.cue_id, scope);
  }
  return result;
}

export function previewProductionCorrection(payload: ReviewPayload, correction: ProductionCorrection): ProductionPlan {
  if (payload.schema_version !== 2) throw new Error("legacy review is display-only");
  const selected = payload.findings.find(item => item.finding_id === correction.finding_id);
  if (!selected) throw new Error("unknown finding");
  if (selected.cue_id === null || selected.range === null) throw new Error("global/unmapped finding cannot authorize cue regeneration");
  const translation = correction.translation === undefined ? selected.translated_text : text(text(correction.translation, 8192, "translation").trim(), 8192, "translation");
  const voice = correction.voice === undefined ? selected.voice : text(text(correction.voice, 256, "voice").trim(), 256, "voice");
  const translationChanged = translation !== selected.translated_text;
  const voiceChanged = voice !== selected.voice;
  if (!translationChanged && !voiceChanged) throw new Error("correction must change translation or voice");
  const stages_to_rerun = translationChanged ? ["translation", "subtitles", "tts", "mix", "render", "qc", "export"] :
    ["tts", "mix", "render", "qc", "export"];
  return { finding_id: selected.finding_id, stages_to_rerun, preserved_artifacts: ["asr", "ocr"],
    replacement_artifacts: stages_to_rerun.map(stage => "replacement:" + stage), retain_previous_until_validated: true,
    requires_full_manual_review: false };
}

export function reviewRangeLabel(payload: ReviewPayload, item: ProductionFinding | ReviewFinding): string {
  if (payload.schema_version === 1) return "Chưa có mốc thời gian";
  if (item.range === null) return "Toàn video";
  const base = payload.time_base;
  const format = (value: string) => {
    const milliseconds = BigInt(value) * BigInt(base.numerator) * 1000n / BigInt(base.denominator);
    const hours = milliseconds / 3600000n;
    const minutes = milliseconds / 60000n % 60n;
    const seconds = milliseconds / 1000n % 60n;
    return `${hours.toString().padStart(2, "0")}:${minutes.toString().padStart(2, "0")}:${seconds.toString().padStart(2, "0")}.${(milliseconds % 1000n).toString().padStart(3, "0")}`;
  };
  return format(item.range.start_ticks) + " – " + format(item.range.end_ticks);
}

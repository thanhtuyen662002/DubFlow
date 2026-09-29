export const REVIEW_SCHEMA_VERSION = 1 as const;
export type ReviewReason =
  | "ASR_OCR_CONFLICT"
  | "SPEAKER_UNCERTAINTY"
  | "TRANSLATION_ANOMALY"
  | "TTS_FAILURE"
  | "INPAINT_RISK"
  | "SYNC_QC_WARNING";

export type TickRange = { start_ticks: string; end_ticks: string };

export type ReviewFinding = {
  finding_id: string;
  reason: ReviewReason;
  range: TickRange;
  source_text: string;
  translated_text: string;
  speaker: string | null;
  voice: string | null;
  output_preview: string | null;
  confidence: string;
  affected_stages: string[];
};

export type ReviewCorrection = {
  finding_id: string;
  translation?: string;
  voice?: string | null;
};

export type RegenerationPlan = {
  finding_id: string;
  stages_to_rerun: string[];
  preserved_artifacts: string[];
  replacement_artifacts: string[];
  retain_previous_until_validated: boolean;
  requires_full_manual_review: false;
};

export type ReviewSnapshot = {
  schema_version: typeof REVIEW_SCHEMA_VERSION;
  findings: ReviewFinding[];
  selected_finding_id: string | null;
  last_plan: RegenerationPlan | null;
};

const REASONS: ReviewReason[] = [
  "ASR_OCR_CONFLICT",
  "SPEAKER_UNCERTAINTY",
  "TRANSLATION_ANOMALY",
  "TTS_FAILURE",
  "INPAINT_RISK",
  "SYNC_QC_WARNING",
];
const I64_MIN = -(1n << 63n);
const I64_MAX = (1n << 63n) - 1n;

function tick(value: string, name: string): bigint {
  if (typeof value !== "string" || !/^(0|-?[1-9][0-9]*)$/.test(value) || value.length > 20) {
    throw new Error(name + " must be a canonical integer tick");
  }
  const result = BigInt(value);
  if (result < I64_MIN || result > I64_MAX) throw new Error(name + " is outside the signed 64-bit range");
  return result;
}

function validateFinding(finding: ReviewFinding): ReviewFinding {
  if (!finding.finding_id || !REASONS.includes(finding.reason)) throw new Error("review finding identity/reason is invalid");
  const start = tick(finding.range.start_ticks, "range.start_ticks");
  const end = tick(finding.range.end_ticks, "range.end_ticks");
  if (start < 0n || end <= start) throw new Error("review range must be positive and non-negative");
  if (!/^(0|0\.[0-9]+|1(?:\.0+)?)$/.test(finding.confidence)) throw new Error("review confidence is invalid");
  if (!finding.affected_stages.length || new Set(finding.affected_stages).size !== finding.affected_stages.length) {
    throw new Error("review finding must identify unique downstream stages");
  }
  return structuredClone(finding);
}

function clone(snapshot: ReviewSnapshot): ReviewSnapshot {
  return structuredClone(snapshot);
}

export function createReviewSnapshot(findings: readonly ReviewFinding[]): ReviewSnapshot {
  const validated = findings.map(validateFinding);
  if (new Set(validated.map((finding) => finding.finding_id)).size !== validated.length) {
    throw new Error("duplicate review finding id");
  }
  return { schema_version: REVIEW_SCHEMA_VERSION, findings: validated, selected_finding_id: null, last_plan: null };
}

export function groupFindings(snapshot: ReviewSnapshot): Record<ReviewReason, ReviewFinding[]> {
  const groups = Object.fromEntries(REASONS.map((reason) => [reason, [] as ReviewFinding[]])) as Record<ReviewReason, ReviewFinding[]>;
  for (const finding of snapshot.findings) groups[finding.reason].push(structuredClone(finding));
  return groups;
}

export class ReviewController {
  private snapshot: ReviewSnapshot;
  private history: ReviewSnapshot[] = [];

  constructor(findings: readonly ReviewFinding[]) {
    this.snapshot = createReviewSnapshot(findings);
  }

  getSnapshot(): ReviewSnapshot {
    return clone(this.snapshot);
  }

  select(findingId: string | null): void {
    if (findingId !== null && !this.finding(findingId)) throw new Error("unknown review finding: " + findingId);
    this.snapshot.selected_finding_id = findingId;
  }

  previewCorrection(correction: ReviewCorrection): RegenerationPlan {
    const finding = this.finding(correction.finding_id);
    if (!finding) throw new Error("unknown review finding: " + correction.finding_id);
    if (correction.translation === undefined && correction.voice === undefined) throw new Error("a correction must change translation or voice");
    if (correction.translation !== undefined && !correction.translation.trim()) throw new Error("translation cannot be empty");
    const stages = [...finding.affected_stages];
    return {
      finding_id: finding.finding_id,
      stages_to_rerun: stages,
      preserved_artifacts: ["asr", "ocr", ...this.snapshot.findings.filter((item) => item.finding_id !== finding.finding_id).map((item) => "finding:" + item.finding_id)],
      replacement_artifacts: stages.map((stage) => "replacement:" + stage),
      retain_previous_until_validated: true,
      requires_full_manual_review: false,
    };
  }

  confirmCorrection(correction: ReviewCorrection): RegenerationPlan {
    const plan = this.previewCorrection(correction);
    const finding = this.finding(correction.finding_id);
    if (!finding) throw new Error("review finding disappeared");
    this.history.push(clone(this.snapshot));
    if (correction.translation !== undefined) finding.translated_text = correction.translation.trim();
    if (correction.voice !== undefined) finding.voice = correction.voice;
    this.snapshot.last_plan = plan;
    return structuredClone(plan);
  }

  undoLastCorrection(): void {
    const previous = this.history.pop();
    if (!previous) throw new Error("no review correction to undo");
    this.snapshot = previous;
  }

  private finding(findingId: string): ReviewFinding | undefined {
    return this.snapshot.findings.find((finding) => finding.finding_id === findingId);
  }
}

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createReviewSnapshot, groupFindings, ReviewController } from "../../apps/desktop/src/features/review/model.ts";
import { readReviewPayload, previewProductionCorrection, reviewRangeLabel } from "../../apps/desktop/src/features/review/wire.ts";

const finding = {
  finding_id: "cue-1",
  reason: "TRANSLATION_ANOMALY",
  range: { start_ticks: "0", end_ticks: "1296000000" },
  source_text: "你好",
  translated_text: "Xin chao",
  speaker: "speaker-1",
  voice: "voice-a",
  output_preview: "preview://cue-1",
  confidence: "0.42",
  affected_stages: ["translation:cue-1", "tts:cue-1", "mix:scene-1", "render:job"],
};

const warning = { ...finding, finding_id: "cue-2", reason: "SYNC_QC_WARNING", range: { start_ticks: "1296000000", end_ticks: "1296009000" } };
const controller = new ReviewController([finding, warning]);
assert.equal(Object.keys(groupFindings(controller.getSnapshot())).length, 6);
assert.equal(groupFindings(controller.getSnapshot()).TRANSLATION_ANOMALY.length, 1);
const plan = controller.previewCorrection({ finding_id: "cue-1", translation: "Xin chào" });
assert.deepEqual(plan.stages_to_rerun, finding.affected_stages);
assert.equal(plan.preserved_artifacts.includes("asr"), true);
assert.equal(plan.preserved_artifacts.includes("ocr"), true);
assert.equal(plan.requires_full_manual_review, false);
controller.confirmCorrection({ finding_id: "cue-1", translation: "Xin chào" });
assert.equal(controller.getSnapshot().findings[0].translated_text, "Xin chào");
controller.undoLastCorrection();
assert.equal(controller.getSnapshot().findings[0].translated_text, "Xin chao");
assert.throws(() => createReviewSnapshot([{ ...finding, range: { start_ticks: "01", end_ticks: "2" } }]));
assert.throws(() => controller.previewCorrection({ finding_id: "cue-1" }));
// Execute the actual Python producer/store serializer. Hosted CI provides
// Python; local qualification supplies the product's owned bundled runtime.
const repositoryRoot = fileURLToPath(new URL("../../", import.meta.url));
const producer = [
  "import sys,json,tempfile,pathlib",
  "sys.path.insert(0," + JSON.stringify(repositoryRoot) + ")",
  "from engine.dubflow.review import ReviewFinding,ReviewSnapshot,ReviewStore,build_review_snapshot",
  "cue=ReviewFinding('finding-a','TTS_FAILURE','9007199254740993','9007199254741993','Hello','Original','speaker-a','voice-a',None,'0.4',('render',),cue_id='cue-a')",
  "global_item=build_review_snapshot('job-review',warnings=('QC_WARNING: advisory',)).findings[0]",
  "snapshot=ReviewSnapshot('job-review',0,(cue,global_item))",
  "with tempfile.TemporaryDirectory(prefix='dubflow-cross-review-') as directory:",
  " store=ReviewStore(pathlib.Path(directory)/'review.json');store.initialize(snapshot)",
  " first,translation_plan=store.commit_correction(snapshot,'finding-a',translation='Corrected')",
  " restored=store.undo(first)",
  " second,voice_plan=store.commit_correction(restored,'finding-a',voice='voice-b')",
  " print(json.dumps({'snapshot':snapshot.to_dict(),'edited':second.to_dict(),'translation_plan':translation_plan.to_dict(),'voice_plan':voice_plan.to_dict()}))",
].join("\n");
const generated = spawnSync(process.env.DUBFLOW_TEST_PYTHON ?? "python", ["-I", "-S", "-B", "-c", producer],
  { encoding: "utf8", timeout: 15000, maxBuffer: 1024 * 1024 });
assert.equal(generated.status, 0, generated.error?.message ?? generated.stderr);
const actual = JSON.parse(generated.stdout);
const payload = readReviewPayload(actual.snapshot);
assert.equal(payload.schema_version, 2);
assert.equal(payload.findings[0].range.start_ticks, "9007199254740993");
assert.equal(BigInt(payload.findings[0].range.start_ticks), 9007199254740993n);
assert.deepEqual(readReviewPayload(actual.edited).last_plan, actual.voice_plan);
assert.deepEqual(previewProductionCorrection(payload, { finding_id: "finding-a", translation: "Corrected" }), actual.translation_plan);
assert.deepEqual(previewProductionCorrection(payload, { finding_id: "finding-a", voice: "voice-b" }), actual.voice_plan);
assert.deepEqual(previewProductionCorrection(payload, { finding_id: "finding-a", translation: "Corrected", voice: "voice-b" }), actual.translation_plan);
assert.throws(() => previewProductionCorrection(payload, { finding_id: "finding-a", translation: " Original ", voice: " voice-a " }), /must change/);
assert.throws(() => previewProductionCorrection(payload, { finding_id: payload.findings[1].finding_id, translation: "Random cue" }), /global\/unmapped/);
assert.equal(reviewRangeLabel(payload, payload.findings[1]), "Toàn video");

const legacyFinding = structuredClone(finding);
legacyFinding.source_text = "";
legacyFinding.range.start_ticks = "-1";
const legacy = readReviewPayload({ schema_version: 1, findings: [legacyFinding] });
assert.equal(legacy.findings[0].source_text, "");
assert.throws(() => previewProductionCorrection(legacy, { finding_id: "cue-1", translation: "Edit" }), /display-only/);
assert.equal(reviewRangeLabel(legacy, legacy.findings[0]), "Chưa có mốc thời gian");
assert.throws(() => readReviewPayload({ schema_version: 1, findings: [], job_id: "old", revision: 0, last_plan: null }), /unsupported fields/);
assert.throws(() => readReviewPayload({ schema_version: 9, findings: [] }), /unsupported/);
assert.throws(() => readReviewPayload({ ...actual.snapshot, revision: Number.MAX_SAFE_INTEGER + 1 }), /exactly/);
assert.throws(() => readReviewPayload({ ...actual.snapshot, time_base: { numerator: "2", denominator: "2000" } }), /reduced/);
assert.throws(() => readReviewPayload({ ...actual.snapshot, time_base: { numerator: "1", denominator: 1000 } }), /invalid/);
assert.throws(() => readReviewPayload({ ...actual.snapshot, unexpected: true }), /unsupported fields/);
const malformed = structuredClone(actual.snapshot);
malformed.findings[0].range.start_ticks = 9007199254740993;
assert.throws(() => readReviewPayload(malformed), /invalid/);
const contradictory = structuredClone(actual.snapshot);
contradictory.findings.push({ ...contradictory.findings[0], finding_id: "another", translated_text: "Contradictory" });
assert.throws(() => readReviewPayload(contradictory), /disagree/);
const fourHours = readReviewPayload({ ...actual.snapshot, time_base: { numerator: "1", denominator: "48000" },
  findings: [{ ...actual.snapshot.findings[0], range: { start_ticks: "691200000", end_ticks: "691248000" } }] });
assert.equal(reviewRangeLabel(fourHours, fourHours.findings[0]), "04:00:00.000 – 04:00:01.000");
console.log("legacy review tests and Python-to-desktop v2 reader/preview tests passed");

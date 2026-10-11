import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createReviewSnapshot, groupFindings, ReviewController } from "../../apps/desktop/src/features/review/model.ts";
import { readReviewPayload, previewProductionCorrection, reviewRangeLabel } from "../../apps/desktop/src/features/review/wire.ts";
import { correctionRequest, acceptCorrectionResponse } from "../../apps/desktop/src/features/review/session.ts";
import { ReviewConnection } from "../../apps/desktop/src/features/review/connection.ts";
import { nativeReviewTransport } from "../../apps/desktop/src/features/review/transport.ts";

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
  " print(json.dumps({'snapshot':snapshot.to_dict(),'first':first.to_dict(),'edited':second.to_dict(),'translation_plan':translation_plan.to_dict(),'voice_plan':voice_plan.to_dict()}))",
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
const request = correctionRequest(payload, { finding_id: "finding-a", translation: " Corrected " });
assert.equal(request.job_id, "job-review");
assert.equal(request.revision, 0);
assert.equal(request.cue_id, "cue-a");
assert.equal(request.correction.translation, "Corrected");
assert.deepEqual(acceptCorrectionResponse(payload, request, actual.first, actual.translation_plan), readReviewPayload(actual.first));
for (const change of [value => { value.job_id = "other-job"; }, value => { value.revision += 1; },
  value => { value.findings[0].source_text = "Changed source"; }, value => { value.findings[1].warning = "Changed warning"; },
  value => { value.findings[0].range.end_ticks = "9007199254742993"; }, value => { value.findings[0].voice = "voice-b"; }]) {
  const invalid = structuredClone(actual.first); change(invalid);
  assert.throws(() => acceptCorrectionResponse(payload, request, invalid, actual.translation_plan), /does not match/);
}
assert.throws(() => acceptCorrectionResponse(payload, { ...request, revision: 3 }, actual.first, actual.translation_plan), /request changed/);
assert.throws(() => acceptCorrectionResponse(payload, request, actual.first, actual.voice_plan), /plan changed/);
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

// A host adapter must not mix a late job reply or stale action with the selection.
let previewCalls = 0, correctionCalls = 0;
const connection = new ReviewConnection({
  async snapshot() { return actual.snapshot; },
  async preview(request, signal) { assert.equal(signal.aborted, false); assert.equal(request.cue_id, "cue-a"); previewCalls++; },
  async correct(value, signal) { assert.equal(signal.aborted, false); assert.deepEqual(value, request); correctionCalls++; return actual.first; },
});
const loaded = await connection.load("job-review");
assert.deepEqual(loaded, payload);
loaded.findings[0].source_text = "A caller cannot mutate the pinned connection";
assert.equal(connection.snapshot().findings[0].source_text, payload.findings[0].source_text);
const actions = connection.actions();
const cueRequest = { job_id: "job-review", revision: 0, finding_id: "finding-a", cue_id: "cue-a" };
for (const invalid of [{ ...cueRequest, job_id: "other-job" }, { ...cueRequest, revision: 1 },
  { ...cueRequest, finding_id: "missing" }, { ...cueRequest, cue_id: "wrong-cue" }]) {
  await assert.rejects(actions.preview(invalid, new AbortController().signal), /does not match/);
}
assert.equal(previewCalls, 0);
await actions.correct(request);
assert.equal(correctionCalls, 1);
assert.deepEqual(connection.snapshot(), readReviewPayload(actual.first));
await assert.rejects(actions.preview(cueRequest, new AbortController().signal), /does not match/);
connection.clear();
await assert.rejects(actions.correct(request), { name: "AbortError" });
assert.equal(correctionCalls, 1);

const loads = [];
const switching = new ReviewConnection({ snapshot(job, signal) { return new Promise((resolve, reject) => loads.push({ job, signal, resolve, reject })); },
  async preview() { throw new Error("must not invoke preview without a selected snapshot"); } });
const oldLoad = switching.load("job-old");
const newLoad = switching.load("job-new");
assert.equal(loads[0].signal.aborted, true);
loads[1].resolve({ ...actual.snapshot, job_id: "job-new" });
assert.equal((await newLoad).job_id, "job-new");
loads[0].resolve({ malformed: "even malformed stale data must not replace the selection" });
assert.equal(await oldLoad, null);
assert.equal(switching.snapshot().job_id, "job-new");
const failedOld = switching.load("job-failing");
const latest = switching.load("job-latest");
loads[2].reject(new Error("old backend failure"));
assert.equal(await failedOld, null);
loads[3].resolve({ ...actual.snapshot, job_id: "job-latest" });
assert.equal((await latest).job_id, "job-latest");
switching.dispose();
await assert.rejects(switching.load("job-review"), /disposed/);

let releasedPreview, activePreviewSignal;
const listening = new ReviewConnection({ async snapshot() { return actual.snapshot; },
  preview(_request, signal) { activePreviewSignal = signal; return new Promise(resolve => { releasedPreview = resolve; }); } });
await listening.load("job-review");
// The producer's cue has no WAV descriptor. A native handler must not be called.
await assert.rejects(listening.actions().preview(cueRequest, new AbortController().signal), /no validated preview/);
const previewSnapshot = structuredClone(actual.snapshot);
previewSnapshot.findings[0].output_preview = "opaque:validated-native-preview";
const cancellable = new ReviewConnection({ async snapshot() { return previewSnapshot; },
  preview(_request, signal) { activePreviewSignal = signal; return new Promise(resolve => { releasedPreview = resolve; }); } });
await cancellable.load("job-review");
const viewAbort = new AbortController();
const pendingPreview = cancellable.actions().preview(cueRequest, viewAbort.signal);
viewAbort.abort();
assert.equal(activePreviewSignal.aborted, true);
releasedPreview();
await assert.rejects(pendingPreview, { name: "AbortError" });
const pendingJobPreview = cancellable.actions().preview(cueRequest, new AbortController().signal);
cancellable.clear();
assert.equal(activePreviewSignal.aborted, true);
releasedPreview();
await assert.rejects(pendingJobPreview, { name: "AbortError" });
const wrongJob = new ReviewConnection({ async snapshot() { return actual.snapshot; }, async preview() {} });
await assert.rejects(wrongJob.load("job-other"), /does not belong/);
assert.equal(wrongJob.snapshot(), null);
let uncertainCalls = 0;
const uncertain = new ReviewConnection({ async snapshot() { return actual.snapshot; }, async preview() {},
  async correct() { uncertainCalls++; throw new Error("response was lost after submission"); } });
await uncertain.load("job-review");
const uncertainActions = uncertain.actions();
await assert.rejects(uncertainActions.correct(request), /response was lost/);
assert.equal(uncertain.snapshot(), null);
await assert.rejects(uncertainActions.correct(request), { name: "AbortError" });
assert.equal(uncertainCalls, 1);
await uncertain.load("job-review");
await assert.rejects(uncertainActions.correct(request), { name: "AbortError" });
const mismatch = new ReviewConnection({ async snapshot() { return actual.snapshot; }, async preview() {},
  async correct() { return { ...actual.first, job_id: "other-job" }; } });
await mismatch.load("job-review");
await assert.rejects(mismatch.actions().correct(request), /does not match/);
assert.equal(mismatch.snapshot(), null);
// Two callers must not submit the same revision while a correction is pending.
const mutations = [];
const serial = new ReviewConnection({ async snapshot(job) { return { ...actual.snapshot, job_id: job }; }, async preview() {},
  correct(value, signal) { return new Promise((resolve, reject) => mutations.push({ value, signal, resolve, reject })); } });
await serial.load("job-review");
const serialActions = serial.actions();
const pendingCorrection = serialActions.correct(request);
await assert.rejects(serialActions.correct(request), /already in progress/);
assert.equal(mutations.length, 1);
assert.equal(serial.snapshot().revision, 0);
mutations[0].resolve(actual.first);
await pendingCorrection;
assert.deepEqual(serial.snapshot(), readReviewPayload(actual.first));
// A cancelled old write's late error must not clear or unlock a newer selection.
await serial.load("job-review");
const oldMutation = serial.actions().correct(request);
const oldRefusal = assert.rejects(oldMutation, /late old failure/);
const newSnapshot = await serial.load("job-new");
assert.equal(mutations[1].signal.aborted, true);
const newRequest = correctionRequest(newSnapshot, { finding_id: "finding-a", translation: "Corrected" });
const newActions = serial.actions();
const newMutation = newActions.correct(newRequest);
mutations[1].reject(new Error("late old failure"));
await oldRefusal;
assert.equal(serial.snapshot().job_id, "job-new");
await assert.rejects(newActions.correct(newRequest), /already in progress/);
assert.equal(mutations.length, 3);
mutations[2].resolve({ ...actual.first, job_id: "job-new" });
await newMutation;
assert.equal(serial.snapshot().revision, 1);
// Native transport binds one opaque host session and never passes a path/hash.
const nativeCalls = [], nativeReplies = [];
let nativeId = 0, played = 0, playingSignal, completePlay;
const native = nativeReviewTransport((command, args) => {
  nativeCalls.push({ command, args });
  if (command === "review_cancel_v1") return Promise.resolve();
  return new Promise((resolve, reject) => nativeReplies.push({ command, args, resolve, reject }));
}, async (_bytes, signal) => { played++; playingSignal = signal; await new Promise(resolve => { completePlay = resolve; }); },
() => "native-request-" + String(++nativeId).padStart(16, "0"));
const nativeConnection = new ReviewConnection(native);
const nativeLoad = nativeConnection.load("job-review");
nativeReplies[0].resolve({ schema_version: 1, kind: "review_desktop_session", session_id: nativeReplies[0].args.requestId, snapshot: previewSnapshot });
await nativeLoad;
const nativeViewSignal = new AbortController();
const nativePreview = nativeConnection.actions().preview(cueRequest, nativeViewSignal.signal);
assert.deepEqual(Object.keys(nativeReplies[1].args).sort(), ["request", "requestId", "sessionId"]);
assert.deepEqual(nativeReplies[1].args.request, cueRequest);
assert.equal(nativeReplies[1].args.sessionId, nativeReplies[0].args.requestId);
nativeViewSignal.abort();
assert(nativeCalls.some(call => call.command === "review_cancel_v1" && call.args.requestId === nativeReplies[1].args.requestId));
nativeReplies[1].resolve(new ArrayBuffer(44));
await assert.rejects(nativePreview, { name: "AbortError" });
assert.equal(played, 0);
const liveAudio = nativeConnection.actions().preview(cueRequest, new AbortController().signal);
nativeReplies[2].resolve(new ArrayBuffer(44));
await new Promise(resolve => setImmediate(resolve));
assert.equal(played, 1);
nativeConnection.clear();
assert.equal(playingSignal.aborted, true);
assert(nativeCalls.some(call => call.command === "review_cancel_v1" && call.args.requestId === nativeReplies[0].args.requestId));
completePlay();
await assert.rejects(liveAudio, { name: "AbortError" });
const reloadedNative = nativeConnection.load("job-review");
nativeReplies[3].resolve({ schema_version: 1, kind: "review_desktop_session", session_id: nativeReplies[3].args.requestId, snapshot: previewSnapshot });
await reloadedNative;
const malformedAudio = nativeConnection.actions().preview(cueRequest, new AbortController().signal);
nativeReplies[4].resolve([82, 73, 70, 70]);
await assert.rejects(malformedAudio, /invalid review audio/);
assert.equal(played, 1);
nativeConnection.dispose();
const badSession = nativeConnection.load("job-review");
await assert.rejects(badSession, /disposed/);
const badNativeConnection = new ReviewConnection(native);
const badSessionReply = badNativeConnection.load("job-review");
nativeReplies[5].resolve({ schema_version: 1, kind: "review_desktop_session", session_id: "another-session", snapshot: previewSnapshot });
await assert.rejects(badSessionReply, /invalid review session/);
assert(nativeCalls.some(call => call.command === "review_cancel_v1" && call.args.requestId === nativeReplies[5].args.requestId));
badNativeConnection.dispose();
console.log("legacy review, Python-to-desktop v2 and native session/audio cancellation tests passed");

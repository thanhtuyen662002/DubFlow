import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createReviewSnapshot, groupFindings, ReviewController } from "../../apps/desktop/src/features/review/model.ts";
import { readReviewPayload, previewProductionCorrection, reviewRangeLabel } from "../../apps/desktop/src/features/review/wire.ts";
import { correctionRequest, acceptCorrectionResponse, previewRequest } from "../../apps/desktop/src/features/review/session.ts";
import { ReviewConnection } from "../../apps/desktop/src/features/review/connection.ts";
import { nativeReviewTransport } from "../../apps/desktop/src/features/review/transport.ts";
import { currentPlanRequest, sameCurrentPlanRequest, readCurrentPlan } from "../../apps/desktop/src/features/review/current-plan.ts";
import { currentConfirmation, readCurrentConfirmation, readAppliedCurrent } from "../../apps/desktop/src/features/review/current-apply.ts";
import { currentUndoRequest, readCurrentUndoRequest, readUndoneCurrent } from "../../apps/desktop/src/features/review/current-undo.ts";
import { currentExportRequest, readCurrentExportRequest, readExportedCurrent } from "../../apps/desktop/src/features/review/current-export.ts";
import { currentInitializeRequest, readCurrentInitializeRequest, readInitializedCurrent } from "../../apps/desktop/src/features/review/current-initialize.ts";

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
assert.equal(nativeReplies[0].command, "review_snapshot_v2");
nativeReplies[0].resolve({ schema_version: 2, kind: "review_desktop_current_session", session_id: nativeReplies[0].args.requestId, snapshot: previewSnapshot });
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
nativeReplies[3].resolve({ schema_version: 2, kind: "review_desktop_current_session", session_id: nativeReplies[3].args.requestId, snapshot: previewSnapshot });
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
nativeReplies[5].resolve({ schema_version: 2, kind: "review_desktop_current_session", session_id: "another-session", snapshot: previewSnapshot });
await assert.rejects(badSessionReply, /invalid review session/);
assert(nativeCalls.some(call => call.command === "review_cancel_v1" && call.args.requestId === nativeReplies[5].args.requestId));
badNativeConnection.dispose();
// This is display/transport evidence. Native physical admission and installed
// GUI execution are separate gates; these opaque token fixtures grant neither.
const currentView = { schema_version: 3, kind: "review_current_snapshot", job_id: actual.edited.job_id,
  current_token: { job_id: actual.edited.job_id, revision: "9007199254740993",
    snapshot_sha256: "sha256:" + "a".repeat(64), graph_sha256: "sha256:" + "b".repeat(64), original_manifest_sha256: "sha256:" + "c".repeat(64) },
  mode: "b2", time_base: actual.edited.time_base, findings: actual.edited.findings, last_plan: actual.edited.last_plan,
  correction_ready: false };
const currentRead = readReviewPayload(currentView);
assert.deepEqual(currentRead, currentView);
assert.equal(currentRead.current_token.revision, "9007199254740993");
assert.equal(Object.hasOwn(currentRead, "revision"), false);
assert.equal(reviewRangeLabel(currentRead, currentRead.findings[0]), reviewRangeLabel(payload, payload.findings[0]));
for (const change of [v => { v.current_token.revision = 9007199254740992; }, v => { v.current_token.revision = "09007199254740993"; },
  v => { v.current_token.revision = "9223372036854775808"; }, v => { v.current_token.job_id = "another-job"; },
  v => { v.current_token.graph_sha256 = "untrusted"; }, v => { v.mode = "unknown"; }, v => { v.correction_ready = true; },
  v => { v.revision = 0; }, v => { v.findings[0].range.end_ticks = "0"; }]) {
  const forged = structuredClone(currentView); change(forged); assert.throws(() => readReviewPayload(forged));
}
assert.throws(() => correctionRequest(currentRead, { finding_id: currentRead.findings[0].finding_id, translation: "Another" }), /display-only/);
let unsafePreview = 0, unsafeCorrection = 0;
const currentConnection = new ReviewConnection({ async snapshot() { return currentView; }, async preview() { unsafePreview++; },
  async correct() { unsafeCorrection++; return actual.first; } });
await currentConnection.load(currentView.job_id);
assert.deepEqual(currentConnection.snapshot().current_token, currentView.current_token);
assert.equal(typeof currentConnection.actions().preview, "function");
assert.equal(currentConnection.actions().correct, undefined);
await assert.rejects(currentConnection.actions().preview(previewRequest(currentRead, currentRead.findings[0].finding_id), new AbortController().signal), /no validated preview/);
assert.equal(unsafePreview + unsafeCorrection, 0);
currentConnection.dispose();
let currentNativeCalls = 0, currentPlayed = 0;
const currentNative = nativeReviewTransport(async (command, args) => {
  currentNativeCalls++;
  assert.equal(command, "review_snapshot_v2");
  return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: currentView };
}, async () => { currentPlayed++; }, () => "native-current-session-0001");
const currentAbort = new AbortController();
assert.deepEqual(await currentNative.snapshot(currentView.job_id, currentAbort.signal), currentView);
await assert.rejects(currentNative.preview({ job_id: currentView.job_id, revision: 0, finding_id: "finding-a", cue_id: "cue-a" }, currentAbort.signal), /complete token/);
assert.equal(currentNativeCalls, 1);
assert.equal(currentPlayed, 0);
// Current audio uses all five native token fields. The cue's display hint is
// not included in IPC and cannot choose a waveform. This mock verifies client
// routing/cancellation only; it does not execute native physical admission.
const playable = structuredClone(currentView);
playable.findings[0].output_preview = "native-current-cue-descriptor";
const audioCalls = [], audioPending = [];
let currentAudioId = 0, currentAudioPlayed = 0, deferCurrentAudio = false;
const fullTokenTransport = nativeReviewTransport(async (command, args) => {
  audioCalls.push({ command, args });
  if (command === "review_cancel_v1") return;
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: playable };
  assert.equal(command, "review_audio_v2");
  assert.deepEqual(Object.keys(args.request).sort(), ["cue_id", "current_token", "finding_id", "job_id"]);
  assert.deepEqual(args.request.current_token, playable.current_token);
  if (deferCurrentAudio) return await new Promise(resolve => audioPending.push({ args, resolve }));
  return new ArrayBuffer(44);
}, async () => { currentAudioPlayed++; }, () => "current-audio-request-" + String(++currentAudioId).padStart(8, "0"));
const fullTokenConnection = new ReviewConnection(fullTokenTransport);
await fullTokenConnection.load(playable.job_id);
const fullRequest = previewRequest(fullTokenConnection.snapshot(), playable.findings[0].finding_id);
assert.equal(fullRequest.current_token.revision, "9007199254740993");
assert.equal(Object.hasOwn(fullRequest, "revision"), false);
await fullTokenConnection.actions().preview(fullRequest, new AbortController().signal);
assert.equal(currentAudioPlayed, 1);
const successfulCalls = audioCalls.filter(call => call.command === "review_audio_v2").length;
for (const change of [r => { r.current_token.revision = "2"; }, r => { r.current_token.graph_sha256 = "sha256:" + "e".repeat(64); },
  r => { r.current_token.snapshot_sha256 = "sha256:" + "f".repeat(64); }, r => { r.current_token.original_manifest_sha256 = "sha256:" + "0".repeat(64); },
  r => { r.current_token.job_id = "other-job"; }, r => { r.path = "C:/untrusted.wav"; }, r => { r.revision = 0; }]) {
  const forged = structuredClone(fullRequest); change(forged);
  await assert.rejects(fullTokenConnection.actions().preview(forged, new AbortController().signal), /does not match/);
}
assert.equal(audioCalls.filter(call => call.command === "review_audio_v2").length, successfulCalls);
deferCurrentAudio = true;
const lateCurrentAudio = fullTokenConnection.actions().preview(fullRequest, new AbortController().signal);
await new Promise(resolve => setImmediate(resolve));
assert.equal(audioPending.length, 1);
fullTokenConnection.clear();
assert(audioCalls.some(call => call.command === "review_cancel_v1" && call.args.requestId === audioPending[0].args.requestId));
audioPending[0].resolve(new ArrayBuffer(44));
await assert.rejects(lateCurrentAudio, { name: "AbortError" });
assert.equal(currentAudioPlayed, 1);
const b1Connection = new ReviewConnection({ async snapshot() { return { ...playable, mode: "b1" }; }, async preview() { throw new Error("B1 must not synthesize preview"); } });
await b1Connection.load(playable.job_id);
assert.equal(b1Connection.actions().preview, undefined);
assert.throws(() => previewRequest(b1Connection.snapshot(), playable.findings[0].finding_id), /B1/);
b1Connection.dispose(); fullTokenConnection.dispose();
// Native proposal envelopes are independent test responses, never fake published
// snapshots. The connection must retain the original current token throughout.
function planResponse(snapshot, request) {
  const stages = snapshot.mode === "b1" ? ["translation", "subtitles", "render", "qc", "export"] :
    request.changes.translation ? ["translation", "subtitles", "tts", "mix", "render", "qc", "export"] : ["tts", "mix", "render", "qc", "export"];
  return { schema_version: 1, kind: "review_current_correction_plan", ...structuredClone(request), mode: snapshot.mode,
    plan: { finding_id: request.finding_id, stages_to_rerun: stages, preserved_artifacts: ["asr", "ocr"], replacement_artifacts: stages.map(s => "replacement:" + s),
      retain_previous_until_validated: true, requires_full_manual_review: false },
    candidate_revision: String(BigInt(snapshot.current_token.revision) + 1n), candidate_snapshot_sha256: "sha256:" + "8".repeat(64),
    read_only: true, processing_release_admitted: false, active_graph_published: false, execution_ready: false };
}
const planCalls = [], pendingPlans = []; let planId = 0, deferPlan = false;
const plannerTransport = nativeReviewTransport(async (command, args) => {
  planCalls.push({ command, args });
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(playable) };
  if (command === "review_plan_v1") {
    if (deferPlan) return new Promise(resolve => pendingPlans.push({ args, resolve }));
    return planResponse(playable, args.request);
  }
  if (command === "review_cancel_v1") return;
  throw new Error("plan preview must not execute correction or media work");
}, async () => { throw new Error("planning must not play audio"); }, () => `request-plan-${++planId}`);
const planner = new ReviewConnection(plannerTransport);
await planner.load(playable.job_id);
const beforePlan = structuredClone(planner.snapshot());
const proposalRequest = currentPlanRequest(beforePlan, { finding_id: beforePlan.findings[0].finding_id, translation: "Bản dịch mới được kiểm tra" });
const proposed = await planner.actions().planCurrent(proposalRequest, new AbortController().signal);
assert.equal(proposed.candidate_revision, "9007199254740994");
assert.equal(proposed.execution_ready, false);
assert.deepEqual(proposed.plan.stages_to_rerun, ["translation", "subtitles", "tts", "mix", "render", "qc", "export"]);
assert.deepEqual(planner.snapshot(), beforePlan);
assert.equal(planner.actions().correct, undefined);
const plannedCalls = planCalls.filter(c => c.command === "review_plan_v1").length;
for (const change of [r => { r.current_token.revision = "0"; }, r => { r.current_token.job_id = "foreign"; },
  r => { r.current_token.snapshot_sha256 = "sha256:" + "d".repeat(64); }, r => { r.current_token.graph_sha256 = "sha256:" + "e".repeat(64); },
  r => { r.current_token.original_manifest_sha256 = "sha256:" + "f".repeat(64); }, r => { r.cue_id = "foreign"; },
  r => { r.path = "D:/foreign.json"; }, r => { r.changes.path = "D:/foreign.json"; }, r => { r.changes.translation = ""; }]) {
  const forged = structuredClone(proposalRequest); change(forged);
  assert.notDeepEqual(forged, proposalRequest);
  await assert.rejects(planner.actions().planCurrent(forged, new AbortController().signal));
}
assert.equal(planCalls.filter(c => c.command === "review_plan_v1").length, plannedCalls);
for (const change of [r => { r.execution_ready = true; }, r => { r.active_graph_published = true; }, r => { r.processing_release_admitted = true; },
  r => { r.read_only = false; }, r => { r.candidate_revision = "2"; }, r => { r.current_token.revision = "2"; },
  r => { r.changes.translation = "Substituted"; }, r => { r.plan.stages_to_rerun.reverse(); }, r => { r.plan.preserved_artifacts = []; },
  r => { r.plan.replacement_artifacts.pop(); }, r => { r.plan.retain_previous_until_validated = false; }, r => { r.path = "foreign"; }]) {
  const forged = planResponse(beforePlan, proposalRequest); change(forged);
  assert.throws(() => readCurrentPlan(beforePlan, proposalRequest, forged));
}
const voiceProposal = currentPlanRequest(beforePlan, { finding_id: beforePlan.findings[0].finding_id, voice: "voice-for-plan" });
assert.deepEqual(readCurrentPlan(beforePlan, voiceProposal, planResponse(beforePlan, voiceProposal)).plan.stages_to_rerun, ["tts", "mix", "render", "qc", "export"]);
const b1PlanSnapshot = structuredClone(beforePlan); b1PlanSnapshot.mode = "b1";
for (const finding of b1PlanSnapshot.findings) finding.voice = null;
const b1Plan = currentPlanRequest(b1PlanSnapshot, { finding_id: b1PlanSnapshot.findings[0].finding_id, translation: "Phụ đề B1 mới" });
assert.deepEqual(readCurrentPlan(b1PlanSnapshot, b1Plan, planResponse(b1PlanSnapshot, b1Plan)).plan.stages_to_rerun, ["translation", "subtitles", "render", "qc", "export"]);
assert.throws(() => currentPlanRequest(b1PlanSnapshot, { finding_id: b1PlanSnapshot.findings[0].finding_id, voice: "invented" }), /B1/);
const b1Planning = new ReviewConnection({ async snapshot() { return b1PlanSnapshot; }, async preview() { throw new Error("no B1 TTS"); },
  async planCurrent(request) { return planResponse(b1PlanSnapshot, request); } });
await b1Planning.load(b1PlanSnapshot.job_id);
assert.equal(b1Planning.actions().preview, undefined);
assert.deepEqual((await b1Planning.actions().planCurrent(b1Plan, new AbortController().signal)).plan.stages_to_rerun, ["translation", "subtitles", "render", "qc", "export"]);
assert.deepEqual(b1Planning.snapshot(), b1PlanSnapshot); b1Planning.dispose();
assert.equal(sameCurrentPlanRequest(b1Plan, { ...b1Plan, changes: { ...b1Plan.changes, path: "foreign" } }), false);
deferPlan = true;
const latePlan = planner.actions().planCurrent(proposalRequest, new AbortController().signal);
await new Promise(resolve => setImmediate(resolve));
assert.equal(pendingPlans.length, 1);
planner.clear();
assert(planCalls.some(c => c.command === "review_cancel_v1" && c.args.requestId === pendingPlans[0].args.requestId));
pendingPlans[0].resolve(planResponse(beforePlan, proposalRequest));
await assert.rejects(latePlan, { name: "AbortError" });
assert.equal(planner.snapshot(), null); planner.dispose();
function appliedResponse(before, confirmation, sessionId) {
  const next = structuredClone(before), plan = confirmation.plan;
  next.current_token.revision = plan.candidate_revision;
  next.current_token.snapshot_sha256 = "sha256:" + "9".repeat(64);
  next.current_token.graph_sha256 = "sha256:" + "7".repeat(64);
  next.last_plan = structuredClone(plan.plan);
  for (const item of next.findings) {
    item.output_preview = "native-retained-render-after-apply";
    if (item.cue_id !== plan.cue_id) continue;
    if (plan.changes.translation !== undefined) item.translated_text = plan.changes.translation;
    if (plan.changes.voice !== undefined) item.voice = plan.changes.voice;
  }
  return { schema_version: 1, kind: "review_desktop_applied_current", before_token: structuredClone(before.current_token), session_id: sessionId, snapshot: next };
}
const explicit = currentConfirmation(beforePlan, planResponse(beforePlan, proposalRequest));
const applied = appliedResponse(beforePlan, explicit, "request-apply-result-0001");
assert.equal(readAppliedCurrent(beforePlan, explicit, applied).current_token.revision, "9007199254740994");
assert.notEqual(applied.snapshot.current_token.snapshot_sha256, explicit.plan.candidate_snapshot_sha256);
assert.deepEqual(beforePlan, playable);
for (const change of [r => { r.confirm = false; }, r => { r.path = "D:/foreign"; }, r => { r.plan.current_token.revision = "0"; }]) {
  const forged = structuredClone(explicit); change(forged);
  assert.throws(() => readCurrentConfirmation(beforePlan, forged));
}
// The client validates structure/scope, not a hash over durable snapshot bytes.
// An otherwise valid substituted text is refused by the host-held saved-plan
// comparison (native unit case), not authenticated by a browser hash claim.
const clientOnlyChange = structuredClone(explicit); clientOnlyChange.plan.changes.translation = "Substituted";
assert.equal(readCurrentConfirmation(beforePlan, clientOnlyChange).plan.changes.translation, "Substituted");
for (const change of [r => { r.before_token.graph_sha256 = "sha256:" + "f".repeat(64); }, r => { r.snapshot.current_token.revision = "0"; },
  r => { r.snapshot.current_token.original_manifest_sha256 = "sha256:" + "0".repeat(64); }, r => { r.snapshot.current_token.graph_sha256 = beforePlan.current_token.graph_sha256; },
  r => { r.snapshot.findings[0].source_text = "Lost source"; }, r => { r.snapshot.findings[0].range.start_ticks = "1"; },
  r => { r.snapshot.findings[0].confidence = "0.99"; }, r => { r.snapshot.findings[0].translated_text = "Different edit"; },
  r => { r.snapshot.last_plan.stages_to_rerun.reverse(); }, r => { r.snapshot.findings[0].output_preview = null; },
  r => { r.snapshot.mode = "b1"; }, r => { r.path = "foreign"; }, r => { r.snapshot.findings.pop(); }]) {
  const forged = structuredClone(applied); change(forged);
  assert.notDeepEqual(forged, applied); assert.throws(() => readAppliedCurrent(beforePlan, explicit, forged));
}
let applyId = 0, waitApply = false, appliedClientView = structuredClone(beforePlan);
const applyCalls = [], applyPending = [];
const applyTransport = nativeReviewTransport(async (command, args) => {
  applyCalls.push({ command, args });
  if (command === "review_cancel_v1") return;
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(appliedClientView) };
  if (command === "review_plan_v1") return planResponse(appliedClientView, args.request);
  if (command === "review_apply_v1") {
    assert.deepEqual(Object.keys(args).sort(), ["confirmation", "requestId", "sessionId"]);
    assert.equal(args.confirmation.confirm, true);
    const response = appliedResponse(appliedClientView, args.confirmation, args.requestId);
    if (waitApply) return new Promise(resolve => applyPending.push({ args, response, resolve }));
    appliedClientView = structuredClone(response.snapshot); return response;
  }
  if (command === "review_audio_v2") return new ArrayBuffer(44);
  throw new Error("unsupported apply route");
}, async () => {}, () => `request-apply-native-${++applyId}`);
const applying = new ReviewConnection(applyTransport);
await applying.load(beforePlan.job_id);
const shown = await applying.actions().planCurrent(proposalRequest, new AbortController().signal);
const confirmed = currentConfirmation(applying.snapshot(), shown);
const updated = await applying.actions().applyCurrent(confirmed, new AbortController().signal);
assert.equal(updated.current_token.revision, "9007199254740994");
assert.deepEqual(applying.snapshot(), updated);
const nativeApply = applyCalls.find(c => c.command === "review_apply_v1");
await applying.actions().preview(previewRequest(updated, updated.findings[0].finding_id), new AbortController().signal);
assert.equal(applyCalls.findLast(c => c.command === "review_audio_v2").args.sessionId, nativeApply.args.requestId);
await assert.rejects(applying.actions().applyCurrent(confirmed, new AbortController().signal));
assert.equal(applyCalls.filter(c => c.command === "review_apply_v1").length, 1);

const retryRequest = currentPlanRequest(updated, { finding_id: updated.findings[0].finding_id, voice: "voice-for-next-apply" });
const nextPlan = await applying.actions().planCurrent(retryRequest, new AbortController().signal);
const nextConfirmation = currentConfirmation(updated, nextPlan);
waitApply = true;
const applyingAbort = new AbortController(), pendingApply = applying.actions().applyCurrent(nextConfirmation, applyingAbort.signal);
await new Promise(resolve => setImmediate(resolve));
assert.equal(applyPending.length, 1);
await assert.rejects(applying.actions().applyCurrent(nextConfirmation, new AbortController().signal), /already in progress/);
applyingAbort.abort();
assert(applyCalls.some(c => c.command === "review_cancel_v1" && c.args.requestId === applyPending[0].args.requestId));
applyPending[0].resolve(applyPending[0].response);
await assert.rejects(pendingApply, { name: "AbortError" });
assert.equal(applying.snapshot(), null);
assert.equal(applyCalls.filter(c => c.command === "review_apply_v1").length, 2);
applying.dispose();

let uncertainApplyCalls = 0;
const uncertainApply = new ReviewConnection({ async snapshot() { return beforePlan; }, async preview() {},
  async applyCurrent() { uncertainApplyCalls++; throw new Error("reply lost after possible commit"); } });
await uncertainApply.load(beforePlan.job_id);
await assert.rejects(uncertainApply.actions().applyCurrent(explicit, new AbortController().signal), /reply lost/);
assert.equal(uncertainApplyCalls, 1); assert.equal(uncertainApply.snapshot(), null);
assert.throws(() => uncertainApply.actions(), /has not loaded/); uncertainApply.dispose();
const b1Confirmed = currentConfirmation(b1PlanSnapshot, planResponse(b1PlanSnapshot, b1Plan));
assert.equal(readAppliedCurrent(b1PlanSnapshot, b1Confirmed, appliedResponse(b1PlanSnapshot, b1Confirmed, "request-apply-b1-0001")).mode, "b1");
// Undo restores native prior content at a NEW public revision. It never turns a
// browser-supplied target into authority or reuses a pre-undo public counter.
function undoneResponse(before, prior, sessionId) {
  const next = structuredClone(prior);
  next.current_token = { ...before.current_token, revision: String(BigInt(before.current_token.revision) + 1n),
    snapshot_sha256: "sha256:" + "6".repeat(64), graph_sha256: prior.current_token.graph_sha256 };
  return { schema_version: 1, kind: "review_desktop_undone_current", before_token: structuredClone(before.current_token), session_id: sessionId, snapshot: next };
}
const undoBefore = structuredClone(updated), undoRequest = currentUndoRequest(undoBefore);
const undoneResult = undoneResponse(undoBefore, beforePlan, "request-undo-result-0001");
assert.equal(readUndoneCurrent(undoBefore, undoRequest, undoneResult).current_token.revision, "9007199254740995");
assert.equal(undoneResult.snapshot.findings[0].translated_text, beforePlan.findings[0].translated_text);
assert.notEqual(undoneResult.snapshot.current_token.revision, beforePlan.current_token.revision);
for (const change of [r => { r.confirm = false; }, r => { r.target_snapshot = beforePlan; }, r => { r.path = "D:/foreign"; },
  r => { r.current_token.revision = "0"; }, r => { r.current_token.graph_sha256 = beforePlan.current_token.graph_sha256; },
  r => { r.current_token.original_manifest_sha256 = "sha256:" + "f".repeat(64); }, r => { r.current_token.revision = 9007199254740994; }]) {
  const forged = structuredClone(undoRequest); change(forged); assert.notDeepEqual(forged, undoRequest);
  assert.throws(() => readCurrentUndoRequest(undoBefore, forged));
}
for (const revision of ["0", "9223372036854775807"]) {
  const invalid = structuredClone(undoBefore); invalid.current_token.revision = revision;
  assert.throws(() => currentUndoRequest(invalid));
}
for (const change of [r => { r.before_token.snapshot_sha256 = "sha256:" + "e".repeat(64); },
  r => { r.snapshot.current_token.revision = beforePlan.current_token.revision; }, r => { r.snapshot.current_token.graph_sha256 = undoBefore.current_token.graph_sha256; },
  r => { r.snapshot.current_token.original_manifest_sha256 = "sha256:" + "a".repeat(64); }, r => { r.snapshot.findings[0].source_text = "Changed source"; },
  r => { r.snapshot.findings[0].range.start_ticks = "1"; }, r => { r.snapshot.findings[0].confidence = "0.98"; },
  r => { r.snapshot.findings[0].cue_id = "foreign"; }, r => { r.snapshot.mode = "b1"; }, r => { r.snapshot.findings.pop(); },
  r => { r.session_id = "invalid"; }, r => { r.path = "foreign"; }]) {
  const forged = structuredClone(undoneResult); change(forged); assert.notDeepEqual(forged, undoneResult);
  assert.throws(() => readUndoneCurrent(undoBefore, undoRequest, forged));
}
let undoId = 0, deferUndo = false, undoClientView = structuredClone(undoBefore);
const undoCalls = [], undoPending = [];
const undoTransport = nativeReviewTransport(async (command, args) => {
  undoCalls.push({ command, args });
  if (command === "review_cancel_v1") return;
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(undoClientView) };
  if (command === "review_undo_v1") {
    assert.deepEqual(Object.keys(args).sort(), ["request", "requestId", "sessionId"]);
    assert.deepEqual(args.request, currentUndoRequest(undoClientView));
    const response = undoneResponse(undoClientView, beforePlan, args.requestId);
    if (deferUndo) return new Promise(resolve => undoPending.push({ args, response, resolve }));
    undoClientView = structuredClone(response.snapshot); return response;
  }
  if (command === "review_audio_v2") return new ArrayBuffer(44);
  if (command === "review_plan_v1") return planResponse(undoClientView, args.request);
  throw new Error("undo must not invoke apply, worker or original correction");
}, async () => {}, () => `request-undo-native-${++undoId}`);
const undoing = new ReviewConnection(undoTransport);
await undoing.load(undoBefore.job_id);
const undoAfter = await undoing.actions().undoCurrent(undoRequest, new AbortController().signal);
assert.deepEqual(undoing.snapshot(), undoAfter);
const nativeUndo = undoCalls.find(c => c.command === "review_undo_v1");
await undoing.actions().preview(previewRequest(undoAfter, undoAfter.findings[0].finding_id), new AbortController().signal);
assert.equal(undoCalls.findLast(c => c.command === "review_audio_v2").args.sessionId, nativeUndo.args.requestId);
await assert.rejects(undoing.actions().undoCurrent(undoRequest, new AbortController().signal));
assert.equal(undoCalls.filter(c => c.command === "review_undo_v1").length, 1);
// A later edit plan uses the restored content and actual monotonic token. This
// is client routing evidence, not physical native after-undo qualification.
const afterUndoEdit = currentPlanRequest(undoAfter, { finding_id: undoAfter.findings[0].finding_id, translation: "Bản dịch sau hoàn tác" });
const afterUndoPlan = await undoing.actions().planCurrent(afterUndoEdit, new AbortController().signal);
assert.equal(afterUndoPlan.current_token.revision, "9007199254740995");
assert.equal(afterUndoPlan.candidate_revision, "9007199254740996");
assert.deepEqual(undoing.snapshot(), undoAfter);
undoing.dispose();
undoClientView = structuredClone(undoBefore); deferUndo = true;
const abortingUndo = new ReviewConnection(undoTransport); await abortingUndo.load(undoBefore.job_id);
const undoAbort = new AbortController(), pendingUndo = abortingUndo.actions().undoCurrent(undoRequest, undoAbort.signal);
await new Promise(resolve => setImmediate(resolve)); assert.equal(undoPending.length, 1);
await assert.rejects(abortingUndo.actions().undoCurrent(undoRequest, new AbortController().signal), /already in progress/);
await assert.rejects(abortingUndo.actions().applyCurrent(confirmed, new AbortController().signal), /already in progress/);
undoAbort.abort();
assert(undoCalls.some(c => c.command === "review_cancel_v1" && c.args.requestId === undoPending[0].args.requestId));
undoPending[0].resolve(undoPending[0].response);
await assert.rejects(pendingUndo, { name: "AbortError" }); assert.equal(abortingUndo.snapshot(), null); abortingUndo.dispose();
let uncertainUndoCalls = 0;
const uncertainUndo = new ReviewConnection({ async snapshot() { return undoBefore; }, async preview() {},
  async undoCurrent() { uncertainUndoCalls++; throw new Error("undo reply lost after possible CAS"); } });
await uncertainUndo.load(undoBefore.job_id);
await assert.rejects(uncertainUndo.actions().undoCurrent(undoRequest, new AbortController().signal), /reply lost/);
assert.equal(uncertainUndoCalls, 1); assert.equal(uncertainUndo.snapshot(), null);
assert.throws(() => uncertainUndo.actions(), /has not loaded/); uncertainUndo.dispose();
const b1UndoBefore = readAppliedCurrent(b1PlanSnapshot, b1Confirmed, appliedResponse(b1PlanSnapshot, b1Confirmed, "request-apply-b1-0001"));
assert.equal(readUndoneCurrent(b1UndoBefore, currentUndoRequest(b1UndoBefore), undoneResponse(b1UndoBefore, b1PlanSnapshot, "request-undo-b1-0001")).mode, "b1");
function exportResponse(before, format, sessionId) {
  return { schema_version: 1, kind: "review_desktop_exported_current", format, current_token: structuredClone(before.current_token), session_id: sessionId, snapshot: structuredClone(before),
    receipt: { schema_version: 1, kind: "review_desktop_export_receipt", format, generation: "a".repeat(64), path: format === "standard" ? "D:/exports/standard" : "D:/exports/import-pack",
      manifest_sha256: "sha256:" + (format === "standard" ? "2" : "3").repeat(64), standard_manifest_sha256: "sha256:" + "2".repeat(64),
      files_verified: before.mode === "b1" ? format === "standard" ? 7 : 10 : format === "standard" ? 10 : 13,
      standard_bundle_published: true, production_editable_pack_published: format === "editable", vendor_execution: false } };
}
const exportBefore = structuredClone(undoBefore), standardRequest = currentExportRequest(exportBefore, "standard"), editableRequest = currentExportRequest(exportBefore, "editable");
const standardResponse = exportResponse(exportBefore, "standard", "request-export-standard-0001");
const standardChecked = readExportedCurrent(exportBefore, standardRequest, standardResponse);
assert.deepEqual(standardChecked.snapshot, exportBefore);
const editableResponse = exportResponse(exportBefore, "editable", "request-export-editable-0001");
assert.equal(readExportedCurrent(exportBefore, editableRequest, editableResponse, standardChecked.receipt).receipt.production_editable_pack_published, true);
assert.throws(() => readExportedCurrent(exportBefore, editableRequest, editableResponse), /standard bundle/);
for (const change of [r => { r.confirm = false; }, r => { r.format = "vendor"; }, r => { r.processing_root = "foreign"; }, r => { r.output_path = "foreign"; },
  r => { r.current_token.revision = "0"; }, r => { r.current_token.graph_sha256 = "sha256:" + "f".repeat(64); }, r => { r.current_token.original_manifest_sha256 = "sha256:" + "f".repeat(64); }]) {
  const forged = structuredClone(standardRequest); change(forged); assert.notDeepEqual(forged, standardRequest);
  assert.throws(() => readCurrentExportRequest(exportBefore, forged));
}
for (const change of [r => { r.current_token.revision = "0"; }, r => { r.snapshot.current_token.revision = "0"; }, r => { r.snapshot.findings[0].source_text = "foreign"; },
  r => { r.snapshot.findings[0].translated_text = "unexpected edit"; }, r => { r.receipt.standard_bundle_published = false; }, r => { r.receipt.vendor_execution = true; },
  r => { r.receipt.files_verified = 7; }, r => { r.receipt.format = "editable"; }, r => { r.receipt.path = "../foreign"; }, r => { r.receipt.manifest_sha256 = "foreign"; },
  r => { r.receipt.standard_manifest_sha256 = "sha256:" + "f".repeat(64); }, r => { r.output_root = "foreign"; }]) {
  const forged = structuredClone(standardResponse); change(forged); assert.notDeepEqual(forged, standardResponse);
  assert.throws(() => readExportedCurrent(exportBefore, standardRequest, forged));
}
const foreignBundle = structuredClone(editableResponse); foreignBundle.receipt.generation = "b".repeat(64);
assert.throws(() => readExportedCurrent(exportBefore, editableRequest, foreignBundle, standardChecked.receipt));
for (const format of ["standard", "editable"]) {
  const standardB1 = exportResponse(b1UndoBefore, "standard", "request-export-b1-standard");
  assert.equal(readExportedCurrent(b1UndoBefore, currentExportRequest(b1UndoBefore, format), exportResponse(b1UndoBefore, format, "request-export-b1-format"), standardB1.receipt).snapshot.mode, "b1");
}
let exportId = 0, waitExport = false, failEditable = false;
const exportCalls = [], exportPending = [];
const exportTransport = nativeReviewTransport(async (command, args) => {
  exportCalls.push({ command, args });
  if (command === "review_cancel_v1") return;
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(exportBefore) };
  if (command === "review_export_v1") {
    assert.deepEqual(Object.keys(args).sort(), ["request", "requestId", "sessionId"]);
    assert.deepEqual(args.request, currentExportRequest(exportBefore, args.request.format));
    if (failEditable && args.request.format === "editable") throw new Error("editable capability unavailable; existing standard remains valid");
    const response = exportResponse(exportBefore, args.request.format, args.requestId);
    if (waitExport) return new Promise(resolve => exportPending.push({ args, response, resolve }));
    return response;
  }
  if (command === "review_audio_v2") return new ArrayBuffer(44);
  throw new Error("export must not invoke apply, undo or vendor launch");
}, async () => {}, () => `request-export-native-${++exportId}`);
const exporting = new ReviewConnection(exportTransport); await exporting.load(exportBefore.job_id);
await assert.rejects(exporting.actions().exportCurrent(editableRequest, new AbortController().signal), /standard bundle/);
assert.equal(exportCalls.filter(c => c.command === "review_export_v1").length, 0);
const standardReceipt = await exporting.actions().exportCurrent(standardRequest, new AbortController().signal);
assert.deepEqual(exporting.snapshot(), exportBefore);
await exporting.actions().preview(previewRequest(exportBefore, exportBefore.findings[0].finding_id), new AbortController().signal);
assert.equal(exportCalls.findLast(c => c.command === "review_audio_v2").args.sessionId, exportCalls.findLast(c => c.command === "review_export_v1").args.requestId);
const editableReceipt = await exporting.actions().exportCurrent(editableRequest, new AbortController().signal);
assert.equal(editableReceipt.standard_manifest_sha256, standardReceipt.manifest_sha256);
assert.deepEqual(exporting.snapshot(), exportBefore); exporting.dispose();
const failingEditable = new ReviewConnection(exportTransport); await failingEditable.load(exportBefore.job_id);
const preservedStandard = await failingEditable.actions().exportCurrent(standardRequest, new AbortController().signal), preservedCopy = structuredClone(preservedStandard);
failEditable = true;
await assert.rejects(failingEditable.actions().exportCurrent(editableRequest, new AbortController().signal), /existing standard/);
assert.deepEqual(preservedStandard, preservedCopy); assert.equal(failingEditable.snapshot(), null);
assert.equal(exportCalls.filter(c => c.command === "review_export_v1" && c.args.request.format === "standard").length, 2);
failingEditable.dispose(); failEditable = false; waitExport = true;
const abortingExport = new ReviewConnection(exportTransport); await abortingExport.load(exportBefore.job_id);
const exportAbort = new AbortController(), pendingExport = abortingExport.actions().exportCurrent(standardRequest, exportAbort.signal);
await new Promise(resolve => setImmediate(resolve)); assert.equal(exportPending.length, 1);
await assert.rejects(abortingExport.actions().undoCurrent(currentUndoRequest(exportBefore), new AbortController().signal), /already in progress/);
await assert.rejects(abortingExport.actions().exportCurrent(standardRequest, new AbortController().signal), /already in progress/);
exportAbort.abort(); assert(exportCalls.some(c => c.command === "review_cancel_v1" && c.args.requestId === exportPending[0].args.requestId));
exportPending[0].resolve(exportPending[0].response); await assert.rejects(pendingExport, { name: "AbortError" }); assert.equal(abortingExport.snapshot(), null); abortingExport.dispose();
let uncertainExportCalls = 0;
const uncertainExport = new ReviewConnection({ async snapshot() { return exportBefore; }, async preview() {},
  async exportCurrent() { uncertainExportCalls++; throw new Error("export reply lost after possible publication"); } });
await uncertainExport.load(exportBefore.job_id);
await assert.rejects(uncertainExport.actions().exportCurrent(standardRequest, new AbortController().signal), /reply lost/);
assert.equal(uncertainExportCalls, 1); assert.equal(uncertainExport.snapshot(), null); uncertainExport.dispose();
function initializedResponse(before, sessionId, mode = "b2") {
  return { schema_version: 1, kind: "review_desktop_initialized_current", job_id: before.job_id, session_id: sessionId,
    snapshot: { schema_version: 3, kind: "review_current_snapshot", job_id: before.job_id,
      current_token: { job_id: before.job_id, revision: "0", snapshot_sha256: "sha256:" + "4".repeat(64), graph_sha256: "sha256:" + "5".repeat(64), original_manifest_sha256: "sha256:" + "6".repeat(64) },
      mode, time_base: structuredClone(before.time_base), findings: structuredClone(before.findings), last_plan: null, correction_ready: false } };
}
const initialBefore = structuredClone(payload), initializeRequest = currentInitializeRequest(initialBefore);
const initialResponse = initializedResponse(initialBefore, "request-initialize-result-0001");
assert.equal(readInitializedCurrent(initialBefore, initializeRequest, initialResponse).current_token.revision, "0");
for (const change of [r => { r.confirm = false; }, r => { r.job_id = "foreign"; }, r => { r.root = "D:/foreign"; }, r => { r.current_token = initialResponse.snapshot.current_token; },
  r => { r.snapshot = initialBefore; }]) {
  const forged = structuredClone(initializeRequest); change(forged); assert.notDeepEqual(forged, initializeRequest);
  assert.throws(() => readCurrentInitializeRequest(initialBefore, forged));
}
for (const change of [r => { r.job_id = "foreign"; }, r => { r.snapshot.current_token.revision = "1"; }, r => { r.snapshot.current_token.job_id = "foreign"; },
  r => { r.snapshot.findings[0].source_text = "foreign"; }, r => { r.snapshot.findings[0].translated_text = "changed"; },
  r => { r.snapshot.findings[0].confidence = "0.98"; }, r => { r.snapshot.findings[0].range.start_ticks = "1"; },
  r => { r.snapshot.findings[0].output_preview = "substituted-original-preview"; }, r => { r.snapshot.findings.pop(); }, r => { r.session_id = "short"; }, r => { r.target_path = "foreign"; }]) {
  const forged = structuredClone(initialResponse); change(forged); assert.notDeepEqual(forged, initialResponse);
  assert.throws(() => readInitializedCurrent(initialBefore, initializeRequest, forged));
}
const nonOriginal = structuredClone(initialBefore); nonOriginal.revision = 1;
assert.throws(() => currentInitializeRequest(nonOriginal));
let initialId = 0, waitInitialize = false, initialNativeView = structuredClone(initialBefore);
const initialCalls = [], initialPending = [];
const initialTransport = nativeReviewTransport(async (command, args) => {
  initialCalls.push({ command, args });
  if (command === "review_cancel_v1") return;
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(initialNativeView) };
  if (command === "review_initialize_v1") {
    assert.deepEqual(Object.keys(args).sort(), ["request", "requestId", "sessionId"]);
    assert.deepEqual(args.request, initializeRequest);
    const response = initializedResponse(initialBefore, args.requestId);
    if (waitInitialize) return new Promise(resolve => initialPending.push({ args, response, resolve }));
    initialNativeView = structuredClone(response.snapshot); return response;
  }
  if (command === "review_plan_v1") return planResponse(initialNativeView, args.request);
  if (command === "review_audio_v2") return new ArrayBuffer(44);
  throw new Error("initialize must not invoke original audio, apply or export");
}, async () => {}, () => `request-initialize-native-${++initialId}`);
const initializing = new ReviewConnection(initialTransport); await initializing.load(initialBefore.job_id);
const oldActions = initializing.actions(), initialized = await oldActions.initializeCurrent(initializeRequest, new AbortController().signal);
assert.deepEqual(initializing.snapshot(), initialized);
assert.equal(oldActions.planCurrent, undefined);
const freshActions = oldActions.refreshed();
assert.equal(freshActions.initializeCurrent, undefined); assert.equal(typeof freshActions.planCurrent, "function");
const initializedPlanRequest = currentPlanRequest(initialized, { finding_id: initialized.findings[0].finding_id, translation: "Sửa sau khi lưu bản gốc" });
const initializedPlan = await freshActions.planCurrent(initializedPlanRequest, new AbortController().signal);
assert.equal(initializedPlan.current_token.revision, "0"); assert.equal(initializedPlan.candidate_revision, "1");
const initialNativeCall = initialCalls.find(c => c.command === "review_initialize_v1");
assert.equal(initialCalls.findLast(c => c.command === "review_plan_v1").args.sessionId, initialNativeCall.args.requestId);
// The actual Python fixture has a TTS warning and no admitted preview. Preserve
// that refusal after initialization instead of inventing playable audio.
await assert.rejects(freshActions.preview(previewRequest(initialized, initialized.findings[0].finding_id), new AbortController().signal), /no validated preview/);
assert.equal(initialCalls.filter(c => c.command === "review_audio_v2" || c.command === "review_audio_v1").length, 0);
await assert.rejects(oldActions.initializeCurrent(initializeRequest, new AbortController().signal));
assert.equal(initialCalls.filter(c => c.command === "review_initialize_v1").length, 1); initializing.dispose();
initialNativeView = structuredClone(initialBefore); waitInitialize = true;
const abortingInitial = new ReviewConnection(initialTransport); await abortingInitial.load(initialBefore.job_id);
const initialAbort = new AbortController(), pendingInitial = abortingInitial.actions().initializeCurrent(initializeRequest, initialAbort.signal);
await new Promise(resolve => setImmediate(resolve)); assert.equal(initialPending.length, 1);
await assert.rejects(abortingInitial.actions().initializeCurrent(initializeRequest, new AbortController().signal), /already in progress/);
initialAbort.abort(); assert(initialCalls.some(c => c.command === "review_cancel_v1" && c.args.requestId === initialPending[0].args.requestId));
initialPending[0].resolve(initialPending[0].response); await assert.rejects(pendingInitial, { name: "AbortError" }); assert.equal(abortingInitial.snapshot(), null); abortingInitial.dispose();
let uncertainInitialCalls = 0;
const uncertainInitial = new ReviewConnection({ async snapshot() { return initialBefore; }, async preview() {},
  async initializeCurrent() { uncertainInitialCalls++; throw new Error("reply lost after possible initialization"); } });
await uncertainInitial.load(initialBefore.job_id);
await assert.rejects(uncertainInitial.actions().initializeCurrent(initializeRequest, new AbortController().signal), /reply lost/);
assert.equal(uncertainInitialCalls, 1); assert.equal(uncertainInitial.snapshot(), null); uncertainInitial.dispose();
const initialB1 = structuredClone(initialBefore); for (const f of initialB1.findings) f.voice = null;
assert.equal(readInitializedCurrent(initialB1, currentInitializeRequest(initialB1), initializedResponse(initialB1, "request-initialize-b1-0001", "b1")).mode, "b1");
console.log("legacy/Python v2, explicit original initialization and current full-token audio/plan/apply/undo/standard-editable routing, exact evidence and cancellation tests passed");


// Real request observation is separate from success and remains selection-bound.
const { readReviewActivity, observeReviewRequest, reviewActivityText } = await import("../../apps/desktop/src/features/review/request-activity.ts");
const activityRecord = (id = "request-observed-client-0001", overrides = {}) => ({ schema_version: 1, kind: "review_desktop_request_activity",
  request_id: id, job_id: initialBefore.job_id, operation: "initialize", state: "running", phase: "processing",
  elapsed_ms: 6240, phase_elapsed_ms: 5000, process_state: "running", process_observation_age_ms: 40, ...overrides });
const activityEnvelope = activity => ({ schema_version: 1, kind: "review_desktop_request_stage", activity, native_observation: "unavailable", native_stage: null });
const aBound = activityRecord();
assert.deepEqual(readReviewActivity(aBound, aBound.request_id, aBound.job_id, "initialize"), aBound);
for (const mutation of [ { request_id: "request-other-client-0001" }, { job_id: "other-job" }, { operation: "apply" },
  { percent: 100 }, { state: "success" }, { state: "ended" }, { phase: "ended" }, { elapsed_ms: -1 },
  { elapsed_ms: 1.5 }, { phase_elapsed_ms: 6241 }, { process_observation_age_ms: null }, { process_state: "idle" }, { path: "C:/fake" } ]) {
  assert.throws(() => readReviewActivity({ ...aBound, ...mutation }, aBound.request_id, aBound.job_id, "initialize"));
}
assert.match(reviewActivityText({ kind: "observed", activity: aBound }), /chưa có kết quả được xác nhận/);
assert.match(reviewActivityText({ kind: "observed", activity: activityRecord(aBound.request_id, { process_observation_age_ms: 6000 }) }), /Chưa cập nhật/);
assert.match(reviewActivityText({ kind: "observed", activity: activityRecord(aBound.request_id, { state: "ended", phase: "ended", process_state: "exited" }) }), /chưa thể kết luận thành công/);
const pauseObservation = () => new Promise(resolve => setTimeout(resolve, 250));
let pendingObservation, observationCalls = 0; const lateActivity = [];
const statusAbort = new AbortController();
const stopObserved = observeReviewRequest(async (command, args) => {
  assert.equal(command, "review_request_stage_v1"); assert.deepEqual(args, { requestId: aBound.request_id }); observationCalls++;
  return new Promise(resolve => { pendingObservation = resolve; });
}, aBound.request_id, aBound.job_id, "initialize", statusAbort.signal, value => lateActivity.push(value));
await pauseObservation(); assert.equal(observationCalls, 1); statusAbort.abort();
pendingObservation(activityEnvelope(aBound)); await new Promise(resolve => setImmediate(resolve)); assert.equal(lateActivity.length, 0); stopObserved();
let missingCalls = 0; const missingActivity = [], missingAbort = new AbortController();
const stopMissing = observeReviewRequest(async () => { missingCalls++; throw new Error("observation unavailable"); },
  aBound.request_id, aBound.job_id, "initialize", missingAbort.signal, value => missingActivity.push(value));
await pauseObservation(); assert.equal(missingCalls, 1); assert.deepEqual(missingActivity, [{ kind: "unavailable" }]); stopMissing(); missingAbort.abort();

let observedInitReply, observedInitId; const observedIpc = [], activityUpdates = [];
const observedTransport = nativeReviewTransport(async (command, args) => {
  observedIpc.push({ command, args: structuredClone(args) });
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(initialBefore) };
  if (command === "review_initialize_v1") {
    observedInitId = args.requestId; return new Promise(resolve => { observedInitReply = resolve; });
  }
  if (command === "review_request_stage_v1") return activityEnvelope(activityRecord(args.requestId, { state: "ended", phase: "ended", process_state: "exited" }));
  if (command === "review_cancel_v1") return;
  throw new Error("unexpected observation route");
}, async () => {}, (() => { let n = 0; return () => `request-activity-integration-${++n}`; })());
const observedConnection = new ReviewConnection(observedTransport); await observedConnection.load(initialBefore.job_id);
let actualReplyAccepted = false;
const observingInit = observedConnection.actions().initializeCurrent(currentInitializeRequest(initialBefore), new AbortController().signal,
  value => activityUpdates.push(value)).then(value => { actualReplyAccepted = true; return value; });
await pauseObservation();
assert.equal(activityUpdates.length, 1); assert.equal(activityUpdates[0].activity.request_id, observedInitId);
assert.equal(activityUpdates[0].activity.state, "ended"); assert.equal(actualReplyAccepted, false);
assert.equal(observedConnection.snapshot().schema_version, 2);
await assert.rejects(observedConnection.actions().initializeCurrent(currentInitializeRequest(initialBefore), new AbortController().signal), /already in progress/);
observedInitReply(initializedResponse(initialBefore, observedInitId)); await observingInit;
assert.equal(actualReplyAccepted, true); assert.equal(observedConnection.snapshot().schema_version, 3);
assert.equal(observedIpc.filter(c => c.command === "review_initialize_v1").length, 1);
assert.equal(observedIpc.filter(c => c.command === "review_request_stage_v1").length, 1);
observedConnection.dispose();
let oldNotify, oldInitReply; const selectionUpdates = [];
const selectedActivity = new ReviewConnection({ async snapshot() { return structuredClone(initialBefore); }, async preview() {},
  async initializeCurrent(_request, _signal, notify) { oldNotify = notify; return new Promise(resolve => { oldInitReply = resolve; }); } });
await selectedActivity.load(initialBefore.job_id);
const oldInitializing = selectedActivity.actions().initializeCurrent(currentInitializeRequest(initialBefore), new AbortController().signal,
  value => selectionUpdates.push(value));
selectedActivity.clear(); oldNotify({ kind: "observed", activity: aBound });
assert.equal(selectionUpdates.length, 0); oldInitReply(initializedResponse(initialBefore, "request-observation-old-0001"));
await assert.rejects(oldInitializing, { name: "AbortError" }); selectedActivity.dispose();
console.log("request activity: exact identity/timing, process-versus-result distinction, unavailable observation, no duplicate mutation, cancellation and late selection guards passed");


const { readReviewStage } = await import("../../apps/desktop/src/features/review/request-activity.ts");
const runtimeStage = { schema_version: 1, kind: "review_native_stage_observation", sequence: 3, stage_epoch: 2,
  stage: "verifying_runtime_files", elapsed_ms: 5000, units_completed: 214, bytes_verified: 12345678 };
const nativeEnvelope = (event = runtimeStage) => ({ ...activityEnvelope(aBound), native_observation: "observed",
  native_stage: { event: structuredClone(event), received_age_ms: 40, advance_age_ms: 2000 } });
const nativeDisplay = readReviewStage(nativeEnvelope(), aBound.request_id, aBound.job_id, "initialize");
assert.equal(nativeDisplay.native.event.units_completed, 214);
assert.match(reviewActivityText(nativeDisplay), /214 tệp/); assert.match(reviewActivityText(nativeDisplay), /2 giây từ mốc xử lý/);
for (const mutation of [{ sequence: 0 }, { sequence: 32769 }, { stage_epoch: 4 }, { elapsed_ms: 6241 },
  { units_completed: -1 }, { bytes_verified: Number.MAX_SAFE_INTEGER + 1 }, { stage: "success" }, { success: true },
  { stage: "synthesizing_cue" }, { root: "C:/substituted" }]) {
  assert.throws(() => readReviewStage(nativeEnvelope({ ...runtimeStage, ...mutation }), aBound.request_id, aBound.job_id, "initialize"));
}
const badAges = nativeEnvelope(); badAges.native_stage.advance_age_ms = 1;
assert.throws(() => readReviewStage(badAges, aBound.request_id, aBound.job_id, "initialize"));
const oldHostShape = activityEnvelope(aBound);
assert.deepEqual(readReviewStage(oldHostShape, aBound.request_id, aBound.job_id, "initialize"), { kind: "observed", activity: aBound });
assert.throws(() => readReviewStage({ ...oldHostShape, native_observation: "observed" }, aBound.request_id, aBound.job_id, "initialize"));
// Observe actual transition messages and reject an older event without running a
// mutation or treating telemetry as its acknowledgement.
let stageCalls = 0; const stageUpdates = [], stageSignal = new AbortController();
const stopStages = observeReviewRequest(async command => {
  assert.equal(command, "review_request_stage_v1"); stageCalls++;
  return nativeEnvelope(stageCalls === 1 ? runtimeStage : { ...runtimeStage, sequence: 2, units_completed: 213 });
}, aBound.request_id, aBound.job_id, "initialize", stageSignal.signal, value => stageUpdates.push(value));
await new Promise(resolve => setTimeout(resolve, 1300)); stopStages(); stageSignal.abort();
assert.equal(stageCalls, 2); assert.equal(stageUpdates[0].native.event.units_completed, 214);
assert.deepEqual(stageUpdates[1], { kind: "unavailable" });
console.log("native stage envelope: actual verified-file counters, closed stages, no success authority, immutable old activity shape and out-of-order observation refusal passed");

// Equal native snapshots remain equal when JSON object key order differs.
let repeatedStageCalls = 0; const repeatedStageUpdates = [], repeatedStageSignal = new AbortController();
const stopRepeatedStage = observeReviewRequest(async () => {
  repeatedStageCalls++;
  const event = repeatedStageCalls === 1 ? runtimeStage : Object.fromEntries(Object.entries(runtimeStage).reverse());
  return nativeEnvelope(event);
}, aBound.request_id, aBound.job_id, "initialize", repeatedStageSignal.signal, value => repeatedStageUpdates.push(value));
await new Promise(resolve => setTimeout(resolve, 1300)); stopRepeatedStage(); repeatedStageSignal.abort();
assert.equal(repeatedStageCalls, 2); assert.equal(repeatedStageUpdates.length, 2);
assert(repeatedStageUpdates.every(value => value.kind === "observed"));
console.log("repeated native snapshots accept JSON key order differences without altering values or advancement age");


const { readReviewFailure, boundReviewFailure, reviewFailureDetails, ReviewFailureError } = await import("../../apps/desktop/src/features/review/request-failure.ts");
const failurePacket = (id, job = initialBefore.job_id, operation = "initialize", overrides = {}) => ({ schema_version: 1, kind: "review_desktop_request_failure",
  request_id: id, job_id: job, operation, outcome: "unconfirmed", phase: "processing", native_stage: "synthesizing_cue",
  message: "Không xác nhận được kết quả; hãy tải lại", diagnostic: "QC refused: <img src=x onerror=alert(1)> is inert diagnostic text", ...overrides });
const rawFailure = failurePacket("request-diagnostic-client-0001");
assert.deepEqual(readReviewFailure(rawFailure, rawFailure.request_id, rawFailure.job_id, "initialize"), rawFailure);
for (const mutation of [{ request_id: "request-different-error-0001" }, { job_id: "other-job" }, { operation: "apply" }, { outcome: "not_committed" },
  { success: true }, { retryable: true }, { root: "C:/substituted" }, { phase: "ended" }, { native_stage: "successful" }, { message: "" },
  { message: "x".repeat(1025) }, { diagnostic: "a".repeat(4097) }, { diagnostic: "漢".repeat(1366) }, { diagnostic: "bad\u0000text" }, { diagnostic: "bad\ud800text" }]) {
  assert.throws(() => readReviewFailure({ ...rawFailure, ...mutation }, rawFailure.request_id, rawFailure.job_id, "initialize"));
}
assert.equal(readReviewFailure({ ...rawFailure, diagnostic: null, native_stage: null }, rawFailure.request_id, rawFailure.job_id, "initialize").diagnostic, null);
const displayedFailure = boundReviewFailure(rawFailure, rawFailure.request_id, rawFailure.job_id, "initialize"); assert(displayedFailure instanceof ReviewFailureError);
assert.match(reviewFailureDetails(displayedFailure, rawFailure.job_id, "initialize"), /<img src=x/);
assert.equal(reviewFailureDetails(displayedFailure, "other-job", "initialize"), null);
assert.equal(reviewFailureDetails(displayedFailure, rawFailure.job_id, "apply"), null);
assert.equal(boundReviewFailure("legacy error", rawFailure.request_id, rawFailure.job_id, "initialize"), "legacy error");
const mismatchedFailure = { ...rawFailure, job_id: "different-job" };
assert.equal(boundReviewFailure(mismatchedFailure, rawFailure.request_id, rawFailure.job_id, "initialize"), mismatchedFailure);
const diagnosticCalls = []; let diagnosticId = 0;
const diagnosticTransport = nativeReviewTransport(async (command, args) => {
  diagnosticCalls.push({ command, args: structuredClone(args) });
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(initialBefore) };
  if (command === "review_initialize_v1") throw failurePacket(args.requestId);
  if (command === "review_cancel_v1") return;
  throw new Error("unexpected diagnostic route");
}, async () => {}, () => `request-diagnostic-native-${++diagnosticId}`);
const diagnosticConnection = new ReviewConnection(diagnosticTransport); await diagnosticConnection.load(initialBefore.job_id);
await assert.rejects(diagnosticConnection.actions().initializeCurrent(currentInitializeRequest(initialBefore), new AbortController().signal), error => {
  assert(error instanceof ReviewFailureError); assert.equal(error.failure.request_id, diagnosticCalls.find(c => c.command === "review_initialize_v1").args.requestId);
  assert.equal(error.failure.outcome, "unconfirmed"); assert.match(reviewFailureDetails(error, initialBefore.job_id, "initialize"), /QC refused/); return true;
});
assert.equal(diagnosticConnection.snapshot(), null);
assert.equal(diagnosticCalls.filter(c => c.command === "review_initialize_v1").length, 1);
assert(diagnosticCalls.some(c => c.command === "review_cancel_v1")); diagnosticConnection.dispose();
let lateFailureReject; const lateDiagnosticCalls = [];
const lateDiagnosticTransport = nativeReviewTransport(async (command, args) => {
  lateDiagnosticCalls.push({ command, args: structuredClone(args) });
  if (command === "review_snapshot_v2") return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(initialBefore) };
  if (command === "review_initialize_v1") return new Promise((_resolve, reject) => { lateFailureReject = () => reject(failurePacket(args.requestId)); });
  if (command === "review_cancel_v1") return;
  throw new Error("unexpected late diagnostic route");
}, async () => {}, (() => { let n = 0; return () => `request-late-diagnostic-${++n}`; })());
const lateDiagnosticConnection = new ReviewConnection(lateDiagnosticTransport); await lateDiagnosticConnection.load(initialBefore.job_id);
const lateDiagnostic = lateDiagnosticConnection.actions().initializeCurrent(currentInitializeRequest(initialBefore), new AbortController().signal);
lateDiagnosticConnection.clear(); lateFailureReject(); await assert.rejects(lateDiagnostic); assert.equal(lateDiagnosticConnection.snapshot(), null);
assert.equal(lateDiagnosticCalls.filter(c => c.command === "review_initialize_v1").length, 1); lateDiagnosticConnection.dispose();
console.log("bound request failures: exact invocation, UTF8/control caps, inert details, unconfirmed outcome, stale/lost/late guards and no retry passed");

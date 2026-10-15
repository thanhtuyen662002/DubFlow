import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { createReviewSnapshot, groupFindings, ReviewController } from "../../apps/desktop/src/features/review/model.ts";
import { readReviewPayload, previewProductionCorrection, reviewRangeLabel } from "../../apps/desktop/src/features/review/wire.ts";
import { correctionRequest, acceptCorrectionResponse, previewRequest } from "../../apps/desktop/src/features/review/session.ts";
import { ReviewConnection } from "../../apps/desktop/src/features/review/connection.ts";
import { nativeReviewTransport } from "../../apps/desktop/src/features/review/transport.ts";
import { currentPlanRequest, sameCurrentPlanRequest, readCurrentPlan } from "../../apps/desktop/src/features/review/current-plan.ts";

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
console.log("legacy/Python v2, current full-token audio and read-only native correction-plan scope/cancellation tests passed");

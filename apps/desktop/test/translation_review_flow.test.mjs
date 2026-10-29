import assert from "node:assert/strict";
import { MemorySnapshotStorage, QueueController } from "../src/features/queue/model.ts";
import { ReviewDraftHistory } from "../src/features/review/history.ts";
import {
  applySourceLanguage, chooseTranslator, effectiveTranslator, FAST_PHRASE_SYLLABLES_PER_SECOND, hachimiAllowed,
  phrasePaces, reviewOverrides, saveReviewRevision, startJobRequest, translatorDefaults,
} from "../src/features/translation_review/flow.ts";

const clock = () => "2026-10-25T00:00:00.000Z";
const VOICE = "vi-truc-ly-vieneu3-v1";

// Chinese survives persistence and reaches start_job; Auto stays "auto".
const storage = new MemorySnapshotStorage();
const queue = new QueueController(storage, clock);
const [zhId, autoId, enId] = queue.addPaths(["C:/clips/douyin.mp4", "C:/clips/auto.mp4", "C:/clips/english.mp4"]);
queue.setSourceLanguage(zhId, "zh");
queue.setTranslationEngine(zhId, "hachimi");
queue.setDubbing(zhId, { enabled: true, voiceId: VOICE });
queue.setSourceLanguage(enId, "en");
queue.setTranslationEngine(enId, "hachimi");

const reopened = new QueueController(storage, clock);
const job = (controller, id) => controller.getSnapshot().jobs.find((candidate) => candidate.id === id);
assert.equal(job(reopened, zhId).sourceLanguage, "zh");
assert.equal(job(reopened, autoId).sourceLanguage, undefined);

assert.deepEqual(startJobRequest(job(reopened, zhId), "D:/Results"), {
  jobId: zhId, sourcePath: "C:/clips/douyin.mp4", outputDir: null, outputRoot: "D:/Results",
  enableDubbing: true, ttsVoiceId: VOICE, burnIn: true, sourceLanguage: "zh", subtitleBox: false,
  translationEngine: "hachimi", blurRegions: null, blurAuto: false, multiVoice: false, speakerVoices: null,
  separateBackground: false, cpuOnly: false,
});
assert.equal(startJobRequest(job(reopened, zhId), null, true).cpuOnly, true, "the app-wide CPU choice reaches start_job");
const autoRequest = startJobRequest(job(reopened, autoId), null);
assert.equal(autoRequest.sourceLanguage, "auto", "Auto must never silently become English");
assert.equal(autoRequest.translationEngine, "argos");
assert.equal(startJobRequest(job(reopened, enId), null).translationEngine, "argos", "Hachimi is Chinese-only");

// Hachimi needs an explicit Chinese source and the licence acknowledgement; Argos never asks.
let asked = 0;
const ack = (answer) => () => { asked += 1; return answer; };
assert.equal(chooseTranslator("argos", "zh", ack(true)), "argos");
assert.equal(asked, 0);
assert.equal(chooseTranslator("hachimi", "zh", ack(false)), null, "declining keeps the previous (Argos) choice");
assert.equal(chooseTranslator("hachimi", "zh", ack(true)), "hachimi");
assert.equal(asked, 2);
assert.equal(chooseTranslator("hachimi", undefined, ack(true)), "argos", "Auto cannot pick Hachimi");
assert.equal(chooseTranslator("hachimi", "en", ack(true)), "argos", "English cannot pick Hachimi");
assert.equal(asked, 2, "no acknowledgement is asked when Hachimi is not allowed");
assert.deepEqual(["zh", "en", undefined, null, "auto"].map(hachimiAllowed), [true, false, false, false, false]);

// A Hachimi choice stored with Auto or English is never used or requested.
const guard = new QueueController(new MemorySnapshotStorage(), clock);
const [autoStoredId] = guard.addPaths(["C:/clips/auto-hachimi.mp4"]);
guard.setTranslationEngine(autoStoredId, "hachimi");
const autoStored = job(guard, autoStoredId);
assert.equal(effectiveTranslator(autoStored), "argos");
assert.equal(startJobRequest(autoStored, null).sourceLanguage, "auto", "Auto is never rewritten to Chinese");
assert.equal(startJobRequest(autoStored, null).translationEngine, "argos");
const enStored = job(reopened, enId);
assert.equal(enStored.translationEngine, "hachimi");
assert.equal(startJobRequest(enStored, null).sourceLanguage, "en");
assert.equal(startJobRequest(enStored, null).translationEngine, "argos");

// Leaving Chinese resets Hachimi; returning to Chinese does not bring it back silently.
const [switchId] = guard.addPaths(["C:/clips/switch.mp4"]);
applySourceLanguage(guard, switchId, "zh");
guard.setTranslationEngine(switchId, "hachimi");
assert.equal(effectiveTranslator(job(guard, switchId)), "hachimi");
applySourceLanguage(guard, switchId, "");
assert.equal(job(guard, switchId).sourceLanguage, undefined);
assert.equal(job(guard, switchId).translationEngine, undefined);
applySourceLanguage(guard, switchId, "zh");
assert.equal(effectiveTranslator(job(guard, switchId)), "argos");
guard.setTranslationEngine(switchId, "hachimi");
applySourceLanguage(guard, switchId, "en");
assert.equal(job(guard, switchId).sourceLanguage, "en");
assert.equal(job(guard, switchId).translationEngine, undefined);
applySourceLanguage(guard, switchId, "zh");
guard.setTranslationEngine(switchId, "hachimi");
applySourceLanguage(guard, switchId, "zh");
assert.equal(job(guard, switchId).translationEngine, "hachimi", "re-selecting Chinese keeps Hachimi");
applySourceLanguage(guard, autoStoredId, "zh");
assert.equal(job(guard, autoStoredId).sourceLanguage, "zh");
assert.equal(job(guard, autoStoredId).translationEngine, undefined, "a stale Auto+Hachimi choice is not revived by Chinese");
assert.equal(startJobRequest(job(guard, autoStoredId), null).translationEngine, "argos");

// Saved defaults keep Hachimi only together with Chinese, so old Auto/English defaults cannot restore it.
assert.deepEqual(translatorDefaults({ dubbing: true, sourceLanguage: "zh", translationEngine: "hachimi" }),
  { dubbing: true, sourceLanguage: "zh", translationEngine: "hachimi" });
assert.deepEqual(translatorDefaults({ dubbing: true, sourceLanguage: undefined, translationEngine: "hachimi" }),
  { dubbing: true, sourceLanguage: undefined });
assert.deepEqual(translatorDefaults({ dubbing: true, sourceLanguage: "en", translationEngine: "hachimi" }),
  { dubbing: true, sourceLanguage: "en" });
assert.deepEqual(translatorDefaults({ dubbing: false }), { dubbing: false });

// A completed job opens its review, one cue is edited and an edit is undone.
const firstOutput = "D:/Results/douyin/douyin.vi.mp4";
reopened.updateStatus(zhId, { ...job(reopened, zhId).status, state: "COMPLETED" }, firstOutput);
const calls = [];
let failSave = true;
const invoke = async (command, args) => {
  calls.push({ command, args });
  if (command === "review_cues") {
    return [
      { cue_id: "c1", machine_text: "Xin chào", text: "Xin chào" },
      { cue_id: "c2", machine_text: "Cảm ơn bạn", text: "Cảm ơn bạn" },
      { cue_id: "c3", machine_text: "Tạm biệt", text: "Tạm biệt" },
    ];
  }
  if (command === "save_review") {
    if (failSave) throw new Error("disk full");
    return "D:/Results/douyin (sửa 1)";
  }
  throw new Error(`unexpected command ${command}`);
};

const completed = job(reopened, zhId);
const cues = await invoke("review_cues", { outputPath: completed.outputPath });
assert.deepEqual(calls[0], { command: "review_cues", args: { outputPath: firstOutput } });
const drafts = new ReviewDraftHistory(cues);
drafts.updateDraft("c2", "Cám ơn nhiều");
drafts.endEdit();
assert.equal(drafts.undo(), "c2");
assert.equal(drafts.draftOf("c2"), "Cảm ơn bạn");
drafts.updateDraft("c1", "  Chào cả nhà  ");
drafts.endEdit();
const overrides = reviewOverrides(cues, (cueId) => drafts.draftOf(cueId));
assert.deepEqual(overrides, { c1: "Chào cả nhà" }, "only the changed cue is sent");

// A failed save keeps the completed output and the review recoverable.
const before = JSON.stringify(reopened.getSnapshot());
await assert.rejects(saveReviewRevision(invoke, reopened, completed, overrides), /disk full/);
assert.deepEqual(calls.at(-1), { command: "save_review", args: { outputPath: firstOutput, overrides: { c1: "Chào cả nhà" } } });
assert.equal(JSON.stringify(reopened.getSnapshot()), before);
assert.equal(job(reopened, zhId).status.state, "COMPLETED");
assert.equal(job(reopened, zhId).outputPath, firstOutput);
assert.equal(drafts.isDirty, true);
assert.equal(drafts.draftOf("c1"), "  Chào cả nhà  ");

// A retried save creates a revision that keeps the job's options and starts in the saved folder.
failSave = false;
const revisionId = await saveReviewRevision(invoke, reopened, job(reopened, zhId), reviewOverrides(cues, (cueId) => drafts.draftOf(cueId)));
const revision = job(reopened, revisionId);
assert.equal(revision.revisionOf, zhId);
assert.equal(revision.outputDir, "D:/Results/douyin (sửa 1)");
assert.equal(revision.sourceLanguage, "zh");
assert.equal(revision.translationEngine, "hachimi");
assert.deepEqual(revision.dubbing, { enabled: true, voiceId: VOICE });
assert.equal(revision.status.state, "QUEUED");
assert.equal(reopened.getSnapshot().selected_job_id, revisionId);
assert.equal(job(reopened, zhId).outputPath, firstOutput, "the original output stays available");
const revisionRequest = startJobRequest(revision, "D:/Elsewhere");
assert.equal(revisionRequest.outputDir, "D:/Results/douyin (sửa 1)");
assert.equal(revisionRequest.outputRoot, null);
assert.equal(revisionRequest.sourceLanguage, "zh");
assert.equal(revisionRequest.translationEngine, "hachimi");
assert.equal(revisionRequest.enableDubbing, true);
assert.equal(revisionRequest.ttsVoiceId, VOICE);
assert.equal(new QueueController(storage, clock).getSnapshot().jobs.find((candidate) => candidate.id === revisionId).revisionOf, zhId);

// Reviewing needs a finished output.
await assert.rejects(saveReviewRevision(invoke, reopened, job(reopened, autoId), {}), /completed job/);
// Review pace follows the engine's phrases: a long line next to short ones is not flagged.
const paceCues = [
  { cue_id: "p1", start_ms: 0, end_ms: 1000 }, { cue_id: "p2", start_ms: 1000, end_ms: 2000 },
  { cue_id: "p3", start_ms: 2100, end_ms: 3000 }, { cue_id: "lone", start_ms: 9000, end_ms: 10000 },
];
const paceText = { p1: "một hai ba bốn năm sáu bảy tám", p2: "chín", p3: "mười", lone: "một hai ba bốn năm sáu bảy" };
const paces = phrasePaces(paceCues, (id) => paceText[id]);
assert.equal(paces.get("p1"), 10 / 3, "p1-p3 are one phrase (gaps < 250 ms)");
assert.equal(paces.get("p1"), paces.get("p3"));
assert.ok(paces.get("p1") < FAST_PHRASE_SYLLABLES_PER_SECOND, "8 syllables in 1 s alone, but fine within its phrase");
assert.equal(paces.get("lone"), 7);
assert.ok(paces.get("lone") > FAST_PHRASE_SYLLABLES_PER_SECOND, "a lone over-long line is still flagged");
assert.equal(FAST_PHRASE_SYLLABLES_PER_SECOND, 5.25);
const speakers = phrasePaces([{ cue_id: "a", start_ms: 0, end_ms: 1000, speaker: "S1" }, { cue_id: "b", start_ms: 1000, end_ms: 2000, speaker: "S2" }],
  () => "một hai");
assert.equal(speakers.get("a"), 2, "different speakers never share a phrase");
const long = Array.from({ length: 7 }, (_, index) => ({ cue_id: `c${index}`, start_ms: index * 1000, end_ms: index * 1000 + 1000 }));
const capped = phrasePaces(long, () => "một");
assert.equal(capped.get("c0"), 1, "at most 5 cues per phrase");
assert.equal(capped.get("c5"), 1);
console.log("desktop Chinese review and revision flow tests passed");

import assert from "node:assert/strict";
import { createReviewSnapshot, groupFindings, ReviewController } from "../../apps/desktop/src/features/review/model.ts";
import { REVIEW_HISTORY_LIMIT, ReviewDraftHistory } from "../../apps/desktop/src/features/review/history.ts";

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

// The live dialog uses this helper for textarea focus/input/blur, revert, undo,
// Save availability, and the dirty check guarding close/cancel.
const cues = Object.freeze([
  Object.freeze({ cue_id: "a", text: "Bản đã lưu A", machine_text: "Bản máy dịch A" }),
  Object.freeze({ cue_id: "b", text: "Bản đã lưu B", machine_text: "Bản máy dịch B" }),
]);
const drafts = new ReviewDraftHistory(cues);
const edit = (history, cueId, ...values) => {
  history.beginEdit(cueId);
  for (const text of values) history.updateDraft(cueId, text);
  history.endEdit(cueId);
};
const assertClean = (history) => {
  assert.equal(history.isDirty, false, "close/cancel must not ask to discard");
  assert.equal(history.canSave, false, "Save must be disabled");
};

// Empty history and focus/blur without a net change are safe no-ops.
assertClean(drafts);
assert.equal(drafts.canUndo, false);
assert.equal(drafts.undo(), null);
drafts.beginEdit("a");
drafts.endEdit("a");
edit(drafts, "a", "Đổi ý", cues[0].text);
drafts.replaceDraft("a", cues[0].text);
assert.equal(drafts.canUndo, false);
assert.equal(drafts.undo(), null);
assertClean(drafts);

// Multiple inputs in one focus-to-blur session produce a single undo step.
drafts.beginEdit("a");
drafts.updateDraft("a", "Đang gõ");
assert.equal(drafts.canUndo, true, "the Undo button can finish the active edit");
drafts.updateDraft("a", "Bản sửa A lần 1");
drafts.endEdit("a");
edit(drafts, "b", "Bản sửa B");
edit(drafts, "a", "Bản sửa A lần 2");
assert.equal(drafts.isDirty, true);
assert.equal(drafts.canSave, true);
assert.equal(drafts.undo(), "a");
assert.equal(drafts.draftOf("a"), "Bản sửa A lần 1");
assert.equal(drafts.draftOf("b"), "Bản sửa B", "undo must preserve other cue drafts");
assert.equal(drafts.undo(), "b");
assert.equal(drafts.draftOf("b"), cues[1].text);
assert.equal(drafts.draftOf("a"), "Bản sửa A lần 1");
assert.equal(drafts.undo(), "a");
assert.equal(drafts.draftOf("a"), cues[0].text);
assert.equal(drafts.canUndo, false);
assert.equal(drafts.undo(), null);
assertClean(drafts);

// Reverting is its own session, including when a typing session is still active.
edit(drafts, "b", "Giữ nguyên bản nháp B");
drafts.beginEdit("a");
drafts.updateDraft("a", "  Bản nháp A\nchưa lưu  ");
drafts.replaceDraft("a", cues[0].machine_text);
assert.equal(drafts.draftOf("a"), cues[0].machine_text);
assert.equal(drafts.undo(), "a");
assert.equal(drafts.draftOf("a"), "  Bản nháp A\nchưa lưu  ", "restore the exact draft");
assert.equal(drafts.draftOf("b"), "Giữ nguyên bản nháp B");
assert.equal(drafts.undo(), "a");
assert.equal(drafts.draftOf("a"), cues[0].text);
assert.equal(drafts.undo(), "b");
assertClean(drafts);
drafts.replaceDraft("a", cues[0].machine_text);
drafts.replaceDraft("a", cues[0].machine_text); // A repeated revert is a no-op.
assert.equal(drafts.undo(), "a");
assert.equal(drafts.draftOf("a"), cues[0].text, "restore a previously saved user correction");
assert.equal(drafts.canUndo, false);
assertClean(drafts);

// Empty/whitespace drafts disable Save, but remain fully undoable.
edit(drafts, "a", "");
assert.equal(drafts.draftOf("a"), "");
assert.equal(drafts.isDirty, true);
assert.equal(drafts.canSave, false);
assert.equal(drafts.undo(), "a");
assertClean(drafts);
edit(drafts, "a", " \n ");
assert.equal(drafts.canSave, false);
drafts.undo();
edit(drafts, "a", `  ${cues[0].text}\n`);
assertClean(drafts); // Matches save_review's existing trim semantics.
drafts.undo();
assert.equal(drafts.draftOf("a"), cues[0].text);

// Finalizing before rerender/filter and a later blur must not double-record.
drafts.beginEdit("a");
drafts.updateDraft("a", "Sửa trước khi lọc");
drafts.endEdit();
drafts.endEdit("a");
assert.equal(drafts.undo(), "a");
assert.equal(drafts.undo(), null);
assertClean(drafts);

// A new focus finalizes the previous cue; a delayed blur cannot end the new one.
drafts.beginEdit("a");
drafts.updateDraft("a", "Sửa A");
drafts.beginEdit("b");
drafts.updateDraft("b", "Đang sửa B");
drafts.endEdit("a");
drafts.updateDraft("b", "Sửa B");
assert.equal(drafts.undo(), "b", "Undo also safely finishes an active session");
assert.equal(drafts.draftOf("b"), cues[1].text);
assert.equal(drafts.draftOf("a"), "Sửa A");
assert.equal(drafts.undo(), "a");
assert.equal(drafts.undo(), null);
assertClean(drafts);

// Opening another job resets both committed history and any active editor.
edit(drafts, "a", "Bản nháp của job cũ");
drafts.beginEdit("b");
drafts.updateDraft("b", "Đang nhập ở job cũ");
const otherJob = [{ cue_id: "a", text: "Câu cùng ID ở job mới" }];
drafts.reset(otherJob);
assert.equal(drafts.draftOf("a"), otherJob[0].text);
assert.throws(() => drafts.draftOf("b"), /unknown review cue/);
assert.equal(drafts.canUndo, false);
assert.equal(drafts.undo(), null);
assertClean(drafts);

// Successful save and confirmed discard use the same reset path as main.ts.
drafts.reset(cues);
edit(drafts, "a", "Bản sửa được lưu");
drafts.reset(cues.map((cue) => ({ ...cue, text: drafts.draftOf(cue.cue_id) })));
assert.equal(drafts.draftOf("a"), "Bản sửa được lưu");
assert.equal(drafts.canUndo, false);
assert.equal(drafts.undo(), null);
assertClean(drafts);
drafts.beginEdit("a");
drafts.updateDraft("a", "Bản nháp sẽ bỏ");
drafts.reset(cues);
assert.equal(drafts.canUndo, false);
assert.equal(drafts.undo(), null);
assert.equal(drafts.draftOf("a"), cues[0].text);
assertClean(drafts);

// Only the most recent bounded sessions are retained; other cues stay intact.
const bounded = new ReviewDraftHistory(cues);
edit(bounded, "b", "Bản nháp B phải được giữ");
for (let index = 1; index <= REVIEW_HISTORY_LIMIT + 5; index++) {
  edit(bounded, "a", `Bản sửa ${index}`);
  edit(bounded, "a", `Bản sửa ${index}`); // No-ops must not evict useful history.
}
for (let index = 0; index < REVIEW_HISTORY_LIMIT; index++) assert.equal(bounded.undo(), "a");
assert.equal(bounded.draftOf("a"), "Bản sửa 5");
assert.equal(bounded.draftOf("b"), "Bản nháp B phải được giữ");
assert.equal(bounded.canUndo, false);
assert.equal(bounded.undo(), null);
assert.equal(bounded.isDirty, true, "history eviction must not move the saved baseline");
bounded.reset();
assertClean(bounded);
assert.equal(bounded.undo(), null);

console.log("review center model and live draft history tests passed");

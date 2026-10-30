import assert from "node:assert/strict";
import { BATCH_CUES, normalizeLines, rewriteAsNarration, syllableBudget } from "../src/features/narration/ai.ts";
import { ReviewDraftHistory } from "../src/features/review/history.ts";

const cue = (id, startMs, endMs, text = "bản dịch") => ({ cue_id: id, start_ms: startMs, end_ms: endMs, source_text: "原文", text });

// A cue's budget is what fits at natural pace, never below two syllables.
assert.equal(syllableBudget(cue("a", 0, 2000)), 7);
assert.equal(syllableBudget(cue("a", 0, 300)), 2);

// Only known cues are kept; emoji, hashtags and markdown go; long lines are cut to the budget.
const cues = [cue("c1", 0, 2000), cue("c2", 2000, 2600)];
assert.deepEqual([...normalizeLines({ lines: [
  { id: "c1", text: "  Và rồi 🔥 **anh ấy** quyết định #drama làm một điều không ai ngờ tới  " },
  { id: "c2", text: "Ngắn thôi" },
  { id: "c9", text: "không có câu này" },
  { id: "c1", text: "trùng lặp" },
  { id: "c2", text: 5 },
] }, cues)], [["c1", "Và rồi anh ấy quyết định làm"], ["c2", "Ngắn thôi"]]);

// Long videos go in parts; later parts continue the story; results merge by id.
const many = Array.from({ length: BATCH_CUES + 5 }, (_, index) => cue(`c${index}`, index * 2000, index * 2000 + 1800));
const prompts = [];
const progress = [];
const lines = await rewriteAsNarration("gemini", "key-not-real", many, "binh-luan", {
  title: "Thử nồi chiên",
  onProgress: (done, total) => progress.push(`${done}/${total}`),
  ask: async (provider, key, request) => {
    assert.equal(provider, "gemini");
    prompts.push(request.parts[0].text);
    const ids = [...request.parts[0].text.matchAll(/"id":"(c\d+)"/g)].map((match) => match[1]);
    return { lines: ids.map((id) => ({ id, text: `Lời thuyết minh ${id}` })) };
  },
});
assert.equal(prompts.length, 2);
assert.match(prompts[0], /phần 1\/2/);
assert.match(prompts[1], /phần 2\/2.*không mở đầu lại/);
assert.match(prompts[0], /Tên video: Thử nồi chiên/);
assert.match(prompts[0], /"toi_da_tieng":6/);
assert.equal(lines.size, many.length);
assert.equal(lines.get(`c${BATCH_CUES}`), `Lời thuyết minh c${BATCH_CUES}`);
assert.deepEqual(progress, ["0/2", "1/2", "2/2"]);
await assert.rejects(rewriteAsNarration("claude", "k", cues, "ke-chuyen", { ask: async () => ({ lines: [] }) }), /không trả về/);

// An AI rewrite is one undo step for all its cues.
const history = new ReviewDraftHistory([{ cue_id: "c1", text: "một" }, { cue_id: "c2", text: "hai" }]);
history.updateDraft("c1", "một đã sửa");
history.replaceDrafts(new Map([["c1", "thuyết minh một"], ["c2", "thuyết minh hai"]]));
assert.deepEqual([history.draftOf("c1"), history.draftOf("c2")], ["thuyết minh một", "thuyết minh hai"]);
history.undo();
assert.deepEqual([history.draftOf("c1"), history.draftOf("c2")], ["một đã sửa", "hai"]);
history.undo();
assert.equal(history.draftOf("c1"), "một");
assert.equal(history.canUndo, false);

console.log("desktop narration tests passed");

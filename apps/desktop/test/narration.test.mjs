import assert from "node:assert/strict";
import { BATCH_CUES, narrationTermsNote, normalizeLines, rewriteAsNarration, syllableBudget } from "../src/features/narration/ai.ts";
import { resolveGuide } from "../src/features/ai/glossary.ts";
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

// Issue #953 (J16): the video's context and the terms its lines say reach the narration prompt; mandatory terms are checked after.
{
  const job = {
    terms: [{ source: "拖拉机", target: "máy cày kéo", mandatory: true, note: "" },
            { source: "GPU", target: "card đồ họa", mandatory: false, note: "" },
            { source: "李明", target: "Lý Minh", mandatory: true, note: "" }],
    books: ["b1"],
    context: { topic: "Phim thử máy nông nghiệp", audience: "", tone: "Trung tính", people: "李明: nông dân", notes: "" },
  };
  const books = [{ id: "b1", name: "Chung", auto: false, terms: [
    { source: "拖拉机", target: "xe kéo", mandatory: true, note: "" },
    { source: "lighthouse", target: "tháp hải đăng", mandatory: false, note: "" }] }];
  const guide = resolveGuide(job, books);
  const sourceCue = (id, source) => ({ cue_id: id, start_ms: 0, end_ms: 4000, source_text: source, text: "bản dịch" });
  const narrCues = [sourceCue("n1", "这台拖拉机很旧"), sourceCue("n2", "李明在开拖拉机"), sourceCue("n3", "The lighthouse is old"), sourceCue("n4", "没有术语")];
  const asked = [];
  let report = null;
  const out = await rewriteAsNarration("claude", "key-not-real", narrCues, "ke-chuyen", {
    guide, onTerms: (terms) => { report = terms; },
    ask: async (_provider, _key, request) => {
      asked.push(request);
      return { lines: [
        { id: "n1", text: "Chiếc máy cày kéo cũ kỹ hiện ra" },
        { id: "n2", text: "李明 lái chiếc xe kéo qua cánh đồng" },
        { id: "n3", text: "Ngọn tháp hải đăng đã cũ" },
        { id: "n4", text: "Mọi thứ yên ắng" },
      ] };
    },
  });
  const text = asked[0].parts[0].text;
  assert.match(asked[0].system, /thuật ngữ BẮT BUỘC/);
  // The video's own mandatory term wins over the shared book's spelling for the same source.
  assert.match(text, /Thuật ngữ BẮT BUỘC, luôn viết đúng như sau: 拖拉机 → máy cày kéo; 李明 → Lý Minh/);
  assert.doesNotMatch(text, /xe kéo/);
  // Only terms the lines say: GPU is not in any line, lighthouse is (preferred, from the shared book).
  assert.match(text, /Thuật ngữ nên dùng \(trừ khi sai ngữ cảnh\): lighthouse → tháp hải đăng/);
  assert.doesNotMatch(text, /GPU/);
  // Context is context, never a term.
  assert.match(text, /Bối cảnh do người dùng ghi:\n- Chủ đề: Phim thử máy nông nghiệp\n- Giọng điệu: Trung tính\n- Nhân vật và cách xưng hô: 李明: nông dân/);
  assert.doesNotMatch(text.split("Thuật ngữ BẮT BUỘC")[1], /Phim thử máy/);
  // A term left in its source spelling is put back; one simply absent is reported, not guessed.
  assert.equal(out.get("n1"), "Chiếc máy cày kéo cũ kỹ hiện ra");
  assert.equal(out.get("n2"), "Lý Minh lái chiếc xe kéo qua cánh đồng");
  assert.deepEqual(report, { replaced: 1, missing: [{ cueId: "n2", terms: ["拖拉机"] }] });
  assert.match(narrationTermsNote(report), /Đã đặt lại 1 thuật ngữ.*1 câu chưa có thuật ngữ bắt buộc \(拖拉机\)/);
  assert.equal(narrationTermsNote(null), "");

  // No glossary: the prompt carries no term or context lines and nothing is reported.
  let none = "unset";
  const plain = [];
  await rewriteAsNarration("claude", "k", narrCues, "ke-chuyen", { guide: resolveGuide(undefined, []), onTerms: () => { none = "called"; },
    ask: async (_p, _k, request) => { plain.push(request.parts[0].text); return { lines: [{ id: "n1", text: "Một câu" }] }; } });
  assert.doesNotMatch(plain[0], /Thuật ngữ|Bối cảnh do người dùng/);
  assert.equal(none, "unset");
}

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

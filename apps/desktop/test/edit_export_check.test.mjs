// Kiểm tra kỹ thuật (Issue #935): the advisory check after each editor export, read strictly and never worded like Final QC.
import assert from "node:assert/strict";
import { CHECK_NOTE, checkBadge, checkLines, checkRunning, checkSummary, checkWarnings, parseExportCheck } from "../src/features/edit/export_check.ts";
import { previewInvoke } from "../src/shell/preview_invoke.ts";

const base = { schema_version: 1, kind: "export_check", advisory: true, video: "D:/Videos/Phim (sửa 1).mp4", warnings: [], unmeasured: [], black: [], frozen: [],
  checked_at: 1_795_000_000 };
const done = { ...base, state: "DONE", percent: 100, warnings: ["black"], black: [[4, 7]], checks: [
  { id: "decode", label: "Giải mã", ok: true, detail: "đọc hết 12,0 giây, không có lỗi" },
  { id: "duration", label: "Thời lượng", ok: true, detail: "video 12000 ms, bản sửa 12000 ms" },
  { id: "audio", label: "Âm thanh", ok: true, detail: "đỉnh -12.7 dBTP" },
  { id: "black", label: "Hình đen", ok: false, detail: "đen 3,0 giây ở 0:04–0:07" },
] };

// Strict parsing: anything that is not the host's advisory check shows nothing.
assert.equal(parseExportCheck(done).checks.length, 4);
for (const bad of [null, undefined, "x", 3, [], { ...done, kind: "final_qc" }, { ...done, kind: undefined }, { ...done, advisory: false },
  { ...done, advisory: "true" }, { ...done, state: "READY_TO_FINALIZE" }, { ...done, state: "done" }, { ...done, checks: "x" },
  { ...done, checks: [{ id: "a", label: "A", ok: "yes", detail: "" }] }, { ...done, checks: [{ id: "a", label: "A", detail: "" }] },
  { ...done, checks: [{ id: 3, label: "A", ok: true, detail: "" }] }]) {
  assert.equal(parseExportCheck(bad), null, JSON.stringify(bad));
}
const many = parseExportCheck({ ...done, checks: Array.from({ length: 60 }, (_, index) => ({ id: `c${index}`, label: "X", ok: true, detail: "d".repeat(900) })) });
assert.equal(many.checks.length, 40, "at most 40 checks are kept");
assert.equal(many.checks[0].detail.length, 400, "each detail is cut to 400 characters");
assert.equal(parseExportCheck({ ...done, checks: [{ id: "a", ok: null }] }).checks[0].label, "a", "a check without a label is named by its id");
assert.equal(parseExportCheck({ ...base, state: "RUNNING", percent: 250, checks: [] }).percent, 100);
assert.equal(parseExportCheck({ ...base, state: "RUNNING", percent: "x", checks: [] }).percent, 0);

// Text and tone of every state.
const summary = (value) => checkSummary(parseExportCheck(value));
assert.deepEqual(summary({ ...base, state: "RUNNING", percent: 42.7, checks: [] }),
  { text: "Đang kiểm tra kỹ thuật… 42% — video đã lưu và xem được ngay; muốn đổi tên, di chuyển hay xóa tệp thì bấm Dừng kiểm tra trước.", tone: "info" });
// queued behind another check: the host checks one video at a time
assert.deepEqual(summary({ ...base, state: "RUNNING", percent: 0, checks: [], waiting: true }),
  { text: "Đang chờ kiểm tra kỹ thuật (mỗi lần kiểm tra một video) — video đã lưu và dùng được ngay.", tone: "info" });
assert.equal(parseExportCheck({ ...base, state: "DONE", percent: 100, checks: [], waiting: true }).waiting, false, "only a RUNNING check waits");
assert.equal(checkRunning([{ check: { ...base, state: "RUNNING", percent: 0, checks: [], waiting: true } }]), true, "a waiting check is still polled");
assert.deepEqual(summary(done), { text: "Kiểm tra kỹ thuật: 1 điểm cần xem.", tone: "warn" });
assert.deepEqual(checkLines(parseExportCheck(done)), ["Hình đen: đen 3,0 giây ở 0:04–0:07"]);
const clean = { ...done, warnings: [], checks: done.checks.filter((check) => check.ok) };
assert.deepEqual(summary(clean), { text: "Kiểm tra kỹ thuật: không thấy lỗi.", tone: "ok" });
assert.deepEqual(checkLines(parseExportCheck(clean)), []);
const unmeasured = { ...clean, checks: [...clean.checks, { id: "captions", label: "Phụ đề", ok: null, detail: "không có tệp phụ đề" },
  { id: "subtitle_frame", label: "Phụ đề trong khung", ok: null, detail: "không có phụ đề in vào hình" }] };
assert.deepEqual(summary(unmeasured), { text: "Kiểm tra kỹ thuật: không thấy lỗi. Chưa đo: Phụ đề, Phụ đề trong khung.", tone: "ok" },
  "what was not measured is named, never counted as fine");
assert.deepEqual(checkLines(parseExportCheck(unmeasured)), ["Chưa đo — Phụ đề: không có tệp phụ đề", "Chưa đo — Phụ đề trong khung: không có phụ đề in vào hình"],
  "and listed with its reason");
assert.equal(checkWarnings(parseExportCheck(unmeasured)), 0, "not measured is not a point to look at");
// A warning and a check not measured in the same report: neither hides the other (#935, Lead review).
const mixed = { ...done, unmeasured: ["subtitle_frame"], checks: [...done.checks,
  { id: "subtitle_frame", label: "Phụ đề trong khung", ok: null, detail: "không có phụ đề in vào hình" }] };
assert.deepEqual(summary(mixed), { text: "Kiểm tra kỹ thuật: 1 điểm cần xem. Chưa đo: Phụ đề trong khung.", tone: "warn" });
assert.deepEqual(checkLines(parseExportCheck(mixed)), ["Hình đen: đen 3,0 giây ở 0:04–0:07", "Chưa đo — Phụ đề trong khung: không có phụ đề in vào hình"]);
assert.equal(checkWarnings(parseExportCheck(mixed)), 1);
assert.equal(checkBadge(mixed).title,
  `Kiểm tra kỹ thuật: 1 điểm cần xem. Chưa đo: Phụ đề trong khung.\nHình đen: đen 3,0 giây ở 0:04–0:07\nChưa đo — Phụ đề trong khung: không có phụ đề in vào hình\n${CHECK_NOTE}`);
assert.equal(checkWarnings(parseExportCheck({ ...mixed, state: "RUNNING" })), 0, "only a finished check has points");
assert.deepEqual(summary({ ...base, state: "STOPPED", percent: 0, checks: [] }),
  { text: "Đã dừng kiểm tra kỹ thuật. Video vẫn được giữ nguyên.", tone: "info" });
assert.deepEqual(summary({ ...base, state: "INTERRUPTED", percent: 0, checks: [] }),
  { text: "Kiểm tra kỹ thuật chưa xong vì DubFlow đã đóng. Video vẫn được giữ nguyên.", tone: "info" });
assert.deepEqual(summary({ ...base, state: "UNAVAILABLE", percent: 0, checks: [], error: "không tìm thấy FFmpeg" }),
  { text: "Không chạy được kiểm tra kỹ thuật (không tìm thấy FFmpeg). Video vẫn được giữ nguyên.", tone: "info" });
assert.equal(CHECK_NOTE, "Chỉ để tham khảo: kiểm tra này không chặn, không xóa và không thay đổi video.");

// Never worded like Final QC, whatever the state.
for (const value of [done, clean, unmeasured, mixed, ...["RUNNING", "STOPPED", "INTERRUPTED", "UNAVAILABLE"].map((state) => ({ ...base, state, percent: 0, checks: [] }))]) {
  const check = parseExportCheck(value);
  for (const text of [checkSummary(check).text, ...checkLines(check), checkBadge(value).title]) {
    assert.doesNotMatch(text, /đạt|Sẵn sàng|READY|Kiểm tra cuối/i, text);
  }
}

// The list line: its class follows the tone, its tooltip holds the points and the note.
assert.deepEqual(checkBadge(done), { text: "Kiểm tra kỹ thuật: 1 điểm cần xem.", className: "edit-export-check mqc-warn",
  title: `Kiểm tra kỹ thuật: 1 điểm cần xem.\nHình đen: đen 3,0 giây ở 0:04–0:07\n${CHECK_NOTE}` });
assert.equal(checkBadge(clean).className, "edit-export-check mqc-ok");
assert.equal(checkBadge({ ...base, state: "RUNNING", percent: 1, checks: [] }).className, "edit-export-check");
assert.equal(checkBadge(undefined), null);
assert.equal(checkBadge({ ...done, kind: "final_qc" }), null);

// Polling goes on only while a check runs.
const running = { ...base, state: "RUNNING", percent: 10, checks: [] };
assert.equal(checkRunning([]), false);
assert.equal(checkRunning([{ path: "a" }, { path: "b", check: done }]), false);
assert.equal(checkRunning([{ path: "a", check: done }, { path: "b", check: running }]), true, "the newest export's check runs");
assert.equal(checkRunning([{ path: "a", check: running }, { path: "b", check: done }]), true, "an older one still running is followed too");
assert.equal(checkRunning([{ path: "a", check: { ...running, kind: "final_qc" } }]), false);
assert.equal(checkRunning([{ path: "a", check: { ...running, state: "INTERRUPTED" } }]), false);

// The browser preview: after the export, its check runs, then reports one point; Dừng kiểm tra stops it.
const now = Date.now;
try {
  let clock = now();
  Date.now = () => clock;
  const outputPath = "preview/check/final_vi.mp4";
  const request = { outputPath, folder: "D:/Videos", fileName: "Phim (sửa 1)", quality: "source", segments: [{ start: 0, end: 12, speed: 1 }] };
  const { path } = await previewInvoke("edit_export", { request });
  clock += 5000;
  let status = await previewInvoke("edit_status", { outputPath, kind: "job" });
  assert.equal(status.status.state, "COMPLETED");
  assert.equal(parseExportCheck(status.exports.at(-1).check).state, "RUNNING");
  clock += 2000;
  status = await previewInvoke("edit_status", { outputPath, kind: "job" });
  // the preview's report has a warning and a check not measured: both are shown
  assert.equal(checkSummary(parseExportCheck(status.exports.at(-1).check)).text, "Kiểm tra kỹ thuật: 1 điểm cần xem. Chưa đo: Phụ đề trong khung.");
  assert.deepEqual(checkLines(parseExportCheck(status.exports.at(-1).check)),
    ["Hình đen: đen 3,0 giây ở 0:04–0:07", "Chưa đo — Phụ đề trong khung: không có phụ đề in vào hình"]);
  const other = "preview/check-stop/final_vi.mp4";
  const started = await previewInvoke("edit_export", { request: { ...request, outputPath: other } });
  clock += 6000;
  await previewInvoke("edit_check_cancel", { outputPath: other, kind: "job", path: started.path });
  status = await previewInvoke("edit_status", { outputPath: other, kind: "job" });
  assert.equal(parseExportCheck(status.exports.at(-1).check).state, "STOPPED");
  assert.equal(path, `${request.folder}/Phim (sửa 1).mp4`);
} finally { Date.now = now; }

console.log("edit export check tests passed");

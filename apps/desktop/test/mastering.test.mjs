import assert from "node:assert/strict";
import {
  nextInQueue, queueSummary, startUnit, PRIORITY_LABELS,
  coverageText, evidenceHelp, provenanceText, isLevel, unitNumber, levelText, masteringOf, needsExportConfirm, parseReview, reviewSummary, seconds, unitBadge,
} from "../src/features/mastering/model.ts";
import { buildRequest } from "../src/features/product/model.ts";

// Levels: four, each described per workflow; anything else is not a level.
assert.ok(isLevel("off") && isLevel("dynamic") && !isLevel("max") && !isLevel(3));
assert.notEqual(levelText("dubbing", "balanced"), levelText("product", "balanced"));
assert.match(levelText("dubbing", "dynamic"), /chỉ gợi ý/);

// The engine's view is checked field by field; malformed parts are dropped.
const raw = {
  kind: "editorial_review_view", plan_id: "product-1", mode: "content", revision: 3, can_undo: true,
  units: [
    { unit_id: "s1", text: "Câu một", function: "hook", start_ms: 0, end_ms: 4000, coverage: { status: "generic" }, notes: [],
      evidence: "NOT_NEEDED", state: "READY", user_state: null, decision: null, chosen: [{ id: "v1c1", source_id: "v1", start_ms: 0, end_ms: 2000, keyframe: "a.jpg" }],
      pieces: [{ source_id: "v1", source_start: 0, source_end: 2000, timeline_start: 0, timeline_end: 2000 }], alternatives: [], unreviewed: false },
    { unit_id: "s2", text: "Câu hai", state: "MATCH_NOT_FOUND", evidence: "MATCH_NOT_FOUND", coverage: { milli: 0, status: "none" },
      user_state: "USER_APPROVED", decision: { pin: [], exclude: ["v1c2"], approved: ["v1c3"] },
      chosen: [{ id: "bad" }], alternatives: [1, 2, 3, 4, { id: "v1c4", source_id: "v1", start_ms: 10, end_ms: 20, score: 300 }], unreviewed: false },
    { text: "no id" },
  ],
  warnings: [{ id: "0123456789abcdef", text: "Cảnh báo", acknowledged: false }, { text: "no id" }],
  unreviewed: [], accept_unreviewed: false, summary: { READY: 1, MATCH_NOT_FOUND: 1, x: "y" },
};
const view = parseReview(raw);
assert.equal(view.units.length, 2);
assert.equal(view.units[1].chosen.length, 0);
assert.deepEqual(view.units[1].alternatives.map((shot) => shot.id), ["v1c4"]);  // malformed entries dropped, at most three kept
assert.deepEqual(view.summary, { READY: 1, MATCH_NOT_FOUND: 1 });
assert.equal(view.warnings.length, 1);
assert.equal(parseReview({ kind: "other", units: [] }), null);

// The badge is the user's word when there is one; the AI's verdict stays in the data.
assert.equal(unitBadge(view.units[0]).text, "Đạt");
assert.equal(unitBadge(view.units[1]).text, "Bạn đã duyệt");
assert.equal(view.units[1].state, "MATCH_NOT_FOUND");
assert.equal(coverageText(view.units[0]), "Không cần cảnh riêng");
assert.equal(coverageText(view.units[1]), "Hình khớp 0 % thời lượng câu");
// rc9 P2-N14: the user's pinned shot is theirs, never "Hình khớp 100 %"
assert.equal(coverageText({ ...view.units[1], coverage: { milli: 0, need_ms: 2000, illustrated_ms: 0, user_ms: 2000 } }), "cảnh bạn chọn 100 % thời lượng câu");
assert.equal(coverageText({ ...view.units[1], coverage: { milli: 500, need_ms: 2000, illustrated_ms: 1000, user_ms: 1000 } }), "AI thấy khớp 50 % · cảnh bạn chọn 50 % thời lượng câu");
assert.equal(seconds(65_400), "1:05,4");
assert.equal(seconds(2_000), "2,0 s");
assert.equal(reviewSummary(view), "Mọi câu đã đạt hoặc đã duyệt · 1 cảnh báo chưa xem");

// Export asks only when something is unreviewed and not yet accepted.
assert.equal(needsExportConfirm(view), false);
assert.equal(needsExportConfirm({ ...view, unreviewed: ["s2"] }), true);
assert.equal(needsExportConfirm({ ...view, unreviewed: ["s2"], accept_unreviewed: true }), false);
assert.equal(needsExportConfirm(null), false);

// A video's mastering report.
assert.equal(masteringOf({ index: 1 }), null);
assert.deepEqual(masteringOf({ mastering: { level: "balanced", unreviewed: 2, warnings: ["a", 3] } }), { unreviewed: 2, userSettled: null, warnings: ["a"], fallback: null, level: "balanced" });
// rc8 P1-N3: sentences the user settled are never shown as an automatic pass
{
  const { masteringNote } = await import("../src/features/mastering/model.ts");
  const settled = masteringOf({ mastering: { level: "balanced", unreviewed: 0, user_settled: 4 } });
  assert.equal(settled.userSettled, 4);
  assert.doesNotMatch(masteringNote(settled), /tự động/);
  assert.match(masteringNote(settled), /Bạn đã duyệt hoặc chọn cảnh cho 4 câu/);
  assert.doesNotMatch(masteringNote({ unreviewed: 0, userSettled: null, fallback: null }), /tự động/, "an older run: who settled is unknown");
  assert.match(masteringNote({ unreviewed: 0, userSettled: 0, fallback: null }), /tự động \(chưa hiệu chỉnh/);
  assert.equal(masteringNote({ unreviewed: 3, userSettled: 1, fallback: null }), "3 câu cần xem lại cảnh");
}

// The product request: off sends nothing new; a level and the vision route are sent; "Chỉ chữ" never asks for mastering.
const base = { info: { name: "Nồi", price: "1đ", features: [], audience: "", notes: "" }, voiceId: "vi", scripts: [{ text: "Câu.", use: true }],
  visuals: ["D:\\a.mp4"], music: null, volumePercent: 15, cpuOnly: false, outputRoot: null };
assert.equal("mastering" in buildRequest({ ...base, mastering: "off", visionProvider: "gemini" }), false);
assert.equal("mastering" in buildRequest(base), false);
const on = buildRequest({ ...base, mastering: "balanced", visionProvider: "gemini" });
assert.equal(on.mastering, "balanced");
assert.deepEqual(on.visionAi, { provider: "gemini" });
assert.equal("mastering" in buildRequest({ ...base, template: "chi-chu", info: { ...base.info, features: ["x"] }, mastering: "dynamic" }), false);
assert.equal("mastering" in buildRequest({ ...base, mastering: "max" }), false);

// A match the planner could not place is said so: never "has a match" beside "0 %" (rc4 Windows Top 5).
const unplaced = { evidence: "MATCH_AVAILABLE", coverage: { milli: 0, illustrated_ms: 0, status: "none" }, decision: null };
assert.match(evidenceHelp(unplaced), /chưa đặt được/);
assert.equal(evidenceHelp({ ...unplaced, coverage: { milli: 500, illustrated_ms: 1500 } }), "Có cảnh khớp lời câu này.");
// rc8 P2-N6: a pinned shot is the user's choice; the planner's "has a match" never vouches for it
assert.match(evidenceHelp({ ...unplaced, decision: { pin: ["v1c1"], exclude: [] } }), /Cảnh do bạn chọn/);
assert.doesNotMatch(evidenceHelp({ ...unplaced, coverage: { milli: 900, illustrated_ms: 1500 }, decision: { pin: ["v1c1"], exclude: [] } }), /Có cảnh khớp/);
assert.equal(evidenceHelp({ evidence: "UNKNOWN", coverage: {}, decision: null }), "");
// P1-N1: an opening that needs no shot of its own but is not ready says its shot needs a look
assert.equal(evidenceHelp({ evidence: "NOT_NEEDED", coverage: { status: "generic" }, decision: null, state: "READY" }),
  "Câu mở đầu/kêu gọi: không cần cảnh riêng.");
assert.match(evidenceHelp({ evidence: "NOT_NEEDED", coverage: { status: "generic" }, decision: null, state: "NEEDS_REVIEW" }),
  /cảnh đang dùng cần kiểm tra/);

console.log("mastering tests passed");

// Lồng tiếng: the Hậu kỳ card reads the export's QC; off has no card; the true-peak ceiling is checked after AAC.
{
  const { parseDubbingQc, qcRows, climaxText } = await import("../src/features/mastering/qc.ts");
  assert.equal(parseDubbingQc(null), null);
  assert.equal(parseDubbingQc({ level: "max" }), null);
  const qc = parseDubbingQc({
    level: "balanced", loudness: true, fallback: false, video_filters: ["crop"], notes: ["Màu: giữ nguyên.", 3],
    measured: { mix: { integrated: -12.4, true_peak: 0.3, lra: 7.6 } },
    output_loudness: { integrated: -16.1, true_peak: -1.2, lra: 7.1 },
    climax: { start_ms: 63500, end_ms: 68500 }, climax_decision: "apply",
  });
  assert.deepEqual(qc.notes, ["Màu: giữ nguyên."]);
  assert.equal(qc.climaxDecision, null, "only keep/drop are decisions");
  const rows = qcRows(qc);
  const loudRow = rows.find((row) => row.label === "Âm lượng");
  assert.equal(loudRow.tone, "warn");
  assert.match(loudRow.text, /−12,4 LUFS.*−16,1 LUFS.*−1,2 dBTP sau AAC.*vượt trần/);
  assert.ok(rows.some((row) => row.label === "Màu" && row.text === "Giữ nguyên."));
  assert.match(climaxText(qc), /1:03–1:08.*không chèn vào video/);
  const ok = qcRows(parseDubbingQc({ level: "light", loudness: true, notes: [], measured: {}, output_loudness: { integrated: -16, true_peak: -2.5, lra: 5 } }));
  assert.equal(ok.find((row) => row.label === "Âm lượng").tone, "ok");
  const back = qcRows(parseDubbingQc({ level: "light", loudness: false, fallback: true, notes: ["Đã quay về âm thanh gốc (B1)"], measured: {} }));
  assert.ok(back.some((row) => row.label === "Quay về"));
}

// Queue: a job keeps its level; off is no value; unknown levels are refused.
{
  const { isMasteringLevel } = await import("../src/features/queue/model.ts");
  assert.ok(isMasteringLevel("light") && isMasteringLevel("dynamic") && !isMasteringLevel("off") && !isMasteringLevel("max"));
  const { readJobDefaults, serializeJobDefaults } = await import("../src/features/queue/defaults.ts");
  const saved = serializeJobDefaults({ dubbing: { enabled: true, voiceId: null }, mastering: "balanced" });
  assert.equal(readJobDefaults(saved, null).mastering, "balanced");
  assert.equal(readJobDefaults(JSON.stringify({ ...JSON.parse(saved), mastering: "max" }), null).mastering, undefined);
}
console.log("mastering qc ok");

// Export gate (lead review of #704): fails closed on an unreadable or malformed review or a failed save.
{
  const { exportGate } = await import("../src/features/mastering/model.ts");
  const review = { outputDir: "D:/DubFlow Output/Ban hang - A/run-1", video: 1 };
  const view = (unreviewed, accepted = false) => ({ ...raw, unreviewed, accept_unreviewed: accepted });
  const calls = [];
  const passedQc = { kind: "final_qc", state: "READY_TO_FINALIZE", passed: true, unmeasured: [],
    checks: [{ id: "render", label: "Bản dựng", ok: true, detail: "video-1.mp4" }] };
  const invoker = (answers) => async (command, args) => {
    const key = command === "mastering_final_qc" ? "qc" : args.action;
    if (key !== "qc") calls.push(key);
    const next = key in answers ? answers[key] : key === "qc" ? passedQc : undefined;
    if (next instanceof Error) throw next;
    return next;
  };
  const yes = async () => true, no = async () => false;
  assert.deepEqual(await exportGate(invoker({ view: view([]) }), review, yes), { open: true });
  assert.equal((await exportGate(invoker({ view: new Error("io") }), review, yes)).open, false);
  assert.match((await exportGate(invoker({ view: new Error("io") }), review, yes)).error, /Không đọc được/);
  assert.equal((await exportGate(invoker({ view: { kind: "nonsense" } }), review, yes)).open, false);
  assert.equal((await exportGate(invoker({ view: null }), review, yes)).open, false);
  const unreviewedView = view(raw.units.map((unit) => unit.unit_id));
  assert.deepEqual(await exportGate(invoker({ view: unreviewedView }), review, no), { open: false });
  const failedSave = await exportGate(invoker({ view: unreviewedView, accept: new Error("disk") }), review, yes);
  assert.equal(failedSave.open, false);
  assert.match(failedSave.error, /Không lưu được/);
  calls.length = 0;
  assert.deepEqual(await exportGate(invoker({ view: unreviewedView, accept: unreviewedView }), review, yes), { open: true });
  assert.deepEqual(calls, ["view", "accept"]);
  assert.deepEqual(await exportGate(invoker({ view: view(["s1"], true) }), review, no), { open: true }, "accepted before: no second ask");
  // §B Final QC: a reviewed video whose render fails the final check does not open for export, and says why
  const failedQc = { ...passedQc, state: "FINAL_QC_FAILED", passed: false,
    checks: [...passedQc.checks, { id: "duration", label: "Thời lượng", ok: false, detail: "dài 6500 ms, kế hoạch 6000 ms" },
             { id: "audio", label: "Âm thanh", ok: null, detail: "chưa đo đỉnh âm" }] };
  const blocked = await exportGate(invoker({ view: view([]), qc: failedQc }), review, yes);
  assert.equal(blocked.open, false);
  assert.match(blocked.error, /Kiểm tra cuối chưa đạt/);
  assert.match(blocked.error, /Thời lượng: dài 6500/);
  assert.match(blocked.error, /Chưa đo: Âm thanh/);
  assert.equal((await exportGate(invoker({ view: view([]), qc: new Error("python") }), review, yes)).open, false, "a QC that cannot run fails closed");
  assert.equal((await exportGate(invoker({ view: view([]), qc: { kind: "other" } }), review, yes)).open, false);
  const { parseFinalQc } = await import("../src/features/mastering/model.ts");
  assert.equal(parseFinalQc({ ...passedQc, checks: [{ id: "audio", label: "x", ok: false, detail: "" }] }).passed, false,
    "passed is never trusted over a failed check");
  assert.equal(parseFinalQc({ ...passedQc, state: "RENDER_PENDING" }).passed, false);
  const pendingMsg = (await exportGate(invoker({ view: view([]), qc: { ...passedQc, state: "RENDER_PENDING", passed: false } }), review, yes)).error;
  assert.match(pendingMsg, /render lại/);
}
console.log("export gate ok");

// rc2 Windows check: a run saved beside its clips is remembered (D2); the card says why the voice is quieter (D5).
{
  const { parseRecentRuns, rememberRun, RECENT_RUNS_MAX } = await import("../src/features/product/model.ts");
  assert.deepEqual(parseRecentRuns(null), []);
  assert.deepEqual(parseRecentRuns("{not json"), []);
  assert.deepEqual(parseRecentRuns(JSON.stringify(["D:/a", 3, "", "D:/a", "D:/b"])), ["D:/a", "D:/b"]);
  let raw = null;
  for (let i = 0; i < RECENT_RUNS_MAX + 5; i += 1) raw = rememberRun(raw, `D:/clips/DubFlow Output/Ban hang - A/run-${i}`);
  const kept = parseRecentRuns(raw);
  assert.equal(kept.length, RECENT_RUNS_MAX);
  assert.equal(kept[0], `D:/clips/DubFlow Output/Ban hang - A/run-${RECENT_RUNS_MAX + 4}`, "newest first");
  assert.equal(parseRecentRuns(rememberRun(raw, kept[5]))[0], kept[5], "a run started again moves to the front once");
  const { parseDubbingQc, qcRows } = await import("../src/features/mastering/qc.ts");
  const lowered = qcRows(parseDubbingQc({ level: "balanced", loudness: true, measured: { mix: { integrated: -16.6, true_peak: -0.7, lra: 9 } },
    output_loudness: { integrated: -18.5, true_peak: -2.5, lra: 9 },
    notes: ["Âm lượng: -16.6 → -18.4 LUFS (thấp hơn mục tiêu -16 LUFS vì đỉnh -0.7 dBTP: giữ tăng/giảm tuyến tính)."] }));
  assert.match(lowered.find((row) => row.label === "Âm lượng").text, /Thấp hơn mục tiêu vì đỉnh/);
}
console.log("rc2 findings ok");

assert.equal(unitNumber("s3"), "3");
assert.equal(unitNumber("i2.u14"), "14");
assert.equal(unitNumber("hook.u1"), "1");

// P2-14: a file name is evidence of provenance "filename_metadata", never content
{
  const named = parseReview({ kind: "editorial_review_view", mode: "signals_only", provenance: { filename_metadata: 3, vision: 0, bogus: "x" }, units: [], warnings: [] });
  assert.deepEqual(named.provenance, { filename_metadata: 3, vision: 0 });
  assert.match(provenanceText(named), /tên file 3 cảnh/);
  assert.match(provenanceText(named), /không tính là đã thấy nội dung/);
  assert.match(provenanceText(named), /không câu nào được coi là sẵn sàng/);
  const seen = parseReview({ kind: "editorial_review_view", mode: "content", provenance: { vision: 5 }, units: [], warnings: [] });
  assert.equal(provenanceText(seen), "Bằng chứng: AI nhìn hình 5 cảnh.");
  const old = parseReview({ kind: "editorial_review_view", mode: "signals_only", units: [], warnings: [] });
  assert.match(provenanceText(old), /Chưa có bằng chứng nội dung/);
}

{
  // the review queue (Assisted Editorial Production §B)
  const raw = {
    kind: "editorial_review_view", mode: "content", warnings: [], queue: ["u2", "u3", "u1", "ghost"], queue_calibrated: false, position: "u3",
    shots: [{ id: "c1", source_id: "v1", start_ms: 0, end_ms: 2000 }, { bad: true }],
    units: [
      { unit_id: "u1", state: "READY", priority: "optional", queue_reasons: [] },
      { unit_id: "u2", state: "MATCH_NOT_FOUND", priority: "critical", queue_reasons: ["chưa tìm thấy cảnh khớp"], later: true },
      { unit_id: "u3", state: "NEEDS_REVIEW", priority: "review", queue_reasons: ["x"] },
      { unit_id: "u4", state: "READY", priority: "nonsense", user_state: "USER_SOURCE_MISSING", decision: { pin: [], exclude: [], source_missing: true } },
    ],
  };
  const view = parseReview(raw);
  assert.deepEqual(view.queue, ["u2", "u3", "u1"]);  // an id the units do not have is dropped
  assert.equal(view.units[1].priority, "critical");
  assert.equal(view.units[1].later, true);
  assert.equal(view.units[3].priority, null);
  assert.equal(view.units[3].decision.source_missing, true);
  assert.equal(unitBadge(view.units[3]).text, "Bạn ghi: không có cảnh phù hợp");
  assert.equal(view.shots.length, 1);
  assert.equal(queueSummary(view), "Hàng đợi duyệt: 1 nghiêm trọng · 1 cần xem · 1 tuỳ chọn (nhóm theo cờ của AI, chưa hiệu chỉnh)");
  assert.equal(startUnit(view), "u3");  // the saved place
  assert.equal(startUnit({ ...view, position: "u9" }), "u2");
  assert.equal(nextInQueue(view, "u2"), "u3");
  assert.equal(nextInQueue(view, "u1"), "u2");  // wraps to the first one waiting
  // u3 was settled while the user was on it: next is what came after it in the queue they were walking
  assert.equal(nextInQueue({ queue: ["u2", "u1"] }, "u3", ["u2", "u3", "u1"]), "u1");
  assert.equal(nextInQueue({ queue: [] }, "u1"), null);
  assert.equal(queueSummary({ units: [], queue: [], queue_calibrated: false }), "Hàng đợi duyệt trống: mọi câu đã được bạn xử lý.");
  assert.equal(PRIORITY_LABELS.critical.text, "Nghiêm trọng");
  console.log("review queue ok");
}

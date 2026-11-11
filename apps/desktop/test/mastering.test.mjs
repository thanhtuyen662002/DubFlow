import assert from "node:assert/strict";
import {
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
assert.deepEqual(masteringOf({ mastering: { level: "balanced", unreviewed: 2, warnings: ["a", 3] } }), { unreviewed: 2, warnings: ["a"], fallback: null, level: "balanced" });

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
assert.equal(evidenceHelp({ ...unplaced, decision: { pin: ["v1c1"], exclude: [] } }), "Có cảnh khớp lời câu này.");
assert.equal(evidenceHelp({ evidence: "UNKNOWN", coverage: {}, decision: null }), "");

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
  const invoker = (answers) => async (command, args) => { calls.push(args.action); const next = answers[args.action]; if (next instanceof Error) throw next; return next; };
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

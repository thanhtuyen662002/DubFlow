import assert from "node:assert/strict";
import {
  nextInQueue, queueSummary, startUnit, PRIORITY_LABELS,
  coverageText, evidenceHelp, exclusionText, familyLines, provenanceText, wholeShotOf, isLevel, unitNumber, levelText, masteringOf, needsExportConfirm, parseReview, reviewSummary, seconds, unitBadge,
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

// #805: a piece knows its whole shot, an excluded shot says by which sentence, and "Bỏ cả cảnh gốc" lists what it leaves out
{
  const split = parseReview({
    ...raw,
    units: [{ ...raw.units[0], chosen: [{ id: "v1c3f2", source_id: "v1", start_ms: 5000, end_ms: 6000, parent: "v1c3", excluded: true, excluded_by: "s2" }] }],
    shots: [{ id: "v1c3f1", source_id: "v1", start_ms: 4000, end_ms: 5000 }],
    families: {
      v1c3: { id: "v1c3", source_id: "v1", start_ms: 4000, end_ms: 7000, pieces: [
        { id: "v1c3f1", start_ms: 4000, end_ms: 5000, used_by: ["s1"] }, { id: "v1c3f2", start_ms: 5000, end_ms: 6000, used_by: [] }, { bad: 1 }] },
      v9: { id: "other", source_id: "v1", start_ms: 0, end_ms: 1, pieces: [] },  // a key that is not its id is dropped
    },
    exclusions: [{ id: "v1c3", by: "s2", whole: true, covers: ["v1c3f1", "v1c3f2"], reused: [] }, { by: "s1" }],
  });
  const piece = split.units[0].chosen[0];
  assert.deepEqual([piece.parent, piece.excluded, piece.excluded_by], ["v1c3", true, "s2"]);
  assert.deepEqual([split.shots[0].parent, split.shots[0].excluded, split.shots[0].excluded_by], ["v1c3f1", false, null]);
  assert.deepEqual(Object.keys(split.families), ["v1c3"]);
  assert.deepEqual(familyLines(split.families.v1c3), ["Đoạn 1 · 4,0 s–5,0 s · Câu 1 đang dùng", "Đoạn 2 · 5,0 s–6,0 s · chưa câu nào dùng"]);
  assert.equal(split.exclusions.length, 1);
  assert.equal(exclusionText(split.exclusions[0]), "Câu 2 đã loại cả cảnh gốc v1c3 (2 đoạn)");
  assert.equal(exclusionText({ id: "v1c4", by: "i1.u3", whole: false, covers: ["v1c4"], reused: ["v1c4"] }),
    "Câu 3 đã loại cảnh v1c4 · vẫn phải dùng vì không còn cảnh nào khác");
  // "Bỏ cả cảnh gốc" is offered on every piece of a split shot, an unused alternative too, never on a whole shot
  assert.equal(wholeShotOf(split, { id: "v1c3f1", parent: "v1c3" })?.id, "v1c3");
  assert.equal(wholeShotOf(split, split.units[0].chosen[0])?.id, "v1c3");
  assert.equal(wholeShotOf(split, { id: "v1c4", parent: "v1c4" }), null);
  assert.equal(wholeShotOf(split, { id: "v1c9f1", parent: "v1c9" }), null);  // no family listed: nothing to confirm
  assert.deepEqual([view.families, view.exclusions], [{}, []]);  // an older engine: nothing to show
}

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
  // issue #715: the post-AAC measurement shows even when no normalisation was applied (none needed, or B1)
  const unfiltered = qcRows(parseDubbingQc({ level: "balanced", loudness: false, notes: ["Âm lượng: giữ nguyên (đã gần mục tiêu)."],
    measured: { mix: { integrated: -16.2, true_peak: -2.0, lra: 6 } }, output_loudness: { integrated: -16.3, true_peak: -2.1, lra: 6 } }));
  const plain = unfiltered.find((row) => row.label === "Âm lượng");
  assert.equal(plain.tone, "info", "nothing was normalised: shown as a measurement");
  assert.match(plain.text, /^Giữ nguyên \(đã gần mục tiêu\)\. Đo bản xuất: −16,3 LUFS, đỉnh −2,1 dBTP sau AAC \(trần −1,5 dBTP: đạt\)$/);
  const fellBack = qcRows(parseDubbingQc({ level: "light", loudness: false, fallback: true, notes: ["Đã quay về âm thanh gốc (B1)"],
    measured: {}, output_loudness: { integrated: -14.0, true_peak: -0.4, lra: 8 } }));
  assert.ok(fellBack.some((row) => row.label === "Quay về"), "the B1 explanation stays");
  const loudB1 = fellBack.find((row) => row.label === "Âm lượng");
  assert.equal(loudB1.tone, "warn");
  assert.match(loudB1.text, /^Không chuẩn hóa\. Đo bản xuất: −14,0 LUFS, đỉnh −0,4 dBTP sau AAC \(vượt trần/);
  // no measurement and no normalisation: unchanged
  assert.equal(back.find((row) => row.label === "Âm lượng").text, "Giữ nguyên.");
  // mastering off: no card at all
  assert.equal(parseDubbingQc({ loudness: false, output_loudness: { integrated: -16, true_peak: -2, lra: 5 } }), null);
  // a report from before issue #821 has no conformance: the rows above are unchanged by it
  assert.equal(qc.conformance, null);
}

// Issue #821: the engine's own post-AAC conformance (ceiling −1.5 dBTP) is read strictly and shown in the Âm lượng row.
{
  const { parseDubbingQc, qcRows } = await import("../src/features/mastering/qc.ts");
  const card = (conformance, extra = {}) => parseDubbingQc({
    level: "balanced", loudness: true, notes: ["Âm lượng: −15,4 → −17,2 LUFS."], measured: { mix: { integrated: -15.4, true_peak: -0.7, lra: 7.6 } },
    output_loudness: { integrated: -17.7, true_peak: -2.3, lra: 7.4 }, conformance, ...extra });
  const row = (qc) => qcRows(qc).find((item) => item.label === "Âm lượng");
  const corrected = { status: "corrected", ceiling_dbtp: -1.5, true_peak_dbtp: -2.3, trim_db: 0.49, tries: 2, path: "B2" };
  // "unmeasured" (false here) was added by the review of #821 for the B1 / Vietsub-only records
  assert.deepEqual(card(corrected).conformance, { status: "corrected", path: "B2", truePeak: -2.3, trimDb: 0.49, tries: 2, ceilingMet: null, unmeasured: false });
  // strict: unknown states, another ceiling, bad numbers or a record that contradicts itself are dropped
  for (const bad of [{ ...corrected, status: "ok" }, { ...corrected, path: "B3" }, { ...corrected, ceiling_dbtp: -1 },
    { ...corrected, trim_db: -0.5 }, { ...corrected, trim_db: "0.5" }, { ...corrected, tries: 1.5 }, { ...corrected, trim_db: 0 },
    { ...corrected, true_peak_dbtp: -1.2 }, { ...corrected, path: "B1" }, { ...corrected, status: "not_applicable" },
    { ...corrected, ceiling_met: "yes" }, [corrected], "corrected", null,
    { ...corrected, unmeasured: true }, { ...corrected, unmeasured: "yes" }]) {
    assert.equal(card(bad).conformance, null, JSON.stringify(bad));
  }
  const fixed = row(card(corrected));
  assert.equal(fixed.tone, "ok");
  assert.match(fixed.text, /\(trần −1,5 dBTP: đạt\)\. Đã giảm thêm 0,5 dB sau AAC để đỉnh ≤ −1,5 dBTP$/);
  // the trim alone (the mix needed no normalisation): loudness is true and the planner's "giữ nguyên" is not shown
  const trimOnly = row(parseDubbingQc({ level: "light", loudness: true, notes: ["Âm lượng: đã đạt −16,2 LUFS, đỉnh −3,0 dBTP; giữ nguyên."],
    measured: { mix: { integrated: -16.2, true_peak: -3.0, lra: 6 } }, output_loudness: { integrated: -16.8, true_peak: -2.0, lra: 6 },
    conformance: { ...corrected, true_peak_dbtp: -2.0, trim_db: 0.6 } }));
  assert.match(trimOnly.text, /Đã giảm thêm 0,6 dB sau AAC/);
  assert.doesNotMatch(trimOnly.text, /giữ nguyên/i);
  const failed = row(card({ status: "failed", ceiling_dbtp: -1.5, true_peak_dbtp: -1.4, trim_db: 0.49, tries: 4, path: "B2" },
    { output_loudness: { integrated: -17.7, true_peak: -1.4, lra: 7.4 } }));
  assert.equal(failed.tone, "warn");
  assert.match(failed.text, /vượt trần −1,5 dBTP\)\. Không đạt trần −1,5 dBTP sau AAC dù đã giảm thêm 0,5 dB qua 4 lần thử mã hóa/);
  const unmeasured = row(card({ status: "unmeasured", ceiling_dbtp: -1.5, true_peak_dbtp: null, trim_db: 0, tries: 0, path: "B2" },
    { output_loudness: null }));
  assert.equal(unmeasured.tone, "warn");
  assert.match(unmeasured.text, /không đo được sau AAC nên chưa xác nhận trần −1,5 dBTP/);
  const plainUnmeasured = row(parseDubbingQc({ level: "light", loudness: false, notes: ["Âm lượng: đã đạt −16,2 LUFS; giữ nguyên."], measured: {},
    conformance: { status: "unmeasured", ceiling_dbtp: -1.5, true_peak_dbtp: null, trim_db: 0, tries: 1, path: "B2" } }));
  assert.equal(plainUnmeasured.tone, "warn");
  assert.match(plainUnmeasured.text, /^Đã đạt −16,2 LUFS; giữ nguyên; không đo được sau AAC/);
  const b1 = row(parseDubbingQc({ level: "light", loudness: false, fallback: true, notes: ["Đã quay về âm thanh gốc (B1)"], measured: {},
    output_loudness: { integrated: -14.0, true_peak: -0.4, lra: 8 },
    conformance: { status: "not_applicable", ceiling_dbtp: -1.5, true_peak_dbtp: -0.4, trim_db: 0, tries: 0, path: "B1", ceiling_met: false } }));
  assert.equal(b1.tone, "warn");
  assert.match(b1.text, /vượt trần −1,5 dBTP\)\. Đây là âm thanh gốc \(B1\), không được chỉnh$/);
  // every trim tried and discarded: the card says so, never "giảm thêm 0,0 dB"
  const kept = row(card({ status: "failed", ceiling_dbtp: -1.5, true_peak_dbtp: -1.31, trim_db: 0, tries: 4, path: "B2" },
    { output_loudness: { integrated: -17.2, true_peak: -1.31, lra: 7.4 } }));
  assert.equal(kept.tone, "warn");
  assert.match(kept.text, /Không đạt trần −1,5 dBTP sau AAC dù đã thử giảm 3 lần \(không lần nào hạ được đỉnh nên giữ bản chưa giảm\)$/);
  assert.doesNotMatch(kept.text, /0,0 dB/);
  // B1 / Vietsub only, export not measured: said, never "Giữ nguyên." as if it were fine
  const b1Record = { status: "not_applicable", ceiling_dbtp: -1.5, true_peak_dbtp: null, trim_db: 0, tries: 0, path: "B1", ceiling_met: null, unmeasured: true };
  const b1Card = (record) => parseDubbingQc({ level: "light", loudness: false, fallback: true, notes: ["Đã quay về âm thanh gốc (B1)"], measured: {},
    output_loudness: null, conformance: record });
  assert.equal(b1Card(b1Record).conformance.unmeasured, true);
  const b1Unmeasured = row(b1Card(b1Record));
  assert.equal(b1Unmeasured.tone, "warn");
  assert.equal(b1Unmeasured.text, "Âm thanh gốc (B1), không được chỉnh; không đo được sau AAC nên chưa xác nhận trần −1,5 dBTP.");
  for (const bad of [{ ...b1Record, true_peak_dbtp: -2 }, { ...b1Record, ceiling_met: true }]) {
    assert.equal(b1Card(bad).conformance, null, JSON.stringify(bad));
  }
  const vietsub = row(b1Card({ ...b1Record, path: "none" }));
  assert.equal(vietsub.tone, "info");
  assert.match(vietsub.text, /không đo được sau AAC/);
  const passed = row(card({ status: "passed", ceiling_dbtp: -1.5, true_peak_dbtp: -2.3, trim_db: 0, tries: 1, path: "B2" }));
  assert.equal(passed.tone, "ok");
  assert.match(passed.text, /\(trần −1,5 dBTP: đạt\)$/);
  assert.doesNotMatch(passed.text, /giảm thêm/);
}
console.log("aac conformance ok");

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

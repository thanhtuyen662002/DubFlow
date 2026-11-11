import assert from "node:assert/strict";
import {
  coverageText, isLevel, levelText, masteringOf, needsExportConfirm, parseReview, reviewSummary, seconds, unitBadge,
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

console.log("mastering tests passed");

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

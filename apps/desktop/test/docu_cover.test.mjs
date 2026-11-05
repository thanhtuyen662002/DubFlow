import assert from "node:assert/strict";
import { clampBand, coverRequest, docuDir, sameCover, sourceCovers } from "../src/features/edit/docu_cover.ts";

// Issue #520: a documentary's burned-in cover, per source, from its spec and visual plan.
const open = { spec: { sources: [
  { id: "src_01", title: "Đập", video: "D:\\a.mp4" },
  { id: "src_02", title: "Cầu", video: "D:\\b.mp4" },
  { id: "src_03", title: "Không có kế hoạch", video: "D:\\c.mp4" },
] } };
const plan = { plan: { sources: [
  { id: "src_01", framing: { band: { top: 0.78, bottom: 0.97, share: 0.9 }, clear: false, logos: [[0.03, 0.04, 0.2, 0.09], [0.9, 0.9, 0.5, 0.5]] } },
  // Cleared by zoom: nothing stretched, so nothing to show.
  { id: "src_02", framing: { band: { top: 0.9, bottom: 0.95 }, clear: true, logos: [] } },
] } };
const covers = sourceCovers(open, plan);
assert.deepEqual(covers.map((cover) => cover.id), ["src_01", "src_02"]);
assert.deepEqual(covers[0].band, { top: 0.78, bottom: 0.97 });
assert.deepEqual(covers[0].logos, [[0.03, 0.04, 0.2, 0.09]], "a box outside the frame is dropped");
assert.equal(covers[1].band, null);
assert.deepEqual(sourceCovers(open, { plan: null }), []);
assert.deepEqual(sourceCovers(null, plan), []);

const edited = structuredClone(covers[0]);
assert.ok(sameCover(edited, covers[0]));
edited.band = clampBand(0.9, 0.85);
assert.deepEqual(edited.band, { top: 0.9, bottom: 0.91 });
assert.deepEqual(clampBand(-0.2, 1.4), { top: 0, bottom: 1 });
assert.ok(!sameCover(edited, covers[0]));
assert.deepEqual(coverRequest("D:\\run", { ...edited, band: null, logos: [] }), { dir: "D:\\run", source: "src_01", band: null, logos: [] });
assert.deepEqual(coverRequest("D:\\run", covers[0]).band, [0.78, 0.97]);
assert.equal(docuDir("D:\\Out\\Phim tai lieu - X\\run-1\\documentary.mp4"), "D:\\Out\\Phim tai lieu - X\\run-1");
assert.equal(docuDir("/out/run-1/documentary.mp4"), "/out/run-1");
console.log("docu cover ok");

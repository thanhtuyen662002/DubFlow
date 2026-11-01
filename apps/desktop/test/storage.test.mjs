import assert from "node:assert/strict";
import { gigabytes, sizeLabel } from "../src/features/fetch/progress.ts";
import {
  AFTER_JOB_DELAY_MS, AUTO_CATEGORIES, AUTO_CLEAN_DELAY_MS, AUTO_CLEAN_KEY, autoCleanDays, autoCleanEnabled, autoNotice,
  CATEGORY_TEXT, categorySize, cleanableBytes, cleanButtonLabel, defaultSelection, parseClean, parseReport, parentFolder,
  queueOutputs, resultLine, summaryLine,
} from "../src/features/storage/model.ts";

const GB = 1_073_741_824;

// Vietnamese sizes: one decimal in GB, MB below 1 GB.
assert.equal(gigabytes(14.2 * GB), "14,2");
assert.equal(sizeLabel(9.8 * GB), "9,8 GB");
assert.equal(sizeLabel(1234.5 * GB), "1.234,5 GB");
assert.equal(sizeLabel(350 * 1_048_576), "350 MB");
assert.equal(sizeLabel(0), "0 MB");
assert.equal(sizeLabel(5 * 1_048_576), "5,0 MB");

const report = parseReport({
  categories: [
    { id: "versions", bytes: 8 * GB, items: 12, busy: false },
    { id: "work", bytes: 1.8 * GB, items: 4, busy: false },
    { id: "link_cache", bytes: 0, items: 0, busy: true },
    { id: "profiles", bytes: 0, items: 0, busy: false },
    { id: "unknown", bytes: 5 * GB },
  ],
  total_used: 14.2 * GB, install_free_bytes: 50 * GB, output_free_bytes: null,
});
// Fixed order, unknown ids dropped, missing values become zero.
assert.deepEqual(report.categories.map((category) => category.id), ["work", "versions", "profiles", "link_cache"]);
assert.equal(report.installFreeBytes, 50 * GB);
assert.equal(report.outputFreeBytes, null);
assert.equal(summaryLine(report), "DubFlow đang dùng 14,2 GB · có thể dọn 9,8 GB");
assert.equal(parseReport({}), null);
assert.equal(parseReport(null), null);

// Busy or empty categories are not pre-selected and never counted.
const selection = defaultSelection(report);
assert.deepEqual([...selection].sort(), ["versions", "work"]);
assert.equal(cleanableBytes(report, new Set(["work"])), 1.8 * GB);
assert.equal(cleanableBytes(report, new Set(["link_cache"])), 0);
assert.equal(cleanButtonLabel(cleanableBytes(report, selection)), "Dọn 9,8 GB");
assert.equal(categorySize(report.categories[3]), "Đang tải link");
assert.equal(categorySize(report.categories[1]), "8,0 GB");
for (const id of ["work", "versions", "profiles", "link_cache"]) assert.ok(CATEGORY_TEXT[id].title && CATEGORY_TEXT[id].detail);
assert.match(CATEGORY_TEXT.versions.detail, /1 bản trước/);

const cleaned = parseClean({ freed_bytes: 2.3 * GB, skipped: ["Bản cài 1.1.18", 4] });
assert.deepEqual(cleaned, { freedBytes: 2.3 * GB, skipped: ["Bản cài 1.1.18"] });
assert.equal(resultLine(cleaned), "Đã giải phóng 2,3 GB");
assert.deepEqual(parseClean(null), { freedBytes: 0, skipped: [] });

// Automatic clean: on by default, 3 days, at once when the output drive is low.
assert.equal(AUTO_CLEAN_KEY, "dubflow.autoClean.v1");
assert.equal(autoCleanEnabled(null), true);
assert.equal(autoCleanEnabled("1"), true);
assert.equal(autoCleanEnabled("0"), false);
assert.equal(autoCleanDays(null), 3);
assert.equal(autoCleanDays(40 * GB), 3);
assert.equal(autoCleanDays(14.9 * GB), 0);
assert.deepEqual([...AUTO_CATEGORIES], ["work", "versions", "profiles"]);
assert.equal(AUTO_CLEAN_DELAY_MS, 60_000);
assert.ok(AFTER_JOB_DELAY_MS > 0 && AFTER_JOB_DELAY_MS < AUTO_CLEAN_DELAY_MS);
// Silent unless more than 1 GB was freed.
assert.equal(autoNotice(0.9 * GB), null);
assert.equal(autoNotice(GB), null);
assert.equal(autoNotice(2.3 * GB), "Đã tự dọn dẹp, giải phóng 2,3 GB");

// Output folders: finished jobs may be cleaned; every other job's folder is protected.
assert.equal(parentFolder("C:\\Videos\\DubFlow Output\\Clip - vi\\run-1\\final_vi.mp4"), "C:\\Videos\\DubFlow Output\\Clip - vi\\run-1");
assert.equal(parentFolder("D:/x/final_vi.mp4"), "D:/x");
const job = (state, outputPath, outputDir) => ({ status: { state }, outputPath, outputDir });
assert.deepEqual(queueOutputs([
  job("COMPLETED", "C:\\Out\\A - vi\\run-1\\final_vi.mp4"),
  job("COMPLETED", null, "C:\\Out\\B - vi\\link-2"),
  job("RUNNING", null, "C:\\Out\\C - vi\\run-3"),
  job("FAILED", null, "C:\\Out\\D - vi\\run-4"),
  job("QUEUED", null, undefined),
  // A re-run in the same folder as a finished job keeps that folder protected.
  job("RUNNING", null, "C:\\Out\\B - vi\\link-2"),
]), {
  finished: ["C:\\Out\\A - vi\\run-1"],
  running: ["C:\\Out\\C - vi\\run-3", "C:\\Out\\D - vi\\run-4", "C:\\Out\\B - vi\\link-2"],
});

console.log("storage tests passed");

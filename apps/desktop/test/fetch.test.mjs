import assert from "node:assert/strict";
import {
  addSample, formatRemaining, formatSpeed, fractionDone, megabytes, progressLine, remainingMs, speed, viNumber,
} from "../src/features/fetch/progress.ts";

const MB = 1_048_576;

// Vietnamese number format: comma decimals, dot thousands.
assert.equal(viNumber(3.14, 1), "3,1");
assert.equal(viNumber(1234), "1.234");
assert.equal(viNumber(1234567.5, 1), "1.234.567,5");
assert.equal(megabytes(120 * MB), "120");
assert.equal(megabytes(3.1 * MB), "3,1");
assert.equal(formatSpeed(3.1 * MB), "3,1 MB/s");
assert.equal(formatSpeed(12 * MB), "12 MB/s");
assert.equal(formatSpeed(50 * 1024), "50 KB/s");

// Speed over the last ~6 s of polls, not since the start.
let samples = [];
for (let second = 0; second <= 20; second++) {
  // 1 MB/s for 10 s, then 3 MB/s.
  const bytes = second <= 10 ? second * MB : 10 * MB + (second - 10) * 3 * MB;
  samples = addSample(samples, bytes, second * 1000);
}
assert.ok(samples.length <= 8, `history stays short: ${samples.length}`);
assert.equal(Math.round(speed(samples) / MB), 3);
assert.equal(speed(addSample([], 5 * MB, 0)), null, "one poll is not a speed");
assert.equal(speed([{ bytes: 0, atMs: 0 }, { bytes: MB, atMs: 500 }]), null, "too short to measure");

// A restarted download starts a new history; a repeated poll time replaces the sample.
assert.deepEqual(addSample([{ bytes: 10, atMs: 0 }, { bytes: 20, atMs: 1000 }], 5, 2000), [{ bytes: 5, atMs: 2000 }]);
assert.deepEqual(addSample([{ bytes: 10, atMs: 0 }], 30, 0), [{ bytes: 30, atMs: 0 }]);

// Time left.
assert.equal(remainingMs(120 * MB, 286 * MB, 3.1 * MB) > 53_000, true);
assert.equal(remainingMs(120 * MB, null, 3.1 * MB), null);
assert.equal(remainingMs(120 * MB, 286 * MB, 0), null);
assert.equal(formatRemaining(54_000), "còn 54 giây");
assert.equal(formatRemaining(89_000), "còn 89 giây");
assert.equal(formatRemaining(95_000), "còn 2 phút");
assert.equal(formatRemaining(5 * 60_000), "còn 5 phút");
assert.equal(formatRemaining(65 * 60_000), "còn 1 giờ 5 phút");
assert.equal(formatRemaining(120 * 60_000), "còn 2 giờ");
assert.equal(fractionDone(1, null), null);
assert.equal(fractionDone(2, 1), 1);

// The row line, with and without a known size.
const steady = [{ bytes: 101.4 * MB, atMs: 0 }, { bytes: 120 * MB, atMs: 6000 }];
assert.equal(progressLine(120 * MB, 286 * MB, steady), "41% · 120 / 286 MB · 3,1 MB/s · còn 54 giây");
assert.equal(progressLine(120 * MB, null, steady), "120 MB · 3,1 MB/s");
assert.equal(progressLine(120 * MB, 286 * MB, steady, true), "41% · 3,1 MB/s · còn 54 giây");
assert.equal(progressLine(120 * MB, 286 * MB, [{ bytes: 120 * MB, atMs: 0 }]), "41% · 120 / 286 MB");
assert.equal(progressLine(0, null, []), "0,0 MB");
console.log("desktop fetch progress tests passed");

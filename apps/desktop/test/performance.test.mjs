import assert from "node:assert/strict";
import { suggested, clamp, describeChoice, describeMachine, firstRun, hostThreads, readPerformance } from "../src/features/performance/model.ts";

const PC = { threads: 12, recommended: 10, memory_gb: 16, gpu: "NVIDIA GeForce RTX 3060 (12 GB)" };

// First run saves the machine's own suggestion as "Tự động".
assert.deepEqual(firstRun(PC), { mode: "auto", threads: 10 });
assert.equal(readPerformance(null), null);
assert.equal(readPerformance("{"), null);
assert.equal(readPerformance(JSON.stringify({ mode: "turbo", threads: 4 })), null);
assert.deepEqual(readPerformance(JSON.stringify({ mode: "custom", threads: 6 })), { mode: "custom", threads: 6 });
assert.deepEqual(readPerformance(JSON.stringify({ mode: "custom", threads: -3 })), { mode: "custom", threads: 1 });

// The host gets 0 for "all", the suggestion for "auto", and a clamped number for "custom".
assert.equal(hostThreads({ mode: "all", threads: 3 }, PC), 0);
assert.equal(hostThreads({ mode: "auto", threads: 3 }, PC), 10);
assert.equal(hostThreads({ mode: "custom", threads: 64 }, PC), 12);
assert.equal(hostThreads({ mode: "custom", threads: 4 }, null), 4);
assert.equal(hostThreads({ mode: "auto", threads: 4 }, null), 0);
assert.equal(clamp(0, PC), 1);
assert.equal(clamp(Number.NaN, PC), 1);

assert.equal(describeMachine(PC), "Máy này: 12 luồng CPU · 16 GB RAM · GPU NVIDIA GeForce RTX 3060 (12 GB)");
assert.equal(describeMachine({ threads: 4, recommended: 3, memory_gb: null, gpu: null }),
  "Máy này: 4 luồng CPU · không thấy GPU NVIDIA (render bằng CPU)");
assert.match(describeChoice({ mode: "auto", threads: 1 }, PC), /tối đa 10\/12 luồng/);
assert.match(describeChoice({ mode: "all", threads: 1 }, PC), /cả 12 luồng/);
assert.match(describeChoice({ mode: "custom", threads: 6 }, PC), /Tối đa 6\/12 luồng/);
assert.deepEqual([1, 4, 12, 24].map(suggested), [1, 3, 10, 22]);
console.log("performance settings ok");

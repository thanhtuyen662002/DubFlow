import assert from "node:assert/strict";
import { createOutputLocationController, createProcessingStarter } from "../src/features/output_location/controller.ts";

const row = (id) => ({ id, outputRoot: null, outputPath: null, status: { state: "QUEUED" } });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
function fixture() {
  let selected = row("first"), dialog = deferred(), calls = 0;
  const saved = [], errors = [], changes = [];
  const controller = createOutputLocationController({
    selected: () => selected,
    pick: () => { calls++; return dialog.promise; },
    save: (id, root) => { saved.push([id, root]); selected = { ...selected, outputRoot: root }; },
    changed: () => changes.push(controller.busy),
    error: (message) => errors.push(message),
  });
  return { controller, saved, errors, changes, get selected() { return selected; }, set selected(v) { selected = v; }, get dialog() { return dialog; }, get calls() { return calls; } };
}

{
  const f = fixture(), pending = f.controller.choose();
  assert.equal(f.controller.busy, true);
  await f.controller.choose();
  assert.equal(f.calls, 1);
  f.dialog.resolve("D:/Kết quả có dấu"); await pending;
  assert.deepEqual(f.saved, [["first", "D:/Kết quả có dấu"]]);
  assert.deepEqual(f.changes, [true, false]);
  f.controller.reset(); assert.equal(f.selected.outputRoot, null);
}
{
  const f = fixture(), pending = f.controller.choose();
  f.dialog.resolve(null); await pending;
  assert.deepEqual(f.saved, []); assert.equal(f.controller.busy, false);
}
for (const changed of [row("second"), { ...row("first"), status: { state: "RUNNING" } },
  { ...row("first"), status: { state: "COMPLETED" }, outputPath: "D:/old/final.mp4" },
  { ...row("first"), outputRoot: "D:/already changed" }, undefined]) {
  const f = fixture(), pending = f.controller.choose();
  f.selected = changed;
  f.dialog.resolve("D:/late"); await pending;
  assert.deepEqual(f.saved, []); assert.equal(f.controller.busy, false);
}
{
  const f = fixture(), pending = f.controller.choose();
  f.dialog.reject(new Error("dialog refused")); await pending;
  assert.equal(f.errors.length, 1); assert.equal(f.controller.busy, false); assert.deepEqual(f.saved, []);
}
{
  const f = fixture(); f.selected.status.state = "COMPLETED";
  await f.controller.choose(); f.controller.reset();
  assert.equal(f.calls, 0); assert.deepEqual(f.saved, []);
}
console.log("Output location: choice/cancel/one-dialog/late-row-start-removal-root-change/error/terminal/reset PASS");

function startFixture() {
  let selected = { ...row("first"), sourcePath: "D:/source.mp4", dubbing: { enabled: true, voiceId: "vi-truc-ly-vieneu3-v1" } };
  const admission = deferred(), execution = deferred(), errors = [], executed = [];
  let admits = 0;
  const starter = createProcessingStarter({
    selected: () => selected,
    ready: (job) => job.status.state === "QUEUED",
    admit: () => { admits++; return admission.promise; },
    execute: (job) => { executed.push(structuredClone(job)); selected.status.state = "RUNNING"; return execution.promise; },
    rejected: (jobId, error, dispatched) => { errors.push({ jobId, error: String(error), dispatched }); },
    changed: () => {},
  });
  return { starter, admission, execution, errors, executed, get admits() { return admits; }, get selected() { return selected; }, set selected(v) { selected = v; } };
}
{
  const f = startFixture(), before = structuredClone(f.selected), pending = f.starter.start();
  await f.starter.start(); assert.equal(f.admits, 1);
  f.admission.reject(new Error("output path too long")); await pending;
  assert.deepEqual(f.selected, before); assert.deepEqual(f.executed, []);
  assert.equal(f.errors[0].dispatched, false); assert.equal(f.starter.busy, false);
}
{
  const f = startFixture(), pending = f.starter.start();
  f.admission.resolve("D:/short/output"); await Promise.resolve();
  assert.equal(f.executed.length, 1); assert.equal(f.starter.busy, true);
  f.execution.resolve(); await pending; assert.equal(f.starter.busy, false);
}
for (const change of [
  (f) => { f.selected.outputRoot = "D:/changed"; },
  (f) => { f.selected.dubbing.voiceId = "other"; },
  (f) => { f.selected.status.state = "COMPLETED"; },
  (f) => { f.selected.id = "second"; },
  (f) => { f.selected = undefined; },
]) {
  const f = startFixture(), pending = f.starter.start(); change(f);
  f.admission.resolve(); await pending;
  assert.deepEqual(f.executed, []); assert.equal(f.starter.busy, false);
}
{
  const f = startFixture(), pending = f.starter.start();
  f.admission.resolve(); await Promise.resolve(); f.execution.reject(new Error("spawn refused"));
  await pending; assert.equal(f.errors[0].dispatched, true); assert.equal(f.starter.busy, false);
}
console.log("Output preflight: QUEUED preserved/refusal, duplicate start, valid start, late changed root/voice/state/row/removal, post-dispatch error PASS");

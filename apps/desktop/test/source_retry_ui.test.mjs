import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";

// Execute only the actual main.ts AST nodes. App startup/Tauri/intervals are not evaluated.
const mainUrl = process.env.DUBFLOW_SOURCE_UI_MAIN
  ? pathToFileURL(process.env.DUBFLOW_SOURCE_UI_MAIN) : new URL("../src/main.ts", import.meta.url);
const main = await readFile(mainUrl, "utf8");
const mainSha = createHash("sha256").update(main).digest("hex");
if (process.env.DUBFLOW_SOURCE_UI_MAIN_SHA256) assert.equal(mainSha, process.env.DUBFLOW_SOURCE_UI_MAIN_SHA256);
const require = createRequire(import.meta.url);
const compilerPath = process.env.DUBFLOW_SOURCE_UI_TS || require.resolve("typescript");
const ts = require(compilerPath);
const ast = ts.createSourceFile("main.ts", main, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const selected = ["$", "sourcePhases", "sourceProblem", "renderSource"].map(name => {
  const found = ast.statements.filter(node => ts.isFunctionDeclaration(node) && node.name?.text === name ||
    ts.isVariableStatement(node) && node.declarationList.declarations.some(d => ts.isIdentifier(d.name) && d.name.text === name));
  assert.equal(found.length, 1, "actual main.ts must have exactly one " + name);
  return { name, source: found[0].getText(ast) };
});
const compiled = ts.transpileModule(selected.map(node => node.source).join("\n"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None }, reportDiagnostics: true,
});
assert(!compiled.diagnostics?.some(d => d.category === ts.DiagnosticCategory.Error));
const { createSourceServiceController } = await import(new URL("./features/source_intake/service_controller.ts", mainUrl));
const { QueueController } = await import(new URL("./features/queue/model.ts", mainUrl));
const { cancellationMessage, inputProblemMessage } = await import(new URL("./features/source_intake/ui_messages.ts", mainUrl));
const bindActualRender = new Function("document", "sourceController", "sourceList", "sourceFeedback", "sourceStop",
  "cancellationMessage", "inputProblemMessage", compiled.outputText + "\nreturn renderSource;");
console.log(JSON.stringify({ actual_main: fileURLToPath(mainUrl), main_sha256: mainSha, compiler_version: ts.version,
  compiled_sha256: createHash("sha256").update(compiled.outputText).digest("hex"),
  nodes: selected.map(node => ({ name: node.name, sha256: createHash("sha256").update(node.source).digest("hex") })) }));

// Tiny deterministic DOM with real EventTarget dispatch. No renderer/callback mirror.
class Element extends EventTarget {
  constructor(tag, document) { super(); this.tagName = tag.toUpperCase(); this.ownerDocument = document;
    this.children = []; this.parentElement = null; this.disabled = false; this.ownText = ""; }
  set textContent(value) { this.replaceChildren(); this.ownText = String(value); }
  get textContent() { return this.ownText + this.children.map(node => node.textContent).join(""); }
  set innerHTML(_value) { throw new Error("HTML sink forbidden in UI test"); }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
  replaceChildren(...nodes) { for (const node of this.children) node.parentElement = null;
    this.children = []; this.ownText = ""; this.append(...nodes); }
  get isConnected() { return this === this.ownerDocument.root || !!this.parentElement?.isConnected; }
  click() { if (!this.disabled) this.dispatchEvent(new Event("click", { cancelable: true })); }
}
const descendants = node => [node, ...node.children.flatMap(descendants)];
const RETRY = "Thử thêm vào hàng xử lý", FORGET = "Bỏ kết quả này";
const mediaPath = "D:/Dữ liệu UI/Cảnh, 1.mp4", scan = "source-ui-1", identity = "generic:ui,1";
const settle = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const nodes = new Map();
  const document = { createElement(tag) { return new Element(tag, this); }, querySelector(selector) { return nodes.get(selector) ?? null; } };
  document.root = document.createElement("main");
  for (const id of ["source-service-status", "source-rejections", "source-list", "source-feedback", "source-stop"]) {
    const element = document.createElement(id === "source-stop" ? "button" : "div");
    nodes.set("#" + id, element); document.root.append(element);
  }
  const list = nodes.get("#source-list"), feedback = nodes.get("#source-feedback");
  let durable = null, writable = false, writes = 0, nextId = 0, sequence = 0, offline = false;
  const events = [], nativeCalls = [], retryCalls = [], cancelCalls = [];
  const storage = { read: () => durable, write(value) { writes++;
    if (!writable) throw new Error("CONTROLLED_STORAGE_REJECTED: private diagnostic"); durable = value; } };
  const queue = new QueueController(storage, () => "2026-10-21T00:00:00.000Z", 100, () => "ui-retry-" + ++nextId);
  const controller = createSourceServiceController(queue, async (command, args) => {
    nativeCalls.push({ command, args });
    if (offline) throw new Error("controlled source host unavailable");
    if (command === "source_service_events_v1") return events.splice(0);
    if (command === "source_service_send_v1") return { delivery: "submitted" };
    if (command === "source_service_start_v1" || command === "source_service_state_v1") return { phase: "ready", failure: null, retired: false };
    if (command === "source_service_stop_v1") return { phase: "stopping", failure: null, retired: false };
    assert.fail("unexpected native callback " + command);
  }, { scanId: () => scan });
  let onRetry;
  const observed = { ...controller,
    retryLocalQueue(...args) { retryCalls.push(args); onRetry?.(); return controller.retryLocalQueue(...args); },
    cancel(...args) { cancelCalls.push(args); return controller.cancel(...args); },
  };
  const render = bindActualRender(document, observed, list, feedback, nodes.get("#source-stop"), cancellationMessage, inputProblemMessage);
  const unsubscribe = controller.subscribe(render);
  const h = { document, list, feedback, queue, controller, nativeCalls, retryCalls, cancelCalls, render,
    writes: () => writes, durable: () => durable, setWritable(value) { writable = value; },
    onRetry(callback) { onRetry = callback; },
    button(label) { const found = descendants(list).filter(node => node.tagName === "BUTTON" && node.textContent === label);
      assert.equal(found.length, 1, "expected one actual mounted " + label); return found[0]; },
    push(value) { events.push({ sequence: ++sequence, value }); },
    async failAdmission() {
      await controller.submit("https://example.test/ui,1");
      h.push({ event: "source_status", scan_id: scan, status: "completed", dispatch_revision: 2, enumeration_retired: false });
      await controller.poll();
      h.push({ event: "source_items", scan_id: scan, offset: 0, total: 1, items: [{ identity_key: identity, status: "discovered" }] });
      h.push({ event: "source_status", scan_id: scan, status: "completed", dispatch_revision: 2, enumeration_retired: true });
      await controller.poll();
      assert.equal(await controller.download(scan, identity), true);
      h.push({ event: "source_downloaded", scan_id: scan, identity_key: identity, media_path: mediaPath, sha256: "a".repeat(64), size_bytes: 1621 });
      await controller.poll();
      assert.equal(events.length, 0); assert.equal(controller.getState().rows[0].queue_retry_available, true);
      assert.equal(queue.getSnapshot().jobs.length, 0); assert.equal(durable, null); assert.equal(writes, 1);
    },
    async failHost() { offline = true; await controller.poll(); },
    async cleanup() { unsubscribe(); if (!controller.getState().disposed) await controller.dispose(); },
  };
  return h;
}

test("source retry UI: unavailable host still admits retained receipt once with only its scan id", async t => {
  const h = fixture(); t.after(() => h.cleanup()); await h.failAdmission(); await h.failHost();
  const actual = h.controller.getState(); assert.equal(actual.failure, "SOURCE_IPC_FAILED");
  // Exercise presentation's unavailable branch; actual receipt authority stays in the real controller.
  h.render({ ...actual, service: "unavailable" });
  const retry = h.button(RETRY); assert.equal(retry.disabled, false);
  h.setWritable(true); const calls = h.nativeCalls.length; retry.click();
  assert.deepEqual(h.retryCalls, [[scan]], "no path, identity, event or request object from the UI");
  assert.equal(h.nativeCalls.length, calls); assert.equal(h.writes(), 2);
  assert.equal(h.queue.getSnapshot().jobs.length, 1); assert.equal(h.queue.getSnapshot().jobs[0].sourcePath, mediaPath);
  assert.equal(h.feedback.textContent, "Đã thêm 1 video; 0 video đã có trong hàng xử lý.");
  assert.equal(h.controller.getState().rows[0].local_queue, "added");
  assert.equal(h.controller.getState().failure, "SOURCE_IPC_FAILED");
  assert(!descendants(h.list).some(node => node.textContent === RETRY));
});

test("source retry UI: button disables before synchronous reentry and renders actual rejection then recovery", async t => {
  const h = fixture(); t.after(() => h.cleanup()); await h.failAdmission();
  const retry = h.button(RETRY); let reenter = true;
  h.onRetry(() => { assert.equal(retry.disabled, true); if (reenter) { reenter = false; retry.dispatchEvent(new Event("click")); } });
  const calls = h.nativeCalls.length; retry.click(); h.onRetry(null);
  assert.equal(h.retryCalls.length, 1); assert.equal(h.writes(), 2);
  assert.equal(h.queue.getSnapshot().jobs.length, 0); assert.equal(h.durable(), null);
  assert.equal(h.feedback.textContent, "Chưa thêm được video vào hàng xử lý. Hãy kiểm tra danh sách và dung lượng trống.");
  assert(!h.document.root.textContent.includes("private diagnostic"));
  const fresh = h.button(RETRY); assert.notEqual(fresh, retry); assert.equal(fresh.disabled, false);
  assert.equal(h.writes(), 2, "rerender does not retry storage");
  h.setWritable(true); fresh.click();
  assert.deepEqual(h.retryCalls, [[scan], [scan]]); assert.equal(h.writes(), 3);
  assert.equal(h.feedback.textContent, "Đã thêm 1 video; 0 video đã có trong hàng xử lý.");
  assert.equal(h.nativeCalls.length, calls);
});

test("source retry UI: feedback reports present from actual queue dedup without another write", async t => {
  const h = fixture(); t.after(() => h.cleanup()); await h.failAdmission(); h.setWritable(true);
  assert.equal(h.queue.addPaths([mediaPath]).length, 1);
  const writes = h.writes(), before = h.queue.getSnapshot(), calls = h.nativeCalls.length;
  h.button(RETRY).click();
  assert.deepEqual(h.retryCalls, [[scan]]); assert.equal(h.writes(), writes);
  assert.deepEqual(h.queue.getSnapshot(), before);
  assert.equal(h.controller.getState().rows[0].local_queue, "present");
  assert.equal(h.feedback.textContent, "Đã thêm 0 video; 1 video đã có trong hàng xử lý.");
  assert.equal(h.nativeCalls.length, calls);
});

test("source retry UI: forget calls same-scan local cancel and detached stale retry cannot admit", async t => {
  const h = fixture(); t.after(() => h.cleanup()); await h.failAdmission();
  const oldRetry = h.button(RETRY), forget = h.button(FORGET), calls = h.nativeCalls.length;
  forget.click(); await settle();
  assert.deepEqual(h.cancelCalls, [[scan]]); assert.equal(h.nativeCalls.length, calls);
  assert.equal(h.controller.getState().rows[0].phase, "cancelled");
  assert.equal(h.controller.getState().rows[0].queue_retry_available, false); assert.equal(oldRetry.isConnected, false);
  h.setWritable(true); oldRetry.dispatchEvent(new Event("click"));
  assert.deepEqual(h.retryCalls, [[scan]]); assert.equal(h.queue.getSnapshot().jobs.length, 0);
  assert.equal(h.writes(), 1); assert.equal(h.nativeCalls.length, calls);
  assert.equal(h.feedback.textContent, "Kết quả này không còn sẵn để thêm lại. Hãy kiểm tra hàng xử lý.");
});

test("source retry UI: disposed controller rejects old callback and stale disposed presentation disables button", async t => {
  const h = fixture(); t.after(() => h.cleanup()); await h.failAdmission();
  const before = h.controller.getState(), old = h.button(RETRY);
  await h.controller.dispose(); h.setWritable(true); const calls = h.nativeCalls.length;
  old.dispatchEvent(new Event("click"));
  assert.deepEqual(h.retryCalls, [[scan]]); assert.equal(h.writes(), 1);
  assert.equal(h.queue.getSnapshot().jobs.length, 0); assert.equal(h.nativeCalls.length, calls);
  // Deliberately stale row snapshot plus disposed flag: UI cannot grant controller authority.
  h.render({ ...before, disposed: true });
  const disabled = h.button(RETRY); assert.equal(disabled.disabled, true);
  disabled.click(); disabled.dispatchEvent(new Event("click"));
  assert.equal(h.retryCalls.length, 1); assert.equal(h.writes(), 1);
  assert.equal(h.queue.getSnapshot().jobs.length, 0); assert.equal(h.nativeCalls.length, calls);
  assert.equal(h.button(FORGET).disabled, true);
});

import assert from "node:assert/strict";
import test from "node:test";
import { mountReviewCenter } from "../../apps/desktop/src/features/review/view.ts";
import { ReviewConnection } from "../../apps/desktop/src/features/review/connection.ts";
import { nativeReviewTransport } from "../../apps/desktop/src/features/review/transport.ts";
import { ReviewFailureError } from "../../apps/desktop/src/features/review/request-failure.ts";

// Deterministic tiny DOM adapted from review.test.mjs. No old test module/Python producer is imported.
// Unsupported selectors and HTML sinks refuse instead of silently simulating a successful render.
class ReviewElement extends EventTarget {
  constructor(tag, document) {
    super(); this.tagName = tag.toUpperCase(); this.ownerDocument = document;
    this.children = []; this.parentElement = null; this.attributes = new Map();
    this.dataset = {}; this.style = {}; this.className = ""; this.disabled = false;
    this.value = ""; this.ownText = "";
  }
  set textContent(value) { this.replaceChildren(); this.ownText = String(value); }
  get textContent() { return this.ownText + this.children.map(node => node.textContent).join(""); }
  set innerHTML(_value) { this.ownerDocument.htmlWrites++; throw new Error("HTML sink forbidden in Review failure details"); }
  insertAdjacentHTML() { this.ownerDocument.htmlWrites++; throw new Error("HTML insertion forbidden"); }
  append(...nodes) { for (const node of nodes) { node.parentElement = this; this.children.push(node); } }
  replaceChildren(...nodes) {
    for (const node of this.children) node.parentElement = null;
    this.children = []; this.ownText = ""; this.append(...nodes);
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  get isConnected() { return this === this.ownerDocument.host || !!this.parentElement?.isConnected; }
  focus() { this.ownerDocument.activeElement = this; }
  click() {
    if (this.disabled) return;
    const proceed = this.dispatchEvent(new Event("click", { cancelable: true }));
    if (proceed && this.tagName === "BUTTON" && this.type === "submit") {
      let form = this.parentElement;
      while (form && form.tagName !== "FORM") form = form.parentElement;
      form?.dispatchEvent(new Event("submit", { cancelable: true }));
    }
  }
  querySelectorAll(selector) {
    const all = nodes(this).slice(1);
    if (selector === "button") return all.filter(node => node.tagName === "BUTTON");
    if (selector === "button[data-review-navigation]") return all.filter(node => node.tagName === "BUTTON" && "reviewNavigation" in node.dataset);
    if (selector === "[data-review-activity]") return all.filter(node => "reviewActivity" in node.dataset);
    throw new Error("unsupported test DOM selector: " + selector);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
}
const nodes = root => [root, ...root.children.flatMap(nodes)];
function container() {
  const document = { htmlWrites: 0, createElement(tag) { return new ReviewElement(tag, this); } };
  document.host = document.createElement("div"); return document.host;
}
const button = (root, label) => nodes(root).find(node => node.tagName === "BUTTON" && node.textContent === label);
const statusText = root => nodes(root).find(node => node.className === "review-status").textContent;
const detailsPre = root => nodes(root).find(node => node.tagName === "PRE" && node.parentElement?.tagName === "DETAILS");
const settle = () => new Promise(resolve => setImmediate(resolve));
async function until(predicate, explanation) {
  for (let attempt = 0; attempt < 30; attempt++) { if (predicate()) return; await settle(); }
  assert.fail(explanation);
}

function snapshot(label) {
  const job = "job-failure-v2-view-" + label;
  return {
    schema_version: 3, kind: "review_current_snapshot", job_id: job, mode: "b2",
    current_token: { job_id: job, revision: "0", snapshot_sha256: "sha256:" + "a".repeat(64),
      graph_sha256: "sha256:" + "b".repeat(64), original_manifest_sha256: "sha256:" + "c".repeat(64) },
    time_base: { numerator: "1", denominator: "1000" },
    findings: [{ finding_id: "finding-v2-view", reason: "TTS_FAILURE", cue_id: "cue-v2-view",
      range: { start_ticks: "0", end_ticks: "2000" }, source_text: "Lời nguồn",
      translated_text: "Bản dịch hiện hành", speaker: "speaker-1", voice: "voice-a", output_preview: null,
      confidence: null, confidence_source: null, warning: null, affected_stages: ["tts", "mix", "render"] }],
    last_plan: null, correction_ready: false,
  };
}
function planResponse(current, request) {
  const stages = ["translation", "subtitles", "tts", "mix", "render", "qc", "export"];
  return { schema_version: 1, kind: "review_current_correction_plan", ...structuredClone(request), mode: "b2",
    plan: { finding_id: request.finding_id, stages_to_rerun: stages, preserved_artifacts: ["asr", "ocr"],
      replacement_artifacts: stages.map(stage => "replacement:" + stage), retain_previous_until_validated: true,
      requires_full_manual_review: false },
    candidate_revision: String(BigInt(current.current_token.revision) + 1n), candidate_snapshot_sha256: "sha256:" + "8".repeat(64),
    read_only: true, processing_release_admitted: false, active_graph_published: false, execution_ready: false };
}
function failurePacket(id, job, diagnostic, overrides) {
  return { schema_version: 2, kind: "review_desktop_request_failure", request_id: id, job_id: job,
    operation: "apply", outcome: "unconfirmed", phase: "processing", native_stage: "rendering_video",
    message: "Không xác nhận được kết quả; hãy tải lại", diagnostic, ...overrides };
}
function harness(label, diagnostic, overrides = {}) {
  const stored = snapshot(label), before = structuredClone(stored), calls = [], errors = [];
  let sequence = 0, rejectPending = null, emitted = null, view;
  const transport = nativeReviewTransport(async (command, args) => {
    calls.push({ command, args: structuredClone(args) });
    if (command === "review_snapshot_v2") {
      assert.equal(args.jobId, stored.job_id);
      return { schema_version: 2, kind: "review_desktop_current_session", session_id: args.requestId, snapshot: structuredClone(stored) };
    }
    if (command === "review_plan_v1") return planResponse(stored, args.request);
    if (command === "review_apply_v1") {
      emitted = failurePacket(args.requestId, stored.job_id, diagnostic, overrides);
      return new Promise((_resolve, reject) => { rejectPending = () => { rejectPending = null; reject(emitted); }; });
    }
    if (command === "review_cancel_v1") return;
    if (command === "review_request_stage_v1") throw new Error("controlled observation unavailable");
    assert.fail("unexpected native callback: " + command);
  }, async () => assert.fail("failure view must not start playback"), () => "request-v2-view-" + label + "-" + ++sequence);
  const connection = new ReviewConnection(transport), host = container();
  function mount(current) {
    const actual = connection.actions([{ voice_id: "voice-a", label: "Giọng A" }]);
    const observed = { ...actual, applyCurrent: async (...args) => {
      try { return await actual.applyCurrent(...args); } catch (error) { errors.push(error); throw error; }
    } };
    view = mountReviewCenter(host, current, observed);
  }
  return { host, stored, before, calls, errors, connection,
    get view() { return view; }, get emitted() { return emitted; },
    mutationCount: () => calls.filter(call => call.command === "review_apply_v1").length,
    async load() { const current = await connection.load(stored.job_id); assert(current); mount(current); },
    reject() { assert(rejectPending, "actual apply callback must be pending"); rejectPending(); },
    async reload() { view.destroy(); const current = await connection.load(stored.job_id); assert(current); mount(current); },
    cleanup() { view?.destroy(); connection.dispose(); if (rejectPending) rejectPending(); },
  };
}
async function startOneMountedApply(h) {
  const draft = nodes(h.host).find(node => node.tagName === "TEXTAREA");
  assert(draft); draft.value = "Bản dịch mới cần kiểm tra";
  draft.dispatchEvent(new Event("input"));
  const inspect = button(h.host, "Xem phạm vi chạy lại"); assert(inspect && !inspect.disabled); inspect.click();
  await until(() => button(h.host, "Xác nhận và dựng lại câu này")?.disabled === false, "real plan path did not enable the submit button");
  const submit = button(h.host, "Xác nhận và dựng lại câu này"), form = submit.parentElement;
  assert.equal(form.tagName, "FORM"); submit.click();
  await until(() => h.mutationCount() === 1, "mounted submit did not reach real transport apply callback");
  // Both a second click and a synthetic second submit must stop at the real busy guard.
  submit.click(); form.dispatchEvent(new Event("submit", { cancelable: true })); await settle();
  assert.equal(h.mutationCount(), 1);
  return { submit, form };
}
async function failedView(h) {
  await until(() => h.connection.snapshot() === null && /Hãy tải lại lời thoại/.test(statusText(h.host)) &&
    nodes(h.host).find(node => node.className === "review-center")?.getAttribute("aria-busy") === "false",
  "mounted failure did not become unconfirmed/stale/reload-required");
}
function assertInertDetails(h, diagnostic) {
  const pre = detailsPre(h.host); assert(pre, "technical details must be mounted");
  assert(pre.textContent.endsWith(diagnostic));
  assert.match(pre.textContent, /Bước xử lý: Xử lý yêu cầu/);
  assert.match(pre.textContent, /Công đoạn: Dựng video/);
  assert(!pre.textContent.includes("rendering_video"));
  assert.equal(pre.children.length, 0, "diagnostic must be one textContent value, not parsed children");
  assert.equal(pre.style.whiteSpace, "pre-wrap"); assert.equal(pre.style.overflowWrap, "anywhere");
  assert.equal(h.host.ownerDocument.htmlWrites, 0);
  assert(!nodes(h.host).some(node => ["IMG", "SCRIPT", "IFRAME", "A"].includes(node.tagName)));
}
function assertUnconfirmed(h) {
  assert.match(statusText(h.host), /Chưa xác định.*đã áp dụng hay chưa/);
  assert.match(statusText(h.host), /Hãy tải lại lời thoại/);
  assert.match(statusText(h.host), /không tự gửi lại/);
  assert.doesNotMatch(statusText(h.host), /Đã kiểm tra và áp dụng bản thay thế/);
  assert.equal(h.connection.snapshot(), null);
  assert.throws(() => h.connection.actions(), /has not loaded/);
  assert.deepEqual(h.stored, h.before);
  assert.equal(h.stored.current_token.revision, "0");
  assert.equal(h.mutationCount(), 1);
}

// Worst-case native byte escaping, not a shortened preview or UTF-8 replacement fixture.
const worstDiagnostic = 'REVIEW_ADAPTER_REFUSED: status=exit code: 3; stderr_bytes=4096; stderr_ascii="' + "\\xff".repeat(4096) + '"';

test("mounted v2 retains every escaped native byte, fixed stage and one unconfirmed action", async t => {
  assert(new TextEncoder().encode(worstDiagnostic).length > 16 * 1024);
  const h = harness("worst", worstDiagnostic); t.after(() => h.cleanup());
  await h.load(); const prior = await startOneMountedApply(h); h.reject(); await failedView(h);
  assertInertDetails(h, worstDiagnostic); assertUnconfirmed(h);
  const pre = detailsPre(h.host);
  assert.equal(pre.textContent.slice(-worstDiagnostic.length), worstDiagnostic);
  assert.equal(pre.textContent.match(/\\xff/g).length, 4096);
  assert(h.errors[0] instanceof ReviewFailureError);
  assert.equal(h.errors[0].failure.diagnostic, worstDiagnostic);
  assert.equal(h.errors[0].failure.outcome, "unconfirmed");
  assert.equal(h.errors[0].failure.schema_version, 2);
  assert.equal(Object.keys(h.errors[0].failure).sort().join(","),
    "diagnostic,job_id,kind,message,native_stage,operation,outcome,phase,request_id,schema_version");
  prior.form.dispatchEvent(new Event("submit", { cancelable: true }));
  prior.submit.click(); await settle(); assert.equal(h.mutationCount(), 1);
  await h.reload();
  const loads = h.calls.filter(call => call.command === "review_snapshot_v2");
  assert.equal(loads.length, 2); assert.notEqual(loads[0].args.requestId, loads[1].args.requestId);
  assert.deepEqual(h.connection.snapshot(), h.before);
  assert.equal(detailsPre(h.host), undefined); assert.equal(h.mutationCount(), 1);
});

test("mounted v2 diagnostic markup stays textContent and never gains executable or commit authority", async t => {
  const diagnostic = '<img src=x onerror=globalThis.__reviewV2Injected=1><script>globalThis.__reviewV2Injected=2</script>\nfile:///C:/private\t\\xff';
  const old = globalThis.__reviewV2Injected; globalThis.__reviewV2Injected = 0;
  const h = harness("markup", diagnostic); t.after(() => { h.cleanup(); old === undefined ? delete globalThis.__reviewV2Injected : globalThis.__reviewV2Injected = old; });
  await h.load(); await startOneMountedApply(h); h.reject(); await failedView(h);
  assertInertDetails(h, diagnostic); assertUnconfirmed(h);
  assert(detailsPre(h.host).textContent.includes("<img src=x"));
  assert.equal(globalThis.__reviewV2Injected, 0);
  for (const key of ["retryable", "committed", "success", "token", "root", "snapshot"]) assert(!(key in h.errors[0].failure));
});

for (const [label, overrides] of [
  ["wrong-id", { request_id: "request-v2-foreign-binding-0001" }],
  ["wrong-job", { job_id: "foreign-job-v2" }],
  ["wrong-op", { operation: "undo" }],
]) test("mounted v2 hides technical details for " + label, async t => {
  const h = harness(label, worstDiagnostic, overrides); t.after(() => h.cleanup());
  await h.load(); await startOneMountedApply(h); h.reject(); await failedView(h);
  assertUnconfirmed(h);
  assert.equal(detailsPre(h.host), undefined);
  assert(!h.host.textContent.includes("stderr_ascii="));
  assert(!h.host.textContent.includes("Công đoạn:"));
  assert(!(h.errors[0] instanceof ReviewFailureError));
  assert.equal(h.host.ownerDocument.htmlWrites, 0);
});

test("late same-job v2 failure cannot leak details into a freshly loaded mounted session", async t => {
  const h = harness("stale", worstDiagnostic); t.after(() => h.cleanup());
  await h.load(); await startOneMountedApply(h);
  const fresh = await h.connection.load(h.stored.job_id);
  assert(fresh); h.view.replace(fresh);
  const freshText = h.host.textContent;
  h.reject(); await until(() => h.errors.length === 1, "late callback did not settle");
  await settle(); await settle();
  assert.deepEqual(h.connection.snapshot(), fresh);
  assert.equal(h.host.textContent, freshText);
  assert.equal(detailsPre(h.host), undefined);
  assert(!h.host.textContent.includes("stderr_ascii="));
  const loads = h.calls.filter(call => call.command === "review_snapshot_v2");
  assert.equal(loads.length, 2); assert.notEqual(loads[0].args.requestId, loads[1].args.requestId);
  assert.equal(h.mutationCount(), 1); assert.deepEqual(h.stored, h.before);
});

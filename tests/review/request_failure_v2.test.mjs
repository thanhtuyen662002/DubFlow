import assert from "node:assert/strict";
import test from "node:test";
import { readReviewFailure, boundReviewFailure, reviewFailureDetails, ReviewFailureError } from "../../apps/desktop/src/features/review/request-failure.ts";

const id = "request-failure-v2-regression-0001", job = "review-b2-job";
const packet = (version = 2, overrides = {}) => ({
  schema_version: version, kind: "review_desktop_request_failure", request_id: id, job_id: job,
  operation: "apply", outcome: "unconfirmed", phase: "processing", native_stage: "synthesizing_cue",
  message: "Không xác nhận được kết quả; hãy tải lại", diagnostic: "adapter refused", ...overrides,
});
const read = value => readReviewFailure(value, id, job, "apply");

test("v1 keeps its exact 4096-byte diagnostic boundary", () => {
  assert.equal(read(packet(1, { diagnostic: "a".repeat(4096) })).diagnostic.length, 4096);
  assert.throws(() => read(packet(1, { diagnostic: "a".repeat(4097) })));
  assert.throws(() => read(packet(1, { diagnostic: "漢".repeat(1366) })));
});

test("v2 retains the complete escaped diagnostic for all 4096 native stderr bytes", () => {
  // Independent worst-case native escape_ascii expansion: each byte 0xff becomes four ASCII bytes.
  const diagnostic = 'REVIEW_ADAPTER_REFUSED: status=exit code: 2; stderr_bytes=4096; stderr_ascii="' + "\\xff".repeat(4096) + '"';
  assert(new TextEncoder().encode(diagnostic).length > 16 * 1024);
  const failure = read(packet(2, { diagnostic }));
  assert.equal(failure.diagnostic, diagnostic);
  assert.equal(failure.outcome, "unconfirmed");
  const details = reviewFailureDetails(boundReviewFailure(failure, id, job, "apply"), job, "apply");
  assert(details.endsWith(diagnostic));
});

test("v2 uses a 32768-byte UTF8 cap without widening the message cap", () => {
  assert.equal(read(packet(2, { diagnostic: "a".repeat(32768) })).diagnostic.length, 32768);
  assert.equal(read(packet(2, { diagnostic: "漢".repeat(10922) + "aa" })).diagnostic.length, 10924);
  for (const change of [{ diagnostic: "a".repeat(32769) }, { diagnostic: "漢".repeat(10923) }, { message: "a".repeat(1025) }]) {
    assert.throws(() => read(packet(2, change)));
  }
});

test("native stage is visible as fixed Vietnamese text, with no stage when unavailable", () => {
  const error = boundReviewFailure(packet(), id, job, "apply");
  assert(error instanceof ReviewFailureError);
  assert.match(reviewFailureDetails(error, job, "apply"), /Công đoạn: Tạo giọng cho câu thoại/);
  const unavailable = boundReviewFailure(packet(1, { native_stage: null, diagnostic: null }), id, job, "apply");
  assert(!reviewFailureDetails(unavailable, job, "apply").includes("Công đoạn:"));
});

test("both versions remain closed, bound to the invocation, and unconfirmed", () => {
  for (const version of [1, 2]) {
    for (const change of [{ schema_version: 3 }, { schema_version: "2" }, { request_id: "request-other-00000000" },
      { job_id: "other-job" }, { operation: "undo" }, { outcome: "not_committed" }, { success: true }, { retryable: true },
      { root: "C:/substitute" }, { phase: "ended" }, { native_stage: "committed_successfully" }, { diagnostic: "" },
      { diagnostic: "bad\u0000text" }, { diagnostic: "bad\u0085text" }, { diagnostic: "bad\ud800text" }]) {
      assert.throws(() => read(packet(version, change)));
    }
  }
});

test("accepted packets are cloned, and stale or mismatched details stay unavailable", () => {
  const raw = packet(), error = boundReviewFailure(raw, id, job, "apply");
  raw.diagnostic = "changed after binding";
  assert.equal(error.failure.diagnostic, "adapter refused");
  assert.equal(reviewFailureDetails(error, "other-job", "apply"), null);
  assert.equal(reviewFailureDetails(error, job, "undo"), null);
  assert.equal(boundReviewFailure("legacy error", id, job, "apply"), "legacy error");
});

test("diagnostic markup stays complete inert text with no new authority fields", () => {
  const diagnostic = '<img src=x onerror=alert(1)>\nfile:///C:/private\t\\xff';
  const error = boundReviewFailure(packet(2, { diagnostic }), id, job, "apply");
  assert(reviewFailureDetails(error, job, "apply").endsWith(diagnostic));
  assert.equal(Object.keys(error.failure).sort().join(","), "diagnostic,job_id,kind,message,native_stage,operation,outcome,phase,request_id,schema_version");
  assert.equal(error.failure.outcome, "unconfirmed");
});

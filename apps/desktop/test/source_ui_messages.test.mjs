import assert from "node:assert/strict";
import test from "node:test";
import { cancellationMessage, inputProblemMessage } from "../src/features/source_intake/ui_messages.ts";

test("pending or uncertain cancellation never claims pipe delivery or confirmed cancellation", () => {
  for (const delivery of ["pending", "unknown", null]) {
    const message = cancellationMessage(delivery);
    assert.doesNotMatch(message, /Đã gửi|Đã hủy/u);
  }
  assert.notEqual(cancellationMessage("pending"), cancellationMessage("unknown"));
});

test("a submitted cancellation still waits for the native confirmation", () => {
  assert.match(cancellationMessage("submitted"), /Đã gửi.+chờ xác nhận/u);
  assert.doesNotMatch(cancellationMessage("submitted"), /Đã hủy/u);
});

test("input length, URL, local path and capacity failures retain their actionable cause", () => {
  for (const [code, reason] of [["INPUT_TOO_LONG", /độ dài/u], ["URL_INVALID", /URL/u],
    ["LOCAL_PATH_INVALID", /đường dẫn/u], ["QUEUE_LIMIT", /giới hạn/u]]) {
    const message = inputProblemMessage(code);
    assert.equal(typeof message, "string", code);
    assert.match(message, reason, code);
    assert.doesNotMatch(message, /dừng nguồn trực tuyến/u);
  }
  assert.equal(inputProblemMessage("UNRECOGNIZED"), null);
});

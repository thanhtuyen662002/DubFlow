import assert from "node:assert/strict";
import { VoiceCapture, CAPTURE_CONSTRAINTS } from "../src/features/edit/voice_capture.ts";

/** A fake microphone stream whose tracks count their stops. */
function fakeStream() {
  const tracks = [{ stopped: 0, stop() { this.stopped += 1; } }];
  return { tracks, getTracks: () => tracks, live: () => tracks.some((track) => track.stopped === 0) };
}

/** getUserMedia that resolves (or rejects) only when the test says so, counting its calls. */
function deferredMedia() {
  const calls = [];
  const getUserMedia = (constraints) => new Promise((resolve, reject) => calls.push({ constraints, resolve, reject }));
  return { calls, getUserMedia };
}

function recorderFactory({ throwOnCreate = false, throwOnStart = false } = {}) {
  const made = [];
  return {
    made,
    createRecorder: (stream) => {
      if (throwOnCreate) throw new Error("NotSupportedError");
      const recorder = { stream, started: null, start(slice) { if (throwOnStart) throw new Error("InvalidStateError"); this.started = slice; } };
      made.push(recorder);
      return recorder;
    },
  };
}

// ---------- A normal start ----------
{
  const media = deferredMedia();
  const factory = recorderFactory();
  const capture = new VoiceCapture({ getUserMedia: media.getUserMedia, createRecorder: factory.createRecorder });
  const pending = capture.start(1000);
  assert.equal(capture.starting, true, "waiting for permission");
  assert.equal(media.calls[0].constraints, CAPTURE_CONSTRAINTS);
  const stream = fakeStream();
  media.calls[0].resolve(stream);
  const result = await pending;
  assert.equal(result.kind, "started");
  assert.equal(result.stream, stream);
  assert.equal(factory.made[0].started, 1000);
  assert.equal(stream.live(), true, "a started capture keeps its microphone");
  assert.equal(capture.starting, false);
}

// ---------- Double click while the permission prompt is open ----------
{
  const media = deferredMedia();
  const factory = recorderFactory();
  const capture = new VoiceCapture({ getUserMedia: media.getUserMedia, createRecorder: factory.createRecorder });
  const first = capture.start();
  const second = await capture.start();
  assert.equal(second.kind, "busy");
  assert.equal(media.calls.length, 1, "the microphone is asked for once");
  media.calls[0].resolve(fakeStream());
  assert.equal((await first).kind, "started");
  assert.equal(factory.made.length, 1, "one recorder");
}

// ---------- The editor closes while permission is pending ----------
{
  const media = deferredMedia();
  const factory = recorderFactory();
  const capture = new VoiceCapture({ getUserMedia: media.getUserMedia, createRecorder: factory.createRecorder });
  const pending = capture.start();
  capture.cancel();
  assert.equal(capture.starting, false);
  const stream = fakeStream();
  media.calls[0].resolve(stream);
  assert.equal((await pending).kind, "cancelled");
  assert.equal(stream.live(), false, "the late stream is closed");
  assert.equal(factory.made.length, 0, "no recorder is made");
  // A permission refusal that arrives after the close is a cancel too.
  const refused = capture.start();
  capture.cancel();
  media.calls[1].reject(new Error("NotAllowedError"));
  assert.equal((await refused).kind, "cancelled");
  // A new start after a cancel works, and the old one does not clear its "starting".
  const old = capture.start();
  capture.cancel();
  const fresh = capture.start();
  media.calls[2].resolve(fakeStream());
  assert.equal((await old).kind, "cancelled");
  assert.equal(capture.starting, true, "the new start still waits");
  media.calls[3].resolve(fakeStream());
  assert.equal((await fresh).kind, "started");
}

// ---------- Permission refused ----------
{
  const media = deferredMedia();
  const capture = new VoiceCapture({ getUserMedia: media.getUserMedia, createRecorder: recorderFactory().createRecorder });
  const pending = capture.start();
  media.calls[0].reject(new Error("NotAllowedError"));
  const result = await pending;
  assert.equal(result.kind, "denied");
  assert.equal(capture.starting, false, "the button comes back");
}

// ---------- The recorder cannot be made, or cannot start ----------
for (const options of [{ throwOnCreate: true }, { throwOnStart: true }]) {
  const media = deferredMedia();
  const capture = new VoiceCapture({ getUserMedia: media.getUserMedia, createRecorder: recorderFactory(options).createRecorder });
  const pending = capture.start();
  const stream = fakeStream();
  media.calls[0].resolve(stream);
  const result = await pending;
  assert.equal(result.kind, "failed");
  assert.equal(stream.live(), false, "every acquired track is stopped");
  assert.equal(capture.starting, false);
}

console.log("edit_voice_capture: ok");

import assert from "node:assert/strict";
import { VoiceCapture, VoiceTakes, CAPTURE_CONSTRAINTS } from "../src/features/edit/voice_capture.ts";

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

// ---------- Takes: stop, then start again before the stop event arrives ----------
{
  /** A recorder whose data and stop events come only when the test delivers them. */
  class FakeRecorder {
    constructor(stream) { this.stream = stream; this.state = "inactive"; this.mimeType = "audio/webm"; this.listeners = { dataavailable: [], stop: [] }; }
    addEventListener(type, listener) { this.listeners[type].push(listener); }
    start() { this.state = "recording"; }
    stop() { this.state = "inactive"; }
    data(bytes) { for (const listener of this.listeners.dataavailable) listener({ data: new Blob([bytes]) }); }
    stopped() { for (const listener of this.listeners.stop) listener({}); }
  }
  const media = deferredMedia();
  const recorders = [];
  const finished = [];
  let release = null;
  let interruptions = 0;
  const takes = new VoiceTakes({
    interrupted: () => { interruptions += 1; },
    getUserMedia: media.getUserMedia,
    makeRecorder: (stream) => { const made = new FakeRecorder(stream); recorders.push(made); return made; },
    // The save of a take is slow: the next take must wait for it too.
    finished: (take) => { finished.push(take); return new Promise((resolve) => { release = resolve; }); },
  });
  const first = takes.start(12.5);
  assert.equal(takes.state, "starting");
  media.calls[0].resolve(fakeStream());
  assert.equal((await first).kind, "started");
  assert.equal(takes.state, "recording");
  recorders[0].data("one-");
  takes.stop();
  assert.equal(takes.state, "finishing", "blocked from the stop request");
  assert.equal((await takes.start(40)).kind, "busy", "no second take before the first stop event");
  assert.equal(media.calls.length, 1, "the microphone is not asked for again");
  recorders[0].data("take");
  recorders[0].stopped();
  assert.equal(finished.length, 1);
  assert.equal(finished[0].chunks.length, 2);
  assert.equal(await new Blob(finished[0].chunks).text(), "one-take");
  assert.equal(finished[0].at, 12.5);
  assert.equal(takes.state, "finishing", "still saving");
  assert.equal((await takes.start(40)).kind, "busy", "no second take while the first is saved");
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(takes.state, "idle");
  const second = takes.start(40);
  media.calls[1].resolve(fakeStream());
  assert.equal((await second).kind, "started");
  recorders[1].data("two");
  takes.stop();
  recorders[1].stopped();
  assert.equal(await new Blob(finished[1].chunks).text(), "two", "each take keeps its own bytes");
  assert.equal(finished[1].at, 40, "and its own start time");
  assert.equal(await new Blob(finished[0].chunks).text(), "one-take", "the first take is untouched");
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));

  // A recorder that stops on its own (microphone unplugged) still ends its take.
  const third = takes.start(70);
  const stream = fakeStream();
  media.calls[2].resolve(stream);
  await third;
  recorders[2].data("cut");
  recorders[2].stop();
  recorders[2].stopped();
  assert.equal(stream.live(), false, "its microphone is closed");
  assert.equal(interruptions, 1, "the editor hears of it (to restore the preview's sound)");
  assert.equal(finished[2].at, 70);
  assert.equal(takes.state, "finishing");
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(takes.state, "idle");

  assert.equal(interruptions, 1, "a stop asked for is not an interruption");

  // Already inactive with its stop event still queued: stop() keeps the next take waiting.
  const fifth = takes.start(80);
  media.calls[3].resolve(fakeStream());
  await fifth;
  recorders[3].data("queued");
  recorders[3].state = "inactive";
  takes.stop();
  assert.equal(takes.state, "finishing");
  assert.equal((await takes.start(85)).kind, "busy", "busy before the queued stop event");
  recorders[3].stopped();
  assert.equal(finished[3].at, 80);
  assert.equal(await new Blob(finished[3].chunks).text(), "queued");
  assert.equal(takes.state, "finishing", "busy until its save settles");
  assert.equal((await takes.start(85)).kind, "busy");
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(takes.state, "idle");
  assert.equal(interruptions, 1);

  // Stopping while the microphone is still being opened cancels the start.
  const fourth = takes.start(90);
  takes.stop();
  const late = fakeStream();
  media.calls[4].resolve(late);
  assert.equal((await fourth).kind, "cancelled");
  assert.equal(late.live(), false);
  assert.equal(takes.state, "idle");
}

console.log("edit_voice_capture: ok");

import assert from "node:assert/strict";
import { Autosaver, MAX_WAIT_MS, QUIET_MS, RETRY_FIRST_MS, RETRY_MAX_MS, retryDelay, saveDelay } from "../src/features/edit/autosave.ts";
import { askAlone, CLOSE_EVENT, CLOSE_LISTENERS_KEY, CLOSE_WAIT_MS, closeGuard, closeRequests, flushWithin, pageQuestions, unsavedPrompt, wireWindowClose }
  from "../src/features/edit/close_guard.ts";

// A fake clock: timers run when the test moves time on. `fire` is when a timer set for `ms` really fires (a browser
// drops the fraction of a millisecond, for one).
function clock({ start = 0, fire = (ms) => ms } = {}) {
  let now = start, next = 1;
  const due = new Map();
  const timers = {
    now: () => now,
    set: (callback, ms) => { const id = next++; due.set(id, { at: now + fire(ms), callback }); return id; },
    clear: (id) => { due.delete(id); },
  };
  const advance = async (ms) => {
    const end = now + ms;
    for (let fired = 0; ; fired += 1) {
      if (fired > 10_000) throw new Error("timers keep firing without time passing");
      const [id, timer] = [...due.entries()].sort((a, b) => a[1].at - b[1].at)[0] ?? [];
      if (!timer || timer.at > end) break;
      now = timer.at;
      due.delete(id);
      timer.callback();
      await new Promise((resolve) => setImmediate(resolve));
    }
    now = end;
    await new Promise((resolve) => setImmediate(resolve));
  };
  return { timers, advance };
}

// ---------- The delay ----------
assert.equal(saveDelay(0, 0), QUIET_MS);
assert.equal(saveDelay(900, 0), MAX_WAIT_MS - 900, "never later than the max wait after the first unsaved change");
assert.equal(saveDelay(5000, 0), 0);

// ---------- A burst of quick edits is saved while it goes on (PC baseline: a kill 151 ms after an edit lost it) ----------
{
  const { timers, advance } = clock();
  const writes = [];
  const saver = new Autosaver(async () => { writes.push(timers.now()); }, timers);
  for (let i = 0; i < 30; i += 1) {  // an edit every 100 ms for 3 s: the old trailing debounce saved none until 3.5 s
    saver.changed();
    await advance(100);
  }
  assert.ok(writes.length >= 2, `saved during the burst: ${writes}`);
  assert.ok(writes[0] <= MAX_WAIT_MS, `first save within the max wait: ${writes[0]}`);
  for (let i = 1; i < writes.length; i += 1) assert.ok(writes[i] - writes[i - 1] <= MAX_WAIT_MS + 100, `${writes}`);
  await advance(QUIET_MS);
  assert.equal(saver.pending, false, "everything written once quiet");
}

// ---------- One edit, then quiet: one save after QUIET_MS ----------
{
  const { timers, advance } = clock();
  let writes = 0;
  const saver = new Autosaver(async () => { writes += 1; }, timers);
  saver.changed();
  await advance(QUIET_MS - 1);
  assert.equal(writes, 0);
  await advance(1);
  assert.equal(writes, 1);
  await advance(5000);
  assert.equal(writes, 1, "no save without a change");
}

// ---------- Saves never overlap, and a change made during a slow save is saved after it ----------
{
  const { timers, advance } = clock();
  let active = 0, most = 0, writes = 0;
  let release;
  const saver = new Autosaver(() => new Promise((resolve) => {
    active += 1; most = Math.max(most, active); writes += 1;
    release = () => { active -= 1; resolve(); };
  }), timers);
  saver.changed();
  await advance(QUIET_MS);
  assert.equal(writes, 1);
  saver.changed();
  await advance(QUIET_MS);  // the second save waits for the first
  assert.equal(writes, 1);
  release();
  await advance(0);
  assert.equal(writes, 2);
  release();
  await saver.flush();
  assert.equal(most, 1);
  assert.equal(saver.pending, false);
}

// ---------- flush writes a pending change now; a failed write is not lost track of ----------
{
  const { timers } = clock();
  let writes = 0;
  const saver = new Autosaver(async () => { writes += 1; }, timers);
  saver.changed();
  await saver.flush();
  assert.equal(writes, 1);
  await saver.flush();
  assert.equal(writes, 1, "nothing pending: nothing written");
  const failing = new Autosaver(async () => { throw new Error("disk full"); }, timers);
  failing.changed();
  // the view shows the error; the saver does not throw past it, but it does not call the change saved either
  assert.equal(await failing.flush(), false);
  assert.equal(failing.pending, true);
  assert.equal(failing.failing, true);
}

// ---------- Issue #929: a failed write stays pending and is tried again by itself ----------
// The retry delay: 1 s, doubling, never more than 30 s.
assert.deepEqual([1, 2, 3, 4, 5, 6, 7, 50, 1e9].map(retryDelay), [1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000, 30000]);
assert.equal(retryDelay(1), RETRY_FIRST_MS);
assert.equal(retryDelay(0), RETRY_FIRST_MS);
assert.equal(retryDelay(99), RETRY_MAX_MS);

// A held file (PC check 5C): the first write fails, the file is let go, and the change reaches the disk with no new edit.
{
  const { timers, advance } = clock();
  let held = true;
  const attempts = [], saved = [];
  const saver = new Autosaver(async () => {
    attempts.push(timers.now());
    if (held) throw new Error("Không lưu được bản nháp chỉnh sửa");
    saved.push(timers.now());
  }, timers);
  saver.changed();
  await advance(QUIET_MS);
  assert.deepEqual(attempts, [QUIET_MS]);
  assert.equal(saver.pending, true, "the failed change is still pending");
  assert.equal(saver.failing, true);
  held = false;
  await advance(RETRY_FIRST_MS - 1);
  assert.deepEqual(saved, [], "not tried again before the retry delay");
  await advance(1);
  assert.deepEqual(saved, [QUIET_MS + RETRY_FIRST_MS], "tried again after the retry delay, with no new edit");
  assert.equal(saver.pending, false);
  assert.equal(saver.failing, false);
  await advance(60_000);
  assert.equal(attempts.length, 2, "nothing more to write");
}

// A file held for good: retried with a doubling delay up to 30 s; flush says it failed and the backoff goes on.
{
  const { timers, advance } = clock();
  const attempts = [];
  const saver = new Autosaver(async () => { attempts.push(timers.now()); throw new Error("EBUSY"); }, timers);
  saver.changed();
  await advance(QUIET_MS + 1000 + 2000 + 4000 + 8000 + 16000 + 30000 + 30000);
  const gaps = attempts.slice(1).map((at, i) => at - attempts[i]);
  assert.deepEqual(gaps, [1000, 2000, 4000, 8000, 16000, 30000, 30000], `bounded backoff: ${gaps}`);
  assert.equal(saver.pending, true);
  const before = attempts.length;
  const flushedAt = timers.now();
  assert.equal(await saver.flush(), false, "flush tries at once and reports the failure");
  assert.deepEqual(attempts.slice(before), [flushedAt]);
  assert.equal(saver.pending, true, "a persistent failure stays pending");
  await advance(RETRY_MAX_MS - 1);
  assert.equal(attempts.length, before + 1, "not before the backoff after the failed flush");
  await advance(1);
  assert.deepEqual(attempts.slice(before), [flushedAt, flushedAt + RETRY_MAX_MS], "the background retry goes on after a failed flush");
  saver.drop();
}

// An edit never brings a try before its backoff is over (Lead review): after the 1st, 2nd and 3rd failure in a row,
// edits keep coming, and the next try is exactly at the end of the 1 s, 2 s and 4 s backoff.
{
  const { timers, advance } = clock();
  const attempts = [];
  let fail = true;
  const saver = new Autosaver(async () => { attempts.push(timers.now()); if (fail) throw new Error("held"); }, timers);
  saver.changed();
  await advance(QUIET_MS);  // fails at 300
  let failedAt = 300;
  for (const backoff of [1000, 2000, 4000]) {
    for (let at = 100; at < backoff; at += 100) {  // typing all through the backoff
      await advance(100);
      saver.changed();
    }
    assert.equal(attempts.at(-1), failedAt, `no try during the ${backoff} ms backoff: ${attempts}`);
    await advance(failedAt + backoff - timers.now());
    assert.equal(attempts.at(-1), failedAt + backoff, `tried exactly when the ${backoff} ms backoff ends: ${attempts}`);
    failedAt += backoff;
  }
  assert.deepEqual(attempts, [300, 1300, 3300, 7300]);
  fail = false;
  await advance(8000);  // the 8 s backoff ends at 15300: the change, made during it, is written then
  assert.deepEqual(attempts, [300, 1300, 3300, 7300, 15300]);
  assert.equal(saver.pending, false);
}

// An edit made while a write is running sets a timer; when that write then fails, the timer does not try before the
// backoff either (Lead review), nor does a write the timer queued behind it.
for (const queued of [false, true]) {
  const { timers, advance } = clock();
  const attempts = [];
  let release;
  const saver = new Autosaver(() => new Promise((resolve, reject) => {
    attempts.push(timers.now());
    release = () => reject(new Error("held"));
  }), timers);
  saver.changed();
  await advance(QUIET_MS);  // the write starts at 300 and is still running
  saver.changed();  // its timer is due at 600
  if (queued) await advance(QUIET_MS);  // the timer fires during the write: its write waits behind it
  release();  // the write fails at 300 (or 600): due again 1 s later
  const failedAt = timers.now();
  await advance(0);
  await advance(RETRY_FIRST_MS - 1);
  assert.deepEqual(attempts, [300], `queued ${queued}: nothing before the backoff ends`);
  await advance(1);
  assert.deepEqual(attempts, [300, failedAt + RETRY_FIRST_MS], `queued ${queued}: tried when it ends`);
  saver.drop();
}

// A newer edit made while a write is running is not cleared when the older write ends, whether it worked or failed.
for (const outcome of ["ok", "fail"]) {
  const { timers, advance } = clock();
  let release, writes = 0;
  const saver = new Autosaver(() => new Promise((resolve, reject) => {
    writes += 1;
    const first = writes === 1;
    release = () => (first && outcome === "fail" ? reject(new Error("held")) : resolve());
  }), timers);
  saver.changed();
  await advance(QUIET_MS);
  assert.equal(writes, 1);
  saver.changed();  // made while the first write runs
  release();
  await advance(0);
  assert.equal(saver.pending, true, `${outcome}: the newer edit is still pending`);
  // written by its own timer after a success, and exactly when the backoff ends after a failure
  const due = outcome === "ok" ? QUIET_MS : RETRY_FIRST_MS;
  await advance(due - 1);
  assert.equal(writes, 1, `${outcome}: not before ${due} ms`);
  await advance(1);
  assert.equal(writes, 2, `${outcome}: the newer edit is written`);
  release();
  assert.equal(await saver.flush(), true);
  assert.equal(saver.pending, false);
}

// flush waits for a write already running, and tries a failed one again at once.
{
  const { timers } = clock();
  let release, writes = 0, fail = true;
  const saver = new Autosaver(() => new Promise((resolve, reject) => {
    writes += 1;
    release = () => (fail ? reject(new Error("held")) : resolve());
  }), timers);
  saver.changed();
  const first = saver.flush();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(writes, 1);
  release();
  assert.equal(await first, false);
  fail = false;
  const second = saver.flush();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(writes, 2, "a failed change is tried again at once by flush");
  release();
  assert.equal(await second, true);
  assert.equal(saver.pending, false);
}

// flush waits for a write the timer already started (the #929 path: × while the debounce's write to the held file runs)
// and reports how it ended.
for (const ok of [false, true]) {
  const { timers, advance } = clock();
  let release, writes = 0;
  const saver = new Autosaver(() => new Promise((resolve, reject) => {
    writes += 1;
    release = () => (ok ? resolve() : reject(new Error("held")));
  }), timers);
  saver.changed();
  await advance(QUIET_MS);
  assert.equal(writes, 1, "the timer's write is running");
  let settled = false;
  const flushed = saver.flush().then((value) => { settled = true; return value; });
  await advance(0);
  assert.equal(settled, false, `${ok}: flush waits for the running write`);
  release();
  await advance(0);
  // it worked: nothing more to write; it failed: flush tries the change again at once, and waits for that too
  assert.equal(writes, ok ? 1 : 2, `${ok}: writes`);
  if (!ok) assert.equal(settled, false, "the retry is still running");
  if (!ok) release();
  assert.equal(await flushed, ok, `${ok}: and says how it ended`);
  assert.equal(saver.pending, !ok);
  saver.drop();
}

// flush says true only when the disk has every change: edits that keep coming during every write are not on disk yet.
{
  const { timers } = clock();
  let writes = 0;
  const saver = new Autosaver(async () => { writes += 1; saver.changed(); }, timers);
  saver.changed();
  assert.equal(await saver.flush(), false);
  assert.equal(writes, 3, "a few rounds at most");
  assert.equal(saver.pending, true);
  saver.drop();
}

// drop (the user chose to lose the change) stops the retries.
{
  const { timers, advance } = clock();
  let writes = 0;
  const saver = new Autosaver(async () => { writes += 1; throw new Error("held"); }, timers);
  saver.changed();
  await advance(QUIET_MS);
  saver.drop();
  await advance(RETRY_MAX_MS * 3);
  assert.equal(writes, 1);
  assert.equal(saver.pending, false);
  assert.equal(await saver.flush(), true);
  assert.equal(writes, 1);
}

// A write that never ends (a close gave up on it after CLOSE_WAIT_MS and the user chose "Vẫn thoát") does not hold the
// next session's saves (Lead review): the new edit is written, and flush answers.
{
  const { timers, advance } = clock();
  const started = [];
  const saver = new Autosaver(() => {
    started.push(timers.now());
    return started.length === 1 ? new Promise(() => {}) : Promise.resolve();
  }, timers);
  saver.changed();
  await advance(QUIET_MS);
  assert.equal(started.length, 1, "the old session's write hangs");
  const closing = flushWithin(() => saver.flush(), CLOSE_WAIT_MS, timers);
  await advance(CLOSE_WAIT_MS);
  assert.equal(await closing, false, "the close counts it as not saved");
  saver.drop();  // "Vẫn thoát"
  saver.changed();  // the next video's first edit
  await advance(QUIET_MS);
  assert.equal(started.length, 2, "the new edit is written");
  assert.equal(saver.pending, false);
  saver.changed();
  assert.equal(await saver.flush(), true);
}

// A write still waiting when the change is dropped (the user chose to lose it and opened another video) does not
// swallow the next session's change: in-flight write -> queued successor -> drop -> new edit -> the new edit is written.
{
  const { timers, advance } = clock();
  const releases = [];
  const wrote = [];
  let session = "old";
  const saver = new Autosaver(() => new Promise((resolve, reject) => {
    const who = session;
    releases.push((ok) => { wrote.push(`${who}:${ok ? "ok" : "fail"}`); (ok ? resolve : () => reject(new Error("held")))(); });
  }), timers);
  saver.changed();
  await advance(QUIET_MS);  // the old session's write is running
  assert.equal(releases.length, 1);
  saver.changed();
  await advance(QUIET_MS);  // its successor waits
  saver.drop();  // "Vẫn thoát"
  session = "new";
  saver.changed();  // the next video's first edit, before the waiting write's turn
  await advance(QUIET_MS);
  releases.shift()(false);  // the old write fails
  await advance(0);
  assert.equal(saver.failing, false, "the dropped session's failure does not count against the new one");
  await advance(MAX_WAIT_MS);
  assert.equal(releases.length, 1, `the new session's edit is written: ${wrote}`);
  releases.shift()(true);
  await advance(0);
  assert.deepEqual(wrote, ["old:fail", "new:ok"]);
  assert.equal(saver.pending, false);
  await advance(RETRY_MAX_MS);
  assert.equal(releases.length, 0, "no stale retry of the dropped change");
}

// ---------- Issue #929: closing waits for the draft, and asks before losing a change ----------
{
  const { timers, advance } = clock();
  // the draft reaches the disk: no question
  let asked = 0;
  const ok = closeGuard({ flush: async () => true, ask: async () => { asked += 1; return true; }, timers });
  assert.equal(await ok(), true);
  assert.equal(asked, 0);

  // it does not: one question; "Ở lại" keeps the window open and the change pending
  const { timers: t2 } = clock();
  const saver = new Autosaver(async () => { throw new Error("held"); }, t2);
  saver.changed();
  const answers = [false, true];
  const questions = [];
  const guarded = closeGuard({ flush: () => saver.flush(), ask: async () => { questions.push(1); return answers.shift(); }, timers: t2 });
  assert.equal(await guarded(), false, "Ở lại: the window stays");
  assert.equal(questions.length, 1);
  assert.equal(saver.pending, true, "Ở lại keeps the change and its retry");
  // two close requests at once (the × button twice, Alt+F4 with it): one question, one answer for both
  const [a, b] = await Promise.all([guarded(), guarded()]);
  assert.equal(questions.length, 2);
  assert.equal(a, true, "Vẫn đóng: only an explicit choice lets the change go");
  assert.equal(b, true);

  // a write that hangs counts as not saved once CLOSE_WAIT_MS is past
  let hangAsked = 0;
  const hanging = closeGuard({ flush: () => new Promise(() => {}), ask: async () => { hangAsked += 1; return false; }, timers });
  const pending = hanging();
  await advance(CLOSE_WAIT_MS - 1);
  assert.equal(hangAsked, 0);
  await advance(1);
  assert.equal(await pending, false);
  assert.equal(hangAsked, 1);

  // a flush that throws, or a question that fails, never lets the window close by itself
  assert.equal(await flushWithin(() => { throw new Error("x"); }, 1000, timers), false);
  assert.equal(await flushWithin(async () => { throw new Error("x"); }, 1000, timers), false);
  assert.equal(await closeGuard({ flush: async () => false, ask: async () => { throw new Error("x"); }, timers })(), false);
  // only an explicit true counts: a flush or a question that answers nothing does not let the window go
  assert.equal(await flushWithin(async () => undefined, 1000, timers), false);
  let askedAfterVoid = 0;
  assert.equal(await closeGuard({ flush: async () => undefined, ask: async () => { askedAfterVoid += 1; return undefined; }, timers })(), false);
  assert.equal(askedAfterVoid, 1, "a flush that answers nothing is asked about");
  for (const truthy of ["yes", 1, {}]) {
    assert.equal(await flushWithin(async () => truthy, 1000, timers), false, `a flush answering ${JSON.stringify(truthy)} is not saved`);
    assert.equal(await closeGuard({ flush: async () => false, ask: async () => truthy, timers })(), false,
      `a question answering ${JSON.stringify(truthy)} does not close`);
  }
}

// The window close waits for an editor close that is already asking: never two questions at once.
{
  const { timers } = clock();
  let answer, flushed = 0, asked = 0;
  const editorClosing = new Promise((resolve) => { answer = resolve; });
  let saved = false;
  const mayClose = closeGuard({ before: () => editorClosing, flush: async () => { flushed += 1; return saved; },
    ask: async () => { asked += 1; return false; }, timers });
  const result = mayClose();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(flushed, 0, "nothing until the editor's own question is answered");
  assert.equal(asked, 0);
  saved = true;  // "Vẫn thoát" there: the change was let go, nothing is pending
  answer(true);
  assert.equal(await result, true);
  assert.equal(flushed, 1);
  assert.equal(asked, 0, "no second question");
  // nothing closing: no wait at all
  assert.equal(await closeGuard({ before: () => null, flush: async () => true, ask: async () => false, timers })(), true);
}

// A timer that fires a little early (setTimeout drops the fraction of a millisecond, the steady clock has one) never
// leaves a failed change without a retry: it is set again, and set in whole milliseconds rounded up.
for (const [name, fire, edit] of [["truncated", (ms) => Math.trunc(ms), true], ["early", (ms) => ms - 0.5, true],
  ["early, no edit", (ms) => ms - 0.5, false]]) {
  const { timers, advance } = clock({ start: 0.25, fire });
  let fails = 1;
  const writes = [];
  const saver = new Autosaver(async () => { writes.push(timers.now()); if (fails-- > 0) throw new Error("held"); }, timers);
  saver.changed();
  await advance(QUIET_MS + 1);
  assert.equal(writes.length, 1, `${name}: the first write failed`);
  await advance(100.3);
  if (edit) saver.changed();  // an edit during the backoff, at a time with a fraction
  await advance(RETRY_FIRST_MS + 50);
  assert.equal(writes.length, 2, `${name}: tried again after the backoff`);
  assert.ok(writes[1] >= writes[0] + RETRY_FIRST_MS, `${name}: not before the backoff: ${writes}`);
  assert.equal(saver.pending, false, `${name}: on disk`);
}

// A system clock step back cannot push the next try past RETRY_MAX_MS (windowTimers uses a steady clock too).
{
  let now = 0, next = 1;
  const due = new Map();
  const timers = { now: () => now, set: (callback, ms) => { const id = next++; due.set(id, { at: now + ms, callback }); return id; },
    clear: (id) => { due.delete(id); } };
  const saver = new Autosaver(async () => { throw new Error("held"); }, timers);
  saver.changed();
  due.get(1).callback();
  due.delete(1);
  await new Promise((resolve) => setImmediate(resolve));
  now -= 3_600_000;  // the clock goes back an hour
  saver.changed();
  const waits = [...due.values()].map((timer) => timer.at - now);
  assert.equal(waits.length, 1);
  assert.ok(waits[0] <= RETRY_MAX_MS, `next try in ${waits[0]} ms`);
  saver.drop();
}

// The wording never says the change is saved, and the safe answer is "Ở lại".
for (const action of ["close", "exit"]) {
  const prompt = unsavedPrompt(action, true);
  assert.equal(prompt.cancelLabel, "Ở lại");
  assert.equal(prompt.confirmLabel, action === "close" ? "Vẫn đóng" : "Vẫn thoát");
  assert.equal(prompt.danger, true);
  assert.match(prompt.message, /chưa lưu được/);
  assert.match(prompt.message, /sẽ mất/);
  assert.doesNotMatch(`${prompt.title} ${prompt.message}`, /vẫn được lưu|đã lưu/i);
}

// The × button and the system's close (Alt+F4, the taskbar) both go through the guard.
{
  // A stand-in for the Tauri 2.12 window. As in tauri's manager/window.rs, while a close request listener is
  // registered the system close is held and the listeners run; with none it closes. destroy() is not allowed (the app
  // has no core:window:allow-destroy), and registering or removing a listener takes a moment (a round trip to the
  // host, longer than a microtask: a close that does not wait for the removal is held). `log` says, for every close,
  // whether it was held or closed the window.
  function fakeWindow({ listen = true, armAfter = 0 } = {}) {
    const state = { handlers: [], closed: 0, requests: 0, destroyed: 0, log: [] };
    let armNow;
    const armed = armAfter ? new Promise((resolve) => { armNow = resolve; }) : Promise.resolve();
    const window = {
      async onCloseRequested(handler) {
        state.handlers.push(handler);  // the page side knows the handler at once
        await armed;
        if (!listen) {
          state.handlers.splice(state.handlers.indexOf(handler), 1);
          throw new Error("no event permission");
        }
        // like Tauri's wrapper: the window is destroyed unless the handler prevented it
        return async () => {
          await new Promise((resolve) => setTimeout(resolve, 2));
          state.handlers.splice(state.handlers.indexOf(handler), 1);
        };
      },
      async close() {
        state.requests += 1;
        await null;
        state.log.push(state.handlers.length ? "held" : "closed");
        if (!state.handlers.length) { state.closed += 1; return; }
        for (const handler of [...state.handlers]) {
          let prevented = false;
          await handler({ preventDefault: () => { prevented = true; } });
          if (!prevented) state.destroyed += 1;
        }
      },
      async destroy() { throw new Error("window.destroy not allowed"); },
    };
    return { window, state, arm: () => armNow?.() };
  }
  function fakeButton() {
    const listeners = [];
    return { addEventListener: (type, listener) => listeners.push(listener), click: () => listeners.forEach((l) => l()) };
  }
  const settle = () => new Promise((resolve) => setTimeout(resolve, 30));

  for (const via of ["button", "system"]) {
    let flushed = 0, answer = false, asked = 0;
    const { window, state } = fakeWindow();
    const button = fakeButton();
    const { timers } = clock();
    const mayClose = closeGuard({ flush: async () => { flushed += 1; return false; }, ask: async () => { asked += 1; return answer; }, timers });
    assert.equal(await wireWindowClose(window, button, mayClose), true, "the guard is in place");
    const request = () => (via === "button" ? button.click() : void window.close());
    request();
    await settle();
    assert.equal(flushed, 1, `${via}: waits for the draft`);
    assert.equal(asked, 1, `${via}: asks when it could not be saved`);
    assert.equal(state.closed, 0, `${via}: Ở lại keeps the window open`);
    answer = true;
    request();
    await settle();
    assert.equal(state.closed, 1, `${via}: Vẫn đóng closes it`);
    assert.equal(asked, 2, `${via}: one question per close, none more after Vẫn đóng`);
    assert.equal(state.destroyed, 0, `${via}: never through destroy`);
    assert.equal(state.handlers.length, 0, `${via}: the listener is removed before the window closes`);
    assert.deepEqual(state.log, via === "button" ? ["closed"] : ["held", "held", "closed"],
      `${via}: the close after Vẫn đóng waits for the listener to go, so it is not held again`);
  }
  // saved: the window closes with no question, through the system close
  {
    let asked = 0;
    const { window, state } = fakeWindow();
    const button = fakeButton();
    const { timers } = clock();
    await wireWindowClose(window, button, closeGuard({ flush: async () => true, ask: async () => { asked += 1; return false; }, timers }));
    button.click();
    await settle();
    assert.equal(state.closed, 1);
    assert.equal(asked, 0);
    assert.equal(state.destroyed, 0);
  }
  // × clicked while the listener is still starting: the close waits for it, then goes, with one question at most
  {
    let asked = 0;
    const { window, state, arm } = fakeWindow({ armAfter: 1 });
    const button = fakeButton();
    const { timers } = clock();
    const guarded = wireWindowClose(window, button, closeGuard({ flush: async () => false, ask: async () => { asked += 1; return true; }, timers }));
    button.click();
    await settle();
    assert.equal(state.closed, 0, "not before the listener is in place");
    arm();
    assert.equal(await guarded, true);
    await settle();
    assert.equal(state.closed, 1);
    assert.equal(asked, 1, "one question");
  }
  // the listener cannot start: the guard says so, so the editor does not open (see the view checks below); with
  // nothing to save, × still closes
  {
    const { window, state } = fakeWindow({ listen: false });
    const button = fakeButton();
    const { timers } = clock();
    assert.equal(await wireWindowClose(window, button, closeGuard({ flush: async () => true, ask: async () => false, timers })), false);
    button.click();
    await settle();
    assert.equal(state.closed, 1);
  }

  // A reload (F5, Ctrl+R, Tải lại) leaves the page's close listener in Tauri, which then holds every close for a
  // handler that no longer runs: the page after it removes that listener before it listens itself.
  function fakeTauri({ failUnlisten = () => false } = {}) {
    const state = { listeners: new Map(), next: 0, closed: 0, calls: [] };
    const bridge = {
      async listen(event, label, handler) {
        await new Promise((resolve) => setTimeout(resolve, 1));
        const id = state.next++;
        state.listeners.set(id, { event, label, handler });
        state.calls.push(`listen ${id}`);
        return id;
      },
      async unlisten(event, id) {
        await new Promise((resolve) => setTimeout(resolve, 1));
        if (failUnlisten(id)) throw new Error("ipc");
        state.calls.push(`unlisten ${id}`);
        state.listeners.delete(id);
      },
    };
    // as in tauri's manager/window.rs: held while any close listener is registered, and each listener is told
    const close = async () => {
      await null;
      const listeners = [...state.listeners.values()].filter((listener) => listener.event === CLOSE_EVENT);
      if (!listeners.length) { state.closed += 1; return; }
      for (const listener of listeners) listener.handler();
    };
    return { state, bridge, close };
  }
  function memoryStore() {
    let value = null;
    return { get: () => value, set: (next) => { value = next; } };
  }
  {
    const store = memoryStore();
    const tauri = fakeTauri();
    const { timers } = clock();
    // the page before the reload
    await wireWindowClose({ close: tauri.close, onCloseRequested: closeRequests("main", tauri.bridge, store) }, fakeButton(),
      closeGuard({ flush: async () => true, ask: async () => false, timers }));
    assert.equal(store.get(), "[0]", "the listener's id is kept where a reload finds it");
    for (const listener of tauri.state.listeners.values()) listener.handler = () => {};  // reloaded: nothing runs it
    // the page after it
    const button = fakeButton();
    assert.equal(await wireWindowClose({ close: tauri.close, onCloseRequested: closeRequests("main", tauri.bridge, store) }, button,
      closeGuard({ flush: async () => true, ask: async () => false, timers })), true);
    assert.deepEqual(tauri.state.calls, ["listen 0", "unlisten 0", "listen 1"], "the old listener goes before the new one starts");
    assert.equal(tauri.state.listeners.get(1).label, "main");
    assert.equal(store.get(), "[1]");
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1, "the window still closes after a reload");
    assert.equal(store.get(), null, "nothing left to remove");
  }
  // a removal that fails is kept, and tried again before the next listener
  {
    const store = memoryStore();
    let failing = true;
    const tauri = fakeTauri({ failUnlisten: () => failing });
    const onCloseRequested = closeRequests("main", tauri.bridge, store);
    const stop = await onCloseRequested(async () => {});
    await assert.rejects(Promise.resolve(stop()));
    assert.equal(store.get(), "[0]");
    failing = false;
    await onCloseRequested(async () => {});
    assert.deepEqual(tauri.state.calls, ["listen 0", "unlisten 0", "listen 1"]);
    assert.equal(store.get(), "[1]");
  }
  // a store that refuses or holds something else: the guard still starts, and nothing else is removed
  for (const store of [
    { get: () => { throw new Error("denied"); }, set: () => { throw new Error("denied"); } },
    { get: () => "not json", set: () => {} },
    { get: () => JSON.stringify(["7", -1, 2.5, null, { id: 3 }]), set: () => {} },
  ]) {
    const tauri = fakeTauri();
    const stop = await closeRequests("main", tauri.bridge, store)(async () => {});
    assert.deepEqual(tauri.state.calls, ["listen 0"]);
    await stop();
    assert.equal(tauri.state.listeners.size, 0);
  }
  // the close request reaches the guard's handler, which always holds it
  {
    const tauri = fakeTauri();
    const events = [];
    await closeRequests("main", tauri.bridge, memoryStore())(async (event) => { events.push(event); event.preventDefault(); });
    await tauri.close();
    await settle();
    assert.equal(events.length, 1);
    assert.equal(tauri.state.closed, 0);
  }
}

// One question at a time: with another open (the editor's exit question, or any other), the window close or the
// editor exit stays without asking; the question starts on "Ở lại".
{
  const order = [];
  const place = { opened: false, focused: 0, open() { return this.opened; }, focusStay() { this.focused += 1; order.push("focus"); } };
  let asked = 0;
  const ask = () => { asked += 1; order.push("ask"); return Promise.resolve(true); };
  assert.equal(await askAlone(ask, place), true);
  assert.equal(place.focused, 1, "Enter right after Alt+F4 keeps the change");
  assert.deepEqual(order, ["ask", "focus"], "the question is on the page (confirmDialog adds it at once) before Ở lại takes the focus");
  place.opened = true;
  assert.equal(await askAlone(ask, place), false, "another question open: stay");
  assert.equal(asked, 1, "no second question");
  const { readFileSync } = await import("node:fs");
  const confirm = readFileSync(new URL("../src/shell/confirm.ts", import.meta.url), "utf8");
  assert.match(confirm, /actions\.append\(cancel, confirm\)/, "the dialog's first button is the cancel (Ở lại) one");
  assert.match(confirm, /backdrop\.className = "confirm-backdrop"/);
  assert.match(confirm, /panel\.className = "confirm-panel"/);
  assert.match(confirm, /actions\.className = "confirm-actions"/);
  assert.match(confirm, /document\.body\.append\(backdrop\);\s*confirm\.focus\(\);\s*\}\);\s*\}/, "the dialog is on the page when confirmDialog returns");
  // the page's questions, on a stand-in document: the newest panel's first button gets the focus
  const focused = [];
  const panel = (name) => ({ querySelector: (selector) => selector === ".confirm-actions button" ? { focus: () => focused.push(name) } : null });
  let backdrop = null;
  globalThis.document = {
    querySelector: (selector) => (selector === ".confirm-backdrop" ? backdrop : null),
    querySelectorAll: (selector) => (selector === ".confirm-panel" ? [panel("older"), panel("newest")] : []),
  };
  try {
    assert.equal(pageQuestions.open(), false);
    backdrop = {};
    assert.equal(pageQuestions.open(), true);
    pageQuestions.focusStay();
    assert.deepEqual(focused, ["newest"]);
  } finally {
    delete globalThis.document;
  }
  assert.match(confirm, /cancelFocused \? "cancel" : "confirm"/, "Enter on the focused Ở lại answers stay");
}

// The wiring (view.ts and main.ts are DOM code): a failed save reaches the saver, and every close goes through the guard.
{
  const { readFileSync } = await import("node:fs");
  // the code without its comments, so a check cannot be met by text in a comment
  const code = (path) => readFileSync(new URL(path, import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
  const capabilities = JSON.parse(readFileSync(new URL("../src-tauri/capabilities/default.json", import.meta.url), "utf8"));
  const capabilitiesHaveEvents = () => capabilities.permissions.includes("core:default") || capabilities.permissions.includes("core:event:default");
  const view = code("../src/features/edit/view.ts");
  const save = view.slice(view.indexOf("async function save(): Promise<void>"), view.indexOf("function scheduleSave(): void"));
  assert.match(save, /catch \(error\) \{[\s\S]*\n\s*throw error;\s*\}\s*\}\s*$/, "save() tells the saver that the write failed");
  assert.match(view, /new Autosaver\(\(\) => save\(\), windowTimers\)/, "the saver gets save() as it is, failures included");
  const closeNow = view.slice(view.indexOf("async function closeNow("), view.indexOf("function leave(leaving: EditTarget)"));
  assert.match(closeNow, /const saved = await flushWithin\(\(\) => saver\.flush\(\), CLOSE_WAIT_MS, windowTimers\);/);
  assert.ok(closeNow.indexOf("flushWithin(") < closeNow.indexOf("cover.end()"), "the draft is written before the editor ends");
  // a change is dropped only after the user chose to, never on "Ở lại"; a reload's close carries it instead of asking
  assert.match(closeNow, /if \(!saved && carrying\) \{\s*carry\(session\);\s*\} else if \(!saved\) \{[^}]*if \(!\(await askAlone\(\(\) => host\.confirm\(unsavedPrompt\("exit", unapplied\)\)\)\)\) return false;\s*discard\(\);/);
  assert.match(closeNow, /if \(carrying\) carry\(session\);\s*else \{\s*if \(!\(await askAlone\(\(\) => host\.confirm\(unsavedPrompt\("exit"\)\)\)\)\) return false;\s*discard\(\);/,
    "a change made while the question was open is written too, and dropped only on Vẫn thoát");
  assert.match(closeNow, /if \(saver\.pending && !\(await flushWithin\(\(\) => saver\.flush\(\), CLOSE_WAIT_MS, windowTimers\)\)\)/);
  assert.equal((closeNow.match(/discard\(\)/g) ?? []).length, 2);
  assert.match(view, /function discard\(\): void \{\s*saver\.drop\(\);\s*carried = null;\s*\}/, "Vẫn thoát lets a carried change go too");
  assert.equal((view.match(/saver\.drop\(\)/g) ?? []).length, 1, "nothing else drops a change");
  // a carried change stays pending, with its backoff: written from its copy when no editor takes it up, and a window
  // close is not told the disk has it before then
  assert.match(view, /function carry\(current: Session\): void \{\s*carried = \{ outputPath: current\.outputPath, kind: current\.target\.kind, project: draftDocument\(current\),[^}]*\};\s*saver\.changed\(\);\s*\}/);
  assert.match(save, /^async function save\(\): Promise<void> \{\s*const left = carried && carried\.outputPath !== session\?\.outputPath \? carried : null;\s*if \(left\) \{\s*try \{\s*await invoke\("edit_project_save", \{ outputPath: left\.outputPath, kind: left\.kind, project: left\.project, keepOld: left\.keepOld \}\);[\s\S]*?throw error;\s*\}[\s\S]*?if \(carried === left\) carried = null;\s*\}\s*if \(!session\) return;/);
  assert.match(view, /async function flush\(\): Promise<boolean> \{\s*return \(await saver\.flush\(\)\) && !carried;\s*\}/);
  const opening = view.slice(view.indexOf("async function open(target: EditTarget)"), view.indexOf("function close(ask: boolean"));
  assert.ok(opening.indexOf("const carry = carried?.outputPath === outputPath ? carried : null;") > 0
    && opening.indexOf("const carry = carried?.outputPath === outputPath ? carried : null;") < opening.indexOf("edit_project_load"),
  "the carried change is taken before the load (a retry may write it after the load read the older draft)");
  assert.match(opening, /if \(carry && carried === carry\) \{\s*carried = null;\s*scheduleSave\(\);\s*\}/, "the reopened editor writes it on");
  // only a close that left counts as leaving: "Ở lại" does not stop a reload, "Thoát" does
  const closing = view.slice(view.indexOf("function close(ask: boolean"), view.indexOf("async function closeNow("));
  assert.match(closing, /\.then\(\(left\) => \{\s*if \(left && !quiet\) exits \+= 1;\s*return left;\s*\}\)/);
  assert.equal((view.match(/exits \+= 1/g) ?? []).length, 1);
  // the editor opens only once every window close is guarded
  const open = view.slice(view.indexOf("async function open(target: EditTarget)"), view.indexOf("edit_project_load"));
  assert.match(open, /if \(host\.closeGuarded && !\(await host\.closeGuarded\(\)\)\) \{[^}]*return false;\s*\}/);
  const main = code("../src/main.ts");
  assert.match(main, /const closable = \{ close: \(\) => appWindow\.close\(\), onCloseRequested: closeRequests\(appWindow\.label, events, reloadSafe\) \};\s*windowCloseGuarded = wireWindowClose\(closable, \$\("#window-close"\), mayClose\);/);
  // the event bridge calls the commands Tauri's own listen and unlisten call, with the same arguments
  assert.match(main, /tauriInvoke<number>\("plugin:event\|listen",\s*\{ event, target: \{ kind: "Window", label \}, handler: transformCallback\(handler\) \}\)/);
  assert.match(main, /await tauriInvoke\("plugin:event\|unlisten", \{ event, eventId: id \}\);/);
  assert.match(main, /window\.sessionStorage\.getItem\(CLOSE_LISTENERS_KEY\)/);
  const tauriEvent = readFileSync(new URL("../node_modules/@tauri-apps/api/event.js", import.meta.url), "utf8");
  assert.match(tauriEvent, /invoke\('plugin:event\|listen', \{\s*event,\s*target,\s*handler: transformCallback\(handler\)\s*\}\)/);
  assert.match(tauriEvent, /invoke\('plugin:event\|unlisten', \{\s*event,\s*eventId\s*\}\)/);
  const tauriWindow = readFileSync(new URL("../node_modules/@tauri-apps/api/window.js", import.meta.url), "utf8");
  assert.match(tauriWindow, /target: \{ kind: 'Window', label: this\.label \}/, "the window's own listen targets it the same way");
  assert.equal(CLOSE_EVENT, "tauri://close-requested");
  assert.match(tauriEvent, /WINDOW_CLOSE_REQUESTED"\] = "tauri:\/\/close-requested"/);
  assert.ok(capabilitiesHaveEvents(), "listening is allowed (core:default has core:event:default)");
  assert.match(main, /closeGuarded: \(\) => windowCloseGuarded,/);
  assert.ok(main.indexOf("let windowCloseGuarded") < main.indexOf("const editor = mountEditor("), "set before the editor can open");
  assert.doesNotMatch(main, /editor\.flush\(\)\.finally\(/, "no close that ignores a failed save");
  assert.match(main, /closeGuard\(\{ before: \(\) => editor\.closing\(\), flush: \(\) => editor\.flush\(\),\s*ask: \(\) => askAlone\(\(\) => confirmDialog\(unsavedPrompt\("close"\)\)\)/,
    "the window close waits for an editor close in progress, then asks one question at a time");
  assert.doesNotMatch(main + view + code("../src/features/edit/close_guard.ts"), /\.destroy\(\)/, "never destroys the window");
  assert.ok(!capabilities.permissions.includes("core:window:allow-destroy"), "no new window permission (Issue #929 contract)");
  // every save goes through the saver (a direct save() would neither be retried nor seen by a close)
  assert.doesNotMatch(view, /void save\(\)/);
  assert.equal((view.match(/\bsave\(\)/g) ?? []).length, 2, "save() is defined once and called only by the Autosaver");
  // a reload of the same video carries an unsaved change instead of asking, and does not undo a later exit
  const reopen = view.slice(view.indexOf("async function reopenSame("), view.indexOf("async function applyLayers("));
  assert.match(reopen, /const exit = exits;[\s\S]*if \(!\(await close\(false, true, true\)\)\) return;\s*if \(exits !== exit \|\| !\(await open\(target\)\)\) return;\s*if \(exits !== exit\) \{\s*await close\(false\);\s*return;\s*\}\s*await then\(\);/,
    "a user who left before or while it reopened is not taken back");
  assert.match(view, /const exit = exits;\s*if \(!\(await close\(false, true\)\)\) return;\s*if \(exits !== exit\) return;[\s\S]{0,300}if \(!opened\) leave\(from\);\s*else if \(exits !== exit\) await close\(false\);/,
    "the template's reopen, likewise");
  assert.equal((view.match(/reopenSame\(/g) ?? []).length, 4, "the three documentary reloads use it");
  assert.doesNotMatch(view, /reload: async/, "no reload of its own");
  assert.match(view, /if \(carry\) loaded\.project = carry\.project;/);
  // a refused close from a queue selection is not asked again on every render
  assert.match(view, /if \(stayedFor !== id\) void close\(false\)\.then\(\(left\) => \{ if \(!left && session\) stayedFor = id; \}\);/);
}

console.log("edit autosave tests passed");

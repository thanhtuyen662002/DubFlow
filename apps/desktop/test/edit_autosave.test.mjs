import assert from "node:assert/strict";
import { Autosaver, MAX_WAIT_MS, QUIET_MS, RETRY_FIRST_MS, RETRY_MAX_MS, retryDelay, saveDelay } from "../src/features/edit/autosave.ts";
import { CLOSE_WAIT_MS, closeGuard, flushWithin, unsavedPrompt, wireWindowClose } from "../src/features/edit/close_guard.ts";

// A fake clock: timers run when the test moves time on.
function clock() {
  let now = 0, next = 1;
  const due = new Map();
  const timers = {
    now: () => now,
    set: (callback, ms) => { const id = next++; due.set(id, { at: now + ms, callback }); return id; },
    clear: (id) => { due.delete(id); },
  };
  const advance = async (ms) => {
    const end = now + ms;
    for (;;) {
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

// A file held for good: retried with a doubling delay up to 30 s, never two writes at once; flush says it failed.
{
  const { timers, advance } = clock();
  const attempts = [];
  let active = 0, most = 0;
  const saver = new Autosaver(async () => {
    active += 1; most = Math.max(most, active);
    attempts.push(timers.now());
    await null;  // the write ends a little later
    active -= 1;
    throw new Error("EBUSY");
  }, timers);
  saver.changed();
  await advance(QUIET_MS + 1000 + 2000 + 4000 + 8000 + 16000 + 30000 + 30000);
  const gaps = attempts.slice(1).map((at, i) => at - attempts[i]);
  assert.deepEqual(gaps, [1000, 2000, 4000, 8000, 16000, 30000, 30000], `bounded backoff: ${gaps}`);
  assert.equal(most, 1, "writes never overlap");
  assert.equal(saver.pending, true);
  const before = attempts.length;
  assert.equal(await saver.flush(), false, "flush tries at once and reports the failure");
  assert.equal(attempts.length, before + 1);
  assert.equal(saver.pending, true, "a persistent failure stays pending");
  await advance(RETRY_FIRST_MS * 2);  // still tried again after a failed flush (the 2nd failure in a row is long past)
  assert.ok(attempts.length >= before + 1);
  await advance(RETRY_MAX_MS);
  assert.ok(attempts.length >= before + 2, "the background retry goes on after a failed flush");
  assert.equal(most, 1);
}

// An edit while failing brings the next try forward to at most MAX_WAIT_MS after it, but not before the retry is due.
{
  const { timers, advance } = clock();
  const attempts = [];
  let fail = true;
  const saver = new Autosaver(async () => { attempts.push(timers.now()); if (fail) throw new Error("held"); }, timers);
  saver.changed();
  await advance(QUIET_MS);  // fails at 300: due again at 1300
  await advance(100);
  saver.changed();  // at 400: not before 1300, and no later than 1400
  await advance(899);
  assert.equal(attempts.length, 1);
  await advance(1);
  assert.deepEqual(attempts, [300, 1300]);  // fails again: due at 3300
  for (let i = 0; i < 20; i += 1) {  // typing while it fails: at most one try per MAX_WAIT_MS
    saver.changed();
    await advance(100);
  }
  const tries = attempts.slice(2);
  for (let i = 1; i < tries.length; i += 1) assert.ok(tries[i] - tries[i - 1] >= MAX_WAIT_MS, `${attempts}`);
  assert.ok(tries.length >= 1 && tries[0] - 1400 <= MAX_WAIT_MS, `an edit gets a try within MAX_WAIT_MS: ${attempts}`);
  fail = false;
  saver.changed();
  assert.equal(await saver.flush(), true);
  assert.equal(saver.pending, false);
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
  await advance(MAX_WAIT_MS + RETRY_FIRST_MS);
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
  // a stand-in for the Tauri window: `close` raises a close request, and the window goes unless a handler prevents it
  function fakeWindow({ listen = true } = {}) {
    const state = { handlers: [], closed: 0, requests: 0 };
    const window = {
      async onCloseRequested(handler) {
        if (!listen) throw new Error("no event permission");
        state.handlers.push(handler);
        return () => undefined;
      },
      async close() {
        state.requests += 1;
        if (!state.handlers.length) { state.closed += 1; return; }
        let prevented = false;
        for (const handler of state.handlers) await handler({ preventDefault: () => { prevented = true; } });
        if (!prevented) state.closed += 1;
      },
    };
    return { window, state };
  }
  function fakeButton() {
    const listeners = [];
    return { addEventListener: (type, listener) => listeners.push(listener), click: () => listeners.forEach((l) => l()) };
  }
  const settle = () => new Promise((resolve) => setTimeout(resolve, 5));

  for (const via of ["button", "system"]) {
    let flushed = 0, answer = false, asked = 0;
    const { window, state } = fakeWindow();
    const button = fakeButton();
    const { timers } = clock();
    const mayClose = closeGuard({ flush: async () => { flushed += 1; return false; }, ask: async () => { asked += 1; return answer; }, timers });
    await wireWindowClose(window, button, mayClose);
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
  }
  // saved: the window closes with no question
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
  }
  // no close request listener (the event permission missing): the × button still asks first
  {
    let asked = 0;
    const { window, state } = fakeWindow({ listen: false });
    const button = fakeButton();
    const { timers } = clock();
    await wireWindowClose(window, button, closeGuard({ flush: async () => false, ask: async () => { asked += 1; return false; }, timers }));
    button.click();
    await settle();
    assert.equal(asked, 1);
    assert.equal(state.closed, 0);
  }
}

// The wiring (view.ts and main.ts are DOM code): a failed save reaches the saver, and every close goes through the guard.
{
  const { readFileSync } = await import("node:fs");
  const view = readFileSync(new URL("../src/features/edit/view.ts", import.meta.url), "utf8");
  const save = view.slice(view.indexOf("async function save(): Promise<void>"), view.indexOf("function scheduleSave(): void"));
  assert.match(save, /catch \(error\) \{[\s\S]*throw error;\s*\}\s*\}\s*$/, "save() tells the saver that the write failed");
  const closeNow = view.slice(view.indexOf("async function closeNow("), view.indexOf("function leave(leaving: EditTarget)"));
  assert.match(closeNow, /flushWithin\(\(\) => saver\.flush\(\), CLOSE_WAIT_MS, windowTimers\)/);
  assert.ok(closeNow.indexOf("flushWithin(") < closeNow.indexOf("cover.end()"), "the draft is written before the editor ends");
  assert.match(closeNow, /unsavedPrompt\("exit"/);
  assert.ok(closeNow.indexOf("saver.drop()") > closeNow.indexOf("unsavedPrompt(\"exit\""), "a change is dropped only after the user chose to");
  const main = readFileSync(new URL("../src/main.ts", import.meta.url), "utf8");
  assert.match(main, /wireWindowClose\(appWindow, \$\("#window-close"\), mayClose\)/);
  assert.doesNotMatch(main, /editor\.flush\(\)\.finally\(/, "no close that ignores a failed save");
  assert.match(main, /closeGuard\(\{ before: \(\) => editor\.closing\(\)/, "the window close waits for an editor close in progress");
  // every save goes through the saver (a direct save() would neither be retried nor seen by a close)
  assert.doesNotMatch(view, /void save\(\)/);
  assert.equal((view.match(/\bsave\(\)/g) ?? []).length, 2, "save() is defined once and called only by the Autosaver");
  // a reload of the same video carries an unsaved change instead of asking, and does not undo a later exit
  const reopen = view.slice(view.indexOf("async function reopenSame("), view.indexOf("/** The documentary's split layers back"));
  assert.match(reopen, /close\(false, true, true\)/);
  assert.match(reopen, /if \(exits !== exit\) return;/);
  assert.equal((view.match(/reopenSame\(/g) ?? []).length, 4, "the three documentary reloads use it");
  assert.doesNotMatch(view, /reload: async/, "no reload of its own");
  assert.match(view, /if \(carry\) loaded\.project = carry\.project;/);
  // a refused close from a queue selection is not asked again on every render
  assert.match(view, /if \(stayedFor !== id\) void close\(false\)\.then\(\(left\) => \{ if \(!left && session\) stayedFor = id; \}\);/);
  const capabilities = JSON.parse(readFileSync(new URL("../src-tauri/capabilities/default.json", import.meta.url), "utf8"));
  assert.ok(capabilities.permissions.includes("core:window:allow-destroy"), "a close request the guard lets through can end the window");
}

console.log("edit autosave tests passed");

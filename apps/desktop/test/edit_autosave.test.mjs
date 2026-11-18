import assert from "node:assert/strict";
import { Autosaver, DraftSavers, MAX_WAIT_MS, QUIET_MS, RETRY_FIRST_MS, RETRY_MAX_MS, retryDelay, saveDelay } from "../src/features/edit/autosave.ts";
import { askAlone, CLOSE_EVENT, CLOSE_LISTENERS_KEY, CLOSE_WAIT_MS, closeGuard, closeRequests, EDITOR_HELD, EDITOR_LEFT, editorGuard, flushWithin,
  pageQuestions, StaleCloseListeners, storageIds, unsavedPrompt, wireWindowClose } from "../src/features/edit/close_guard.ts";

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
  // when the next timer is due (Infinity: none)
  const nextDue = () => Math.min(Infinity, ...[...due.values()].map((timer) => timer.at));
  return { timers, advance, nextDue };
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

// The Lead's exact order (review comment 4606816229): write #1 starts at 300, an edit at 400 sets its timer for 700,
// the timer fires while write #1 still runs and queues a successor, write #1 fails at 800. The successor does not run
// when write #1 settles: the next try is at 1800, the retry's own.
{
  const { timers, advance } = clock();
  const attempts = [];
  let release;
  const saver = new Autosaver(() => new Promise((resolve, reject) => {
    attempts.push(timers.now());
    release = () => reject(new Error("held"));
  }), timers);
  saver.changed();
  await advance(QUIET_MS);
  assert.deepEqual(attempts, [300]);
  await advance(100);
  saver.changed();  // t=400: due at 700
  await advance(300);  // t=700: the timer fires while write #1 runs, its write waits behind it
  assert.deepEqual(attempts, [300], "nothing overlaps write #1");
  await advance(100);
  release();  // t=800: write #1 fails; due again at 1800
  await advance(0);
  assert.deepEqual(attempts, [300], "the queued write does not run when write #1 settles");
  await advance(RETRY_FIRST_MS - 1);
  assert.deepEqual(attempts, [300], "nor before 1800");
  await advance(1);
  assert.deepEqual(attempts, [300, 1800]);
  saver.drop();
}

// An edit during a backoff does not even set a timer before the retry is due (no early wake that has to wait again).
{
  const { timers, advance, nextDue } = clock();
  const saver = new Autosaver(async () => { throw new Error("held"); }, timers);
  saver.changed();
  await advance(QUIET_MS);  // fails at 300: due again at 1300
  await advance(100);
  saver.changed();
  assert.equal(nextDue(), QUIET_MS + RETRY_FIRST_MS);
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
  // nor can the old write land over the new one: the host runs draft saves one after another, in the order asked (a
  // synchronous command runs on its main thread), so the new draft is written after the old one ends, or not at all
  const { readFileSync } = await import("node:fs");
  const edit = readFileSync(new URL("../src-tauri/src/edit.rs", import.meta.url), "utf8");
  assert.match(edit, /#\[tauri::command\]\r?\npub fn edit_project_save\(/, "edit_project_save is a synchronous command");
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

// DraftSavers (Issue #929): one saver per video, writing its open editor's draft, else the copy a reload carried. In
// the model, as in the editor (view.ts), a video's writes go to its open editor's draft when it is open, else to its copy.
function model() {
  const { timers, advance, nextDue } = clock();
  const held = new Set();  // drafts another program holds: writing them fails
  const hangs = new Map();  // a write of that draft does not end until the promise does (once)
  const disk = new Map();
  const tries = [];  // `${time} ${video}` for every write of a draft
  const running = new Map();
  const state = { open: null, during: null, most: 0 };
  const writeOne = async (outputPath, project) => {
    tries.push(`${timers.now()} ${outputPath}`);
    running.set(outputPath, (running.get(outputPath) ?? 0) + 1);
    state.most = Math.max(state.most, running.get(outputPath));
    try {
      const during = state.during;
      state.during = null;
      during?.();
      const hang = hangs.get(outputPath);
      hangs.delete(outputPath);
      if (hang) await hang;
      if (held.has(outputPath)) throw new Error(`${outputPath} held`);
      disk.set(outputPath, project);
    } finally {
      running.set(outputPath, running.get(outputPath) - 1);
    }
  };
  const drafts = new DraftSavers(async (outputPath, copy) => {
    if (state.open?.outputPath === outputPath) await writeOne(outputPath, state.open.project);
    else if (copy) await writeOne(outputPath, copy.project);
  }, timers);
  return { timers, advance, nextDue, held, hangs, disk, tries, state, drafts };
}
// The Lead's case: video A's change, carried by a reload, has failed through the 1 s and 2 s backoffs; the user opens
// B, edits it, and chooses "Vẫn thoát" while B cannot be saved either. B's change goes; A's stays pending and is not
// tried before its retry is due.
{
  const { advance, nextDue, held, disk, tries, state, drafts } = model();
  held.add("A").add("B");
  state.open = { outputPath: "A", project: "A1" };
  drafts.changed("A");
  await advance(QUIET_MS);  // 300: fails, due again at 1300
  drafts.carry({ outputPath: "A", project: "A1" });  // a reload of A; nothing reopens it
  state.open = null;
  await advance(1000);  // 1300: fails again, due at 3300
  await advance(200);
  state.open = { outputPath: "B", project: "B1" };  // 1500: B opened and edited
  drafts.changed("B");
  await advance(QUIET_MS);  // 1800: B fails
  await advance(100);
  assert.equal(await drafts.flush("B"), false, "the close writes B now: it fails");  // 1900
  assert.deepEqual(tries, ["300 A", "1300 A", "1800 B", "1900 B"], "the editor's close tries only its own video");
  assert.deepEqual(drafts.unsaved(), ["A", "B"]);
  drafts.drop("B");  // Vẫn thoát (view.ts discard)
  state.open = null;
  assert.deepEqual(drafts.unsaved(), ["A"], "A's change is still pending");
  assert.equal(nextDue(), 3300, "and is not tried before its retry is due");
  await advance(3300 - 1900 - 1);
  assert.equal(tries.length, 4);
  await advance(1);
  assert.deepEqual(tries.slice(4), ["3300 A"], "tried again when due, and B's change is not written");
  held.delete("A");  // the other program lets A go: due again at 3300 + 4000
  await advance(4000);
  assert.deepEqual(tries.slice(5), ["7300 A"]);
  assert.equal(disk.get("A"), "A1");
  assert.equal(disk.has("B"), false, "B's change went, as the user chose");
  assert.deepEqual(drafts.unsaved(), []);
  assert.equal(drafts.get("A"), undefined, "the copy written goes");
  assert.equal(nextDue(), Infinity);
  assert.equal(state.most, 1, "writes of one draft never overlap");
}
// round 6: a held copy of A does not slow the open video's saves: B's edits reach the disk within MAX_WAIT_MS
{
  const { timers, advance, held, disk, tries, state, drafts } = model();
  held.add("A");
  state.open = { outputPath: "A", project: "A1" };
  drafts.changed("A");
  await advance(QUIET_MS);
  drafts.carry({ outputPath: "A", project: "A1" });
  state.open = null;
  await advance(120_000);  // A has backed off to RETRY_MAX_MS
  state.open = { outputPath: "B", project: "B0" };
  for (let i = 1; i <= 20; i += 1) {
    state.open.project = `B${i}`;
    drafts.changed("B");
    const at = timers.now();
    await advance(MAX_WAIT_MS);
    assert.equal(disk.get("B"), `B${i}`, `B's edit ${i} on disk within MAX_WAIT_MS of ${at}`);
  }
  assert.deepEqual(drafts.unsaved(), ["A"], "A still pending");
  assert.ok(tries.filter((entry) => entry.endsWith(" A")).length < 12, "A on its own backoff");
}
// a write of A under way when B's change goes (the close gave up waiting) is still waited for by A's saver: nothing of
// A starts beside it, and when it fails A is tried again after its backoff
{
  const { advance, nextDue, held, hangs, tries, state, drafts } = model();
  held.add("A").add("B");
  state.open = { outputPath: "A", project: "A1" };
  drafts.changed("A");
  await advance(QUIET_MS);  // 300: A fails
  drafts.carry({ outputPath: "A", project: "A1" });  // due at 1300
  state.open = { outputPath: "B", project: "B1" };
  drafts.changed("B");
  let release;
  hangs.set("A", new Promise((resolve) => { release = resolve; }));
  await advance(1000);  // 600: B fails; 1300: the retry of A starts and does not end
  assert.deepEqual(tries, ["300 A", "600 B", "1300 A"]);
  drafts.drop("B");  // Vẫn thoát on B
  state.open = null;
  await advance(RETRY_MAX_MS);
  assert.deepEqual(tries, ["300 A", "600 B", "1300 A"], "nothing of A starts beside the write under way, nothing of B at all");
  release();
  await advance(0);  // 31300: it fails, the second failure: due 2 s later
  assert.equal(nextDue(), 31300 + 2 * RETRY_FIRST_MS);
  held.delete("A");
  await advance(2 * RETRY_FIRST_MS);
  assert.deepEqual(tries.slice(3), ["33300 A"]);
  assert.deepEqual(drafts.unsaved(), []);
  assert.equal(state.most, 1);
}
// with no other video's change left, Vẫn thoát leaves nothing to try again
{
  const { advance, nextDue, held, tries, state, drafts } = model();
  held.add("B");
  state.open = { outputPath: "B", project: "B1" };
  drafts.changed("B");
  await advance(QUIET_MS);
  drafts.drop("B");
  state.open = null;
  assert.deepEqual(drafts.unsaved(), []);
  assert.equal(nextDue(), Infinity);
  assert.equal(await drafts.flushAll(), true, "nothing left: a window close does not ask");
  await advance(RETRY_MAX_MS);
  assert.deepEqual(tries, ["300 B"]);
}
// letting a video's change go lets its copy go too, and no other
{
  const { advance, nextDue, held, tries, drafts } = model();
  held.add("B").add("C");
  drafts.carry({ outputPath: "B", project: "B1" });
  drafts.carry({ outputPath: "C", project: "C1" });
  await advance(QUIET_MS);  // both fail: due at 1300
  drafts.drop("B");
  assert.equal(drafts.get("B"), undefined);
  assert.equal(drafts.get("C")?.project, "C1");
  assert.deepEqual(drafts.unsaved(), ["C"]);
  assert.equal(nextDue(), 1300);
  await advance(1000);
  assert.deepEqual(tries, ["300 B", "300 C", "1300 C"]);
  drafts.drop("C");
  assert.equal(nextDue(), Infinity, "nothing left to try");
}
// a newer change of a video whose copy is pending replaces it, with the backoff that copy had
{
  const { advance, nextDue, held, disk, tries, drafts } = model();
  held.add("A");
  const a1 = { outputPath: "A", project: "A1" }, a2 = { outputPath: "A", project: "A2" };
  drafts.carry(a1);
  await advance(QUIET_MS);  // 300: fails, due at 1300
  drafts.carry(a2);
  assert.equal(nextDue(), 1300, "not tried sooner for being newer");
  assert.equal(drafts.take(a1), false, "the older copy is not pending any more");
  held.delete("A");
  await advance(1000);
  assert.deepEqual(tries, ["300 A", "1300 A"]);
  assert.equal(disk.get("A"), "A2");
}
// a reopened editor takes its copy up and goes on through the same saver: on the copy's backoff, never sooner; another
// video's copy is written on its own meanwhile
{
  const { advance, nextDue, held, disk, tries, state, drafts } = model();
  held.add("A");
  state.open = { outputPath: "A", project: "A1" };
  drafts.changed("A");
  await advance(QUIET_MS);  // 300: fails
  const a1 = { outputPath: "A", project: "A1" };
  drafts.carry(a1);
  drafts.carry({ outputPath: "C", project: "C1" });
  state.open = null;
  await advance(1000);  // 600: C written; 1300: A fails, due at 3300
  assert.equal(disk.get("C"), "C1");
  assert.equal(drafts.get("C"), undefined);
  await advance(200);
  state.open = { outputPath: "A", project: "A1" };  // 1500: the editor of A opens with the copy
  assert.equal(drafts.take(a1), true);
  assert.equal(drafts.take(a1), false, "taken once");
  assert.equal(drafts.get("A"), undefined);
  assert.deepEqual(drafts.unsaved(), ["A"], "still pending: the editor goes on with it");
  assert.equal(nextDue(), 3300, "on the copy's backoff");
  drafts.changed("A");
  assert.equal(nextDue(), 3300, "an edit does not bring it forward");
  held.delete("A");
  await advance(1800);
  assert.deepEqual(tries, ["300 A", "600 C", "1300 A", "3300 A"], "written once, by the editor");
  assert.equal(disk.get("A"), "A1");
  assert.deepEqual(drafts.unsaved(), []);
}
// review round 7: the editor takes the copy while the copy's write is under way, and that write fails: the change is
// still pending (a window close is not told the disk has it) and the retry writes the editor's draft
{
  const { advance, held, hangs, disk, tries, state, drafts } = model();
  held.add("A");
  const a1 = { outputPath: "A", project: "A1" };
  drafts.carry(a1);
  let release;
  hangs.set("A", new Promise((resolve) => { release = resolve; }));
  await advance(QUIET_MS);  // 300: the copy's write starts and waits
  state.open = { outputPath: "A", project: "A1" };
  assert.equal(drafts.take(a1), true);
  release();
  await advance(0);  // it fails
  assert.deepEqual(drafts.unsaved(), ["A"]);
  assert.equal(await drafts.flushAll(), false, "a window close asks");
  state.open.project = "A2";
  held.delete("A");
  await advance(RETRY_MAX_MS);
  assert.equal(disk.get("A"), "A2", "the retry writes the open editor's draft");
  assert.deepEqual(drafts.unsaved(), []);
  assert.equal(await drafts.flushAll(), true);
  assert.equal(tries.length, 3);
}
// ... and when it works, an edit made in the editor meanwhile is still written after it
{
  const { advance, hangs, disk, tries, state, drafts } = model();
  const a1 = { outputPath: "A", project: "A1" };
  drafts.carry(a1);
  let release;
  hangs.set("A", new Promise((resolve) => { release = resolve; }));
  await advance(QUIET_MS);  // 300: the copy's write starts and waits
  state.open = { outputPath: "A", project: "A2" };
  assert.equal(drafts.take(a1), true);
  drafts.changed("A");
  release();
  await advance(0);
  assert.equal(disk.get("A"), "A1");
  assert.deepEqual(drafts.unsaved(), ["A"], "the edit made during the write is not on disk yet");
  await advance(QUIET_MS);
  assert.equal(disk.get("A"), "A2");
  assert.deepEqual(drafts.unsaved(), []);
  assert.deepEqual(tries, ["300 A", "600 A"]);
}
// one written is let go, unless a newer one replaced it meanwhile; one forgotten (the disk has a newer draft) is never
// written
{
  const { advance, disk, state, drafts } = model();
  const a2 = { outputPath: "A", project: "A2" }, a3 = { outputPath: "A", project: "A3" };
  drafts.carry(a2);
  state.during = () => drafts.carry(a3);  // a reload carries a newer change while A2 is written
  await advance(QUIET_MS);
  assert.equal(disk.get("A"), "A2");
  assert.equal(drafts.get("A"), a3, "the newer copy stays");
  await advance(QUIET_MS);
  assert.equal(disk.get("A"), "A3");
  assert.equal(drafts.get("A"), undefined);
  drafts.carry(a2);
  drafts.forget("A");
  await advance(RETRY_MAX_MS);
  assert.equal(disk.get("A"), "A3");
  assert.deepEqual(drafts.unsaved(), []);
  // only the copy goes: a change of an editor of that video is still written
  state.open = { outputPath: "A", project: "A4" };
  drafts.changed("A");
  drafts.carry(a2);
  drafts.forget("A");
  await advance(QUIET_MS);
  assert.equal(disk.get("A"), "A4");
}
// a write the close gave up on (still running) holds up the video's next one, never runs beside it; once it ends the
// copy is written
{
  const { advance, disk, hangs, tries, state, drafts } = model();
  let release;
  state.open = { outputPath: "A", project: "A1" };
  hangs.set("A", new Promise((resolve) => { release = resolve; }));
  drafts.changed("A");
  await advance(QUIET_MS);  // 300: the write of A1 does not end
  drafts.carry({ outputPath: "A", project: "A2" });  // the close gave up on it and carried the change
  state.open = null;
  await advance(QUIET_MS);
  assert.deepEqual(tries, ["300 A"]);
  release();
  await advance(0);
  assert.deepEqual(tries, ["300 A", "600 A"]);
  assert.equal(disk.get("A"), "A2");
  assert.deepEqual(drafts.unsaved(), []);
  assert.equal(state.most, 1);
}
// flushAll writes every video's draft now and says whether one is left; flush writes one video's only
{
  const { disk, tries, state, drafts } = model();
  drafts.carry({ outputPath: "X", project: "X1" });
  drafts.carry({ outputPath: "Y", project: "Y1" });
  state.open = { outputPath: "Z", project: "Z1" };
  drafts.changed("Z");
  assert.equal(await drafts.flush("Y"), true);
  assert.deepEqual(tries, ["0 Y"], "only Y");
  assert.equal(await drafts.flush("W"), true, "nothing of W pending");
  assert.equal(await drafts.flushAll(), true);
  assert.deepEqual([disk.get("X"), disk.get("Y"), disk.get("Z")], ["X1", "Y1", "Z1"]);
}
{
  const { held, disk, drafts } = model();
  held.add("X");
  drafts.carry({ outputPath: "X", project: "X1" });
  drafts.carry({ outputPath: "Y", project: "Y1" });
  assert.equal(await drafts.flushAll(), false, "X is held");
  assert.equal(disk.get("Y"), "Y1");
  assert.deepEqual(drafts.unsaved(), ["X"]);
  held.delete("X");
  assert.equal(await drafts.flushAll(), true);
  assert.equal(disk.get("X"), "X1");
}
// round 8: a change made while the close waits for another video's write (B edited, a new copy of C) is written before
// the close is told the disk has every change
{
  const { timers, advance, hangs, disk, state, drafts } = model();
  let release;
  drafts.carry({ outputPath: "A", project: "A1" });
  hangs.set("A", new Promise((resolve) => { release = resolve; }));
  state.open = { outputPath: "B", project: "B1" };
  drafts.changed("B");
  const closing = flushWithin(() => drafts.flushAll(), CLOSE_WAIT_MS, timers);
  await advance(0);
  assert.equal(disk.get("B"), "B1");
  state.open.project = "B2";
  drafts.changed("B");
  drafts.carry({ outputPath: "C", project: "C1" });
  release();
  await advance(0);
  assert.equal(await closing, true);
  assert.deepEqual([disk.get("A"), disk.get("B"), disk.get("C")], ["A1", "B2", "C1"]);
  assert.deepEqual(drafts.unsaved(), []);
}
// ... and when changes of other videos keep coming (each video's own flush done), it answers false after a few rounds
{
  const { state, drafts } = model();
  let n = 0;
  const ping = () => { n += 1; const other = n % 2 ? "C" : "B"; drafts.carry({ outputPath: other, project: `${other}${n}` }); state.during = ping; };
  drafts.carry({ outputPath: "B", project: "B0" });
  state.during = ping;
  assert.equal(await drafts.flushAll(), false);
  assert.ok(n >= 3, `three rounds: ${n}`);
  state.during = null;
}
// ... and so when each round brings a change of a video not flushed yet (every flush of the round done)
{
  const { state, drafts } = model();
  let n = 0;
  const next = () => { n += 1; drafts.carry({ outputPath: `V${n}`, project: `V${n}` }); state.during = next; };
  drafts.carry({ outputPath: "V0", project: "V0" });
  state.during = next;
  assert.equal(await drafts.flushAll(), false);
  assert.equal(n, 3, "three rounds");
  assert.deepEqual(drafts.unsaved(), ["V3"]);
  state.during = null;
}
// ... and when changes keep coming, it answers false after a few rounds (the close then asks)
{
  const { state, drafts } = model();
  state.open = { outputPath: "B", project: "B0" };
  let edits = 0;
  state.during = function again() { edits += 1; state.open.project = `B${edits}`; drafts.changed("B"); state.during = again; };
  drafts.changed("B");
  assert.equal(await drafts.flushAll(), false);
  state.during = null;
}
// The Lead's case (4607766663): A's change carried by a reload, B open, and B's write hangs. Closing the window tries
// every video at once within CLOSE_WAIT_MS: A reaches the disk though B never ends; asked, the question names B.
{
  const { timers, advance, held, hangs, disk, tries, state, drafts } = model();
  held.add("A");
  drafts.carry({ outputPath: "A", project: "A1" });
  await advance(QUIET_MS);  // 300: A fails, due at 1300
  held.delete("A");  // let go, but its retry is not due yet
  state.open = { outputPath: "B", project: "B1" };
  hangs.set("B", new Promise(() => {}));  // B's write never ends
  drafts.changed("B");
  const closing = flushWithin(() => drafts.flushAll(), CLOSE_WAIT_MS, timers);
  await advance(0);
  assert.equal(disk.get("A"), "A1", "A is written now, beside B's write");
  assert.ok(tries.includes("300 B"));
  await advance(CLOSE_WAIT_MS);
  assert.equal(await closing, false, "B is not on disk: the close asks");
  assert.deepEqual(drafts.unsaved(), ["B"], "and names B only");
}
// ... whichever video comes first: the open video's write hangs and a copy of another is written all the same
{
  const { timers, advance, held, hangs, disk, state, drafts } = model();
  state.open = { outputPath: "B", project: "B1" };
  hangs.set("B", new Promise(() => {}));
  drafts.changed("B");
  held.add("A");
  drafts.carry({ outputPath: "A", project: "A1" });
  await advance(QUIET_MS);  // 300: B's write starts and never ends; A fails
  held.delete("A");
  const closing = flushWithin(() => drafts.flushAll(), CLOSE_WAIT_MS, timers);
  await advance(0);
  assert.equal(disk.get("A"), "A1");
  await advance(CLOSE_WAIT_MS);
  assert.equal(await closing, false);
  assert.deepEqual(drafts.unsaved(), ["B"]);
}
// ... and with A still held as well, both are tried and both named
{
  const { timers, advance, held, hangs, tries, state, drafts } = model();
  held.add("A");
  drafts.carry({ outputPath: "A", project: "A1" });
  await advance(QUIET_MS);  // 300: A fails
  state.open = { outputPath: "B", project: "B1" };
  hangs.set("B", new Promise(() => {}));
  drafts.changed("B");
  const closing = flushWithin(() => drafts.flushAll(), CLOSE_WAIT_MS, timers);
  await advance(0);
  assert.deepEqual(tries.filter((entry) => entry === "300 A").length, 2, "A tried again at once, not after its backoff");
  await advance(CLOSE_WAIT_MS);
  assert.equal(await closing, false);
  assert.deepEqual(drafts.unsaved(), ["A", "B"]);
  const prompt = unsavedPrompt("close", false, drafts.unsaved());
  assert.match(prompt.message, /của 2 video chưa lưu được vào ổ đĩa \(“A”, “B”\)/);
  assert.match(prompt.message, /sẽ mất tất cả các thay đổi đó\./);
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

// The window close names every video whose change would go (the Lead, 4607766663): one by name, several counted and
// named, five at most, a long name cut.
{
  const one = unsavedPrompt("close", false, ["Video A"]);
  assert.match(one.message, /^Thay đổi trong Chỉnh sửa của video “Video A” chưa lưu được vào ổ đĩa/);
  assert.match(one.message, /Đóng DubFlow bây giờ sẽ mất thay đổi đó\./);
  const many = unsavedPrompt("close", false, ["1", "2", "3", "4", "5", "6", "7"]);
  assert.match(many.message, /^Thay đổi trong Chỉnh sửa của 7 video chưa lưu được vào ổ đĩa \(“1”, “2”, “3”, “4”, “5”, 2 video khác\)/);
  assert.doesNotMatch(many.message, /“6”/);
  assert.match(many.message, /sẽ mất tất cả các thay đổi đó\./);
  const five = unsavedPrompt("close", false, ["1", "2", "3", "4", "5"]);
  assert.match(five.message, /\(“1”, “2”, “3”, “4”, “5”\)/);
  const long = unsavedPrompt("close", false, ["x".repeat(60), "y".repeat(61)]);
  assert.ok(long.message.includes(`“${"x".repeat(60)}”`), "60 characters fit");
  assert.ok(long.message.includes(`“${"y".repeat(59)}…”`), "a longer name is cut");
  assert.match(unsavedPrompt("close").message, /^Thay đổi mới nhất trong Chỉnh sửa chưa lưu được vào ổ đĩa/);
  assert.match(unsavedPrompt("exit", true).message, /Thoát chỉnh sửa bây giờ sẽ mất thay đổi đó và các thay đổi chưa bấm Áp dụng\./);
}

// The × button and the system's close (Alt+F4, the taskbar) both go through the guard.
{
  // The stand-ins below wait on a real timer for each call to the host (as IPC would); `settle` waits until every one
  // they started has ended and nothing more follows, however long a timer takes (Windows rounds a 1 ms timer up to its
  // 15.6 ms tick, so a fixed wait was too short there for a close that removes several listeners one after another).
  let inflight = 0;
  const later = (ms) => {
    inflight += 1;
    return new Promise((resolve) => setTimeout(() => { inflight -= 1; resolve(); }, ms));
  };
  const settle = async () => {
    for (let idle = 0, turns = 0; idle < 3; turns += 1) {
      assert.ok(turns < 5000, "the host calls end");
      await new Promise((resolve) => (inflight ? setTimeout(resolve, 1) : setImmediate(resolve)));
      idle = inflight ? 0 : idle + 1;
    }
  };
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
          await later(2);
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

  for (const via of ["button", "system"]) {
    let flushed = 0, answer = false, asked = 0;
    const { window, state } = fakeWindow();
    const button = fakeButton();
    const { timers } = clock();
    const mayClose = closeGuard({ flush: async () => { flushed += 1; return false; }, ask: async () => { asked += 1; return answer; }, timers });
    assert.equal(await wireWindowClose(window, button, mayClose).ready(), true, "the guard is in place");
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
    assert.equal(await wireWindowClose(window, button, closeGuard({ flush: async () => true, ask: async () => { asked += 1; return false; }, timers })).ready(), true);
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
    const wiring = wireWindowClose(window, button, closeGuard({ flush: async () => false, ask: async () => { asked += 1; return true; }, timers }));
    button.click();
    await settle();
    assert.equal(state.closed, 0, "not before the listener is in place");
    arm();
    assert.equal(await wiring.ready(), true);
    await settle();
    assert.equal(state.closed, 1);
    assert.equal(asked, 1, "one question");
  }
  // the listener cannot start: the guard says so, so the editor does not open (see the view checks below) and no
  // draft can be pending; with nothing to save, × and the system close (unguarded now) both still close
  for (const via of ["button", "system"]) {
    const { window, state } = fakeWindow({ listen: false });
    const button = fakeButton();
    const { timers } = clock();
    let flushed = 0;
    assert.equal(await wireWindowClose(window, button, closeGuard({ flush: async () => { flushed += 1; return true; }, ask: async () => false, timers })).ready(), false);
    assert.equal(state.handlers.length, 0, "no listener left in place");
    if (via === "button") button.click();
    else void window.close();
    await settle();
    assert.equal(state.closed, 1, `${via}: closes`);
    assert.equal(flushed, via === "button" ? 1 : 0, `${via}: the × button still writes the draft first; the system close cannot be seen`);
  }

  // A reload (F5, Ctrl+R, Tải lại) leaves the page's close listener in Tauri, which then holds every close for a
  // handler that no longer runs: the page after it removes that listener before it listens itself.
  // A stand-in for Tauri 2.12's listener registry: ids come from one counter for every listener of any event (as
  // next_event_id), a listener is in place before its id comes back, and unlisten removes only a listener of that
  // event with that id (Listeners::unlisten_js), answering Ok when there is none.
  function fakeTauri({ failUnlisten = () => false } = {}) {
    const state = { listeners: new Map(), next: 0, closed: 0, calls: [], dropReplies: false };
    const bridge = {
      async listen(event, label, handler) {
        await later(1);
        const id = state.next++;
        state.listeners.set(id, { event, label, handler });
        state.calls.push(`listen ${id}`);
        if (state.dropReplies) return new Promise(() => {});  // the page reloaded before the reply came back
        return id;
      },
      async unlisten(event, id) {
        await later(1);
        if (failUnlisten(id)) throw new Error("ipc");
        if (state.listeners.get(id)?.event === event) {
          state.calls.push(`unlisten ${id}`);
          state.listeners.delete(id);
        }
      },
    };
    const other = (event) => { const id = state.next++; state.listeners.set(id, { event, label: "main", handler: () => {} }); return id; };
    // as in tauri's manager/window.rs: held while any close listener is registered, and each listener is told
    const close = async () => {
      await null;
      const listeners = [...state.listeners.values()].filter((listener) => listener.event === CLOSE_EVENT);
      if (!listeners.length) { state.closed += 1; return; }
      for (const listener of listeners) listener.handler();
    };
    const reload = () => { for (const listener of state.listeners.values()) listener.handler = () => {}; };  // nothing runs them
    return { state, bridge, close, other, reload };
  }
  function memoryStore(initial = null) {
    let value = initial;
    return { get: () => value, set: (next) => { value = next; } };
  }
  const kept = (store) => (store.get() === null ? null : JSON.parse(store.get()));
  const closeListeners = (tauri) => [...tauri.state.listeners.entries()].filter(([, l]) => l.event === CLOSE_EVENT).map(([id]) => id);
  const page = (tauri, store, button = fakeButton()) => {
    const { timers } = clock();
    return wireWindowClose({ close: tauri.close, onCloseRequested: closeRequests("main", tauri.bridge, store) }, button,
      closeGuard({ flush: async () => true, ask: async () => false, timers })).ready();
  };
  {
    const store = memoryStore();
    const tauri = fakeTauri();
    await page(tauri, store);
    assert.deepEqual(kept(store), { ids: [0], from: 1 }, "the listener's id is kept where a reload finds it, and every id below it is accounted for");
    tauri.reload();
    const button = fakeButton();
    assert.equal(await page(tauri, store, button), true);
    assert.deepEqual(tauri.state.calls, ["listen 0", "unlisten 0", "listen 1"], "the old listener goes before the new one starts");
    assert.equal(tauri.state.listeners.get(1).label, "main");
    assert.deepEqual(kept(store), { ids: [1], from: 2 });
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1, "the window still closes after a reload");
    assert.deepEqual(kept(store), { ids: [], from: 2 }, "nothing left to remove");
  }
  // the page reloads while its listen is under way: the listener is in place, its id never comes back; the next
  // page removes every close listener from where that listen started up to its own, and nothing else
  {
    const store = memoryStore();
    const tauri = fakeTauri();
    await page(tauri, store);  // close listener 0
    tauri.reload();
    tauri.other("tauri://resize");  // 1: another event's listener
    tauri.state.dropReplies = true;
    void page(tauri, store);  // its listen registers 2, and the page reloads before the reply
    await settle();
    tauri.state.dropReplies = false;
    assert.deepEqual(closeListeners(tauri), [2], "the earlier page's listener went; the lost one is in place");
    assert.equal(kept(store).from, 1, "where the lost listen started is kept");
    tauri.reload();
    const resize = tauri.other("tauri://resize");  // 3
    const button = fakeButton();
    assert.equal(await page(tauri, store, button), true);  // 4
    assert.deepEqual(closeListeners(tauri), [4], "the lost listener is removed");
    assert.ok(tauri.state.listeners.has(1) && tauri.state.listeners.has(resize), "other events' listeners stay");
    assert.deepEqual(kept(store), { ids: [4], from: 5 });
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1, "the window closes");
  }
  // round 9: a reload mid-sweep (an unlisten that never answers) leaves the mark where it was, so the page after sweeps
  // from there: a close listener nothing names (the store never took a write) still goes, also when a reload mid-listen
  // follows
  for (const thenLost of [false, true]) {
    const store = memoryStore();
    const tauri = fakeTauri();
    tauri.other(CLOSE_EVENT);  // 0: a close listener the store does not name
    const hanging = { listen: tauri.bridge.listen, unlisten: (event, id) => (id === 0 ? new Promise(() => {}) : tauri.bridge.unlisten(event, id)) };
    void closeRequests("main", hanging, store)(async () => {});  // listens as 1; its removal of 0 never answers
    await settle();
    tauri.reload();
    assert.deepEqual(closeListeners(tauri), [0, 1]);
    if (thenLost) {
      tauri.state.dropReplies = true;
      void page(tauri, store);  // listens as 2, and the page reloads before the reply
      await settle();
      tauri.state.dropReplies = false;
      tauri.reload();
    }
    const button = fakeButton();
    assert.equal(await page(tauri, store, button), true, `${thenLost}: ready`);
    const own = thenLost ? 3 : 2;
    assert.deepEqual(closeListeners(tauri), [own], `${thenLost}: every one left behind went`);
    assert.deepEqual(kept(store), { ids: [own], from: own + 1 });
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1, `${thenLost}: the window closes`);
  }
  // a removal that fails is kept, and tried again before the next listener: from the close (stop) and from the start;
  // while it is there the new listener is in place but not ready (StaleCloseListeners)
  {
    const store = memoryStore();
    const failing = new Set([0]);
    const tauri = fakeTauri({ failUnlisten: (id) => failing.has(id) });
    const onCloseRequested = closeRequests("main", tauri.bridge, store);
    const stop = await onCloseRequested(async () => {});
    await assert.rejects(Promise.resolve(stop()));
    assert.deepEqual(kept(store), { ids: [0], from: 1 });
    await assert.rejects(onCloseRequested(async () => {}), StaleCloseListeners, "0 still fails: not ready");
    assert.deepEqual(kept(store), { ids: [0, 1], from: 2 }, "kept with the new one");
    assert.deepEqual(closeListeners(tauri), [0, 1], "the new one is in place: every close still reaches the guard");
    tauri.reload();
    failing.clear();
    await closeRequests("main", tauri.bridge, store)(async () => {});
    assert.deepEqual(tauri.state.calls.filter((call) => call.startsWith("unlisten")), ["unlisten 0", "unlisten 1"]);
    assert.deepEqual(closeListeners(tauri), [2]);
    assert.deepEqual(kept(store), { ids: [2], from: 3 });
  }
  // the Lead's case: a reload left a listener that cannot be removed while the guard starts. The guard is not ready,
  // so the editor stays closed (the view checks closeGuarded below); a close is not lost to the leftover listener: it
  // reaches the guard, which removes the leftover first and then closes; while it still cannot go, the window stays
  // open and guarded, and the next close tries again
  for (const via of ["button", "system"]) {
    const store = memoryStore();
    const failing = new Set();
    const tauri = fakeTauri({ failUnlisten: (id) => failing.has(id) });
    await page(tauri, store);  // 0
    tauri.reload();
    failing.add(0);
    const button = fakeButton();
    assert.equal(await page(tauri, store, button), false, `${via}: not ready while the leftover listener is there`);
    assert.deepEqual(closeListeners(tauri), [0, 1]);
    const request = () => (via === "button" ? button.click() : void tauri.close());
    request();
    await settle();
    assert.equal(tauri.state.closed, 0, `${via}: the leftover still cannot go: the window stays open`);
    assert.deepEqual(closeListeners(tauri), [0, 1], `${via}: and guarded`);
    failing.clear();
    request();
    await settle();
    assert.deepEqual(tauri.state.calls.filter((call) => call.startsWith("unlisten")), ["unlisten 0", "unlisten 1"],
      `${via}: the leftover goes first, then the guard's own`);
    assert.deepEqual(closeListeners(tauri), []);
    assert.equal(tauri.state.closed, 1, `${via}: the close completes after the cleanup`);
    assert.deepEqual(kept(store), { ids: [], from: 2 });
  }
  // the same when the leftover is one whose id never came back (a reload mid-listen): not ready until it is removed
  {
    const store = memoryStore();
    const failing = new Set();
    const tauri = fakeTauri({ failUnlisten: (id) => failing.has(id) });
    await page(tauri, store);  // 0
    tauri.reload();
    tauri.state.dropReplies = true;
    void page(tauri, store);  // 0 goes, 1 is in place and its id never comes back
    await settle();
    tauri.state.dropReplies = false;
    tauri.reload();
    failing.add(1);
    const button = fakeButton();
    assert.equal(await page(tauri, store, button), false, "the lost listener could not be removed: not ready");
    assert.deepEqual(closeListeners(tauri), [1, 2]);
    assert.deepEqual(kept(store), { ids: [1, 2], from: 3 });
    failing.clear();
    button.click();
    await settle();
    assert.deepEqual(closeListeners(tauri), []);
    assert.equal(tauri.state.closed, 1, "closes once the lost listener is removed");
  }
  // a leftover removed by one close is not tried again by the next (its own removal failed in between)
  {
    const store = memoryStore();
    const failing = new Set();
    const tries = [];
    const tauri = fakeTauri({ failUnlisten: (id) => { tries.push(id); return failing.has(id); } });
    await page(tauri, store);  // 0
    tauri.reload();
    failing.add(0);
    const button = fakeButton();
    assert.equal(await page(tauri, store, button), false);  // 1
    failing.clear();
    failing.add(1);
    button.click();
    await settle();
    assert.deepEqual(closeListeners(tauri), [1], "the leftover went; the guard's own could not");
    failing.clear();
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1);
    assert.deepEqual(tries, [0, 0, 1, 1], "0: at the start and by the first close only");
  }
  // × and Alt+F4 at once: both wait for the same answer, and the listener is removed once and the window closed once
  for (const first of ["button", "system"]) {
    const store = memoryStore();
    const tauri = fakeTauri();
    let closes = 0, asked = 0;
    const { timers } = clock();
    const button = fakeButton();
    const ask = async () => { asked += 1; await later(3); return true; };
    assert.equal(await wireWindowClose({ close: () => { closes += 1; return tauri.close(); },
      onCloseRequested: closeRequests("main", tauri.bridge, store) }, button, closeGuard({ flush: async () => false, ask, timers })).ready(), true);
    if (first === "button") { button.click(); void tauri.close(); } else { void tauri.close(); button.click(); }
    await settle();
    assert.equal(asked, 1, `${first} first: one question`);
    assert.deepEqual(tauri.state.calls.filter((call) => call.startsWith("unlisten")), ["unlisten 0"], `${first} first: one unlisten`);
    assert.equal(closes, 1, `${first} first: one window.close()`);
    assert.equal(tauri.state.closed, 1);
  }
  // a store that held something else (nothing this guard writes): the page before may have left a listener nothing
  // names, so every close listener below this page's own is removed (nothing else listens to close requests), and
  // other events' listeners stay; the store holds this page's id again, so the guard is ready
  for (const garbage of ["not json", JSON.stringify([0]), JSON.stringify({ ids: ["0", -1, 2.5, null, { id: 0 }], from: "0" }),
    JSON.stringify({ ids: [0], from: -1 }), JSON.stringify({ ids: [-1] }), JSON.stringify({ ids: [], from: 1.5 }),
    JSON.stringify({ ids: [], from: "x" }), JSON.stringify({ ids: [] }), JSON.stringify({ ids: [], from: -10000 }), JSON.stringify(null),
    JSON.stringify({ from: 0 })]) {
    const store = memoryStore(garbage);
    const tauri = fakeTauri();
    tauri.other(CLOSE_EVENT);  // 0: a listener the page before left
    const resize = tauri.other("tauri://resize");  // 1
    const button = fakeButton();
    assert.equal(await page(tauri, store, button), true, `${garbage}: ready`);
    assert.deepEqual(closeListeners(tauri), [2], `${garbage}: the leftover went`);
    assert.ok(tauri.state.listeners.has(resize), `${garbage}: another event's listener stays`);
    assert.deepEqual(kept(store), { ids: [2], from: 3 });
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1, `${garbage}: the window closes`);
  }
  // the Lead's case (4607766656): a store that refuses (WebStorage denied). The page's listener is in place, so a close
  // still reaches the guard, but the guard is not ready: a reload would leave that listener with nothing naming it, so
  // the editor stays closed. After a reload the page removes every close listener below its own (the one left), and
  // Alt+F4 or × closes the window
  for (const via of ["button", "system"]) {
    const denied = { get: () => { throw new Error("denied"); }, set: () => { throw new Error("denied"); } };
    const tauri = fakeTauri();
    assert.equal(await page(tauri, denied), false, `${via}: not ready, the editor stays closed`);
    assert.deepEqual(closeListeners(tauri), [0], `${via}: in place: every close reaches the guard`);
    tauri.reload();
    const resize = tauri.other("tauri://resize");  // 1
    const button = fakeButton();
    assert.equal(await page(tauri, denied, button), false, `${via}: after the reload: still not ready`);
    assert.deepEqual(closeListeners(tauri), [2], `${via}: the listener the page before left went`);
    assert.ok(tauri.state.listeners.has(resize), `${via}: another event's listener stays`);
    if (via === "button") button.click();
    else void tauri.close();
    await settle();
    assert.equal(tauri.state.closed, 1, `${via}: the window closes`);
    assert.deepEqual(closeListeners(tauri), []);
  }
  // round 8: a store that reads but cannot write (sessionStorage full: QuotaExceededError). Not ready either; after a
  // reload the page finds nothing in it, so it sweeps from the first id: the listener the page before left goes, and
  // Alt+F4 or × closes the window
  for (const via of ["button", "system"]) {
    const tauri = fakeTauri();
    const readOnly = { get: () => null, set: () => { throw new Error("QuotaExceededError"); } };
    assert.equal(await page(tauri, readOnly), false, `${via}: not ready`);
    assert.deepEqual(closeListeners(tauri), [0]);
    tauri.reload();
    const button = fakeButton();
    assert.equal(await page(tauri, readOnly, button), false, `${via}: after the reload: still not ready`);
    assert.deepEqual(closeListeners(tauri), [1], `${via}: the listener the page before left went`);
    if (via === "button") button.click();
    else void tauri.close();
    await settle();
    assert.equal(tauri.state.closed, 1, `${via}: the window closes`);
  }
  // a store that cannot be read but takes writes: not ready (fail closed); every page sweeps from the first id, so
  // after a reload the window still closes
  {
    const tauri = fakeTauri();
    const store = memoryStore();
    const unreadable = { get: () => { throw new Error("SecurityError"); }, set: (value) => store.set(value) };
    assert.equal(await page(tauri, unreadable), false);
    tauri.reload();
    const button = fakeButton();
    assert.equal(await page(tauri, unreadable, button), false);
    assert.deepEqual(closeListeners(tauri), [1]);
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1);
  }
  // round 8: the store refused only the page before's writes (one full moment): the page after finds nothing, sweeps
  // from the first id and is ready, with the leftover gone
  {
    const tauri = fakeTauri();
    const store = memoryStore();
    let refuse = true;
    const flaky = { get: () => store.get(), set: (value) => { if (refuse) throw new Error("QuotaExceededError"); store.set(value); } };
    assert.equal(await page(tauri, flaky), false);
    tauri.reload();
    refuse = false;
    const button = fakeButton();
    assert.equal(await page(tauri, flaky, button), true, "ready: every id below its own was swept");
    assert.deepEqual(closeListeners(tauri), [1]);
    assert.deepEqual(kept(store), { ids: [1], from: 2 });
    void tauri.close();
    await settle();
    assert.equal(tauri.state.closed, 1);
  }
  // round 8: a store that stops taking writes keeps the last mark it took, never one above a listener still in place:
  // a close fails after the guard's listener went (the store then refuses), the guard is put back unrecorded, and the
  // page after a reload still sweeps it
  {
    const tauri = fakeTauri();
    const store = memoryStore();
    let refuse = false, closeFails = true;
    const flaky = { get: () => store.get(), set: (value) => { if (refuse) throw new Error("QuotaExceededError"); store.set(value); } };
    const { timers } = clock();
    const button = fakeButton();
    const wiring = wireWindowClose({ close: async () => { if (closeFails) { refuse = true; throw new Error("close refused"); } return tauri.close(); },
      onCloseRequested: closeRequests("main", tauri.bridge, flaky) }, button, closeGuard({ flush: async () => true, ask: async () => false, timers }));
    assert.equal(await wiring.ready(), true);
    button.click();
    await settle();
    assert.deepEqual(closeListeners(tauri), [1], "put back");
    assert.equal(await wiring.ready(), false, "but not recorded: not ready");
    assert.deepEqual(kept(store), { ids: [], from: 1 }, "the last mark the store took");
    tauri.reload();
    refuse = false;
    closeFails = false;
    const next = fakeButton();
    assert.equal(await page(tauri, store, next), true);
    assert.deepEqual(closeListeners(tauri), [2], "the unrecorded one went");
    next.click();
    await settle();
    assert.equal(tauri.state.closed, 1);
  }
  // the sweep before the answer is bounded (sweepLimit ids below this page's own): the ids further down are not tried
  // yet, so the guard is not ready and they stay marked in the store; a close tries them before this page's own goes,
  // so the window still closes (round 8: with the same bound after a reload too)
  {
    const store = memoryStore("not json");
    const tauri = fakeTauri();
    tauri.other(CLOSE_EVENT);  // 0: left, below the sweep
    for (let i = 0; i < 5; i += 1) tauri.other("tauri://resize");  // 1..5
    const { timers } = clock();
    const bounded = (button) => wireWindowClose({ close: tauri.close, onCloseRequested: closeRequests("main", tauri.bridge, store, 3) }, button,
      closeGuard({ flush: async () => true, ask: async () => false, timers }));
    assert.equal(await bounded(fakeButton()).ready(), false);
    assert.deepEqual(closeListeners(tauri), [0, 6]);
    assert.deepEqual(tauri.state.calls.filter((call) => call.startsWith("unlisten")), [], "3, 4 and 5 tried: none was a close listener");
    assert.deepEqual(kept(store), { ids: [6], from: 0 }, "0 to 2 stay marked");
    tauri.reload();
    const button = fakeButton();
    assert.equal(await bounded(button).ready(), false, "the same bound after a reload: still not ready");
    assert.deepEqual(closeListeners(tauri), [0, 7]);
    assert.deepEqual(kept(store), { ids: [7], from: 0 });
    button.click();
    await settle();
    assert.deepEqual(closeListeners(tauri), [], "the close tried the rest first");
    assert.equal(tauri.state.closed, 1, "and the window closes");
    assert.deepEqual(kept(store), { ids: [], from: 8 });
  }
  // round 9: ... and a reload mid-listen after a bounded sweep keeps the low mark: the page after it still tries the ids
  // the first one left (the lost listen's page wrote nothing)
  {
    const store = memoryStore();
    const tauri = fakeTauri();
    tauri.other(CLOSE_EVENT);  // 0: left, below the sweep
    for (let i = 0; i < 5; i += 1) tauri.other("tauri://resize");  // 1..5
    const { timers } = clock();
    const bounded = (button) => wireWindowClose({ close: tauri.close, onCloseRequested: closeRequests("main", tauri.bridge, store, 3) }, button,
      closeGuard({ flush: async () => true, ask: async () => false, timers }));
    assert.equal(await bounded(fakeButton()).ready(), false);  // 6; 0 to 2 not tried yet
    assert.deepEqual(kept(store), { ids: [6], from: 0 });
    tauri.reload();
    tauri.state.dropReplies = true;
    void bounded(fakeButton());  // 6 goes, 7 is in place and its id never comes back
    await settle();
    tauri.state.dropReplies = false;
    assert.deepEqual(closeListeners(tauri), [0, 7]);
    assert.deepEqual(kept(store), { ids: [6], from: 0 }, "the lost listen's page wrote nothing");
    tauri.reload();
    const button = fakeButton();
    assert.equal(await bounded(button).ready(), false, "0 to 4 still not tried");  // 8: tries 5 to 7
    assert.deepEqual(closeListeners(tauri), [0, 8]);
    button.click();
    await settle();
    assert.deepEqual(closeListeners(tauri), []);
    assert.equal(tauri.state.closed, 1);
  }
  // within the bound: every id is tried, and the guard is ready
  {
    const store = memoryStore("not json");
    const tauri = fakeTauri();
    tauri.other(CLOSE_EVENT);  // 0
    tauri.other("tauri://resize");  // 1
    const { timers } = clock();
    const wiring = wireWindowClose({ close: tauri.close, onCloseRequested: closeRequests("main", tauri.bridge, store, 2) }, fakeButton(),
      closeGuard({ flush: async () => true, ask: async () => false, timers }));
    assert.equal(await wiring.ready(), true);
    assert.deepEqual(closeListeners(tauri), [2]);
    assert.deepEqual(kept(store), { ids: [2], from: 3 });
  }
  // ready() says what holds now: a close that removed the listener and then failed (window.close() refused) is
  // guarded again by a new listener, and ready again; when that listener cannot start (the Lead, 4607806191), it is
  // not ready, so the editor does not open, and `unguarded` is told once, so the open editor ends (main.ts) before
  // editing goes on with a system close nothing holds
  {
    const tauri = fakeTauri();
    let closeFails = true, listenFails = false;
    const inner = closeRequests("main", tauri.bridge, memoryStore());
    const { timers } = clock();
    const button = fakeButton();
    let unguarded = 0;
    const wiring = wireWindowClose({ close: async () => { if (closeFails) throw new Error("close refused"); return tauri.close(); },
      onCloseRequested: (handler) => (listenFails ? Promise.reject(new Error("ipc")) : inner(handler)) }, button,
    closeGuard({ flush: async () => true, ask: async () => false, timers }), { unguarded: () => { unguarded += 1; } });
    assert.equal(await wiring.ready(), true);
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 0);
    assert.deepEqual(closeListeners(tauri), [1], "guarded again");
    assert.equal(await wiring.ready(), true, "and ready");
    assert.equal(unguarded, 0);
    listenFails = true;
    button.click();
    await settle();
    assert.deepEqual(closeListeners(tauri), [], "the new listener could not start");
    assert.equal(await wiring.ready(), false, "not ready: the editor does not open");
    assert.equal(unguarded, 1, "told once: the open editor ends");
    closeFails = false;
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1, "with nothing to save the × button still closes");
  }
  // a close whose removal of the listener fails keeps it in place: still ready
  {
    let failOwn = true;
    const tauri = fakeTauri({ failUnlisten: (id) => failOwn && id === 0 });
    const { timers } = clock();
    const button = fakeButton();
    const wiring = wireWindowClose({ close: tauri.close, onCloseRequested: closeRequests("main", tauri.bridge, memoryStore()) }, button,
      closeGuard({ flush: async () => true, ask: async () => false, timers }));
    assert.equal(await wiring.ready(), true);
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 0);
    assert.deepEqual(closeListeners(tauri), [0]);
    assert.equal(await wiring.ready(), true, "the listener is still in place");
    failOwn = false;
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1);
  }
  // round 8: with timers, a listener that could not be put back is tried again, 1 s later, then twice as long; once in
  // place the guard is ready again (`unguarded` was told once); now() says the same as ready() without waiting
  {
    const tauri = fakeTauri();
    let listenFails = false, tries = 0, unguarded = 0;
    const inner = closeRequests("main", tauri.bridge, memoryStore());
    const { timers, advance, nextDue } = clock();
    const button = fakeButton();
    const wiring = wireWindowClose({ close: async () => { throw new Error("close refused"); },
      onCloseRequested: (handler) => { tries += 1; return listenFails ? Promise.reject(new Error("ipc")) : inner(handler); } }, button,
    closeGuard({ flush: async () => true, ask: async () => false, timers }), { timers, unguarded: () => { unguarded += 1; } });
    assert.equal(wiring.now(), false, "not before the listener is in place");
    assert.equal(await wiring.ready(), true);
    assert.equal(wiring.now(), true);
    listenFails = true;
    button.click();
    await settle();
    assert.equal(tries, 2);
    assert.equal(unguarded, 1);
    assert.equal(wiring.now(), false);
    assert.equal(nextDue(), timers.now() + RETRY_FIRST_MS, "tried again 1 s later");
    await advance(RETRY_FIRST_MS);
    await settle();
    assert.equal(tries, 3);
    assert.equal(unguarded, 1, "told once");
    assert.equal(nextDue(), timers.now() + 2 * RETRY_FIRST_MS, "then 2 s later");
    listenFails = false;
    await advance(2 * RETRY_FIRST_MS);
    await settle();
    assert.equal(tries, 4);
    assert.equal(await wiring.ready(), true, "in place again: ready");
    assert.equal(wiring.now(), true);
    assert.deepEqual(closeListeners(tauri), [1]);
    assert.equal(nextDue(), Infinity, "no more tries");
    // a listener in place with a leftover one (not ready) is not tried again: the next close removes the leftover
    const stale = fakeTauri({ failUnlisten: (id) => id === 0 });
    const staleStore = memoryStore();
    await closeRequests("main", stale.bridge, staleStore)(async () => {});  // 0, left by a reload
    stale.reload();
    const clock2 = clock();
    const held = wireWindowClose({ close: stale.close, onCloseRequested: closeRequests("main", stale.bridge, staleStore) }, fakeButton(),
      closeGuard({ flush: async () => true, ask: async () => false, timers: clock2.timers }), { timers: clock2.timers, unguarded: () => { unguarded += 1; } });
    assert.equal(await held.ready(), false);
    assert.equal(clock2.nextDue(), Infinity);
  }
  // round 9: a retry that comes due after a close put a listener back, while one is being put in place, or while a close
  // is under way never adds a second listener (the close puts one back itself when the window stays open)
  for (const when of ["put back", "arming", "closing"]) {
    const tauri = fakeTauri();
    let listenFails = true, listens = 0, gate = null, closing = null;
    const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
    const inner = closeRequests("main", tauri.bridge, memoryStore());
    const { timers, advance, nextDue } = clock();
    const button = fakeButton();
    const wiring = wireWindowClose({
      close: async () => { if (closing) await closing.promise; throw new Error("close refused"); },
      onCloseRequested: async (handler) => {
        listens += 1;
        if (gate) await gate.promise;
        if (listenFails) throw new Error("ipc");
        return inner(handler);
      } }, button, closeGuard({ flush: async () => true, ask: async () => false, timers }), { timers });
    assert.equal(await wiring.ready(), false, `${when}: the first listen fails`);
    assert.equal(nextDue(), timers.now() + RETRY_FIRST_MS);
    listenFails = false;
    if (when === "arming") gate = deferred();
    if (when === "closing") closing = deferred();
    button.click();  // "Vẫn đóng": no listener to remove, window.close() fails (once let through), one is put back
    await settle();
    assert.equal(listens, when === "closing" ? 1 : 2, `${when}: before the retry`);
    await advance(RETRY_FIRST_MS);  // the retry comes due
    await settle();
    assert.equal(listens, when === "closing" ? 1 : 2, `${when}: the retry adds none`);
    gate?.resolve();
    closing?.resolve();
    await settle();
    assert.equal(listens, 2, `${when}: one put back`);
    assert.deepEqual(closeListeners(tauri), [0], `${when}: one listener`);
    assert.equal(await wiring.ready(), true);
    await advance(RETRY_MAX_MS);
    await settle();
    assert.equal(listens, 2, `${when}: no more tries`);
  }
  // the Lead's case (4608236916): the guard was in place; a close let through ("Vẫn đóng") removed its listener and
  // window.close() failed; no listener can be put back; the open editor's change cannot be saved, its close asks and
  // the user stays. The editor is held (takes no input, so nothing is added that Alt+F4 could lose; × still asks),
  // the listener is tried again, and once it is back the editor is let go and Alt+F4 reaches the guard, which asks
  // instead of closing. While no listener can be put in place nothing in the page can hold a system close: that
  // window is what the hold and the retries cover.
  {
    const tauri = fakeTauri();
    let listenFails = false, closeFails = true, pending = false, answer = true, asked = 0;
    const inner = closeRequests("main", tauri.bridge, memoryStore());
    const { timers, advance } = clock();
    const button = fakeButton();
    const editor = { open: true, holds: [], told: [], closes: 0 };
    let wiring = null;
    const held = editorGuard({
      open: () => editor.open,
      // the editor's own close: its draft cannot be saved, it asks, and the user stays
      close: async () => { editor.closes += 1; return false; },
      hold: (on) => { editor.holds.push(on); },
      tell: (message) => { editor.told.push(message); },
      guardedNow: () => wiring.now(),
    });
    wiring = wireWindowClose({ close: async () => { if (closeFails) throw new Error("close refused"); return tauri.close(); },
      onCloseRequested: (handler) => (listenFails ? Promise.reject(new Error("ipc")) : inner(handler)) }, button,
    closeGuard({ flush: async () => !pending, ask: async () => { asked += 1; return answer; }, timers }), { timers, ...held });
    assert.equal(await wiring.ready(), true);
    pending = true;
    listenFails = true;
    void tauri.close();  // Alt+F4, "Vẫn đóng": the listener goes, window.close() fails, none can be put back
    await settle();
    assert.equal(asked, 1);
    assert.deepEqual(closeListeners(tauri), []);
    assert.equal(editor.closes, 1, "the open editor's close runs (and asks)");
    assert.deepEqual(editor.holds, [true], "the user stayed: the editor is held");
    assert.deepEqual(editor.told, [EDITOR_HELD]);
    assert.equal(await wiring.ready(), false, "no editor opens meanwhile");
    await advance(RETRY_FIRST_MS);  // tried again 1 s later: fails as well
    await settle();
    assert.deepEqual(editor.holds, [true], "still held");
    assert.deepEqual(editor.told, [EDITOR_HELD]);
    assert.equal(wiring.now(), false);
    listenFails = false;
    await advance(2 * RETRY_FIRST_MS);
    await settle();
    assert.deepEqual(closeListeners(tauri), [1], "the listener is back");
    assert.deepEqual(editor.holds, [true, false], "the editor is let go");
    assert.deepEqual(editor.told, [EDITOR_HELD, ""]);
    closeFails = false;
    answer = false;  // Ở lại
    void tauri.close();  // Alt+F4 with the change still not saved
    await settle();
    assert.equal(asked, 2, "asked, not closed");
    assert.equal(tauri.state.closed, 0);
    assert.deepEqual(closeListeners(tauri), [1]);
    answer = true;
    void tauri.close();
    await settle();
    assert.equal(tauri.state.closed, 1, "Vẫn đóng closes it");
  }
  // editorGuard on its own: the editor that left is told so and not held; the guard back while the question is open
  // holds nothing; no editor, nothing; one close at a time; a close that throws counts as staying
  {
    const run = async ({ open = true, left = false, back = false, throws = false } = {}) => {
      const editor = { holds: [], told: [], closes: 0 };
      let guarded = false, release;
      const held = editorGuard({ open: () => open, close: () => { editor.closes += 1; return new Promise((resolve, reject) => { release = () => (throws ? reject(new Error("x")) : resolve(left)); }); },
        hold: (on) => editor.holds.push(on), tell: (message) => editor.told.push(message), guardedNow: () => guarded });
      held.unguarded();
      held.unguarded();  // a second while the first closes: one close
      if (back) { guarded = true; held.guarded(); }
      release?.();
      await settle();
      return { editor, held };
    };
    const leftCase = await run({ left: true });
    assert.equal(leftCase.editor.closes, 1, "one close at a time");
    assert.deepEqual(leftCase.editor.holds, []);
    assert.deepEqual(leftCase.editor.told, [EDITOR_LEFT]);
    leftCase.held.guarded();
    assert.deepEqual(leftCase.editor.holds, [], "nothing to let go");
    const backCase = await run({ back: true });
    assert.deepEqual(backCase.editor.holds, [], "the guard came back while asking: not held");
    const throwsCase = await run({ throws: true });
    assert.deepEqual(throwsCase.editor.holds, [true]);
    assert.deepEqual(throwsCase.editor.told, [EDITOR_HELD]);
    const none = await run({ open: false });
    assert.equal(none.editor.closes, 0);
    assert.deepEqual(none.editor.told, []);
    // let go once: a later guarded (every listener put in place) clears nothing more
    throwsCase.held.guarded();
    throwsCase.held.guarded();
    assert.deepEqual(throwsCase.editor.holds, [true, false]);
    assert.deepEqual(throwsCase.editor.told, [EDITOR_HELD, ""]);
    // the editor went while its close failed: nothing to hold
    {
      let open = true, release;
      const editor = { holds: [], told: [] };
      const held = editorGuard({ open: () => open, close: () => new Promise((resolve, reject) => { release = () => reject(new Error("x")); }),
        hold: (on) => editor.holds.push(on), tell: (message) => editor.told.push(message), guardedNow: () => false });
      held.unguarded();
      open = false;
      release();
      await settle();
      assert.deepEqual(editor.holds, []);
      assert.deepEqual(editor.told, []);
    }
    // each time the guard goes, the editor open then is closed (the one before left, and one was opened since)
    {
      let closes = 0;
      const editor = { holds: [], told: [] };
      const held = editorGuard({ open: () => true, close: async () => { closes += 1; return true; },
        hold: (on) => editor.holds.push(on), tell: (message) => editor.told.push(message), guardedNow: () => false });
      held.unguarded();
      await settle();
      held.unguarded();
      await settle();
      assert.equal(closes, 2);
      assert.deepEqual(editor.told, [EDITOR_LEFT, EDITOR_LEFT]);
    }
  }
  // a re-arm after a failed close that finds a leftover it cannot remove: a listener is in place (every close reaches
  // the guard), so nothing is told unguarded, and the editor is not held
  {
    const tauri = fakeTauri({ failUnlisten: (id) => id === 1 });
    let unguarded = 0, guarded = 0, closeFails = true;
    const { timers, nextDue } = clock();
    const button = fakeButton();
    const wiring = wireWindowClose({ close: async () => { if (closeFails) throw new Error("close refused"); return tauri.close(); },
      onCloseRequested: closeRequests("main", tauri.bridge, memoryStore()) }, button,
    closeGuard({ flush: async () => true, ask: async () => false, timers }), { timers, unguarded: () => { unguarded += 1; }, guarded: () => { guarded += 1; } });
    assert.equal(await wiring.ready(), true);
    assert.equal(guarded, 1);
    tauri.other(CLOSE_EVENT);  // 1: a close listener that cannot be removed
    button.click();
    await settle();
    assert.deepEqual(closeListeners(tauri), [1, 2], "put back, with the leftover still there");
    assert.equal(await wiring.ready(), false);
    assert.equal(unguarded, 0, "a listener is in place");
    assert.equal(guarded, 2);
    assert.equal(nextDue(), Infinity, "not tried again: the next close removes the leftover first");
    closeFails = false;
  }
  // a close under way: not ready while it removes the listener
  {
    const tauri = fakeTauri();
    const { timers } = clock();
    const button = fakeButton();
    let answer;
    const wiring = wireWindowClose({ close: tauri.close, onCloseRequested: closeRequests("main", tauri.bridge, memoryStore()) }, button,
      closeGuard({ flush: async () => false, ask: () => new Promise((resolve) => { answer = resolve; }), timers }));
    assert.equal(await wiring.ready(), true);
    button.click();
    await settle();
    assert.equal(await wiring.ready(), true, "asking: still in place");
    answer(true);
    await null;
    await null;
    await null;
    assert.equal(await wiring.ready(), false, "closing: not any more");
    await settle();
    assert.equal(tauri.state.closed, 1);
  }
  // the store in sessionStorage: the page after a reload reads what the page before wrote
  {
    const items = new Map();
    let reads = 0;
    const storage = { getItem: (key) => { reads += 1; return items.has(key) ? items.get(key) : null; },
      setItem: (key, value) => items.set(key, String(value)), removeItem: (key) => items.delete(key) };
    const tauri = fakeTauri();
    const before = storageIds(() => storage);
    assert.equal(reads, 0, "read only when used");
    await page(tauri, before);
    assert.deepEqual(JSON.parse(items.get(CLOSE_LISTENERS_KEY)), { ids: [0], from: 1 });
    tauri.reload();
    const button = fakeButton();
    await page(tauri, storageIds(() => storage), button);
    assert.deepEqual(closeListeners(tauri), [1]);
    button.click();
    await settle();
    assert.equal(tauri.state.closed, 1);
    assert.deepEqual(JSON.parse(items.get(CLOSE_LISTENERS_KEY)), { ids: [], from: 2 }, "nothing left to remove");
    // a storage that cannot be reached at all: in place, not ready (as a store that refuses, above)
    const unreachable = storageIds(() => { throw new Error("SecurityError"); });
    const other = fakeTauri();
    assert.equal(await page(other, unreachable), false);
    assert.deepEqual(closeListeners(other), [0]);
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
  assert.match(save, /\} catch \(error\) \{\s*if \(session\?\.outputPath === outputPath\) \{[\s\S]*?\}\s*throw error;\s*\}\s*\}\s*$/,
    "save() tells the saver that the write failed");
  assert.match(view, /const drafts = new DraftSavers<CarriedDraft>\(async \(outputPath, copy\) => \{\s*if \(session\?\.outputPath === outputPath\) await save\(\);\s*else if \(copy\) await saveCopy\(copy\);\s*\}, windowTimers\);/,
    "each video's saver writes its open editor's draft (save() as it is, failures included), else the copy a reload carried");
  assert.doesNotMatch(view, /new Autosaver\(/, "no saver beside the per-video ones");
  const closeNow = view.slice(view.indexOf("async function closeNow("), view.indexOf("function leave(leaving: EditTarget)"));
  assert.match(closeNow, /const outputPath = session\.outputPath;\s*const saved = await flushWithin\(\(\) => drafts\.flush\(outputPath\), CLOSE_WAIT_MS, windowTimers\) \|\| ownOnDisk\(\);/,
    "only this editor's draft decides: another video's carried change is neither asked about here nor replaced");
  assert.ok(closeNow.indexOf("flushWithin(") < closeNow.indexOf("cover.end()"), "the draft is written before the editor ends");
  // a change is dropped only after the user chose to, never on "Ở lại"; a reload's close carries it instead of asking
  assert.match(closeNow, /if \(!saved && carrying\) \{\s*carry\(session\);\s*\} else if \(!saved\) \{[^}]*if \(!\(await askAlone\(\(\) => host\.confirm\(unsavedPrompt\("exit", unapplied\)\)\)\)\) return false;\s*discard\(session\);/);
  assert.match(closeNow, /if \(!ownOnDisk\(\) && !\(await flushWithin\(\(\) => drafts\.flush\(outputPath\), CLOSE_WAIT_MS, windowTimers\)\) && session && !ownOnDisk\(\)\) \{\s*if \(carrying\) carry\(session\);\s*else \{\s*if \(!\(await askAlone\(\(\) => host\.confirm\(unsavedPrompt\("exit"\)\)\)\)\) return false;\s*discard\(session\);/,
    "a change made while the question was open is written too, and dropped only on Vẫn thoát");
  assert.equal((closeNow.match(/discard\(session\)/g) ?? []).length, 2);
  assert.match(closeNow, /if \(ownOnDisk\(\)\) drafts\.forget\(session\.outputPath\);\s*const leaving = session\.target;/,
    "a later write that has every change makes the reload's older copy go (it would be written over the newer draft)");
  // Vẫn thoát lets this editor's change go, and only it: another video's carried change has a saver of its own and
  // stays pending with its backoff (tested above with videos A and B)
  assert.match(view, /function discard\(current: Session\): void \{\s*drafts\.drop\(current\.outputPath\);\s*edits = savedEdits;\s*\}/);
  assert.equal((view.match(/drafts\.drop\(/g) ?? []).length, 1, "nothing else drops a change (a carry keeps it with its video's saver)");
  // the open editor's own changes are counted, and counted as on disk only by its own write that worked
  assert.match(view, /function changed\(\): void \{\s*edits \+= 1;\s*if \(session\) drafts\.changed\(session\.outputPath\);\s*\}/);
  assert.equal((view.match(/drafts\.changed\(/g) ?? []).length, 1, "every change goes through changed()");
  assert.match(view, /function ownOnDisk\(\): boolean \{\s*return savedEdits === edits;\s*\}/);
  assert.equal((view.match(/savedEdits = /g) ?? []).length, 3, "set when an editor opens, by its own write, and by Vẫn thoát");
  // a carried change stays pending, with its backoff: written from its copy when no editor takes it up, and a window
  // close is not told the disk has it before then
  // one carried copy per video: a reload of one never replaces another's
  assert.match(view, /function carry\(current: Session\): void \{\s*drafts\.carry\(\{ outputPath: current\.outputPath, kind: current\.target\.kind, name: current\.target\.displayName, project: draftDocument\(current\),\s*keepOld: current\.keepOld, oldKept: current\.oldKept \}\);\s*\}/);
  assert.match(view, /async function saveCopy\(left: CarriedDraft\): Promise<void> \{\s*try \{\s*await invoke\("edit_project_save", \{ outputPath: left\.outputPath, kind: left\.kind, project: left\.project, keepOld: left\.keepOld \}\);\s*\} catch \(error\) \{\s*if \(session && session\.outputPath !== left\.outputPath\) \{[\s\S]{0,200}?\}\s*throw error;\s*\}\s*left\.keepOld = false;\s*if \(session\?\.outputPath === left\.outputPath\) session\.keepOld = false;\s*\}/,
    "each copy written from itself, on its own backoff; once on disk its unreadable draft is kept aside (keepOld) for any editor of it");
  // ... written first, from its copy; once on disk its unreadable draft is kept aside (keepOld) for any editor of it;
  // a failure there does not hold up the open video's own draft
  assert.match(save, /^async function save\(\): Promise<void> \{\s*if \(!session\) return;\s*const mine = session;\s*const at = edits;/,
    "the open editor's saver writes only its own draft: a held copy of another video never slows it");
  assert.match(save, /await invoke\("edit_project_save", \{ outputPath, kind, project, keepOld \}\);\s*if \(session === mine\) savedEdits = Math\.max\(savedEdits, at\);\s*const copy = drafts\.get\(outputPath\);\s*if \(keepOld && copy\) copy\.keepOld = false;/,
    "its own write counts its changes as on disk, and a reload's copy does not keep aside again a draft it kept aside");
  assert.equal((save.match(/edit_project_save/g) ?? []).length, 1);
  // a window close (and the window hidden) writes every video's draft, each on its own; the editor's own uses (its
  // close, Áp dụng, a template, Xuất) wait for its own video's only
  assert.match(view, /function flush\(\): Promise<boolean> \{\s*return drafts\.flushAll\(\);\s*\}/);
  assert.match(view, /async function flushOwn\(\): Promise<boolean> \{\s*return session \? drafts\.flush\(session\.outputPath\) : true;\s*\}/);
  assert.equal((view.match(/\bflush\(\)/g) ?? []).length, 4, "flush() is in the Editor type (the window close), defined once, and used by the window hidden and the page going away");
  assert.match(view, /document\.addEventListener\("visibilitychange", \(\) => \{ if \(document\.visibilityState === "hidden"\) void flush\(\); \}\);\s*window\.addEventListener\("pagehide", \(\) => \{ void flush\(\); \}\);/);
  assert.match(view, /start\.disabled = true;\s*error\.hidden = true;\s*try \{\s*await flushOwn\(\);\s*const normalize/, "Xuất waits for its own video's draft only");
  // the window close names the videos whose change would go
  assert.match(view, /function unsaved\(\): string\[\] \{\s*return drafts\.unsaved\(\)\.map\(\(outputPath\) => session\?\.outputPath === outputPath \? session\.target\.displayName\s*: drafts\.get\(outputPath\)\?\.name \?\? outputPath\.split\(\/\[\\\\\/\]\/\)\.pop\(\) \?\? outputPath\);\s*\}/);
  assert.match(view, /\n\s*flush,\s*unsaved,\s*closing: \(\) => closing\?\.pending \?\? null,/);
  const opening = view.slice(view.indexOf("async function open(target: EditTarget"), view.indexOf("function close(ask: boolean"));
  assert.ok(opening.indexOf("const carry = drafts.get(outputPath) ?? null;") > 0
    && opening.indexOf("const carry = drafts.get(outputPath) ?? null;") < opening.indexOf("edit_project_load"),
  "the carried change is taken before the load (a retry may write it after the load read the older draft)");
  assert.equal((opening.match(/const carry\b/g) ?? []).length, 1, "taken once, before the load (no later copy shadows it)");
  assert.match(opening, /if \(carry\) loaded\.project = carry\.project;/);
  assert.match(opening, /if \(carry && drafts\.take\(carry\)\) scheduleSave\(\);/, "the reopened editor writes it on, through its video's saver, after its backoff");
  assert.match(opening, /edits = 0;\s*savedEdits = 0;\s*session = \{/, "a new editor's own draft starts as on disk, counted before anything can change it");
  // a draft on disk that could not be read now (held) is kept aside by the first save that can, never written over
  assert.match(opening, /keepOld: carry \? carry\.keepOld : !!loaded\.draft_problem \|\| \(!!loaded\.project && !project\),/);
  // a reopen the user left while it loaded opens nothing: checked after the load, before the editor is made
  assert.match(opening, /async function open\(target: EditTarget, wanted: \(\) => boolean = \(\) => true\): Promise<boolean>/);
  const wantedAt = opening.indexOf("|| !wanted()) return false;");
  assert.ok(wantedAt > opening.indexOf("edit_project_load") && wantedAt < opening.indexOf("session = {"), "checked after the load");
  // round 8: the window close guard can go while the video loads: checked again after the load, without waiting
  const nowAt = opening.indexOf("if (host.closeGuardedNow && !host.closeGuardedNow()) {");
  assert.ok(nowAt > wantedAt && nowAt < opening.indexOf("session = {"), "the guard checked again after the load, before the editor is made");
  assert.match(opening, /if \(host\.closeGuardedNow && !host\.closeGuardedNow\(\)\) \{\s*host\.showError\(UNGUARDED\);\s*return false;\s*\}/);
  assert.equal((opening.match(/await /g) ?? []).length, 2, "and nothing waits after it, so the answer still holds when the editor opens");
  // only a close that left counts as leaving: "Ở lại" does not stop a reload, "Thoát" does
  const closing = view.slice(view.indexOf("function close(ask: boolean"), view.indexOf("async function closeNow("));
  assert.match(closing, /\.then\(\(left\) => \{\s*if \(left && !quiet\) exits \+= 1;\s*return left;\s*\}\)/);
  assert.equal((view.match(/exits \+= 1/g) ?? []).length, 1);
  // the editor opens only once every window close is guarded
  const open = view.slice(view.indexOf("async function open(target: EditTarget"), view.indexOf("edit_project_load"));
  assert.match(open, /if \(host\.closeGuarded && !\(await host\.closeGuarded\(\)\)\) \{[^}]*return false;\s*\}/);
  const main = code("../src/main.ts");
  assert.match(main, /const closable = \{ close: \(\) => appWindow\.close\(\), onCloseRequested: closeRequests\(appWindow\.label, events, storageIds\(\(\) => window\.sessionStorage\)\) \};\s*const held = editorGuard\(\{ open: \(\) => editor\.target !== null \|\| editor\.reopening, close: \(\) => editor\.close\(false\),\s*hold: \(on\) => editor\.hold\(on\), tell: showError, guardedNow: \(\) => wiring\.now\(\) \}\);\s*const wiring = wireWindowClose\(closable, \$\("#window-close"\), mayClose, \{ timers: windowTimers, \.\.\.held \}\);\s*windowCloseReady = \(\) => wiring\.ready\(\);\s*windowCloseNow = \(\) => wiring\.now\(\);/,
    "the editor asks whether the guard is ready now, each time it opens (and again once the video loaded); a guard that could not be put back closes or holds the open editor and is tried again");
  // a held editor takes no input: no key, no drop; a closed one is let go
  assert.match(view, /function hold\(on: boolean\): void \{\s*holding = on && session !== null;\s*screen\.inert = holding;\s*\}/);
  assert.match(view, /document\.addEventListener\("keydown", \(event\) => \{\s*if \(!session \|\| holding \|\| screen\.hidden/);
  assert.match(view, /drop: \(paths, point\) => \{ if \(!holding\) void dropFiles\(paths, point\); \},\s*hold,/);
  assert.match(view, /session = null;\s*hold\(false\);/);
  assert.equal((view.match(/holding = /g) ?? []).length, 2, "declared false, then set only by hold()");
  // the event bridge calls the commands Tauri's own listen and unlisten call, with the same arguments
  assert.match(main, /tauriInvoke<number>\("plugin:event\|listen",\s*\{ event, target: \{ kind: "Window", label \}, handler: transformCallback\(handler\) \}\)/);
  assert.match(main, /await tauriInvoke\("plugin:event\|unlisten", \{ event, eventId: id \}\);/);
  // installing an update or restarting quits through the host's app.exit, with no close request: guarded too
  assert.match(main, /async function quitInvoke<T>\(command: string, args\?: Record<string, unknown>\): Promise<T> \{\s*if \(\(command === "install_update" \|\| command === "restart_app"\) && !\(await mayQuit\(\)\)\) \{\s*throw new Error\([^)]*\);\s*\}\s*return invoke<T>\(command, args\);\s*\}/);
  assert.match(main, /mountUpdates\(quitInvoke,/);
  assert.match(main, /ask: \(\) => askAlone\(\(\) => confirmDialog\(unsavedPrompt\("close", false, editor\.unsaved\(\)\)\)\), timers: windowTimers \}\);\s*mayQuit = mayClose;/,
    "the question names every video whose change would go");
  const updates = code("../src/features/update/view.ts");
  assert.match(updates, /invoke\("install_update", /);
  assert.match(updates, /invoke\("restart_app"\)/);
  const host = readFileSync(new URL("../src-tauri/src/main.rs", import.meta.url), "utf8");
  assert.equal((host.match(/app\.exit\(/g) ?? []).length, 2, "install_update and restart_app are the only quits of the host");
  assert.match(host, /fn install_update\([^]*?app\.exit\(0\);[^]*?fn restart_app\([^]*?app\.exit\(0\);/);
  // leaving the page while a video is being opened again goes through the editor's close (counted as leaving)
  assert.match(main, /if \(editor\.target \|\| editor\.reopening\) \{\s*if \(view === "edit"\)/);
  assert.match(view, /get reopening\(\) \{ return !session && quietlyLeft !== null; \},/);
  // Phụ đề "Áp dụng" makes the new version from the draft on disk: refused while a change is not on it
  assert.match(view, /flush: async \(\) => \{\s*await flushOwn\(\);\s*if \(!ownOnDisk\(\)\) throw new Error\("Chưa lưu được bản nháp[^"]*"\);\s*\},\s*finish: async \(\) => \{ await close\(false\); \},/,
    "judged on this video's own draft: another video's carried change does not hold it up");
  assert.equal((view.match(/flush: async \(\) => \{ await flushOwn\(\); \}/g) ?? []).length, 1, "only the template's, which carries the draft itself");
  const subtitles = code("../src/features/edit/subtitles_view.ts");
  assert.match(subtitles, /try \{\s*await context\.flush\(\);\s*const applied[\s\S]*?edit_subtitles_apply[\s\S]*?\} catch \(error\) \{\s*host\.showError\(`Không áp dụng được thay đổi/,
    "a refused flush stops Áp dụng before the new version is made, and says so");
  const tauriEvent = readFileSync(new URL("../node_modules/@tauri-apps/api/event.js", import.meta.url), "utf8");
  assert.match(tauriEvent, /invoke\('plugin:event\|listen', \{\s*event,\s*target,\s*handler: transformCallback\(handler\)\s*\}\)/);
  assert.match(tauriEvent, /invoke\('plugin:event\|unlisten', \{\s*event,\s*eventId\s*\}\)/);
  const tauriWindow = readFileSync(new URL("../node_modules/@tauri-apps/api/window.js", import.meta.url), "utf8");
  assert.match(tauriWindow, /target: \{ kind: 'Window', label: this\.label \}/, "the window's own listen targets it the same way");
  assert.equal(CLOSE_EVENT, "tauri://close-requested");
  assert.match(tauriEvent, /WINDOW_CLOSE_REQUESTED"\] = "tauri:\/\/close-requested"/);
  assert.ok(capabilitiesHaveEvents(), "listening is allowed (core:default has core:event:default)");
  assert.match(main, /closeGuarded: \(\) => windowCloseReady\(\),\s*closeGuardedNow: \(\) => windowCloseNow\(\),/);
  assert.ok(main.indexOf("let windowCloseReady") < main.indexOf("const editor = mountEditor("), "set before the editor can open");
  assert.doesNotMatch(main, /editor\.flush\(\)\.finally\(/, "no close that ignores a failed save");
  assert.match(main, /closeGuard\(\{ before: \(\) => editor\.closing\(\), flush: \(\) => editor\.flush\(\),\s*ask: \(\) => askAlone\(\(\) => confirmDialog\(unsavedPrompt\("close", false, editor\.unsaved\(\)\)\)\)/,
    "the window close waits for an editor close in progress, then asks one question at a time");
  assert.doesNotMatch(main + view + code("../src/features/edit/close_guard.ts"), /\.destroy\(\)/, "never destroys the window");
  assert.ok(!capabilities.permissions.includes("core:window:allow-destroy"), "no new window permission (Issue #929 contract)");
  // every save goes through the saver (a direct save() would neither be retried nor seen by a close)
  assert.doesNotMatch(view, /void save\(\)/);
  assert.equal((view.match(/\bsave\(\)/g) ?? []).length, 2, "save() is defined once and called only by its video's saver");
  // a reload of the same video carries an unsaved change instead of asking, and does not undo a later exit
  const reopen = view.slice(view.indexOf("async function reopenSame("), view.indexOf("async function applyLayers("));
  assert.match(reopen, /const exit = exits;[\s\S]*if \(!\(await close\(false, true, true\)\)\) return;\s*if \(exits !== exit\) return;\s*if \(!\(await open\(target, \(\) => exits === exit\)\)\) \{\s*if \(exits === exit && !session && !opening\) leave\(target\);\s*return;\s*\}\s*await then\(\);/,
    "a user who left before or while it reopened is not taken back; one that could not be reopened is left, unless another video's open took the editor");
  assert.match(view, /function leave\(leaving: EditTarget\): void \{\s*if \(session\) return;\s*quietlyLeft = null;/, "leaving ends a pending reopen");
  assert.match(view, /const exit = exits;\s*if \(!\(await close\(false, true\)\)\) return;\s*if \(exits !== exit\) return;[\s\S]{0,300}displayName: next\.displayName \},\s*\(\) => exits === exit\);\s*if \(!opened && exits === exit && !session && !opening\) leave\(from\);/,
    "the template's reopen, likewise, and it does not leave a second time");
  assert.equal((view.match(/reopenSame\(/g) ?? []).length, 4, "the three documentary reloads use it");
  assert.doesNotMatch(view, /reload: async/, "no reload of its own");
  // a refused close from a queue selection is not asked again on every render
  assert.match(view, /if \(stayedFor !== id\) void close\(false\)\.then\(\(left\) => \{ if \(!left && session\) stayedFor = id; \}\);/);
}

console.log("edit autosave tests passed");

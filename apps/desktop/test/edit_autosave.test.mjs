import assert from "node:assert/strict";
import { Autosaver, MAX_WAIT_MS, QUIET_MS, saveDelay } from "../src/features/edit/autosave.ts";

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
  await failing.flush();  // the view shows the error; the saver does not throw past it
}

console.log("edit autosave tests passed");

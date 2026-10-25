import assert from "node:assert/strict";
import { checkDue, parseCheck, parseVersions } from "../src/features/update/view.ts";

assert.deepEqual(parseCheck({ ok: true, available: true, version: "0.3.0", notes: "n", setup: { size_bytes: 1048576 } }),
  { ok: true, unsupported: false, update: { version: "0.3.0", notes: "n", sizeBytes: 1048576 } });
assert.deepEqual(parseCheck({ ok: true, available: false }), { ok: true, update: null, unsupported: false });
assert.deepEqual(parseCheck({ ok: true, available: false, unsupported: true }), { ok: true, update: null, unsupported: true });
assert.equal(parseCheck({ ok: false, code: "SIGNATURE_INVALID", message: "bad" }).ok, false);
assert.equal(parseCheck(null).ok, false);
// A malformed "available" answer is not an update.
assert.equal(parseCheck({ ok: true, available: true }).update, null);

const versions = parseVersions({ current: "0.2.0-rc6", versions: [{ version: "0.2.0-rc6", channel: "candidate", installed_at_utc: "x" }, { channel: "x" }] });
assert.equal(versions.current, "0.2.0-rc6");
assert.equal(versions.versions.length, 1);
assert.equal(parseVersions({ ok: false }), null);

const now = Date.UTC(2026, 9, 25);
assert.equal(checkDue(now, null), true);
assert.equal(checkDue(now, String(now - 60_000)), false);
assert.equal(checkDue(now, String(now - 25 * 3600_000)), true);
assert.equal(checkDue(now, String(now + 3600_000)), true);
assert.equal(checkDue(now, "garbage"), true);
console.log("desktop update tests passed");

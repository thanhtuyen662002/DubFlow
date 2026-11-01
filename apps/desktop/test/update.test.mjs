import assert from "node:assert/strict";
import { CHECK_INTERVAL_MS, FIRST_CHECK_MS, parseCheck, parseVersions } from "../src/features/update/view.ts";

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

// Checked shortly after launch, then every 6 hours.
assert.equal(FIRST_CHECK_MS, 4000);
assert.equal(CHECK_INTERVAL_MS, 6 * 3600_000);
console.log("desktop update tests passed");

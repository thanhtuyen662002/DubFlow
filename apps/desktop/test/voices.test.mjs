import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dubbingOptions, filterVoices, parseVoiceCatalog, voiceLabel } from "../src/features/voices/model.ts";
import { MemorySnapshotStorage, QueueController, parseQueueSnapshot } from "../src/features/queue/model.ts";

const manifest = JSON.parse(readFileSync(new URL("../../../models/manifests/production-vieneu-v1.json", import.meta.url), "utf8"));
const catalog = parseVoiceCatalog({ default_voice_id: manifest.voice_id, voices: manifest.voices });
assert.equal(catalog.voices.length, 25);
for (const accent of ["Bắc", "Trung", "Nam"]) {
  for (const gender of ["Nam", "Nữ"]) assert.ok(filterVoices(catalog, { accent, gender }).length > 0);
}
assert.ok(filterVoices(catalog, { style: "tin tức" }).length > 0);
assert.ok(filterVoices(catalog, { style: "kể chuyện" }).length > 0);
assert.ok(voiceLabel(catalog.voices[0]).includes(catalog.voices[0].name));
for (const invalid of [null, {}, { default_voice_id: "missing", voices: catalog.voices },
  { ...catalog, voices: [catalog.voices[0], catalog.voices[0]] },
  { ...catalog, voices: [{ ...catalog.voices[0], approved: false }] }]) {
  assert.throws(() => parseVoiceCatalog(invalid));
}
assert.throws(() => dubbingOptions(catalog, true, "unavailable"));

const storage = new MemorySnapshotStorage();
const queue = new QueueController(storage);
const [first, second] = queue.addPaths(["C:/media/first.mp4", "C:/media/second.mp4"]);
const chosen = catalog.voices.find((voice) => voice.accent === "Nam" && voice.gender === "Nam").voice_id;
queue.setDubbing(first, dubbingOptions(catalog, true, chosen));
const restored = new QueueController(storage);
assert.deepEqual(restored.getSnapshot().jobs[0].dubbing, { enabled: true, voiceId: chosen });
assert.deepEqual(restored.getSnapshot().jobs[1].dubbing, { enabled: false, voiceId: null });
const firstJob = restored.getSnapshot().jobs[0];
restored.updateStatus(first, { ...firstJob.status, state: "RECOVERED" });
assert.throws(() => restored.setDubbing(first, dubbingOptions(catalog, true, catalog.default_voice_id)));
assert.equal(new QueueController(storage).getSnapshot().jobs[0].dubbing.voiceId, chosen);
assert.throws(() => restored.setDubbing(second, { enabled: true, voiceId: null }));

const legacy = queue.getSnapshot();
for (const job of legacy.jobs) delete job.dubbing;
assert.deepEqual(parseQueueSnapshot(JSON.stringify(legacy)).jobs[0].dubbing, { enabled: false, voiceId: null });
legacy.jobs[0].dubbing = { enabled: true, voiceId: "../outside" };
assert.equal(parseQueueSnapshot(JSON.stringify(legacy)), null);
console.log("desktop voice catalog, filtering, persistence and recovery tests passed");

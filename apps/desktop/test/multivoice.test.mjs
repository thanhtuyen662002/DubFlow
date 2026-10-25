import assert from "node:assert/strict";
import { MemorySnapshotStorage, QueueController, isSpeakerVoices, parseQueueSnapshot } from "../src/features/queue/model.ts";

const clock = () => "2026-10-25T00:00:00.000Z";
const queue = new QueueController(new MemorySnapshotStorage(), clock);
const [id] = queue.addPaths(["C:/drama.mp4"]);
queue.setMultiVoice(id, true);
assert.equal(queue.getSnapshot().jobs[0].multiVoice, true);
assert.equal(parseQueueSnapshot(JSON.stringify(queue.getSnapshot())).jobs[0].multiVoice, true);

// Speaker voices turn multi-voice on and survive persistence and revisions.
queue.setSpeakerVoices(id, { S1: "vi-truc-ly-vieneu3-v1", S2: "vi-adam-bua-vieneu3-v1" });
assert.deepEqual(queue.getSnapshot().jobs[0].speakerVoices, { S1: "vi-truc-ly-vieneu3-v1", S2: "vi-adam-bua-vieneu3-v1" });
queue.updateStatus(id, { ...queue.getSnapshot().jobs[0].status, state: "COMPLETED" });
const revision = queue.reviseJob(id, "D:/Out/rev");
const revised = queue.getSnapshot().jobs.find((job) => job.id === revision);
assert.equal(revised.multiVoice, true);
assert.deepEqual(revised.speakerVoices, { S1: "vi-truc-ly-vieneu3-v1", S2: "vi-adam-bua-vieneu3-v1" });
queue.setSpeakerVoices(revision, { S2: "vi-minh-khang-vieneu3-v1" });
assert.deepEqual(queue.getSnapshot().jobs.find((job) => job.id === revision).speakerVoices, { S2: "vi-minh-khang-vieneu3-v1" });

// Turning multi-voice off clears chosen voices; options lock once processing starts.
queue.setMultiVoice(revision, false);
assert.equal(queue.getSnapshot().jobs.find((job) => job.id === revision).speakerVoices, undefined);
assert.throws(() => queue.setMultiVoice(id, false), /before processing/);

for (const invalid of [{}, { S9: "v" }, { X1: "v" }, { S1: "Bad Voice" }, ["S1"], null]) {
  assert.equal(isSpeakerVoices(invalid), false, JSON.stringify(invalid));
}
assert.throws(() => queue.setSpeakerVoices(revision, { S0: "v" }), /Invalid speaker voices/);
assert.equal(parseQueueSnapshot(JSON.stringify({ ...queue.getSnapshot(), jobs: [{ ...queue.getSnapshot().jobs[0], speakerVoices: { S1: 5 } }] })), null);
console.log("desktop multi-voice queue tests passed");

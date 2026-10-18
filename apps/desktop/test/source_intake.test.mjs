import assert from "node:assert/strict";
import { parseSourceInput, supervisorSourceItems, SOURCE_BYTE_LIMITS, utf8Bytes } from "../src/features/source_intake/model.ts";
import { submitSources } from "../src/features/source_intake/controller.ts";
import { MemorySnapshotStorage, QueueController } from "../src/features/queue/model.ts";
import "./source_materialized.test.mjs";

const path = "C:\\Phim Việt\\Cảnh, phần 2 có dấu.mp4";
const url = "https://example.test/clip,part2.mp4?q=a,b";
const paths = ["https://example.test/scene/1", "https://example.test/scene//1", "https://example.test/scene/1/"];
const parsed = parseSourceInput([path, url, ...paths].join("\r\n"));
assert.equal(parsed.rejected.length, 0);
assert.equal(parsed.items.length, 5);
assert.equal(parsed.items[0].local_path, path);
assert.equal(parsed.items[1].transport_url, url);
assert.equal(parsed.items[1].url, url);
assert.equal(new Set(parsed.items.slice(2).map((item) => item.identity_key)).size, 3);
assert.equal(parseSourceInput(paths.join("\n") + "\n" + paths[0]).rejected[0].code, "DUPLICATE_SOURCE");
assert.equal(parseSourceInput("https://example.test/?a=1&b=2\nhttps://example.test/?b=2&a=1").items.length, 2);

const prefix = "https://example.test/";
const exact = prefix + "a".repeat(SOURCE_BYTE_LIMITS.identity - utf8Bytes(`url:${prefix}`));
const multi = "C:/" + "ệ".repeat(338) + "a"; // local: + 3 + 1014 + 1 == 1024 bytes
assert.equal(utf8Bytes(`local:${multi.toLowerCase().replaceAll("/", "\\")}`), 1024);
const mixed = parseSourceInput(["not a source", exact, exact + "b", multi, multi + "ệ", path,
  "https://user:secret@example.test/x", "C:/bad\u0085name.mp4", "C:/" + "ệ".repeat(1500), url].join("\n"));
assert.equal(mixed.items.length, 4);
assert.deepEqual(mixed.rejected.map((item) => item.line), [1, 3, 5, 7, 8, 9]);
assert.equal(mixed.rejected.filter((item) => item.code === "INPUT_TOO_LONG").length, 3);
assert(!JSON.stringify(mixed.rejected).includes("secret"));
assert.equal(parseSourceInput(`bad\n${path}\n${url}`, 1).items[0].local_path, path);
assert.equal(parseSourceInput(`bad\n${path}\n${url}`, 1).rejected[1].code, "QUEUE_LIMIT");
assert.equal(parseSourceInput(path + ", other.mp4").items[0].local_path, path + ", other.mp4");
assert.equal(parseSourceInput("\r\n" + path + "\r" + url + "\n").items.length, 2);

const secret = parseSourceInput("https://example.test/x?part=a,b&token=super-secret");
assert(!JSON.stringify(supervisorSourceItems(secret)).includes("super-secret"));
assert.equal(secret.items[0].transport_url, "https://example.test/x?part=a,b&token=super-secret");

const queue = new QueueController(new MemorySnapshotStorage());
let failOnline;
const pending = submitSources(`${url}\n${path}`, {
  addLocal: (paths) => queue.addPaths(paths), enqueue: () => new Promise((_, reject) => { failOnline = reject; }),
});
assert.equal(queue.getSummary().total, 1); // before online promise settles
assert.equal(queue.getSnapshot().jobs[0].sourcePath, path);
const local = await submitSources("C:/Phim Việt/cảnh nữa.mp4", {
  addLocal: (paths) => queue.addPaths(paths), enqueue: () => { throw new Error("must remain offline"); },
});
assert.equal(local.local, 1);
assert.equal(queue.getSummary().total, 2);
failOnline(new Error("provider stalled"));
assert.equal((await pending).rejected[0].code, "SOURCE_UNAVAILABLE");
assert.equal(queue.getSnapshot().jobs[0].status.state, "QUEUED");

const reply = await submitSources(paths.join("\n"), { addLocal: () => assert.fail("no local"),
  enqueue: async () => ({ event: "source_scan_accepted", rejected: [{ index: 1, code: "INPUT_INVALID", field: "source_url" }] }),
});
assert.equal(reply.online, 2);
assert.equal(reply.rejected[0].input, paths[1]);
let chunks = 0;
const many = await submitSources(Array.from({length: 130}, (_, i) => `${prefix}${i}`).join("\n"), {
  addLocal: () => {}, enqueue: async (items) => { assert(items.length <= 64); chunks++; return { event: "source_scan_accepted" }; },
});
assert.equal(chunks, 3); assert.equal(many.online, 130);

if (process.argv.includes("--wire")) console.log(JSON.stringify({ limits: SOURCE_BYTE_LIMITS,
  items: supervisorSourceItems({ ...mixed, items: mixed.items.filter((item) => item.kind === "url") }),
  references: [path, url, ...paths], distinct: supervisorSourceItems({ ...parsed, items: parsed.items.filter((item) => item.kind === "url") }) }));
else console.log("source intake: PASS (identity, UTF-8, mixed errors, local during pending/failed online)");

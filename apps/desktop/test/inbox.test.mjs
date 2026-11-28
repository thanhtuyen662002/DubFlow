import assert from "node:assert/strict";
import { agentJobPlan, isAgentJob, parseInboxRequests } from "../src/features/queue/inbox.ts";
import { FIRST_RUN_DEFAULTS } from "../src/features/queue/defaults.ts";
import { MemorySnapshotStorage, QueueController } from "../src/features/queue/model.ts";
import { AI_CONTEXT_KEY, setKeysClosed, unattendedAiContext } from "../src/features/ai/ai_context.ts";

// An agent's dubbing requests (DubFlow MCP, ADR-0058, Issue #998): what take_inbox returns becomes a queue job.
const ID = "mcp-20261128-100000-a1b2c3d4";
const options = { dub: true, voice_id: "vi-a", source_language: "zh", burn_in: null, blur_auto: true, separate_background: null,
  multi_voice: null, translation_engine: "argos" };

// 1. The frontend's own check of the host's answer: bad items are dropped, never half-read.
const parsed = parseInboxRequests([
  { id: ID, source_path: "D:\\Nguồn\\clip.mp4", title: " Đập\nTam Hiệp ", options },
  { id: "job-123", source_path: "D:\\a.mp4", title: "", options },
  { id: "mcp-20261128-100000-00000002", source_path: "", title: "", options },
  { id: "mcp-20261128-100000-00000003", source_path: "D:\\b.mp4", title: "", options: { ...options, dub: "yes" } },
  { id: "mcp-20261128-100000-00000004", source_path: "D:\\c.mp4", title: "", options: { ...options, translation_engine: "google" } },
  { id: "mcp-20261128-100000-00000005", source_path: "D:\\d.mp4", title: "", options: {} },
]);
assert.deepEqual(parsed.map((request) => request.id), [ID, "mcp-20261128-100000-00000005"]);
assert.equal(parsed[0].title, "Đập Tam Hiệp");
assert.equal(parsed[1].options.dub, null, "an option left out is null: the user's default");
assert.deepEqual(parseInboxRequests(null), []);
assert.deepEqual(parseInboxRequests({ id: ID }), []);
assert.equal(parseInboxRequests(Array.from({ length: 30 }, (_, index) => ({ id: `mcp-20261128-100000-${String(index).padStart(8, "0")}`,
  source_path: "D:\\x.mp4", title: "", options: {} }))).length, 20);
assert.ok(isAgentJob({ id: ID }) && !isAgentJob({ id: "job-0123" }) && !isAgentJob(undefined));

// 2. The plan: what the agent asked for, and the user's own default for everything it left out.
const asked = agentJobPlan(parsed[0].options, FIRST_RUN_DEFAULTS);
assert.deepEqual(asked, { dubbing: true, voiceId: "vi-a", sourceLanguage: "zh", burnIn: true, blurAuto: true, separateBackground: true,
  multiVoice: false, translationEngine: "argos", mastering: null });
const mine = { ...FIRST_RUN_DEFAULTS, dubbing: false, voiceId: "vi-b", burnIn: false, blurAuto: false, sourceLanguage: "en",
  translationEngine: "hachimi", mastering: "balanced", blurRegions: [{ x: 0, y: 0.8, width: 1, height: 0.2 }] };
assert.deepEqual(agentJobPlan(parsed[1].options, mine), { dubbing: false, voiceId: "vi-b", sourceLanguage: "en", burnIn: false,
  blurAuto: true, separateBackground: true, multiVoice: false, translationEngine: "hachimi", mastering: "balanced" });
assert.equal(agentJobPlan({ ...parsed[1].options, source_language: "auto" }, mine).sourceLanguage, null, "auto detects, whatever the default");

// 3. The queue job keeps the request's id, once.
const queue = new QueueController(new MemorySnapshotStorage(), () => "2026-11-28T10:00:00.000Z");
const [mineId] = queue.addPaths(["D:\\Nguồn\\clip.mp4"]);
assert.equal(queue.addAgentJob(ID, parsed[0].source_path, parsed[0].title), ID, "the same file as a user's own job is a separate job");
assert.equal(queue.addAgentJob(ID, parsed[0].source_path, parsed[0].title), null);
const job = queue.getSnapshot().jobs.find((item) => item.id === ID);
assert.equal(job.displayName, "Đập Tam Hiệp");
assert.equal(job.status.state, "QUEUED");
assert.equal(queue.getSnapshot().selected_job_id, mineId, "the user's selection stays");
assert.equal(queue.addAgentJob("mcp-20261128-100000-00000009", "D:\\Nguồn\\other.mp4", "").valueOf(), "mcp-20261128-100000-00000009");
assert.equal(queue.getSnapshot().jobs.at(-1).displayName, "other.mp4");
assert.throws(() => queue.addAgentJob("job-1", "D:\\x.mp4", ""));

// 4. An agent's start never asks the AI question: the user's own answer, else offline.
const store = (value) => ({ getItem: (key) => (key === AI_CONTEXT_KEY && value !== null ? value : null), setItem: () => { throw new Error("nothing kept"); } });
const session = { provider: () => "deepseek", options: () => ({}) };
assert.equal(unattendedAiContext(session, store("on")), "on");
assert.equal(unattendedAiContext(session, store("off")), "off");
assert.equal(unattendedAiContext(session, store("ask")), "off");
setKeysClosed(true);
assert.equal(unattendedAiContext(session, store("on")), "off", "a run with its keys closed sends no AI");
setKeysClosed(false);

console.log("inbox tests passed");

import assert from "node:assert/strict";
import { applyAgentEdit, dubbedTitle, parseEditRequests } from "../src/features/edit/agent.ts";
import { addText, createEdit, serializeProject } from "../src/features/edit/model.ts";

// An agent's Chỉnh sửa draft (DubFlow MCP, ADR-0059, Issue #1002): parsed by the editor's own model, saved as the
// video's draft with the user's previous one set aside, exported the way Xuất video does. No host here: a fake invoke.
const OUTPUT = "D:\\Videos\\DubFlow Output\\Đập Tam Hiệp - vi\\run-0123456789ab\\final_vi.mp4";
const ID = "mcp-20261128-100000-eeeeeeee";
const info = { path: OUTPUT, title: "Đập Tam Hiệp", width: 1920, height: 1080, fps: 30, hasAudio: true };

// The agent's edit: the part from 2 s to 8 s, with a text.
const whole = createEdit(10, info);
const cut = { ...whole, segments: [{ ...whole.segments[0], start: 2, end: 8 }] };
const edited = addText(cut, 0.5, "Đập Tam Hiệp");
const draft = serializeProject(edited, 3, "s1");

// 1. The frontend's own check of take_inbox's answer: only edit requests, whole.
const requests = parseEditRequests([
  { kind: "edit", id: ID, output_path: OUTPUT, project: draft, export: { file_name: " Đập (agent) ", quality: "1080p" } },
  { kind: "dubbing", id: "mcp-20261128-100000-00000001", source_path: "D:\\a.mp4", options: {} },
  { kind: "edit", id: "job-1", output_path: OUTPUT, project: draft, export: null },
  { kind: "edit", id: "mcp-20261128-100000-00000002", output_path: OUTPUT, project: [1], export: null },
  { kind: "edit", id: "mcp-20261128-100000-00000003", output_path: OUTPUT, project: draft, export: { quality: "4k" } },
]);
assert.deepEqual(requests.map((request) => request.id), [ID, "mcp-20261128-100000-00000003"]);
assert.deepEqual(requests[0].export, { file_name: "Đập (agent)", quality: "1080p" });
assert.deepEqual(requests[1].export, { file_name: null, quality: null }, "an unknown quality is the editor's default");
assert.deepEqual(parseEditRequests(null), []);
assert.equal(dubbedTitle(OUTPUT), "Đập Tam Hiệp");

// A fake host: what the editor would load, and every call made.
function host(overrides = {}) {
  const calls = [];
  const answers = {
    edit_project_load: { duration: 10, width: 1920, height: 1080, fps: 30, has_audio: true, cues: [{ start_ms: 2500, end_ms: 4000, text: "Con đập" }],
      project: { schema_version: 4, thumbnail: { at: 3 } } },
    edit_project_save: null,
    edit_status: { status: null, exports: [{ path: "D:\\Videos\\Đập Tam Hiệp (sửa 1).mp4" }] },
    edit_export: null,
    ...overrides,
  };
  const invoke = async (command, args) => {
    calls.push([command, args]);
    const answer = answers[command];
    if (answer instanceof Error) throw answer;
    return answer;
  };
  return { calls, invoke };
}
const editorHost = (open = null) => ({ openPath: () => open, outputRoot: () => null, loudness: () => true });

// 2. Saved and exported like Xuất video.
{
  const { calls, invoke } = host();
  const report = await applyAgentEdit(invoke, { id: ID, output_path: OUTPUT, project: draft, export: { file_name: null, quality: "720p" } }, editorHost());
  assert.equal(report.state, "EXPORTING");
  assert.equal(report.file_name, "Đập Tam Hiệp (sửa 2)", "the next free name, as Xuất video picks it");
  assert.deepEqual(calls.map(([command]) => command), ["edit_project_load", "edit_project_save", "edit_status", "edit_export"]);
  const [, save] = calls[1];
  assert.equal(save.keepOld, true, "the user's previous draft is set aside, never written over");
  assert.equal(save.project.agent.id, ID, "the draft says an agent saved it");
  assert.equal(save.kind, "job");
  assert.deepEqual(save.project.thumbnail, { at: 3 }, "the draft's thumbnail stays");
  assert.deepEqual(save.project.segments.map(({ start, end }) => [start, end]), [[2, 8]]);
  assert.equal(save.project.texts[0].text, "Đập Tam Hiệp");
  const { request } = calls[3][1];
  assert.equal(request.outputPath, OUTPUT);
  assert.equal(request.quality, "720p");
  assert.equal(request.folder, "D:\\Videos\\DubFlow Output\\Đập Tam Hiệp - vi\\run-0123456789ab");
  assert.equal(request.normalize, true);
  assert.equal(request.audioOnly, false);
  assert.deepEqual(request.cues, [{ start_ms: 500, end_ms: 2000, text: "Con đập" }], "subtitles follow the cut");
  assert.equal(request.segments.length, 1);
  assert.equal(request.texts.length, 1);
}

// 3. Saved only; and the refusals, which save nothing.
{
  const { calls, invoke } = host();
  const report = await applyAgentEdit(invoke, { id: ID, output_path: OUTPUT, project: draft, export: null }, editorHost());
  assert.equal(report.state, "SAVED");
  assert.deepEqual(calls.map(([command]) => command), ["edit_project_load", "edit_project_save"]);
}
{
  // An agent's own earlier draft is replaced, not set aside: only the user's drafts fill the few kept copies.
  const loaded = { duration: 10, width: 1920, height: 1080, fps: 30, has_audio: true, cues: [], project: { schema_version: 4, agent: { id: "mcp-20261128-090000-aaaaaaaa" } } };
  const { calls, invoke } = host({ edit_project_load: loaded });
  await applyAgentEdit(invoke, { id: ID, output_path: OUTPUT, project: draft, export: null }, editorHost());
  assert.equal(calls[1][1].keepOld, false);
  const { calls: none, invoke: fresh } = host({ edit_project_load: { ...loaded, project: null } });
  await applyAgentEdit(fresh, { id: ID, output_path: OUTPUT, project: draft, export: null }, editorHost());
  assert.equal(none[1][1].keepOld, false, "no draft yet: nothing to set aside");
}
{
  const { calls, invoke } = host();
  const report = await applyAgentEdit(invoke, { id: ID, output_path: OUTPUT, project: draft, export: null },
    editorHost("d:/videos/dubflow output/đập tam hiệp - vi/run-0123456789ab/FINAL_VI.mp4"));
  assert.equal(report.state, "REJECTED");
  assert.match(report.message, /đang mở trong Chỉnh sửa/);
  assert.deepEqual(calls, [], "nothing is read or written under an open editor");
}
{
  const { calls, invoke } = host();
  const report = await applyAgentEdit(invoke, { id: ID, output_path: OUTPUT, project: { schema_version: 4, segments: "x" }, export: null }, editorHost());
  assert.equal(report.state, "REJECTED");
  assert.deepEqual(calls.map(([command]) => command), ["edit_project_load"]);
}
{
  const { invoke } = host({ edit_project_load: new Error("Loại video không hợp lệ") });
  assert.equal((await applyAgentEdit(invoke, { id: ID, output_path: OUTPUT, project: draft, export: null }, editorHost())).state, "REJECTED");
}
{
  const { invoke } = host({ edit_export: new Error("busy") });
  const report = await applyAgentEdit(invoke, { id: ID, output_path: OUTPUT, project: draft, export: { file_name: "x", quality: null } }, editorHost());
  assert.equal(report.state, "FAILED");
  assert.match(report.message, /Đã lưu bản nháp/);
}

console.log("edit agent tests passed");

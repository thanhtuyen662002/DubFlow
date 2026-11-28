# ADR-0059: MCP edits go through Chỉnh sửa's own draft, in the running app

Status: Accepted. Issue #1002. Extends ADR-0058 (the MCP queue inbox).

## Context

Agents can now find footage, make product videos and documentaries, dub (ADR-0058), and make Shorts, 16:9 versions,
packaged posts and compilations (#1000) through DubFlow's MCP server. What they cannot do is edit, as the user does in
Chỉnh sửa.

The editor has one model and one renderer:

- The draft is `project.json` (schema 4) in the video's `.dubflow-work\edit`. `features/edit/model.ts` reads it
  (`parseProject`, which checks it against the video's real duration and size) and writes it (`serializeProject`).
- Xuất video turns the edit into a render request (`exportPlan`, `remapCues`) and the host's `edit_export` renders it.

A second editor in Python would drift from both. A draft written straight to disk would race an open editor's
autosave, and a draft the editor cannot read would only be set aside when the user opens it.

## Decision

Edits go through the running app, through the inbox of ADR-0058:

1. MCP `get_edit` returns a finished dubbed video's draft, without its undo history, or a starter draft (one part
   covering the whole video), plus the video's facts and its reviewed subtitles. `save_edit` fills each media file's
   facts by probing it, then writes `<data root>\control\inbox\<id>.json`:
   `{"schema_version": 1, "kind": "edit", "id": "mcp-…", "created_at", "output_path": "…\\final_vi.mp4",
   "project": {…the draft…}, "export": null | {"file_name"?, "quality"?}}`, at most 1 MiB.
2. `take_inbox` also takes `edit` requests. It checks the id, a finished `final_vi.mp4` (the host's `review_dir`),
   that `project` is an object, and the export options (a plain file name, `source`, `1080p` or `720p`). It returns
   every accepted request tagged with its `kind`.
3. The frontend handles an edit request as follows:
   - It refuses it while that video is open in Chỉnh sửa.
   - It parses the draft with `parseProject` against `edit_project_load`'s facts, and refuses a draft the editor
     could not open.
   - It saves the draft with `edit_project_save`, marked `agent: {id, saved}`. A previous draft the user saved is set
     aside first (`keepOld`), never overwritten. The editor's own saves leave the mark out, so a draft is the user's
     again as soon as they edit it. An earlier agent draft is simply replaced, so a run of agent edits never pushes
     the user's draft out of the few copies the editor keeps. The previous draft's thumbnail is kept.
   - If an export was asked for, it starts `edit_export` with the request Xuất video would build.
4. The new host command `inbox_report` writes the app's answer to `inbox\results\<id>.json`: `SAVED`, `EXPORTING`
   (with the file it exports to), `REJECTED` or `FAILED`, with a message. It writes only for a request that is in
   `inbox\accepted`, and an answer is at most 16 KiB. MCP `get_edit_job` reads the answer, then the editor's own
   export status (`.dubflow-work\edit\status.json`) for progress and the finished file.

## Consequences

- One editor model and one renderer. What an agent makes opens in Chỉnh sửa as the user's own draft, with undo
  starting there.
- The app must be open, and the video must not be open in Chỉnh sửa, for an agent's edit to land.
- Schema 1 covers finished dubbed videos (`job` targets). Other targets (product runs, plain files, documentaries)
  can be added as further `target` values later.
- The draft is still untrusted input. The host checks its envelope, `parseProject` checks its contents, and
  `edit_project_save` (`check_project`) and `edit_export` check them again before anything is written or rendered.

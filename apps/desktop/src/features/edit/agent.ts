import { exportFolder, pathKey } from "./library.ts";
import { defaultExportName, exportPlan, parseProject, remapCues, serializeProject, type Cue, type Quality } from "./model.ts";

/**
 * An agent's edit of a finished dubbed video, sent through DubFlow's MCP server (ADR-0059, Issue #1002). The host's
 * `take_inbox` (inbox.rs) checked its envelope. Here the editor's own model reads it, saves it as the video's
 * Chỉnh sửa draft (the user's previous draft set aside, never overwritten), and exports it the way Xuất video does.
 */
export type EditExportRequest = { file_name: string | null; quality: Quality | null };
export type EditRequest = { id: string; output_path: string; project: Record<string, unknown>; export: EditExportRequest | null };
export type EditReport = {
  state: "SAVED" | "EXPORTING" | "REJECTED" | "FAILED"; message: string; folder?: string; file_name?: string;
  /** Overlays, sounds, texts or markers the editor's model left out (it skips one it cannot use instead of refusing the draft). */
  dropped?: Record<string, number>;
};

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Loaded = { duration: number; width: number; height: number; fps?: number | null; has_audio: boolean; project: unknown; cues?: Cue[] | null };
type Status = { exports?: Array<{ path: string }> };

export type AgentEditHost = {
  /** The video open in Chỉnh sửa now, if any: an agent never writes under an open editor. */
  openPath(): string | null;
  /** Thư mục lưu kết quả, when the user chose one. */
  outputRoot(): string | null;
  /** Chuẩn hóa độ to, as the user last left it in Xuất video. */
  loudness(): boolean;
};

const QUALITIES: readonly Quality[] = ["source", "1080p", "720p"];
const AGENT_ID = /^mcp-[0-9a-z-]{8,64}$/;

/** take_inbox's edit requests, checked again; anything else is left out. */
export function parseEditRequests(value: unknown): EditRequest[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 20).flatMap((item): EditRequest[] => {
    if (!item || typeof item !== "object") return [];
    const raw = item as Record<string, unknown>;
    if (raw.kind !== "edit" || typeof raw.id !== "string" || !AGENT_ID.test(raw.id) || typeof raw.output_path !== "string"
      || !raw.output_path.trim() || !raw.project || typeof raw.project !== "object" || Array.isArray(raw.project)) return [];
    const options = raw.export && typeof raw.export === "object" ? raw.export as Record<string, unknown> : null;
    const quality = options && QUALITIES.includes(options.quality as Quality) ? options.quality as Quality : null;
    const fileName = options && typeof options.file_name === "string" && options.file_name.trim() ? options.file_name.trim() : null;
    return [{ id: raw.id, output_path: raw.output_path, project: raw.project as Record<string, unknown>,
      export: options ? { file_name: fileName, quality } : null }];
  });
}

/** "<title> - vi\run-…\final_vi.mp4" → "<title>", the name Xuất video starts from. */
export function dubbedTitle(outputPath: string): string {
  const parts = outputPath.split(/[\\/]+/).filter(Boolean);
  const folder = parts.at(-3) ?? parts.at(-1) ?? "video";
  return folder.replace(/ - vi$/, "") || "video";
}

function short(error: unknown): string {
  return String(error).slice(0, 200);
}

export async function applyAgentEdit(invoke: Invoke, request: EditRequest, host: AgentEditHost): Promise<EditReport> {
  const outputPath = request.output_path;
  const open = host.openPath();
  if (open && pathKey(open) === pathKey(outputPath)) {
    return { state: "REJECTED", message: "Video này đang mở trong Chỉnh sửa; hãy đóng nó (hoặc chuyển sang video khác) rồi gửi lại." };
  }
  let loaded: Loaded;
  try {
    loaded = await invoke<Loaded>("edit_project_load", { outputPath, kind: "job" });
  } catch (error) {
    return { state: "REJECTED", message: `Không mở được video để chỉnh sửa: ${short(error)}` };
  }
  const title = dubbedTitle(outputPath);
  const info = { path: outputPath, title, width: loaded.width, height: loaded.height, fps: loaded.fps ?? 30, hasAudio: loaded.has_audio };
  const project = parseProject(request.project, loaded.duration, info);
  if (!project) return { state: "REJECTED", message: "Bản nháp không đúng định dạng của Chỉnh sửa (schema 4); xem get_edit." };
  const document = serializeProject(project.edit, Math.max(1, project.changes), project.selected, project.recent);
  const kept: Record<string, number> = { overlays: project.edit.overlays.length, audio: project.edit.audio.length, texts: project.edit.texts.length,
    markers: project.edit.markers.length };
  const dropped = Object.fromEntries(Object.entries(kept).flatMap(([key, count]) => {
    const asked = Array.isArray(request.project[key]) ? (request.project[key] as unknown[]).length : 0;
    return asked > count ? [[key, asked - count]] : [];
  }));
  const extra = Object.keys(dropped).length
    ? { dropped, note: ` Bỏ qua vì không hợp lệ: ${Object.entries(dropped).map(([key, count]) => `${count} ${key}`).join(", ")}.` } : { note: "" };
  const previous = loaded.project && typeof loaded.project === "object" ? loaded.project as Record<string, unknown> : null;
  if (previous?.thumbnail) document.thumbnail = previous.thumbnail;
  // A draft an agent saved carries `agent` until the user edits it (the editor's own saves leave it out). Only the user's
  // own draft is set aside (edit.rs replace_draft keeps the last few), so a run of agent edits never pushes it out.
  const byAgent = !!previous?.agent && typeof previous.agent === "object";
  document.agent = { id: request.id, saved: new Date().toISOString() };
  try {
    await invoke("edit_project_save", { outputPath, kind: "job", project: document, keepOld: !!previous && !byAgent });
  } catch (error) {
    return { state: "FAILED", message: `Không lưu được bản nháp: ${short(error)}` };
  }
  const skipped = extra.dropped ? { dropped: extra.dropped } : {};
  if (!request.export) return { state: "SAVED", message: `Đã lưu bản nháp; mở video trong Chỉnh sửa để xem và xuất.${extra.note}`, ...skipped };
  const status = await invoke<Status>("edit_status", { outputPath, kind: "job" }).catch(() => null);
  const fileName = request.export.file_name ?? defaultExportName(title, (status?.exports ?? []).map((entry) => entry.path));
  const folder = exportFolder(outputPath, host.outputRoot());
  const cues = remapCues(loaded.cues ?? [], project.edit).filter((cue) => cue.end_ms > cue.start_ms);
  try {
    await invoke("edit_export", { request: { outputPath, kind: "job", quality: request.export.quality ?? "source", folder, fileName, cues,
      ...exportPlan(project.edit), normalize: host.loudness(), audioOnly: false } });
  } catch (error) {
    return { state: "FAILED", message: `Đã lưu bản nháp nhưng không bắt đầu xuất được: ${short(error)}${extra.note}`, ...skipped };
  }
  return { state: "EXPORTING", message: `Đã lưu bản nháp và đang xuất video.${extra.note}`, folder, file_name: fileName, ...skipped };
}

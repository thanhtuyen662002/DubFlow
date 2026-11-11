/**
 * Tổng hợp từ video gốc (spec v2, Issue #728): the draft the page edits, the
 * request the host checks again (`compilation_raw_render`), and the run the
 * host reads back (`compilation_raw_open`). Nothing here reorders the user's
 * items: an AI ranking suggestion is only shown, and applied when the user
 * presses its button.
 */
import { isLevel, type Level } from "../mastering/model.ts";

export const SOURCE_FORMATS = ["mp4", "mov", "mkv", "webm", "m4v", "avi"] as const;
export const MAX_ITEMS = 30;
export const MAX_SOURCES = 8;
export const MAX_NARRATION = 1500;
export type Rights = "owned" | "licensed" | "permitted" | "unknown";
export const RIGHTS_NAMES: Record<Rights, string> = {
  owned: "Của tôi", licensed: "Có giấy phép", permitted: "Được cho phép", unknown: "Chưa rõ",
};
export type KeepAudio = "auto" | "on" | "off";

export type SourceForm = {
  /** The page's own key for the row. */
  key: string; path: string; item: string; start: string; end: string; notes: string; rights: Rights;
};
export type RawItemForm = {
  key: string; itemId: string | null; title: string; rank: string; narration: string; notes: string; keepAudio: KeepAudio;
};
export type RawDraft = {
  title: string; layout: "bang-thong-tin" | "toan-man-hinh"; kind: "top_n" | "list"; ranking: "user" | "suggested";
  applySuggestion: boolean; hook: string; intro: string; outro: string; items: RawItemForm[]; sources: SourceForm[];
  voiceId: string; speed: number; music: string | null; musicVolume: number; mastering: Level;
  useAi: boolean; draftNarration: boolean; targetMinutes: string;
};
export type AiRoute = { provider: string; model?: string; baseUrl?: string };
export type RawSourceRequest = { video: string; startMs: number | null; endMs: number | null; notes: string | null; rights: Rights };
export type RawItemRequest = {
  itemId: string | null; title: string; rank: number | null; narration: string; notes: string; sources: RawSourceRequest[];
  keepSourceAudio: KeepAudio;
};
export type RawRequest = {
  title: string; layout: RawDraft["layout"]; voiceId: string; speed: number; music: string | null; musicVolume: number;
  kind: RawDraft["kind"]; ranking: RawDraft["ranking"]; applySuggestion: boolean; hook: string; intro: string; outro: string;
  items: RawItemRequest[]; mastering: Level; ai: AiRoute | null; draftNarration: boolean; outputRoot: string | null; cpuOnly: boolean;
};

let counter = 0;
const nextKey = (prefix: string): string => `${prefix}${++counter}`;
const count = (text: string): number => [...text].length;
const CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]/u;
const validText = (text: string, limit: number, required = false, spoken = false): boolean =>
  count(text) <= limit && !CONTROL.test(text) && (!required || Boolean(text.trim()))
  && (!spoken || !text.trim() || /[\p{L}\p{N}]/u.test(text));

export function emptyDraft(): RawDraft {
  return {
    title: "", layout: "toan-man-hinh", kind: "top_n", ranking: "user", applySuggestion: false, hook: "", intro: "", outro: "",
    items: [], sources: [], voiceId: "", speed: 1, music: null, musicVolume: 0.12, mastering: "balanced",
    useAi: false, draftNarration: false, targetMinutes: "",
  };
}

export function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** Whether the engine reads this file (by its extension). */
export function supported(path: string): boolean {
  const ext = /\.([^.\\/]+)$/.exec(path)?.[1]?.toLowerCase() ?? "";
  return (SOURCE_FORMATS as readonly string[]).includes(ext);
}

function itemTitle(path: string): string {
  return [...fileName(path).replace(/\.[^.]+$/, "").replace(/[_]+/g, " ").trim()].slice(0, 80).join("") || "Mục mới";
}

export function newItem(title = "Mục mới"): RawItemForm {
  return { key: nextKey("item"), itemId: null, title, rank: "", narration: "", notes: "", keepAudio: "auto" };
}

/**
 * Add source videos: each new one becomes its own item (titled from its file name) while there is room, else it
 * joins `into` (or the last item) when that has room. Unsupported formats and files already in the draft are
 * reported, never added.
 */
export function addSources(draft: RawDraft, paths: readonly string[], into?: string): {
  added: number; unsupported: string[]; duplicate: string[]; full: string[];
} {
  const result = { added: 0, unsupported: [] as string[], duplicate: [] as string[], full: [] as string[] };
  for (const path of paths) {
    if (!supported(path)) { result.unsupported.push(fileName(path)); continue; }
    if (draft.sources.some((source) => source.path.toLocaleLowerCase("en") === path.toLocaleLowerCase("en"))) {
      result.duplicate.push(fileName(path)); continue;
    }
    let item = into ? draft.items.find((candidate) => candidate.key === into) : undefined;
    if (!item && draft.items.length < MAX_ITEMS) {
      item = newItem(itemTitle(path));
      draft.items.push(item);
    }
    item ??= draft.items[draft.items.length - 1];
    if (!item || sourcesOf(draft, item.key).length >= MAX_SOURCES) { result.full.push(fileName(path)); continue; }
    draft.sources.push({ key: nextKey("src"), path, item: item.key, start: "", end: "", notes: "", rights: "unknown" });
    result.added++;
  }
  return result;
}

export function sourcesOf(draft: RawDraft, itemKey: string): SourceForm[] {
  return draft.sources.filter((source) => source.item === itemKey);
}

export function removeSource(draft: RawDraft, key: string): void {
  draft.sources = draft.sources.filter((source) => source.key !== key);
}

/** Remove an item and its sources. */
export function removeItem(draft: RawDraft, key: string): void {
  draft.items = draft.items.filter((item) => item.key !== key);
  draft.sources = draft.sources.filter((source) => source.item !== key);
}

export function moveItem(draft: RawDraft, key: string, delta: number): void {
  const index = draft.items.findIndex((item) => item.key === key), target = index + delta;
  if (index < 0 || target < 0 || target >= draft.items.length) return;
  [draft.items[index], draft.items[target]] = [draft.items[target], draft.items[index]];
}

/** Number the items in the list's order: countdown (#N first … #1 last, a Top N) or from #1. */
export function numberRanks(draft: RawDraft, countdown: boolean): void {
  const total = draft.items.length;
  draft.items.forEach((item, index) => { item.rank = String(countdown ? total - index : index + 1); });
}

/**
 * The user applies the AI's suggested order (item ids, programme order): the items move, each place keeps its
 * rank number, so a countdown stays a countdown. Unknown or missing ids leave the draft unchanged.
 */
export function applySuggestedOrder(draft: RawDraft, order: readonly string[]): boolean {
  const byId = new Map(draft.items.filter((item) => item.itemId).map((item) => [item.itemId!, item]));
  if (order.length !== draft.items.length || new Set(order).size !== order.length || order.some((id) => !byId.has(id))) return false;
  const ranks = draft.items.map((item) => item.rank);
  draft.items = order.map((id) => byId.get(id)!);
  draft.items.forEach((item, index) => { item.rank = ranks[index]; });
  return true;
}

/** Empty is no trim; seconds with a comma or a point; anything else is an error, never silently changed. */
export function milliseconds(value: string): number | null | "invalid" {
  const text = value.trim().replace(",", ".");
  if (!text) return null;
  const number = Number(text), ms = Math.round(number * 1000);
  return Number.isFinite(number) && number >= 0 && Number.isSafeInteger(ms) ? ms : "invalid";
}

/** Roughly how long the narration reads (Vietnamese, about 14 characters a second at speed 1.0). */
export function narrationSeconds(draft: RawDraft): number {
  const text = [draft.hook, draft.intro, draft.outro, ...draft.items.map((item) => item.narration)].join(" ");
  return Math.round(count(text.replace(/\s+/g, " ").trim()) / (14 * (draft.speed || 1)));
}

/** A line comparing the estimate with the target; the target only guides the writing, it cuts nothing. */
export function durationNote(draft: RawDraft): string {
  const estimate = narrationSeconds(draft);
  const minutes = Number(draft.targetMinutes.replace(",", "."));
  const said = `Lời bình đọc khoảng ${Math.floor(estimate / 60)} phút ${estimate % 60} giây`;
  if (!draft.targetMinutes.trim() || !Number.isFinite(minutes) || minutes <= 0) return `${said}.`;
  const target = Math.round(minutes * 60), off = estimate - target;
  if (Math.abs(off) <= Math.max(15, target * 0.15)) return `${said}, gần với mục tiêu ${minutes} phút.`;
  return `${said}: ${off > 0 ? "dài hơn" : "ngắn hơn"} mục tiêu ${minutes} phút. Thời lượng thật theo lời bình và cảnh của bạn; DubFlow không tự cắt bớt.`;
}

/** The request for the host, or the first problem in words. */
export function buildRawRequest(draft: RawDraft, options: { ai: AiRoute | null; outputRoot: string | null; cpuOnly: boolean }): RawRequest | string {
  if (!validText(draft.title, 120, true)) return "Nhập chủ đề, tối đa 120 ký tự.";
  if (!validText(draft.hook, 300, false, true)) return "Câu mở màn tối đa 300 ký tự.";
  if (!validText(draft.intro, 800, false, true) || !validText(draft.outro, 800, false, true)) return "Mở đầu và kết thúc tối đa 800 ký tự.";
  if (draft.items.length < 2 || draft.items.length > MAX_ITEMS) return `Cần từ 2 đến ${MAX_ITEMS} mục.`;
  if (!draft.voiceId) return "Hãy chọn giọng đọc.";
  if (!isLevel(draft.mastering)) return "Mức tối ưu hậu kỳ không hợp lệ.";
  if (!Number.isFinite(draft.speed) || draft.speed < 1 || draft.speed > 1.3) return "Tốc độ đọc từ 1,0 đến 1,3.";
  const ai = draft.useAi ? options.ai : null;
  const ranking = draft.ranking === "suggested" && ai ? "suggested" : "user";
  if (draft.ranking === "suggested" && !ai) return "Gợi ý thứ hạng cần nhà cung cấp AI có khóa trong Cài đặt › Tài khoản.";
  const drafting = draft.draftNarration && Boolean(ai);
  if (draft.draftNarration && !ai) return "AI viết lời bình cần nhà cung cấp AI có khóa trong Cài đặt › Tài khoản.";
  const ranks = new Set<number>();
  const items: RawItemRequest[] = [];
  for (const [index, item] of draft.items.entries()) {
    const label = `Mục ${index + 1}`;
    if (!validText(item.title, 80, true)) return `${label}: nhập tiêu đề, tối đa 80 ký tự.`;
    if (!validText(item.narration, MAX_NARRATION, false, true)) return `${label}: lời bình tối đa ${MAX_NARRATION} ký tự.`;
    if (!item.narration.trim() && !drafting) return `${label}: chưa có lời bình (viết lời bình, hoặc bật “AI viết lời bình”).`;
    if (!validText(item.notes, 600)) return `${label}: ghi chú tối đa 600 ký tự.`;
    let rank: number | null = null;
    if (item.rank.trim()) {
      rank = Number(item.rank);
      if (!Number.isInteger(rank) || rank < 1 || rank > 1000 || ranks.has(rank)) return `${label}: thứ hạng là số nguyên dương, không trùng.`;
      ranks.add(rank);
    }
    const sources: RawSourceRequest[] = [];
    for (const [k, source] of sourcesOf(draft, item.key).entries()) {
      const startMs = milliseconds(source.start), endMs = milliseconds(source.end);
      if (!supported(source.path)) return `${label}: định dạng chưa hỗ trợ: ${fileName(source.path)}.`;
      if (startMs === "invalid" || endMs === "invalid" || (endMs !== null && endMs <= (startMs ?? 0))) {
        return `${label}, nguồn ${k + 1}: mốc cắt không hợp lệ (giây; kết thúc sau bắt đầu).`;
      }
      if (!validText(source.notes, 400)) return `${label}, nguồn ${k + 1}: ghi chú tối đa 400 ký tự.`;
      sources.push({ video: source.path, startMs, endMs, notes: source.notes.trim() || null, rights: source.rights });
    }
    if (!sources.length || sources.length > MAX_SOURCES) return `${label}: cần từ 1 đến ${MAX_SOURCES} video nguồn.`;
    items.push({ itemId: item.itemId, title: item.title.trim(), rank, narration: item.narration.trim(), notes: item.notes.trim(),
      sources, keepSourceAudio: item.keepAudio });
  }
  return {
    title: draft.title.trim(), layout: draft.layout, voiceId: draft.voiceId, speed: draft.speed, music: draft.music,
    musicVolume: draft.musicVolume, kind: draft.kind, ranking, applySuggestion: ranking === "suggested" && draft.applySuggestion,
    hook: draft.hook.trim(), intro: draft.intro.trim(), outro: draft.outro.trim(), items, mastering: draft.mastering, ai,
    draftNarration: drafting, outputRoot: options.outputRoot, cpuOnly: options.cpuOnly,
  };
}

const str = (value: unknown): string => (typeof value === "string" ? value : "");
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const secondsText = (ms: unknown): string => (typeof ms === "number" && Number.isFinite(ms) && ms >= 0 ? String(ms / 1000) : "");

/** A run's spec (v2) as a draft to edit and make again; items keep their ids, so their review decisions stay. */
export function specToDraft(value: unknown): RawDraft | null {
  const spec = (value ?? {}) as Record<string, unknown>;
  if (spec.schema_version !== 2 || !Array.isArray(spec.items)) return null;
  const draft = emptyDraft();
  const program = (spec.program ?? {}) as Record<string, unknown>;
  const mastering = ((spec.mastering ?? {}) as Record<string, unknown>).level;
  const editorial = (spec.editorial ?? {}) as Record<string, unknown>;
  Object.assign(draft, {
    title: str(spec.title), layout: spec.layout === "bang-thong-tin" ? "bang-thong-tin" : "toan-man-hinh",
    kind: program.kind === "list" ? "list" : "top_n", ranking: program.ranking === "suggested" ? "suggested" : "user",
    applySuggestion: program.apply_suggestion === true, hook: str(spec.hook), intro: str(spec.intro), outro: str(spec.outro),
    voiceId: str(spec.voice_id), speed: num(spec.speed) ?? 1, music: str(spec.music) || null, musicVolume: num(spec.music_volume) ?? 0.12,
    mastering: isLevel(mastering) ? mastering : "balanced", useAi: Boolean(editorial.ai), draftNarration: editorial.draft_narration === true,
  });
  for (const raw of spec.items.slice(0, MAX_ITEMS)) {
    const item = (raw ?? {}) as Record<string, unknown>;
    const form = newItem(str(item.title));
    form.itemId = /^i\d{1,2}$/.test(str(item.item_id)) ? str(item.item_id) : null;
    form.rank = typeof item.rank === "number" ? String(item.rank) : "";
    form.narration = str(item.narration);
    form.notes = str(item.notes);
    form.keepAudio = item.keep_source_audio === "on" || item.keep_source_audio === "off" ? item.keep_source_audio : "auto";
    draft.items.push(form);
    for (const rawSource of (Array.isArray(item.sources) ? item.sources : []).slice(0, MAX_SOURCES)) {
      const source = (rawSource ?? {}) as Record<string, unknown>;
      if (!str(source.video)) continue;
      const rights = source.rights;
      draft.sources.push({ key: nextKey("src"), path: str(source.video), item: form.key, start: secondsText(source.start_ms),
        end: secondsText(source.end_ms), notes: str(source.notes),
        rights: rights === "owned" || rights === "licensed" || rights === "permitted" ? rights : "unknown" });
    }
  }
  return draft;
}

export type RawStatus = {
  state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; message: string; done: number; total: number; unreviewed: number;
};
export type RawPart = {
  id: string; kind: string; title: string; rank: number | null; narration: string; sources: string[]; startMs: number;
  durationMs: number; unreviewed: number; accepted: boolean; summary: Record<string, number>;
};
export type RawRun = {
  dir: string; spec: unknown; status: RawStatus | null; parts: RawPart[]; video: string | null; final: string | null;
  /** Whether `final` is the current programme (P2-6); null when there is no final export. */
  finalState: FinalState | null;
  suggestion: { order: string[]; reasons: Record<string, string> } | null; applied: boolean;
  rendered: string[]; reused: string[]; warnings: string[]; credits: string[];
};

export type FinalState = {
  stale: boolean; reason: "rerendered" | "rendering" | "unknown" | null; revision: number | null; sha256: string | null;
  exportedAtMs: number | null;
};

function parseFinalState(value: unknown): FinalState | null {
  if (!value || typeof value !== "object") return null;
  const object = value as Record<string, unknown>;
  const reason = ["rerendered", "rendering", "unknown"].includes(object.reason as string) ? object.reason as FinalState["reason"] : null;
  const whole = (v: unknown): number | null => (Number.isInteger(v) && (v as number) >= 0 ? (v as number) : null);
  const sha = typeof object.sha256 === "string" && /^[0-9a-f]{64}$/.test(object.sha256) ? object.sha256 : null;
  // anything but an explicit "current" is stale: a final is never shown as done on a doubt
  return { stale: object.stale !== false || reason !== null, reason, revision: whole(object.revision), sha256: sha,
    exportedAtMs: whole(object.exported_at_ms) };
}

/** The line under the final export: what the final file is, and whether it still matches the programme. */
export function finalText(run: Pick<RawRun, "final" | "finalState">): string {
  if (!run.final) return "";
  const state = run.finalState;
  if (!state || state.stale) {
    const why = state?.reason === "rendering" ? "chương trình đang được dựng lại"
      : state?.reason === "rerendered" ? "chương trình đã được dựng lại sau lần xuất này"
      : "không xác định được bản này xuất từ bản dựng nào";
    return `Bản cuối cũ: ${run.final} (${why}). Bản này không còn là bản hiện tại: hãy xuất bản cuối lại.`;
  }
  return `Bản cuối (lần xuất ${state.revision ?? 1}): ${run.final}`;
}

function parseStatus(value: unknown): RawStatus | null {
  const object = (value ?? {}) as Record<string, unknown>;
  if (!["RUNNING", "COMPLETED", "FAILED", "CANCELLED"].includes(object.state as string) || typeof object.message !== "string") return null;
  const whole = (v: unknown): number => (Number.isInteger(v) && (v as number) >= 0 ? (v as number) : 0);
  return { state: object.state as RawStatus["state"], message: object.message, done: whole(object.done), total: whole(object.total),
    unreviewed: whole(object.unreviewed) };
}

const strings = (value: unknown, limit = 64): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === "string").slice(0, limit) : []);

/** What `compilation_raw_open` returned, checked field by field. */
export function parseRawRun(value: unknown): RawRun | null {
  const object = (value ?? {}) as Record<string, unknown>;
  if (typeof object.dir !== "string" || !object.dir) return null;
  const program = (object.program ?? {}) as Record<string, unknown>;
  const review = (program.review ?? {}) as Record<string, Record<string, unknown>>;
  const parts: RawPart[] = (Array.isArray(program.parts) ? program.parts : []).flatMap((raw) => {
    const part = (raw ?? {}) as Record<string, unknown>;
    if (typeof part.id !== "string") return [];
    const state = review[part.id] ?? {};
    const summary: Record<string, number> = {};
    for (const [key, n] of Object.entries((state.summary ?? {}) as Record<string, unknown>)) if (typeof n === "number") summary[key] = n;
    return [{ id: part.id, kind: str(part.kind), title: str(part.title), rank: num(part.rank), narration: str(part.narration),
      sources: strings(part.sources, 16), startMs: num(part.start_ms) ?? 0, durationMs: num(part.duration_ms) ?? 0,
      unreviewed: num(state.unreviewed) ?? 0, accepted: state.accept_unreviewed === true, summary }];
  });
  const ranking = (program.ranking ?? {}) as Record<string, unknown>;
  const suggestionValue = (ranking.suggestion ?? null) as Record<string, unknown> | null;
  const suggestion = suggestionValue && Array.isArray(suggestionValue.order)
    ? { order: strings(suggestionValue.order, MAX_ITEMS), reasons: Object.fromEntries(Object.entries((suggestionValue.reasons ?? {}) as Record<string, unknown>)
      .filter(([, reason]) => typeof reason === "string").map(([id, reason]) => [id, reason as string])) }
    : null;
  return {
    dir: object.dir, spec: object.spec, status: parseStatus(object.status), parts, video: str(object.video) || null, final: str(object.final) || null,
    finalState: str(object.final) ? parseFinalState(object.final_state) : null,
    suggestion, applied: ((object.spec ?? {}) as Record<string, Record<string, unknown>>).program?.apply_suggestion === true,
    rendered: strings(program.rendered_parts), reused: strings(program.reused_parts), warnings: strings(program.warnings, 30),
    credits: strings(program.credits, 60),
  };
}

/** How the page names a part: "Câu mở màn", "Mở đầu", "#3 · Tiêu đề" (Top N), "Tiêu đề" (danh sách), "Kết thúc". */
export function partName(part: Pick<RawPart, "id" | "kind" | "title" | "rank">, kind: RawDraft["kind"]): string {
  if (part.id === "hook") return "Câu mở màn";
  if (part.id === "intro") return "Mở đầu";
  if (part.id === "outro") return "Kết thúc";
  return kind === "top_n" && part.rank !== null ? `#${part.rank} · ${part.title}` : part.title;
}

/** A shot's source as the panel names it: `i2-s1` → "Mục 2 · nguồn 1 (tên tệp)". */
export function sourceName(sourceId: string, spec: unknown): string {
  const match = /^(i\d{1,2})-s(\d)$/.exec(sourceId);
  if (!match) return sourceId;
  const items = ((spec ?? {}) as Record<string, unknown>).items;
  const index = Array.isArray(items) ? items.findIndex((item) => (item as Record<string, unknown>)?.item_id === match[1]) : -1;
  const sources = index >= 0 ? (items as Array<Record<string, unknown>>)[index].sources : null;
  const video = Array.isArray(sources) ? str((sources[Number(match[2]) - 1] as Record<string, unknown> | undefined)?.video) : "";
  return `Mục ${index >= 0 ? index + 1 : "?"} · nguồn ${match[2]}${video ? ` (${fileName(video)})` : ""}`;
}

/** The parts that must be reviewed (or accepted) before the final export, and how many sentences are open. */
export function openForExport(run: RawRun): { parts: RawPart[]; sentences: number } {
  const parts = run.parts.filter((part) => part.unreviewed > 0 && !part.accepted);
  return { parts, sentences: parts.reduce((sum, part) => sum + part.unreviewed, 0) };
}

/** One line of what the last render did: parts made again and parts reused. */
export function renderStats(run: RawRun): string {
  if (!run.rendered.length && !run.reused.length) return "";
  return `Lần dựng gần nhất: dựng ${run.rendered.length} phần, dùng lại ${run.reused.length} phần không đổi.`;
}

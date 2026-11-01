/**
 * The Phụ đề mode of the editor (Issue #414): the line list, the style
 * panel, the live preview over the clean source picture with the dubbed mix,
 * dragging the subtitle block, and "Áp dụng", which makes a new version of
 * the job through the review re-run.
 */
import type { QueueJob } from "../queue/model.ts";
import type { ConfirmOptions } from "../../shell/confirm.ts";
import { formatClock, MAIN, outputToSource, type Edit } from "./model.ts";
import {
  applyPreset, changeSummary, cleanText, cueAt, cueOutputTime, DEFAULT_STYLE, dragPosition, isColor, normalizeStyle, parseStyle,
  presetOf, PRESETS, previewMetrics, regionOn, rgba, sameStyle, STYLE_STORAGE_KEY, SUBTITLE_FONTS, subtitleChanges, BOX_SWATCHES,
  OUTLINE_SWATCHES, TEXT_SWATCHES, withoutPosition, type PresetId, type SubtitleCue, type SubtitleStyle,
} from "./subtitles.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Region = { x: number; y: number; w: number; h: number; start_ms?: number; end_ms?: number };
type LoadResponse = {
  cues: SubtitleCue[]; style: unknown; source_path: string | null; mix_path: string | null; dubbed: boolean; blur_regions: Region[];
};
type Rect = { left: number; top: number; width: number; height: number };

export type SubtitleHost = {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  showError: (text: string) => void;
  mediaUrl: (path: string) => Promise<string | null>;
  /** Queue the new version prepared in `outputDir` and start it; its job id. */
  revise: (job: QueueJob, outputDir: string) => Promise<string | null>;
};

export type SubtitleContext = {
  video: HTMLVideoElement;
  /** The picture inside the player frame. */
  picture: () => Rect;
  edit: () => Edit;
  outputTime: () => number;
  playing: () => boolean;
  /** The player showing the output right now (the main video or an inserted clip's). */
  active: () => HTMLVideoElement;
  seek: (time: number) => void;
  /** Save the cut/insert draft now, so the new version gets it. */
  flush: () => Promise<void>;
  /** Leave edit mode once the new version is queued. */
  finish: () => Promise<void>;
  /** The footer or a badge changed. */
  changed: () => void;
};

export type SubtitleMode = {
  /** A new editing session. */
  begin(job: QueueJob, outputPath: string): void;
  /** The session ends. */
  end(): void;
  enter(): void;
  leave(): void;
  readonly active: boolean;
  /** Draw the line on screen, the covers and keep the dubbed audio in step. */
  frame(): void;
  /** The cut/insert edit changed: line times and cut lines. */
  editChanged(): void;
  /** The volume of the playing main-video segment, heard through the dubbed mix. */
  volume(level: number): void;
  /** Lines, marks or the look changed and are not applied yet. */
  dirty(): boolean;
  /** Revisions made by "Áp dụng" that are still to be offered for editing. */
  readonly pending: Set<string>;
};

const LIST_STEP = 200;
const SAMPLE = "Phụ đề tiếng Việt mẫu";
const MIC = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" fill="none"/></svg>';
const FALLBACK_NOTE = "Đang xem trên video đã xuất: phụ đề cũ đã in sẵn trong video nên có thể thấy hai lớp chữ. Video xuất ra sẽ chỉ có phụ đề mới.";

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing subtitle element: ${selector}`);
  return node;
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

/** Text drawn as libass draws it: outline outside the glyphs, an optional box behind the block. */
export function paintSubtitle(node: HTMLElement, style: SubtitleStyle, picture: { width: number; height: number }): void {
  const metrics = previewMetrics(style, picture);
  Object.assign(node.style, {
    fontFamily: `"${style.font}", sans-serif`,
    fontSize: `${metrics.fontPx}px`,
    fontWeight: style.bold ? "700" : "400",
    lineHeight: String(metrics.lineHeight),
    color: style.primary,
    webkitTextStroke: metrics.outlinePx > 0 ? `${metrics.outlinePx * 2}px ${style.outline}` : "0",
    background: style.box ? rgba(style.box, style.box_opacity) : "transparent",
    padding: style.box ? `${metrics.paddingPx}px` : "0",
  });
}

export function mountSubtitles(invoke: Invoke, host: SubtitleHost, context: SubtitleContext): SubtitleMode {
  const { video } = context;
  const layer = $("#edit-subs-layer");
  const block = $("#subs-block");
  const text = $("#subs-text");
  const covers = $("#subs-covers");
  const note = $("#subs-note");
  const guides = { center: layer.querySelector<HTMLElement>(".subs-guide.center")!, top: layer.querySelector<HTMLElement>(".subs-guide.top")!,
    bottom: layer.querySelector<HTMLElement>(".subs-guide.bottom")! };
  const section = $("#edit-subs");
  const list = $<HTMLOListElement>("#subs-list");
  const more = $("#subs-more");
  const loading = $("#subs-loading");
  const tabs = [...document.querySelectorAll<HTMLButtonElement>("#subs-tabs [data-pane]")];
  const foot = $("#subs-foot");
  const applyButton = $<HTMLButtonElement>("#subs-apply");
  const size = $<HTMLInputElement>("#subs-size");
  const outlineWidth = $<HTMLInputElement>("#subs-outline-width");
  const bold = $<HTMLInputElement>("#subs-bold");
  const boxToggle = $<HTMLInputElement>("#subs-box");
  const boxOpacity = $<HTMLInputElement>("#subs-box-opacity");
  const alignButtons = [...document.querySelectorAll<HTMLButtonElement>("#subs-align [data-align]")];
  const audio = new Audio();
  audio.preload = "auto";

  let job: QueueJob | null = null;
  let outputPath = "";
  let data: LoadResponse | null = null;
  let loadToken = 0;
  let active = false;
  let pane: "lines" | "style" = "lines";
  const drafts = new Map<string, string>();
  const marked = new Set<string>();
  let style: SubtitleStyle = DEFAULT_STYLE;
  let baseStyle: SubtitleStyle = DEFAULT_STYLE;
  let limit = LIST_STEP;
  let current: string | null = null;
  let applying = false;
  // The preview's own sources: the clean source video and the dubbed mix.
  let swapped = false;
  let savedSrc: string | null = null;
  let usingMix = false;
  let swapToken = 0;
  let level = 1;
  const pending = new Set<string>();
  type Drag = { pointer: number; x: number; y: number; start: { x: number; y: number }; size: { width: number; height: number }; moved: boolean };
  let drag: Drag | null = null;
  /** What the subtitle block last drew (text, look, picture size). */
  let painted = "";

  const cues = (): SubtitleCue[] => data?.cues ?? [];
  const draftOf = (cueId: string): string => drafts.get(cueId) ?? cues().find((cue) => cue.cue_id === cueId)?.text ?? "";
  const dubbed = (): boolean => !!data?.dubbed;

  // ---------- Loading ----------

  async function load(): Promise<void> {
    if (!job) return;
    const token = ++loadToken;
    loading.hidden = false;
    loading.textContent = "Đang đọc phụ đề…";
    try {
      const response = await invoke<LoadResponse>("edit_subtitles_load", { outputPath });
      if (token !== loadToken) return;
      data = { ...response, cues: [...response.cues].sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms) };
      baseStyle = normalizeStyle(parseStyle(response.style) ?? DEFAULT_STYLE);
      style = baseStyle;
      loading.hidden = true;
      renderList();
      renderStyle();
      renderFoot();
      if (active) void swapIn();
      context.changed();
    } catch (error) {
      if (token !== loadToken) return;
      loading.textContent = `Không đọc được phụ đề của video này: ${String(error).slice(0, 200)}`;
    }
  }

  // ---------- Preview sources ----------

  function showNote(message: string | null): void {
    note.hidden = !message;
    note.textContent = message ?? "";
  }

  /** Play the clean source muted with the dubbed mix beside it, or explain why not. */
  async function swapIn(): Promise<void> {
    if (!data || !active || swapped) return;
    const token = ++swapToken;
    const usable = !!data.source_path && (!!data.mix_path || !data.dubbed);
    if (!usable) {
      showNote(data.dubbed && data.source_path ? `${FALLBACK_NOTE} Không tìm thấy bản lồng tiếng.` : FALLBACK_NOTE);
      return;
    }
    let sourceUrl: string | null = null, mixUrl: string | null = null;
    try {
      [sourceUrl, mixUrl] = await Promise.all([host.mediaUrl(data.source_path!), data.mix_path ? host.mediaUrl(data.mix_path) : Promise.resolve(null)]);
    } catch {
      sourceUrl = null;
    }
    if (token !== swapToken || !active || swapped) return;
    if (!sourceUrl || (data.mix_path && !mixUrl)) {
      showNote(FALLBACK_NOTE);
      return;
    }
    showNote(null);
    swapped = true;
    savedSrc = video.getAttribute("src");
    usingMix = !!mixUrl;
    if (mixUrl) audio.src = mixUrl;
    video.src = sourceUrl;
    video.muted = usingMix;
  }

  function swapOut(): void {
    swapToken += 1;
    if (!swapped) return;
    swapped = false;
    audio.pause();
    audio.removeAttribute("src");
    audio.load();
    usingMix = false;
    video.muted = false;
    if (savedSrc) video.src = savedSrc;
    else video.removeAttribute("src");
    savedSrc = null;
  }

  video.addEventListener("error", () => {
    if (!swapped) return;
    swapOut();
    showNote(`${FALLBACK_NOTE} Trình xem chưa đọc được video gốc.`);
  });
  for (const name of ["play", "pause", "seeked", "ratechange", "waiting"]) video.addEventListener(name, () => { if (active) syncAudio(); });

  /** The dubbed mix follows the source video while a main-video segment plays. */
  function syncAudio(): void {
    if (!usingMix) return;
    const onMain = context.active() === video && !video.paused && context.playing() && video.readyState >= 2;
    if (audio.readyState >= 1 && Math.abs(audio.currentTime - video.currentTime) > (onMain ? 0.25 : 0.04)) audio.currentTime = video.currentTime;
    audio.playbackRate = video.playbackRate;
    audio.volume = Math.min(1, level);
    audio.muted = level === 0;
    if (onMain && audio.paused) void audio.play().catch(() => undefined);
    else if (!onMain && !audio.paused) audio.pause();
  }

  // ---------- Preview drawing ----------

  function mainSeconds(): number | null {
    const point = outputToSource(context.edit(), context.outputTime());
    return point && point.segment.mediaId === MAIN ? point.sourceTime : null;
  }

  function placeBlock(rect: Rect): void {
    const metrics = previewMetrics(style, rect);
    const width = Math.max(0, rect.width - metrics.marginX * 2);
    Object.assign(block.style, { width: `${width}px`, left: "", top: "", bottom: "", transform: "" });
    if (style.x !== undefined && style.y !== undefined) {
      Object.assign(block.style, { left: `${style.x * rect.width - width / 2}px`, top: `${style.y * rect.height}px`, transform: "translateY(-50%)" });
    } else if (style.align === "top") {
      Object.assign(block.style, { left: `${metrics.marginX}px`, top: `${metrics.marginV}px` });
    } else if (style.align === "middle") {
      Object.assign(block.style, { left: `${metrics.marginX}px`, top: "50%", transform: "translateY(-50%)" });
    } else {
      Object.assign(block.style, { left: `${metrics.marginX}px`, bottom: `${metrics.marginV}px` });
    }
  }

  /**
   * A wrapped inline block is as wide as its container; libass's box hugs
   * the widest line, so the block is narrowed to it.
   */
  function fitBox(): void {
    text.style.width = "";
    if (!style.box || block.hidden || !text.textContent) return;
    const range = document.createRange();
    range.selectNodeContents(text);
    const widest = Math.max(0, ...[...range.getClientRects()].map((line) => line.width));
    // Border box: the box padding is inside the width.
    const padding = previewMetrics(style, context.picture()).paddingPx;
    if (widest > 0) text.style.width = `${Math.ceil(widest + padding * 2 + 1)}px`;
  }

  function frame(): void {
    if (!active) return;
    const rect = context.picture();
    Object.assign(layer.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` });
    const seconds = mainSeconds();
    const cue = seconds === null ? null : cueAt(cues(), seconds);
    const line = cue ? cleanText(draftOf(cue.cue_id)) : "";
    // On the style tab or while dragging an empty moment shows a sample line to place.
    const sample = !line && (pane === "style" || !!drag) && !!data;
    block.hidden = !line && !sample;
    block.classList.toggle("sample", sample);
    const shown = line || (sample ? SAMPLE : "");
    const key = `${shown}\n${JSON.stringify(style)}\n${rect.width}x${rect.height}`;
    if (key !== painted) {
      painted = key;
      text.textContent = shown;
      paintSubtitle(text, style, rect);
      placeBlock(rect);
      fitBox();
    }
    renderCovers(seconds);
    markCurrent(cue?.cue_id ?? null);
    syncAudio();
  }

  /** The render's caption cover, approximated: blurred rectangles and the dark band. */
  function renderCovers(seconds: number | null): void {
    if (!swapped || seconds === null || !job) {
      covers.replaceChildren();
      return;
    }
    const ms = seconds * 1000;
    const regions = [...(job.blurRegions ?? []), ...(data?.blur_regions ?? [])].filter((region) => regionOn(region, ms));
    const nodes: HTMLElement[] = regions.map((region) => {
      const cover = element("span", "subs-cover");
      Object.assign(cover.style, { left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.w * 100}%`, height: `${region.h * 100}%` });
      return cover;
    });
    if (job.subtitleBox && job.burnIn !== false) nodes.push(element("span", "subs-band"));
    covers.replaceChildren(...nodes);
  }

  function markCurrent(cueId: string | null): void {
    if (cueId === current) return;
    list.querySelector(`.subs-line[data-id="${CSS.escape(current ?? "")}"]`)?.classList.remove("current");
    current = cueId;
    if (!cueId) return;
    const row = list.querySelector<HTMLElement>(`.subs-line[data-id="${CSS.escape(cueId)}"]`);
    row?.classList.add("current");
    if (row && context.playing() && pane === "lines" && !list.contains(document.activeElement)) row.scrollIntoView({ block: "nearest" });
  }

  new ResizeObserver(() => frame()).observe(layer.parentElement!);

  // ---------- Dragging the block ----------


  text.addEventListener("pointerdown", (event) => {
    if (!active || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    text.setPointerCapture(event.pointerId);
    const picture = layer.getBoundingClientRect();
    const box = text.getBoundingClientRect();
    drag = { pointer: event.pointerId, x: event.clientX, y: event.clientY, moved: false, size: { width: box.width, height: box.height },
      start: { x: (box.left + box.width / 2 - picture.left) / Math.max(1, picture.width), y: (box.top + box.height / 2 - picture.top) / Math.max(1, picture.height) } };
    layer.classList.add("dragging");
  });
  text.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointer) return;
    if (!drag.moved && Math.abs(event.clientX - drag.x) + Math.abs(event.clientY - drag.y) < 3) return;
    drag.moved = true;
    const picture = layer.getBoundingClientRect();
    const next = dragPosition(drag.start, { dx: event.clientX - drag.x, dy: event.clientY - drag.y }, drag.size, picture, event.shiftKey);
    style = { ...style, x: next.x, y: next.y };
    for (const [name, guide] of Object.entries(guides)) guide.hidden = !next.guides.includes(name as "center" | "top" | "bottom");
    frame();
  });
  const endDrag = (event: PointerEvent): void => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const moved = drag.moved;
    drag = null;
    layer.classList.remove("dragging");
    for (const guide of Object.values(guides)) guide.hidden = true;
    if (moved) styleChanged();
    else frame();
  };
  text.addEventListener("pointerup", endDrag);
  text.addEventListener("pointercancel", endDrag);
  // A plain click on the text plays or pauses, like a click on the video.
  text.addEventListener("click", (event) => event.stopPropagation());

  // ---------- Line list ----------

  function timeLabel(cue: SubtitleCue): { text: string; cut: boolean; time: number | null } {
    const time = cueOutputTime(context.edit(), cue);
    return time === null ? { text: formatClock(cue.start_ms / 1000), cut: true, time } : { text: formatClock(time), cut: false, time };
  }

  function renderList(): void {
    const all = cues();
    const rows = all.slice(0, limit).map(lineRow);
    list.replaceChildren(...rows);
    more.hidden = all.length <= limit;
    current = null;
    if (!all.length && data) {
      loading.hidden = false;
      loading.textContent = "Video này chưa có câu phụ đề nào.";
    }
  }

  function lineRow(cue: SubtitleCue): HTMLLIElement {
    const row = element("li", "subs-line");
    row.dataset.id = cue.cue_id;
    const head = element("div", "subs-line-head");
    const time = element("button", "subs-time");
    time.type = "button";
    const badges = element("span", "subs-badges");
    head.append(time, badges);
    const input = element("textarea", "subs-input");
    input.rows = 1;
    input.maxLength = 2000;
    input.spellcheck = false;
    input.value = draftOf(cue.cue_id);
    input.setAttribute("aria-label", `Câu lúc ${formatClock(cue.start_ms / 1000)}`);
    input.addEventListener("input", () => {
      if (cleanText(input.value) === cleanText(cue.text)) drafts.delete(cue.cue_id);
      else drafts.set(cue.cue_id, input.value);
      refreshRow(row, cue);
      renderFoot();
      frame();
    });
    input.addEventListener("focus", () => seekTo(cue));
    const source = element("p", "subs-source", cue.source_text);
    source.lang = "zh";
    row.append(head, input, source);
    if (dubbed()) {
      const voice = element("button", "subs-voice");
      voice.type = "button";
      voice.innerHTML = MIC;
      voice.append(element("span", "", "Giọng câu này"));
      voice.title = "Đọc lại giọng câu này theo chữ đã sửa khi bấm Áp dụng";
      voice.addEventListener("click", () => {
        if (marked.has(cue.cue_id)) marked.delete(cue.cue_id);
        else marked.add(cue.cue_id);
        refreshRow(row, cue);
        renderFoot();
      });
      row.append(voice);
    }
    row.addEventListener("click", (event) => {
      if ((event.target as Element).closest("textarea, .subs-voice")) return;
      seekTo(cue);
    });
    refreshRow(row, cue);
    return row;
  }

  function seekTo(cue: SubtitleCue): void {
    const time = cueOutputTime(context.edit(), cue);
    if (time !== null) context.seek(time + 0.001);
  }

  function refreshRow(row: HTMLElement, cue: SubtitleCue): void {
    const label = timeLabel(cue);
    const time = row.querySelector<HTMLButtonElement>(".subs-time")!;
    time.textContent = label.text;
    time.title = label.cut ? "Câu này nằm trong đoạn đã cắt bỏ" : "Đến câu này";
    time.disabled = label.cut;
    row.classList.toggle("cut", label.cut);
    const edited = cleanText(draftOf(cue.cue_id)) !== cleanText(cue.text);
    // The voice engine reads the same text the same way, so a new take only
    // makes sense for changed text or a line that failed to dub.
    const canRevoice = edited || cue.dub_failed;
    if (!canRevoice) marked.delete(cue.cue_id);
    const voiced = marked.has(cue.cue_id);
    const empty = !cleanText(draftOf(cue.cue_id));
    row.classList.toggle("edited", edited);
    row.classList.toggle("invalid", empty);
    const badges = row.querySelector(".subs-badges")!;
    badges.replaceChildren(...[
      label.cut ? element("span", "subs-badge muted", "Đã cắt") : null,
      empty ? element("span", "subs-badge warn", "Trống") : edited ? element("span", "subs-badge", "Đã sửa") : null,
      voiced ? element("span", "subs-badge voice", "Đọc lại") : null,
      cue.dub_failed && !voiced ? element("span", "subs-badge warn", "Chưa lồng tiếng") : null,
    ].filter((node): node is HTMLSpanElement => node !== null));
    const voice = row.querySelector<HTMLButtonElement>(".subs-voice");
    if (voice) {
      voice.disabled = !canRevoice;
      voice.title = canRevoice ? "Đọc lại giọng câu này theo chữ đã sửa khi bấm Áp dụng" : "Sửa chữ câu này trước rồi mới đọc lại giọng";
      voice.setAttribute("aria-pressed", String(voiced));
      voice.setAttribute("aria-label", `Giọng câu này${voiced ? ": sẽ đọc lại" : ""}`);
    }
  }

  new IntersectionObserver((entries) => {
    if (!entries.some((entry) => entry.isIntersecting) || !data || limit >= cues().length) return;
    const from = limit;
    limit += LIST_STEP;
    list.append(...cues().slice(from, limit).map(lineRow));
    more.hidden = cues().length <= limit;
  }, { root: $("#subs-lines-pane"), rootMargin: "400px" }).observe(more);

  // ---------- Style panel ----------

  function swatchRow(container: HTMLElement, colors: readonly string[], name: string, get: () => string | null, set: (value: string) => void): void {
    const buttons = colors.map((color) => {
      const button = element("button", "subs-swatch");
      button.type = "button";
      button.setAttribute("role", "radio");
      button.style.background = color;
      button.dataset.color = color;
      button.title = color;
      button.setAttribute("aria-label", `${name} ${color}`);
      button.addEventListener("click", () => set(color));
      return button;
    });
    const custom = element("label", "subs-swatch custom");
    custom.title = `${name} tùy chọn`;
    const picker = element("input");
    picker.type = "color";
    picker.setAttribute("aria-label", `${name} tùy chọn`);
    picker.addEventListener("input", () => set(picker.value.toUpperCase()));
    custom.append(picker);
    container.replaceChildren(...buttons, custom);
    container.dataset.name = name;
    swatchState.push(() => {
      const value = get();
      let matched = false;
      for (const button of buttons) {
        const on = button.dataset.color === value;
        matched ||= on;
        button.setAttribute("aria-checked", String(on));
      }
      custom.classList.toggle("on", !!value && !matched);
      if (value && isColor(value)) {
        picker.value = value.toLowerCase();
        custom.style.setProperty("--custom", value);
      }
    });
  }
  const swatchState: Array<() => void> = [];
  swatchRow($("#subs-primary"), TEXT_SWATCHES, "Màu chữ", () => style.primary, (color) => setStyle({ primary: color }));
  swatchRow($("#subs-outline"), OUTLINE_SWATCHES, "Màu viền", () => style.outline, (color) => setStyle({ outline: color }));
  swatchRow($("#subs-box-color"), BOX_SWATCHES, "Màu hộp", () => style.box, (color) => setStyle({ box: color }));

  const presetButtons = PRESETS.map((preset) => {
    const button = element("button", "subs-preset");
    button.type = "button";
    button.setAttribute("role", "radio");
    button.dataset.preset = preset.id;
    const sample = element("span", "subs-preset-sample", "Phụ đề");
    paintSubtitle(sample, { ...preset.look, align: "bottom" }, { width: 1920 * 0.45, height: 1080 * 0.45 });
    button.append(sample, element("span", "subs-preset-label", preset.label));
    button.addEventListener("click", () => {
      style = applyPreset(style, preset.id as PresetId);
      styleChanged();
    });
    return button;
  });
  $("#subs-presets").replaceChildren(...presetButtons);

  const fontButtons = SUBTITLE_FONTS.map((font) => {
    const button = element("button", "subs-font");
    button.type = "button";
    button.setAttribute("role", "radio");
    button.dataset.font = font;
    button.setAttribute("aria-label", font);
    button.title = font;
    const sample = element("span", "subs-font-sample", "Chữ Việt");
    sample.style.fontFamily = `"${font}", sans-serif`;
    button.append(sample, element("span", "subs-font-name", font));
    button.addEventListener("click", () => setStyle({ font }));
    return button;
  });
  $("#subs-fonts").replaceChildren(...fontButtons);

  function setStyle(change: Partial<SubtitleStyle>): void {
    style = normalizeStyle({ ...style, ...change });
    styleChanged();
  }

  function styleChanged(): void {
    style = normalizeStyle(style);
    renderStyle();
    renderFoot();
    frame();
  }

  function renderStyle(): void {
    const preset = presetOf(style);
    for (const button of presetButtons) button.setAttribute("aria-checked", String(button.dataset.preset === preset));
    for (const button of fontButtons) button.setAttribute("aria-checked", String(button.dataset.font === style.font));
    $("#subs-font-name").textContent = style.font;
    if (document.activeElement !== size) size.value = String(Math.round(style.size * 100));
    $("#subs-size-value").textContent = `${Math.round(style.size * 100)}%`;
    if (document.activeElement !== outlineWidth) outlineWidth.value = String(style.outline_width);
    $("#subs-outline-value").textContent = String(style.outline_width).replace(".", ",");
    bold.checked = style.bold;
    boxToggle.checked = style.box !== null;
    $("#subs-box-options").hidden = style.box === null;
    if (document.activeElement !== boxOpacity) boxOpacity.value = String(Math.round(style.box_opacity * 100));
    $("#subs-box-opacity-value").textContent = `${Math.round(style.box_opacity * 100)}%`;
    const placed = style.x !== undefined;
    for (const button of alignButtons) button.setAttribute("aria-checked", String(!placed && button.dataset.align === style.align));
    $<HTMLButtonElement>("#subs-reset-position").disabled = !placed && style.align === "bottom";
    $("#subs-position-note").textContent = placed
      ? "Đang dùng vị trí đã kéo. Chọn Trên, Giữa, Dưới hoặc Đặt lại vị trí để bỏ."
      : "Kéo khối phụ đề trên video để đổi chỗ; giữ Shift để kéo cả sang ngang.";
    for (const update of swatchState) update();
  }

  size.addEventListener("input", () => setStyle({ size: Number(size.value) / 100 }));
  outlineWidth.addEventListener("input", () => setStyle({ outline_width: Number(outlineWidth.value) }));
  boxOpacity.addEventListener("input", () => setStyle({ box_opacity: Number(boxOpacity.value) / 100 }));
  bold.addEventListener("change", () => setStyle({ bold: bold.checked }));
  boxToggle.addEventListener("change", () => setStyle({ box: boxToggle.checked ? style.box ?? "#000000" : null }));
  for (const button of alignButtons) {
    button.addEventListener("click", () => {
      style = { ...withoutPosition(style), align: button.dataset.align as SubtitleStyle["align"] };
      styleChanged();
    });
  }
  $("#subs-reset-position").addEventListener("click", () => {
    style = { ...withoutPosition(style), align: "bottom" };
    styleChanged();
  });

  for (const tab of tabs) {
    tab.addEventListener("click", () => {
      pane = tab.dataset.pane === "style" ? "style" : "lines";
      renderPane();
      frame();
    });
  }

  function renderPane(): void {
    for (const tab of tabs) tab.setAttribute("aria-selected", String(tab.dataset.pane === pane));
    $("#subs-lines-pane").hidden = pane !== "lines";
    $("#subs-style-pane").hidden = pane !== "style";
  }

  // ---------- Footer, "Áp dụng" ----------

  function pendingChanges() {
    const changes = subtitleChanges(cues(), draftOf, marked);
    const styleDiffers = !sameStyle(style, baseStyle);
    return { changes, styleDiffers, any: changes.edited > 0 || changes.revoice.length > 0 || styleDiffers };
  }

  function renderFoot(): void {
    const { changes, styleDiffers, any } = pendingChanges();
    const summary = $("#subs-summary");
    summary.textContent = changes.empty ? `${changes.empty} câu đang trống` : changeSummary(changes.edited, changes.revoice.length, styleDiffers, true);
    summary.title = changeSummary(changes.edited, changes.revoice.length, styleDiffers);
    summary.classList.toggle("warn", changes.empty > 0);
    applyButton.disabled = applying || !data || !any || changes.empty > 0 || changes.tooLong > 0;
    applyButton.textContent = applying ? "Đang áp dụng…" : "Áp dụng";
    applyButton.title = applyButton.disabled && !applying ? "Sửa câu, đánh dấu đọc lại giọng hoặc đổi kiểu chữ để áp dụng" : "Tạo phiên bản mới với phụ đề này";
    context.changed();
  }

  async function apply(): Promise<void> {
    if (!job || !data || applying) return;
    const { changes, styleDiffers, any } = pendingChanges();
    if (!any || changes.empty) return;
    const revoiced = changes.revoice.length;
    const confirmed = await host.confirm({
      title: "Áp dụng phụ đề?",
      message: `${changeSummary(changes.edited, revoiced, styleDiffers)}. Sẽ tạo phiên bản mới; ${revoiced
        ? "các câu được đánh dấu sẽ đọc lại giọng, các câu khác giữ giọng cũ"
        : "giọng đọc giữ nguyên"}. Bước nhận dạng và dịch được dùng lại nên thường chỉ mất vài phút. Video hiện tại không bị thay đổi.`,
      confirmLabel: "Áp dụng",
      cancelLabel: "Xem lại",
    });
    if (!confirmed || !job) return;
    applying = true;
    renderFoot();
    const source = job;
    try {
      await context.flush();
      const applied = normalizeStyle(style);
      const folder = await invoke<string>("edit_subtitles_apply", {
        outputPath, overrides: changes.overrides, voice: changes.voice, revoice: changes.revoice, subtitleStyle: applied,
      });
      try {
        window.localStorage.setItem(STYLE_STORAGE_KEY, JSON.stringify(applied));
      } catch {
        // The remembered default is a convenience; the new version has its style.
      }
      const id = await host.revise(source, folder);
      if (id) pending.add(id);
      drafts.clear();
      marked.clear();
      baseStyle = applied;
      await context.finish();
    } catch (error) {
      host.showError(`Không áp dụng được phụ đề: ${String(error).slice(0, 200)}`);
    } finally {
      applying = false;
      renderFoot();
    }
  }
  applyButton.addEventListener("click", () => void apply());

  // ---------- Mode ----------

  function show(on: boolean): void {
    section.hidden = !on;
    foot.hidden = !on;
    layer.hidden = !on;
  }

  return {
    pending,
    get active() { return active; },
    begin(next, path) {
      job = next;
      outputPath = path;
      data = null;
      loadToken += 1;
      drafts.clear();
      marked.clear();
      style = DEFAULT_STYLE;
      baseStyle = DEFAULT_STYLE;
      limit = LIST_STEP;
      current = null;
      list.replaceChildren();
      showNote(null);
      renderPane();
      renderFoot();
    },
    end() {
      this.leave();
      job = null;
      data = null;
      loadToken += 1;
      drafts.clear();
      marked.clear();
      list.replaceChildren();
    },
    enter() {
      if (active) return;
      active = true;
      show(true);
      renderPane();
      if (!data) void load();
      else {
        renderStyle();
        renderFoot();
        void swapIn();
      }
      frame();
    },
    leave() {
      if (!active) return;
      active = false;
      show(false);
      swapOut();
      showNote(null);
      covers.replaceChildren();
    },
    frame,
    editChanged() {
      if (!active || !data) return;
      const byId = new Map(cues().map((cue) => [cue.cue_id, cue]));
      for (const row of list.querySelectorAll<HTMLElement>(".subs-line")) {
        const cue = byId.get(row.dataset.id ?? "");
        if (cue) refreshRow(row, cue);
      }
    },
    volume(value) {
      level = value;
      if (swapped && usingMix) video.muted = true;
      syncAudio();
    },
    dirty() {
      return !!data && pendingChanges().any;
    },
  };
}

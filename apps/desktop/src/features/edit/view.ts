/**
 * Edit mode on the queue screen (Issue #408): the Cắt ghép panel, the
 * timeline, the live preview through the queue player, autosave of the draft,
 * the "Xuất video" dialog and the "Bản đã sửa" list in Kết quả.
 */
import type { QueueJob } from "../queue/model.ts";
import type { ConfirmOptions } from "../../shell/confirm.ts";
import {
  createEdit, defaultExportName, EditHistory, estimateExportBytes, exportSize, formatClock, formatSpeed, isUnchanged,
  outputDuration, outputStarts, outputToSource, parseClock, parseProject, remapCues, remove, sameSegments,
  serializeProject, setSpeed, setVolume, sourceToOutput, SPEED_PRESETS, split, thumbGrid, tickStep, trim, zoomScale,
  type Cue, type Edit, type Quality, type Segment,
} from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Facts = { duration: number; width: number; height: number; size_bytes: number; has_audio: boolean; cues: Cue[] };
type LoadResponse = Facts & { project: unknown };
type ExportEntry = {
  path: string; srt_path?: string | null; created: number; duration: number; quality?: string;
  segments?: Array<Pick<Segment, "start" | "end" | "speed" | "volume">>;
};
type ExportState = "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
type ExportStatus = { state: ExportState; percent: number; message: string; output_path: string | null };
type StatusResponse = { status: ExportStatus | null; exports: ExportEntry[] };

export type EditorHost = {
  video: HTMLVideoElement;
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  /** Re-render the queue screen after entering or leaving edit mode. */
  refresh: () => void;
  /** Redraw the player's play button and time. */
  renderPlayer: () => void;
  showError: (text: string) => void;
  /** Play a finished export in the in-app player. */
  playExport: (jobId: string, path: string) => void;
};

export type Editor = {
  /** The job whose edit is open, if any. */
  readonly jobId: string | null;
  /** The selected job, on every render: the Kết quả list follows it and edit mode closes when it changes. */
  select(job: QueueJob | null): void;
  /** Output time / output length while editing, else null. */
  timeLabel(): string | null;
  togglePlay(): void;
  /** Write a pending autosave now. */
  flush(): Promise<void>;
};

type Session = {
  job: QueueJob;
  outputPath: string;
  facts: Facts;
  history: EditHistory;
  /** An uncommitted change while dragging a handle or a slider. */
  draft: Edit | null;
  selected: string | null;
  outputTime: number;
  /** Segment being played. */
  index: number;
  /** The edit last sent to "Xuất video", marked exported when it finishes. */
  exporting: Edit | null;
};

const PAD = 12;
const THUMB_WIDTH = 76;
const THUMB_PX = 192;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing editor element: ${selector}`);
  return node;
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

function folderOf(path: string): string {
  return path.replace(/[\\/][^\\/]*$/, "");
}

function megabytes(bytes: number): string {
  if (bytes <= 0) return "";
  return bytes >= 1_000_000_000 ? `khoảng ${(bytes / 1_000_000_000).toFixed(1).replace(".", ",")} GB` : `khoảng ${Math.max(1, Math.round(bytes / 1_000_000))} MB`;
}

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

export function mountEditor(invoke: Invoke, host: EditorHost): Editor {
  const { video } = host;
  const screen = $("#queue-content");
  const openButton = $<HTMLButtonElement>("#edit-open");
  const undoButton = $<HTMLButtonElement>("#edit-undo");
  const redoButton = $<HTMLButtonElement>("#edit-redo");
  const changesBadge = $("#edit-changes");
  const exportButton = $<HTMLButtonElement>("#edit-export");
  const startInput = $<HTMLInputElement>("#edit-start");
  const endInput = $<HTMLInputElement>("#edit-end");
  const speeds = $("#edit-speeds");
  const customSpeed = $<HTMLInputElement>("#edit-speed-custom");
  const volume = $<HTMLInputElement>("#edit-volume");
  const scroll = $("#tl-scroll");
  const content = $("#tl-content");
  const ruler = $("#tl-ruler");
  const track = $("#tl-track");
  const playhead = $("#tl-playhead");
  const zoom = $<HTMLInputElement>("#tl-zoom");
  const dialog = $("#export-dialog");
  const nameInput = $<HTMLInputElement>("#export-name");
  const qualityButtons = [...document.querySelectorAll<HTMLButtonElement>("#export-quality [data-quality]")];

  let session: Session | null = null;
  let saveTimer = 0;
  let saving: Promise<void> = Promise.resolve();
  let loop = 0;
  let pps = 1;
  let opening = false;
  // Thumbnails: one frame per grid cell of the open video, fetched lazily.
  let grid = { step: 1, times: [] as number[] };
  let thumbsFor = "";
  const thumbs = new Map<number, string | null>();
  const wanted: number[] = [];
  let thumbWorkers = 0;
  // Kết quả list and export progress for the selected job.
  let listJob: QueueJob | null = null;
  let listStatus: StatusResponse | null = null;
  let pollTimer = 0;
  let pollToken = 0;
  // Export dialog.
  let folder = "";
  let quality: Quality = "source";
  /** The dialog started an export and waits for it. */
  let dialogWaiting = false;
  let dialogDone: string | null = null;

  const current = (): Edit => session!.draft ?? session!.history.current;
  const hasSource = (): boolean => !!video.getAttribute("src") && video.readyState >= 1;
  const unexported = (): boolean => !!session && session.history.changes > 0 && !isUnchanged(session.history.current, session.facts.duration);

  // ---------- Playback: the player plays the edited output ----------

  function applySegment(segment: Segment): void {
    video.playbackRate = segment.speed;
    // Above 100% is heard only after export.
    video.volume = Math.min(1, segment.volume);
    video.muted = segment.volume === 0;
  }

  function seekOutput(time: number): void {
    if (!session) return;
    const edit = current();
    session.outputTime = clamp(time, 0, outputDuration(edit));
    const point = outputToSource(edit, session.outputTime);
    if (point) {
      session.index = point.index;
      applySegment(point.segment);
      if (hasSource() && Math.abs(video.currentTime - point.sourceTime) > 0.001) video.currentTime = point.sourceTime;
    }
    renderPlayhead();
    host.renderPlayer();
  }

  function tick(): void {
    loop = 0;
    if (!session || video.paused) return;
    const edit = current();
    const starts = outputStarts(edit);
    let index = Math.min(session.index, edit.segments.length - 1);
    let segment = edit.segments[index];
    const now = video.currentTime;
    if (now < segment.start - 0.3 || now > segment.end + 0.3) {
      // The player moved on its own (or the edit changed): follow the output position.
      const time = sourceToOutput(edit, now, index);
      if (time === null) seekOutput(session.outputTime);
      else session.outputTime = time;
      index = outputToSource(edit, session.outputTime)?.index ?? index;
      session.index = index;
      applySegment(edit.segments[index]);
    } else if (now >= segment.end - 0.02) {
      if (index + 1 < edit.segments.length) {
        index += 1;
        segment = edit.segments[index];
        session.index = index;
        session.outputTime = starts[index];
        applySegment(segment);
        // A plain split continues without a seek.
        if (Math.abs(segment.start - now) > 0.05) video.currentTime = segment.start;
      } else {
        session.outputTime = outputDuration(edit);
        video.pause();
      }
    } else {
      session.outputTime = starts[index] + Math.max(0, now - segment.start) / segment.speed;
    }
    renderPlayhead(true);
    host.renderPlayer();
    if (!video.paused) loop = requestAnimationFrame(tick);
  }

  video.addEventListener("play", () => {
    if (session && !loop) loop = requestAnimationFrame(tick);
  });
  video.addEventListener("ended", () => {
    if (!session) return;
    const edit = current();
    if (session.index + 1 < edit.segments.length) {
      seekOutput(outputStarts(edit)[session.index + 1]);
      void video.play().catch(() => undefined);
    }
  });
  video.addEventListener("loadedmetadata", () => {
    if (session) seekOutput(session.outputTime);
  });

  function togglePlay(): void {
    if (!session || !hasSource()) return;
    if (video.paused || video.ended) {
      if (session.outputTime >= outputDuration(current()) - 0.05) seekOutput(0);
      else seekOutput(session.outputTime);
      void video.play().catch(() => undefined);
    } else {
      video.pause();
    }
  }

  function restorePlayer(): void {
    if (loop) cancelAnimationFrame(loop);
    loop = 0;
    video.playbackRate = 1;
    video.volume = 1;
    video.muted = false;
  }

  // ---------- Changes, history, autosave ----------

  /** After an edit changes, keep the playhead on the same picture when it still exists. */
  function settle(before: Edit, after: Edit): void {
    if (!session) return;
    const point = outputToSource(before, session.outputTime);
    let time: number | null = null;
    if (point) {
      const index = after.segments.findIndex((segment) => segment.id === point.segment.id);
      time = sourceToOutput(after, point.sourceTime, index);
      if (time === null && index < 0) time = outputStarts(after)[Math.min(point.index, after.segments.length - 1)] ?? 0;
    }
    if (!after.segments.some((segment) => segment.id === session!.selected)) {
      session.selected = outputToSource(after, time ?? session.outputTime)?.segment.id ?? after.segments[0].id;
    }
    seekOutput(time ?? session.outputTime);
  }

  function commit(next: Edit, selected?: string): void {
    if (!session) return;
    const before = session.history.current;
    session.draft = null;
    if (!session.history.push(next)) {
      render();
      return;
    }
    if (selected) session.selected = selected;
    settle(before, next);
    render();
    scheduleSave();
  }

  function undo(): void {
    if (!session || session.draft) return;
    const before = session.history.current;
    const edit = session.history.undo();
    if (!edit) return;
    settle(before, edit);
    render();
    scheduleSave();
  }

  function redo(): void {
    if (!session || session.draft) return;
    const before = session.history.current;
    const edit = session.history.redo();
    if (!edit) return;
    settle(before, edit);
    render();
    scheduleSave();
  }

  async function save(): Promise<void> {
    if (!session) return;
    const { outputPath, history, selected } = session;
    try {
      await invoke("edit_project_save", { outputPath, project: serializeProject(history.current, history.changes, selected) });
      $("#edit-save-state").textContent = "";
    } catch (error) {
      $("#edit-save-state").textContent = `Chưa lưu được bản nháp: ${String(error).slice(0, 140)}`;
    }
  }

  function scheduleSave(): void {
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      saveTimer = 0;
      saving = save();
    }, 500);
  }

  async function flush(): Promise<void> {
    if (saveTimer) {
      window.clearTimeout(saveTimer);
      saveTimer = 0;
      saving = save();
    }
    await saving;
  }

  // ---------- Operations ----------

  function note(text: string): void {
    const hint = $("#edit-hint");
    hint.textContent = text;
    window.setTimeout(() => { if (hint.textContent === text) hint.textContent = ""; }, 4000);
  }

  function selectedSegment(): Segment | undefined {
    if (!session) return undefined;
    const edit = current();
    return edit.segments.find((segment) => segment.id === session!.selected)
      ?? outputToSource(edit, session.outputTime)?.segment;
  }

  function splitAtPlayhead(): void {
    if (!session || session.draft) return;
    const edit = session.history.current;
    const next = split(edit, session.outputTime);
    if (next === edit) {
      note(edit.segments.length >= 500 ? "Đã đạt tối đa 500 đoạn." : "Đầu phát đang sát mép đoạn; dời đi một chút rồi tách.");
      return;
    }
    commit(next, outputToSource(next, session.outputTime)?.segment.id);
  }

  function deleteSelected(): void {
    if (!session || session.draft) return;
    const edit = session.history.current;
    const segment = selectedSegment();
    if (!segment) return;
    if (edit.segments.length <= 1) {
      note("Cần giữ lại ít nhất một đoạn.");
      return;
    }
    const index = edit.segments.indexOf(segment);
    const next = remove(edit, segment.id);
    const time = outputStarts(next)[Math.min(index, next.segments.length - 1)];
    session.outputTime = index < next.segments.length ? time : outputDuration(next);
    commit(next, outputToSource(next, Math.min(time, outputDuration(next) - 0.001))?.segment.id);
  }

  function step(seconds: number): void {
    if (session) seekOutput(session.outputTime + seconds);
  }

  // ---------- Rendering ----------

  function renderPlayhead(follow = false): void {
    if (!session) return;
    const x = PAD + session.outputTime * pps;
    playhead.style.transform = `translateX(${x}px)`;
    if (follow && (x < scroll.scrollLeft + 24 || x > scroll.scrollLeft + scroll.clientWidth - 24)) {
      scroll.scrollLeft = Math.max(0, x - scroll.clientWidth * 0.25);
    }
  }

  function fitScale(): number {
    const total = session ? outputDuration(current()) : 1;
    return Math.max(0.02, (scroll.clientWidth - PAD * 2) / Math.max(total, 0.1));
  }

  function renderPanel(): void {
    if (!session) return;
    const { history, facts } = session;
    const edit = current();
    undoButton.disabled = !history.canUndo || !!session.draft;
    redoButton.disabled = !history.canRedo || !!session.draft;
    const pending = unexported();
    changesBadge.hidden = !pending;
    $("#edit-changes-text").textContent = `${history.changes} thay đổi chưa xuất`;
    changesBadge.title = `${history.changes} thay đổi chưa xuất · bản nháp được tự lưu`;
    exportButton.disabled = isUnchanged(edit, facts.duration);
    exportButton.title = exportButton.disabled ? "Chưa có thay đổi để xuất" : "Xuất bản đã chỉnh sửa thành file video mới";
    const segment = selectedSegment();
    if (!segment) return;
    const index = edit.segments.indexOf(segment);
    const start = outputStarts(edit)[index];
    const length = (segment.end - segment.start) / segment.speed;
    $("#edit-segment-title").textContent = `Đoạn ${index + 1} · ${formatClock(start)} – ${formatClock(start + length)}`;
    if (document.activeElement !== startInput) startInput.value = formatClock(segment.start, true);
    if (document.activeElement !== endInput) endInput.value = formatClock(segment.end, true);
    $("#edit-length").textContent = segment.speed === 1
      ? `Dài ${formatClock(segment.end - segment.start, true)} · ${edit.segments.length} đoạn`
      : `Dài ${formatClock(segment.end - segment.start, true)} · thành ${formatClock(length, true)} ở ${formatSpeed(segment.speed)}`;
    for (const button of speeds.querySelectorAll<HTMLButtonElement>("[data-speed]")) {
      button.setAttribute("aria-checked", String(Number(button.dataset.speed) === segment.speed));
    }
    if (document.activeElement !== customSpeed) customSpeed.value = String(segment.speed);
    if (document.activeElement !== volume) volume.value = String(Math.round(segment.volume * 100));
    $("#edit-volume-value").textContent = `${Math.round(segment.volume * 100)}%`;
    $("#edit-volume-note").hidden = segment.volume <= 1;
    $<HTMLButtonElement>("#edit-delete").disabled = edit.segments.length <= 1;
    $<HTMLButtonElement>("#tl-delete").disabled = edit.segments.length <= 1;
  }

  function renderTimeline(): void {
    if (!session) return;
    const edit = current();
    const total = outputDuration(edit);
    const fit = fitScale();
    pps = zoomScale(fit, Number(zoom.value) / 100);
    zoom.disabled = fit >= 20;
    const width = Math.max(scroll.clientWidth, Math.ceil(PAD * 2 + total * pps));
    content.style.width = `${width}px`;
    // Ruler.
    const tick = tickStep(pps);
    const ticks: HTMLElement[] = [];
    for (let time = 0; time <= total + 1e-6 && ticks.length < 2000; time += tick) {
      const mark = element("span", "tl-tick", formatClock(time, tick < 1));
      mark.style.left = `${PAD + time * pps}px`;
      ticks.push(mark);
    }
    ruler.replaceChildren(...ticks);
    // Segments.
    const starts = outputStarts(edit);
    const blocks = edit.segments.map((segment, index) => {
      const block = element("div", `tl-seg${segment.id === session!.selected ? " selected" : ""}`);
      block.dataset.id = segment.id;
      const left = PAD + starts[index] * pps;
      const length = (segment.end - segment.start) / segment.speed * pps;
      block.style.left = `${left}px`;
      block.style.width = `${Math.max(2, length)}px`;
      block.title = `Đoạn ${index + 1}: ${formatClock(segment.start, true)} – ${formatClock(segment.end, true)} trong video gốc`;
      const strip = element("span", "tl-thumbs");
      for (let x = 0; x < length && strip.childElementCount < 400; x += THUMB_WIDTH) {
        const source = segment.start + ((x + THUMB_WIDTH / 2) / pps) * segment.speed;
        const cell = Math.min(grid.times.length - 1, Math.floor(Math.min(source, segment.end) / grid.step));
        if (cell < 0) break;
        const thumb = element("span", "tl-thumb");
        thumb.dataset.cell = String(cell);
        thumb.style.left = `${x}px`;
        strip.append(thumb);
      }
      const label = element("span", "tl-label");
      if (length >= 34) label.append(element("span", "tl-name", `Đoạn ${index + 1}`));
      if (segment.speed !== 1 && length >= 24) label.append(element("span", "tl-badge", formatSpeed(segment.speed)));
      if (segment.volume !== 1 && length >= 60) label.append(element("span", "tl-badge", segment.volume === 0 ? "Tắt tiếng" : `${Math.round(segment.volume * 100)}%`));
      const startHandle = element("span", "tl-handle start");
      startHandle.dataset.edge = "start";
      startHandle.title = "Kéo để cắt đầu đoạn";
      const endHandle = element("span", "tl-handle end");
      endHandle.dataset.edge = "end";
      endHandle.title = "Kéo để cắt cuối đoạn";
      block.append(strip, label, startHandle, endHandle);
      return block;
    });
    track.replaceChildren(...blocks);
    renderThumbs();
    renderPlayhead();
    $("#tl-info").textContent = `${edit.segments.length} đoạn · ${formatClock(total)}`;
  }

  function render(): void {
    if (!session) return;
    renderPanel();
    renderTimeline();
    host.renderPlayer();
  }

  // ---------- Thumbnails ----------

  function renderThumbs(): void {
    if (!session) return;
    const low = scroll.scrollLeft - 300, high = scroll.scrollLeft + scroll.clientWidth + 300;
    for (const thumb of track.querySelectorAll<HTMLElement>(".tl-thumb")) {
      const cell = Number(thumb.dataset.cell);
      const url = thumbs.get(cell);
      if (url) {
        if (!thumb.style.backgroundImage) thumb.style.backgroundImage = `url("${url}")`;
        continue;
      }
      if (url === null || wanted.includes(cell)) continue;
      const block = thumb.closest<HTMLElement>(".tl-seg");
      const x = (block ? parseFloat(block.style.left) : 0) + parseFloat(thumb.style.left);
      if (x + THUMB_WIDTH >= low && x <= high) wanted.push(cell);
    }
    pumpThumbs();
  }

  function pumpThumbs(): void {
    while (session && thumbWorkers < 2 && wanted.length) {
      const cell = wanted.shift()!;
      if (thumbs.has(cell)) continue;
      thumbWorkers += 1;
      const path = session.outputPath;
      const position = clamp(grid.times[cell] / session.facts.duration, 0, 1);
      void invoke<ArrayBuffer>("video_frame", { sourcePath: path, position, maxWidth: THUMB_PX })
        .then((bytes) => {
          if (thumbsFor !== path) return;
          thumbs.set(cell, URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })));
        })
        .catch(() => { if (thumbsFor === path) thumbs.set(cell, null); })
        .finally(() => {
          thumbWorkers -= 1;
          if (session?.outputPath === path) {
            const url = thumbs.get(cell);
            if (url) for (const thumb of track.querySelectorAll<HTMLElement>(`.tl-thumb[data-cell="${cell}"]`)) thumb.style.backgroundImage = `url("${url}")`;
            pumpThumbs();
          }
        });
    }
  }

  function resetThumbs(path: string, duration: number): void {
    if (thumbsFor === path) return;
    for (const url of thumbs.values()) if (url) URL.revokeObjectURL(url);
    thumbs.clear();
    wanted.length = 0;
    thumbsFor = path;
    grid = thumbGrid(duration);
  }

  let scrollFrame = 0;
  scroll.addEventListener("scroll", () => {
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0;
      renderThumbs();
    });
  });
  new ResizeObserver(() => { if (session) renderTimeline(); }).observe(scroll);

  // ---------- Timeline input ----------

  function timeAt(clientX: number): number {
    const box = content.getBoundingClientRect();
    return (clientX - box.left - PAD) / pps;
  }

  type Drag = { kind: "seek" } | { kind: "trim"; id: string; edge: "start" | "end"; x: number; from: number; base: Edit; moved: boolean }
    | { kind: "select"; id: string; x: number };
  let drag: Drag | null = null;

  content.addEventListener("pointerdown", (event) => {
    if (!session || event.button !== 0 || session.draft) return;
    const target = event.target as HTMLElement;
    const handle = target.closest<HTMLElement>(".tl-handle");
    const block = target.closest<HTMLElement>(".tl-seg");
    event.preventDefault();
    content.setPointerCapture(event.pointerId);
    if (handle && block) {
      const segment = session.history.current.segments.find((candidate) => candidate.id === block.dataset.id);
      if (!segment) return;
      const edge = handle.dataset.edge === "start" ? "start" : "end";
      session.selected = segment.id;
      if (!video.paused) video.pause();
      drag = { kind: "trim", id: segment.id, edge, x: event.clientX, from: segment[edge], base: session.history.current, moved: false };
      render();
      return;
    }
    if (block) {
      drag = { kind: "select", id: block.dataset.id!, x: event.clientX };
      return;
    }
    drag = { kind: "seek" };
    seekOutput(timeAt(event.clientX));
  });

  content.addEventListener("pointermove", (event) => {
    if (!session || !drag) return;
    if (drag.kind === "seek") {
      seekOutput(timeAt(event.clientX));
    } else if (drag.kind === "select" && Math.abs(event.clientX - drag.x) > 4) {
      drag = { kind: "seek" };
      seekOutput(timeAt(event.clientX));
    } else if (drag.kind === "trim") {
      const trimming = drag;
      const segment = trimming.base.segments.find((candidate) => candidate.id === trimming.id)!;
      const next = trim(trimming.base, trimming.id, trimming.edge, trimming.from + ((event.clientX - trimming.x) / pps) * segment.speed);
      trimming.moved = true;
      session.draft = next;
      // Show the frame at the edge being moved.
      const index = next.segments.findIndex((candidate) => candidate.id === trimming.id);
      const moved = next.segments[index];
      const start = outputStarts(next)[index];
      seekOutput(trimming.edge === "start" ? start : start + (moved.end - moved.start) / moved.speed - 0.04);
      renderPanel();
      renderTimeline();
    }
  });

  function endDrag(): void {
    if (!session || !drag) return;
    const ended = drag;
    drag = null;
    if (ended.kind === "trim") {
      const next = session.draft;
      session.draft = null;
      if (next && ended.moved) commit(next, ended.id);
      else render();
    } else if (ended.kind === "select") {
      session.selected = ended.id;
      const block = track.querySelector<HTMLElement>(`.tl-seg[data-id="${ended.id}"]`);
      if (block) seekOutput(timeAt(ended.x));
      render();
      void save();
    }
  }
  content.addEventListener("pointerup", endDrag);
  content.addEventListener("pointercancel", endDrag);

  zoom.addEventListener("input", () => {
    if (!session) return;
    renderTimeline();
    scroll.scrollLeft = Math.max(0, PAD + session.outputTime * pps - scroll.clientWidth / 2);
    renderThumbs();
  });

  // ---------- Panel input ----------

  for (const speed of SPEED_PRESETS) {
    const button = element("button", "", formatSpeed(speed));
    button.type = "button";
    button.setAttribute("role", "radio");
    button.dataset.speed = String(speed);
    button.addEventListener("click", () => {
      const segment = selectedSegment();
      if (session && segment) commit(setSpeed(session.history.current, segment.id, speed));
    });
    speeds.append(button);
  }
  customSpeed.addEventListener("change", () => {
    const segment = selectedSegment();
    const value = Number(customSpeed.value.replace(",", "."));
    if (session && segment && Number.isFinite(value) && value > 0) commit(setSpeed(session.history.current, segment.id, value));
    else render();
  });
  volume.addEventListener("input", () => {
    const segment = selectedSegment();
    if (!session || !segment) return;
    session.draft = setVolume(session.history.current, segment.id, Number(volume.value) / 100);
    const playing = session.draft.segments.find((candidate) => candidate.id === segment.id)!;
    if (current().segments[session.index]?.id === segment.id) applySegment(playing);
    renderPanel();
  });
  volume.addEventListener("change", () => {
    if (session?.draft) commit(session.draft);
  });
  for (const [input, edge] of [[startInput, "start"], [endInput, "end"]] as const) {
    input.addEventListener("change", () => {
      const segment = selectedSegment();
      const value = parseClock(input.value);
      if (session && segment && value !== null) commit(trim(session.history.current, segment.id, edge, value));
      else render();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
      if (event.key === "Escape") {
        input.blur();
        render();
      }
    });
  }
  $("#edit-split").addEventListener("click", splitAtPlayhead);
  $("#tl-split").addEventListener("click", splitAtPlayhead);
  $("#edit-delete").addEventListener("click", deleteSelected);
  $("#tl-delete").addEventListener("click", deleteSelected);
  undoButton.addEventListener("click", undo);
  redoButton.addEventListener("click", redo);
  $("#edit-exit").addEventListener("click", () => void close(true));
  openButton.addEventListener("click", () => { if (listJob) void open(listJob); });
  exportButton.addEventListener("click", openDialog);

  document.addEventListener("keydown", (event) => {
    if (!session || screen.hidden || !dialog.hidden || document.querySelector(".confirm-backdrop, .review:not([hidden])")) return;
    const target = event.target;
    if (target instanceof Element && target.closest("input, textarea, select, [contenteditable='true']")) return;
    const key = event.key.toLowerCase();
    const ctrl = event.ctrlKey || event.metaKey;
    let handled = true;
    if (ctrl && !event.altKey && key === "z") {
      if (event.shiftKey) redo();
      else undo();
    } else if (ctrl && !event.altKey && key === "y") {
      redo();
    } else if (ctrl && !event.altKey && key === "b") {
      splitAtPlayhead();
    } else if (ctrl || event.altKey) {
      handled = false;
    } else if (key === " ") {
      togglePlay();
    } else if (key === "s") {
      splitAtPlayhead();
    } else if (key === "delete" || key === "backspace") {
      deleteSelected();
    } else if (key === "arrowleft" || key === "arrowright") {
      step((key === "arrowleft" ? -1 : 1) * (event.shiftKey ? 5 : 1));
    } else if (key === "escape") {
      void close(true);
    } else {
      handled = false;
    }
    if (handled) event.preventDefault();
  });

  // ---------- Entering and leaving ----------

  async function open(job: QueueJob): Promise<void> {
    if (session || opening || job.status.state !== "COMPLETED" || !job.outputPath) return;
    const outputPath = job.outputPath;
    opening = true;
    openButton.disabled = true;
    try {
      const [loaded, status] = await Promise.all([
        invoke<LoadResponse>("edit_project_load", { outputPath }),
        invoke<StatusResponse>("edit_status", { outputPath }).catch(() => null),
      ]);
      if (listJob?.id !== job.id) return;
      const project = parseProject(loaded.project, loaded.duration);
      const edit = project?.edit ?? createEdit(loaded.duration);
      const last = status?.exports.at(-1);
      // Exported in the background after the draft was saved: nothing is pending.
      const changes = last?.segments && sameSegments(edit.segments, last.segments) ? 0 : project?.changes ?? 0;
      const { project: _, ...facts } = loaded;
      if (status) listStatus = status;
      const start = video.readyState >= 1 ? sourceToOutput(edit, video.currentTime) ?? 0 : 0;
      session = {
        job, outputPath, facts, history: new EditHistory(edit, changes), draft: null,
        selected: project?.selected ?? outputToSource(edit, start)?.segment.id ?? edit.segments[0].id,
        outputTime: start, index: 0, exporting: null,
      };
      resetThumbs(outputPath, loaded.duration);
      zoom.value = "0";
      $("#edit-save-state").textContent = "";
      $("#edit-hint").textContent = "";
      screen.classList.add("editing");
      host.refresh();
      seekOutput(start);
      render();
      $<HTMLButtonElement>("#edit-exit").focus();
    } catch (error) {
      host.showError(`Không mở được chế độ chỉnh sửa: ${String(error).slice(0, 200)}`);
    } finally {
      opening = false;
      openButton.disabled = false;
    }
  }

  async function close(ask: boolean): Promise<boolean> {
    if (!session) return true;
    if (ask && unexported()) {
      const leave = await host.confirm({
        title: "Thoát chỉnh sửa?", message: "Các thay đổi vẫn được lưu nháp; bạn có thể xuất sau.", confirmLabel: "Thoát", cancelLabel: "Ở lại",
      });
      if (!leave) return false;
    }
    if (!session) return true;
    if (session.draft) session.draft = null;
    await flush();
    restorePlayer();
    session = null;
    drag = null;
    dialog.hidden = true;
    screen.classList.remove("editing");
    host.refresh();
    renderList();
    return true;
  }

  // ---------- Export status, Kết quả list ----------

  function schedulePoll(delay: number): void {
    window.clearTimeout(pollTimer);
    const token = ++pollToken;
    pollTimer = window.setTimeout(() => void poll(token), delay);
  }

  async function poll(token: number): Promise<void> {
    const job = listJob;
    if (!job?.outputPath || token !== pollToken) return;
    const previous = listStatus?.status?.state;
    let response: StatusResponse;
    try {
      response = await invoke<StatusResponse>("edit_status", { outputPath: job.outputPath });
    } catch {
      if (token === pollToken && previous === "RUNNING") schedulePoll(2000);
      return;
    }
    if (token !== pollToken || listJob?.id !== job.id) return;
    listStatus = response;
    const state = response.status?.state;
    if (previous === "RUNNING" && state === "COMPLETED" && session?.job.id === job.id && session.exporting) {
      session.history.markExported(session.exporting);
      session.exporting = null;
      renderPanel();
      void save();
    }
    if (dialogWaiting && state === "COMPLETED") dialogDone = response.status?.output_path ?? null;
    if (dialogWaiting && (state === "FAILED" || state === "CANCELLED")) {
      const error = $("#export-error");
      error.textContent = state === "CANCELLED" ? "Đã hủy xuất video." : response.status?.message ?? "Không xuất được video.";
      error.hidden = false;
    }
    if (state !== "RUNNING") dialogWaiting = false;
    renderList();
    renderDialog();
    if (state === "RUNNING") schedulePoll(700);
  }

  function renderList(): void {
    const section = $("#edit-exports");
    const job = listJob;
    const done = !!job && job.status.state === "COMPLETED" && !!job.outputPath;
    const exports = done ? listStatus?.exports ?? [] : [];
    const status = done ? listStatus?.status : null;
    const running = status?.state === "RUNNING";
    section.hidden = !done || (!exports.length && !running && status?.state !== "FAILED");
    const progress = $("#edit-export-progress");
    progress.hidden = !running;
    if (running) {
      $("#edit-export-progress-text").textContent = `Đang xuất bản sửa… ${Math.floor(status!.percent)}%`;
      $("#edit-export-progress-bar").style.width = `${status!.percent}%`;
    }
    const failed = $("#edit-export-failed");
    failed.hidden = status?.state !== "FAILED";
    failed.textContent = status?.state === "FAILED" ? status.message : "";
    const list = $("#edit-export-list");
    list.replaceChildren(...[...exports].reverse().map((entry) => {
      const item = element("li", "edit-export-item");
      const text = element("span", "edit-export-text");
      const when = new Date(entry.created * 1000);
      const two = (value: number): string => String(value).padStart(2, "0");
      const stamp = Number.isFinite(when.getTime())
        ? `${two(when.getDate())}/${two(when.getMonth() + 1)} ${two(when.getHours())}:${two(when.getMinutes())}` : "";
      text.append(element("strong", "", fileName(entry.path)), element("small", "", [formatClock(entry.duration), stamp].filter(Boolean).join(" · ")));
      text.title = entry.path;
      const view = element("button", "btn", "Xem");
      view.type = "button";
      view.addEventListener("click", () => { if (job) host.playExport(job.id, entry.path); });
      const reveal = element("button", "btn", "Mở thư mục");
      reveal.type = "button";
      reveal.addEventListener("click", () => {
        invoke("reveal_download", { path: entry.path }).catch((error) => host.showError(`Không mở được thư mục: ${String(error).slice(0, 160)}`));
      });
      const actions = element("span", "edit-export-actions");
      actions.append(view, reveal);
      item.append(text, actions);
      return item;
    }));
  }

  function select(job: QueueJob | null): void {
    if (session && job?.id !== session.job.id) void close(false);
    const done = !!job && job.status.state === "COMPLETED" && !!job.outputPath;
    openButton.hidden = !done;
    const same = listJob?.id === job?.id && listJob?.outputPath === job?.outputPath && listJob?.status.state === job?.status.state;
    listJob = job;
    if (same) return;
    listStatus = null;
    pollToken += 1;
    window.clearTimeout(pollTimer);
    renderList();
    if (done) schedulePoll(0);
  }

  async function cancelExport(): Promise<void> {
    const job = listJob;
    if (!job?.outputPath) return;
    try {
      await invoke("edit_cancel", { outputPath: job.outputPath });
    } catch (error) {
      host.showError(`Không hủy được: ${String(error).slice(0, 160)}`);
    }
    schedulePoll(300);
  }
  $("#edit-export-cancel").addEventListener("click", () => void cancelExport());

  // ---------- "Xuất video" dialog ----------

  function openDialog(): void {
    if (!session || exportButton.disabled) return;
    if (!video.paused) video.pause();
    const running = listStatus?.status?.state === "RUNNING";
    if (!running) {
      nameInput.value = defaultExportName(session.job.displayName, (listStatus?.exports ?? []).map((entry) => entry.path));
      folder = folderOf(session.outputPath);
      quality = "source";
      dialogDone = null;
      $("#export-error").hidden = true;
    }
    dialog.hidden = false;
    renderDialog();
    (running ? $<HTMLButtonElement>("#export-later") : nameInput).focus();
    if (!running) nameInput.select();
  }

  function closeDialog(): void {
    dialog.hidden = true;
    if (session) exportButton.focus();
  }

  function renderDialog(): void {
    if (dialog.hidden || !session) return;
    const status = listStatus?.status;
    const running = status?.state === "RUNNING";
    const done = !running && dialogDone !== null;
    const { facts } = session;
    const edit = current();
    const seconds = outputDuration(edit);
    $("#export-form").toggleAttribute("disabled", running || done);
    $("#export-folder").textContent = folder;
    $("#export-folder").title = folder;
    for (const button of qualityButtons) button.setAttribute("aria-checked", String(button.dataset.quality === quality));
    const size = exportSize(facts.width, facts.height, quality);
    const upscale = size.height * size.width > facts.width * facts.height;
    $("#export-quality-hint").textContent = facts.width
      ? `${size.width}×${size.height}${quality === "source" ? " · giữ nguyên khung hình" : upscale ? " · phóng lên, file to hơn" : " · nhẹ, đăng nhanh"}`
      : "";
    const bytes = estimateExportBytes(facts.size_bytes, facts.duration, seconds, facts.width, facts.height, quality);
    $("#export-info").textContent = [`Dài ${formatClock(seconds)}`, megabytes(bytes)].filter(Boolean).join(" · ");
    const progress = $("#export-progress");
    progress.hidden = !running && !done;
    if (running) {
      $("#export-progress-text").textContent = status!.message || "Đang xuất video…";
      $("#export-progress-pct").textContent = `${Math.floor(status!.percent)}%`;
      $("#export-progress-bar").style.width = `${status!.percent}%`;
    } else if (done) {
      $("#export-progress-text").textContent = `Đã xuất xong: ${fileName(dialogDone!)}`;
      $("#export-progress-pct").textContent = "100%";
      $("#export-progress-bar").style.width = "100%";
    }
    const start = $<HTMLButtonElement>("#export-start");
    const later = $<HTMLButtonElement>("#export-later");
    const cancel = $<HTMLButtonElement>("#export-cancel");
    const reveal = $<HTMLButtonElement>("#export-reveal");
    start.hidden = running || done;
    cancel.hidden = !running;
    reveal.hidden = !done;
    later.textContent = running ? "Chạy nền" : done ? "Đóng" : "Để sau";
    later.title = running ? "Đóng hộp thoại; video vẫn tiếp tục xuất, tiến độ hiện ở thẻ Kết quả" : "";
    start.disabled = !nameInput.value.trim() || !folder;
  }

  async function startExport(): Promise<void> {
    if (!session) return;
    const edit = current();
    const outputPath = session.outputPath;
    const cues = remapCues(session.facts.cues, edit).filter((cue) => cue.end_ms > cue.start_ms);
    const start = $<HTMLButtonElement>("#export-start");
    const error = $("#export-error");
    start.disabled = true;
    error.hidden = true;
    try {
      await flush();
      await invoke("edit_export", { request: {
        outputPath, quality, folder, fileName: nameInput.value.trim(), cues,
        segments: edit.segments.map(({ start: from, end, speed, volume: level }) => ({ start: from, end, speed, volume: level })),
      } });
      if (session?.outputPath !== outputPath) return;
      session.exporting = edit;
      dialogWaiting = true;
      dialogDone = null;
      listStatus = { status: { state: "RUNNING", percent: 0, message: "Đang bắt đầu xuất video…", output_path: null }, exports: listStatus?.exports ?? [] };
      renderDialog();
      renderList();
      schedulePoll(500);
    } catch (failure) {
      error.textContent = String(failure).slice(0, 300);
      error.hidden = false;
      start.disabled = false;
    }
  }

  for (const button of qualityButtons) {
    button.addEventListener("click", () => {
      quality = button.dataset.quality as Quality;
      renderDialog();
    });
  }
  nameInput.addEventListener("input", renderDialog);
  nameInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (!$<HTMLButtonElement>("#export-start").disabled) void startExport();
    }
  });
  $("#export-change").addEventListener("click", async () => {
    const picked = await invoke<string | null>("pick_folder").catch(() => null);
    if (picked) folder = picked;
    renderDialog();
  });
  $("#export-start").addEventListener("click", () => void startExport());
  $("#export-later").addEventListener("click", closeDialog);
  $("#export-close").addEventListener("click", closeDialog);
  $("#export-cancel").addEventListener("click", () => void cancelExport());
  $("#export-reveal").addEventListener("click", () => {
    if (dialogDone) invoke("reveal_download", { path: dialogDone }).catch((error) => host.showError(`Không mở được thư mục: ${String(error).slice(0, 160)}`));
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      closeDialog();
    }
  });
  dialog.addEventListener("mousedown", (event) => {
    if (event.target === dialog) closeDialog();
  });

  return {
    get jobId() { return session?.job.id ?? null; },
    select,
    timeLabel: () => session ? `${formatClock(session.outputTime)} / ${formatClock(outputDuration(current()))}` : null,
    togglePlay,
    flush,
  };
}

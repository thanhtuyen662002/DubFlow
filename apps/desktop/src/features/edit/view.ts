/**
 * Edit mode on the queue screen (Issues #408, #410): the Cắt ghép and Chèn
 * panels, the timeline (segments, joints with transitions, the overlay row,
 * the sound row of Âm thanh, #420, the text row of Chữ, #422), the live
 * preview through the queue player plus one <video> per other media and per
 * overlay, autosave of the draft, the "Xuất video" dialog and the "Bản đã
 * sửa" list in Kết quả. Issue #426 opens Bán hàng videos (with the Mẫu
 * mode first) and compilations too; Phụ đề and Che stay with dubbing jobs.
 */
import type { QueueJob } from "../queue/model.ts";
import type { ConfirmOptions } from "../../shell/confirm.ts";
import type { AiSession } from "../ai/provider.ts";
import { isAudioPath, type SfxLibrary } from "../sfx/library.ts";
import {
  addAudio, addedId, addOverlay, addText, append, createEdit, defaultExportName, EditHistory, estimateExportBytes, exportPlan, exportSize,
  formatClock, formatSpeed, insertAt, isUnchanged, MAIN, mainLevel, MAX_MEDIA, MAX_OVERLAYS, MAX_SEGMENTS, matchesExport, MIN_SEGMENT,
  MIN_TRANSITION, mediaOf, moveOverlay, outputDuration, outputStarts, outputToSource, overlayHeight, overlayLength,
  overlayPreset, overlayPresetOf, parseClock, parseProject, RECENT_LIMIT, remapCues, remove, removeOverlay, resizeOverlay,
  samePath, serializeProject, setOverlayTime, setOverlayVolume, setSpeed, setTransition, setVolume, sourceToOutput, openingTime, resolveTarget, modeOffered,
  SPEED_PRESETS, split, thumbGrid, tickStep, transitionAt, transitionRoom, trim, zoomScale,
  addMarker, copySegment, duplicateSegment, keepRange, moveSegment, nearbyPoint, pasteSegment, removeMarker, removeRange, segmentLength,
  setMarker, snapPoints, snapTime, MAX_MARKERS, type SegmentClip, lookOf, playsForward, sourceAt,
  isStillPath, overlayLanes, moveOverlayLayer, overlaps, overlayStyle, setOverlayStyle, rangeEdit, overlayAt, placementOf, setOverlayFades, setOverlayMotion,
  type OverlayKey,
  type Cue, type Edit, type Media, type MediaInfo, type Overlay, type OverlayPreset, type Quality, type Segment, type TransitionType,
} from "./model.ts";
import { mountSubtitles } from "./subtitles_view.ts";
import { burnRequest, exportLines, plainSubtitles, serializePlain, type PlainSubtitles } from "./plain_subs.ts";
import { STYLE_STORAGE_KEY, storedStyle } from "./subtitles.ts";
import { COVER_STORAGE_KEY, plainCover } from "./cover.ts";
import { mountCover } from "./cover_view.ts";
import { mountDocuCover } from "./docu_cover_view.ts";
import { mountDocuLayers } from "./docu_layers_view.ts";
import type { Layers } from "./docu_layers.ts";
import type { AudioFacts } from "../sfx/library.ts";
import { mountAudio } from "./audio_view.ts";
import { mountText } from "./text_view.ts";
import { mountTemplate } from "./template_view.ts";
import { mountLook } from "./look_view.ts";
import { lookBadges } from "./look.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Facts = { duration: number; width: number; height: number; fps?: number; size_bytes: number; has_audio: boolean; cues: Cue[] };
type LoadResponse = Facts & { project: unknown; kind?: string; reviewable?: boolean };
type ExportEntry = {
  path: string; srt_path?: string | null; created: number; duration: number; quality?: string;
  media?: unknown; segments?: unknown; transitions?: unknown; overlays?: unknown;
};
type ExportState = "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
type ExportStatus = { state: ExportState; percent: number; message: string; output_path: string | null };
type StatusResponse = { status: ExportStatus | null; exports: ExportEntry[] };
type MediaResponse = { path: string; title: string; duration: number; width: number; height: number; fps: number; has_audio: boolean };

/** A finished queue video the Chèn panel offers. */
export type LibraryJob = { id: string; title: string; outputPath: string; kind: EditTarget["kind"]; meta?: string };

/**
 * A video the editor opens: a queue job's `final_vi.mp4`, a Bán hàng video,
 * a compilation (Issue #426), a documentary or any video file (Issue #497).
 */
export type EditTarget = {
  /** The queue job's id, else the video's path. */
  id: string;
  /** "file": any other video on the computer (Issue #497). */
  kind: "job" | "product" | "compilation" | "docu" | "file";
  outputPath: string;
  displayName: string;
  /** The queue job (kind "job"): Phụ đề and Che re-run it. */
  job?: QueueJob;
  /** Fills a first draft when the video has no saved one (a documentary's callouts, ADR-0049). */
  seed?: (edit: Edit) => Edit;
};

export function jobTarget(job: QueueJob): EditTarget {
  return { id: job.id, kind: "job", outputPath: job.outputPath ?? "", displayName: job.displayName, job };
}

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
  /** A URL the player can stream a local video from, or null when it cannot. */
  mediaUrl: (path: string) => Promise<string | null>;
  /** Finished videos in the queue, for "Từ DubFlow". */
  library: () => LibraryJob[];
  /** The full translation review (speakers, lines to look at), over the editor. */
  openReview: () => void;
  /** Phụ đề "Áp dụng": queue the new version prepared in `outputDir` and start it; its job id. */
  revise: (job: QueueJob, outputDir: string) => Promise<string | null>;
  /** Âm thanh: the sound-effect pack and the user's sound folder. */
  sfx: SfxLibrary;
  /** The AI account "Gợi ý AI" uses (Cài đặt AI). */
  ai: AiSession;
  openAiSettings: () => void;
  /** Mẫu: render on the CPU only (Cài đặt). */
  cpuOnly: () => boolean;
  /** Edit mode ended for `target` (to go back to the page it came from). */
  left: (target: EditTarget) => void;
};

export type Editor = {
  /** The job whose edit is open, if any. */
  readonly jobId: string | null;
  /** The video whose edit is open, if any. */
  readonly target: EditTarget | null;
  /** Open a Bán hàng video or a compilation (leaving any open edit first); false when it did not open. */
  openTarget(target: EditTarget): Promise<boolean>;
  /** Leave edit mode, asking first when `ask` and something is not saved; false when the user stays. */
  close(ask: boolean): Promise<boolean>;
  /** The selected job, on every render: the Kết quả list follows it and edit mode closes when it changes. */
  select(job: QueueJob | null): void;
  /** Output time / output length while editing, else null. */
  timeLabel(): string | null;
  /** Whether the edited output is playing; null outside edit mode. */
  isPlaying(): boolean | null;
  togglePlay(): void;
  /** A click on the picture: drops the selected overlay or text, else plays or pauses. */
  clickPicture(): void;
  /** Write a pending autosave now. */
  flush(): Promise<void>;
  /** Switch the open edit to a tool tab; false when this video does not have it. */
  show(mode: "subs" | "more"): boolean;
  /** Files dropped on the window while editing, at a point in client pixels (null when unknown). */
  drop(paths: string[], point: { x: number; y: number } | null): void;
};

type Session = {
  target: EditTarget;
  /** Phụ đề and Che: a dubbing job with its translation. */
  reviewable: boolean;
  outputPath: string;
  facts: Facts;
  history: EditHistory;
  /** An uncommitted change while dragging a handle or a slider. */
  draft: Edit | null;
  selected: string | null;
  /** The selected overlay; its controls replace the segment's. */
  overlay: string | null;
  outputTime: number;
  /** Segment being played. */
  index: number;
  playing: boolean;
  /** The edit last sent to "Xuất video", marked exported when it finishes. */
  exporting: Edit | null;
  /** Files picked from the computer, kept with the draft. */
  recent: Media[];
  /** Che drawn here and applied at export (Issue #510): every video that is not a re-runnable dubbing job. */
  plainCover: boolean;
  /** Phụ đề kept in the draft (Issue #523), for the same videos; `touched` once changed or saved before. */
  plainSubs: PlainSubtitles | null;
  subsTouched: boolean;
  /** Điểm vào / điểm ra (I, O) in output seconds. */
  markIn: number | null;
  markOut: number | null;
  /** J/K/L: the playback rate on top of each segment's speed. */
  shuttle: number;
};

type Mode = "template" | "cut" | "insert" | "subs" | "cover" | "audio" | "text" | "more";
type Where = "playhead" | "end" | "overlay";
type LibraryItem = { key: string; path: string; title: string; meta: string; kind: "job" | "file"; duration: number | null };
type Box = { element: HTMLDivElement; video: HTMLVideoElement; image: HTMLImageElement; path: string };

const PAD = 12;
/** Height of one overlay row on the timeline (overlapping overlays take more rows). */
const OV_ROW = 28;
const TIMELINE_PX = 170;
/** The timeline below a 600 px window (styles.css). */
const SHORT_TIMELINE_PX = 140;
/** A timeline drop this close to a segment edge goes on the edge, not a sliver away. */
const DROP_SNAP_PX = 12;
/** Moving edges and the playhead snap to points this close. */
const SNAP_PX = 8;
/** A segment drag starts after this many pixels (less is a click). */
const DRAG_PX = 5;
/** Waveform strip height in a segment block. */
const WAVE_PX = 18;
const THUMB_WIDTH = 76;
const THUMB_PX = 192;
const POSTER_PX = 160;
const LIBRARY_LIMIT = 40;
/** Other-media players kept loaded besides the current and the next one. */
const ALT_KEEP = 3;
const WHERE_LABEL: Record<Where, { button: string; action: string; note: string }> = {
  playhead: { button: "Chèn", action: "Chèn tại đầu phát", note: "Tách đoạn tại đầu phát; các đoạn sau và phụ đề tự dồn ra sau." },
  end: { button: "Thêm", action: "Thêm vào cuối", note: "Thêm sau đoạn cuối cùng của video." },
  overlay: { button: "Chồng", action: "Chồng lên video", note: "Hiện thành khung nhỏ trên video từ đầu phát; chỉnh vị trí, cỡ và âm lượng sau khi thêm." },
};
/** Chuẩn hóa độ to on the export dialog, remembered (Issue #521). */
const LOUDNESS_KEY = "dubflow.edit.loudness";

const TRANSITION_NAMES: Record<TransitionType, string> = {
  fade: "Mờ qua đen", dissolve: "Hòa trộn", fadewhite: "Lóe trắng", wipeleft: "Quét sang trái", wiperight: "Quét sang phải",
  slideleft: "Trượt sang trái", slideright: "Trượt sang phải", zoomin: "Phóng to", circleopen: "Mở tròn",
};

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

function stem(path: string): string {
  return fileName(path).replace(/\.[^.]*$/, "");
}

function folderOf(path: string): string {
  return path.replace(/[\\/][^\\/]*$/, "");
}

function megabytes(bytes: number): string {
  if (bytes <= 0) return "";
  return bytes >= 1_000_000_000 ? `khoảng ${(bytes / 1_000_000_000).toFixed(1).replace(".", ",")} GB` : `khoảng ${Math.max(1, Math.round(bytes / 1_000_000))} MB`;
}

/** "0,5 giây" */
function seconds(value: number): string {
  return `${String(Math.round(value * 10) / 10).replace(".", ",")} giây`;
}

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

/** "Sao chép" keeps a segment across videos while the app runs. */
let clipboard: SegmentClip | null = null;

export function mountEditor(invoke: Invoke, host: EditorHost): Editor {
  const { video } = host;
  const screen = $("#queue-content");
  const frame = video.parentElement as HTMLElement;
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
  const overlayTrack = $("#tl-overlays");
  const audioTrack = $("#tl-audio");
  const textTrack = $("#tl-text");
  const subsTrack = $("#tl-subs");
  const timeline = $("#edit-timeline");
  const playhead = $("#tl-playhead");
  const zoom = $<HTMLInputElement>("#tl-zoom");
  const dialog = $("#export-dialog");
  const nameInput = $<HTMLInputElement>("#export-name");
  const qualityButtons = [...document.querySelectorAll<HTMLButtonElement>("#export-quality [data-quality]")];
  const modeTabs = [...document.querySelectorAll<HTMLButtonElement>("#edit-modes [data-mode]")];
  const whereButtons = [...document.querySelectorAll<HTMLButtonElement>("#insert-where [data-where]")];
  const sourceTabs = [...document.querySelectorAll<HTMLButtonElement>("#insert-tabs [data-source]")];
  const presetButtons = [...document.querySelectorAll<HTMLButtonElement>("#ov-presets [data-preset]")];
  const ovSize = $<HTMLInputElement>("#ov-size");
  const ovVolume = $<HTMLInputElement>("#ov-volume");
  const ovAt = $<HTMLInputElement>("#ov-at");
  const ovStart = $<HTMLInputElement>("#ov-start");
  const ovEnd = $<HTMLInputElement>("#ov-end");
  const ovOpacity = $<HTMLInputElement>("#ov-opacity");
  const ovRadius = $<HTMLInputElement>("#ov-radius");
  const shapeButtons = [...document.querySelectorAll<HTMLButtonElement>("#ov-shape [data-shape]")];
  const ovBorder = $<HTMLInputElement>("#ov-border");
  const ovBorderColor = $<HTMLInputElement>("#ov-border-color");
  const ovMotion = $<HTMLInputElement>("#ov-motion");
  const ovFadeIn = $<HTMLInputElement>("#ov-fade-in");
  const ovFadeOut = $<HTMLInputElement>("#ov-fade-out");
  const keyButtons = [...document.querySelectorAll<HTMLButtonElement>("#ov-keys [data-key]")];
  const ghost = $<HTMLCanvasElement>("#edit-ghost");
  const fadeLayer = $("#edit-fade");
  const overlayLayer = $("#edit-overlays");
  const pop = $("#tl-transition");
  const popTypes = [...document.querySelectorAll<HTMLButtonElement>("#tl-pop-types [data-type]")];
  const popDuration = $<HTMLInputElement>("#tl-pop-duration");
  const rangeLayer = $("#tl-range");
  const markerLayer = $("#tl-markers");
  const snapLine = $("#tl-snapline");
  const dropLine = $("#tl-drop");
  const menu = $("#tl-menu");
  const snapButton = $<HTMLButtonElement>("#tl-snap");
  const markerList = $("#markers-list");
  /** Hút mép, remembered across videos. */
  let snapping = (() => { try { return localStorage.getItem("dubflow.edit.snap") !== "0"; } catch { return true; } })();
  // Timeline waveforms: one level byte per 1/50 s of each media's sound, fetched once.
  const peaks = new Map<string, Uint8Array | null>();
  const peaksLoading = new Set<string>();

  let session: Session | null = null;
  let saveTimer = 0;
  let saving: Promise<void> = Promise.resolve();
  let loop = 0;
  let pps = 1;
  let opening = false;
  let openTicket = 0;
  let mode: Mode = "cut";
  let where: Where = "playhead";
  let source: "dubflow" | "computer" = "dubflow";
  // Timeline thumbnails: one frame per grid cell of each media, fetched lazily ("<path>\n<cell>").
  const grids = new Map<string, { step: number; times: number[]; duration: number }>();
  const thumbs = new Map<string, string | null>();
  const wanted: string[] = [];
  let thumbWorkers = 0;
  let thumbEpoch = 0;
  // Chèn sources.
  const infoCache = new Map<string, Promise<MediaInfo | null>>();
  const posters = new Map<string, string | null>();
  const posterQueue: string[] = [];
  let posterWorkers = 0;
  let library: LibraryItem[] = [];
  let libraryToken = 0;
  // Playback across media: the queue player for "main", one <video> per other media, one per overlay.
  const alts = new Map<string, HTMLVideoElement>();
  const boxes = new Map<string, Box>();
  let active: HTMLVideoElement = video;
  /** Reverse and held-frame segments play by the clock, not by the player: when and where it started. */
  let clock: { at: number; output: number } | null = null;
  /** Joint whose outgoing frame the ghost holds (dissolve preview), or -1. */
  let ghostJoint = -1;
  /** The transition popover's joint (id of the segment before it). */
  let popFor: string | null = null;
  // Kết quả list and export progress for the selected job.
  let listJob: QueueJob | null = null;
  let listStatus: StatusResponse | null = null;
  let pollTimer = 0;
  let pollToken = 0;
  // Export dialog.
  let folder = "";
  let quality: Quality = "source";
  /** Tùy chọn khác of the export dialog (Issue #526); kept while the app runs. */
  const options = { aspect: "", fill: "blur", fps: "", rate: "", range: "all" as "all" | "marked", audioOnly: false };
  /** Which placement of the selected overlay drag, size and presets change (Chuyển động). */
  let overlayKey: OverlayKey = "start";
  let overlayKeyFor: string | null = null;
  /** The key being edited for this overlay: the end only while it moves. */
  const keyOf = (overlay: Overlay): OverlayKey => overlayKeyFor === overlay.id && overlay.to ? overlayKey : "start";
  /** The dialog started an export and waits for it. */
  let dialogWaiting = false;
  let dialogDone: string | null = null;

  const current = (): Edit => session!.draft ?? session!.history.current;
  const hasSource = (): boolean => !!video.getAttribute("src") && video.readyState >= 1;
  const unexported = (): boolean => !!session && session.history.changes > 0 && !isUnchanged(session.history.current, session.facts.duration);
  const mediaPath = (media: Media): string => media.id === MAIN ? session?.outputPath ?? media.path : media.path;

  // ---------- Players: one per media ----------

  function altFor(media: Media): HTMLVideoElement {
    const key = media.path.toLocaleLowerCase("en");
    let player = alts.get(key);
    if (!player) {
      const created = element("video", "edit-alt");
      created.preload = "auto";
      created.playsInline = true;
      created.addEventListener("click", () => { clickPicture(); });
      created.addEventListener("loadedmetadata", () => { if (session && active === created) seekOutput(session.outputTime, true); });
      created.addEventListener("pause", () => externalPause(created));
      ghost.before(created);
      alts.set(key, created);
      void host.mediaUrl(media.path).then((url) => { if (url && alts.get(key) === created) created.src = url; }).catch(() => undefined);
      player = created;
    }
    return player;
  }

  function playerFor(mediaId: string): HTMLVideoElement {
    if (mediaId === MAIN || !session) return video;
    const media = mediaOf(current(), mediaId);
    return media ? altFor(media) : video;
  }

  function release(player: HTMLVideoElement): void {
    player.pause();
    player.removeAttribute("src");
    player.load();
    player.remove();
  }

  /** Unload other-media players that are not current, next, or among the last few. */
  function trimAlts(keep: Set<HTMLVideoElement>): void {
    for (const [key, player] of alts) {
      if (alts.size <= ALT_KEEP) break;
      if (keep.has(player)) continue;
      release(player);
      alts.delete(key);
    }
  }

  function activate(player: HTMLVideoElement): void {
    if (active === player) return;
    const previous = active;
    active = player;
    if (!previous.paused) previous.pause();
    lookPanel.clear(previous);
    for (const alt of alts.values()) alt.classList.toggle("active", alt === player);
  }

  function applySegment(segment: Segment, player: HTMLVideoElement): void {
    player.playbackRate = clamp(segment.speed * (session?.shuttle ?? 1), 0.0625, 16);
    applyLevel(segment, player);
  }

  /** The segment's volume times "Âm thanh gốc", lowered under ducking sounds. */
  function applyLevel(segment: Segment, player: HTMLVideoElement): void {
    const level = segment.volume * (session ? mainLevel(current(), session.outputTime) : 1);
    // Above 100% is heard only after export.
    player.volume = Math.min(1, level);
    player.muted = level === 0;
    // Phụ đề previews the muted source with the dubbed mix beside it.
    if (player === video && subs.active) subs.volume(level);
  }

  /** Cue the next segment's player when it is another file, so the switch is quick. */
  function prepareNext(edit: Edit, index: number): void {
    const next = edit.segments[index + 1];
    const keep = new Set<HTMLVideoElement>([active]);
    if (next && next.mediaId !== edit.segments[index].mediaId) {
      const player = playerFor(next.mediaId);
      keep.add(player);
      if (player !== active && player.readyState >= 1 && Math.abs(player.currentTime - next.start) > 0.05) player.currentTime = next.start;
    }
    trimAlts(keep);
  }

  // ---------- Playback: the players play the edited output ----------

  function seekOutput(time: number, keepGhost = false): void {
    if (!session) return;
    const edit = current();
    session.outputTime = clamp(time, 0, outputDuration(edit));
    if (!keepGhost) ghostJoint = -1;
    const point = outputToSource(edit, session.outputTime);
    if (point) {
      session.index = point.index;
      const player = playerFor(point.segment.mediaId);
      activate(player);
      applySegment(point.segment, player);
      if (player.readyState >= 1 && Math.abs(player.currentTime - point.sourceTime) > 0.001) player.currentTime = point.sourceTime;
      clock = null;
      if (!playsForward(point.segment)) {
        // Reverse and held frames are drawn by seeking; the clock moves the playhead (tick).
        if (!player.paused) player.pause();
      } else if (session.playing && player.paused && player.readyState >= 1) void player.play().catch(() => undefined);
      prepareNext(edit, point.index);
    }
    renderEffects();
    renderPlayhead();
    host.renderPlayer();
  }

  function captureGhost(player: HTMLVideoElement, joint: number): void {
    try {
      if (!player.videoWidth) return;
      ghost.width = player.videoWidth;
      ghost.height = player.videoHeight;
      ghost.getContext("2d")?.drawImage(player, 0, 0);
      ghostJoint = joint;
    } catch {
      ghostJoint = -1;
    }
  }

  function tick(): void {
    loop = 0;
    if (!session || !session.playing) return;
    const edit = current();
    const starts = outputStarts(edit);
    const index = Math.min(session.index, edit.segments.length - 1);
    const segment = edit.segments[index];
    const player = active;
    if (!playsForward(segment)) {
      const now = performance.now();
      if (!clock) clock = { at: now, output: session.outputTime };
      const end = index + 1 < edit.segments.length ? starts[index + 1] : outputDuration(edit);
      const time = clock.output + ((now - clock.at) / 1000) * session.shuttle;
      if (time >= end - 0.001) {
        clock = null;
        if (index + 1 < edit.segments.length) seekOutput(starts[index + 1], true);
        else {
          session.outputTime = outputDuration(edit);
          pausePlayback();
        }
      } else {
        session.outputTime = time;
        const target = sourceAt(segment, time - starts[index]);
        if (player.readyState >= 1 && !player.seeking && Math.abs(player.currentTime - target) > 0.03) player.currentTime = target;
        renderEffects();
      }
    } else if (player.readyState >= 2) {
      const now = player.currentTime;
      // A dissolve hands over before the segment's end.
      const until = index + 1 < edit.segments.length ? segment.start + (starts[index + 1] - starts[index]) * segment.speed : segment.end;
      if (now < segment.start - 0.3 || now > segment.end + 0.3) {
        // The player moved on its own (or the edit changed): back to the output position.
        seekOutput(session.outputTime, true);
      } else if (now >= until - 0.02 || player.ended) {
        if (index + 1 < edit.segments.length) {
          const next = edit.segments[index + 1];
          const joint = transitionAt(edit, index);
          if (joint && overlaps(joint.type)) captureGhost(player, index);
          const nextPlayer = playerFor(next.mediaId);
          if (nextPlayer === player && Math.abs(next.start - now) <= 0.05) {
            // A plain split continues without a seek.
            session.index = index + 1;
            session.outputTime = starts[index + 1];
            applySegment(next, player);
            renderEffects();
          } else {
            seekOutput(starts[index + 1], true);
          }
        } else {
          session.outputTime = outputDuration(edit);
          pausePlayback();
        }
      } else {
        session.outputTime = starts[index] + Math.max(0, now - segment.start) / segment.speed;
        renderEffects();
      }
    }
    renderPlayhead(true);
    host.renderPlayer();
    if (session.playing) loop = requestAnimationFrame(tick);
  }

  function startLoop(): void {
    if (!loop) loop = requestAnimationFrame(tick);
  }

  function pausePlayback(): void {
    if (!session) return;
    session.playing = false;
    session.shuttle = 1;
    active.playbackRate = session.index < current().segments.length ? current().segments[session.index].speed : 1;
    active.pause();
    for (const box of boxes.values()) box.video.pause();
    audio.pause();
    renderEffects();
    host.renderPlayer();
  }

  /** Paused from outside (a media key, the end of a file) while it is the playing one. */
  function externalPause(player: HTMLVideoElement): void {
    if (session?.playing && player === active && !player.ended && !player.seeking) {
      const edit = current();
      if (session.outputTime < outputDuration(edit) - 0.1) pausePlayback();
    }
  }

  video.addEventListener("play", () => {
    if (session && !session.playing && active === video) {
      session.playing = true;
      startLoop();
      host.renderPlayer();
    }
  });
  video.addEventListener("pause", () => externalPause(video));
  video.addEventListener("loadedmetadata", () => {
    if (session && active === video) seekOutput(session.outputTime, true);
  });

  function clickPicture(): void {
    if (!session) return;
    if (session.overlay) {
      selectOverlay(null);
      return;
    }
    if (mode === "text" && textMode.selected) {
      textMode.select(null);
      render();
      return;
    }
    togglePlay();
  }

  function togglePlay(): void {
    if (!session || !hasSource()) return;
    if (session.playing) {
      pausePlayback();
      return;
    }
    if (session.outputTime >= outputDuration(current()) - 0.05) session.outputTime = 0;
    session.playing = true;
    seekOutput(session.outputTime);
    const playing = current().segments[session.index];
    if (active.readyState >= 1 && (!playing || playsForward(playing))) void active.play().catch(() => undefined);
    startLoop();
    host.renderPlayer();
  }

  function restorePlayer(): void {
    if (loop) cancelAnimationFrame(loop);
    loop = 0;
    activate(video);
    for (const player of alts.values()) release(player);
    alts.clear();
    for (const box of boxes.values()) release(box.video);
    boxes.clear();
    overlayLayer.replaceChildren();
    fadeLayer.style.opacity = "0";
    ghost.style.opacity = "0";
    ghostJoint = -1;
    video.playbackRate = 1;
    video.volume = 1;
    video.muted = false;
    lookPanel.clear(video);
    const tint = document.querySelector<HTMLElement>("#edit-tint");
    if (tint) tint.style.opacity = "0";
  }

  // ---------- Transitions and overlays on the player ----------

  /** The video picture inside the player frame (object-fit: contain). */
  function pictureRect(): { left: number; top: number; width: number; height: number } {
    const width = frame.clientWidth, height = frame.clientHeight;
    const sourceWidth = session?.facts.width || video.videoWidth || 16, sourceHeight = session?.facts.height || video.videoHeight || 9;
    const scale = Math.min(width / sourceWidth, height / sourceHeight);
    const shownWidth = sourceWidth * scale, shownHeight = sourceHeight * scale;
    return { left: (width - shownWidth) / 2, top: (height - shownHeight) / 2, width: shownWidth, height: shownHeight };
  }

  function renderEffects(): void {
    if (!session) return;
    const edit = current();
    const time = session.outputTime;
    const starts = outputStarts(edit);
    let black = 0, white = false;
    let handover: { type: TransitionType; progress: number } | null = null;
    for (let index = 0; index + 1 < edit.segments.length; index++) {
      const transition = transitionAt(edit, index);
      if (!transition) continue;
      const joint = starts[index + 1];
      if (transition.type === "fade") {
        const half = transition.duration / 2, distance = Math.abs(time - joint);
        if (distance < half) black = Math.max(black, 1 - distance / half);
      } else if (ghostJoint === index && time >= joint && time < joint + transition.duration) {
        handover = { type: transition.type, progress: (time - joint) / transition.duration };
        if (transition.type === "fadewhite") {
          black = Math.max(black, 1 - Math.abs(handover.progress * 2 - 1));
          white = true;
        }
      }
    }
    fadeLayer.style.opacity = black.toFixed(3);
    fadeLayer.classList.toggle("white", white);
    drawHandover(handover);
    const segment = edit.segments[session.index];
    if (segment) applyLevel(segment, active);
    if (segment) {
      const media = mediaOf(edit, segment.mediaId);
      const progress = (time - starts[session.index]) / Math.max(0.001, segmentLength(segment));
      lookPanel.apply(active, segment, progress, pictureRect(), { width: media?.width || active.videoWidth || 16, height: media?.height || active.videoHeight || 9 });
    }
    renderOverlays(edit, time);
    subs.frame();
    plainCoverFrame();
    // Sound clips play along at normal speed only (L twice plays faster).
    audio.frame(time, session.playing && session.shuttle === 1);
    textMode.frame(time);
  }

  /** The outgoing frame (the ghost) as an overlapping transition hands over; the export is exact. */
  function drawHandover(handover: { type: TransitionType; progress: number } | null): void {
    const style = ghost.style;
    style.clipPath = style.transform = style.maskImage = style.webkitMaskImage = "";
    if (!handover) {
      style.opacity = "0";
      return;
    }
    const p = Math.min(1, Math.max(0, handover.progress));
    const percent = `${(p * 100).toFixed(2)}%`;
    let opacity = 1;
    switch (handover.type) {
      case "dissolve": opacity = 1 - p; break;
      case "fadewhite": opacity = p < 0.5 ? 1 : 0; break;
      case "wipeleft": style.clipPath = `inset(0 ${percent} 0 0)`; break;
      case "wiperight": style.clipPath = `inset(0 0 0 ${percent})`; break;
      case "slideleft": style.transform = `translateX(-${percent})`; break;
      case "slideright": style.transform = `translateX(${percent})`; break;
      case "zoomin": style.transform = `scale(${(1 + p).toFixed(3)})`; opacity = 1 - p; break;
      case "circleopen": {
        const mask = `radial-gradient(circle at 50% 50%, transparent ${(p * 72).toFixed(2)}%, #000 ${(p * 72 + 0.5).toFixed(2)}%)`;
        style.maskImage = style.webkitMaskImage = mask;
        break;
      }
      default: opacity = 1 - p;
    }
    style.opacity = opacity.toFixed(3);
  }

  function boxFor(overlay: Overlay): Box {
    let box = boxes.get(overlay.id);
    if (!box) {
      const box_ = element("div", "edit-ov");
      box_.dataset.id = overlay.id;
      const player = element("video");
      player.preload = "auto";
      player.playsInline = true;
      const image = element("img");
      image.alt = "";
      image.draggable = false;
      image.hidden = true;
      const handle = element("span", "edit-ov-handle");
      handle.title = "Kéo để đổi cỡ";
      box_.append(player, image, handle);
      overlayLayer.append(box_);
      box = { element: box_, video: player, image, path: "" };
      boxes.set(overlay.id, box);
    }
    return box;
  }

  function renderOverlays(edit: Edit, time: number): void {
    if (!session) return;
    const rect = pictureRect();
    Object.assign(overlayLayer.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` });
    const total = outputDuration(edit);
    const level = mainLevel(edit, time, total);
    const seen = new Set<string>();
    for (const [layer, overlay] of edit.overlays.entries()) {
      seen.add(overlay.id);
      const length = overlayLength(edit, overlay, total);
      const on = time >= overlay.at && time < overlay.at + length;
      const selected = session.overlay === overlay.id;
      const existing = boxes.get(overlay.id);
      if (!on && !selected) {
        if (existing) {
          existing.element.hidden = true;
          if (!existing.video.paused) existing.video.pause();
        }
        continue;
      }
      const box = boxFor(overlay);
      const media = mediaOf(edit, overlay.mediaId);
      const path = media ? mediaPath(media) : "";
      const still = isStillPath(path);
      if (box.path !== path) {
        box.path = path;
        box.video.removeAttribute("src");
        box.image.removeAttribute("src");
        box.video.hidden = still;
        box.image.hidden = !still;
        void host.mediaUrl(path).then((url) => {
          if (!url || box.path !== path) return;
          if (still) box.image.src = url;
          else box.video.src = url;
        }).catch(() => undefined);
      }
      box.element.hidden = false;
      // Later overlays are drawn on top (Lớp), as the export lays them.
      box.element.style.zIndex = String(layer + 1);
      box.element.classList.toggle("selected", selected);
      box.element.classList.toggle("idle", !on);
      box.element.title = `${media?.title || (still ? "Ảnh chồng" : "Video chồng")} · kéo để đổi chỗ`;
      // Paused on a selected overlay: the key being edited; otherwise where it is now (Chuyển động, fades).
      const editingKey = selected && !session.playing;
      const now = overlayAt(edit, overlay, time, total);
      const place = editingKey ? placementOf(overlay, keyOf(overlay)) : now;
      const height = overlayHeight(edit, { mediaId: overlay.mediaId, w: place.w });
      Object.assign(box.element.style, {
        left: `${place.x * 100}%`, top: `${place.y * 100}%`, width: `${place.w * 100}%`, height: `${height * 100}%`,
        opacity: on && !editingKey && now.alpha < 1 ? now.alpha.toFixed(3) : "",
      });
      box.element.classList.toggle("end-key", editingKey && keyOf(overlay) === "end");
      // Độ trong, bo góc, viền as the export draws them (sizes on a 1080 frame).
      const look = overlayStyle(overlay);
      const boxWidth = place.w * rect.width, boxHeight = height * rect.height;
      const border = look.border ? Math.max(1, look.border * Math.min(rect.width, rect.height) / 1080) : 0;
      Object.assign((still ? box.image : box.video).style, {
        opacity: look.opacity === 1 ? "" : String(look.opacity),
        borderRadius: look.shape === "ellipse" ? "50%" : look.radius ? `${(look.radius * Math.min(boxWidth, boxHeight)).toFixed(1)}px` : "",
        border: border ? `${border.toFixed(1)}px solid ${look.borderColor}` : "",
      });
      const player = box.video;
      if (still) {
        if (!player.paused) player.pause();
      } else if (on) {
        const want = overlay.start + (time - overlay.at);
        player.muted = overlay.volume * level === 0;
        player.volume = Math.min(1, overlay.volume * level);
        if (player.readyState >= 1 && Math.abs(player.currentTime - want) > (session.playing ? 0.3 : 0.04)) player.currentTime = want;
        if (session.playing && player.paused && player.readyState >= 2) void player.play().catch(() => undefined);
        else if (!session.playing && !player.paused) player.pause();
      } else if (!player.paused) {
        player.pause();
      }
    }
    for (const [id, box] of boxes) {
      if (seen.has(id)) continue;
      box.image.removeAttribute("src");
      release(box.video);
      box.element.remove();
      boxes.delete(id);
    }
  }

  new ResizeObserver(() => {
    if (!session) return;
    renderOverlays(current(), session.outputTime);
    plainCoverFrame();
    // Texts measure their lines: after this layout pass.
    requestAnimationFrame(() => { if (session) textMode.frame(session.outputTime); });
  }).observe(frame);

  /** Phụ đề and Che jump the playhead to a line or a region. */
  function seekTo(time: number): void {
    if (!session) return;
    session.overlay = null;
    seekOutput(time);
    session.selected = outputToSource(current(), session.outputTime)?.segment.id ?? session.selected;
    renderPanel();
    renderTimeline();
  }
  // Under Che a click on the picture beside the regions drops the selection instead of playing.
  // A click that began on a region or while drawing one is not a click beside them.
  let pressed: Element | null = null;
  frame.addEventListener("pointerdown", (event) => { pressed = event.target as Element; }, true);
  frame.addEventListener("click", (event) => {
    if (!session || mode !== "cover" || pressed?.closest(".cover-box, #cover-draw, #cover-boxes")) return;
    if (cover.deselect()) {
      event.stopPropagation();
      event.preventDefault();
    }
  }, true);
  const cover = mountCover({
    video,
    picture: () => pictureRect(),
    edit: () => current(),
    outputTime: () => session?.outputTime ?? 0,
    seek: seekTo,
    job: () => session?.target.job ?? null,
    changed: () => {
      if (session?.plainCover) {
        plainCoverFrame();
        if (subs.showing) subs.frame();
        scheduleSave();
        renderPanel();
      } else subs.refresh();
    },
  });

  /**
   * Che on a video exported here: the preview layer shows the cover over the
   * main video (the regions take the pointer only under Che), and the
   * subtitle block of a dubbing job stays hidden.
   */
  function plainCoverDocument(project: unknown): unknown {
    let remembered: unknown = null;
    try {
      remembered = JSON.parse(window.localStorage.getItem(COVER_STORAGE_KEY) ?? "null");
    } catch {
      // The remembered style is a convenience.
    }
    return plainCover(project, remembered);
  }

  function plainCoverFrame(): void {
    // The plain subtitle layer draws the cover with its lines.
    if (!session?.plainCover || subs.showing) return;
    const layer = $("#edit-subs-layer");
    const show = mode === "cover" || !!cover.document();
    layer.hidden = !show;
    layer.classList.toggle("passive", true);
    layer.classList.add("plain-cover");
    if (!show) return cover.draw(null);
    const rect = pictureRect();
    Object.assign(layer.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` });
    $("#subs-block").hidden = true;
    const point = outputToSource(current(), session.outputTime);
    cover.draw(point && point.segment.mediaId === MAIN ? point.sourceTime : null);
  }
  const docuCover = mountDocuCover(invoke, {
    note,
    confirm: (options) => host.confirm(options),
    // The film was rendered again: open it afresh, keeping its draft and the Che tab.
    reload: async () => {
      if (!session) return;
      const target = session.target;
      if (!(await close(false, true))) return;
      if (await open(target)) setMode("cover");
    },
  });
  const docuLayers = mountDocuLayers(invoke, {
    note,
    confirm: (options) => host.confirm(options),
    reload: async (layers) => {
      if (!session) return;
      const target = session.target;
      if (!(await close(false, true))) return;
      if (await open(target)) await applyLayers(layers);
    },
  });

  /** The documentary's split layers back in the reopened draft (Issue #525). */
  async function applyLayers(layers: Layers): Promise<void> {
    if (!session) return;
    let edit = current();
    for (const callout of layers.texts) edit = addText(edit, callout.at, callout.text, callout.look, callout.seconds);
    if (layers.bed) {
      const facts = await invoke<AudioFacts>("audio_info", { path: layers.bed }).catch(() => null);
      if (!session) return;
      if (facts) edit = addAudio(edit, { source: { kind: "file", path: layers.bed }, title: "Nhạc nền phim", duration: facts.duration }, 0);
      else note("Không đọc được nhạc nền đã tách");
    }
    if (edit !== current()) commit(edit);
    if (layers.burnCaptions && session.plainSubs) {
      session.plainSubs.burn = true;
      session.subsTouched = true;
      subs.refresh();
      scheduleSave();
      renderPanel();
    }
  }

  const subs = mountSubtitles(invoke, host, {
    video,
    picture: () => pictureRect(),
    edit: () => current(),
    outputTime: () => session?.outputTime ?? 0,
    playing: () => !!session?.playing,
    active: () => active,
    seek: seekTo,
    pps: () => pps,
    pause: () => { if (session?.playing) pausePlayback(); },
    flush: () => flush(),
    finish: async () => { await close(false); },
    changed: () => renderPanel(),
    cover,
    plainChanged: () => {
      if (!session?.plainSubs) return;
      session.subsTouched = true;
      try {
        window.localStorage.setItem(STYLE_STORAGE_KEY, JSON.stringify(session.plainSubs.style));
      } catch {
        // The remembered look is a convenience.
      }
      scheduleSave();
      renderPanel();
    },
    plainCover: (seconds) => cover.draw(session?.plainCover && (mode === "cover" || !!cover.document()) ? seconds : null),
  });
  const revisionReady = $("#edit-revision-ready");
  const audio = mountAudio(invoke, host, {
    edit: () => current(),
    base: () => session!.history.current,
    busy: () => !session || !!session.draft,
    commit: (next) => commit(next),
    preview: (next) => {
      if (!session) return;
      session.draft = next;
      renderPanel();
      renderTimeline();
      renderEffects();
    },
    outputTime: () => session?.outputTime ?? 0,
    playing: () => !!session?.playing,
    seek: (time) => seekOutput(time),
    pps: () => pps,
    timeAt: (clientX) => timeAt(clientX),
    cues: () => session ? remapCues(session.plainSubs ? exportLines(session.plainSubs) : session.facts.cues, current()) : [],
    enterMode: () => {
      if (!session) return;
      session.overlay = null;
      if (mode !== "audio") setMode("audio");
      else render();
    },
    note,
    redraw: () => { if (session) render(); },
  });
  const textMode = mountText({
    edit: () => current(),
    base: () => session!.history.current,
    busy: () => !session || !!session.draft,
    commit: (next) => commit(next),
    preview: (next) => {
      if (!session) return;
      session.draft = next;
      renderPanel();
      renderTimeline();
      renderEffects();
    },
    outputTime: () => session?.outputTime ?? 0,
    seek: (time) => seekOutput(time),
    pause: () => { if (session?.playing) pausePlayback(); },
    pps: () => pps,
    timeAt: (clientX) => timeAt(clientX),
    picture: () => pictureRect(),
    enterMode: () => {
      if (!session) return;
      session.overlay = null;
      if (mode !== "text") setMode("text");
      else render();
    },
    note,
    redraw: () => { if (session) render(); },
  });

  const lookPanel = mountLook({
    edit: () => current(),
    base: () => session!.history.current,
    busy: () => !session || !!session.draft,
    commit: (next, selected) => commit(next, selected),
    preview: (next) => {
      if (!session) return;
      session.draft = next;
      renderPanel();
      renderTimeline();
      renderEffects();
    },
    selected: () => selectedSegment(),
    outputTime: () => session?.outputTime ?? 0,
    note,
    pickLut: () => invoke<string | null>("pick_lut"),
    lutFrame: async (segment, lut) => {
      const edit = current();
      const media = mediaOf(edit, segment.mediaId);
      if (!media) throw new Error("Không tìm thấy video của đoạn này.");
      // The playhead's frame when it is in this segment, else the segment's first.
      const point = outputToSource(edit, session?.outputTime ?? 0);
      const time = point?.segment.id === segment.id ? point.sourceTime : segment.start;
      const bytes = await invoke<ArrayBuffer>("edit_lut_frame", { path: mediaPath(media), time, lut });
      return URL.createObjectURL(new Blob([bytes], { type: "image/png" }));
    },
  });
  const templateMode = mountTemplate(invoke, {
    draft: () => session ? { edit: session.history.current, changes: session.history.changes, selected: session.selected, recent: session.recent } : null,
    flush: () => flush(),
    pause: () => { if (session?.playing) pausePlayback(); },
    cpuOnly: () => host.cpuOnly(),
    reopen: async (next) => {
      if (!session) return;
      const from = session.target;
      await close(false, true);
      mode = "template";
      const opened = await open({ id: next.outputPath, kind: "product", outputPath: next.outputPath, displayName: next.displayName });
      if (!opened) leave(from);
    },
    changed: () => renderPanel(),
  });

  // ---------- Changes, history, autosave ----------

  /** After an edit changes, keep the playhead on the same picture when it still exists. */
  function settle(before: Edit, after: Edit): void {
    if (!session) return;
    const point = outputToSource(before, session.outputTime);
    let time: number | null = null;
    if (point) {
      const index = after.segments.findIndex((segment) => segment.id === point.segment.id);
      time = sourceToOutput(after, point.sourceTime, index, point.segment.mediaId);
      if (time === null && index < 0) time = outputStarts(after)[Math.min(point.index, after.segments.length - 1)] ?? 0;
    }
    if (!after.segments.some((segment) => segment.id === session!.selected)) {
      session.selected = outputToSource(after, time ?? session.outputTime)?.segment.id ?? after.segments[0].id;
    }
    if (session.overlay && !after.overlays.some((overlay) => overlay.id === session!.overlay)) session.overlay = null;
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
    const { outputPath, history, selected, recent, target: { kind } } = session;
    try {
      const project = serializeProject(history.current, history.changes, selected, recent);
      if (session.plainCover) project.cover = cover.document();
      if (session.plainSubs && session.subsTouched) (project as Record<string, unknown>).subtitles = serializePlain(session.plainSubs);
      await invoke("edit_project_save", { outputPath, kind, project });
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

  function selectedOverlay(): Overlay | undefined {
    if (!session?.overlay) return undefined;
    return current().overlays.find((overlay) => overlay.id === session!.overlay);
  }

  function splitAtPlayhead(): void {
    if (!session || session.draft) return;
    const edit = session.history.current;
    const next = split(edit, session.outputTime);
    if (next === edit) {
      note(edit.segments.length >= MAX_SEGMENTS ? "Đã đạt tối đa 500 đoạn." : "Đầu phát đang sát mép đoạn; dời đi một chút rồi tách.");
      return;
    }
    session.overlay = null;
    commit(next, outputToSource(next, session.outputTime)?.segment.id);
  }

  function deleteSelected(): void {
    if (!session || session.draft) return;
    if (mode === "audio") {
      audio.deleteSelected();
      return;
    }
    if (mode === "text") {
      textMode.deleteSelected();
      return;
    }
    const edit = session.history.current;
    const overlay = selectedOverlay();
    if (overlay) {
      session.overlay = null;
      commit(removeOverlay(edit, overlay.id));
      return;
    }
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

  function step(seconds_: number): void {
    if (session) seekOutput(session.outputTime + seconds_);
  }

  function selectOverlay(id: string | null): void {
    if (!session) return;
    session.overlay = id;
    render();
  }

  // ---------- Arranging, clipboard, markers, in/out, J/K/L ----------

  function duplicateSelected(): void {
    if (!session || session.draft) return;
    const segment = selectedSegment();
    if (!segment) return;
    const edit = session.history.current;
    const next = duplicateSegment(edit, segment.id);
    if (next === edit) {
      note("Đã đạt tối đa 500 đoạn.");
      return;
    }
    const index = edit.segments.indexOf(segment);
    session.overlay = null;
    commit(next, next.segments[index + 1].id);
    seekOutput(outputStarts(next)[index + 1]);
    note("Đã nhân bản đoạn; bản sao nằm ngay sau.");
  }

  function copySelected(): boolean {
    if (!session) return false;
    const segment = selectedSegment();
    const clip = segment ? copySegment(current(), segment.id) : null;
    if (!clip) return false;
    clipboard = clip.media.kind === "main"
      ? { ...clip, media: { ...clip.media, path: session.outputPath, title: clip.media.title || session.target.displayName } }
      : clip;
    note(`Đã sao chép đoạn dài ${seconds(segmentLength(segment!))}. Ctrl V để dán tại đầu phát.`);
    return true;
  }

  function cutSelected(): void {
    if (!session || session.draft || current().segments.length <= 1) return;
    if (copySelected()) deleteSelected();
  }

  function pasteAtPlayhead(): void {
    if (!session || session.draft) return;
    if (!clipboard) {
      note("Chưa sao chép đoạn nào. Chọn một đoạn rồi bấm Ctrl C.");
      return;
    }
    const edit = session.history.current;
    const next = pasteSegment(edit, session.outputTime, clipboard, session.outputPath);
    if (next === edit) {
      note(edit.media.length >= MAX_MEDIA ? "Đã dùng tối đa 20 video trong một bản chỉnh sửa." : "Đã đạt tối đa 500 đoạn.");
      return;
    }
    const added = addedId(edit, next);
    session.overlay = null;
    commit(next, added ?? undefined);
    const index = next.segments.findIndex((segment) => segment.id === added);
    if (index >= 0) seekOutput(outputStarts(next)[index]);
  }

  function addMarkerHere(): void {
    if (!session || session.draft) return;
    const edit = session.history.current;
    const next = addMarker(edit, session.outputTime, `Dấu ${edit.markers.length + 1}`);
    if (next === edit) {
      note(edit.markers.length >= MAX_MARKERS ? "Đã có tối đa 100 dấu." : "Đã có dấu ở đúng chỗ này.");
      return;
    }
    if (mode !== "cut") setMode("cut");
    commit(next);
  }

  function setMark(which: "in" | "out"): void {
    if (!session) return;
    const time = Math.round(session.outputTime * 1000) / 1000;
    if (which === "in") {
      session.markIn = time;
      if (session.markOut !== null && session.markOut <= time + MIN_SEGMENT) session.markOut = null;
    } else {
      session.markOut = time;
      if (session.markIn !== null && session.markIn >= time - MIN_SEGMENT) session.markIn = null;
    }
    if (mode !== "cut") setMode("cut");
    else render();
  }

  function clearMarks(): boolean {
    if (!session || (session.markIn === null && session.markOut === null)) return false;
    session.markIn = session.markOut = null;
    render();
    return true;
  }

  /** The I–O range, filling a missing end with the start or end of the video. */
  function markedRange(): { from: number; to: number } | null {
    if (!session || (session.markIn === null && session.markOut === null)) return null;
    return { from: session.markIn ?? 0, to: session.markOut ?? outputDuration(current()) };
  }

  function applyRange(keep: boolean): void {
    if (!session || session.draft) return;
    const range = markedRange();
    if (!range) return;
    const edit = session.history.current;
    const next = keep ? keepRange(edit, range.from, range.to) : removeRange(edit, range.from, range.to);
    if (next === edit) {
      note(keep ? "Khoảng chọn đã là cả video." : "Không cắt được: khoảng quá ngắn hoặc là cả video.");
      return;
    }
    session.markIn = session.markOut = null;
    session.outputTime = keep ? 0 : Math.min(range.from, outputDuration(next));
    commit(next);
    seekOutput(keep ? 0 : Math.min(range.from, outputDuration(next)));
  }

  /** L: play; again while playing: 2× (and back). */
  function shuttleForward(): void {
    if (!session) return;
    if (!session.playing) {
      session.shuttle = 1;
      togglePlay();
      return;
    }
    session.shuttle = session.shuttle >= 2 ? 1 : 2;
    const segment = current().segments[session.index];
    if (segment) applySegment(segment, active);
    renderEffects();
    note(session.shuttle === 1 ? "Phát tốc độ thường." : "Phát nhanh 2×; âm thanh thêm tạm tắt khi phát nhanh.");
  }

  function stepFrames(count: number): void {
    if (!session) return;
    if (session.playing) pausePlayback();
    const fps = mediaOf(current(), MAIN)?.fps || session.facts.fps || 30;
    step(count / fps);
  }

  function jumpPoint(direction: -1 | 1): void {
    if (!session) return;
    seekOutput(nearbyPoint(current(), session.outputTime, direction));
    renderPlayhead(true);
  }

  // ---------- Context menu ----------

  function openMenu(x: number, y: number): void {
    if (!session) return;
    const edit = current();
    for (const item of menu.querySelectorAll<HTMLButtonElement>("[data-action]")) {
      const action = item.dataset.action;
      item.disabled = (action === "paste" && !clipboard) || ((action === "delete" || action === "cut") && edit.segments.length <= 1);
    }
    menu.hidden = false;
    const box = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - box.width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - box.height - 8))}px`;
    menu.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
  }

  function closeMenu(): void {
    menu.hidden = true;
  }

  /** Length, frame and sound of a local video, probed once. */
  function mediaInfo(path: string, kind: MediaInfo["kind"], title?: string): Promise<MediaInfo | null> {
    const key = path.toLocaleLowerCase("en");
    let pending = infoCache.get(key);
    if (!pending) {
      pending = invoke<MediaResponse>("edit_media_info", { path })
        .then((info) => ({ path, title: title || info.title || stem(path), duration: info.duration, width: info.width, height: info.height,
          fps: info.fps, hasAudio: info.has_audio, kind }))
        .catch((error) => {
          infoCache.delete(key);
          host.showError(`Không đọc được video ${fileName(path)}: ${String(error).slice(0, 160)}`);
          return null;
        });
      infoCache.set(key, pending);
    }
    return pending.then((info) => info && { ...info, kind, title: title || info.title });
  }

  /** Why an insert did nothing. */
  function refusal(edit: Edit, info: MediaInfo, overlay: boolean): string {
    if (info.duration < MIN_SEGMENT) return "Video này quá ngắn để chèn.";
    if (overlay && edit.overlays.length >= MAX_OVERLAYS) return "Đã đạt tối đa 20 lớp chồng (video, ảnh).";
    if (!overlay && edit.segments.length >= MAX_SEGMENTS - 1) return "Đã đạt tối đa 500 đoạn.";
    if (edit.media.length >= MAX_MEDIA && !edit.media.some((media) => samePath(media.path, info.path))) return "Một bản sửa dùng tối đa 20 video khác nhau.";
    return "Không chèn được video này.";
  }

  /** Insert (at `time`, else the playhead), append, or overlay a media; returns the new segment or overlay id. */
  function place(info: MediaInfo, chosen: Where, time?: number): string | null {
    if (!session || session.draft) return null;
    // A picture is a logo or still over the video, never a segment.
    const how: Where = isStillPath(info.path) ? "overlay" : chosen;
    const edit = session.history.current;
    const main = mediaOf(edit, MAIN);
    const media = main && samePath(info.path, session.outputPath) ? { ...info, path: main.path || session.outputPath } : info;
    const next = how === "overlay" ? addOverlay(edit, media, session.outputTime)
      : how === "end" ? append(edit, media) : insertAt(edit, time ?? session.outputTime, media);
    if (next === edit) {
      note(refusal(edit, media, how === "overlay"));
      return null;
    }
    const id = addedId(edit, next);
    if (how === "overlay") {
      session.overlay = id;
      commit(next);
    } else {
      session.overlay = null;
      commit(next, id ?? undefined);
      const index = next.segments.findIndex((segment) => segment.id === id);
      if (index >= 0) seekOutput(outputStarts(next)[index]);
    }
    render();
    const controls = how === "overlay" ? $("#edit-ov-controls") : $("#edit-seg-controls");
    controls.scrollIntoView({ block: "nearest" });
    return id;
  }

  async function placePath(path: string, kind: MediaInfo["kind"], title: string | undefined, how: Where): Promise<void> {
    const info = await mediaInfo(path, kind, title);
    if (info && session) place(info, how);
  }

  function remember(info: MediaInfo): void {
    if (!session) return;
    const media: Media = { ...info, id: `r${session.recent.length + 1}`, kind: "file" };
    session.recent = [media, ...session.recent.filter((item) => !samePath(item.path, info.path))].slice(0, RECENT_LIMIT);
    scheduleSave();
  }

  async function pickFiles(): Promise<void> {
    let paths: string[] = [];
    try {
      paths = await invoke<string[]>("pick_edit_files");
    } catch (error) {
      host.showError(`Không mở được hộp thoại chọn file: ${String(error).slice(0, 160)}`);
      return;
    }
    for (const path of paths) {
      const info = await mediaInfo(path, "file");
      if (info) remember(info);
    }
    renderSources();
  }

  /** A drop point on the timeline, snapped to a segment edge within a few pixels. */
  function dropTime(clientX: number): number {
    const edit = session!.history.current;
    const time = clamp(timeAt(clientX), 0, outputDuration(edit));
    const edges = [...outputStarts(edit), outputDuration(edit)];
    const nearest = edges.reduce((best, edge) => Math.abs(edge - time) < Math.abs(best - time) ? edge : best, edges[0]);
    return Math.abs(nearest - time) * pps <= DROP_SNAP_PX ? nearest : time;
  }

  /**
   * Dropped videos go in one after another at the drop point on the timeline;
   * dropped anywhere else they follow the "Chèn vào đâu" choice.
   */
  async function dropFiles(dropped: string[], point: { x: number; y: number } | null): Promise<void> {
    if (!session || !dropped.length) return;
    // Audio files go to the sound row; videos (and folders) keep the Chèn behaviour.
    const sounds = dropped.filter(isAudioPath);
    const stills = dropped.filter(isStillPath);
    const paths = dropped.filter((path) => !isAudioPath(path) && !isStillPath(path));
    if (sounds.length) await dropAudio(sounds, point);
    // Pictures go over the video, from the playhead.
    for (const path of stills) {
      const info = await mediaInfo(path, "file");
      if (!info || !session) continue;
      remember(info);
      place(info, "overlay");
    }
    if (!paths.length || !session) return;
    let videos: string[] = [];
    try {
      videos = await invoke<string[]>("expand_video_paths", { paths });
    } catch (error) {
      host.showError(`Không đọc được mục đã thả: ${String(error).slice(0, 160)}`);
      return;
    }
    if (!videos.length) {
      note("Không tìm thấy video trong mục đã thả.");
      return;
    }
    const box = scroll.getBoundingClientRect();
    const overTimeline = !!point && point.x >= box.left && point.x <= box.right && point.y >= box.top - 40 && point.y <= box.bottom;
    const how: Where = overTimeline ? "playhead" : where;
    let time = overTimeline ? dropTime(point!.x) : session.outputTime;
    for (const path of videos) {
      const info = await mediaInfo(path, "file");
      if (!info || !session) continue;
      remember(info);
      if (how !== "playhead") {
        place(info, how);
        continue;
      }
      const id = place(info, "playhead", time);
      if (!id) break;
      const edit = session.history.current;
      const index = edit.segments.findIndex((segment) => segment.id === id);
      time = outputStarts(edit)[index] + segmentLength(edit.segments[index]);
    }
    renderSources();
  }

  /** Dropped audio files become sound clips at the drop point (or the playhead), one after another. */
  async function dropAudio(paths: string[], point: { x: number; y: number } | null): Promise<void> {
    if (!session) return;
    let files: string[] = [];
    try {
      files = await invoke<string[]>("expand_audio_paths", { paths });
    } catch (error) {
      host.showError(`Không đọc được file âm thanh đã thả: ${String(error).slice(0, 160)}`);
      return;
    }
    if (!files.length || !session) {
      note("Không đọc được file âm thanh đã thả.");
      return;
    }
    const box = scroll.getBoundingClientRect();
    const overTimeline = !!point && point.x >= box.left && point.x <= box.right && point.y >= box.top - 40 && point.y <= box.bottom;
    const time = overTimeline ? clamp(timeAt(point!.x), 0, outputDuration(session.history.current)) : session.outputTime;
    await audio.drop(files, time);
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

  /** The modes this video has: Mẫu for Bán hàng videos, Phụ đề and Che for dubbing jobs. */
  function offered(next: Mode): boolean {
    return !!session && modeOffered(next, session.target.kind, session.reviewable);
  }

  function renderModes(): void {
    for (const tab of modeTabs) {
      tab.setAttribute("aria-selected", String(tab.dataset.mode === mode));
      tab.hidden = !offered(tab.dataset.mode as Mode);
    }
    $("#edit-panel").classList.toggle("subs", mode === "subs" || mode === "cover");
    $("#edit-panel").classList.toggle("audio", mode === "audio");
    $("#edit-panel").classList.toggle("template", mode === "template");
  }

  function renderPanel(): void {
    if (!session) return;
    const { history, facts } = session;
    const edit = current();
    renderModes();
    undoButton.disabled = !history.canUndo || !!session.draft;
    redoButton.disabled = !history.canRedo || !!session.draft;
    const pending = unexported();
    changesBadge.hidden = !pending;
    $("#edit-changes-text").textContent = `${history.changes} thay đổi chưa xuất`;
    changesBadge.title = `${history.changes} thay đổi chưa xuất · bản nháp được tự lưu`;
    exportButton.disabled = isUnchanged(edit, facts.duration) && !(session.plainCover && cover.document())
      && !(session.plainSubs && session.subsTouched);
    exportButton.title = exportButton.disabled ? "Chưa có thay đổi để xuất" : "Xuất bản đã chỉnh sửa thành file video mới";
    const panelMode = mode === "subs" || mode === "cover" || mode === "audio" || mode === "text" || mode === "template" || mode === "more";
    $("#edit-more").hidden = mode !== "more";
    const overlay = panelMode ? undefined : selectedOverlay();
    const segment = selectedSegment();
    const inserted = !!segment && segment.mediaId !== MAIN;
    $("#edit-sources").hidden = mode !== "insert";
    $("#edit-ov-controls").hidden = !overlay;
    $("#edit-seg-controls").hidden = panelMode || !!overlay || (mode === "insert" && !inserted);
    $("#edit-select-hint").hidden = !(mode === "insert" && !overlay && !inserted);
    $("#edit-cut-notes").hidden = mode !== "cut" || !!overlay;
    renderRangePanel(mode === "cut" && !overlay);
    renderMarkerList(edit, mode === "cut" && !overlay);
    audio.render(mode === "audio");
    textMode.render(mode === "text");
    templateMode.render(mode === "template");
    if (templateMode.dirty() || templateMode.busy) {
      changesBadge.hidden = false;
      $("#edit-changes-text").textContent = templateMode.busy ? "Đang dựng lại theo mẫu" : pending ? `${history.changes} thay đổi · mẫu chưa áp dụng` : "Mẫu chưa áp dụng";
      changesBadge.title = templateMode.busy ? "Video đang được dựng lại theo mẫu mới" : "Bấm Áp dụng mẫu ở cuối bảng Mẫu để dựng lại video";
    }
    if (subs.dirty()) {
      changesBadge.hidden = false;
      $("#edit-changes-text").textContent = pending ? `${history.changes} thay đổi · chưa áp dụng` : "Chưa áp dụng";
      changesBadge.title = "Bấm Áp dụng ở cuối bảng Phụ đề hoặc Che để tạo phiên bản mới";
    }
    if (overlay) renderOverlayPanel(edit, overlay);
    if (!segment) return;
    const index = edit.segments.indexOf(segment);
    const start = outputStarts(edit)[index];
    const length = segmentLength(segment);
    const media = mediaOf(edit, segment.mediaId);
    const held = lookOf(segment).freeze !== null;
    lookPanel.render(segment);
    const title = $("#edit-segment-title");
    title.textContent = inserted
      ? `Đoạn chèn · ${media?.title || "video"} · ${formatClock(start)} – ${formatClock(start + length)}`
      : `Đoạn ${edit.segments.slice(0, index + 1).filter((item) => item.mediaId === MAIN).length} · ${formatClock(start)} – ${formatClock(start + length)} trên video xuất`;
    title.title = inserted ? media?.path ?? "" : "";
    if (document.activeElement !== startInput) startInput.value = formatClock(segment.start, true);
    if (document.activeElement !== endInput) endInput.value = formatClock(segment.end, true);
    $("#edit-length").textContent = held
      ? `Dừng hình ${formatClock(length, true)} tại ${formatClock(segment.start, true)} của video`
      : segment.speed === 1
      ? `Dài ${formatClock(segment.end - segment.start, true)} · ${edit.segments.length} đoạn`
      : `Dài ${formatClock(segment.end - segment.start, true)} · thành ${formatClock(length, true)} ở ${formatSpeed(segment.speed)}`;
    for (const button of speeds.querySelectorAll<HTMLButtonElement>("[data-speed]")) {
      button.setAttribute("aria-checked", String(Number(button.dataset.speed) === segment.speed));
    }
    if (document.activeElement !== customSpeed) customSpeed.value = String(segment.speed);
    if (document.activeElement !== volume) volume.value = String(Math.round(segment.volume * 100));
    $("#edit-volume-value").textContent = `${Math.round(segment.volume * 100)}%`;
    $("#edit-volume-note").hidden = segment.volume <= 1;
    $("#edit-delete-label").textContent = inserted ? "Xóa đoạn chèn" : "Xóa đoạn";
    $<HTMLButtonElement>("#edit-delete").disabled = edit.segments.length <= 1;
    $<HTMLButtonElement>("#tl-delete").disabled = edit.segments.length <= 1 && !overlay && !(mode === "audio" && audio.selected)
      && !(mode === "text" && textMode.selected);
  }

  function renderRangePanel(show: boolean): void {
    const range = markedRange();
    $("#edit-range").hidden = !show || !range;
    if (!range || !session) return;
    const both = session.markIn !== null && session.markOut !== null;
    $("#edit-range-text").textContent = `${session.markIn === null ? "Từ đầu" : formatClock(range.from, true)} – ${session.markOut === null ? "đến cuối" : formatClock(range.to, true)}`
      + ` · dài ${seconds(range.to - range.from)}${both ? "" : " · bấm " + (session.markIn === null ? "I" : "O") + " để đặt đầu còn lại"}`;
  }

  function renderMarkerList(edit: Edit, show: boolean): void {
    $("#edit-markers").hidden = !show;
    if (!show) return;
    const focused = document.activeElement instanceof HTMLInputElement && markerList.contains(document.activeElement)
      ? document.activeElement.closest<HTMLElement>("[data-id]")?.dataset.id : null;
    if (focused) return;
    if (!edit.markers.length) {
      markerList.replaceChildren(element("li", "markers-empty", "Bấm M hoặc nút Dấu để đánh dấu chỗ cần chú ý; dấu không xuất ra video."));
      return;
    }
    markerList.replaceChildren(...edit.markers.map((marker) => {
      const item = element("li", "marker-row");
      item.dataset.id = marker.id;
      const time = element("button", "marker-time", formatClock(marker.at, true));
      time.type = "button";
      time.title = "Tới dấu này";
      const name = element("input");
      name.type = "text";
      name.value = marker.label;
      name.maxLength = 60;
      name.placeholder = "Tên dấu";
      name.setAttribute("aria-label", `Tên dấu lúc ${formatClock(marker.at, true)}`);
      const remove_ = element("button", "marker-remove", "×");
      remove_.type = "button";
      remove_.dataset.role = "remove";
      remove_.title = "Xóa dấu";
      remove_.setAttribute("aria-label", "Xóa dấu");
      item.append(time, name, remove_);
      return item;
    }));
  }

  function renderOverlayPanel(edit: Edit, overlay: Overlay): void {
    const media = mediaOf(edit, overlay.mediaId);
    const title = $("#ov-title");
    const still = isStillPath(media?.path ?? "");
    title.textContent = `${still ? "Ảnh chồng" : "Video chồng"} · ${media?.title || (still ? "ảnh" : "video")}`;
    $("#ov-volume-field").hidden = still || !media?.hasAudio;
    $("#ov-start-field").hidden = still;
    $("#ov-end-label").textContent = still ? "Dài" : "Đến";
    const look = overlayStyle(overlay);
    for (const [input, value, text] of [[ovOpacity, look.opacity * 100, `${Math.round(look.opacity * 100)}%`],
      [ovRadius, look.radius * 100, look.radius ? `${Math.round(look.radius * 100)}%` : "Không"],
      [ovBorder, look.border, look.border ? `${look.border} px` : "Không"]] as const) {
      if (document.activeElement !== input) input.value = String(Math.round(value));
      $(`#${input.id}-value`).textContent = text;
    }
    for (const button of shapeButtons) button.setAttribute("aria-checked", String(button.dataset.shape === look.shape));
    $("#ov-radius-field").hidden = look.shape === "ellipse";
    const layer = edit.overlays.indexOf(overlay);
    $("#ov-layer-value").textContent = edit.overlays.length > 1 ? `${layer + 1}/${edit.overlays.length} (${layer === edit.overlays.length - 1 ? "trên cùng" : layer === 0 ? "dưới cùng" : "giữa"})` : "chỉ có 1 lớp";
    $<HTMLButtonElement>("#ov-layer-up").disabled = layer >= edit.overlays.length - 1;
    $<HTMLButtonElement>("#ov-layer-down").disabled = layer <= 0;
    if (document.activeElement !== ovBorderColor) ovBorderColor.value = look.borderColor.toLowerCase();
    ovBorderColor.disabled = !look.border;
    title.title = media?.path ?? "";
    const key = keyOf(overlay), place = placementOf(overlay, key);
    const preset = overlayPresetOf(edit, overlay, key);
    for (const button of presetButtons) button.setAttribute("aria-checked", String(button.dataset.preset === preset));
    if (document.activeElement !== ovSize) ovSize.value = String(Math.round(place.w * 100));
    $("#ov-size-value").textContent = `${Math.round(place.w * 100)}%`;
    ovMotion.checked = !!overlay.to;
    $("#ov-keys").hidden = !overlay.to;
    for (const button of keyButtons) button.setAttribute("aria-checked", String(button.dataset.key === key));
    $("#ov-motion-note").hidden = !overlay.to;
    for (const [input, value] of [[ovFadeIn, overlay.fadeIn ?? 0], [ovFadeOut, overlay.fadeOut ?? 0]] as const) {
      if (document.activeElement !== input) input.value = String(value);
      $(`#${input.id}-value`).textContent = value ? `${String(value).replace(".", ",")} giây` : "Tắt";
    }
    if (document.activeElement !== ovVolume) ovVolume.value = String(Math.round(overlay.volume * 100));
    $("#ov-volume-value").textContent = `${Math.round(overlay.volume * 100)}%`;
    $("#ov-volume-note").hidden = overlay.volume <= 1;
    if (document.activeElement !== ovAt) ovAt.value = formatClock(overlay.at, true);
    if (document.activeElement !== ovStart) ovStart.value = formatClock(overlay.start, true);
    if (document.activeElement !== ovEnd) ovEnd.value = formatClock(still ? overlay.end - overlay.start : overlay.end, true);
    const shown = overlayLength(edit, overlay);
    $("#ov-length").textContent = `Hiện ${formatClock(shown, true)} trên video · ${formatClock(overlay.at)} – ${formatClock(overlay.at + shown)}`;
    $<HTMLButtonElement>("#tl-delete").disabled = false;
  }

  function renderSources(): void {
    if (!session) return;
    for (const button of whereButtons) button.setAttribute("aria-checked", String(button.dataset.where === where));
    $("#insert-where-note").textContent = WHERE_LABEL[where].note;
    for (const tab of sourceTabs) tab.setAttribute("aria-selected", String(tab.dataset.source === source));
    $("#insert-dubflow").hidden = source !== "dubflow";
    $("#insert-computer").hidden = source !== "computer";
    if (source === "dubflow") {
      $("#insert-library").replaceChildren(...library.map(libraryRow));
      $("#insert-library-empty").hidden = library.length > 0;
    } else {
      const recent = session.recent.map((media): LibraryItem => ({
        key: `file:${media.path}`, path: media.path, title: media.title || stem(media.path), meta: folderOf(media.path), kind: "file", duration: media.duration,
      }));
      $("#insert-recent").replaceChildren(...recent.map(libraryRow));
    }
  }

  function libraryRow(item: LibraryItem): HTMLLIElement {
    const row = element("li", "insert-item");
    const thumb = element("span", "insert-thumb");
    thumb.dataset.poster = item.path;
    const badge = element("small", "", item.duration ? formatClock(item.duration) : "");
    thumb.append(badge);
    const poster = posters.get(item.path);
    if (poster) thumb.style.backgroundImage = `url("${poster}")`;
    else if (poster === undefined) requestPoster(item.path);
    if (!item.duration) {
      void mediaInfo(item.path, item.kind).then((info) => {
        if (info && !badge.textContent) badge.textContent = formatClock(info.duration);
      });
    }
    const text = element("span", "insert-text");
    text.append(element("strong", "", item.title), element("small", "", item.meta));
    text.title = item.path;
    const action = element("button", "btn", WHERE_LABEL[where].button);
    action.type = "button";
    action.title = `${WHERE_LABEL[where].action}: ${item.title}`;
    action.setAttribute("aria-label", `${WHERE_LABEL[where].action}: ${item.title}`);
    action.addEventListener("click", () => void placePath(item.path, item.kind, item.title, where));
    row.append(thumb, text, action);
    return row;
  }

  function requestPoster(path: string): void {
    if (posters.has(path) || posterQueue.includes(path)) return;
    posterQueue.push(path);
    pumpPosters();
  }

  function pumpPosters(): void {
    while (posterWorkers < 2 && posterQueue.length) {
      const path = posterQueue.shift()!;
      posterWorkers += 1;
      const poster: Promise<string | null> = isStillPath(path) ? host.mediaUrl(path)
        : invoke<ArrayBuffer>("video_frame", { sourcePath: path, position: 0.1, maxWidth: POSTER_PX })
          .then((bytes) => URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })));
      void poster.then((url) => posters.set(path, url || null))
        .catch(() => posters.set(path, null))
        .finally(() => {
          posterWorkers -= 1;
          const url = posters.get(path);
          if (url) {
            for (const thumb of document.querySelectorAll<HTMLElement>(".insert-thumb")) {
              if (thumb.dataset.poster === path) thumb.style.backgroundImage = `url("${url}")`;
            }
          }
          pumpPosters();
        });
    }
  }

  /** Finished queue videos other than this one, and every edited export of them. */
  async function loadLibrary(): Promise<void> {
    if (!session) return;
    const token = ++libraryToken;
    const jobs = host.library().filter((job) => job.id !== session!.target.id && job.outputPath);
    const items: LibraryItem[] = [];
    const statuses = await Promise.all(jobs.map((job) => invoke<StatusResponse>("edit_status", { outputPath: job.outputPath, kind: job.kind }).catch(() => null)));
    if (token !== libraryToken || !session) return;
    jobs.forEach((job, index) => {
      items.push({ key: `job:${job.id}`, path: job.outputPath, title: job.title, meta: job.meta ?? "Video đã làm trong DubFlow", kind: "job", duration: null });
      for (const entry of [...(statuses[index]?.exports ?? [])].reverse()) {
        items.push({ key: `export:${entry.path}`, path: entry.path, title: stem(entry.path), meta: `Bản sửa của ${job.title}`, kind: "job",
          duration: entry.duration > 0 ? entry.duration : null });
      }
    });
    library = items.slice(0, LIBRARY_LIMIT);
    renderSources();
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
    const tick_ = tickStep(pps);
    const ticks: HTMLElement[] = [];
    for (let time = 0; time <= total + 1e-6 && ticks.length < 2000; time += tick_) {
      const mark = element("span", "tl-tick", formatClock(time, tick_ < 1));
      mark.style.left = `${PAD + time * pps}px`;
      ticks.push(mark);
    }
    ruler.replaceChildren(...ticks);
    // Segments.
    const starts = outputStarts(edit);
    let number = 0;
    const blocks: HTMLElement[] = edit.segments.map((segment, index) => {
      const inserted = segment.mediaId !== MAIN;
      if (!inserted) number += 1;
      const media = mediaOf(edit, segment.mediaId);
      const block = element("div", `tl-seg${inserted ? " inserted" : ""}${segment.id === session!.selected && !session!.overlay ? " selected" : ""}`);
      block.dataset.id = segment.id;
      const left = PAD + starts[index] * pps;
      const length = segmentLength(segment) * pps;
      block.style.left = `${left}px`;
      block.style.width = `${Math.max(2, length)}px`;
      block.title = inserted
        ? `Đoạn chèn: ${media?.title ?? ""} ${formatClock(segment.start, true)} – ${formatClock(segment.end, true)}`
        : `Đoạn ${number}: ${formatClock(segment.start, true)} – ${formatClock(segment.end, true)} trong video gốc`;
      const strip = element("span", "tl-thumbs");
      const path = media ? mediaPath(media) : "";
      const grid = media ? gridFor(path, media.duration) : null;
      for (let x = 0; grid && x < length && strip.childElementCount < 400; x += THUMB_WIDTH) {
        const at = sourceAt(segment, (x + THUMB_WIDTH / 2) / pps);
        const cell = Math.min(grid.times.length - 1, Math.floor(Math.min(at, segment.end) / grid.step));
        if (cell < 0) break;
        const thumb = element("span", "tl-thumb");
        thumb.dataset.key = `${path}\n${cell}`;
        thumb.style.left = `${x}px`;
        strip.append(thumb);
      }
      const label = element("span", "tl-label");
      if (inserted && length >= 40) label.append(element("span", "tl-name media", media?.title || "Chèn"));
      else if (!inserted && length >= 34) label.append(element("span", "tl-name", `Đoạn ${number}`));
      if (segment.speed !== 1 && playsForward(segment) && length >= 24) label.append(element("span", "tl-badge", formatSpeed(segment.speed)));
      for (const badge of lookBadges(segment.look)) if (length >= 70) label.append(element("span", "tl-badge look", badge));
      if (segment.volume !== 1 && length >= 60) label.append(element("span", "tl-badge", segment.volume === 0 ? "Tắt tiếng" : `${Math.round(segment.volume * 100)}%`));
      const startHandle = element("span", "tl-handle start");
      startHandle.dataset.edge = "start";
      startHandle.title = "Kéo để cắt đầu đoạn";
      const endHandle = element("span", "tl-handle end");
      endHandle.dataset.edge = "end";
      endHandle.title = "Kéo để cắt cuối đoạn";
      block.append(strip);
      if (media?.hasAudio && segment.volume > 0 && length >= 8 && lookOf(segment).freeze === null) {
        const wave = element("canvas", "tl-wave");
        wave.dataset.path = path;
        wave.dataset.id = segment.id;
        block.append(wave);
      }
      if (lookOf(segment).freeze !== null) block.classList.add("held");
      block.append(label, startHandle, endHandle);
      return block;
    });
    // Joints: "+" adds a transition, a chip shows one.
    for (let index = 0; index + 1 < edit.segments.length; index++) {
      const transition = transitionAt(edit, index);
      const afterId = edit.segments[index].id;
      const joint = element("button", `tl-joint${transition ? ` on ${transition.type}` : ""}${popFor === afterId ? " open" : ""}`, transition ? "" : "+");
      joint.type = "button";
      joint.dataset.after = afterId;
      joint.style.left = `${PAD + (starts[index + 1] + (transition && overlaps(transition.type) ? transition.duration / 2 : 0)) * pps}px`;
      const label = transition ? `${TRANSITION_NAMES[transition.type]} ${seconds(transition.duration)}` : "Thêm chuyển cảnh";
      joint.title = `${label} · bấm để chọn chuyển cảnh`;
      joint.setAttribute("aria-label", `${label} sau đoạn ${index + 1}`);
      joint.setAttribute("aria-haspopup", "dialog");
      blocks.push(joint);
    }
    track.replaceChildren(...blocks);
    drawWaves(edit);
    // Overlay row.
    overlayTrack.hidden = !edit.overlays.length;
    content.classList.toggle("with-overlays", edit.overlays.length > 0);
    const ovLanes = overlayLanes(edit, total);
    const ovRows = Math.max(1, ...[...ovLanes.values()].map((lane) => lane + 1));
    overlayTrack.style.height = `${ovRows * OV_ROW - 4}px`;
    overlayTrack.replaceChildren(...edit.overlays.map((overlay, layer) => {
      const media = mediaOf(edit, overlay.mediaId);
      const block = element("div", `tl-ov${overlay.id === session!.overlay ? " selected" : ""}`);
      block.dataset.id = overlay.id;
      block.style.top = `${(ovLanes.get(overlay.id) ?? 0) * OV_ROW}px`;
      block.style.height = `${OV_ROW - 4}px`;
      block.style.zIndex = String(layer + 1);
      block.style.left = `${PAD + overlay.at * pps}px`;
      block.style.width = `${Math.max(6, overlayLength(edit, overlay, total) * pps)}px`;
      block.title = `${media && isStillPath(media.path) ? "Ảnh chồng" : "Video chồng"} · lớp ${layer + 1}/${edit.overlays.length}: ${media?.title ?? ""} · ${formatClock(overlay.at, true)} – ${formatClock(overlay.at + overlayLength(edit, overlay, total), true)}`;
      const name = element("span", "tl-ov-name", media?.title || "Video chồng");
      const startHandle = element("span", "tl-handle start");
      startHandle.dataset.edge = "start";
      const endHandle = element("span", "tl-handle end");
      endHandle.dataset.edge = "end";
      block.append(name, startHandle, endHandle);
      return block;
    }));
    renderMarks(edit, total);
    // Text and sound rows: under the video track; the timeline grows with its rows.
    const subsHeight = subs.renderTrack(subsTrack, PAD);
    const textHeight = textMode.renderTrack(textTrack, mode === "text", PAD) + (subsHeight ? 6 + subsHeight : 0);
    const audioHeight = audio.renderTrack(audioTrack, mode === "audio", PAD);
    content.classList.toggle("with-audio", audioHeight > 0 || textHeight > 0);
    const rows = 22 + (edit.overlays.length ? ovRows * OV_ROW + 2 : 0) + 48 + (textHeight ? 6 + textHeight : 0) + (audioHeight ? 6 + audioHeight : 0) + 16;
    // At most 40% of the window: further rows scroll, the video keeps its room.
    // Never lower than the timeline without rows (lower in short windows, see styles.css), so it does not jump.
    const least = window.innerHeight <= 600 ? SHORT_TIMELINE_PX : TIMELINE_PX;
    const most = Math.max(least, Math.round(window.innerHeight * 0.4));
    timeline.style.height = audioHeight || textHeight || ovRows > 1 ? `${Math.min(most, Math.max(least, 36 + rows))}px` : "";
    renderThumbs();
    renderPlayhead();
    const inserts = edit.segments.filter((segment) => segment.mediaId !== MAIN).length;
    $("#tl-info").textContent = [`${edit.segments.length} đoạn`, inserts ? `${inserts} đoạn chèn` : "", edit.overlays.length ? `${edit.overlays.length} lớp chồng` : "",
      edit.audio.length ? `${edit.audio.length} âm thanh` : "", edit.texts.length ? `${edit.texts.length} chữ` : "", formatClock(total)].filter(Boolean).join(" · ");
  }

  /** Markers on the ruler and the I–O range over the tracks. */
  function renderMarks(edit: Edit, total: number): void {
    if (!session) return;
    markerLayer.replaceChildren(...edit.markers.map((marker) => {
      const flag = element("div", "tl-marker");
      flag.dataset.id = marker.id;
      flag.style.left = `${PAD + marker.at * pps}px`;
      flag.title = `${marker.label || "Dấu"} · ${formatClock(marker.at, true)} · bấm để tới, kéo để dời`;
      flag.setAttribute("aria-label", flag.title);
      if (marker.label) flag.append(element("span", "tl-marker-name", marker.label));
      return flag;
    }));
    const range = markedRange();
    rangeLayer.hidden = !range;
    if (range) {
      rangeLayer.style.left = `${PAD + Math.min(range.from, total) * pps}px`;
      rangeLayer.style.width = `${Math.max(2, (Math.min(range.to, total) - Math.min(range.from, total)) * pps)}px`;
      rangeLayer.classList.toggle("open-start", session.markIn === null);
      rangeLayer.classList.toggle("open-end", session.markOut === null);
    }
  }

  // ---------- Waveforms ----------

  function requestPeaks(path: string): void {
    if (!path || peaks.has(path) || peaksLoading.has(path)) return;
    peaksLoading.add(path);
    void invoke<ArrayBuffer>("edit_audio_peaks", { path })
      .then((bytes) => peaks.set(path, new Uint8Array(bytes)))
      .catch(() => peaks.set(path, null))
      .finally(() => {
        peaksLoading.delete(path);
        if (session) drawWaves(current());
      });
  }

  /** Draw each segment's sound under its thumbnails (50 levels a second of its media). */
  function drawWaves(edit: Edit): void {
    const ratio = Math.min(2, window.devicePixelRatio || 1);
    for (const canvas of track.querySelectorAll<HTMLCanvasElement>("canvas.tl-wave")) {
      const path = canvas.dataset.path ?? "";
      const segment = edit.segments.find((candidate) => candidate.id === canvas.dataset.id);
      const levels = peaks.get(path);
      if (levels === undefined) {
        requestPeaks(path);
        continue;
      }
      if (!levels || !segment) {
        canvas.hidden = true;
        continue;
      }
      const width = Math.min(8192, Math.max(1, Math.round(segmentLength(segment) * pps)));
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(WAVE_PX * ratio);
      canvas.style.width = `${width}px`;
      const context = canvas.getContext("2d");
      if (!context) continue;
      context.scale(ratio, ratio);
      context.fillStyle = "oklch(0.86 0.12 160 / 0.85)";
      const gain = Math.min(1, 0.35 + segment.volume * 0.65);
      for (let x = 0; x < width; x += 2) {
        const a = sourceAt(segment, x / pps), b = sourceAt(segment, (x + 2) / pps);
        const from = Math.floor(Math.min(a, b) * 50);
        const to = Math.max(from + 1, Math.floor(Math.max(a, b) * 50));
        let level = 0;
        for (let index = from; index < to && index < levels.length; index++) level = Math.max(level, levels[index]);
        const height = Math.max(level ? 1 : 0, (level / 255) * WAVE_PX * gain);
        context.fillRect(x, WAVE_PX - height, 1.4, height);
      }
    }
  }

  function render(): void {
    if (!session) return;
    renderPanel();
    renderTimeline();
    subs.editChanged();
    renderEffects();
    if (!pop.hidden) renderPopover();
    host.renderPlayer();
  }

  // ---------- Thumbnails ----------

  function gridFor(path: string, duration: number): { step: number; times: number[]; duration: number } {
    let grid = grids.get(path);
    if (!grid) {
      grid = { ...thumbGrid(duration), duration };
      grids.set(path, grid);
    }
    return grid;
  }

  function renderThumbs(): void {
    if (!session) return;
    const low = scroll.scrollLeft - 300, high = scroll.scrollLeft + scroll.clientWidth + 300;
    for (const thumb of track.querySelectorAll<HTMLElement>(".tl-thumb")) {
      const key = thumb.dataset.key!;
      const url = thumbs.get(key);
      if (url) {
        if (!thumb.style.backgroundImage) thumb.style.backgroundImage = `url("${url}")`;
        continue;
      }
      if (url === null || wanted.includes(key)) continue;
      const block = thumb.closest<HTMLElement>(".tl-seg");
      const x = (block ? parseFloat(block.style.left) : 0) + parseFloat(thumb.style.left);
      if (x + THUMB_WIDTH >= low && x <= high) wanted.push(key);
    }
    pumpThumbs();
  }

  function pumpThumbs(): void {
    while (session && thumbWorkers < 2 && wanted.length) {
      const key = wanted.shift()!;
      if (thumbs.has(key)) continue;
      const [path, cellText] = key.split("\n");
      const grid = grids.get(path);
      const cell = Number(cellText);
      if (!grid || !(grid.duration > 0)) continue;
      thumbWorkers += 1;
      const epoch = thumbEpoch;
      const position = clamp(grid.times[cell] / grid.duration, 0, 1);
      void invoke<ArrayBuffer>("video_frame", { sourcePath: path, position, maxWidth: THUMB_PX })
        .then((bytes) => {
          if (epoch === thumbEpoch) thumbs.set(key, URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" })));
        })
        .catch(() => { if (epoch === thumbEpoch) thumbs.set(key, null); })
        .finally(() => {
          thumbWorkers -= 1;
          if (session && epoch === thumbEpoch) {
            const url = thumbs.get(key);
            if (url) {
              for (const thumb of track.querySelectorAll<HTMLElement>(".tl-thumb")) {
                if (thumb.dataset.key === key) thumb.style.backgroundImage = `url("${url}")`;
              }
            }
            pumpThumbs();
          }
        });
    }
  }

  function resetThumbs(): void {
    for (const url of thumbs.values()) if (url) URL.revokeObjectURL(url);
    thumbs.clear();
    grids.clear();
    wanted.length = 0;
    thumbEpoch += 1;
  }

  let scrollFrame = 0;
  scroll.addEventListener("scroll", () => {
    if (scrollFrame) return;
    scrollFrame = requestAnimationFrame(() => {
      scrollFrame = 0;
      renderThumbs();
      if (!pop.hidden) closePopover();
    });
  });
  new ResizeObserver(() => { if (session) renderTimeline(); }).observe(scroll);

  // ---------- Timeline input ----------

  function timeAt(clientX: number): number {
    const box = content.getBoundingClientRect();
    return (clientX - box.left - PAD) / pps;
  }

  type Drag = { kind: "seek" }
    | { kind: "trim"; id: string; edge: "start" | "end"; side: "start" | "end"; sign: number; x: number; from: number; base: Edit; moved: boolean }
    | { kind: "select"; id: string; x: number }
    | { kind: "reorder"; id: string; x: number; base: Edit; to: number }
    | { kind: "marker"; id: string; x: number; base: Edit; moved: boolean }
    | { kind: "ov-move"; id: string; x: number; base: Edit; moved: boolean }
    | { kind: "ov-trim"; id: string; edge: "start" | "end"; x: number; base: Edit; moved: boolean };
  let drag: Drag | null = null;

  /** `time` snapped to the timeline's points (Hút mép, not while Alt is held), showing the guide. */
  function snapped(time: number, event: { altKey: boolean }, edit: Edit, extra: number[] = [], skip: number[] = []): number {
    if (!session || !snapping || event.altKey) {
      snapLine.hidden = true;
      return time;
    }
    const marks = [session.markIn, session.markOut].filter((value): value is number => value !== null);
    const points = snapPoints(edit, [...extra, ...marks]).filter((point) => !skip.some((other) => Math.abs(other - point) < 1e-6));
    const result = snapTime(time, points, SNAP_PX / pps);
    snapLine.hidden = result === time;
    if (result !== time) snapLine.style.transform = `translateX(${PAD + result * pps}px)`;
    return result;
  }

  /** Where a dragged segment would land: the index among the others whose middle the pointer passed, and that joint's time. */
  function reorderTarget(base: Edit, id: string, time: number): { to: number; joint: number } {
    const starts = outputStarts(base);
    const others = base.segments.map((segment, at) => ({ segment, start: starts[at] })).filter(({ segment }) => segment.id !== id);
    let to = 0;
    others.forEach(({ segment, start }, index) => { if (time > start + segmentLength(segment) / 2) to = index + 1; });
    const joint = to === 0 ? others[0]?.start ?? 0 : others[to - 1].start + segmentLength(others[to - 1].segment);
    return { to, joint };
  }


  content.addEventListener("pointerdown", (event) => {
    if (!session || event.button !== 0 || session.draft) return;
    const target = event.target as HTMLElement;
    const joint = target.closest<HTMLElement>(".tl-joint");
    if (joint) {
      event.preventDefault();
      if (popFor === joint.dataset.after && !pop.hidden) closePopover();
      else openPopover(joint.dataset.after!, joint);
      return;
    }
    closeMenu();
    const flag = target.closest<HTMLElement>(".tl-marker");
    if (flag) {
      event.preventDefault();
      content.setPointerCapture(event.pointerId);
      drag = { kind: "marker", id: flag.dataset.id!, x: event.clientX, base: session.history.current, moved: false };
      return;
    }
    const handle = target.closest<HTMLElement>(".tl-handle");
    const overlayBlock = target.closest<HTMLElement>(".tl-ov");
    const block = target.closest<HTMLElement>(".tl-seg");
    event.preventDefault();
    content.setPointerCapture(event.pointerId);
    if (overlayBlock) {
      const id = overlayBlock.dataset.id!;
      session.overlay = id;
      const base = session.history.current;
      drag = handle
        ? { kind: "ov-trim", id, edge: handle.dataset.edge === "start" ? "start" : "end", x: event.clientX, base, moved: false }
        : { kind: "ov-move", id, x: event.clientX, base, moved: false };
      render();
      return;
    }
    if (handle && block) {
      const segment = session.history.current.segments.find((candidate) => candidate.id === block.dataset.id);
      if (!segment) return;
      const side = handle.dataset.edge === "start" ? "start" : "end";
      session.selected = segment.id;
      session.overlay = null;
      if (session.playing) pausePlayback();
      if (lookOf(segment).freeze !== null) {
        render();
        note("Đoạn dừng hình: chỉnh độ dài ở mục Giữ hình.");
        return;
      }
      // A reversed segment shows its end first: its left edge is the media's end.
      const reversed = lookOf(segment).reverse;
      const edge = reversed ? (side === "start" ? "end" : "start") : side;
      drag = { kind: "trim", id: segment.id, edge, side, sign: reversed ? -1 : 1, x: event.clientX, from: segment[edge], base: session.history.current, moved: false };
      render();
      return;
    }
    if (block) {
      drag = { kind: "select", id: block.dataset.id!, x: event.clientX };
      return;
    }
    drag = { kind: "seek" };
    seekOutput(snapped(timeAt(event.clientX), event, current(), [], [session.outputTime]));
  });

  content.addEventListener("pointermove", (event) => {
    if (!session || !drag) return;
    if (drag.kind === "seek") {
      seekOutput(snapped(timeAt(event.clientX), event, current()));
    } else if (drag.kind === "select" && Math.abs(event.clientX - drag.x) > DRAG_PX) {
      const base = session.history.current;
      if (base.segments.length < 2) {
        drag = { kind: "seek" };
        seekOutput(timeAt(event.clientX));
        return;
      }
      if (session.playing) pausePlayback();
      const { id, x } = drag;
      session.selected = id;
      session.overlay = null;
      drag = { kind: "reorder", id, x, base, to: base.segments.findIndex((segment) => segment.id === id) };
      content.classList.add("reordering");
      render();
    } else if (drag.kind === "reorder") {
      const reordering = drag;
      const { to, joint } = reorderTarget(reordering.base, reordering.id, timeAt(event.clientX));
      reordering.to = to;
      const from = reordering.base.segments.findIndex((segment) => segment.id === reordering.id);
      dropLine.hidden = to === from;
      dropLine.style.transform = `translateX(${PAD + joint * pps}px)`;
      const block = track.querySelector<HTMLElement>(`.tl-seg[data-id="${reordering.id}"]`);
      if (block) block.style.transform = `translate(${event.clientX - reordering.x}px, -6px)`;
    } else if (drag.kind === "marker") {
      const dragging = drag;
      if (!dragging.moved && Math.abs(event.clientX - dragging.x) <= 3) return;
      dragging.moved = true;
      const marker = dragging.base.markers.find((candidate) => candidate.id === dragging.id);
      if (!marker) return;
      const time = snapped(marker.at + (event.clientX - dragging.x) / pps, event, dragging.base, [session.outputTime], [marker.at]);
      session.draft = setMarker(dragging.base, dragging.id, { at: time });
      renderMarks(session.draft, outputDuration(session.draft));
    } else if (drag.kind === "trim") {
      const trimming = drag;
      const segment = trimming.base.segments.find((candidate) => candidate.id === trimming.id)!;
      let sourceTime = trimming.from + trimming.sign * ((event.clientX - trimming.x) / pps) * segment.speed;
      if (trimming.edge === "end" && trimming.sign === 1) {
        // The end edge moves on the output: snap it there, then back to the media's seconds.
        const index = trimming.base.segments.indexOf(segment);
        const start = outputStarts(trimming.base)[index];
        const edge = start + (sourceTime - segment.start) / segment.speed;
        const original = start + segmentLength(segment);
        const points = snapped(edge, event, trimming.base, [session.outputTime], [original]);
        sourceTime = segment.start + (points - start) * segment.speed;
      }
      const next = trim(trimming.base, trimming.id, trimming.edge, sourceTime);
      trimming.moved = true;
      session.draft = next;
      // Show the frame at the edge being moved.
      const index = next.segments.findIndex((candidate) => candidate.id === trimming.id);
      const moved = next.segments[index];
      const start = outputStarts(next)[index];
      seekOutput(trimming.side === "start" ? start : start + segmentLength(moved) - 0.04);
      renderPanel();
      renderTimeline();
    } else if (drag.kind === "ov-move" || drag.kind === "ov-trim") {
      const dragging = drag;
      if (!dragging.moved && Math.abs(event.clientX - dragging.x) <= 4) return;
      dragging.moved = true;
      const overlay = dragging.base.overlays.find((candidate) => candidate.id === dragging.id);
      if (!overlay) return;
      const shift = (event.clientX - dragging.x) / pps;
      let next: Edit;
      if (dragging.kind === "ov-move") {
        const length = overlayLength(dragging.base, overlay);
        let at = overlay.at + shift;
        const head = snapped(at, event, dragging.base, [session.outputTime], [overlay.at, overlay.at + length]);
        if (head !== at) at = head;
        else {
          const tail = snapped(at + length, event, dragging.base, [session.outputTime], [overlay.at, overlay.at + length]);
          at = tail - length;
        }
        next = setOverlayTime(dragging.base, dragging.id, { at: Math.max(0, at) });
      } else if (dragging.edge === "end") {
        next = setOverlayTime(dragging.base, dragging.id, { end: overlay.end + shift });
      } else {
        const start = clamp(overlay.start + shift, Math.max(0, overlay.start - overlay.at), overlay.end - MIN_SEGMENT);
        next = setOverlayTime(dragging.base, dragging.id, { start, at: overlay.at + (start - overlay.start) });
      }
      session.draft = next;
      const moved = next.overlays.find((candidate) => candidate.id === dragging.id)!;
      seekOutput(dragging.kind === "ov-trim" && dragging.edge === "end" ? moved.at + overlayLength(next, moved) - 0.04 : moved.at);
      renderPanel();
      renderTimeline();
    }
  });

  function endDrag(): void {
    if (!session || !drag) return;
    const ended = drag;
    drag = null;
    snapLine.hidden = true;
    dropLine.hidden = true;
    content.classList.remove("reordering");
    if (ended.kind === "reorder") {
      const next = moveSegment(ended.base, ended.id, ended.to);
      if (next !== ended.base) {
        commit(next, ended.id);
        const index = next.segments.findIndex((segment) => segment.id === ended.id);
        seekOutput(outputStarts(next)[index]);
        if (Object.keys(next.transitions).length < Object.keys(ended.base.transitions).length) note("Đã dời đoạn; chuyển cảnh ở các mối nối thay đổi đã được bỏ.");
      } else render();
      return;
    }
    if (ended.kind === "marker") {
      const next = session.draft;
      session.draft = null;
      const marker = ended.base.markers.find((candidate) => candidate.id === ended.id);
      if (next && ended.moved) commit(next);
      else if (marker) {
        seekOutput(marker.at);
        renderPlayhead(true);
      }
      return;
    }
    if (ended.kind === "trim" || ended.kind === "ov-move" || ended.kind === "ov-trim") {
      const next = session.draft;
      session.draft = null;
      if (next && ended.moved) commit(next, ended.kind === "trim" ? ended.id : undefined);
      else render();
    } else if (ended.kind === "select") {
      session.selected = ended.id;
      session.overlay = null;
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

  // ---------- Overlay boxes on the player: drag to move, corner to resize ----------

  type BoxDrag = { id: string; resize: boolean; x: number; y: number; base: Edit; moved: boolean; pointer: number };
  let boxDrag: BoxDrag | null = null;

  overlayLayer.addEventListener("pointerdown", (event) => {
    const target = event.target as HTMLElement;
    const box = target.closest<HTMLElement>(".edit-ov");
    if (!session || !box || event.button !== 0 || session.draft) return;
    event.preventDefault();
    event.stopPropagation();
    box.setPointerCapture(event.pointerId);
    session.overlay = box.dataset.id!;
    boxDrag = { id: box.dataset.id!, resize: !!target.closest(".edit-ov-handle"), x: event.clientX, y: event.clientY, base: session.history.current, moved: false, pointer: event.pointerId };
    render();
  });
  overlayLayer.addEventListener("pointermove", (event) => {
    if (!session || !boxDrag || event.pointerId !== boxDrag.pointer) return;
    const dragging = boxDrag;
    const overlay = dragging.base.overlays.find((candidate) => candidate.id === dragging.id);
    const rect = overlayLayer.getBoundingClientRect();
    if (!overlay || rect.width <= 0 || rect.height <= 0) return;
    const dx = (event.clientX - dragging.x) / rect.width, dy = (event.clientY - dragging.y) / rect.height;
    if (!dragging.moved && Math.abs(event.clientX - dragging.x) + Math.abs(event.clientY - dragging.y) < 3) return;
    dragging.moved = true;
    const key = keyOf(overlay), place = placementOf(overlay, key);
    session.draft = dragging.resize ? resizeOverlay(dragging.base, dragging.id, place.w + dx, key)
      : moveOverlay(dragging.base, dragging.id, place.x + dx, place.y + dy, key);
    renderEffects();
    renderPanel();
  });
  const endBoxDrag = (event: PointerEvent): void => {
    if (!session || !boxDrag || event.pointerId !== boxDrag.pointer) return;
    const ended = boxDrag;
    boxDrag = null;
    const next = session.draft;
    session.draft = null;
    if (next && ended.moved) commit(next);
    else render();
  };
  overlayLayer.addEventListener("pointerup", endBoxDrag);
  overlayLayer.addEventListener("pointercancel", endBoxDrag);

  // ---------- Transition popover ----------

  function openPopover(afterId: string, anchor: HTMLElement): void {
    if (!session) return;
    popFor = afterId;
    pop.hidden = false;
    renderPopover();
    const box = anchor.getBoundingClientRect();
    const width = pop.offsetWidth, height = pop.offsetHeight;
    const left = clamp(box.left + box.width / 2 - width / 2, 8, window.innerWidth - width - 8);
    const top = box.top - height - 8 >= 8 ? box.top - height - 8 : Math.min(window.innerHeight - height - 8, box.bottom + 8);
    pop.style.left = `${left}px`;
    pop.style.top = `${top}px`;
    for (const chip of track.querySelectorAll<HTMLElement>(".tl-joint")) chip.classList.toggle("open", chip.dataset.after === afterId);
    (popTypes.find((button) => button.getAttribute("aria-checked") === "true") ?? popTypes[0]).focus();
  }

  function closePopover(): void {
    if (pop.hidden) return;
    pop.hidden = true;
    const afterId = popFor;
    popFor = null;
    for (const chip of track.querySelectorAll<HTMLElement>(".tl-joint")) chip.classList.remove("open");
    const chip = track.querySelector<HTMLElement>(`.tl-joint[data-after="${afterId}"]`);
    if (chip && pop.contains(document.activeElement)) chip.focus();
  }

  function renderPopover(): void {
    if (!session || !popFor) return;
    const edit = current();
    const index = edit.segments.findIndex((segment) => segment.id === popFor);
    if (index < 0 || index + 1 >= edit.segments.length) {
      closePopover();
      return;
    }
    const stored = edit.transitions[popFor];
    const effective = transitionAt(edit, index);
    const room = transitionRoom(edit, index);
    const fits = room >= MIN_TRANSITION - 1e-9;
    const type = effective?.type ?? null;
    for (const button of popTypes) {
      button.setAttribute("aria-checked", String((button.dataset.type === "none" && !type) || button.dataset.type === type));
      button.disabled = button.dataset.type !== "none" && !fits;
    }
    const value = effective?.duration ?? stored?.duration ?? 0.5;
    popDuration.max = String(Math.max(MIN_TRANSITION, Math.floor(room * 10) / 10));
    popDuration.disabled = !effective;
    if (document.activeElement !== popDuration) popDuration.value = String(Math.min(value, Number(popDuration.max)));
    $("#tl-pop-value").textContent = seconds(value);
    $("#tl-pop-where").textContent = `Giữa đoạn ${index + 1} và ${index + 2} · ${formatClock(outputStarts(edit)[index + 1])}`;
    $("#tl-pop-note").textContent = fits ? "Xem trước chỉ gần đúng; video xuất ra sẽ chính xác." : "Hai đoạn kề nhau quá ngắn để thêm chuyển cảnh.";
  }

  for (const button of popTypes) {
    button.addEventListener("click", () => {
      if (!session || !popFor || session.draft) return;
      const type = button.dataset.type === "none" ? null : button.dataset.type as TransitionType;
      commit(setTransition(session.history.current, popFor, type, Number(popDuration.value) || 0.5));
    });
  }
  popDuration.addEventListener("input", () => {
    if (!session || !popFor) return;
    const stored = session.history.current.transitions[popFor];
    if (!stored) return;
    session.draft = setTransition(session.history.current, popFor, stored.type, Number(popDuration.value));
    renderTimeline();
    renderPopover();
    renderEffects();
  });
  popDuration.addEventListener("change", () => {
    if (session?.draft) commit(session.draft);
  });
  pop.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      event.preventDefault();
      closePopover();
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if (pop.hidden) return;
    const target = event.target as Element;
    if (!pop.contains(target) && !target.closest(".tl-joint")) closePopover();
  }, true);

  // ---------- Panel input ----------

  function setMode(next: Mode): void {
    mode = next;
    if (!session) return;
    if (!offered(mode)) mode = "cut";
    if (mode !== "cover") cover.leave();
    if (mode !== "audio") audio.select(null);
    if (mode !== "text") textMode.select(null);
    if (mode === "cover" && session.plainCover) {
      session.overlay = null;
      // The plain Phụ đề panel gives way; its burned lines still show over the picture.
      subs.leave();
      cover.enter();
      plainCoverFrame();
    } else if (mode === "subs" || mode === "cover") {
      session.overlay = null;
      subs.enter(mode);
      if (mode === "cover") cover.enter();
    } else {
      subs.leave();
      plainCoverFrame();
    }
    $("#cover-export-note").hidden = !session.plainCover;
    if (mode === "insert") {
      renderSources();
      void loadLibrary();
    }
    render();
  }
  for (const tab of modeTabs) {
    tab.addEventListener("click", () => {
      const next = tab.dataset.mode;
      setMode(next === "insert" || next === "subs" || next === "cover" || next === "audio" || next === "text" || next === "template" || next === "more" ? next : "cut");
    });
  }
  for (const button of whereButtons) {
    button.addEventListener("click", () => {
      where = button.dataset.where as Where;
      renderSources();
    });
  }
  for (const tab of sourceTabs) {
    tab.addEventListener("click", () => {
      source = tab.dataset.source === "computer" ? "computer" : "dubflow";
      renderSources();
      if (source === "dubflow") void loadLibrary();
    });
  }
  $("#insert-pick").addEventListener("click", () => void pickFiles());

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
    if (current().segments[session.index]?.id === segment.id) applySegment(playing, active);
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
  }
  // Overlay controls.
  for (const button of presetButtons) {
    button.addEventListener("click", () => {
      const overlay = selectedOverlay();
      if (session && overlay) commit(overlayPreset(session.history.current, overlay.id, button.dataset.preset as OverlayPreset, keyOf(overlay)));
    });
  }
  ovMotion.addEventListener("change", () => {
    const overlay = selectedOverlay();
    if (!session || !overlay) return;
    overlayKeyFor = overlay.id;
    overlayKey = ovMotion.checked ? "end" : "start";
    commit(setOverlayMotion(session.history.current, overlay.id, ovMotion.checked));
  });
  for (const button of keyButtons) {
    button.addEventListener("click", () => {
      const overlay = selectedOverlay();
      if (!session || !overlay?.to) return;
      overlayKeyFor = overlay.id;
      overlayKey = button.dataset.key as OverlayKey;
      // Show the key: the playhead goes to where the overlay starts or ends.
      const length = overlayLength(current(), overlay);
      seekOutput(overlayKey === "start" ? overlay.at : overlay.at + Math.max(0, length - 0.04));
      render();
    });
  }
  for (const [input, field] of [[ovFadeIn, "fadeIn"], [ovFadeOut, "fadeOut"]] as const) {
    input.addEventListener("input", () => {
      const overlay = selectedOverlay();
      if (!session || !overlay) return;
      session.draft = setOverlayFades(session.history.current, overlay.id, { [field]: Number(input.value) });
      renderPanel();
      renderEffects();
    });
    input.addEventListener("change", () => {
      if (session?.draft) commit(session.draft);
    });
  }
  ovSize.addEventListener("input", () => {
    const overlay = selectedOverlay();
    if (!session || !overlay) return;
    session.draft = resizeOverlay(session.history.current, overlay.id, Number(ovSize.value) / 100, keyOf(overlay));
    renderPanel();
    renderEffects();
  });
  ovVolume.addEventListener("input", () => {
    const overlay = selectedOverlay();
    if (!session || !overlay) return;
    session.draft = setOverlayVolume(session.history.current, overlay.id, Number(ovVolume.value) / 100);
    renderPanel();
    renderEffects();
  });
  const styleInputs = [[ovOpacity, (value: number) => ({ opacity: value / 100 })], [ovRadius, (value: number) => ({ radius: value / 100 })],
    [ovBorder, (value: number) => ({ border: value })]] as const;
  for (const [input, change] of styleInputs) {
    input.addEventListener("input", () => {
      const overlay = selectedOverlay();
      if (!session || !overlay) return;
      session.draft = setOverlayStyle(session.history.current, overlay.id, change(Number(input.value)));
      renderPanel();
      renderEffects();
    });
  }
  for (const button of shapeButtons) {
    button.addEventListener("click", () => {
      const overlay = selectedOverlay();
      if (session && overlay) commit(setOverlayStyle(session.history.current, overlay.id, { shape: button.dataset.shape === "ellipse" ? "ellipse" : "rect" }));
    });
  }
  for (const [id, step] of [["#ov-layer-up", 1], ["#ov-layer-down", -1]] as const) {
    $(id).addEventListener("click", () => {
      const overlay = selectedOverlay();
      if (session && overlay) commit(moveOverlayLayer(session.history.current, overlay.id, step));
    });
  }
  ovBorderColor.addEventListener("input", () => {
    const overlay = selectedOverlay();
    if (!session || !overlay) return;
    session.draft = setOverlayStyle(session.history.current, overlay.id, { borderColor: ovBorderColor.value });
    renderEffects();
  });
  for (const input of [ovSize, ovVolume, ovOpacity, ovRadius, ovBorder, ovBorderColor]) {
    input.addEventListener("change", () => {
      if (session?.draft) commit(session.draft);
    });
  }
  for (const [input, field] of [[ovAt, "at"], [ovStart, "start"], [ovEnd, "end"]] as const) {
    input.addEventListener("change", () => {
      const overlay = selectedOverlay();
      const value = parseClock(input.value);
      // A picture's "Dài" is how long it shows.
      const still = overlay && isStillPath(mediaOf(session!.history.current, overlay.mediaId)?.path ?? "");
      const times = still && field === "end" && value !== null ? { end: overlay.start + value } : { [field]: value };
      if (session && overlay && value !== null) commit(setOverlayTime(session.history.current, overlay.id, times));
      else render();
    });
  }
  for (const input of [startInput, endInput, ovAt, ovStart, ovEnd]) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
      if (event.key === "Escape") {
        input.blur();
        render();
      }
    });
  }
  $("#ov-delete").addEventListener("click", deleteSelected);
  $("#edit-split").addEventListener("click", splitAtPlayhead);
  $("#tl-split").addEventListener("click", splitAtPlayhead);
  $("#edit-delete").addEventListener("click", deleteSelected);
  $("#tl-delete").addEventListener("click", deleteSelected);
  undoButton.addEventListener("click", undo);
  redoButton.addEventListener("click", redo);
  $("#edit-exit").addEventListener("click", () => void close(true));
  $("#edit-revision-open").addEventListener("click", () => {
    if (!listJob) return;
    subs.pending.delete(listJob.id);
    revisionReady.hidden = true;
    mode = "subs";
    void open(jobTarget(listJob));
  });
  $("#edit-revision-dismiss").addEventListener("click", () => {
    if (listJob) subs.pending.delete(listJob.id);
    revisionReady.hidden = true;
  });
  openButton.addEventListener("click", () => { if (listJob) void open(jobTarget(listJob)); });
  $("#subs-full-review").addEventListener("click", () => host.openReview());
  exportButton.addEventListener("click", openDialog);

  document.addEventListener("keydown", (event) => {
    if (!session || screen.hidden || !dialog.hidden || document.querySelector(".confirm-backdrop, .review:not([hidden])")) return;
    const target = event.target;
    if (target instanceof Element && target.closest("input, textarea, select, [contenteditable='true'], [contenteditable='plaintext-only']")) return;
    const key = event.key.toLowerCase();
    const ctrl = event.ctrlKey || event.metaKey;
    if (mode === "cover" && !ctrl && !event.altKey && cover.keydown(event)) {
      event.preventDefault();
      return;
    }
    let handled = true;
    if (ctrl && !event.altKey && key === "z") {
      if (event.shiftKey) redo();
      else undo();
    } else if (ctrl && !event.altKey && key === "y") {
      redo();
    } else if (ctrl && !event.altKey && key === "b") {
      splitAtPlayhead();
    } else if (ctrl && !event.altKey && !event.shiftKey && key === "c" && cutModes()) {
      if (!copySelected()) handled = false;
    } else if (ctrl && !event.altKey && !event.shiftKey && key === "x" && cutModes()) {
      cutSelected();
    } else if (ctrl && !event.altKey && !event.shiftKey && key === "v" && cutModes()) {
      pasteAtPlayhead();
    } else if (ctrl && !event.altKey && !event.shiftKey && key === "d" && cutModes()) {
      duplicateSelected();
    } else if (ctrl || event.altKey) {
      handled = false;
    } else if (key === " ") {
      togglePlay();
    } else if (key === "k") {
      if (session.playing) pausePlayback();
    } else if (key === "l") {
      shuttleForward();
    } else if (key === "j") {
      if (session.playing) pausePlayback();
      step(-1);
    } else if (key === "," || key === ".") {
      stepFrames(key === "," ? -1 : 1);
    } else if (key === "arrowup" || key === "arrowdown") {
      jumpPoint(key === "arrowup" ? -1 : 1);
    } else if (key === "home" || key === "end") {
      seekOutput(key === "home" ? 0 : outputDuration(current()));
      renderPlayhead(true);
    } else if (key === "i" || key === "o") {
      setMark(key === "i" ? "in" : "out");
    } else if (key === "m") {
      addMarkerHere();
    } else if (key === "s") {
      splitAtPlayhead();
    } else if (key === "delete" || key === "backspace") {
      deleteSelected();
    } else if (key === "arrowleft" || key === "arrowright") {
      step((key === "arrowleft" ? -1 : 1) * (event.shiftKey ? 5 : 1));
    } else if (key === "escape") {
      if (!menu.hidden) closeMenu();
      else if (!pop.hidden) closePopover();
      else if (clearMarks()) { /* I–O cleared */ }
      else if (session.overlay) selectOverlay(null);
      else if (mode === "audio" && audio.selected) {
        audio.select(null);
        render();
      } else if (mode === "text" && textMode.selected) {
        textMode.select(null);
        render();
      } else void close(true);
    } else {
      handled = false;
    }
    if (handled) event.preventDefault();
  });

  /** Segment shortcuts (copy, paste, duplicate) belong to Cắt ghép and Chèn; other modes have their own selection. */
  function cutModes(): boolean {
    return mode === "cut" || mode === "insert";
  }

  content.addEventListener("contextmenu", (event) => {
    if (!session || session.draft) return;
    const block = (event.target as HTMLElement).closest<HTMLElement>(".tl-seg");
    if (!block) return;
    event.preventDefault();
    session.selected = block.dataset.id!;
    session.overlay = null;
    if (!cutModes()) setMode("cut");
    render();
    openMenu(event.clientX, event.clientY);
  });
  menu.addEventListener("click", (event) => {
    const item = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-action]");
    if (!item || item.disabled) return;
    closeMenu();
    const action = item.dataset.action;
    if (action === "copy") copySelected();
    else if (action === "cut") cutSelected();
    else if (action === "paste") pasteAtPlayhead();
    else if (action === "duplicate") duplicateSelected();
    else if (action === "split") splitAtPlayhead();
    else if (action === "delete") deleteSelected();
  });
  menu.addEventListener("keydown", (event) => {
    const items = [...menu.querySelectorAll<HTMLButtonElement>("button:not(:disabled)")];
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      items[(index + (event.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMenu();
    }
  });
  document.addEventListener("pointerdown", (event) => { if (!menu.hidden && !menu.contains(event.target as Node)) closeMenu(); }, true);
  window.addEventListener("blur", closeMenu);
  scroll.addEventListener("scroll", closeMenu);

  $("#edit-duplicate").addEventListener("click", duplicateSelected);
  $("#tl-marker").addEventListener("click", addMarkerHere);
  $("#markers-add").addEventListener("click", addMarkerHere);
  $("#range-remove").addEventListener("click", () => applyRange(false));
  $("#range-keep").addEventListener("click", () => applyRange(true));
  $("#range-clear").addEventListener("click", () => { clearMarks(); });
  snapButton.setAttribute("aria-pressed", String(snapping));
  snapButton.addEventListener("click", () => {
    snapping = !snapping;
    snapButton.setAttribute("aria-pressed", String(snapping));
    try { localStorage.setItem("dubflow.edit.snap", snapping ? "1" : "0"); } catch { /* per-viewer convenience only */ }
  });
  markerList.addEventListener("click", (event) => {
    if (!session || session.draft) return;
    const item = (event.target as HTMLElement).closest<HTMLElement>("[data-id]");
    const button = (event.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!item || !button) return;
    const marker = current().markers.find((candidate) => candidate.id === item.dataset.id);
    if (!marker) return;
    if (button.dataset.role === "remove") commit(removeMarker(session.history.current, marker.id));
    else {
      seekOutput(marker.at);
      renderPlayhead(true);
    }
  });
  markerList.addEventListener("change", (event) => {
    if (!session || session.draft) return;
    const input = event.target as HTMLInputElement;
    const id = input.closest<HTMLElement>("[data-id]")?.dataset.id;
    if (id) commit(setMarker(session.history.current, id, { label: input.value.trim() }));
  });
  markerList.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target instanceof HTMLInputElement) event.target.blur();
  });
  content.addEventListener("dblclick", (event) => {
    const flag = (event.target as HTMLElement).closest<HTMLElement>(".tl-marker");
    if (!flag || !session) return;
    if (mode !== "cut") setMode("cut");
    markerList.querySelector<HTMLInputElement>(`[data-id="${flag.dataset.id}"] input`)?.focus();
  });

  // ---------- Entering and leaving ----------

  async function open(target: EditTarget): Promise<boolean> {
    if (session || opening || !target.outputPath || (target.job && target.job.status.state !== "COMPLETED")) return false;
    const outputPath = target.outputPath;
    const ticket = ++openTicket;
    opening = true;
    openButton.disabled = true;
    try {
      const [loaded, status] = await Promise.all([
        invoke<LoadResponse>("edit_project_load", { outputPath, kind: target.kind }),
        invoke<StatusResponse>("edit_status", { outputPath, kind: target.kind }).catch(() => null),
      ]);
      // The queue selection moved on while a queue video was opening.
      if (ticket !== openTicket || (target.kind === "job" && listJob?.id !== target.id)) return false;
      const opened = resolveTarget(target, loaded.kind);
      const info = { path: outputPath, title: opened.displayName, width: loaded.width, height: loaded.height, fps: loaded.fps ?? 30, hasAudio: loaded.has_audio };
      const project = parseProject(loaded.project, loaded.duration, info);
      const edit = project?.edit ?? (opened.seed ?? ((fresh: Edit) => fresh))(createEdit(loaded.duration, info));
      const last = status?.exports.at(-1);
      // Exported in the background after the draft was saved: nothing is pending.
      const changes = last?.segments && matchesExport(edit, last) ? 0 : project?.changes ?? 0;
      const { project: _, kind: _kind, reviewable, ...facts } = loaded;
      if (status) listStatus = status;
      // A queue video goes on from where its player was; any other video starts at its beginning
      // (the shared player may still hold another video's time).
      const start = openingTime(opened.kind, edit, video.readyState >= 1 ? video.currentTime : null);
      if (!video.paused) video.pause();
      session = {
        target: opened, reviewable: opened.kind === "job" && !!opened.job && reviewable !== false, outputPath, facts, history: new EditHistory(edit, changes), draft: null,
        selected: project?.selected ?? outputToSource(edit, start)?.segment.id ?? edit.segments[0].id, overlay: null,
        outputTime: start, index: 0, playing: false, exporting: null, recent: project?.recent ?? [], plainCover: false, plainSubs: null, subsTouched: false, markIn: null, markOut: null, shuttle: 1,
      };
      session.plainCover = !session.reviewable;
      resetThumbs();
      active = video;
      library = [];
      zoom.value = "0";
      cover.begin();
      docuCover.begin(outputPath, opened.kind);
      docuLayers.begin(outputPath, opened.kind);
      if (session.plainCover) cover.loaded(plainCoverDocument(loaded.project), []);
      if (session.reviewable && opened.job) subs.begin(opened.job, outputPath);
      else {
        let remembered = null;
        try {
          remembered = storedStyle(window.localStorage.getItem(STYLE_STORAGE_KEY));
        } catch {
          // The remembered look is a convenience.
        }
        session.plainSubs = plainSubtitles(loaded.project, facts.cues, remembered);
        session.subsTouched = !!(loaded.project && typeof loaded.project === "object" && "subtitles" in loaded.project);
        subs.beginPlain(outputPath, session.plainSubs);
      }
      audio.begin();
      textMode.begin();
      templateMode.begin(opened);
      // A Bán hàng video opens on its template; modes it does not have fall back to Cắt ghép.
      if (opened.kind === "product") mode = "template";
      if (!offered(mode)) mode = "cut";
      $("#edit-save-state").textContent = "";
      $("#edit-hint").textContent = "";
      screen.classList.add("editing");
      host.refresh();
      seekOutput(start);
      render();
      if (mode === "insert") {
        renderSources();
        void loadLibrary();
      }
      if (mode === "subs" || mode === "cover") setMode(mode);
      $<HTMLButtonElement>("#edit-exit").focus();
      return true;
    } catch (error) {
      host.showError(`Không mở được chế độ chỉnh sửa: ${String(error).slice(0, 200)}`);
      return false;
    } finally {
      opening = false;
      openButton.disabled = false;
    }
  }

  /** Leave edit mode; `quiet` keeps the page (the next video opens right after). */
  let asking: Promise<boolean> | null = null;
  function close(ask: boolean, quiet = false): Promise<boolean> {
    // One "Thoát chỉnh sửa?" at a time: navigating again while it is open waits on the same answer.
    if (asking) return asking;
    const pending = closeNow(ask, quiet);
    if (!ask) return pending;
    asking = pending;
    void pending.finally(() => { if (asking === pending) asking = null; });
    return pending;
  }

  async function closeNow(ask: boolean, quiet: boolean): Promise<boolean> {
    if (!session) return true;
    if (ask && (unexported() || subs.dirty() || docuCover.dirty || docuLayers.busy || templateMode.dirty() || templateMode.busy)) {
      const leave = await host.confirm(templateMode.busy ? {
        title: "Thoát chỉnh sửa?", message: "Video đang được dựng lại theo mẫu mới. Bản mới vẫn được tạo và có trong Bán hàng › Video đã tạo, nhưng bản nháp chỉnh sửa không được chuyển sang.",
        confirmLabel: "Thoát", cancelLabel: "Ở lại", danger: true,
      } : subs.dirty() || docuCover.dirty || templateMode.dirty() ? {
        title: "Thoát chỉnh sửa?", message: templateMode.dirty() ? "Các thay đổi mẫu chưa bấm Áp dụng mẫu sẽ mất. Cắt ghép vẫn được lưu nháp."
          : "Các câu đã sửa, kiểu chữ và cách che chưa bấm Áp dụng sẽ mất. Cắt ghép vẫn được lưu nháp.",
        confirmLabel: "Thoát", cancelLabel: "Ở lại", danger: true,
      } : {
        title: "Thoát chỉnh sửa?", message: "Các thay đổi vẫn được lưu nháp; bạn có thể xuất sau.", confirmLabel: "Thoát", cancelLabel: "Ở lại",
      });
      if (!leave) return false;
    }
    if (!session) return true;
    const leaving = session.target;
    if (session.draft) session.draft = null;
    session.playing = false;
    await flush();
    cover.end();
    docuCover.end();
    docuLayers.end();
    $("#edit-subs-layer").classList.remove("plain-cover");
    $("#edit-subs-layer").hidden = true;
    subs.end();
    audio.end();
    textMode.end();
    templateMode.end();
    restorePlayer();
    timeline.style.height = "";
    session = null;
    drag = null;
    boxDrag = null;
    closePopover();
    dialog.hidden = true;
    if (!quiet) leave(leaving);
    return true;
  }

  /** The screen without edit mode after `leaving` closed. */
  function leave(leaving: EditTarget): void {
    if (session) return;
    screen.classList.remove("editing");
    // Kết quả follows the queue's selection again.
    if (leaving.kind !== "job") {
      listStatus = null;
      pollToken += 1;
      window.clearTimeout(pollTimer);
      if (listJob?.status.state === "COMPLETED" && listJob.outputPath) schedulePoll(0);
    }
    host.refresh();
    renderList();
    host.left(leaving);
  }

  // ---------- Export status, Kết quả list ----------

  function schedulePoll(delay: number): void {
    window.clearTimeout(pollTimer);
    const token = ++pollToken;
    pollTimer = window.setTimeout(() => void poll(token), delay);
  }

  /** Whose renders the status follows: the open video, else the queue's finished selection. */
  function subject(): { id: string; outputPath: string; kind: EditTarget["kind"] } | null {
    if (session) return { id: session.target.id, outputPath: session.outputPath, kind: session.target.kind };
    return listJob?.outputPath ? { id: listJob.id, outputPath: listJob.outputPath, kind: "job" } : null;
  }

  async function poll(token: number): Promise<void> {
    const job = subject();
    if (!job?.outputPath || token !== pollToken) return;
    const previous = listStatus?.status?.state;
    let response: StatusResponse;
    try {
      response = await invoke<StatusResponse>("edit_status", { outputPath: job.outputPath, kind: job.kind });
    } catch {
      if (token === pollToken && previous === "RUNNING") schedulePoll(2000);
      return;
    }
    if (token !== pollToken || subject()?.id !== job.id) return;
    listStatus = response;
    const state = response.status?.state;
    if (previous === "RUNNING" && state === "COMPLETED" && session?.target.id === job.id && session.exporting) {
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
    // The queue's Kết quả list; another page's video keeps its renders to itself.
    if (session && session.target.kind !== "job") return;
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
    if (session && session.target.kind === "job" && job?.id !== session.target.id) void close(false);
    const done = !!job && job.status.state === "COMPLETED" && !!job.outputPath;
    openButton.hidden = !done;
    // A version made by Phụ đề "Áp dụng" is offered for editing once it is ready.
    revisionReady.hidden = !(done && job && subs.pending.has(job.id));
    const same = listJob?.id === job?.id && listJob?.outputPath === job?.outputPath && listJob?.status.state === job?.status.state;
    listJob = job;
    // Editing another page's video: the status follows that video.
    if (same || (session && session.target.kind !== "job")) return;
    listStatus = null;
    pollToken += 1;
    window.clearTimeout(pollTimer);
    renderList();
    if (done) schedulePoll(0);
  }

  async function cancelExport(): Promise<void> {
    const job = subject();
    if (!job?.outputPath) return;
    try {
      await invoke("edit_cancel", { outputPath: job.outputPath, kind: job.kind });
    } catch (error) {
      host.showError(`Không hủy được: ${String(error).slice(0, 160)}`);
    }
    schedulePoll(300);
  }
  $("#edit-export-cancel").addEventListener("click", () => void cancelExport());

  // ---------- "Xuất video" dialog ----------

  function openDialog(): void {
    if (!session || exportButton.disabled) return;
    if (session.playing) pausePlayback();
    closePopover();
    const running = listStatus?.status?.state === "RUNNING";
    if (!running) {
      nameInput.value = defaultExportName(session.target.displayName, (listStatus?.exports ?? []).map((entry) => entry.path));
      folder = folderOf(session.outputPath);
      quality = "source";
      dialogDone = null;
      $("#export-error").hidden = true;
      let loud = false;
      try { loud = localStorage.getItem(LOUDNESS_KEY) === "1"; } catch { /* default off */ }
      $<HTMLInputElement>("#export-loudness").checked = loud;
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

  /** What "Xuất video" renders: the whole edit, or only its I–O range. */
  function exportedEdit(): Edit {
    const edit = current();
    const range = options.range === "marked" ? markedRange() : null;
    return (range && rangeEdit(edit, range.from, range.to)) || edit;
  }

  function renderOptions(): void {
    const group = (selector: string, key: string, value: string): void => {
      for (const button of document.querySelectorAll<HTMLButtonElement>(`${selector} [data-${key}]`)) {
        button.setAttribute("aria-checked", String(button.dataset[key] === value));
      }
    };
    group("#export-aspect", "aspect", options.aspect);
    group("#export-fill", "fill", options.fill);
    group("#export-fps", "fps", options.fps);
    group("#export-rate", "rate", options.rate);
    const range = markedRange();
    if (!range) options.range = "all";
    group("#export-range", "range", options.range);
    $<HTMLButtonElement>('#export-range [data-range="marked"]').disabled = !range;
    $("#export-range-hint").textContent = range
      ? `Đã đánh dấu ${formatClock(Math.min(range.from, range.to), true)} – ${formatClock(Math.max(range.from, range.to), true)}`
      : "Nhấn I và O trên dòng thời gian để đánh dấu một đoạn.";
    $("#export-fill").hidden = !options.aspect;
    $<HTMLInputElement>("#export-audio-only").checked = options.audioOnly;
    for (const id of ["#export-aspect", "#export-fill", "#export-fps", "#export-rate"]) {
      for (const button of document.querySelectorAll<HTMLButtonElement>(`${id} button`)) button.disabled = options.audioOnly;
    }
    const hint = $("#export-aspect-hint");
    hint.hidden = !options.aspect || options.audioOnly;
    hint.textContent = options.fill === "blur" ? "Giữ trọn hình, phần thừa là nền mờ của chính video." : "Giữ trọn hình, phần thừa là viền đen.";
    const chosen = [options.range === "marked" ? "đoạn I–O" : "", options.audioOnly ? "chỉ âm thanh" : options.aspect,
      options.audioOnly || !options.fps ? "" : `${options.fps} hình/giây`, options.audioOnly ? "" : options.rate === "low" ? "nhẹ" : options.rate === "high" ? "nét" : ""];
    $("#export-more-summary").textContent = chosen.filter(Boolean).join(" · ");
  }

  for (const [selector, key] of [["#export-aspect", "aspect"], ["#export-fill", "fill"], ["#export-fps", "fps"], ["#export-rate", "rate"], ["#export-range", "range"]] as const) {
    for (const button of document.querySelectorAll<HTMLButtonElement>(`${selector} [data-${key}]`)) {
      button.addEventListener("click", () => {
        (options as Record<string, unknown>)[key] = button.dataset[key] ?? "";
        renderDialog();
      });
    }
  }
  $<HTMLInputElement>("#export-audio-only").addEventListener("change", (event) => {
    options.audioOnly = (event.target as HTMLInputElement).checked;
    renderDialog();
  });

  function renderDialog(): void {
    if (dialog.hidden || !session) return;
    const status = listStatus?.status;
    const running = status?.state === "RUNNING";
    const done = !running && dialogDone !== null;
    const { facts } = session;
    const edit = exportedEdit();
    const total = outputDuration(edit);
    renderOptions();
    $("#export-form").toggleAttribute("disabled", running || done);
    $("#export-folder").textContent = folder;
    $("#export-folder").title = folder;
    for (const button of qualityButtons) button.setAttribute("aria-checked", String(button.dataset.quality === quality));
    const size = exportSize(facts.width, facts.height, quality);
    const upscale = size.height * size.width > facts.width * facts.height;
    $("#export-quality-hint").textContent = facts.width
      ? `${size.width}×${size.height}${quality === "source" ? " · giữ nguyên khung hình" : upscale ? " · phóng lên, file to hơn" : " · nhẹ, đăng nhanh"}`
      : "";
    const inserts = edit.segments.filter((segment) => segment.mediaId !== MAIN).length;
    const extras = $("#export-extras");
    const parts = [inserts ? `${inserts} đoạn chèn` : "", edit.overlays.length ? `${edit.overlays.length} lớp chồng` : ""].filter(Boolean);
    const sounds = edit.audio.length ? `${edit.audio.length} âm thanh thêm` : "";
    const level = edit.mainVolume !== 1 ? `âm thanh gốc ${Math.round(edit.mainVolume * 100)}%` : "";
    extras.textContent = [
      parts.length ? `Gồm ${parts.join(" · ")}, đưa về cùng khung hình và tốc độ khung hình của video chính` : "",
      sounds || level ? `${[sounds, level].filter(Boolean).join(", ")}; trộn và giới hạn để không bị rè` : "",
      edit.texts.length ? `${edit.texts.length} chữ được vẽ lên hình theo cỡ khung xuất` : "",
    ].filter(Boolean).join(". ");
    extras.hidden = !extras.textContent;
    const rate = options.rate === "low" ? 0.6 : options.rate === "high" ? 1.6 : 1;
    const bytes = options.audioOnly ? total * 24_000 : estimateExportBytes(facts.size_bytes, facts.duration, total, facts.width, facts.height, quality) * rate;
    $("#export-info").textContent = [`Dài ${formatClock(total)}`, megabytes(bytes)].filter(Boolean).join(" · ");
    $("#export-name").nextElementSibling!.textContent = options.audioOnly ? ".m4a" : ".mp4";
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
    const edit = exportedEdit();
    const whole = edit === current();
    const { outputPath, target: { kind } } = session;
    // A plain video's lines from Phụ đề, else its own captions, go in the .srt.
    const cues = remapCues(session.plainSubs ? exportLines(session.plainSubs) : session.facts.cues, edit).filter((cue) => cue.end_ms > cue.start_ms);
    const burn = session.plainSubs ? burnRequest(session.plainSubs) : null;
    const start = $<HTMLButtonElement>("#export-start");
    const error = $("#export-error");
    start.disabled = true;
    error.hidden = true;
    try {
      await flush();
      const normalize = $<HTMLInputElement>("#export-loudness").checked;
      try { localStorage.setItem(LOUDNESS_KEY, normalize ? "1" : "0"); } catch { /* the choice is only a convenience */ }
      await invoke("edit_export", { request: { outputPath, kind, quality, folder, fileName: nameInput.value.trim(), cues, ...exportPlan(edit), normalize,
        audioOnly: options.audioOnly, ...(options.audioOnly ? {} : {
          ...(options.fps ? { fps: Number(options.fps) } : {}), ...(options.rate ? { bitrate: options.rate } : {}),
          ...(options.aspect ? { aspect: options.aspect, fill: options.fill } : {}) }),
        ...(session.plainCover && cover.document() ? { cover: cover.document() } : {}), ...(burn ? { burn } : {}) } });
      if (session?.outputPath !== outputPath) return;
      // Only a whole video counts as the export of this edit (a range or its sound does not).
      session.exporting = whole && !options.audioOnly ? edit : null;
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
    get jobId() { return session?.target.job?.id ?? null; },
    get target() { return session?.target ?? null; },
    openTarget: async (target) => {
      if (session?.target.outputPath === target.outputPath) return true;
      const from = session?.target ?? null;
      if (session && !(await close(true, true))) return false;
      const opened = await open(target);
      if (!opened && from) leave(from);
      return opened;
    },
    close: (ask) => close(ask),
    select,
    timeLabel: () => session ? `${formatClock(session.outputTime)} / ${formatClock(outputDuration(current()))}` : null,
    isPlaying: () => session ? session.playing : null,
    togglePlay,
    clickPicture,
    flush,
    show(next) {
      if (!session || !offered(next)) return false;
      setMode(next);
      return true;
    },
    drop: (paths, point) => void dropFiles(paths, point),
  };
}

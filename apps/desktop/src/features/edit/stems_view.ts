/**
 * Lớp âm thanh in Âm thanh: the main video's sound in parts, each with its
 * own level — the Vietnamese dub, the original voice, the music and effects
 * — and Tách giọng, which separates a video's original voice from its music
 * when it has no separated parts yet. The preview plays the parts beside the
 * muted video, kept on its time (boost.ts lets them go past 100 %); the
 * export mixes the same files (`stem_weights` in the host).
 */
import { boostedAudio, fallbackAudio, isBoosted, setLevel, wake } from "./boost.ts";
import { ALL_STEM_KEYS, defaultStems, setStems, stemLevel, stemWeights, type Edit, type StemKey, type StemLevels } from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

type Separation = { state: string; progress?: number; message?: string };
/** `outdated`: the accompaniment came from an older model; Tách giọng may run again. */
/** `music`/`sfx`: the accompaniment split in two (Tách nhạc và SFX); `split` is that run's progress. */
type StemsResponse = {
  voice: string | null; source: string | null; background: string | null; separation: Separation | null; outdated?: boolean;
  music?: string | null; sfx?: string | null; split?: Separation | null;
  /** The original voice alone (source − accompaniment), only drawn on the timeline. */
  spoken?: string | null;
};
/** A part as a timeline lane: its file to draw and its level. */
export type StemLane = { key: StemKey; label: string; path: string; level: number };
type FileKey = "voice" | "source" | "background" | "music" | "sfx";

export type StemsContext = {
  /** "Thêm nhạc nền" / "Thêm SFX": the Âm thanh tools that add sounds. */
  addMusic: () => void;
  addSfx: () => void;
  edit: () => Edit;
  base: () => Edit;
  busy: () => boolean;
  commit: (next: Edit) => void;
  preview: (next: Edit | null) => void;
  note: (text: string) => void;
  mediaUrl: (path: string) => Promise<string | null>;
  /** The parts changed (found, separated): the player's sound must be set again. */
  changed: () => void;
};

export type StemsMode = {
  begin(outputPath: string, kind: string, hasAudio: boolean): void;
  end(): void;
  render(shown: boolean): void;
  /** Whether the preview plays the parts instead of the main video's own track. */
  active(edit: Edit): boolean;
  /** The parts as timeline lanes while Lớp âm thanh is on (none when off). */
  lanes(edit: Edit): StemLane[];
  /** A lane's level dragged on the timeline (`commit` false while dragging). */
  setLevel(key: StemKey, value: number, commit: boolean): void;
  /** Turn a part off, or back on at its level before. */
  toggle(key: StemKey): void;
  /** Keep the parts on `video`'s time at `level` (the segment's volume × Âm thanh gốc); `on` false pauses them. */
  sync(video: HTMLVideoElement, level: number, on: boolean, playing: boolean): void;
  pause(): void;
};

type Preset = { label: string; title: string; levels: StemLevels };

const POLL_MS = 1000;
const SEPARATE_NOTE = "Lần đầu tải bộ tách giọng khoảng 67 MB; video dài có thể mất vài phút. Bạn vẫn chỉnh sửa được trong lúc chờ.";

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing stems element: ${selector}`);
  return node;
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

const percent = (value: number): string => `${Math.round(value * 100)}%`;

export function mountStems(invoke: Invoke, context: StemsContext): StemsMode {
  const section = $("#au-stems");
  let outputPath = "";
  let kind = "";
  let hasAudio = false;
  let files: StemsResponse | null = null;
  let loading = false;
  let token = 0;
  let poll = 0;
  /** Bumped when Tách giọng rewrote the same files: their URLs change so nothing cached plays. */
  let revision = 0;
  let shown = false;
  /** Nghe riêng: one part alone in the preview (not saved). */
  let solo: StemKey | null = null;
  /** Each part's level before Tắt, to turn it back on. */
  const before = new Map<StemKey, number>();
  const players = new Map<FileKey, { element: HTMLAudioElement; path: string }>();

  const separated = (): boolean => !!files?.source && !!files.background;
  const split = (): boolean => separated() && !!files?.music && !!files.sfx;
  const hasVoice = (): boolean => !!files?.voice;
  const keys = (): StemKey[] => ALL_STEM_KEYS.filter((key) => key === "voice" ? hasVoice() : key === "background" ? separated() : key === "sfx" ? split() : !!files?.source);

  /** What the parts start at when first changed. */
  function initial(): StemLevels {
    return hasVoice() ? defaultStems(separated()) : { voice: 0, original: 1, background: 1 };
  }

  function label(key: StemKey): string {
    if (key === "voice") return "Giọng Việt (lồng tiếng)";
    if (key === "sfx") return "Tiếng động, hiệu ứng (SFX)";
    if (key === "background") return split() ? "Nhạc" : "Nhạc nền và tiếng động";
    if (!separated()) return "Âm gốc (giọng gốc lẫn nhạc nền)";
    return hasVoice() ? "Giọng gốc" : "Lời (giọng nói trong video)";
  }

  function presets(): Preset[] {
    const sep = separated();
    if (split()) {
      return hasVoice() ? [
        { label: "Lồng tiếng chuẩn", title: "Giọng Việt, nhạc và SFX 100%, tắt giọng gốc", levels: { voice: 1, original: 0, background: 1, sfx: 1 } },
        { label: "Giọng Việt rõ", title: "Giọng Việt 140%, nhạc 50%, SFX 80%, tắt giọng gốc", levels: { voice: 1.4, original: 0, background: 0.5, sfx: 0.8 } },
        { label: "Bỏ nhạc, giữ SFX", title: "Tắt nhạc (tránh bản quyền), giữ tiếng động", levels: { voice: 1, original: 0, background: 0, sfx: 1 } },
        { label: "Chỉ nhạc", title: "Tắt giọng và SFX", levels: { voice: 0, original: 0, background: 1, sfx: 0 } },
        { label: "Bản gốc", title: "Tắt giọng Việt, nghe như video gốc", levels: { voice: 0, original: 1, background: 1, sfx: 1 } },
      ] : [
        { label: "Bỏ lời", title: "Tắt lời, giữ nhạc và SFX", levels: { voice: 0, original: 0, background: 1, sfx: 1 } },
        { label: "Bỏ nhạc", title: "Tắt nhạc (tránh bản quyền), giữ lời và SFX", levels: { voice: 0, original: 1, background: 0, sfx: 1 } },
        { label: "Chỉ lời", title: "Tắt nhạc và SFX", levels: { voice: 0, original: 1, background: 0, sfx: 0 } },
        { label: "Chỉ SFX", title: "Tắt lời và nhạc", levels: { voice: 0, original: 0, background: 0, sfx: 1 } },
        { label: "Như gốc", title: "Lời, nhạc và SFX 100%", levels: { voice: 0, original: 1, background: 1, sfx: 1 } },
      ];
    }
    if (!hasVoice()) {
      return [
        { label: "Bỏ giọng, giữ nhạc", title: "Tắt giọng nói, giữ nhạc nền và tiếng động", levels: { voice: 0, original: 0, background: 1 } },
        { label: "Chỉ giọng nói", title: "Tắt nhạc nền và tiếng động", levels: { voice: 0, original: 1, background: 0 } },
        { label: "Giọng rõ, nhạc nhỏ", title: "Giọng nói 130%, nhạc nền 50%", levels: { voice: 0, original: 1.3, background: 0.5 } },
        { label: "Như gốc", title: "Giọng nói và nhạc nền 100%", levels: { voice: 0, original: 1, background: 1 } },
      ];
    }
    return sep ? [
      { label: "Lồng tiếng chuẩn", title: "Giọng Việt và nhạc nền 100%, tắt giọng gốc", levels: { voice: 1, original: 0, background: 1 } },
      { label: "Giọng Việt rõ", title: "Giọng Việt 140%, nhạc nền 60%, tắt giọng gốc", levels: { voice: 1.4, original: 0, background: 0.6 } },
      { label: "Giữ chút giọng gốc", title: "Giọng gốc 20% dưới giọng Việt", levels: { voice: 1, original: 0.2, background: 1 } },
      { label: "Chỉ giọng Việt", title: "Tắt giọng gốc và nhạc nền", levels: { voice: 1, original: 0, background: 0 } },
      { label: "Chỉ nhạc nền", title: "Tắt cả hai giọng, giữ nhạc và tiếng động", levels: { voice: 0, original: 0, background: 1 } },
      { label: "Bản gốc", title: "Tắt giọng Việt, nghe như video gốc", levels: { voice: 0, original: 1, background: 1 } },
    ] : [
      { label: "Lồng tiếng chuẩn", title: "Giọng Việt 100%, âm gốc 35%", levels: { voice: 1, original: 0.35, background: 1 } },
      { label: "Giọng Việt rõ", title: "Giọng Việt 140%, âm gốc 20%", levels: { voice: 1.4, original: 0.2, background: 1 } },
      { label: "Chỉ giọng Việt", title: "Tắt âm gốc (cả nhạc nền)", levels: { voice: 1, original: 0, background: 1 } },
      { label: "Bản gốc", title: "Tắt giọng Việt", levels: { voice: 0, original: 1, background: 1 } },
    ];
  }

  // ---------- Loading and Tách giọng ----------

  async function load(): Promise<void> {
    const mine = ++token;
    loading = true;
    renderPanel();
    try {
      const next = await invoke<StemsResponse>("edit_stems", { outputPath, kind });
      if (mine !== token) return;
      const wasSeparated = separated();
      const wasSplit = split();
      const wasOutdated = !!files?.outdated && files.separation?.state === "RUNNING";
      files = next;
      if (!wasSeparated && separated()) afterSeparation();
      else if (wasSeparated && !wasSplit && split()) context.note("Đã tách nhạc và tiếng động (SFX): chỉnh riêng từng lớp bên dưới.");
      else if (wasOutdated && separated() && !next.outdated) {
        // Same file names, new sound: the players read it again.
        revision += 1;
        for (const player of players.values()) release(player.element);
        players.clear();
        context.note("Đã lọc lại giọng gốc bằng bộ lọc mới: nhạc nền sạch hơn.");
      }
    } catch (error) {
      if (mine !== token) return;
      files = { voice: null, source: null, background: null, separation: { state: "FAILED", message: String(error).slice(0, 200) } };
    } finally {
      if (mine === token) loading = false;
    }
    if (mine !== token) return;
    clearTimeout(poll);
    if (files?.separation?.state === "RUNNING" || files?.split?.state === "RUNNING") poll = window.setTimeout(() => void load(), POLL_MS);
    for (const [key, player] of players) {
      if (files?.[key] === player.path) continue;
      release(player.element);
      players.delete(key);
    }
    renderPanel();
    context.changed();
  }

  /** The original sound just split in two: the parts on stay as they sounded, the original voice out of a dub. */
  function afterSeparation(): void {
    const edit = context.base();
    if (context.busy()) return;
    if (edit.mainStems) {
      const whole = edit.mainStems.original;
      context.commit(setStems(edit, { ...edit.mainStems, original: hasVoice() ? 0 : whole, background: hasVoice() ? Math.max(whole, edit.mainStems.background) : whole }));
    } else {
      context.commit(setStems(edit, initial()));
    }
    context.note(hasVoice() ? "Đã tách giọng gốc khỏi nhạc nền: giọng gốc đang tắt, nhạc nền giữ nguyên."
      : split() ? "Đã tách thành 3 lớp: lời, nhạc và tiếng động (SFX)." : "Đã tách giọng nói khỏi nhạc nền.");
  }

  async function splitMusic(): Promise<void> {
    try {
      await invoke("edit_split", { outputPath, kind });
      files = files ? { ...files, split: { state: "RUNNING", progress: 0, message: "Đang bắt đầu tách nhạc và tiếng động…" } } : files;
      renderPanel();
      clearTimeout(poll);
      poll = window.setTimeout(() => void load(), POLL_MS);
    } catch (error) {
      context.note(String(error).slice(0, 200));
    }
  }

  async function separate(): Promise<void> {
    try {
      await invoke("edit_separate", { outputPath, kind });
      files = files ? { ...files, separation: { state: "RUNNING", progress: 0, message: "Đang bắt đầu tách giọng…" } } : files;
      renderPanel();
      clearTimeout(poll);
      poll = window.setTimeout(() => void load(), POLL_MS);
    } catch (error) {
      context.note(String(error).slice(0, 200));
    }
  }

  // ---------- Panel ----------

  function levels(edit: Edit): StemLevels {
    return edit.mainStems ?? initial();
  }

  /** One part set; the music keeps SFX where it was (SFX follows the music only until it is set). */
  function withLevel(current: StemLevels, key: StemKey, value: number): StemLevels {
    const next = { ...current, [key]: value };
    if (split() && key === "background" && current.sfx === undefined) next.sfx = current.background;
    return next;
  }

  function apply(next: StemLevels, commit: boolean): void {
    const edit = setStems(context.base(), next);
    if (commit) {
      context.preview(null);
      context.commit(edit);
    } else {
      context.preview(edit);
    }
  }

  /** The panel's controls, drawn again only when its shape changes (a drag keeps its slider). */
  let shape = "";
  const rows = new Map<StemKey, { input: HTMLInputElement; out: HTMLOutputElement; mute: HTMLButtonElement }>();
  const chips: Array<{ chip: HTMLButtonElement; levels: StemLevels }> = [];
  let progressFill: HTMLElement | null = null;
  let progressText: HTMLElement | null = null;

  function renderPanel(): void {
    section.hidden = !shown || (!hasAudio && !files?.source);
    if (section.hidden) {
      shape = "";
      return;
    }
    const edit = context.edit();
    const parts = keys();
    const running = files?.separation?.state === "RUNNING";
    const splitting = files?.split?.state === "RUNNING";
    const next = JSON.stringify([parts, !!edit.mainStems, running, splitting, loading && !files, files?.separation?.state, files?.separation?.message,
      files?.split?.state, solo, hasAudio]);
    if (next === shape) {
      update(edit);
      return;
    }
    shape = next;
    rows.clear();
    chips.length = 0;
    progressFill = progressText = null;
    const head = element("div", "au-stems-head");
    head.append(element("h3", "edit-segment-title", "Lớp âm thanh của video"));
    if (edit.mainStems && parts.length) {
      const reset = element("button", "btn ghost small", "Đặt lại");
      reset.type = "button";
      reset.title = "Bỏ chỉnh riêng từng lớp, phát âm thanh có sẵn của video";
      reset.addEventListener("click", () => {
        if (context.busy()) return;
        solo = null;
        context.commit(setStems(context.base(), null));
      });
      head.append(reset);
    }
    const body: HTMLElement[] = [head];
    if (loading && !files) {
      body.push(element("p", "edit-note", "Đang đọc các lớp âm thanh…"));
    } else if (parts.length) {
      body.push(element("p", "edit-note", edit.mainStems
        ? "Chỉnh riêng từng lớp, 0–200%. Âm lượng cả video bên dưới áp dụng lên tổng."
        : "Đang phát âm thanh có sẵn của video. Kéo một thanh hoặc chọn kiểu bên dưới để chỉnh riêng từng lớp."));
      const current = levels(edit);
      for (const key of parts) body.push(row(key, stemLevel(current, key), !!edit.mainStems));
      const chipRow = element("div", "au-stems-presets");
      for (const preset of presets()) {
        const chip = element("button", "btn chip", preset.label);
        chip.type = "button";
        chip.title = preset.title;
        const on = !!edit.mainStems && parts.every((key) => stemLevel(edit.mainStems!, key) === stemLevel(preset.levels, key));
        chip.setAttribute("aria-pressed", String(on));
        chip.addEventListener("click", () => { if (!context.busy()) apply(preset.levels, true); });
        chipRow.append(chip);
        chips.push({ chip, levels: preset.levels });
      }
      body.push(chipRow);
    }
    if (separated() && !split() && hasAudio) {
      // Older separations (and dubbing jobs) have one accompaniment: split it into music and SFX on request.
      const box = element("div", "au-stems-separate");
      if (splitting) {
        const bar = element("div", "au-stems-track");
        const fill = element("div", "au-stems-fill");
        fill.style.width = `${Math.max(2, Math.min(100, Number(files?.split?.progress) || 0))}%`;
        bar.append(fill);
        progressFill = fill;
        progressText = element("small", "edit-note", files?.split?.message || "Đang tách nhạc và tiếng động…");
        box.append(progressText, bar);
      } else {
        const button = element("button", "btn", "Tách nhạc và tiếng động (SFX)");
        button.type = "button";
        button.disabled = loading;
        button.addEventListener("click", () => void splitMusic());
        box.append(button, element("small", "edit-note", "Tách nhạc nền thành 2 lớp: nhạc (giai điệu) và tiếng động, hiệu ứng (SFX). Không cần tải thêm, vài giây cho mỗi phút video."));
        if (files?.split?.state === "FAILED" && files.split.message) box.append(element("small", "edit-note error", files.split.message));
      }
      body.push(box);
    }
    if (split() && edit.mainStems) {
      body.push(element("small", "edit-note", "Trống, nhịp trong bài nhạc thường nằm ở lớp SFX. Lớp được tách tự động nên có thể còn lẫn một chút."));
    }
    if (parts.length) {
      const add = element("div", "au-stems-add");
      const music = element("button", "btn ghost small", "＋ Thêm nhạc nền");
      music.type = "button";
      music.title = "Chọn file nhạc trên máy, thêm tại đầu phát (bật “Lặp lại đến hết video” để làm nhạc nền)";
      music.addEventListener("click", () => context.addMusic());
      const sfx = element("button", "btn ghost small", "＋ Thêm SFX");
      sfx.type = "button";
      sfx.title = "Chọn hiệu ứng âm thanh trong Kho DubFlow";
      sfx.addEventListener("click", () => context.addSfx());
      add.append(music, sfx);
      body.push(add);
    }
    if ((!separated() || files?.outdated) && hasAudio) {
      const box = element("div", "au-stems-separate");
      const status = files?.separation;
      if (running) {
        const bar = element("div", "au-stems-track");
        const fill = element("div", "au-stems-fill");
        fill.style.width = `${Math.max(2, Math.min(100, Number(status?.progress) || 0))}%`;
        bar.append(fill);
        progressFill = fill;
        const cancel = element("button", "btn ghost small", "Hủy");
        cancel.type = "button";
        cancel.addEventListener("click", () => void invoke("edit_separate_cancel", { outputPath, kind }).catch(() => undefined));
        progressText = element("small", "edit-note", status?.message || "Đang tách giọng…");
        box.append(progressText, bar, cancel);
      } else if (separated()) {
        const button = element("button", "btn", "Lọc lại giọng gốc (bộ lọc mới)");
        button.type = "button";
        button.disabled = loading;
        button.addEventListener("click", () => void separate());
        box.append(button, element("small", "edit-note",
          `Nhạc nền của video này được tách bằng bộ lọc cũ nên còn sót giọng gốc và tiếng rè. Lọc lại để nhạc nền sạch hơn. ${SEPARATE_NOTE}`));
        if (status?.state === "FAILED" && status.message) box.append(element("small", "edit-note error", status.message));
      } else {
        const button = element("button", "btn", hasVoice() ? "Tách giọng gốc khỏi nhạc nền" : "Tách lời, nhạc và SFX");
        button.type = "button";
        button.disabled = loading;
        button.addEventListener("click", () => void separate());
        box.append(button, element("small", "edit-note", hasVoice()
          ? `Video này chưa có nhạc nền tách riêng nên giọng gốc còn lẫn trong âm gốc. ${SEPARATE_NOTE}`
          : `Tách âm thanh thành 3 lớp: lời, nhạc và tiếng động (SFX), rồi chỉnh riêng từng lớp. ${SEPARATE_NOTE}`));
        if (status?.state === "FAILED" && status.message) box.append(element("small", "edit-note error", status.message));
      }
      body.push(box);
    }
    section.replaceChildren(...body);
    update(edit);
  }

  /** Values only: levels, Tắt/Bật, the chosen kiểu and the separation's progress. */
  function update(edit: Edit): void {
    const current = levels(edit);
    for (const [key, { input, out, mute }] of rows) {
      const value = stemLevel(current, key);
      if (document.activeElement !== input) input.value = String(Math.round(value * 100));
      out.textContent = percent(value);
      mute.textContent = value === 0 ? "Bật" : "Tắt";
      mute.title = value === 0 ? "Bật lại lớp này" : "Tắt lớp này";
    }
    const parts = keys();
    for (const { chip, levels: preset } of chips) {
      const on = !!edit.mainStems && parts.every((key) => stemLevel(edit.mainStems!, key) === stemLevel(preset, key));
      chip.setAttribute("aria-pressed", String(on));
    }
    const status = files?.separation?.state === "RUNNING" ? files.separation : files?.split?.state === "RUNNING" ? files.split : files?.separation;
    if (progressFill) progressFill.style.width = `${Math.max(2, Math.min(100, Number(status?.progress) || 0))}%`;
    if (progressText) progressText.textContent = status?.message || "Đang tách giọng…";
  }

  function row(key: StemKey, value: number, on: boolean): HTMLElement {
    const wrap = element("div", "au-stem");
    const name = element("span", "edit-label");
    const out = element("output", "", percent(value));
    name.append(label(key), " ", out);
    const input = element("input");
    input.type = "range";
    input.min = "0";
    input.max = "200";
    input.step = "5";
    input.value = String(Math.round(value * 100));
    input.setAttribute("aria-label", label(key));
    input.classList.toggle("off", !on);
    input.addEventListener("input", () => {
      if (!shown) return;
      out.textContent = percent(Number(input.value) / 100);
      apply(withLevel(levels(context.base()), key, Number(input.value) / 100), false);
    });
    input.addEventListener("change", () => {
      if (!shown) return;
      apply(withLevel(levels(context.base()), key, Number(input.value) / 100), true);
    });
    const mute = element("button", "btn ghost small au-stem-mute", value === 0 ? "Bật" : "Tắt");
    mute.type = "button";
    mute.title = value === 0 ? "Bật lại lớp này" : "Tắt lớp này";
    mute.addEventListener("click", () => {
      const current = levels(context.base());
      const value = stemLevel(current, key);
      if (value > 0) before.set(key, value);
      apply(withLevel(current, key, value > 0 ? 0 : before.get(key) ?? 1), true);
    });
    const alone = element("button", "btn ghost small au-stem-solo", "Nghe riêng");
    alone.type = "button";
    alone.title = "Chỉ nghe lớp này khi xem thử (không đổi video)";
    alone.setAttribute("aria-pressed", String(solo === key));
    alone.addEventListener("click", () => {
      solo = solo === key ? null : key;
      if (solo && !context.base().mainStems) apply(levels(context.base()), true);
      renderPanel();
      context.changed();
    });
    const controls = element("div", "au-stem-controls");
    controls.append(input, mute, alone);
    rows.set(key, { input, out, mute });
    wrap.append(name, controls);
    return wrap;
  }

  // ---------- Preview ----------

  function release(element: HTMLAudioElement): void {
    element.pause();
    element.removeAttribute("src");
    element.load();
  }

  function playerFor(key: FileKey, path: string): HTMLAudioElement {
    const existing = players.get(key);
    if (existing && existing.path === path) return existing.element;
    if (existing) release(existing.element);
    const player = { element: boostedAudio(), path };
    players.set(key, player);
    const load = (target: HTMLAudioElement): void => {
      void context.mediaUrl(path).then((url) => {
        if (url && revision) url += `${url.includes("?") ? "&" : "?"}r=${revision}`;
        if (url && players.get(key) === player && player.element === target) target.src = url;
      }).catch(() => undefined);
    };
    // A load the gain cannot hear (no CORS) plays plainly instead, up to 100 %.
    player.element.addEventListener("error", () => {
      if (!isBoosted(player.element) || players.get(key) !== player || !player.element.getAttribute("src")) return;
      release(player.element);
      player.element = fallbackAudio();
      load(player.element);
    });
    load(player.element);
    return player.element;
  }

  function pause(): void {
    for (const player of players.values()) if (!player.element.paused) player.element.pause();
  }

  return {
    begin(path, videoKind, audio) {
      outputPath = path;
      kind = videoKind;
      hasAudio = audio;
      files = null;
      solo = null;
      before.clear();
      void load();
    },
    end() {
      token += 1;
      clearTimeout(poll);
      for (const player of players.values()) release(player.element);
      players.clear();
      files = null;
      shown = false;
      section.hidden = true;
    },
    render(show) {
      shown = show;
      renderPanel();
    },
    active(edit) {
      return !!edit.mainStems && !!files?.source;
    },
    lanes(edit) {
      const current = edit.mainStems;
      if (!current || !files?.source) return [];
      const lanes: Array<{ key: StemKey; label: string; path: string | null | undefined }> = [];
      if (hasVoice()) lanes.push({ key: "voice", label: "Giọng Việt", path: files.voice });
      if (!separated()) lanes.push({ key: "original", label: "Âm gốc", path: files.source });
      else {
        lanes.push({ key: "original", label: hasVoice() ? "Giọng gốc" : "Lời", path: files.spoken || files.source });
        if (split()) lanes.push({ key: "background", label: "Nhạc", path: files.music }, { key: "sfx", label: "SFX", path: files.sfx });
        else lanes.push({ key: "background", label: "Nhạc nền", path: files.background });
      }
      return lanes.filter((lane): lane is { key: StemKey; label: string; path: string } => !!lane.path)
        .map((lane) => ({ ...lane, level: stemLevel(current, lane.key) }));
    },
    setLevel(key, value, commit) {
      if (context.busy() && commit === false && !context.edit().mainStems) return;
      apply(withLevel(levels(context.base()), key, Math.min(2, Math.max(0, value))), commit);
      update(context.edit());
    },
    toggle(key) {
      if (context.busy()) return;
      const current = levels(context.base());
      const value = stemLevel(current, key);
      if (value > 0) before.set(key, value);
      apply(withLevel(current, key, value > 0 ? 0 : before.get(key) ?? 1), true);
    },
    sync(video, level, on, playing) {
      const edit = context.edit();
      if (!on || !edit.mainStems || !files?.source) {
        pause();
        return;
      }
      const heard = solo ? Object.fromEntries(ALL_STEM_KEYS.map((key) => [key, key === solo ? stemLevel(edit.mainStems!, key) : 0])) as StemLevels : edit.mainStems;
      const weights = stemWeights(heard, separated(), split());
      if (playing) wake();
      for (const key of ["voice", "source", "background", "music", "sfx"] as const) {
        const path = files[key];
        if (path && weights[key] === 0 && (key === "music" || key === "sfx" || key === "background")) {
          // A silent part does not need to play (the split parts are big files).
          const idle = players.get(key);
          if (idle && !idle.element.paused) idle.element.pause();
          continue;
        }
        if (!path) continue;
        const player = playerFor(key, path);
        setLevel(player, weights[key] * level);
        player.playbackRate = video.playbackRate;
        if (player.readyState >= 1 && Math.abs(player.currentTime - video.currentTime) > (playing ? 0.25 : 0.04)) player.currentTime = video.currentTime;
        if (playing && player.paused && player.readyState >= 2) void player.play().catch(() => undefined);
        else if (!playing && !player.paused) player.pause();
      }
    },
    pause,
  };
}

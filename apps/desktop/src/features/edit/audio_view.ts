/**
 * The Âm thanh mode of the editor (Issue #420): the volume of the original
 * sound, the sound library (Kho DubFlow, the user's folder, Gợi ý AI), the
 * selected clip's controls, the sound row of the timeline (move, trim,
 * pending AI suggestions) and the live preview of the clips, one <audio>
 * per clip scheduled by output time.
 */
import { PROVIDERS, type AiSession } from "../ai/provider.ts";
import { CATALOG } from "../sfx/bundled.ts";
import { CATEGORIES, searchSounds, soundLength, type Category, type Sound } from "../sfx/catalog.ts";
import { suggestEffects, type Suggestion, type TimedLine } from "../sfx/ai.ts";
import { packSummary, sizeText, type AudioFacts, type SfxLibrary, type UserAudio } from "../sfx/library.ts";
import {
  addAudio, audioGain, audioLanes, audioLength, audioPosition, DEFAULT_DUCK_DB, MAX_LOOP_PART, formatClock, MAX_AUDIO, MIN_AUDIO, outputDuration, parseClock,
  removeAudio, setAudioProps, setAudioTime, setMainVolume, type AudioClip, type AudioInfo, type Edit,
} from "./model.ts";

/** The catalog's category ids as shown ("Bấm/Pop" stays the id in the sound pack). */
const categoryName = (category: string): string => category === "Bấm/Pop" ? "Bấm, tách" : category;

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type AudioHost = {
  sfx: SfxLibrary;
  ai: AiSession;
  openAiSettings: () => void;
  mediaUrl: (path: string) => Promise<string | null>;
  showError: (text: string) => void;
};

export type AudioContext = {
  /** The edit on screen (a drag's draft included). */
  edit: () => Edit;
  /** The committed edit operations start from. */
  base: () => Edit;
  /** Another change is being dragged. */
  busy: () => boolean;
  commit: (next: Edit) => void;
  /** Show an uncommitted change (null drops it). */
  preview: (next: Edit | null) => void;
  outputTime: () => number;
  playing: () => boolean;
  seek: (time: number) => void;
  /** Pixels per second and the output time under a client x. */
  pps: () => number;
  timeAt: (clientX: number) => number;
  /** The dubbed lines on the edited timeline. */
  cues: () => TimedLine[];
  /** Open the Âm thanh panel. */
  enterMode: () => void;
  note: (text: string) => void;
  /** The timeline must be drawn again (suggestions changed). */
  redraw: () => void;
};

export type AudioMode = {
  begin(): void;
  end(): void;
  /** Draw the panel (when shown). */
  render(shown: boolean): void;
  /** Draw the sound row; its height in pixels (0 when hidden). */
  renderTrack(row: HTMLElement, shown: boolean, pad: number): number;
  /** Keep the clips in step with the output at `time`. */
  frame(time: number, playing: boolean): void;
  pause(): void;
  /** The selected clip, if any. */
  readonly selected: string | null;
  select(id: string | null): void;
  /** Delete the selected clip; false when none is selected. */
  deleteSelected(): boolean;
  /** Audio files dropped (or picked) to play from output time `at`, one after another. */
  drop(paths: string[], at: number): Promise<void>;
};

type Tab = "library" | "mine" | "ai";
type Pending = Suggestion & { key: string };
type Player = { element: HTMLAudioElement; key: string };

/** Short sounds are drawn this wide at least, so they can be clicked and their edges dragged. */
const MIN_BLOCK_PX = 22;
const LANE_PX = 20;
const MAX_LANES = 4;
const MINE_STEP = 200;
const PLAY_ICON = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z" fill="currentColor"/></svg>';
const STOP_ICON = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor"/></svg>';
const OVER_100 = "Trên 100% chỉ nghe được sau khi xuất video.";

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing audio element: ${selector}`);
  return node;
};

function element<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));
const fileName = (path: string): string => path.split(/[\\/]/).pop() ?? path;
const stem = (path: string): string => fileName(path).replace(/\.[^.]*$/, "");
const percent = (value: number): string => `${Math.round(value * 100)}%`;
const fadeText = (value: number): string => value > 0 ? `${String(value).replace(".", ",")} giây` : "Tắt";
const BY_ID = new Map(CATALOG.sounds.map((sound) => [sound.id, sound]));

export function mountAudio(invoke: Invoke, host: AudioHost, context: AudioContext): AudioMode {
  const section = $("#edit-audio");
  const mainInput = $<HTMLInputElement>("#au-main");
  const selectedBox = $("#au-selected");
  const atInput = $<HTMLInputElement>("#au-at");
  const startInput = $<HTMLInputElement>("#au-start");
  const endInput = $<HTMLInputElement>("#au-end");
  const volumeInput = $<HTMLInputElement>("#au-volume");
  const fadeInInput = $<HTMLInputElement>("#au-fade-in");
  const fadeOutInput = $<HTMLInputElement>("#au-fade-out");
  const duckToggle = $<HTMLInputElement>("#au-duck");
  const loopToggle = $<HTMLInputElement>("#au-loop");
  const duckInput = $<HTMLInputElement>("#au-duck-db");
  const tabs = [...document.querySelectorAll<HTMLButtonElement>("#au-tabs [data-au-tab]")];
  const search = $<HTMLInputElement>("#au-search");
  const cats = $("#au-cats");
  const soundsList = $("#au-sounds");
  const filesList = $("#au-files");
  const aiList = $("#au-ai-list");
  const aiRun = $<HTMLButtonElement>("#au-ai-run");
  const aiState = $("#au-ai-state");

  let active = false;
  let shown = false;
  let tab: Tab = "library";
  let category: Category | null = null;
  let selected: string | null = null;
  let pending: Pending[] = [];
  let focusKey: string | null = null;
  let aiBusy = false;
  let aiToken = 0;
  let files: UserAudio[] | null = null;
  let filesFolder: string | null = null;
  let filesError = "";
  let filesLimit = MINE_STEP;
  let filesToken = 0;
  const facts = new Map<string, AudioFacts | null>();
  const factQueue: string[] = [];
  let factWorkers = 0;
  const players = new Map<string, Player>();
  // "Nghe thử": one sound at a time.
  const listen = new Audio();
  listen.preload = "auto";
  let listening: string | null = null;
  let packBusy = false;

  // ---------- Sources ----------

  const sourceKey = (clip: Pick<AudioClip, "source">): string => clip.source.kind === "sfx" ? `sfx:${clip.source.id}` : `file:${clip.source.path}`;

  async function sourceUrl(key: string): Promise<string | null> {
    const path = key.startsWith("sfx:") ? await host.sfx.soundPath(key.slice(4)) : key.slice(5);
    return path ? host.mediaUrl(path).catch(() => null) : null;
  }

  /** Sound effects need the pack: download it first (once). */
  async function ready(): Promise<boolean> {
    if (host.sfx.status()?.installed) return true;
    packBusy = true;
    renderPack();
    const installed = await host.sfx.install();
    packBusy = false;
    renderPack();
    if (!installed) context.note("Chưa tải được kho hiệu ứng. Kiểm tra mạng rồi thử lại.");
    return installed;
  }

  function soundInfo(sound: Sound): AudioInfo {
    return { source: { kind: "sfx", id: sound.id }, title: sound.name_vi, duration: sound.duration_ms / 1000 };
  }

  /** Add sounds at their times as one step; selects the last one added. */
  function addAll(items: Array<{ info: AudioInfo; at: number }>): number {
    if (!items.length || context.busy()) return 0;
    let next = context.base();
    let added = 0;
    let last: string | null = null;
    for (const { info, at } of items) {
      const after = addAudio(next, info, at);
      if (after === next) continue;
      last = after.audio[after.audio.length - 1].id;
      next = after;
      added += 1;
    }
    if (!added) {
      context.note(context.base().audio.length >= MAX_AUDIO ? "Đã đạt tối đa 100 âm thanh." : "Không thêm được âm thanh này.");
      return 0;
    }
    if (added < items.length) context.note(`Đã thêm ${added}/${items.length} âm thanh (tối đa ${MAX_AUDIO}).`);
    selected = last;
    context.commit(next);
    return added;
  }

  async function addSound(sound: Sound, at = context.outputTime()): Promise<void> {
    if (!(await ready())) return;
    addAll([{ info: soundInfo(sound), at }]);
  }

  /** Length and title of a user's audio file, probed once. */
  function audioFacts(path: string): Promise<AudioFacts | null> {
    if (facts.has(path)) return Promise.resolve(facts.get(path) ?? null);
    return invoke<AudioFacts>("audio_info", { path }).then((info) => {
      facts.set(path, info);
      return info;
    }).catch((error) => {
      facts.set(path, null);
      host.showError(`Không đọc được âm thanh ${fileName(path)}: ${String(error).slice(0, 160)}`);
      return null;
    });
  }

  async function addFiles(paths: string[], at: number): Promise<void> {
    const items: Array<{ info: AudioInfo; at: number }> = [];
    let time = at;
    for (const path of paths) {
      const info = await audioFacts(path);
      if (!info || !(info.duration >= MIN_AUDIO)) continue;
      items.push({ info: { source: { kind: "file", path }, title: info.title || stem(path), duration: info.duration }, at: time });
      time += info.duration;
    }
    if (items.length) addAll(items);
    else context.note("Không đọc được file âm thanh nào.");
  }

  // ---------- Nghe thử ----------

  function stopListening(): void {
    listening = null;
    listen.pause();
    renderListenButtons();
  }

  async function toggleListen(key: string): Promise<void> {
    if (listening === key) {
      stopListening();
      return;
    }
    if (key.startsWith("sfx:") && !(await ready())) return;
    listening = key;
    renderListenButtons();
    const url = await sourceUrl(key);
    if (listening !== key) return;
    if (!url) {
      listening = null;
      renderListenButtons();
      context.note("Không phát được âm thanh này trong bản xem thử.");
      return;
    }
    listen.src = url;
    listen.currentTime = 0;
    listen.volume = 1;
    void listen.play().catch(() => { if (listening === key) stopListening(); });
  }
  listen.addEventListener("ended", stopListening);

  function listenButton(key: string, label: string): HTMLButtonElement {
    const button = element("button", "icon-btn au-listen");
    button.type = "button";
    button.dataset.listen = key;
    button.setAttribute("aria-label", `Nghe thử: ${label}`);
    button.title = "Nghe thử";
    button.innerHTML = listening === key ? STOP_ICON : PLAY_ICON;
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      void toggleListen(key);
    });
    return button;
  }

  function renderListenButtons(): void {
    for (const button of section.querySelectorAll<HTMLButtonElement>(".au-listen[data-listen]")) {
      button.innerHTML = button.dataset.listen === listening ? STOP_ICON : PLAY_ICON;
      button.classList.toggle("on", button.dataset.listen === listening);
    }
    const play = $("#au-play");
    play.textContent = play.dataset.listen && play.dataset.listen === listening ? "Dừng nghe thử" : "Nghe thử";
  }

  // ---------- Panel ----------

  function selectedClip(): AudioClip | undefined {
    return selected ? context.edit().audio.find((clip) => clip.id === selected) : undefined;
  }

  function renderMain(edit: Edit): void {
    if (document.activeElement !== mainInput) mainInput.value = String(Math.round(edit.mainVolume * 100));
    $("#au-main-value").textContent = percent(edit.mainVolume);
    $("#au-main-note").hidden = edit.mainVolume <= 1;
  }

  function renderSelected(edit: Edit): void {
    const clip = selectedClip();
    selectedBox.hidden = !clip;
    if (!clip) return;
    const total = outputDuration(edit);
    const length = audioLength(clip, total);
    const title = $("#au-title");
    title.textContent = `${clip.source.kind === "sfx" ? "Hiệu ứng" : "Âm thanh"} · ${clip.title || "không tên"}`;
    title.title = clip.source.kind === "file" ? clip.source.path : "";
    if (document.activeElement !== atInput) atInput.value = formatClock(clip.at, true);
    if (document.activeElement !== startInput) startInput.value = formatClock(clip.start, true);
    if (document.activeElement !== endInput) endInput.value = formatClock(clip.end, true);
    loopToggle.checked = clip.loop === true;
    loopToggle.disabled = !clip.loop && clip.end - clip.start > MAX_LOOP_PART;
    loopToggle.parentElement!.title = loopToggle.disabled ? "Chỉ lặp được phần dài tối đa 10 phút" : "Phát lại phần đã chọn đến hết video (nhạc nền)";
    $("#au-length").textContent = clip.loop
      ? `Lặp lại đến hết video · phát ${formatClock(length, true)} từ ${formatClock(clip.at, true)}`
      : length < clip.end - clip.start - 0.01
      ? `Phát ${formatClock(length, true)} · bị cắt ở cuối video`
      : `Phát ${formatClock(length, true)} · ${formatClock(clip.at, true)} – ${formatClock(clip.at + length, true)}`;
    if (document.activeElement !== volumeInput) volumeInput.value = String(Math.round(clip.volume * 100));
    $("#au-volume-value").textContent = percent(clip.volume);
    $("#au-volume-note").hidden = clip.volume <= 1;
    if (document.activeElement !== fadeInInput) fadeInInput.value = String(clip.fadeIn);
    if (document.activeElement !== fadeOutInput) fadeOutInput.value = String(clip.fadeOut);
    $("#au-fade-in-value").textContent = fadeText(clip.fadeIn);
    $("#au-fade-out-value").textContent = fadeText(clip.fadeOut);
    duckToggle.checked = clip.duckDb > 0;
    $("#au-duck-row").hidden = clip.duckDb <= 0;
    if (document.activeElement !== duckInput) duckInput.value = String(clip.duckDb || DEFAULT_DUCK_DB);
    $("#au-duck-value").textContent = `${clip.duckDb || DEFAULT_DUCK_DB} dB`;
    $("#au-play").dataset.listen = sourceKey(clip);
  }

  function renderTabs(): void {
    for (const button of tabs) button.setAttribute("aria-selected", String(button.dataset.auTab === tab));
    $("#au-library").hidden = tab !== "library";
    $("#au-mine").hidden = tab !== "mine";
    $("#au-ai").hidden = tab !== "ai";
    const count = pending.length;
    $("#au-tab-ai").textContent = count ? `Gợi ý AI (${count})` : "Gợi ý AI";
  }

  function renderPack(): void {
    const status = host.sfx.status();
    const row = $("#au-pack");
    const downloading = status?.state === "downloading" || status?.state === "installing";
    row.hidden = !!status?.installed && !downloading;
    $("#au-pack-text").textContent = `Kho hiệu ứng: ${packSummary(status)}`;
    const get = $<HTMLButtonElement>("#au-pack-get");
    get.hidden = !!status?.installed || downloading;
    get.disabled = packBusy;
    get.textContent = status?.state === "failed" ? "Thử lại" : "Tải về";
    const bar = $("#au-pack-progress");
    bar.hidden = !downloading;
    const share = status && status.size_bytes > 0 ? clamp(status.received / status.size_bytes, 0, 1) : 0;
    $("#au-pack-bar").style.width = `${status?.state === "installing" ? 100 : Math.floor(share * 100)}%`;
  }

  function renderCategories(): void {
    const chips = [null, ...CATEGORIES].map((name) => {
      const chip = element("button", "au-cat", name ? categoryName(name) : "Tất cả");
      chip.type = "button";
      chip.setAttribute("role", "radio");
      chip.setAttribute("aria-checked", String(name === category));
      chip.addEventListener("click", () => {
        category = name;
        renderLibrary();
      });
      return chip;
    });
    cats.replaceChildren(...chips);
  }

  function soundRow(sound: Sound): HTMLLIElement {
    const row = element("li", "au-item");
    row.dataset.key = `sfx:${sound.id}`;
    const text = element("span", "au-text");
    text.append(element("strong", "", sound.name_vi), element("small", "", `${categoryName(sound.category)} · ${soundLength(sound.duration_ms)}`));
    text.title = sound.tags.join(", ");
    const add = element("button", "btn au-add", "Thêm");
    add.type = "button";
    add.title = `Thêm tại đầu phát: ${sound.name_vi}`;
    add.setAttribute("aria-label", `Thêm tại đầu phát: ${sound.name_vi}`);
    add.addEventListener("click", (event) => {
      event.stopPropagation();
      void addSound(sound);
    });
    row.append(listenButton(`sfx:${sound.id}`, sound.name_vi), text, add);
    row.addEventListener("click", () => void toggleListen(`sfx:${sound.id}`));
    return row;
  }

  function renderLibrary(): void {
    renderPack();
    for (const chip of cats.querySelectorAll<HTMLButtonElement>(".au-cat")) {
      chip.setAttribute("aria-checked", String((chip.textContent === "Tất cả" && !category) || chip.textContent === category));
    }
    const found = searchSounds(CATALOG.sounds, search.value, category);
    soundsList.replaceChildren(...found.map(soundRow));
    $("#au-sounds-empty").hidden = found.length > 0;
  }

  // ---------- Của bạn ----------

  async function loadFiles(force = false): Promise<void> {
    const folder = host.sfx.folder();
    if (!folder) {
      files = null;
      filesFolder = null;
      renderMine();
      return;
    }
    if (!force && filesFolder === folder && (files || filesError)) return;
    const token = ++filesToken;
    filesFolder = folder;
    files = null;
    filesError = "";
    filesLimit = MINE_STEP;
    renderMine();
    try {
      const listed = await invoke<UserAudio[]>("user_audio_list", { folder });
      if (token !== filesToken) return;
      files = listed;
    } catch (error) {
      if (token !== filesToken) return;
      filesError = String(error).slice(0, 200);
    }
    renderMine();
  }

  function requestFacts(path: string): void {
    if (facts.has(path) || factQueue.includes(path)) return;
    factQueue.push(path);
    pumpFacts();
  }

  function pumpFacts(): void {
    while (factWorkers < 2 && factQueue.length) {
      const path = factQueue.shift()!;
      factWorkers += 1;
      void invoke<AudioFacts>("audio_info", { path })
        .then((info) => facts.set(path, info))
        .catch(() => facts.set(path, null))
        .finally(() => {
          factWorkers -= 1;
          const info = facts.get(path);
          for (const meta of filesList.querySelectorAll<HTMLElement>("[data-meta]")) {
            if (meta.dataset.meta === path) meta.textContent = fileMeta(path, info);
          }
          pumpFacts();
        });
    }
  }

  function fileMeta(path: string, info: AudioFacts | null | undefined): string {
    const size = sizeText(files?.find((file) => file.path === path)?.size_bytes ?? 0);
    if (info === null) return ["Không đọc được", size].filter(Boolean).join(" · ");
    return [info ? formatClock(info.duration, info.duration < 60) : "…", size].filter(Boolean).join(" · ");
  }

  function fileRow(file: UserAudio): HTMLLIElement {
    const row = element("li", "au-item");
    row.dataset.key = `file:${file.path}`;
    const text = element("span", "au-text");
    const meta = element("small", "", fileMeta(file.path, facts.get(file.path)));
    meta.dataset.meta = file.path;
    text.append(element("strong", "", file.name), meta);
    text.title = file.path;
    const add = element("button", "btn au-add", "Thêm");
    add.type = "button";
    add.setAttribute("aria-label", `Thêm tại đầu phát: ${file.name}`);
    add.addEventListener("click", (event) => {
      event.stopPropagation();
      void addFiles([file.path], context.outputTime());
    });
    row.append(listenButton(`file:${file.path}`, file.name), text, add);
    row.addEventListener("click", () => void toggleListen(`file:${file.path}`));
    if (!facts.has(file.path)) requestFacts(file.path);
    return row;
  }

  function renderMine(): void {
    const folder = host.sfx.folder();
    const label = $("#au-folder-label");
    label.textContent = folder ?? "Chưa chọn thư mục";
    label.title = folder ?? "";
    $("#au-folder-pick").textContent = folder ? "Đổi thư mục…" : "Chọn thư mục…";
    const note = $("#au-files-note");
    if (!folder) {
      filesList.replaceChildren();
      note.textContent = "Chọn thư mục chứa nhạc và hiệu ứng của bạn (mp3, wav, ogg, m4a, aac, flac).";
      $("#au-files-more").hidden = true;
      return;
    }
    if (filesError) {
      filesList.replaceChildren();
      note.textContent = `Không đọc được thư mục: ${filesError}`;
      $("#au-files-more").hidden = true;
      return;
    }
    if (!files) {
      filesList.replaceChildren();
      note.textContent = "Đang đọc thư mục…";
      $("#au-files-more").hidden = true;
      return;
    }
    const query = search.value.trim().toLocaleLowerCase("vi");
    const matching = query ? files.filter((file) => file.name.toLocaleLowerCase("vi").includes(query)) : files;
    filesList.replaceChildren(...matching.slice(0, filesLimit).map(fileRow));
    note.textContent = !files.length ? "Thư mục này chưa có file âm thanh nào."
      : files.length >= 2000 ? "Chỉ hiện 2000 file đầu tiên của thư mục." : "";
    $("#au-files-more").hidden = matching.length <= filesLimit;
  }

  // ---------- Gợi ý AI ----------

  function suggestionTitle(item: Suggestion): string {
    return BY_ID.get(item.sfx_id)?.name_vi ?? item.sfx_id;
  }

  function addSuggestions(items: Pending[]): void {
    const total = outputDuration(context.base());
    const usable = items.filter((item) => item.time_ms / 1000 < total - MIN_AUDIO && BY_ID.has(item.sfx_id));
    if (!usable.length) return;
    void ready().then((installed) => {
      if (!installed) return;
      const added = addAll(usable.map((item) => ({ info: soundInfo(BY_ID.get(item.sfx_id)!), at: item.time_ms / 1000 })));
      if (!added) return;
      const keys = new Set(usable.map((item) => item.key));
      pending = pending.filter((item) => !keys.has(item.key));
      renderAi();
      context.redraw();
    });
  }

  function dismiss(keys: Set<string>): void {
    pending = pending.filter((item) => !keys.has(item.key));
    if (focusKey && keys.has(focusKey)) focusKey = null;
    renderAi();
    renderTabs();
    context.redraw();
  }

  function renderAi(): void {
    const provider = host.ai.provider();
    $("#au-ai-provider").textContent = `AI sẽ gửi lời thoại tiếng Việt, thời gian và danh sách hiệu ứng tới ${PROVIDERS[provider].label} bằng tài khoản của bạn (Cài đặt AI). AI chỉ chọn hiệu ứng có trong Kho DubFlow; bạn duyệt từng gợi ý trước khi thêm.`;
    aiRun.disabled = aiBusy;
    aiRun.textContent = pending.length ? "Gợi ý lại" : "Gợi ý hiệu ứng";
    $("#au-ai-bulk").hidden = !pending.length;
    const total = outputDuration(context.edit());
    aiList.replaceChildren(...pending.map((item) => {
      const row = element("li", `au-ai-item${item.key === focusKey ? " focus" : ""}`);
      row.dataset.key = item.key;
      const time = element("button", "au-ai-time link", formatClock(item.time_ms / 1000));
      time.type = "button";
      time.title = "Tới thời điểm này";
      time.addEventListener("click", () => context.seek(item.time_ms / 1000));
      const text = element("span", "au-text");
      text.append(element("strong", "", suggestionTitle(item)), element("small", "", item.reason_vi));
      const add = element("button", "btn au-add", "Thêm");
      add.type = "button";
      add.disabled = item.time_ms / 1000 >= total - MIN_AUDIO;
      add.title = add.disabled ? "Thời điểm này đã nằm ngoài video sau khi cắt" : `Thêm “${suggestionTitle(item)}” lúc ${formatClock(item.time_ms / 1000)}`;
      add.addEventListener("click", () => addSuggestions([item]));
      const skip = element("button", "btn ghost au-skip", "Bỏ qua");
      skip.type = "button";
      skip.addEventListener("click", () => dismiss(new Set([item.key])));
      const actions = element("span", "au-ai-actions");
      actions.append(listenButton(`sfx:${item.sfx_id}`, suggestionTitle(item)), add, skip);
      row.append(time, text, actions);
      return row;
    }));
    renderTabs();
  }

  async function runAi(): Promise<void> {
    if (aiBusy) return;
    const provider = host.ai.provider(), key = host.ai.read();
    if (!key) {
      aiState.replaceChildren(`Hãy lưu khóa ${PROVIDERS[provider].name} trong Cài đặt AI. `);
      const open = element("button", "link", "Mở Cài đặt AI");
      open.type = "button";
      open.addEventListener("click", () => host.openAiSettings());
      aiState.append(open);
      return;
    }
    const token = ++aiToken;
    aiBusy = true;
    aiState.textContent = `Đang gửi lời thoại tới ${PROVIDERS[provider].label} để gợi ý…`;
    renderAi();
    try {
      const total = outputDuration(context.edit());
      const found = await suggestEffects(provider, key, context.cues(), CATALOG.sounds, Math.floor(total * 1000));
      if (token !== aiToken || !active) return;
      pending = found.map((item, index) => ({ ...item, key: `${token}-${index}` }));
      focusKey = null;
      aiState.textContent = `${pending.length} gợi ý. Nghe thử rồi bấm Thêm cho chỗ bạn thấy hợp; chưa có gì được thêm vào video.`;
    } catch (error) {
      if (token !== aiToken || !active) return;
      aiState.textContent = String((error as Error)?.message ?? error).slice(0, 300);
    } finally {
      if (token === aiToken) aiBusy = false;
      if (active) {
        renderAi();
        context.redraw();
      }
    }
  }

  function render(show: boolean): void {
    shown = show;
    section.hidden = !show;
    if (!show || !active) return;
    const edit = context.edit();
    renderMain(edit);
    renderSelected(edit);
    renderTabs();
    if (tab === "library") renderLibrary();
    if (tab === "mine") {
      renderMine();
      void loadFiles();
    }
    if (tab === "ai") renderAi();
    renderListenButtons();
  }

  // ---------- Timeline row ----------

  function renderTrack(row: HTMLElement, show: boolean, pad: number): number {
    const edit = context.edit();
    const total = outputDuration(edit);
    const visible = active && (show || edit.audio.length > 0 || pending.length > 0);
    row.hidden = !visible;
    if (!visible) {
      row.replaceChildren();
      return 0;
    }
    const pps = context.pps();
    const marks = pending.filter((item) => item.time_ms / 1000 < total).map((item) => ({
      id: `p:${item.key}`, at: item.time_ms / 1000, start: 0, end: (BY_ID.get(item.sfx_id)?.duration_ms ?? 500) / 1000, item,
    }));
    // Lanes as drawn: a short sound takes its minimum width.
    const least = MIN_BLOCK_PX / Math.max(pps, 1e-6);
    const drawn = edit.audio.map((clip) => clip.loop ? clip : { ...clip, end: Math.max(clip.end, clip.start + least) });
    const lanes = audioLanes([...drawn, ...marks], total);
    const count = Math.min(MAX_LANES, Math.max(1, ...[...lanes.values()].map((lane) => lane + 1)));
    const blocks: HTMLElement[] = [];
    for (const clip of edit.audio) {
      const length = audioLength(clip, total);
      const block = element("div", `tl-au${clip.source.kind === "file" ? " file" : ""}${clip.id === selected ? " selected" : ""}`);
      block.dataset.id = clip.id;
      Object.assign(block.style, {
        left: `${pad + clip.at * pps}px`, width: `${Math.max(MIN_BLOCK_PX, length * pps)}px`, top: `${Math.min(count - 1, lanes.get(clip.id) ?? 0) * LANE_PX}px`,
      });
      block.title = `${clip.title} · ${formatClock(clip.at, true)} – ${formatClock(clip.at + length, true)}${clip.duckDb > 0 ? ` · giảm âm gốc ${clip.duckDb} dB` : ""}`;
      block.append(element("span", "tl-au-name", clip.title || "Âm thanh"));
      // A block too narrow for handles is only moved; zoom in to trim it.
      // Every block can be clicked and trimmed: short sounds are drawn at least 22 px wide.
      {
        const start = element("span", "tl-handle start");
        start.dataset.edge = "start";
        const end = element("span", "tl-handle end");
        end.dataset.edge = "end";
        // A looping sound runs to the end of the video: its part is set in the panel.
        block.append(...(clip.loop ? [start] : [start, end]));
      }
      if (clip.loop) {
        block.classList.add("loop");
        block.style.setProperty("--part", `${Math.max(4, (clip.end - clip.start) * pps)}px`);
        block.title += " · lặp lại đến hết video";
      }
      if (clip.fadeIn > 0 || clip.fadeOut > 0) {
        const fades = element("span", "tl-au-fades");
        fades.style.setProperty("--in", `${Math.min(100, clip.fadeIn / Math.max(length, 0.001) * 100)}%`);
        fades.style.setProperty("--out", `${Math.min(100, clip.fadeOut / Math.max(length, 0.001) * 100)}%`);
        block.prepend(fades);
      }
      blocks.push(block);
    }
    for (const mark of marks) {
      const block = element("div", `tl-au pending${mark.item.key === focusKey ? " focus" : ""}`);
      block.dataset.pending = mark.item.key;
      Object.assign(block.style, {
        left: `${pad + mark.at * pps}px`, width: `${Math.max(6, Math.min(mark.end, total - mark.at) * pps)}px`,
        top: `${Math.min(count - 1, lanes.get(mark.id) ?? 0) * LANE_PX}px`,
      });
      block.title = `Gợi ý AI: ${suggestionTitle(mark.item)} · ${formatClock(mark.at)}${mark.item.reason_vi ? ` · ${mark.item.reason_vi}` : ""}`;
      block.append(element("span", "tl-au-name", suggestionTitle(mark.item)));
      blocks.push(block);
    }
    if (!edit.audio.length && !marks.length) {
      const hint = element("span", "tl-au-hint", "Thả file âm thanh vào đây, hoặc chọn hiệu ứng trong bảng Âm thanh");
      hint.style.left = `${pad}px`;
      blocks.push(hint);
    }
    row.replaceChildren(...blocks);
    row.style.height = `${count * LANE_PX}px`;
    return count * LANE_PX;
  }

  type Drag = { kind: "move" | "trim"; id: string; edge: "start" | "end"; x: number; base: Edit; moved: boolean; pointer: number };
  let drag: Drag | null = null;
  const row = $("#tl-audio");

  row.addEventListener("pointerdown", (event) => {
    if (!active || event.button !== 0) return;
    const target = event.target as HTMLElement;
    const mark = target.closest<HTMLElement>(".tl-au.pending");
    if (mark) {
      event.stopPropagation();
      focusKey = mark.dataset.pending ?? null;
      tab = "ai";
      context.enterMode();
      const item = pending.find((candidate) => candidate.key === focusKey);
      if (item) context.seek(item.time_ms / 1000);
      aiList.querySelector<HTMLElement>(`[data-key="${focusKey}"]`)?.scrollIntoView({ block: "nearest" });
      return;
    }
    const block = target.closest<HTMLElement>(".tl-au");
    if (!block || context.busy()) return;
    event.stopPropagation();
    event.preventDefault();
    row.setPointerCapture(event.pointerId);
    const handle = target.closest<HTMLElement>(".tl-handle");
    selected = block.dataset.id ?? null;
    drag = { kind: handle ? "trim" : "move", id: selected!, edge: handle?.dataset.edge === "start" ? "start" : "end", x: event.clientX,
      base: context.base(), moved: false, pointer: event.pointerId };
    context.enterMode();
  });

  row.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const dragging = drag;
    if (!dragging.moved && Math.abs(event.clientX - dragging.x) <= 4) return;
    dragging.moved = true;
    const clip = dragging.base.audio.find((candidate) => candidate.id === dragging.id);
    if (!clip) return;
    const shift = (event.clientX - dragging.x) / context.pps();
    let next: Edit;
    if (dragging.kind === "move") {
      next = setAudioTime(dragging.base, dragging.id, { at: clip.at + shift });
    } else if (dragging.edge === "end") {
      next = setAudioTime(dragging.base, dragging.id, { end: clip.end + shift });
    } else {
      const start = clamp(clip.start + shift, Math.max(0, clip.start - clip.at), clip.end - MIN_AUDIO);
      next = setAudioTime(dragging.base, dragging.id, { start, at: clip.at + (start - clip.start) });
    }
    context.preview(next);
    const moved = next.audio.find((candidate) => candidate.id === dragging.id)!;
    context.seek(dragging.kind === "trim" && dragging.edge === "end" ? moved.at + audioLength(moved, outputDuration(next)) - 0.02 : moved.at);
  });

  const endDrag = (event: PointerEvent): void => {
    if (!drag || event.pointerId !== drag.pointer) return;
    const ended = drag;
    drag = null;
    const next = context.busy() ? context.edit() : null;
    context.preview(null);
    if (ended.moved && next) context.commit(next);
    else {
      const clip = context.base().audio.find((candidate) => candidate.id === ended.id);
      if (clip && !ended.moved) context.seek(clamp(context.timeAt(ended.x), clip.at, clip.at + audioLength(clip, outputDuration(context.base()))));
      context.redraw();
    }
  };
  row.addEventListener("pointerup", endDrag);
  row.addEventListener("pointercancel", endDrag);

  // ---------- Live preview ----------

  function playerFor(clip: AudioClip): Player {
    let player = players.get(clip.id);
    const key = sourceKey(clip);
    if (!player) {
      const created = new Audio();
      created.preload = "auto";
      player = { element: created, key: "" };
      players.set(clip.id, player);
    }
    if (player.key !== key) {
      const target = player;
      target.key = key;
      target.element.removeAttribute("src");
      void sourceUrl(key).then((url) => { if (url && target.key === key && players.get(clip.id) === target) target.element.src = url; });
    }
    return player;
  }

  function release(player: Player): void {
    player.element.pause();
    player.element.removeAttribute("src");
    player.element.load();
  }

  function frame(time: number, playing: boolean): void {
    if (!active) return;
    const edit = context.edit();
    const total = outputDuration(edit);
    const seen = new Set<string>();
    for (const clip of edit.audio) {
      seen.add(clip.id);
      const length = audioLength(clip, total);
      const into = time - clip.at;
      const existing = players.get(clip.id);
      // Load a little ahead so the clip starts on time.
      if (into < -2 || into >= length) {
        if (existing && !existing.element.paused) existing.element.pause();
        continue;
      }
      const player = playerFor(clip);
      const audio = player.element;
      if (into >= 0 && playing) {
        const gain = audioGain(clip, time, total);
        audio.volume = clamp(gain, 0, 1);
        audio.muted = gain <= 0;
        const want = audioPosition(clip, time);
        if (audio.readyState >= 1 && Math.abs(audio.currentTime - want) > 0.15) audio.currentTime = want;
        if (audio.paused && audio.readyState >= 2) void audio.play().catch(() => undefined);
      } else {
        if (!audio.paused) audio.pause();
        const want = audioPosition(clip, Math.max(time, clip.at));
        if (audio.readyState >= 1 && Math.abs(audio.currentTime - want) > 0.03) audio.currentTime = want;
      }
    }
    for (const [id, player] of players) {
      if (seen.has(id)) continue;
      release(player);
      players.delete(id);
    }
  }

  function pause(): void {
    for (const player of players.values()) if (!player.element.paused) player.element.pause();
  }

  // ---------- Panel input ----------

  function selectedId(): string | null {
    return selectedClip() ? selected : null;
  }

  mainInput.addEventListener("input", () => {
    if (!active) return;
    context.preview(setMainVolume(context.base(), Number(mainInput.value) / 100));
  });
  mainInput.addEventListener("change", () => {
    if (!active) return;
    const next = context.busy() ? context.edit() : null;
    context.preview(null);
    if (next) context.commit(next);
  });
  const props: Array<[HTMLInputElement, (value: number) => Parameters<typeof setAudioProps>[2]]> = [
    [volumeInput, (value) => ({ volume: value / 100 })],
    [fadeInInput, (value) => ({ fadeIn: value })],
    [fadeOutInput, (value) => ({ fadeOut: value })],
    [duckInput, (value) => ({ duckDb: value })],
  ];
  for (const [input, change] of props) {
    input.addEventListener("input", () => {
      const id = selectedId();
      if (!id) return;
      context.preview(setAudioProps(context.base(), id, change(Number(input.value))));
    });
    input.addEventListener("change", () => {
      const next = context.busy() ? context.edit() : null;
      context.preview(null);
      if (next && selectedId()) context.commit(next);
    });
  }
  loopToggle.addEventListener("change", () => {
    const id = selectedId();
    if (id) context.commit(setAudioProps(context.base(), id, { loop: loopToggle.checked }));
  });
  duckToggle.addEventListener("change", () => {
    const id = selectedId();
    if (id) context.commit(setAudioProps(context.base(), id, { duckDb: duckToggle.checked ? Number(duckInput.value) || DEFAULT_DUCK_DB : 0 }));
  });
  for (const [input, field] of [[atInput, "at"], [startInput, "start"], [endInput, "end"]] as const) {
    input.addEventListener("change", () => {
      const id = selectedId();
      const value = parseClock(input.value);
      if (id && value !== null) context.commit(setAudioTime(context.base(), id, { [field]: value }));
      else context.redraw();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") input.blur();
      if (event.key === "Escape") {
        input.blur();
        context.redraw();
      }
    });
  }
  $("#au-delete").addEventListener("click", () => { deleteSelected(); });
  $("#au-play").addEventListener("click", () => {
    const clip = selectedClip();
    if (clip) context.seek(clip.at);
    const play = $("#au-play");
    if (play.dataset.listen) void toggleListen(play.dataset.listen);
  });
  for (const button of tabs) {
    button.addEventListener("click", () => {
      tab = button.dataset.auTab === "mine" ? "mine" : button.dataset.auTab === "ai" ? "ai" : "library";
      search.placeholder = tab === "mine" ? "Tìm trong thư mục của bạn…" : "Tìm: vút, đấm, ding, cười, cửa…";
      render(shown);
    });
  }
  search.addEventListener("input", () => {
    filesLimit = MINE_STEP;
    if (tab === "mine") renderMine();
    else renderLibrary();
  });
  $("#au-pack-get").addEventListener("click", () => void ready());
  $("#au-folder-pick").addEventListener("click", async () => {
    try {
      const picked = await host.sfx.pickFolder();
      if (picked) await loadFiles(true);
    } catch (error) {
      host.showError(`Không mở được hộp thoại chọn thư mục: ${String(error).slice(0, 160)}`);
    }
  });
  $("#au-files-more").addEventListener("click", () => {
    filesLimit += MINE_STEP;
    renderMine();
  });
  $("#au-pick-files").addEventListener("click", async () => {
    let paths: string[] = [];
    try {
      paths = await invoke<string[]>("pick_audio_files");
    } catch (error) {
      host.showError(`Không mở được hộp thoại chọn file: ${String(error).slice(0, 160)}`);
      return;
    }
    if (paths.length) await addFiles(paths, context.outputTime());
  });
  aiRun.addEventListener("click", () => void runAi());
  $("#au-ai-all").addEventListener("click", () => addSuggestions([...pending]));
  $("#au-ai-none").addEventListener("click", () => dismiss(new Set(pending.map((item) => item.key))));
  host.sfx.subscribe(() => { if (active && shown && tab === "library") renderPack(); });
  renderCategories();

  function deleteSelected(): boolean {
    const id = selectedId();
    if (!id || context.busy()) return false;
    selected = null;
    context.commit(removeAudio(context.base(), id));
    return true;
  }

  return {
    begin: () => {
      active = true;
      selected = null;
      pending = [];
      focusKey = null;
      aiState.textContent = "";
      aiToken += 1;
      aiBusy = false;
    },
    end: () => {
      active = false;
      aiToken += 1;
      aiBusy = false;
      drag = null;
      stopListening();
      for (const player of players.values()) release(player);
      players.clear();
      selected = null;
      pending = [];
    },
    render,
    renderTrack,
    frame,
    pause: () => {
      pause();
    },
    get selected() { return selectedId(); },
    select: (id) => { selected = id; },
    deleteSelected,
    drop: (paths, at) => addFiles(paths, at),
  };
}

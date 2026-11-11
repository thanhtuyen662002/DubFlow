/**
 * Tổng hợp từ video gốc (Issue #728): the Thiết lập form of a raw-footage
 * programme and the Kết quả panel of a v2 run, inside the Tổng hợp page (the
 * page keeps its list, player and polling for both kinds). Every action is a
 * host call that checks its arguments again; the final export is gated by
 * the host on fresh review files.
 */
import {
  addSources, applySuggestedOrder, buildRawRequest, durationNote, emptyDraft, fileName, MAX_ITEMS, MAX_NARRATION, moveItem, newItem,
  numberRanks, openForExport, parseRawRun, partName, removeItem, removeSource, renderStats, RIGHTS_NAMES, sourceName, sourcesOf,
  specToDraft, supported, type RawDraft, type RawPart, type RawRun, type Rights,
} from "./raw_model.ts";
import { isLevel, LEVEL_NAMES, LEVELS, levelText } from "../mastering/model.ts";
import { openReviewPanel } from "../mastering/review_panel.ts";
import { PROVIDERS, type AiSession } from "../ai/provider.ts";
import { parseVoiceCatalog, voiceOptionGroups } from "../voices/model.ts";
import { confirmDialog } from "../../shell/confirm.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type RawHost = {
  setup: HTMLElement; result: HTMLElement;
  outputRoot(): string | null; cpuOnly(): boolean;
  mediaUrl?(path: string): Promise<string | null>;
  /** "Chọn từ Hàng đợi" for source videos. */
  fromQueue?(): void;
  /** A new run started: the page lists it, shows it and follows its progress. */
  started(dir: string, title: string, parts: number): void;
  /** A run is being made again in place: the page follows its progress. */
  rerendered(dir: string): void;
  /** Play the run's video in the page's player from a moment. */
  seek(path: string, ms: number): void;
  /** Open the finished programme in the editor, after the export gate passed. */
  edit?(path: string, title: string): void;
  /** Show the Thiết lập tab (the form now edits a run in place, or is a new draft again). */
  openSetup(): void;
};

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className = "", text = ""): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function button(text: string, className = "btn", title = ""): HTMLButtonElement {
  const node = el("button", className, text);
  node.type = "button";
  if (title) node.title = title;
  return node;
}

function option(text: string, value: string): HTMLOptionElement {
  const node = el("option", "", text);
  node.value = value;
  return node;
}

function words(text: string): HTMLSpanElement {
  return el("span", "", text);
}

function labelled(text: string, control: HTMLElement, wide = true): HTMLLabelElement {
  const label = el("label", wide ? "wide" : "");
  label.append(words(text), control);
  return label;
}

const message = (error: unknown): string => String(error).slice(0, 300);

export type RawPage = {
  showDraft(): void; showRun(dir: string): Promise<void>; addPaths(paths: readonly string[]): string; busy(): boolean;
  /** The run whose programme the form is editing in place, or null. */
  editingDir(): string | null;
  /** The page's "Chỉnh sửa" on a v2 run: the final export gate first, then the editor. */
  exportThenEdit(dir: string): Promise<void>;
};

export function mountRawCompilation(invoke: Invoke, session: AiSession, host: RawHost): RawPage {
  let draft: RawDraft = emptyDraft();
  /** The run being edited in place ("Sửa và dựng lại"), or null for a new programme. */
  let editing: string | null = null;
  let working = false;
  let voicesLoaded = false;
  let shownRun: RawRun | null = null;

  // ---------- Thiết lập ----------
  const form = el("div", "compilation-raw");
  const editNote = el("p", "tab-note boxed");
  editNote.hidden = true;

  const sourcesGroup = el("section", "compilation-group");
  const addFiles = button("Thêm video…", "btn", "Chọn các video gốc trên máy");
  const addFolder = button("Thêm cả thư mục…", "btn", "Thêm mọi video trong một thư mục (tối đa 3 cấp)");
  const fromQueue = button("Chọn từ Hàng đợi", "btn", "Dùng video đã tải về trong Hàng đợi (không cần lồng tiếng)");
  fromQueue.hidden = !host.fromQueue;
  const sourceNote = el("p", "hint", "");
  sourceNote.setAttribute("role", "status");
  const sourceActions = el("div", "compilation-raw-actions");
  sourceActions.append(addFiles, addFolder, fromQueue);
  sourcesGroup.append(el("h3", "setup-group", "Video nguồn"),
    el("p", "hint", `Video gốc, chưa cần lồng tiếng: MP4, MOV, MKV, WEBM, M4V hoặc AVI; mỗi video thành một mục (tối đa ${MAX_ITEMS} mục, 8 nguồn mỗi mục). DubFlow chỉ đọc video nguồn, không ghi gì vào đó.`),
    sourceActions, sourceNote);

  const programGroup = el("section", "compilation-group");
  const title = el("input");
  title.maxLength = 120;
  title.id = "compilation-raw-title";
  const kind = el("select");
  kind.id = "compilation-raw-kind";
  kind.append(option("Top N (xếp hạng)", "top_n"), option("Danh sách (không xếp hạng)", "list"));
  const language = el("select");
  language.append(option("Tiếng Việt", "vi"));
  language.disabled = true;
  language.title = "Giọng đọc hiện có trong DubFlow là tiếng Việt";
  const target = el("input");
  target.type = "number"; target.min = "0"; target.step = "0.5"; target.id = "compilation-raw-target";
  const layout = el("select");
  layout.append(option("Toàn màn hình", "toan-man-hinh"), option("Bảng thông tin bên trái (cho video dọc)", "bang-thong-tin"));
  const durationLine = el("p", "hint", "");
  const programForm = el("div", "product-form");
  programForm.append(labelled("Chủ đề / tiêu đề", title), labelled("Kiểu chương trình", kind, false), labelled("Ngôn ngữ lời bình", language, false),
    labelled("Thời lượng mục tiêu (phút)", target, false), labelled("Bố cục", layout, false));
  programGroup.append(el("h3", "setup-group", "Chương trình"), programForm, durationLine);

  const itemsGroup = el("section", "compilation-group");
  const countLine = el("p", "hint", "");
  const itemList = el("ol", "compilation-raw-items");
  const addItem = button("Thêm mục trống", "btn");
  const countdown = button("Đánh số đếm ngược", "btn", "Mục đầu danh sách là hạng cao nhất (#N), mục cuối là #1");
  const countup = button("Đánh số từ #1", "btn", "Mục đầu danh sách là #1");
  const itemActions = el("div", "compilation-raw-actions");
  itemActions.append(addItem, countdown, countup);
  itemsGroup.append(el("h3", "setup-group", "Các mục"),
    el("p", "hint", "Thứ tự và thứ hạng là của bạn: DubFlow không tự đổi. Dùng ↑ ↓ để sắp xếp; thứ hạng để trống ở chương trình Danh sách."),
    countLine, itemList, itemActions);

  const linesGroup = el("section", "compilation-group");
  const hook = el("textarea"); hook.rows = 2; hook.maxLength = 300;
  const intro = el("textarea"); intro.rows = 3; intro.maxLength = 800;
  const outro = el("textarea"); outro.rows = 3; outro.maxLength = 800;
  const linesForm = el("div", "product-form");
  linesForm.append(labelled("Câu mở màn (tùy chọn, tối đa 300 ký tự)", hook), labelled("Mở đầu (tối đa 800 ký tự)", intro),
    labelled("Kết thúc (tối đa 800 ký tự)", outro));
  linesGroup.append(el("h3", "setup-group", "Lời dẫn chương trình"), linesForm);

  const voiceGroup = el("section", "compilation-group");
  const voice = el("select"); voice.id = "compilation-raw-voice";
  const speed = el("select");
  for (const [value, text] of [["1", "Bình thường (1,0)"], ["1.1", "1,1"], ["1.2", "1,2"], ["1.3", "Nhanh (1,3)"]]) speed.append(option(text, value));
  const musicName = el("span", "", "Không có");
  const musicPick = button("Chọn…", "link");
  const musicClear = button("Bỏ", "link");
  musicClear.hidden = true;
  const volume = el("input"); volume.type = "range"; volume.min = "0"; volume.max = "100";
  const music = el("span", "product-music");
  music.append(words("Nhạc: "), musicName, musicPick, musicClear);
  const voiceForm = el("div", "product-options");
  voiceForm.append(labelled("Giọng đọc", voice, false), labelled("Tốc độ đọc", speed, false), music, labelled("Âm lượng nhạc", volume, false));
  voiceGroup.append(el("h3", "setup-group", "Giọng đọc và nhạc"), voiceForm,
    el("p", "hint", "Âm thanh gốc của mỗi nguồn được giữ ở khoảnh khắc của nó (tùy chọn theo mục), nhỏ lại dưới lời bình."));

  const masteringGroup = el("section", "compilation-group");
  const mastering = el("select"); mastering.id = "compilation-raw-mastering";
  for (const level of LEVELS) mastering.append(option(LEVEL_NAMES[level], level));
  const masteringHint = el("p", "hint", "");
  masteringGroup.append(el("h3", "setup-group", "Tối ưu hậu kỳ tự động"), labelled("Mức", mastering, false), masteringHint);

  const aiGroup = el("section", "compilation-group");
  const useAi = el("input"); useAi.type = "checkbox";
  const suggest = el("input"); suggest.type = "checkbox";
  const applySuggestion = el("input"); applySuggestion.type = "checkbox";
  const drafting = el("input"); drafting.type = "checkbox";
  const aiNote = el("p", "hint", "");
  const check = (box: HTMLInputElement, text: string): HTMLLabelElement => {
    const label = el("label", "check");
    label.append(box, words(` ${text}`));
    return label;
  };
  const applyRow = check(applySuggestion, "Dùng luôn thứ tự AI gợi ý cho bản nháp (bạn vẫn sửa được trước khi xuất)");
  aiGroup.append(el("h3", "setup-group", "AI (tùy chọn)"), check(useAi, "Dùng AI để xem khung hình của các cảnh"),
    check(suggest, "AI gợi ý thứ hạng (chỉ là gợi ý; thứ hạng của bạn giữ nguyên)"), applyRow,
    check(drafting, "AI viết lời bình cho mục để trống"), aiNote);

  const foot = el("div", "actions tab-foot");
  const renderButton = button("Dựng bản nháp", "btn primary block");
  renderButton.id = "compilation-raw-render";
  const cancelEdit = button("Bỏ sửa, quay lại bản mới", "btn block");
  cancelEdit.hidden = true;
  const state = el("p", "hint center");
  state.setAttribute("role", "status");
  state.id = "compilation-raw-state";
  foot.append(renderButton, cancelEdit, state);

  form.append(editNote, sourcesGroup, programGroup, itemsGroup, linesGroup, voiceGroup, masteringGroup, aiGroup);
  host.setup.replaceChildren(form, foot);

  function aiRoute() {
    const provider = session.provider();
    if (!session.saved(provider)) return null;
    const options = session.options(provider);
    return { provider, ...(options.model ? { model: options.model } : {}), ...(options.baseUrl ? { baseUrl: options.baseUrl } : {}) };
  }

  function showAi(): void {
    const route = aiRoute();
    const company = PROVIDERS[session.provider()].company;
    useAi.disabled = !route;
    if (!route) { useAi.checked = false; draft.useAi = false; }
    suggest.disabled = drafting.disabled = !useAi.checked;
    applySuggestion.disabled = !useAi.checked || !suggest.checked;
    applyRow.hidden = !suggest.checked;
    aiNote.textContent = route
      ? `Khi bật, ảnh khung hình của các cảnh, tiêu đề, ghi chú và lời bình được gửi tới ${company} bằng khóa đã lưu trong Cài đặt › Tài khoản. Khóa chỉ được dùng cho lần dựng này, không ghi vào dự án.`
      : "Chưa có khóa AI trong Cài đặt › Tài khoản: DubFlow chọn cảnh bằng tín hiệu hình ảnh, phụ đề và ghi chú của bạn.";
  }

  function fillFields(): void {
    title.value = draft.title; kind.value = draft.kind; target.value = draft.targetMinutes; layout.value = draft.layout;
    hook.value = draft.hook; intro.value = draft.intro; outro.value = draft.outro;
    speed.value = String(draft.speed); volume.value = String(Math.round(draft.musicVolume * 100));
    musicName.textContent = draft.music ? fileName(draft.music) : "Không có"; musicClear.hidden = !draft.music;
    mastering.value = draft.mastering; useAi.checked = draft.useAi; suggest.checked = draft.ranking === "suggested";
    applySuggestion.checked = draft.applySuggestion; drafting.checked = draft.draftNarration;
    if (draft.voiceId && [...voice.options].some((option) => option.value === draft.voiceId)) voice.value = draft.voiceId;
    masteringHint.textContent = levelText("compilation", draft.mastering);
    editNote.hidden = !editing;
    editNote.textContent = editing ? "Đang sửa chương trình đã dựng. “Lưu và dựng lại” chỉ dựng lại các phần thay đổi; quyết định duyệt cảnh của các phần được giữ." : "";
    renderButton.textContent = editing ? "Lưu và dựng lại" : "Dựng bản nháp";
    cancelEdit.hidden = !editing;
    showAi();
  }

  function rerenderItems(): void {
    const total = draft.items.length;
    countLine.textContent = `${total} mục · ${draft.sources.length} video nguồn${total > MAX_ITEMS ? ` (tối đa ${MAX_ITEMS})` : ""}`;
    durationLine.textContent = durationNote(draft);
    itemList.replaceChildren(...draft.items.map((item, index) => {
      const li = el("li", "compilation-raw-item");
      const head = el("div", "compilation-raw-item-head");
      const rank = el("input");
      rank.type = "number"; rank.min = "1"; rank.max = "1000"; rank.value = item.rank; rank.className = "compilation-raw-rank";
      rank.setAttribute("aria-label", `Mục ${index + 1}: thứ hạng`);
      rank.disabled = draft.kind !== "top_n";
      rank.addEventListener("input", () => { item.rank = rank.value; });
      const up = button("↑", "btn", `Đưa mục ${index + 1} lên`), down = button("↓", "btn", `Đưa mục ${index + 1} xuống`);
      up.disabled = index === 0; down.disabled = index === total - 1;
      up.addEventListener("click", () => { moveItem(draft, item.key, -1); rerenderItems(); });
      down.addEventListener("click", () => { moveItem(draft, item.key, 1); rerenderItems(); });
      const remove = button("Xóa mục", "link", "Bỏ mục này và các nguồn của nó khỏi chương trình (video gốc không bị xóa)");
      remove.addEventListener("click", () => { removeItem(draft, item.key); rerenderItems(); });
      head.append(el("b", "", `Mục ${index + 1}`), words(" #"), rank, up, down, remove);
      const name = el("input"); name.maxLength = 80; name.value = item.title;
      name.addEventListener("input", () => { item.title = name.value; });
      const narration = el("textarea"); narration.rows = 3; narration.maxLength = MAX_NARRATION; narration.value = item.narration;
      narration.addEventListener("input", () => { item.narration = narration.value; durationLine.textContent = durationNote(draft); });
      const notes = el("textarea"); notes.rows = 2; notes.maxLength = 600; notes.value = item.notes;
      notes.addEventListener("input", () => { item.notes = notes.value; });
      const keep = el("select");
      keep.append(option("Tự động", "auto"), option("Luôn giữ", "on"), option("Không giữ", "off"));
      keep.value = item.keepAudio;
      keep.addEventListener("change", () => { item.keepAudio = keep.value as typeof item.keepAudio; });
      const fields = el("div", "product-form");
      fields.append(labelled("Tiêu đề mục", name), labelled(`Lời bình (tối đa ${MAX_NARRATION} ký tự)`, narration),
        labelled("Ghi chú cho AI và cho việc chọn cảnh (tên riêng, A = B…)", notes), labelled("Âm thanh gốc", keep, false));
      const sources = el("ul", "compilation-raw-sources");
      for (const [k, source] of sourcesOf(draft, item.key).entries()) {
        const row = el("li", supported(source.path) ? "" : "unsupported");
        row.title = source.path;
        const start = el("input"); start.inputMode = "decimal"; start.placeholder = "từ đầu"; start.value = source.start;
        start.setAttribute("aria-label", `Mục ${index + 1}, nguồn ${k + 1}: bắt đầu (giây)`);
        start.addEventListener("input", () => { source.start = start.value; });
        const end = el("input"); end.inputMode = "decimal"; end.placeholder = "hết video"; end.value = source.end;
        end.setAttribute("aria-label", `Mục ${index + 1}, nguồn ${k + 1}: kết thúc (giây)`);
        end.addEventListener("input", () => { source.end = end.value; });
        const rights = el("select");
        for (const [value, text] of Object.entries(RIGHTS_NAMES)) rights.append(option(text, value));
        rights.value = source.rights;
        rights.setAttribute("aria-label", `Mục ${index + 1}, nguồn ${k + 1}: quyền sử dụng`);
        rights.addEventListener("change", () => { source.rights = rights.value as Rights; });
        const move = el("select");
        move.setAttribute("aria-label", `Chuyển nguồn ${k + 1} sang mục khác`);
        draft.items.forEach((other, j) => move.append(option(`Mục ${j + 1}`, other.key)));
        move.value = item.key;
        move.addEventListener("change", () => {
          if (sourcesOf(draft, move.value).length >= 8) { sourceNote.textContent = "Mục đó đã đủ 8 nguồn."; move.value = item.key; return; }
          source.item = move.value; rerenderItems();
        });
        const drop = button("✕", "link", "Bỏ nguồn này khỏi chương trình (video gốc không bị xóa)");
        drop.setAttribute("aria-label", `Bỏ nguồn ${fileName(source.path)}`);
        drop.addEventListener("click", () => { removeSource(draft, source.key); rerenderItems(); });
        row.append(el("span", "compilation-raw-source-name", `Nguồn ${k + 1}: ${fileName(source.path)}`),
          labelled("Bắt đầu", start, false), labelled("Kết thúc", end, false), rights, move, drop);
        sources.append(row);
      }
      if (!sources.children.length) sources.append(el("li", "hint", "Mục này chưa có video nguồn."));
      li.append(head, fields, sources);
      return li;
    }));
    addItem.disabled = total >= MAX_ITEMS;
  }

  function report(result: ReturnType<typeof addSources>): string {
    const parts = [result.added ? `Đã thêm ${result.added} video.` : ""];
    if (result.unsupported.length) parts.push(`Chưa hỗ trợ định dạng: ${result.unsupported.slice(0, 5).join(", ")}${result.unsupported.length > 5 ? "…" : ""}.`);
    if (result.duplicate.length) parts.push(`Đã có trong chương trình: ${result.duplicate.slice(0, 3).join(", ")}.`);
    if (result.full.length) parts.push(`Không còn chỗ (tối đa ${MAX_ITEMS} mục, 8 nguồn mỗi mục): ${result.full.length} video.`);
    return parts.filter(Boolean).join(" ") || "Không có video nào được thêm.";
  }

  function addPaths(paths: readonly string[]): string {
    const text = report(addSources(draft, paths));
    sourceNote.textContent = text;
    rerenderItems();
    return text;
  }

  async function loadVoices(): Promise<void> {
    if (voicesLoaded) return;
    try {
      const catalog = parseVoiceCatalog(await invoke("voice_catalog"));
      voice.replaceChildren(...voiceOptionGroups(catalog, draft.voiceId || catalog.default_voice_id));
      voicesLoaded = true;
      if (draft.voiceId) voice.value = draft.voiceId;
    } catch (error) { state.textContent = message(error); }
  }

  function readFields(): void {
    Object.assign(draft, {
      title: title.value, kind: kind.value === "list" ? "list" : "top_n", targetMinutes: target.value,
      layout: layout.value === "bang-thong-tin" ? "bang-thong-tin" : "toan-man-hinh", hook: hook.value, intro: intro.value, outro: outro.value,
      voiceId: voice.value, speed: Number(speed.value), musicVolume: Number(volume.value) / 100,
      mastering: isLevel(mastering.value) ? mastering.value : "balanced", useAi: useAi.checked,
      ranking: useAi.checked && suggest.checked ? "suggested" : "user", applySuggestion: useAi.checked && suggest.checked && applySuggestion.checked,
      draftNarration: useAi.checked && drafting.checked,
    });
  }

  async function render(): Promise<void> {
    if (working) return;
    readFields();
    const request = buildRawRequest(draft, { ai: aiRoute(), outputRoot: host.outputRoot(), cpuOnly: host.cpuOnly() });
    if (typeof request === "string") { state.textContent = request; return; }
    working = true;
    renderButton.disabled = true;
    state.textContent = editing ? "Đang lưu và dựng lại…" : "Đang bắt đầu dựng…";
    try {
      if (editing) {
        await invoke("compilation_raw_rerender", { outputDir: editing, request, cpuOnly: host.cpuOnly() });
        const dir = editing;
        editing = null;
        draft = emptyDraft();
        fillFields(); rerenderItems();
        state.textContent = "";
        host.rerendered(dir);
      } else {
        const dir = await invoke<string>("compilation_raw_render", { request });
        state.textContent = "";
        host.started(dir, request.title, request.items.length);
      }
    } catch (error) {
      state.textContent = message(error);
    } finally {
      working = false;
      renderButton.disabled = false;
    }
  }

  title.addEventListener("input", () => { draft.title = title.value; });
  kind.addEventListener("change", () => { draft.kind = kind.value === "list" ? "list" : "top_n"; rerenderItems(); });
  target.addEventListener("input", () => { draft.targetMinutes = target.value; durationLine.textContent = durationNote(draft); });
  for (const box of [hook, intro, outro]) box.addEventListener("input", () => { readFields(); durationLine.textContent = durationNote(draft); });
  speed.addEventListener("change", () => { draft.speed = Number(speed.value); durationLine.textContent = durationNote(draft); });
  mastering.addEventListener("change", () => { masteringHint.textContent = levelText("compilation", isLevel(mastering.value) ? mastering.value : "balanced"); });
  for (const box of [useAi, suggest, applySuggestion, drafting]) box.addEventListener("change", showAi);
  addItem.addEventListener("click", () => { if (draft.items.length < MAX_ITEMS) { draft.items.push(newItem()); rerenderItems(); } });
  countdown.addEventListener("click", () => { numberRanks(draft, true); rerenderItems(); });
  countup.addEventListener("click", () => { numberRanks(draft, false); rerenderItems(); });
  addFiles.addEventListener("click", async () => {
    try { addPaths(await invoke<string[]>("pick_files")); } catch (error) { sourceNote.textContent = message(error); }
  });
  addFolder.addEventListener("click", async () => {
    try {
      const folder = await invoke<string | null>("pick_source_folder");
      if (!folder) return;
      const found = await invoke<string[]>("expand_video_paths", { paths: [folder] });
      addPaths(found.length ? found : []);
      if (!found.length) sourceNote.textContent = "Thư mục này không có video.";
    } catch (error) { sourceNote.textContent = message(error); }
  });
  fromQueue.addEventListener("click", () => host.fromQueue?.());
  musicPick.addEventListener("click", async () => {
    try {
      const [picked] = await invoke<string[]>("pick_product_files", { kind: "music" });
      if (picked) { draft.music = picked; musicName.textContent = fileName(picked); musicClear.hidden = false; }
    } catch (error) { state.textContent = message(error); }
  });
  musicClear.addEventListener("click", () => { draft.music = null; musicName.textContent = "Không có"; musicClear.hidden = true; });
  renderButton.addEventListener("click", () => void render());
  cancelEdit.addEventListener("click", () => { editing = null; draft = emptyDraft(); fillFields(); rerenderItems(); state.textContent = ""; host.openSetup(); });
  form.addEventListener("focusin", showAi);

  // ---------- Kết quả of a v2 run ----------

  async function finalExport(run: RawRun, thenEdit: boolean): Promise<void> {
    const note = host.result.querySelector<HTMLElement>(".compilation-raw-export-state");
    try {
      let answer = await invoke<Record<string, unknown>>("compilation_raw_finalize", { outputDir: run.dir, accept: false });
      if (answer.exported !== true) {
        const parts = Array.isArray(answer.parts) ? answer.parts as Array<{ part: string; unreviewed: number }> : [];
        const names = parts.map((item) => {
          const part = run.parts.find((candidate) => candidate.id === item.part);
          return `${part ? partName(part, kindOf(run)) : item.part} (${item.unreviewed} câu)`;
        });
        const ok = await confirmDialog({
          title: "Xuất bản cuối khi còn câu chưa duyệt?",
          message: `Còn ${Number(answer.unreviewed) || 0} câu cần xem lại hoặc chưa tìm thấy cảnh khớp ở: ${names.join(", ")}. Bạn có thể mở “Duyệt cảnh” để xem từng câu. Xuất bây giờ nghĩa là bạn chấp nhận các câu đó như hiện tại.`,
          confirmLabel: "Tôi chấp nhận, xuất bản cuối", cancelLabel: "Để tôi duyệt trước",
        });
        if (!ok) { if (note) note.textContent = "Chưa xuất. Bản nháp vẫn ở trong thư mục dự án."; return; }
        answer = await invoke<Record<string, unknown>>("compilation_raw_finalize", { outputDir: run.dir, accept: true });
      }
      if (answer.exported === true && typeof answer.path === "string") {
        if (note) note.textContent = `Đã xuất: ${answer.path}`;
        await showRun(run.dir);
        // the editor opens the programme's own video (compilation.mp4), now that the export gate passed
        if (thenEdit && run.video) host.edit?.(run.video, displayTitle(run));
      }
    } catch (error) {
      if (note) note.textContent = message(error);
    }
  }

  const kindOf = (run: RawRun): RawDraft["kind"] => (((run.spec ?? {}) as Record<string, Record<string, unknown>>).program?.kind === "list" ? "list" : "top_n");
  const displayTitle = (run: RawRun): string => String(((run.spec ?? {}) as Record<string, unknown>).title ?? "Video tổng hợp");

  function editRun(run: RawRun, suggested = false): void {
    const next = specToDraft(run.spec);
    if (!next) { state.textContent = "Không đọc được thiết lập của dự án này."; return; }
    if (suggested && run.suggestion && !applySuggestedOrder(next, run.suggestion.order)) {
      state.textContent = "Gợi ý thứ hạng không còn khớp với các mục; hãy sắp xếp tay.";
    }
    if (suggested) next.applySuggestion = false;  // the order is now the user's, written in the list
    draft = next;
    editing = run.dir;
    fillFields(); rerenderItems();
    host.openSetup();
  }

  function partRow(run: RawRun, part: RawPart, programKind: RawDraft["kind"]): HTMLElement {
    const li = el("li", "compilation-raw-part");
    const name = partName(part, programKind);
    const section = part.id === "hook" || part.id === "intro" || part.id === "outro" ? "Phần chương trình" : programKind === "top_n" ? "Mục xếp hạng" : "Mục";
    const open = part.unreviewed > 0 && !part.accepted;
    li.append(el("span", "hint", `${section} · ${part.id}`), el("b", "", name));
    if (part.narration) li.append(el("p", "compilation-raw-narration", part.narration.length > 220 ? `${part.narration.slice(0, 220)}…` : part.narration));
    const facts = [open ? `${part.unreviewed} câu cần xem lại` : part.unreviewed ? `${part.unreviewed} câu chưa duyệt, bạn đã chấp nhận` : "Mọi câu đạt hoặc đã duyệt",
      `${(part.durationMs / 1000).toFixed(1).replace(".", ",")} giây`, run.rendered.includes(part.id) ? "vừa dựng" : run.reused.includes(part.id) ? "dùng lại" : ""];
    li.append(el("span", open ? "mreview-bad" : "hint", facts.filter(Boolean).join(" · ")));
    const actions = el("div", "compilation-raw-actions");
    const watch = button("▶ Xem phần", "btn");
    watch.disabled = !run.video;
    watch.addEventListener("click", () => { if (run.video) host.seek(run.video, part.startMs); });
    const review = button("Duyệt cảnh", open ? "btn primary" : "btn", "Xem cảnh AI chọn cho từng câu, ghim hoặc loại cảnh");
    review.disabled = !run.video || run.status?.state === "RUNNING";
    review.addEventListener("click", () => void openReviewPanel(invoke, {
      outputDir: run.dir, video: 0, videoPath: run.video ?? "", title: name,
      mediaUrl: (path) => host.mediaUrl ? host.mediaUrl(path) : Promise.resolve(null), cpuOnly: host.cpuOnly,
      rerendered: () => host.rerendered(run.dir),
      part: {
        heading: name, lines: [part.narration], offsetMs: part.startMs,
        review: (args) => invoke("compilation_review", { outputDir: run.dir, part: part.id, ...args }),
        keyframe: (frame) => invoke<ArrayBuffer>("compilation_keyframe", { outputDir: run.dir, name: frame }),
        rerender: () => invoke("compilation_raw_rerender", { outputDir: run.dir, request: null, cpuOnly: host.cpuOnly() }),
        rerenderLabel: "Dựng lại các phần đã đổi", sourceLabel: (id) => sourceName(id, run.spec),
      },
    }).then(() => showRun(run.dir)));
    actions.append(watch, review);
    li.append(actions);
    return li;
  }

  async function showRun(dir: string): Promise<void> {
    let run: RawRun | null;
    try {
      run = parseRawRun(await invoke<unknown>("compilation_raw_open", { outputDir: dir }));
    } catch (error) {
      host.result.replaceChildren(el("p", "tab-note boxed", message(error)));
      return;
    }
    if (!run) { host.result.replaceChildren(el("p", "tab-note boxed", "Không đọc được dự án tổng hợp này.")); return; }
    shownRun = run;
    const nodes: HTMLElement[] = [];
    const status = run.status;
    const programKind = kindOf(run);
    nodes.push(el("p", "tab-note", status ? `${status.state === "COMPLETED" ? "Bản nháp đã dựng." : status.message}` : "Chưa đọc được trạng thái."));
    const stats = renderStats(run);
    if (stats) nodes.push(el("p", "hint", stats));
    if (run.suggestion && !run.applied) {
      const box = el("section", "compilation-group");
      box.append(el("h3", "setup-group", "AI gợi ý thứ hạng"),
        el("p", "hint", "Chỉ là gợi ý: video đang theo thứ tự của bạn. Bấm “Dùng thứ tự gợi ý” để đưa vào danh sách, sửa nếu cần, rồi lưu và dựng lại."));
      const list = el("ol", "compilation-past-parts");
      for (const id of run.suggestion.order) {
        const part = run.parts.find((candidate) => candidate.id === id);
        list.append(el("li", "", `${part?.title ?? id}${run.suggestion.reasons[id] ? ` — ${run.suggestion.reasons[id]}` : ""}`));
      }
      const use = button("Dùng thứ tự gợi ý…", "btn");
      use.addEventListener("click", () => editRun(run!, true));
      box.append(list, use);
      nodes.push(box);
    }
    const parts = el("ol", "compilation-raw-parts");
    parts.append(...run.parts.map((part) => partRow(run!, part, programKind)));
    if (run.parts.length) nodes.push(el("h3", "setup-group", "Các phần"), parts);
    if (run.warnings.length) {
      const warnings = el("ul", "mreview-warnings");
      warnings.append(...run.warnings.slice(0, 10).map((warning) => el("li", "", warning)));
      nodes.push(el("h3", "setup-group", "Cảnh báo"), warnings);
    }
    const open = openForExport(run);
    const actions = el("div", "compilation-raw-actions");
    const edit = button("Sửa chương trình…", "btn", "Sửa lời bình, thứ hạng, nguồn; chỉ phần thay đổi được dựng lại");
    edit.disabled = status?.state === "RUNNING";
    edit.addEventListener("click", () => editRun(run!));
    const again = button("Dựng lại các phần đã đổi", "btn", "Dựng lại với các quyết định duyệt cảnh; phần không đổi được dùng lại");
    again.disabled = status?.state === "RUNNING" || !status;
    again.addEventListener("click", async () => {
      try { await invoke("compilation_raw_rerender", { outputDir: run!.dir, request: null, cpuOnly: host.cpuOnly() }); host.rerendered(run!.dir); }
      catch (error) { exportState.textContent = message(error); }
    });
    const finalButton = button(run.final ? "Xuất bản cuối lại" : "Xuất bản cuối…", "btn primary",
      "Kiểm tra mọi câu đã duyệt (hoặc bạn chấp nhận) rồi lưu final.mp4 trong thư mục dự án");
    finalButton.disabled = status?.state !== "COMPLETED";
    finalButton.addEventListener("click", () => void finalExport(run!, false));
    actions.append(edit, again, finalButton);
    const exportState = el("p", "hint compilation-raw-export-state",
      run.final ? `Bản cuối: ${run.final}` : open.sentences ? `Còn ${open.sentences} câu ở ${open.parts.length} phần cần xem lại trước khi xuất bản cuối (hoặc bạn xác nhận chấp nhận).` : "Sẵn sàng xuất bản cuối.");
    exportState.setAttribute("role", "status");
    nodes.push(actions, exportState);
    if (run.credits.length) {
      const credits = el("ul", "hint");
      credits.append(...run.credits.slice(0, 20).map((credit) => el("li", "", credit)));
      nodes.push(el("h3", "setup-group", "Nguồn"), credits);
    }
    host.result.replaceChildren(...nodes);
  }

  return {
    showDraft: () => { fillFields(); rerenderItems(); void loadVoices(); },
    showRun,
    addPaths,
    busy: () => working,
    editingDir: () => editing,
    exportThenEdit: async (dir: string) => {
      if (!shownRun || shownRun.dir !== dir) await showRun(dir);
      if (shownRun && shownRun.dir === dir) await finalExport(shownRun, true);
    },
  };
}

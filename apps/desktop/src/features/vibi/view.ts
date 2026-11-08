/**
 * The Vibi.pro voice studio (#632): browse thousands of ElevenLabs and
 * MiniMax voices, hear their previews, and set every parameter freely
 * (model, stability, similarity, style, speaker boost, speed; MiniMax speed,
 * pitch, volume) with the Mega Projects preset one click away. Also the
 * "Giọng đọc" tab of Cài đặt (key, balance, where the voice is used) and the
 * small row that shows the chosen voice on the Phim tài liệu and Thêm video pages.
 */
import {
  FALLBACK_MODELS, LIMITS, MEGA_RANGES, SAMPLE_LINE, SAMPLE_LINE_VI, SOURCES, defaultSettings, loadChoice, normalizeSettings,
  parseVoicePage, saveChoice, settingsAdvice, settingsFor, settingsSummary, voiceTraits, withMegaProjects,
  type VibiChoice, type VibiModel, type VibiProvider, type VibiSettings, type VibiSource, type VibiVoice,
} from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type MediaUrl = (path: string) => Promise<string | null>;

function element<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<HTMLElementTagNameMap[K]> = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children);
  return node;
}

const message = (error: unknown) => String((error as Error)?.message ?? error).replace(/^Error:\s*/, "").slice(0, 300);

const PLAY = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><path d="M7 4.5v15l12.5-7.5z" fill="currentColor"/></svg>';
const STOP = '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor"/></svg>';

const EL_ACCENTS = ["", "american", "british", "australian", "irish", "scottish", "indian", "african", "canadian"];
const EL_USES: Array<[string, string]> = [["", "Mọi mục đích"], ["narrative_and_story", "Kể chuyện, thuyết minh"], ["informative_and_educational", "Giáo dục, tài liệu"],
  ["entertainment_and_tv", "Giải trí, TV"], ["advertisement", "Quảng cáo"], ["social_media", "Mạng xã hội"], ["conversational", "Hội thoại"],
  ["characters_and_animation", "Nhân vật, hoạt hình"]];
const EL_SORTS: Array<[string, string]> = [["trending", "Đang thịnh hành"], ["cloned_by_count", "Nhiều người dùng nhất"], ["usage_character_count_1y", "Đọc nhiều nhất năm"], ["created_date", "Mới nhất"]];
const EL_LANGUAGES: Array<[string, string]> = [["", "Mọi ngôn ngữ"], ["en", "Tiếng Anh"], ["vi", "Tiếng Việt"], ["zh", "Tiếng Trung"], ["ja", "Tiếng Nhật"], ["ko", "Tiếng Hàn"], ["es", "Tiếng Tây Ban Nha"], ["fr", "Tiếng Pháp"], ["de", "Tiếng Đức"]];
const MM_LANGUAGES: Array<[string, string]> = [["", "Mọi ngôn ngữ"], ["English", "Tiếng Anh"], ["Vietnamese", "Tiếng Việt"], ["Chinese (Mandarin)", "Tiếng Trung"], ["Japanese", "Tiếng Nhật"], ["Korean", "Tiếng Hàn"]];

function options(select: HTMLSelectElement, items: Array<[string, string]>, value = ""): void {
  select.replaceChildren(...items.map(([id, label]) => element("option", { value: id, textContent: label, selected: id === value })));
}

/** One audio player for every preview in the app: one voice plays at a time. */
function createPlayer(invoke: Invoke, mediaUrl: MediaUrl, onError: (text: string) => void) {
  const audio = new Audio();
  let current: string | null = null;
  const buttons = new Map<HTMLButtonElement, string>();
  const paint = () => {
    for (const [button, key] of buttons) {
      // A list drawn again leaves old buttons behind: forget them once shown and gone.
      if (!button.isConnected) { if (button.dataset.shown) buttons.delete(button); continue; }
      button.dataset.shown = "1";
      const on = key === current;
      button.innerHTML = on ? STOP : PLAY;
      button.classList.toggle("on", on);
      button.title = on ? "Dừng" : "Nghe thử";
    }
  };
  const stop = () => { current = null; audio.pause(); paint(); };
  audio.addEventListener("ended", stop);
  async function play(key: string, path: () => Promise<string>): Promise<void> {
    if (current === key) { stop(); return; }
    current = key;
    audio.pause();
    paint();
    try {
      const url = await mediaUrl(await path());
      if (current !== key) return;
      if (!url) throw new Error("Không phát được bản nghe thử");
      audio.src = url;
      await audio.play();
    } catch (error) {
      if (current === key) stop();
      onError(message(error));
    }
  }
  return {
    button(key: string, label: string, path: () => Promise<string>): HTMLButtonElement {
      const button = element("button", { type: "button", className: "icon-btn vibi-play" });
      button.setAttribute("aria-label", label);
      button.innerHTML = PLAY;
      button.title = "Nghe thử";
      buttons.set(button, key);
      button.addEventListener("click", (event) => { event.stopPropagation(); void play(key, path); });
      paint();
      return button;
    },
    preview: (url: string) => invoke<string>("vibi_preview", { url }),
    stop,
  };
}

export type VibiStudio = {
  /** Pick a voice and its settings; resolves with the choice, or null when closed without one. */
  open(initial: VibiSettings | null, context?: "dubbing" | "documentary"): Promise<VibiSettings | null>;
};

export function createVibiStudio(invoke: Invoke, mediaUrl: MediaUrl, openSettings: () => void): VibiStudio {
  const state = element("p", { className: "vibi-state", role: "status" });
  const say = (text: string) => { state.textContent = text; };
  const player = createPlayer(invoke, mediaUrl, say);

  // -- left: the voice browser ------------------------------------------------------
  const tabs = element("div", { className: "vibi-tabs", role: "tablist" });
  const search = element("input", { type: "search", placeholder: "Tìm giọng: deep, narrator, documentary…", className: "vibi-search" });
  search.setAttribute("aria-label", "Tìm giọng");
  const gender = element("select");
  gender.setAttribute("aria-label", "Giới tính");
  const language = element("select");
  language.setAttribute("aria-label", "Ngôn ngữ");
  const accent = element("select");
  accent.setAttribute("aria-label", "Giọng vùng");
  const age = element("select");
  age.setAttribute("aria-label", "Tuổi");
  const useCase = element("select");
  useCase.setAttribute("aria-label", "Mục đích");
  const sort = element("select");
  sort.setAttribute("aria-label", "Sắp xếp");
  options(age, [["", "Mọi độ tuổi"], ["young", "Trẻ"], ["middle_aged", "Trung niên"], ["old", "Lớn tuổi"]]);
  options(useCase, EL_USES);
  options(sort, EL_SORTS, "trending");
  options(accent, EL_ACCENTS.map((item) => [item, item ? item[0].toUpperCase() + item.slice(1) : "Mọi giọng vùng"]));
  const filters = element("div", { className: "vibi-filters" }, search, gender, language, accent, age, useCase, sort);
  const list = element("div", { className: "vibi-list", role: "listbox" });
  list.setAttribute("aria-label", "Danh sách giọng");
  const more = element("button", { type: "button", className: "btn vibi-more", textContent: "Tải thêm giọng", hidden: true });
  const count = element("p", { className: "hint vibi-count" });
  const browser = element("section", { className: "vibi-browser" }, tabs, filters, count, list, more);

  // -- right: the settings ------------------------------------------------------------
  const chosenName = element("strong", { className: "vibi-chosen-name", textContent: "Chưa chọn giọng" });
  const chosenTraits = element("span", { className: "hint" });
  const chosen = element("div", { className: "vibi-chosen" }, chosenName, chosenTraits);
  const model = element("select");
  model.setAttribute("aria-label", "Model");
  const modelHint = element("span", { className: "hint" });
  const mega = element("button", { type: "button", className: "btn small vibi-mega", textContent: "Preset Mega Projects" });
  mega.title = "Turbo v2.5 · ổn định 45 % · rõ 78 % · kịch tính 15 %";
  const reset = element("button", { type: "button", className: "link", textContent: "Về mặc định" });
  const presets = element("div", { className: "vibi-presets" }, mega, reset);
  const sliders = element("div", { className: "vibi-sliders" });
  const boost = element("input", { type: "checkbox" });
  const boostRow = element("label", { className: "vibi-check" }, boost, " Speaker boost (giọng đầy, gần người nghe hơn)");
  const advice = element("ul", { className: "vibi-advice" });
  const sampleText = element("textarea", { rows: 2, className: "vibi-sample-text" });
  sampleText.setAttribute("aria-label", "Câu nghe thử");
  const sampleButton = element("button", { type: "button", className: "btn", textContent: "Nghe thử với cài đặt này" });
  const sampleNote = element("span", { className: "hint", textContent: "Tốn một ít credit, mỗi cài đặt chỉ một lần." });
  const tip = element("p", { className: "hint vibi-tip", textContent: "Mẹo: ElevenLabs ngắt nghỉ theo dấu câu. Đặt “...” hoặc “—” trước con số khủng để tạo nhịp nghỉ gây tò mò." });
  const panel = element("section", { className: "vibi-panel" },
    chosen, element("label", { className: "vibi-field" }, "Model", model, modelHint), presets, sliders, boostRow, advice,
    element("div", { className: "vibi-sample" }, sampleText, element("div", { className: "vibi-sample-row" }, sampleButton, sampleNote)), tip);

  const use = element("button", { type: "button", className: "btn primary", textContent: "Dùng giọng này" });
  const cancel = element("button", { type: "button", className: "btn", textContent: "Hủy" });
  const keyLink = element("button", { type: "button", className: "link", textContent: "Khóa Vibi.pro trong Cài đặt" });
  const close = element("button", { type: "button", className: "icon-btn", innerHTML: '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' });
  close.setAttribute("aria-label", "Đóng");
  const balance = element("span", { className: "vibi-balance hint" });
  const dialog = element("div", { className: "review vibi-studio", hidden: true, role: "dialog" },
    element("div", { className: "review-panel vibi-studio-panel" },
      element("header", { className: "review-head" },
        element("div", {}, element("span", { className: "eyebrow", textContent: "Vibi.pro · ElevenLabs · MiniMax" }),
          element("h2", { textContent: "Chọn giọng và chỉnh thông số" }), balance),
        close),
      element("div", { className: "vibi-body" }, browser, panel),
      element("footer", { className: "vibi-foot" }, state, keyLink, cancel, use)));
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", "Chọn giọng Vibi.pro");
  document.body.append(dialog);

  let source: VibiSource = "shared";
  let page = 0;
  let ticket = 0;
  let current: VibiSettings | null = null;
  let models: Partial<Record<VibiProvider, VibiModel[]>> = {};
  let context: "dubbing" | "documentary" = "documentary";
  let resolve: ((value: VibiSettings | null) => void) | null = null;
  const traits = new Map<string, string>();

  for (const [id, info] of Object.entries(SOURCES) as Array<[VibiSource, (typeof SOURCES)[VibiSource]]>) {
    const tab = element("button", { type: "button", className: "vibi-tab", textContent: info.label, title: info.hint });
    tab.dataset.source = id;
    tab.setAttribute("role", "tab");
    tab.addEventListener("click", () => { source = id; paintTabs(); void load(true); });
    tabs.append(tab);
  }

  function paintTabs(): void {
    for (const tab of tabs.querySelectorAll<HTMLButtonElement>(".vibi-tab")) tab.setAttribute("aria-selected", String(tab.dataset.source === source));
    const eleven = SOURCES[source].provider === "elevenlabs";
    options(gender, eleven ? [["", "Mọi giới tính"], ["male", "Nam"], ["female", "Nữ"], ["neutral", "Trung tính"]] : [["", "Mọi giới tính"], ["Male", "Nam"], ["Female", "Nữ"]], gender.value);
    options(language, eleven ? EL_LANGUAGES : MM_LANGUAGES, eleven ? (context === "dubbing" ? "vi" : "en") : (context === "dubbing" ? "Vietnamese" : "English"));
    const shared = source === "shared";
    accent.hidden = !(shared || source === "default");
    age.hidden = !shared;
    useCase.hidden = !shared;
    sort.hidden = !shared;
    language.hidden = source === "default" || source === "cloned";
    gender.hidden = source === "cloned";
  }

  async function load(fresh: boolean): Promise<void> {
    const mine = ++ticket;
    if (fresh) { page = 0; list.replaceChildren(); }
    more.hidden = true;
    say("Đang tải danh sách giọng…");
    try {
      const value = await invoke<unknown>("vibi_call", { request: { action: "voices", provider: SOURCES[source].provider, source, page,
        filters: { search: search.value.trim(), gender: gender.value, language: language.hidden ? "" : language.value, accent: accent.hidden ? "" : accent.value,
          age: age.hidden ? "" : age.value, use_case: useCase.hidden ? "" : useCase.value, sort: sort.hidden ? "" : sort.value } } });
      if (mine !== ticket) return;
      const result = parseVoicePage(value);
      for (const voice of result.voices) list.append(voiceRow(voice));
      more.hidden = !result.hasMore;
      const shown = list.childElementCount;
      count.textContent = shown ? `${shown}${result.total ? ` / ${result.total}` : result.hasMore ? "+" : ""} giọng` : "";
      say(shown ? "" : "Không có giọng nào khớp bộ lọc.");
    } catch (error) {
      if (mine !== ticket) return;
      say(message(error));
    }
  }

  function voiceRow(voice: VibiVoice): HTMLElement {
    traits.set(`${voice.provider}:${voice.voice_id}`, voiceTraits(voice));
    const row = element("div", { className: "vibi-voice", role: "option", tabIndex: 0 });
    row.dataset.voice = voice.voice_id;
    const avatar = element("span", { className: "vibi-avatar", textContent: voice.name.slice(0, 1).toUpperCase() });
    const tags = element("span", { className: "vibi-tags" }, ...voice.tags.slice(0, 3).map((tag) => element("span", { className: "vibi-tag", textContent: tag })));
    const text = element("span", { className: "vibi-voice-text" },
      element("strong", { textContent: voice.name }), element("span", { className: "hint", textContent: voiceTraits(voice) }),
      ...(voice.description ? [element("span", { className: "vibi-desc", textContent: voice.description })] : []), tags);
    const play = voice.preview_url ? player.button(`p:${voice.preview_url}`, `Nghe thử giọng ${voice.name}`, () => player.preview(voice.preview_url))
      : element("span", { className: "vibi-play-none", title: "Giọng này không có bản nghe thử" });
    row.append(play, avatar, text);
    const pick = () => choose(voice);
    row.addEventListener("click", pick);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); pick(); } });
    row.setAttribute("aria-selected", String(current?.voice_id === voice.voice_id));
    return row;
  }

  function choose(voice: VibiVoice): void {
    const base = current && current.provider === voice.provider ? current : defaultSettings(voice.provider);
    current = { ...base, voice_id: voice.voice_id, voice_name: voice.name, preview_url: voice.preview_url || undefined };
    for (const row of list.querySelectorAll<HTMLElement>(".vibi-voice")) row.setAttribute("aria-selected", String(row.dataset.voice === voice.voice_id));
    void loadModels(voice.provider);
    paintPanel();
  }

  async function loadModels(provider: VibiProvider): Promise<void> {
    if (models[provider]) return;
    try {
      const value = await invoke<{ models?: VibiModel[] }>("vibi_call", { request: { action: "models", provider } });
      models = { ...models, [provider]: Array.isArray(value.models) && value.models.length ? value.models : FALLBACK_MODELS[provider] };
    } catch {
      models = { ...models, [provider]: FALLBACK_MODELS[provider] };
    }
    paintPanel();
  }

  function slider(key: "stability" | "similarity_boost" | "style" | "speed" | "pitch" | "vol", label: string, low: number, high: number, step: number,
                  format: (value: number) => string, hint: string): HTMLElement {
    const value = current![key] as number;
    const input = element("input", { type: "range", min: String(low), max: String(high), step: String(step), value: String(value) });
    input.setAttribute("aria-label", label);
    const number = element("input", { type: "number", min: String(low), max: String(high), step: String(step), className: "vibi-number",
      value: String(key === "stability" || key === "similarity_boost" || key === "style" ? Math.round(value * 100) : value) });
    number.setAttribute("aria-label", `${label} (số)`);
    const shown = element("span", { className: "vibi-value", textContent: format(value) });
    const percent = key === "stability" || key === "similarity_boost" || key === "style";
    if (percent) { number.min = "0"; number.max = "100"; number.step = "1"; }
    const apply = (raw: number) => {
      if (!current || !Number.isFinite(raw)) return;
      current = normalizeSettings({ ...current, [key]: raw }) ?? current;
      const next = current[key] as number;
      input.value = String(next);
      number.value = String(percent ? Math.round(next * 100) : next);
      shown.textContent = format(next);
      paintAdvice();
    };
    input.addEventListener("input", () => apply(Number(input.value)));
    number.addEventListener("change", () => apply(percent ? Number(number.value) / 100 : Number(number.value)));
    const range = (MEGA_RANGES as Record<string, readonly [number, number]>)[key];
    const band = range && current!.provider === "elevenlabs"
      ? element("span", { className: "vibi-band", title: "Khoảng tối ưu Mega Projects" }) : null;
    if (band) { band.style.left = `${((range[0] - low) / (high - low)) * 100}%`; band.style.width = `${((range[1] - range[0]) / (high - low)) * 100}%`; }
    const track = element("span", { className: "vibi-track" }, input, ...(band ? [band] : []));
    return element("div", { className: "vibi-slider" },
      element("div", { className: "vibi-slider-head" }, element("span", { textContent: label }), shown, number),
      track, element("span", { className: "hint", textContent: hint }));
  }

  function paintAdvice(): void {
    advice.replaceChildren(...(current ? settingsAdvice(current) : []).map((text) => element("li", { textContent: text })));
  }

  function paintPanel(): void {
    const ready = Boolean(current?.voice_id);
    use.disabled = !ready;
    sampleButton.disabled = !ready;
    mega.disabled = !ready || current?.provider !== "elevenlabs";
    reset.disabled = !ready;
    model.disabled = !ready;
    if (!current || !ready) {
      chosenName.textContent = "Chưa chọn giọng";
      chosenTraits.textContent = "Bấm một giọng bên trái để chọn, nút ▶ để nghe thử (miễn phí).";
      sliders.replaceChildren();
      boostRow.hidden = true;
      advice.replaceChildren();
      return;
    }
    chosenName.textContent = current.voice_name || current.voice_id;
    chosenTraits.textContent = traits.get(`${current.provider}:${current.voice_id}`) ?? (current.provider === "elevenlabs" ? "ElevenLabs" : "MiniMax");
    const list = models[current.provider] ?? FALLBACK_MODELS[current.provider];
    const known = list.some((item) => item.model_id === current!.model_id);
    model.replaceChildren(...(known ? [] : [element("option", { value: current.model_id, textContent: current.model_id })]),
      ...list.map((item) => element("option", { value: item.model_id, textContent: item.name, title: item.description })));
    model.value = current.model_id;
    const info = list.find((item) => item.model_id === current!.model_id);
    modelHint.textContent = info?.description ?? "";
    const eleven = current.provider === "elevenlabs";
    const pct = (value: number) => `${Math.round(value * 100)} %`;
    if (eleven) {
      const style = info?.can_use_style !== false;
      sliders.replaceChildren(
        slider("stability", "Stability (độ ổn định)", 0, 1, 0.01, pct, "Thấp: nhấn nhá, biểu cảm. Cao: đều, ổn định. Mega Projects: 40–50 %."),
        slider("similarity_boost", "Clarity + Similarity (độ rõ, giống giọng gốc)", 0, 1, 0.01, pct, "Giữ giọng trong, sắc khi đè lên nhạc nền. Mega Projects: 75–80 %."),
        ...(style ? [slider("style", "Style exaggeration (độ kịch tính)", 0, 1, 0.01, pct, "Nhích nhẹ để tăng kịch tính. Mega Projects: 10–20 %.")] : []),
        slider("speed", "Tốc độ", LIMITS.elevenlabs.speed[0], LIMITS.elevenlabs.speed[1], 0.01, (value) => `${value}×`, "0,7× đến 1,2×; 1× là tự nhiên."));
      boostRow.hidden = info?.can_use_speaker_boost === false;
      boost.checked = current.use_speaker_boost;
    } else {
      sliders.replaceChildren(
        slider("speed", "Tốc độ", LIMITS.minimax.speed[0], LIMITS.minimax.speed[1], 0.05, (value) => `${value}×`, "0,5× đến 2×."),
        slider("pitch", "Cao độ", LIMITS.minimax.pitch[0], LIMITS.minimax.pitch[1], 1, (value) => `${value > 0 ? "+" : ""}${value}`, "Âm: trầm hơn, dương: cao hơn."),
        slider("vol", "Âm lượng", 0.1, 3, 0.05, (value) => `${value}`, "1 là gốc."));
      boostRow.hidden = true;
    }
    paintAdvice();
  }

  model.addEventListener("change", () => { if (current) { current = { ...current, model_id: model.value }; paintPanel(); } });
  boost.addEventListener("change", () => { if (current) current = { ...current, use_speaker_boost: boost.checked }; });
  mega.addEventListener("click", () => { if (current) { current = withMegaProjects(current); paintPanel(); say("Đã áp dụng preset Mega Projects."); } });
  reset.addEventListener("click", () => {
    if (current) { current = { ...defaultSettings(current.provider), voice_id: current.voice_id, voice_name: current.voice_name, preview_url: current.preview_url }; paintPanel(); }
  });
  sampleButton.addEventListener("click", () => {
    if (!current) return;
    const settings = settingsFor(current, context);
    const text = sampleText.value.trim();
    sampleButton.disabled = true;
    say("Đang tạo câu nghe thử trên Vibi.pro…");
    void (async () => {
      try {
        const path = await invoke<string>("vibi_sample", { settings, text });
        const url = await mediaUrl(path);
        if (!url) throw new Error("Không phát được câu nghe thử");
        player.stop();
        const audio = new Audio(url);
        await audio.play();
        say("");
      } catch (error) { say(message(error)); }
      finally { sampleButton.disabled = !current; }
    })();
  });
  let searchTimer = 0;
  search.addEventListener("input", () => { clearTimeout(searchTimer); searchTimer = window.setTimeout(() => void load(true), 400); });
  for (const select of [gender, language, accent, age, useCase, sort]) select.addEventListener("change", () => void load(true));
  more.addEventListener("click", () => { page += 1; void load(false); });

  function finish(value: VibiSettings | null): void {
    player.stop();
    dialog.hidden = true;
    resolve?.(value);
    resolve = null;
  }
  use.addEventListener("click", () => finish(current));
  cancel.addEventListener("click", () => finish(null));
  close.addEventListener("click", () => finish(null));
  keyLink.addEventListener("click", () => { finish(null); openSettings(); });
  dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") finish(null); });

  return {
    open(initial, where = "documentary") {
      context = where;
      current = initial ? { ...initial } : null;
      source = initial?.provider === "minimax" ? "system" : "shared";
      sampleText.value = where === "dubbing" ? SAMPLE_LINE_VI : SAMPLE_LINE;
      paintTabs();
      paintPanel();
      dialog.hidden = false;
      say("");
      balance.textContent = "";
      void invoke<{ credit_balance?: number | null; email?: string }>("vibi_call", { request: { action: "me" } })
        .then((me) => { balance.textContent = typeof me.credit_balance === "number" ? `Còn ${me.credit_balance.toLocaleString("vi-VN")} credit` : ""; })
        .catch((error) => say(message(error)));
      if (current) void loadModels(current.provider);
      void load(true);
      search.focus();
      return new Promise((done) => { resolve?.(null); resolve = done; });
    },
  };
}

/** The voice shown on a page, with "Chọn giọng…" and an on/off switch for that page's use. */
export function vibiVoiceRow(studio: VibiStudio, use: "dubbing" | "documentary", onChange: (choice: VibiChoice) => void): {
  element: HTMLElement; refresh(): void; choice(): VibiChoice;
} {
  const toggle = element("input", { type: "checkbox", className: "switch" });
  toggle.setAttribute("role", "switch");
  const title = element("strong", { textContent: use === "dubbing" ? "Giọng Vibi.pro (ElevenLabs, MiniMax)" : "Đọc bằng giọng Vibi.pro" });
  const summary = element("small", { className: "vibi-row-summary" });
  const pick = element("button", { type: "button", className: "btn small", textContent: "Chọn giọng…" });
  const fallback = element("small", { className: "vibi-row-fallback", hidden: true,
    textContent: "Giọng trong máy bên dưới chỉ dùng khi Vibi.pro lỗi hoặc hết credit." });
  const root = element("div", { className: "vibi-row" },
    element("label", { className: "switch-row" }, element("span", {}, title, summary), toggle), pick, fallback);
  function refresh(): void {
    const choice = loadChoice();
    toggle.checked = choice.use[use];
    // In the dubbing options the built-in voices below become the fallback.
    fallback.hidden = use !== "dubbing" || !toggle.checked || !root.parentElement?.classList.contains("voice-picker");
    root.parentElement?.classList.toggle("vibi-on", toggle.checked);
    summary.textContent = choice.settings ? settingsSummary(choice.settings) : "Chưa chọn giọng: hàng nghìn giọng ElevenLabs và MiniMax, chỉnh thông số tự do";
    pick.textContent = choice.settings ? "Đổi giọng…" : "Chọn giọng…";
  }
  async function choose(): Promise<void> {
    const choice = loadChoice();
    const picked = await studio.open(choice.settings, use);
    if (!picked) { refresh(); return; }
    const next = { settings: picked, use: { ...choice.use, [use]: true } };
    saveChoice(next);
    refresh();
    onChange(next);
  }
  toggle.addEventListener("change", () => {
    const choice = loadChoice();
    if (toggle.checked && !choice.settings) { toggle.checked = false; void choose(); return; }
    const next = { ...choice, use: { ...choice.use, [use]: toggle.checked } };
    saveChoice(next);
    refresh();
    onChange(next);
  });
  pick.addEventListener("click", () => void choose());
  refresh();
  return { element: root, refresh, choice: loadChoice };
}

/** Cài đặt › Giọng đọc: the Vibi.pro key, its balance, the chosen voice and where it is used. */
export function mountVibiSettings(container: HTMLElement, invoke: Invoke, studio: VibiStudio, onChange: () => void): { refresh(): void } {
  const status = element("span", { className: "ai-key-status", textContent: "Đang kiểm tra…" });
  const head = element("div", { className: "ai-key-head" }, element("span", { textContent: "Khóa API Vibi.pro" }), status);
  const page = element("button", { type: "button", className: "link ai-key-page", textContent: "Mở trang lấy khóa" });
  const where = element("p", { className: "hint" }, "Đăng nhập vibi.pro, mở trang Docs: khóa (bắt đầu bằng sk_) nằm ở thanh trên cùng, bấm Copy. ", page);
  const input = element("input", { type: "password", autocomplete: "off", placeholder: "sk_…" });
  input.setAttribute("aria-label", "Khóa API Vibi.pro");
  const save = element("button", { type: "button", className: "btn primary", textContent: "Lưu" });
  const check = element("button", { type: "button", className: "btn", textContent: "Kiểm tra khóa" });
  const remove = element("button", { type: "button", className: "link", textContent: "Xóa khóa", hidden: true });
  const state = element("p", { className: "discover-state", role: "status" });
  const keyCard = element("div", { className: "ai-key-card vibi-key-card" }, head, where,
    element("div", { className: "post-key-actions" }, input, save, check, remove), state);
  // One voice serves both rows: a change in either repaints both and the pages' rows.
  const changed = () => { dubbing.refresh(); documentary.refresh(); onChange(); };
  const dubbing = vibiVoiceRow(studio, "dubbing", changed);
  const documentary = vibiVoiceRow(studio, "documentary", changed);
  const note = element("p", { className: "hint", textContent: "Khóa được Windows mã hóa và chỉ gửi tới Vibi.pro khi tạo giọng. Mỗi câu đã đọc được lưu lại trên máy, đọc lại không tốn thêm credit. Hết credit hoặc khóa lỗi khi lồng tiếng: DubFlow tự dùng giọng có sẵn và báo lý do." });
  container.replaceChildren(keyCard, element("div", { className: "vibi-uses" }, dubbing.element, documentary.element), note);

  let saved: string | null = null;
  async function refresh(): Promise<void> {
    try {
      const keys = await invoke<Record<string, string | null>>("ai_key_status");
      saved = keys.vibi ?? null;
    } catch { saved = null; }
    status.textContent = saved ? `Đã lưu (${saved})` : "Chưa có khóa";
    status.classList.toggle("saved", Boolean(saved));
    input.placeholder = saved ? "Dán khóa mới để thay" : "sk_…";
    remove.hidden = !saved;
    check.disabled = !saved && !input.value.trim();
    dubbing.refresh();
    documentary.refresh();
  }
  async function checkKey(key?: string): Promise<void> {
    state.textContent = "Đang kiểm tra với Vibi.pro…";
    try {
      const me = await invoke<{ credit_balance?: number | null; email?: string }>("vibi_call", { request: { action: "me" }, key: key ?? null });
      state.textContent = `Khóa dùng được${me.email ? ` (${me.email})` : ""}${typeof me.credit_balance === "number" ? `: còn ${me.credit_balance.toLocaleString("vi-VN")} credit` : ""}.`;
    } catch (error) { state.textContent = message(error); }
  }
  page.addEventListener("click", () => { invoke("ai_key_page", { provider: "vibi" }).catch((error) => { state.textContent = `Không mở được trang: ${message(error)}`; }); });
  input.addEventListener("input", () => { check.disabled = !saved && !input.value.trim(); });
  save.addEventListener("click", async () => {
    const key = input.value.trim();
    if (!key) { state.textContent = "Dán khóa Vibi.pro vào ô trước."; return; }
    try {
      await invoke("ai_key_set", { provider: "vibi", key });
      input.value = "";
      await refresh();
      await checkKey();
    } catch (error) { state.textContent = message(error); }
  });
  check.addEventListener("click", () => void checkKey(input.value.trim() || undefined));
  remove.addEventListener("click", async () => {
    try { await invoke("ai_key_delete", { provider: "vibi" }); state.textContent = "Đã xóa khóa Vibi.pro."; await refresh(); }
    catch (error) { state.textContent = message(error); }
  });
  void refresh();
  return { refresh: () => void refresh() };
}

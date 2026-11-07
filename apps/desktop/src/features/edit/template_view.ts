/**
 * The Mẫu mode of the editor (Issue #426), for Bán hàng videos: what the
 * video was made from (template, name, price, highlights, background and
 * its focal point, voice, music, script) with the Bán hàng page's controls
 * and live preview. "Áp dụng mẫu" makes the video again in a new run beside
 * the old one, reusing the narration when the script, voice and speed are
 * unchanged, and carries the edit draft over to it.
 */
import { WORDS_PER_SECOND } from "../product/ai.ts";
import {
  backgroundFit, backgroundHint, backgroundItems, captionAt, createImages, mountPreviewCard, PREVIEW_SIDE, previewCardHtml,
  templateButtonsHtml, templateNote, wireTemplates,
} from "../product/look.ts";
import {
  buildRequest, CENTER, fileName, formatFeatures, keepsVoice, parseBackgrounds, parseFeatures, parseProductSpec, parseStatus, TEMPLATES,
  type Focus, type LibraryBackground, type ProductSpec, type SpecInfo, type Template,
} from "../product/model.ts";
import { parseVoiceCatalog, voiceOptionGroups } from "../voices/model.ts";
import { formatClock, outputDuration, retargetEdit, serializeProject, type Edit, type Media } from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

/** The video the editor has open, as the Mẫu mode needs it. */
export type TemplateTarget = { kind: string; outputPath: string; displayName: string };

export type TemplateContext = {
  /** The draft to carry to the new render, with its bookkeeping. */
  draft(): { edit: Edit; changes: number; selected: string | null; recent: Media[] } | null;
  /** Save the draft now. */
  flush(): Promise<void>;
  pause(): void;
  cpuOnly(): boolean;
  /** Leave this video and open the new render in the Mẫu mode. */
  reopen(target: TemplateTarget): Promise<void>;
  /** The panel's state changed (the unsaved badge, the exit question). */
  changed(): void;
};

export type TemplateMode = {
  begin(target: TemplateTarget): void;
  end(): void;
  /** Draw the panel when shown. */
  render(shown: boolean): void;
  /** Changes not applied yet. */
  dirty(): boolean;
  /** A new render is being made. */
  readonly busy: boolean;
};

const CURRENT = "current";
const TICK_MS = 200;
const POLL_MS = 1500;

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing template element: ${selector}`);
  return node;
};

export function mountTemplate(invoke: Invoke, context: TemplateContext): TemplateMode {
  const root = $("#edit-template");
  root.innerHTML = `
    <p id="tpl-intro" class="edit-note"></p>
    <figure class="tpl-preview">${previewCardHtml("tpl", "Xem trước mẫu mới")}</figure>
    <fieldset id="tpl-fields" class="tpl-fields">
      <legend class="visually-hidden">Mẫu video</legend>
      <div class="edit-field">
        <span class="edit-label">Mẫu</span>
        <div id="tpl-templates" class="product-templates tpl-templates" role="radiogroup" aria-label="Mẫu video">${templateButtonsHtml()}</div>
        <small id="tpl-template-note" class="edit-note"></small>
        <small id="tpl-media-note" class="edit-note warn" hidden></small>
      </div>
      <div class="tpl-pair">
        <label class="edit-field">Tên sản phẩm<input id="tpl-name" type="text" maxlength="120" /></label>
        <label class="edit-field">Giá<input id="tpl-price" type="text" maxlength="60" /></label>
      </div>
      <label class="edit-field">Điểm nổi bật (mỗi dòng một ý)<textarea id="tpl-features" rows="3"></textarea>
        <small class="edit-note">Mẫu “Chỉ chữ” hiện mỗi ý thành một thẻ chữ.</small></label>
      <div id="tpl-background-block" class="edit-field">
        <span class="edit-label">Ảnh nền <small id="tpl-background-size" class="edit-note"></small></span>
        <ul id="tpl-backgrounds" class="product-backgrounds" role="radiogroup" aria-label="Ảnh nền"></ul>
        <small id="tpl-background-hint" class="edit-note"></small>
      </div>
      <label class="edit-field">Kịch bản <span id="tpl-words" class="edit-note"></span><textarea id="tpl-script" rows="6" maxlength="3000"></textarea></label>
      <div class="tpl-pair">
        <label class="edit-field">Giọng đọc<select id="tpl-voice"></select></label>
        <label class="edit-field">Tốc độ đọc<select id="tpl-speed"><option value="1">Bình thường</option><option value="1.1">Hơi nhanh</option><option value="1.2">Nhanh</option></select></label>
      </div>
      <div class="edit-field">
        <span class="edit-label">Nhạc nền <span class="tpl-music"><span id="tpl-music-name">Không có</span>
          <button id="tpl-music" class="link" type="button">Chọn…</button><button id="tpl-music-clear" class="link" type="button" hidden>Bỏ</button></span></span>
        <label class="edit-label">Âm lượng nhạc <output id="tpl-volume-value">15%</output></label>
        <input id="tpl-volume" type="range" min="0" max="50" value="15" aria-label="Âm lượng nhạc nền" />
      </div>
    </fieldset>
    <div class="tpl-foot">
      <p id="tpl-voice-note" class="edit-note"></p>
      <p id="tpl-state" class="edit-hint" role="status"></p>
      <div class="tpl-actions">
        <button id="tpl-reset" class="btn" type="button">Hoàn lại</button>
        <button id="tpl-apply" class="btn primary" type="button">Áp dụng mẫu</button>
        <button id="tpl-cancel" class="btn danger" type="button" hidden>Hủy</button>
      </div>
    </div>`;
  const images = createImages(invoke);
  const field = <T extends HTMLElement>(name: string) => $<T>(`#tpl-${name}`);
  let target: TemplateTarget | null = null;
  let info: SpecInfo | null = null;
  let form: ProductSpec | null = null;
  let library: LibraryBackground[] = [];
  /** The background in use when it is not in the library (or not found yet). */
  let current: LibraryBackground | null = null;
  let token = 0;
  let shown = false;
  let running = "";
  let polling = 0;
  let notice = "";
  let playStarted = performance.now();

  const templates = wireTemplates(field("templates"), (next) => {
    if (!form) return;
    form.template = next;
    playStarted = performance.now();
    renderForm();
  });

  function chosenBackground(): LibraryBackground | null {
    if (!form?.background || form.template === "toan-man-hinh") return null;
    const path = form.background.toLocaleLowerCase("en");
    return library.find((entry) => entry.path.toLocaleLowerCase("en") === path) ?? (current && current.path === form.background ? current : null);
  }

  function totalMs(): number {
    if (form && info && form.script.trim() === info.spec.script.trim() && form.speed === info.spec.speed && info.durationMs) return info.durationMs;
    const words = form?.script.split(/\s+/).filter(Boolean).length ?? 0;
    return Math.max(3000, Math.round((words / (WORDS_PER_SECOND * (form?.speed ?? 1))) * 1000) + 1000);
  }

  const card = mountPreviewCard({
    preview: field("preview"), stage: field("stage"), base: field("stage-base"), card: field("stage-card"), caption: field("stage-caption"),
    fill: field("progress-fill"), time: field("progress-time"),
  }, {
    visible: () => shown && !!form,
    state: () => {
      const spec = form!;
      const total = totalMs();
      const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const time = still ? 0 : (performance.now() - playStarted) % total;
      const first = spec.template === "chi-chu" ? undefined : spec.visuals[0];
      const chosen = chosenBackground();
      const fit = backgroundFit(spec.template, chosen);
      const image = (path: string) => images.now(path, PREVIEW_SIDE, () => card.update());
      return {
        template: spec.template, name: spec.name, price: spec.price, features: spec.features, caption: captionAt(spec.script, time, total),
        media: first ? { url: image(first), label: fileName(first) } : null,
        background: chosen && fit ? { url: image(chosen.path), width: chosen.width, height: chosen.height, focus: spec.focus, box: fit.box } : null,
        timeMs: time, totalMs: total,
      };
    },
    background: () => {
      const chosen = chosenBackground();
      const fit = form ? backgroundFit(form.template, chosen) : null;
      return chosen && fit && form ? { entry: chosen, box: fit.box, focus: form.focus } : null;
    },
    focus: (focus: Focus, save) => {
      if (!form) return;
      form.focus = focus;
      if (save) renderFoot();
    },
  });

  function same(a: ProductSpec, b: ProductSpec): boolean {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function dirty(): boolean {
    return !!form && !!info && !same(form, info.spec);
  }

  function renderBackgrounds(): void {
    if (!form) return;
    const meta = TEMPLATES.find((item) => item.id === form!.template)!;
    field("background-block").hidden = !meta.size;
    field("background-size").textContent = meta.size ? `cỡ đẹp nhất ${meta.size}` : "";
    const chosen = chosenBackground();
    const shownLibrary = current && !library.some((entry) => entry.path.toLocaleLowerCase("en") === current!.path.toLocaleLowerCase("en"))
      ? [current, ...library] : library;
    field("backgrounds").replaceChildren(...backgroundItems({
      template: form.template, library: shownLibrary, chosen, images,
      choose: (id) => {
        if (!form) return;
        const entry = shownLibrary.find((item) => item.id === id) ?? null;
        form.background = entry?.path ?? null;
        form.focus = entry && info?.spec.background === entry.path ? info.spec.focus : CENTER;
        renderForm();
      },
      remove: (entry) => void removeBackground(entry),
      add: () => void addBackground(),
    }));
    const missing = !!form.background && !chosen && form.template !== "toan-man-hinh";
    field("background-hint").textContent = missing ? "Ảnh nền của bản này không còn trên máy; hãy chọn ảnh khác." : backgroundHint(form.template, chosen);
  }

  function renderForm(): void {
    if (!form) return;
    templates.render(form.template);
    field("template-note").textContent = templateNote(form.template);
    const needsMedia = form.template !== "chi-chu" && !form.visuals.length;
    field("media-note").hidden = !needsMedia;
    field("media-note").textContent = needsMedia ? "Video này được tạo không có ảnh hay clip nên chỉ dựng lại được bằng mẫu “Chỉ chữ”. Muốn thêm ảnh, hãy tạo video mới ở trang Bán hàng." : "";
    const values: Array<[string, string]> = [["name", form.name], ["price", form.price], ["script", form.script]];
    for (const [name, value] of values) {
      const input = field<HTMLInputElement | HTMLTextAreaElement>(name);
      if (document.activeElement !== input) input.value = value;
    }
    const features = field<HTMLTextAreaElement>("features");
    if (document.activeElement !== features) features.value = formatFeatures(form.features);
    field<HTMLSelectElement>("speed").value = String(form.speed);
    const voice = field<HTMLSelectElement>("voice");
    if ([...voice.options].some((option) => option.value === form!.voiceId)) voice.value = form.voiceId;
    field("music-name").textContent = form.music ? fileName(form.music) : "Không có";
    field("music-name").title = form.music ?? "";
    field("music-clear").hidden = !form.music;
    field<HTMLInputElement>("volume").value = String(Math.round(form.musicVolume * 100));
    field("volume-value").textContent = `${Math.round(form.musicVolume * 100)}%`;
    renderBackgrounds();
    renderFoot();
    card.update();
  }

  function renderFoot(): void {
    const words = form?.script.split(/\s+/).filter(Boolean).length ?? 0;
    field("words").textContent = words ? `${words} chữ` : "";
    const busy = !!running;
    field<HTMLFieldSetElement>("fields").disabled = busy || !form;
    field<HTMLButtonElement>("apply").hidden = busy;
    field<HTMLButtonElement>("apply").disabled = !form || !dirty();
    field<HTMLButtonElement>("apply").title = dirty() ? "Dựng lại video theo mẫu này thành bản mới; bản cũ vẫn giữ" : "Chưa đổi gì trong mẫu";
    field<HTMLButtonElement>("reset").hidden = busy || !dirty();
    field<HTMLButtonElement>("cancel").hidden = !busy;
    if (form && info) {
      const reuse = keepsVoice(info, form);
      field("voice-note").textContent = !dirty() ? ""
        : reuse ? "Kịch bản, giọng và tốc độ không đổi: dùng lại giọng đọc cũ, dựng nhanh hơn."
          : "Kịch bản, giọng hoặc tốc độ đã đổi: giọng sẽ được đọc lại.";
    }
    context.changed();
  }

  async function loadVoices(): Promise<void> {
    const select = field<HTMLSelectElement>("voice");
    if (select.options.length) return;
    try {
      const catalog = parseVoiceCatalog(await invoke<unknown>("voice_catalog"));
      select.replaceChildren(...voiceOptionGroups(catalog, null));
    } catch (error) {
      field("state").textContent = `Không tải được danh sách giọng: ${String(error).slice(0, 160)}`;
    }
  }

  async function loadLibrary(): Promise<void> {
    try {
      library = parseBackgrounds(await invoke<unknown>("product_backgrounds"));
    } catch {
      library = [];
    }
  }

  /** The background in use when the library does not have it: its size from a downscaled copy (the aspect is what matters). */
  async function measureCurrent(path: string, ticket: number): Promise<void> {
    if (library.some((entry) => entry.path.toLocaleLowerCase("en") === path.toLocaleLowerCase("en"))) return;
    const url = await images.url(path, PREVIEW_SIDE);
    if (!url || ticket !== token) return;
    const image = new Image();
    image.src = url;
    try {
      await image.decode();
    } catch {
      return;
    }
    if (ticket !== token || !image.naturalWidth) return;
    current = { id: CURRENT, extension: "", name: "Ảnh nền của bản này", width: image.naturalWidth, height: image.naturalHeight, path };
    renderForm();
  }

  async function addBackground(): Promise<void> {
    const [picked] = await invoke<string[]>("pick_product_files", { kind: "background" }).catch(() => [] as string[]);
    if (!picked || !form) return;
    field("background-hint").textContent = "Đang lưu ảnh nền vào kho…";
    try {
      const [added] = parseBackgrounds([await invoke<unknown>("product_background_add", { path: picked })]);
      await loadLibrary();
      if (added && form) {
        form.background = added.path;
        form.focus = CENTER;
      }
      renderForm();
    } catch (error) {
      field("background-hint").textContent = String(error).slice(0, 200);
    }
  }

  async function removeBackground(entry: LibraryBackground): Promise<void> {
    if (!form) return;
    // The image in use is not in the library: choosing another one is enough.
    if (entry.id === CURRENT) {
      form.background = null;
      renderForm();
      return;
    }
    field("background-hint").textContent = "Xóa ảnh khỏi kho ở trang Bán hàng. Ở đây chỉ chọn ảnh nền cho video này.";
  }

  function stopPolling(): void {
    window.clearInterval(polling);
    polling = 0;
  }

  async function poll(run: string, ticket: number): Promise<void> {
    let status;
    try {
      status = parseStatus(await invoke<unknown>("product_status", { outputDir: run }));
    } catch {
      return;
    }
    if (!status || ticket !== token || running !== run) return;
    field("state").textContent = status.message;
    if (status.state === "RUNNING") return;
    stopPolling();
    running = "";
    const video = status.videos.find((item) => item.ok && item.path);
    if (status.state !== "COMPLETED" || !video?.path) {
      field("state").textContent = status.state === "CANCELLED" ? "Đã hủy dựng lại; video đang sửa vẫn như cũ." : `Không dựng lại được: ${status.message}`;
      renderFoot();
      return;
    }
    await carry(video.path, (video as { reused_voice?: boolean }).reused_voice === true);
  }

  /** Save the draft for the new render and open it. */
  async function carry(path: string, reused: boolean): Promise<void> {
    const draft = context.draft();
    const name = form?.name || info?.spec.name || "Video bán hàng";
    const next: TemplateTarget = { kind: "product", outputPath: path, displayName: `${name} · video 1` };
    let warning = "";
    try {
      const loaded = await invoke<{ duration: number; width: number; height: number; fps?: number; has_audio: boolean }>("edit_project_load", { outputPath: path, kind: "product" });
      if (draft) {
        const before = outputDuration(draft.edit);
        const { edit, trimmed } = retargetEdit(draft.edit, loaded.duration, {
          path, title: next.displayName, width: loaded.width, height: loaded.height, fps: loaded.fps ?? 30, hasAudio: loaded.has_audio,
        });
        await invoke("edit_project_save", { outputPath: path, kind: "product", project: serializeProject(edit, draft.changes, draft.selected, draft.recent) });
        if (Math.abs(loaded.duration - draft.edit.duration) > 0.05) {
          warning = ` Video mới dài ${formatClock(loaded.duration)} (trước ${formatClock(draft.edit.duration)})`
            + (trimmed ? `: ${trimmed} phần cắt ghép, chèn, âm thanh hoặc chữ nằm ngoài độ dài mới đã được cắt bớt hoặc bỏ — hãy xem lại.` : `; bản sửa dài ${formatClock(outputDuration(edit))} (trước ${formatClock(before)}).`);
        }
      }
    } catch (error) {
      warning = ` Chưa chuyển được bản nháp chỉnh sửa sang video mới: ${String(error).slice(0, 160)}`;
    }
    notice = `Đã dựng lại theo mẫu mới thành bản mới (bản cũ vẫn giữ trong thư mục cũ). ${reused ? "Đã dùng lại giọng đọc cũ." : "Giọng đã được đọc lại."}${warning}`;
    await context.reopen(next);
  }

  async function apply(): Promise<void> {
    if (!form || !info || running || !dirty()) return;
    if (form.template !== "chi-chu" && !form.visuals.length) {
      field("state").textContent = "Mẫu này cần ảnh hoặc clip. Hãy chọn “Chỉ chữ” hoặc tạo video mới ở trang Bán hàng.";
      return;
    }
    const request = buildRequest({
      info: { name: form.name.trim(), price: form.price.trim(), features: form.features, audience: "", notes: "" },
      voiceId: form.voiceId || null, scripts: [{ text: form.script, use: true }], visuals: form.visuals, music: form.music,
      volumePercent: form.musicVolume * 100, cpuOnly: context.cpuOnly(), outputRoot: null,
      background: chosenBackground() ? form.background : null, template: form.template, speed: form.speed, focus: form.focus,
    });
    if (typeof request === "string") {
      field("state").textContent = request;
      return;
    }
    context.pause();
    await context.flush();
    field("state").textContent = "Đang bắt đầu dựng lại…";
    try {
      const run = await invoke<string>("product_render", { request: { ...request, reuseAudioFrom: info.run } });
      running = run;
      const ticket = token;
      stopPolling();
      polling = window.setInterval(() => void poll(run, ticket), POLL_MS);
      renderFoot();
      void poll(run, ticket);
    } catch (error) {
      field("state").textContent = `Không dựng lại được: ${String(error).slice(0, 240)}`;
    }
  }

  async function cancel(): Promise<void> {
    if (!running) return;
    field<HTMLButtonElement>("cancel").disabled = true;
    try {
      await invoke("product_cancel", { outputDir: running });
      field("state").textContent = "Đang dừng…";
    } catch (error) {
      field("state").textContent = String(error).slice(0, 200);
    } finally {
      field<HTMLButtonElement>("cancel").disabled = false;
    }
  }

  async function load(next: TemplateTarget, ticket: number): Promise<void> {
    field("state").textContent = notice || "Đang đọc mẫu của video…";
    notice = "";
    try {
      const [loaded] = await Promise.all([invoke<unknown>("product_spec", { outputPath: next.outputPath }), loadVoices(), loadLibrary()]);
      if (ticket !== token) return;
      info = parseProductSpec(loaded);
      if (!info) throw new Error("Không đọc được thông tin mẫu của video này");
      form = structuredClone(info.spec);
      current = null;
      if (field("state").textContent === "Đang đọc mẫu của video…") field("state").textContent = "";
      field("intro").textContent = `Video ${info.variant} của lần tạo này. Đổi mẫu, chữ, nền, giọng hoặc kịch bản rồi bấm “Áp dụng mẫu” để dựng lại thành bản mới; bản cũ vẫn giữ.`;
      renderForm();
      if (info.spec.background) void measureCurrent(info.spec.background, ticket);
    } catch (error) {
      if (ticket !== token) return;
      info = form = null;
      field("intro").textContent = "";
      field("state").textContent = String(error).slice(0, 240);
      renderFoot();
    }
  }

  // Inputs change the form; the preview follows.
  for (const name of ["name", "price", "script"] as const) {
    field<HTMLInputElement | HTMLTextAreaElement>(name).addEventListener("input", (event) => {
      if (!form) return;
      form[name] = (event.target as HTMLInputElement).value;
      renderFoot();
      card.update();
    });
  }
  field<HTMLTextAreaElement>("features").addEventListener("input", (event) => {
    if (!form) return;
    form.features = parseFeatures((event.target as HTMLTextAreaElement).value);
    renderFoot();
    card.update();
  });
  field<HTMLSelectElement>("voice").addEventListener("change", (event) => {
    if (form) form.voiceId = (event.target as HTMLSelectElement).value;
    renderFoot();
  });
  field<HTMLSelectElement>("speed").addEventListener("change", (event) => {
    if (form) form.speed = Number((event.target as HTMLSelectElement).value) || 1;
    renderFoot();
    card.update();
  });
  field<HTMLInputElement>("volume").addEventListener("input", (event) => {
    if (!form) return;
    form.musicVolume = Number((event.target as HTMLInputElement).value) / 100;
    field("volume-value").textContent = `${Math.round(form.musicVolume * 100)}%`;
    renderFoot();
  });
  field("music").addEventListener("click", async () => {
    const [picked] = await invoke<string[]>("pick_product_files", { kind: "music" }).catch(() => [] as string[]);
    if (picked && form) {
      form.music = picked;
      renderForm();
    }
  });
  field("music-clear").addEventListener("click", () => {
    if (form) form.music = null;
    renderForm();
  });
  field("apply").addEventListener("click", () => void apply());
  field("cancel").addEventListener("click", () => void cancel());
  field("reset").addEventListener("click", () => {
    if (!info) return;
    form = structuredClone(info.spec);
    field("state").textContent = "";
    renderForm();
  });
  // The mock playback moves the cards and caption while the panel is open.
  window.setInterval(() => {
    if (shown && !document.hidden) card.update();
  }, TICK_MS);

  return {
    get busy() { return !!running; },
    begin(next) {
      target = next;
      token += 1;
      info = form = null;
      running = "";
      stopPolling();
      if (target.kind === "product") void load(target, token);
    },
    end() {
      token += 1;
      target = null;
      info = form = null;
      running = "";
      stopPolling();
      field("state").textContent = "";
    },
    render(next) {
      const was = shown;
      shown = next && target?.kind === "product";
      root.hidden = !shown;
      if (shown && !was) {
        card.reset();
        card.scale();
      }
      if (shown) card.update();
    },
    dirty,
  };
}

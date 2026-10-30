import { writeEditorial, MAX_CUES } from "./ai.ts";
import { buildRequest, completedVideos, moveItem, newItem, parseStatus, type CompletedVideo, type ItemForm } from "./model.ts";
import { PROVIDERS, type AiSession } from "../ai/provider.ts";
import { parseVoiceCatalog } from "../voices/model.ts";
import { fileName } from "../product/model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

/** Queue selection and editable editorial content stay in memory across navigation. */
export function mountCompilation(invoke: Invoke, session: AiSession, settings: {
  jobs(): readonly CompletedVideo[]; outputRoot(): string | null; cpuOnly(): boolean;
}): { show(): void } {
  const root = document.querySelector<HTMLElement>("#compilation-view")!;
  root.innerHTML = `
    <header class="topbar"><div><h1>Tổng hợp</h1><p class="hint">Ghép các video đã hoàn tất theo thứ tự của bạn, thêm lời dẫn và nhận xét.</p></div></header>
    <div class="product-body compilation-body">
      <fieldset id="compilation-fields" class="compilation-fields">
        <section class="product-card">
          <h2><b>1</b> Chọn video</h2><p class="hint">Chọn 2–30 video đã hoàn tất. Dùng ↑ ↓ để sắp xếp các phần.</p>
          <p id="compilation-empty" class="hint">Chưa có video đã hoàn tất trong hàng đợi.</p>
          <ul id="compilation-available" class="compilation-list"></ul>
          <ol id="compilation-order" class="compilation-list"></ol>
        </section>
        <section class="product-card">
          <h2><b>2</b> Nội dung</h2>
          <div class="product-form">
            <label class="wide">Chủ đề / tiêu đề<input id="compilation-title" maxlength="120" /></label>
            <label class="wide">Bố cục<select id="compilation-layout"><option value="bang-thong-tin">Bảng thông tin (video dọc + bảng bên trái)</option><option value="toan-man-hinh">Toàn màn hình</option></select></label>
          </div>
          <p id="compilation-provider" class="hint"></p>
          <button id="compilation-ai" type="button" class="btn">AI viết mở đầu, chuyển cảnh &amp; nhận xét</button>
          <p id="compilation-ai-state" class="discover-state" role="status"></p>
          <div class="product-form">
            <label>Mở đầu (tối đa 800 ký tự)<textarea id="compilation-intro" rows="5" maxlength="800"></textarea></label>
            <label>Kết thúc (tối đa 800 ký tự)<textarea id="compilation-outro" rows="5" maxlength="800"></textarea></label>
          </div>
          <ol id="compilation-editors" class="product-scripts"></ol>
        </section>
        <section class="product-card">
          <h2><b>3</b> Tạo video</h2>
          <div class="product-options">
            <label>Giọng đọc<select id="compilation-voice"></select></label>
            <label>Tốc độ đọc<select id="compilation-speed"><option value="1">Bình thường (1,0)</option><option value="1.1">1,1</option><option value="1.2">1,2</option><option value="1.3">Nhanh (1,3)</option></select></label>
            <span class="product-music">Nhạc: <span id="compilation-music-name">Không có</span><button id="compilation-music" class="link" type="button">Chọn…</button><button id="compilation-music-clear" class="link" type="button" hidden>Bỏ</button></span>
            <label>Âm lượng nhạc<input id="compilation-volume" type="range" min="0" max="100" value="15" /></label>
          </div>
          <p class="hint">Nhạc chỉ phát dưới lời dẫn, mở đầu và kết thúc. Mỗi clip giữ âm thanh của video đã lồng tiếng.</p>
          <button id="compilation-render" type="button" class="btn primary">Tạo video tổng hợp</button>
        </section>
      </fieldset>
      <div class="product-actions"><button id="compilation-cancel" type="button" class="btn danger" hidden>Hủy</button><p id="compilation-state" role="status"></p></div>
      <div id="compilation-results" class="product-actions" hidden>
        <button id="compilation-open" class="btn" type="button">Mở video</button><button id="compilation-folder" class="btn" type="button">Mở thư mục</button><button id="compilation-copy" class="btn" type="button">Sao chép mô tả</button>
      </div>
    </div>`;
  const $ = <T extends HTMLElement>(name: string): T => root.querySelector<T>(`#compilation-${name}`)!;
  const field = (name: string) => $<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(name);
  let selected: ItemForm[] = [], music: string | null = null, busy = false, output = "", resultPath = "", timer = 0, polling = false;
  let loadingVoices = false;
  const durations = new Map<string, number>();
  const message = (error: unknown) => String(error).slice(0, 250);
  function showProvider(): void {
    $("provider").textContent = `Khi bấm AI, chủ đề và tối đa 60 dòng phụ đề tiếng Việt mỗi video được gửi tới ${PROVIDERS[session.provider()].company}. Dùng nhà cung cấp và khóa đã lưu trong Cài đặt AI.`;
  }
  // Settings may change while this page stays visible.
  document.querySelector("#ai-settings-provider")?.addEventListener("change", showProvider);
  root.addEventListener("focusin", showProvider);
  function lock(value: boolean): void {
    busy = value;
    $<HTMLFieldSetElement>("fields").disabled = value;
  }

  function button(text: string, action: () => void, label: string): HTMLButtonElement {
    const node = document.createElement("button");
    node.type = "button"; node.className = "btn"; node.textContent = text; node.setAttribute("aria-label", label);
    node.addEventListener("click", action);
    return node;
  }

  function renderSelection(): void {
    const jobs = completedVideos(settings.jobs());
    $("empty").hidden = jobs.length > 0;
    $("available").replaceChildren(...jobs.map(job => {
      const li = document.createElement("li"), label = document.createElement("label"), check = document.createElement("input");
      check.type = "checkbox"; check.checked = selected.some(item => item.id === job.id);
      check.disabled = !check.checked && selected.length >= 30;
      check.addEventListener("change", () => {
        selected = check.checked ? [...selected, newItem(job)] : selected.filter(item => item.id !== job.id);
        renderSelection(); renderEditors();
      });
      const duration = job.durationMs ?? durations.get(job.id);
      label.append(check, ` ${job.displayName}${duration ? ` · ${job.durationMs ? "" : "~"}${Math.round(duration / 1000)} giây` : ""}`);
      li.append(label); return li;
    }));
    $("order").replaceChildren(...selected.map((item, index) => {
      const li = document.createElement("li"), name = document.createElement("span");
      name.textContent = `Phần ${index + 1}: ${item.title}`;
      const move = (delta: number) => { selected = moveItem(selected, index, delta); renderSelection(); renderEditors(); };
      const up = button("↑", () => move(-1), `Đưa phần ${index + 1} lên`), down = button("↓", () => move(1), `Đưa phần ${index + 1} xuống`);
      up.disabled = index === 0; down.disabled = index === selected.length - 1;
      li.append(name, up, down); return li;
    }));
  }

  function renderEditors(): void {
    $("editors").replaceChildren(...selected.map((item, index) => {
      const li = document.createElement("li"), heading = document.createElement("h3"), form = document.createElement("div");
      li.className = "compilation-editor"; heading.textContent = `Phần ${index + 1}`; form.className = "product-form";
      const fields: Array<[keyof Omit<ItemForm, "id" | "video">, string, number, boolean]> = [
        ["title", "Tiêu đề phần", 80, false], ["lead", "Lời dẫn / nhận xét trước clip", 400, true],
        ["score", "Điểm (0–10 hoặc để trống)", 0, false], ["pros", "Ưu điểm (tối đa 3 dòng, 60 ký tự/dòng)", 0, true],
        ["cons", "Nhược điểm (tối đa 3 dòng, 60 ký tự/dòng)", 0, true], ["start", "Bắt đầu (giây, để trống = từ đầu)", 0, false],
        ["end", "Kết thúc (giây, để trống = hết clip)", 0, false],
      ];
      for (const [key, title, limit, multiline] of fields) {
        const label = document.createElement("label"), input = document.createElement(multiline ? "textarea" : "input");
        label.textContent = title;
        if (key === "title" || key === "lead") label.className = "wide";
        if (limit) input.maxLength = limit;
        input.value = item[key]; input.setAttribute("aria-label", `Phần ${index + 1}: ${title}`);
        if (input instanceof HTMLInputElement && ["score", "start", "end"].includes(key)) {
          input.type = "number"; input.min = "0"; input.step = key === "score" ? "1" : "0.001";
          if (key === "score") input.max = "10";
        }
        input.addEventListener("input", () => { item[key] = input.value; if (key === "title") renderSelection(); });
        label.append(input); form.append(label);
      }
      li.append(heading, form); return li;
    }));
  }

  async function ai(): Promise<void> {
    if (busy) return;
    if (selected.length < 2 || selected.length > 30 || !field("title").value.trim()) {
      $("ai-state").textContent = "Hãy chọn 2–30 video và nhập chủ đề trước."; return;
    }
    const provider = session.provider(), key = session.read();
    showProvider();
    if (!key) { $("ai-state").textContent = "Hãy lưu khóa của nhà cung cấp trong Cài đặt AI trước."; return; }
    lock(true); $("ai-state").textContent = `${PROVIDERS[provider].name} đang viết nội dung…`;
    try {
      const items = [];
      for (const item of selected) {
        const cues = await invoke<Array<{ text: string; end_ms: number }>>("review_cues", { outputPath: item.video });
        if (cues.length) durations.set(item.id, cues.reduce((end, cue) => Math.max(end, cue.end_ms), 0));
        items.push({ id: item.id, title: item.title, subtitles: cues.slice(0, MAX_CUES).map(cue => cue.text) });
      }
      const result = await writeEditorial(provider, key, field("title").value, items);
      field("intro").value = result.intro; field("outro").value = result.outro;
      selected = selected.map(item => ({ ...item, ...result.items.get(item.id) }));
      renderSelection(); renderEditors();
      $("ai-state").textContent = "Đã điền nội dung. Hãy kiểm tra nhận xét và chỉnh sửa trước khi tạo video.";
    } catch (error) { $("ai-state").textContent = message(error); }
    finally { lock(false); }
  }

  async function poll(): Promise<void> {
    if (polling || !output) return;
    polling = true;
    try {
      const status = parseStatus(await invoke("compilation_status", { outputDir: output }));
      if (!status) return;
      $("state").textContent = `${status.message}${status.total ? ` (${status.done}/${status.total} đoạn)` : ""}`;
      if (status.state !== "RUNNING") {
        window.clearInterval(timer); timer = 0; lock(false);
        $("cancel").hidden = true; $<HTMLButtonElement>("cancel").disabled = false;
        $("results").hidden = status.state !== "COMPLETED";
        resultPath = status.path ?? "";
      }
    } catch { $("state").textContent = "Chưa đọc được trạng thái; đang thử lại…"; }
    finally { polling = false; }
  }

  async function render(): Promise<void> {
    if (busy) return;
    const ready = new Set(completedVideos(settings.jobs()).map(job => job.id));
    if (selected.some(item => !ready.has(item.id))) { $("state").textContent = "Có video không còn hoàn tất trong hàng đợi. Hãy chọn lại."; return; }
    const request = buildRequest({ title: field("title").value, layout: field("layout").value, intro: field("intro").value,
      outro: field("outro").value, items: selected, voiceId: field("voice").value, speed: Number(field("speed").value),
      music, musicVolume: Number(field("volume").value) / 100, outputRoot: settings.outputRoot(), cpuOnly: settings.cpuOnly() });
    if (typeof request === "string") { $("state").textContent = request; return; }
    lock(true); $("results").hidden = true; $("state").textContent = "Đang khởi động tổng hợp…";
    try {
      output = await invoke<string>("compilation_render", { request });
      $("cancel").hidden = false;
      timer = window.setInterval(() => void poll(), 1500);
      await poll();
    } catch (error) { lock(false); $("state").textContent = message(error); }
  }

  async function loadVoices(): Promise<void> {
    const select = $<HTMLSelectElement>("voice");
    if (select.options.length || loadingVoices) return;
    loadingVoices = true;
    try {
      const catalog = parseVoiceCatalog(await invoke("voice_catalog"));
      select.replaceChildren(...catalog.voices.map(voice => {
        const option = document.createElement("option"); option.value = voice.voice_id;
        option.textContent = `${voice.name} · ${voice.gender} · ${voice.accent} · ${voice.style}`;
        option.selected = voice.voice_id === catalog.default_voice_id; return option;
      }));
    } catch (error) { $("state").textContent = message(error); }
    finally { loadingVoices = false; }
  }

  $("ai").addEventListener("click", () => void ai());
  $("render").addEventListener("click", () => void render());
  $("cancel").addEventListener("click", async () => {
    $<HTMLButtonElement>("cancel").disabled = true;
    try { await invoke("compilation_cancel", { outputDir: output }); $("state").textContent = "Đang hủy…"; }
    catch (error) { $<HTMLButtonElement>("cancel").disabled = false; $("state").textContent = message(error); }
  });
  $("music").addEventListener("click", async () => {
    try {
      const [picked] = await invoke<string[]>("pick_product_files", { kind: "music" });
      if (picked) { music = picked; $("music-name").textContent = fileName(picked); $("music-clear").hidden = false; }
    } catch (error) { $("state").textContent = message(error); }
  });
  $("music-clear").addEventListener("click", () => { music = null; $("music-name").textContent = "Không có"; $("music-clear").hidden = true; });
  $("open").addEventListener("click", () => void invoke("open_path", { path: resultPath }).catch(error => { $("state").textContent = message(error); }));
  $("folder").addEventListener("click", () => void invoke("open_folder", { path: output }).catch(error => { $("state").textContent = message(error); }));
  $("copy").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(await invoke<string>("compilation_description", { outputDir: output })); $("state").textContent = "Đã sao chép mô tả và nguồn."; }
    catch (error) { $("state").textContent = message(error); }
  });
  return { show: () => {
    showProvider();
    if (!busy) { renderSelection(); renderEditors(); }
    void loadVoices();
  } };
}

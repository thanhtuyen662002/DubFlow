import { PROVIDERS, type AiSession } from "../ai/provider.ts";
import { suggestSegments } from "./ai.ts";
import { buildRequest, firstLine, formatTime, parseCues, parseStatus, parseTime, type Cue, type Row, type ShortsStatus } from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Job = { id: string; outputPath: string | null; status: { state: string } };

export function mountShorts(invoke: Invoke, session: AiSession): { select(job: Job | null): void } {
  const $ = <T extends HTMLElement>(id: string): T => {
    const node = document.querySelector<T>(`#shorts-${id}`);
    if (!node) throw new Error(`Missing shorts-${id}`);
    return node;
  };
  const section = $<HTMLDetailsElement>("section"), form = $<HTMLFormElement>("form"), fields = $<HTMLFieldSetElement>("fields");
  const cancel = $<HTMLButtonElement>("cancel"), ai = $<HTMLButtonElement>("ai"), add = $<HTMLButtonElement>("add");
  const layout = $<HTMLSelectElement>("layout"), state = $("state"), list = $("rows"), results = $("results");
  let selection = "", path = "", generation = 0, statusRead = 0, timer = 0, busy = false, suggesting = false;
  let cues: Cue[] = [], rows: Row[] = [];

  function disclosure(): void {
    $("provider").textContent = `AI sẽ gửi phụ đề tiếng Việt và thời gian tới ${PROVIDERS[session.provider()].label} bằng tài khoản của bạn (Cài đặt AI).`;
  }

  function controls(): void {
    fields.disabled = busy || suggesting;
    ai.disabled = suggesting || busy;
    add.disabled = rows.length >= 3;
    cancel.hidden = !busy;
  }

  function drawRows(): void {
    list.replaceChildren();
    rows.forEach((row, index) => {
      const block = document.createElement("div");
      block.className = "settings";
      const selected = document.createElement("input");
      selected.type = "checkbox"; selected.checked = row.selected; selected.ariaLabel = `Chọn đoạn ${index + 1}`;
      selected.addEventListener("change", () => { row.selected = selected.checked; });
      block.append(selected);
      const preview = document.createElement("small");
      preview.className = "hint";
      preview.textContent = row.preview;
      for (const [label, value, update] of [
        ["Tiêu đề", row.title, (text: string) => { row.title = text; }],
        ["Bắt đầu (mm:ss)", formatTime(row.start_ms), (text: string) => { row.start_ms = parseTime(text) ?? NaN; }],
        ["Kết thúc (mm:ss)", formatTime(row.end_ms), (text: string) => { row.end_ms = parseTime(text) ?? NaN; }],
      ] as const) {
        const wrapper = document.createElement("label"), input = document.createElement("input");
        wrapper.textContent = label; input.value = value;
        if (label === "Tiêu đề") input.maxLength = 160; // Unicode count checked in model/host.
        input.addEventListener("input", () => {
          update(input.value);
          row.preview = firstLine(cues, row.start_ms, row.end_ms);
          preview.textContent = row.preview;
        });
        wrapper.append(input); block.append(wrapper);
      }
      const remove = document.createElement("button");
      remove.type = "button"; remove.className = "link"; remove.textContent = "Bỏ đoạn";
      remove.addEventListener("click", () => { rows.splice(index, 1); drawRows(); });
      block.append(preview, remove); list.append(block);
    });
    controls();
  }

  function show(status: ShortsStatus): void {
    state.textContent = `${status.message} (${status.done}/${status.total})`;
    busy = status.state === "RUNNING";
    cancel.disabled = false;
    controls();
    results.replaceChildren();
    for (const output of status.outputs) {
      const block = document.createElement("div"), title = document.createElement("p");
      title.textContent = output.title;
      const open = document.createElement("button"), copy = document.createElement("button");
      open.type = copy.type = "button"; open.className = copy.className = "btn";
      open.textContent = "Mở"; copy.textContent = "Sao chép tiêu đề & mô tả";
      open.addEventListener("click", () => { void invoke("open_path", { path: output.path }).catch(() => undefined); });
      copy.addEventListener("click", () => {
        void navigator.clipboard.writeText(output.text).then(() => { copy.textContent = "Đã sao chép"; })
          .catch(() => { copy.textContent = "Chưa sao chép được"; });
      });
      block.append(title, open, copy); results.append(block);
    }
    if (!busy) { window.clearInterval(timer); timer = 0; }
  }

  async function poll(outputPath: string, token: number): Promise<void> {
    const read = ++statusRead;
    try {
      const status = parseStatus(await invoke("shorts_status", { outputPath }));
      if (token === generation && read === statusRead && status) show(status);
    } catch { /* Atomic replacement can race a status read; retry on next tick. */ }
  }

  function watch(outputPath: string, token: number): void {
    window.clearInterval(timer);
    timer = window.setInterval(() => void poll(outputPath, token), 1500);
  }

  section.addEventListener("toggle", disclosure);
  ai.addEventListener("click", async () => {
    if (busy || suggesting || !path) return;
    disclosure();
    const provider = session.provider(), key = session.read();
    if (!key) { state.textContent = `Hãy lưu khóa ${PROVIDERS[provider].name} trong Cài đặt AI.`; return; }
    const token = generation, outputPath = path;
    suggesting = true; controls();
    state.textContent = `Đang gửi phụ đề tới ${PROVIDERS[provider].label} để gợi ý…`;
    try {
      const loaded = parseCues(await invoke("review_cues", { outputPath }));
      if (token !== generation) return;
      cues = loaded;
      const suggested = await suggestSegments(provider, key, loaded);
      if (token !== generation) return;
      rows = suggested; drawRows();
      state.textContent = "Kiểm tra đoạn và chỉnh tiêu đề trước khi tạo Shorts.";
    } catch (error) {
      if (token === generation) state.textContent = String(error).slice(0, 240);
    } finally {
      if (token === generation) { suggesting = false; controls(); }
    }
  });
  add.addEventListener("click", () => {
    if (rows.length >= 3 || busy || suggesting) return;
    const start_ms = rows.length ? rows[rows.length - 1].end_ms : 0;
    if (!Number.isSafeInteger(start_ms)) { state.textContent = "Hãy sửa thời gian đoạn trước trước khi thêm đoạn."; return; }
    const last = cues.reduce((latest, cue) => Math.max(latest, cue.end_ms), cues.length ? 0 : start_ms + 35000);
    const end_ms = Math.max(start_ms + 5000, Math.min(start_ms + 35000, last));
    rows.push({ start_ms, end_ms, title: "Đoạn hay", selected: true, preview: firstLine(cues, start_ms, end_ms) });
    drawRows();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy || suggesting) return;
    const request = buildRequest(path, rows, layout.value);
    if (typeof request === "string") { state.textContent = request; return; }
    const token = ++generation, outputPath = path;
    show({ state: "RUNNING", done: 0, total: request.items.length, message: "Đang khởi động cắt Shorts…", outputs: [] });
    try {
      await invoke("shorts_render", { request });
      if (token !== generation) return;
      watch(outputPath, token); await poll(outputPath, token);
    } catch (error) {
      if (token === generation) show({ state: "FAILED", done: 0, total: request.items.length, message: String(error).slice(0, 240), outputs: [] });
    }
  });
  cancel.addEventListener("click", async () => {
    if (!busy || !path) return;
    const token = generation, outputPath = path;
    cancel.disabled = true;
    try {
      await invoke("shorts_cancel", { outputPath });
      if (token === generation) state.textContent = "Đang dừng cắt Shorts…";
    } catch (error) {
      if (token === generation) { state.textContent = String(error).slice(0, 240); cancel.disabled = false; }
    }
  });

  return { select(job) {
    disclosure();
    const outputPath = job?.status.state === "COMPLETED" ? job.outputPath : null;
    section.hidden = !outputPath;
    const next = outputPath ? `${job!.id}\n${outputPath}` : "";
    if (selection === next) return;
    selection = next;
    const token = ++generation;
    path = outputPath ?? ""; busy = false; suggesting = false; rows = []; cues = [];
    window.clearInterval(timer); timer = 0;
    form.reset(); state.textContent = ""; results.replaceChildren(); drawRows();
    if (!outputPath) return;
    watch(outputPath, token);
    void poll(outputPath, token).then(() => { if (token === generation && !busy) { window.clearInterval(timer); timer = 0; } });
    void invoke("review_cues", { outputPath }).then((value) => {
      if (token !== generation) return;
      cues = parseCues(value);
      for (const row of rows) row.preview = firstLine(cues, row.start_ms, row.end_ms);
      if (rows.length) drawRows();
    }).catch(() => undefined);
  } };
}

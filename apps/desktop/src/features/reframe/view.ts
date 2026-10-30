import { PROVIDERS, type AiSession } from "../ai/provider.ts";
import { parseCues } from "../shorts/model.ts";
import { fillPanel } from "./ai.ts";
import { buildRequest, parseInfo, parseStatus, previewBand, type Info, type ReframeStatus } from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Job = { id: string; outputPath: string | null; status: { state: string } };

export function mountReframe(invoke: Invoke, session: AiSession): { select(job: Job | null): void } {
  const $ = <T extends HTMLElement>(id: string): T => {
    const node = document.querySelector<T>(`#reframe-${id}`);
    if (!node) throw new Error(`Missing reframe-${id}`);
    return node;
  };
  const section = $<HTMLDetailsElement>("section"), form = $<HTMLFormElement>("form"), fields = $<HTMLFieldSetElement>("fields");
  const mode = $<HTMLSelectElement>("mode"), title = $<HTMLInputElement>("title"), score = $<HTMLInputElement>("score");
  const pros = $<HTMLTextAreaElement>("pros"), cons = $<HTMLTextAreaElement>("cons"), summary = $<HTMLInputElement>("summary");
  const start = $<HTMLInputElement>("start"), end = $<HTMLInputElement>("end");
  const focusStart = $<HTMLInputElement>("focus-start"), focusEnd = $<HTMLInputElement>("focus-end");
  const ai = $<HTMLButtonElement>("ai"), cancel = $<HTMLButtonElement>("cancel"), state = $("state");
  const image = $<HTMLImageElement>("image"), band = $("band"), preview = $("preview");
  let selection = "", path = "", generation = 0, statusRead = 0, timer = 0, busy = false, suggesting = false;
  let info: Info | null = null, imageUrl = "", resultPath = "", hasFrame = false;

  function disclosure(): void {
    $("provider").textContent = `AI sẽ gửi tối đa 60 dòng phụ đề tiếng Việt tới ${PROVIDERS[session.provider()].label} bằng tài khoản của bạn (Cài đặt AI).`;
  }
  function controls(): void {
    fields.disabled = busy || suggesting;
    cancel.hidden = !busy;
    const crop = mode.value === "cat-ngang";
    $("panel").hidden = crop; $("crop").hidden = !crop;
  }
  function drawPreview(): void {
    $("focus-start-value").textContent = focusStart.value;
    $("focus-end-value").textContent = focusEnd.value;
    $("preview-title").textContent = title.value;
    if (!info) return;
    const box = previewBand(info.width, info.height, Number(focusStart.value));
    band.style.top = `${box.top * 100}%`; band.style.height = `${box.height * 100}%`;
  }
  function releaseFrame(): void {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    imageUrl = ""; hasFrame = false; preview.hidden = true; image.removeAttribute("src");
  }
  async function loadFrame(outputPath: string, token: number): Promise<void> {
    if (hasFrame || mode.value !== "cat-ngang") return;
    $("preview-state").textContent = "Đang lấy khung hình…";
    try {
      const bytes = await invoke<ArrayBuffer>("video_frame", { sourcePath: outputPath, position: 0 });
      if (token !== generation) return;
      if (imageUrl) URL.revokeObjectURL(imageUrl);
      imageUrl = URL.createObjectURL(new Blob([bytes], { type: "image/jpeg" }));
      image.src = imageUrl; hasFrame = true; preview.hidden = false;
      $("preview-state").textContent = "Khung ở Vị trí đầu; vùng cắt di chuyển đều tới Vị trí cuối.";
      drawPreview();
    } catch {
      if (token === generation) $("preview-state").textContent = "Chưa lấy được khung hình; chọn lại Cắt ngang để thử lại.";
    }
  }
  function show(status: ReframeStatus): void {
    state.textContent = status.message; busy = status.state === "RUNNING"; cancel.disabled = false;
    resultPath = status.state === "COMPLETED" ? status.path! : "";
    $("results").hidden = !resultPath; controls();
    if (!busy) { window.clearInterval(timer); timer = 0; }
  }
  async function poll(outputPath: string, token: number): Promise<void> {
    const read = ++statusRead;
    try {
      const status = parseStatus(await invoke("reframe_status", { outputPath }));
      if (token === generation && read === statusRead && status) show(status);
    } catch { /* Retry atomic status reads on the next tick. */ }
  }
  function watch(outputPath: string, token: number): void {
    window.clearInterval(timer);
    timer = window.setInterval(() => void poll(outputPath, token), 1500);
  }
  section.addEventListener("toggle", disclosure);
  mode.addEventListener("change", () => { controls(); if (path) void loadFrame(path, generation); });
  for (const input of [title, focusStart, focusEnd]) input.addEventListener("input", drawPreview);
  ai.addEventListener("click", async () => {
    if (busy || suggesting || !path) return;
    disclosure();
    const provider = session.provider(), key = session.read();
    if (!key) { state.textContent = `Hãy lưu khóa ${PROVIDERS[provider].name} trong Cài đặt AI.`; return; }
    const token = generation, outputPath = path;
    suggesting = true; controls(); state.textContent = `Đang gửi phụ đề tới ${PROVIDERS[provider].label}…`;
    try {
      const cues = parseCues(await invoke("review_cues", { outputPath }));
      if (token !== generation) return;
      const result = await fillPanel(provider, key, cues);
      if (token !== generation) return;
      title.value = result.title; score.value = result.score; pros.value = result.pros; cons.value = result.cons; summary.value = result.summary;
      state.textContent = "Kiểm tra và sửa nội dung bảng trước khi tạo video."; drawPreview();
    } catch (error) { if (token === generation) state.textContent = String(error).slice(0, 240); }
    finally { if (token === generation) { suggesting = false; controls(); } }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (busy || suggesting || !path) return;
    const request = buildRequest(path, { mode: mode.value, title: title.value, score: score.value, pros: pros.value, cons: cons.value,
      summary: summary.value, focusStart: Number(focusStart.value), focusEnd: Number(focusEnd.value), start: start.value, end: end.value });
    if (typeof request === "string") { state.textContent = request; return; }
    const token = ++generation, outputPath = path;
    show({ state: "RUNNING", message: "Đang khởi động đổi khung 16:9…" });
    try {
      await invoke("reframe_render", { request });
      if (token !== generation) return;
      watch(outputPath, token); await poll(outputPath, token);
      void loadFrame(outputPath, token);
    } catch (error) { if (token === generation) show({ state: "FAILED", message: String(error).slice(0, 240) }); }
  });
  cancel.addEventListener("click", async () => {
    if (!busy || !path) return;
    const token = generation;
    cancel.disabled = true;
    try {
      await invoke("reframe_cancel", { outputPath: path });
      if (token === generation) state.textContent = "Đang dừng đổi khung 16:9…";
    } catch (error) { if (token === generation) { state.textContent = String(error).slice(0, 240); cancel.disabled = false; } }
  });
  for (const [id, command] of [["open", "open_path"], ["folder", "reveal_path"]]) $(id).addEventListener("click", () => {
    if (resultPath) void invoke(command, { path: resultPath }).catch(() => undefined);
  });

  return { select(job) {
    disclosure();
    const outputPath = job?.status.state === "COMPLETED" ? job.outputPath : null;
    if (!outputPath) section.hidden = true;
    const next = outputPath ? `${job!.id}\n${outputPath}` : "";
    if (selection === next) return;
    selection = next;
    const token = ++generation;
    window.clearInterval(timer); timer = 0;
    section.hidden = true; path = ""; info = null; busy = false; suggesting = false; resultPath = "";
    releaseFrame(); form.reset(); controls(); drawPreview(); state.textContent = ""; $("results").hidden = true;
    if (!outputPath) return;
    void invoke("reframe_info", { outputPath }).then(async value => {
      if (token !== generation) return;
      info = parseInfo(value);
      if (!info?.vertical) return;
      path = outputPath; section.hidden = false; drawPreview();
      watch(outputPath, token); await poll(outputPath, token);
      if (token === generation && !busy) { window.clearInterval(timer); timer = 0; }
    }).catch(() => undefined);
  } };
}

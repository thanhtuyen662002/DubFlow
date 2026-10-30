import { buildRequest, charCount, parseInfo, parseStatus, type PackageInfo, type PackageStatus } from "./model.ts";
import { fileName } from "../product/model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Job = { id: string; outputPath: string | null; status: { state: string } };

/** Independent selection tokens keep async results attached to their job. */
export function mountPackage(invoke: Invoke, settings: { cpuOnly(): boolean }): { select(job: Job | null): void } {
  const $ = <T extends HTMLElement>(id: string): T => {
    const node = document.querySelector<T>(`#package-${id}`);
    if (!node) throw new Error(`Missing package-${id}`);
    return node;
  };
  const section = $<HTMLDetailsElement>("section");
  const form = $<HTMLFormElement>("form");
  const fields = $<HTMLFieldSetElement>("fields");
  const intro = $<HTMLTextAreaElement>("intro"), outro = $<HTMLTextAreaElement>("outro");
  const background = $<HTMLSelectElement>("background"), speed = $<HTMLSelectElement>("speed");
  const volume = $<HTMLInputElement>("volume");
  const cancel = $<HTMLButtonElement>("cancel");
  const state = $("state");
  let selection = "", path = "", generation = 0, polling = 0, statusRead = 0;
  let info: PackageInfo | null = null, music: string | null = null, result = "", busy = false;

  function counts(): void {
    $("intro-count").textContent = `${charCount(intro.value)}/600`;
    $("outro-count").textContent = `${charCount(outro.value)}/600`;
  }

  function setMusic(value: string | null): void {
    music = value;
    $("music-name").textContent = value ? fileName(value) : "Chưa chọn nhạc";
    $("music-clear").hidden = !value;
    $("background-row").hidden = !value;
    $("volume-row").hidden = !value;
    if (!value) background.value = "keep";
  }

  function show(status: PackageStatus): void {
    state.textContent = status.message;
    busy = status.state === "RUNNING";
    fields.disabled = busy || !info;
    cancel.hidden = !busy;
    cancel.disabled = false;
    result = status.state === "COMPLETED" ? status.path ?? "" : "";
    $("results").hidden = !result;
    if (!busy) { window.clearInterval(polling); polling = 0; }
  }

  async function poll(outputPath: string, token: number): Promise<void> {
    const read = ++statusRead;
    try {
      const status = parseStatus(await invoke<unknown>("package_status", { outputPath }));
      if (token === generation && read === statusRead && status) show(status);
    } catch {
      // Atomic status replacement can race a read; try again on the next tick.
    }
  }

  function watch(outputPath: string, token: number): void {
    window.clearInterval(polling);
    polling = window.setInterval(() => void poll(outputPath, token), 1500);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (!info || busy) return;
    const request = buildRequest({ outputPath: path, intro: intro.value, outro: outro.value, voiceId: info.voice_id,
      speed: Number(speed.value), music, musicVolume: Number(volume.value) / 100, background: background.value,
      cpuOnly: settings.cpuOnly() }, info);
    if (typeof request === "string") { state.textContent = request; return; }
    const token = ++generation, outputPath = path;
    show({ state: "RUNNING", message: "Đang khởi động đóng gói…" });
    try {
      await invoke("package_video", { request });
      if (token !== generation) return;
      watch(outputPath, token);
      await poll(outputPath, token);
    } catch (error) {
      if (token === generation) show({ state: "FAILED", message: String(error).slice(0, 240) });
    }
  });
  cancel.addEventListener("click", async () => {
    if (!busy || !path) return;
    const token = generation, outputPath = path;
    cancel.disabled = true;
    try {
      await invoke("package_cancel", { outputPath });
      if (token === generation) state.textContent = "Đang dừng đóng gói…";
    } catch (error) {
      if (token === generation) { state.textContent = String(error).slice(0, 240); cancel.disabled = false; }
    }
  });
  $("music-pick").addEventListener("click", async () => {
    const token = generation;
    try {
      const [picked] = await invoke<string[]>("pick_product_files", { kind: "music" });
      if (token === generation && picked) { setMusic(picked); background.value = "replace"; }
    } catch (error) {
      if (token === generation) state.textContent = String(error).slice(0, 240);
    }
  });
  $("music-clear").addEventListener("click", () => setMusic(null));
  intro.addEventListener("input", counts);
  outro.addEventListener("input", counts);
  volume.addEventListener("input", () => { $("volume-value").textContent = `${volume.value}%`; });
  $("open").addEventListener("click", () => { if (result) void invoke("open_path", { path: result }).catch(() => undefined); });
  $("folder").addEventListener("click", () => { if (result) void invoke("reveal_path", { path: result }).catch(() => undefined); });

  return {
    select(job) {
      const outputPath = job?.status.state === "COMPLETED" ? job.outputPath : null;
      section.hidden = !outputPath;
      const next = outputPath ? `${job!.id}\n${outputPath}` : "";
      if (selection === next) return;
      selection = next;
      const token = ++generation;
      path = outputPath ?? "";
      info = null; busy = false; result = "";
      window.clearInterval(polling); polling = 0;
      form.reset(); counts(); setMusic(null);
      fields.disabled = true;
      cancel.hidden = true;
      $("results").hidden = true;
      $("volume-value").textContent = `${volume.value}%`;
      state.textContent = "";
      if (!outputPath) return;
      state.textContent = "Đang đọc thông tin video…";
      void invoke<unknown>("package_info", { outputPath }).then(async (value) => {
        if (token !== generation) return;
        info = parseInfo(value);
        if (!info) throw new Error("Thông tin đóng gói không hợp lệ");
        $<HTMLOptionElement>("duck").disabled = !info.has_background_stem;
        fields.disabled = false;
        state.textContent = "";
        await poll(outputPath, token);
        if (token === generation && busy) watch(outputPath, token);
      }).catch((error) => {
        if (token === generation) state.textContent = String(error).slice(0, 240);
      });
    },
  };
}

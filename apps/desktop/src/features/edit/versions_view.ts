/**
 * Phiên bản và mẫu (Issue #534): named versions of the open edit, kept beside
 * its draft, and layer templates (Chữ, lớp chồng, âm thanh) any video can
 * use. Restoring a version and applying a template are one undoable step.
 */
import type { ConfirmOptions } from "../../shell/confirm.ts";
import { applyLayers, layersSummary, serializeLayers, type Edit } from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type VersionsContext = {
  /** The open edit and how it is saved, or null when none is open. */
  open: () => { edit: Edit; outputPath: string; kind: string; project: () => Record<string, unknown> } | null;
  busy: () => boolean;
  /** A saved version's draft as an edit of the open video (null when it does not fit it). */
  parse: (project: unknown) => Edit | null;
  commit: (next: Edit) => void;
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  note: (text: string) => void;
  /** Called when the dialog closes (focus goes back). */
  closed: () => void;
};

export type VersionsDialog = {
  open(): void;
  close(): void;
  readonly shown: boolean;
};

type Saved = { id: string; name: string; saved_at: number; summary?: string | null };
type Tab = "versions" | "templates";

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing versions element: ${selector}`);
  return node;
};

/** "05/11 14:07" today and this year, else with the year. */
function when(seconds: number): string {
  const date = new Date(seconds * 1000);
  const pad = (value: number): string => String(value).padStart(2, "0");
  const day = `${pad(date.getDate())}/${pad(date.getMonth() + 1)}`;
  const year = date.getFullYear() === new Date().getFullYear() ? "" : `/${date.getFullYear()}`;
  return `${day}${year} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function mountVersions(invoke: Invoke, context: VersionsContext): VersionsDialog {
  const dialog = $("#snap-dialog");
  const tabs = [...dialog.querySelectorAll<HTMLButtonElement>("[data-vtab]")];
  const error = $("#snap-error");
  let tab: Tab = "versions";
  let versions: Saved[] = [];
  let templates: Saved[] = [];
  let working = false;

  function fail(text: string): void {
    error.textContent = text;
    error.hidden = !text;
  }

  function row(item: Saved, actions: Array<[string, string, () => void]>): HTMLLIElement {
    const li = document.createElement("li");
    li.className = "snap-row";
    const text = document.createElement("div");
    const name = document.createElement("strong");
    name.textContent = item.name;
    name.title = item.name;
    const meta = document.createElement("small");
    meta.textContent = [when(item.saved_at), item.summary ?? ""].filter(Boolean).join(" · ");
    text.append(name, meta);
    li.append(text);
    for (const [label, kind, run] of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `btn small ${kind}`;
      button.textContent = label;
      button.disabled = working;
      button.addEventListener("click", run);
      li.append(button);
    }
    return li;
  }

  function render(): void {
    for (const button of tabs) button.setAttribute("aria-selected", String(button.dataset.vtab === tab));
    $("#snap-versions").hidden = tab !== "versions";
    $("#snap-templates").hidden = tab !== "templates";
    $("#snap-list").replaceChildren(...versions.map((item) => row(item, [
      ["Khôi phục", "", () => void restore(item)], ["Xóa", "ghost", () => void remove("version", item)],
    ])));
    $("#snap-version-empty").hidden = versions.length > 0;
    $("#snap-template-list").replaceChildren(...templates.map((item) => row(item, [
      ["Áp dụng", "", () => void apply(item)], ["Xóa", "ghost", () => void remove("template", item)],
    ])));
    $("#snap-template-empty").hidden = templates.length > 0;
    for (const button of dialog.querySelectorAll<HTMLButtonElement>("form button")) button.disabled = working;
  }

  async function refresh(): Promise<void> {
    const opened = context.open();
    if (!opened) return;
    try {
      const [ownVersions, all] = await Promise.all([
        invoke<Saved[]>("edit_versions", { outputPath: opened.outputPath, kind: opened.kind }),
        invoke<Saved[]>("edit_templates"),
      ]);
      versions = ownVersions;
      templates = all;
      fail("");
    } catch (problem) {
      fail(`Không đọc được danh sách đã lưu: ${String(problem).slice(0, 160)}`);
    }
    render();
  }

  async function run(task: () => Promise<void>): Promise<void> {
    if (working) return;
    working = true;
    render();
    try {
      await task();
      fail("");
    } catch (problem) {
      fail(String(problem).slice(0, 200));
    } finally {
      working = false;
      render();
    }
  }

  function defaultName(prefix: string, count: number): string {
    return `${prefix} ${count + 1}`;
  }

  $("#snap-version-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const opened = context.open();
    if (!opened) return;
    const input = $<HTMLInputElement>("#snap-version-name");
    const name = input.value.trim() || defaultName("Phiên bản", versions.length);
    void run(async () => {
      await invoke("edit_version_save", { outputPath: opened.outputPath, kind: opened.kind, name, project: opened.project() });
      input.value = "";
      await refresh();
      context.note(`Đã lưu phiên bản "${name}".`);
    });
  });

  $("#snap-template-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const opened = context.open();
    if (!opened) return;
    const layers = serializeLayers(opened.edit);
    const summary = layersSummary(layers);
    const input = $<HTMLInputElement>("#snap-template-name");
    const name = input.value.trim() || defaultName("Mẫu", templates.length);
    void run(async () => {
      await invoke("edit_template_save", { name, summary, layers });
      input.value = "";
      await refresh();
      context.note(`Đã lưu mẫu "${name}" (${summary}).`);
    });
  });

  async function restore(item: Saved): Promise<void> {
    const opened = context.open();
    if (!opened || context.busy()) return;
    await run(async () => {
      const project = await invoke<unknown>("edit_version_load", { outputPath: opened.outputPath, kind: opened.kind, id: item.id });
      const edit = context.parse(project);
      if (!edit) throw new Error("Phiên bản này không còn khớp với video (video đã thay đổi?).");
      context.commit(edit);
      context.note(`Đã khôi phục "${item.name}". Ctrl+Z để quay lại bản trước.`);
      close();
    });
  }

  async function apply(item: Saved): Promise<void> {
    const opened = context.open();
    if (!opened || context.busy()) return;
    await run(async () => {
      const layers = await invoke<unknown>("edit_template_load", { id: item.id });
      const result = applyLayers(opened.edit, layers);
      if (result.edit === opened.edit) throw new Error("Mẫu này không có gì đặt được lên video này.");
      context.commit(result.edit);
      const skipped = result.skipped ? ` (bỏ qua ${result.skipped} lớp nằm ngoài video hoặc quá giới hạn)` : "";
      context.note(`Đã áp dụng mẫu "${item.name}": thêm ${result.added} lớp${skipped}. Ctrl+Z để bỏ.`);
      close();
    });
  }

  async function remove(what: "version" | "template", item: Saved): Promise<void> {
    const sure = await context.confirm({
      title: what === "version" ? "Xóa phiên bản?" : "Xóa mẫu?",
      message: `"${item.name}" sẽ bị xóa hẳn, không hoàn tác được.`,
      confirmLabel: "Xóa", danger: true,
    });
    const opened = context.open();
    if (!sure || !opened) return;
    await run(async () => {
      if (what === "version") await invoke("edit_version_delete", { outputPath: opened.outputPath, kind: opened.kind, id: item.id });
      else await invoke("edit_template_delete", { id: item.id });
      await refresh();
    });
  }

  for (const button of tabs) {
    button.addEventListener("click", () => {
      tab = button.dataset.vtab === "templates" ? "templates" : "versions";
      render();
    });
  }

  function open(): void {
    if (!context.open()) return;
    dialog.hidden = false;
    fail("");
    render();
    void refresh();
    $<HTMLInputElement>(tab === "versions" ? "#snap-version-name" : "#snap-template-name").focus();
  }

  function close(): void {
    if (dialog.hidden) return;
    dialog.hidden = true;
    context.closed();
  }

  $("#snap-close").addEventListener("click", close);
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    }
  });
  dialog.addEventListener("mousedown", (event) => {
    if (event.target === dialog) close();
  });

  return { open, close, get shown() { return !dialog.hidden; } };
}

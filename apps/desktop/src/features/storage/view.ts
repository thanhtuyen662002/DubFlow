/** Settings "Dung lượng" row, the "Dọn dẹp" dialog and the automatic clean. */
import {
  AFTER_JOB_DELAY_MS, AUTO_CATEGORIES, AUTO_CLEAN_DELAY_MS, AUTO_CLEAN_KEY, autoCleanDays, autoCleanEnabled, autoNotice,
  CATEGORY_TEXT, categorySize, cleanableBytes, cleanButtonLabel, defaultSelection, parseClean, parseReport,
  resultLine, summaryLine, type QueueOutputs, type StorageCategoryId, type StorageReport,
} from "./model.ts";
import { sizeLabel } from "../fetch/progress.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
type Options = {
  /** Output folders of the queue's jobs. */
  outputs: () => QueueOutputs;
  /** A small, non-blocking notice in the queue. */
  notice: (text: string) => void;
};
export type StorageControls = {
  /** Recompute the Settings summary (when Settings opens). */
  refreshSummary(): void;
  /** A job finished successfully: clean automatically a little later. */
  jobFinished(): void;
};

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

export function mountStorage(invoke: Invoke, options: Options): StorageControls {
  const summary = $("#storage-summary");
  const autoToggle = $<HTMLInputElement>("#auto-clean");
  const dialog = $("#cleanup-dialog");
  const list = $("#cleanup-list");
  const state = $("#cleanup-state");
  const result = $("#cleanup-result");
  const total = $("#cleanup-total");
  const run = $<HTMLButtonElement>("#cleanup-run");
  const later = $<HTMLButtonElement>("#cleanup-later");
  let report: StorageReport | null = null;
  let chosen = new Set<StorageCategoryId>();
  /** A clean (manual or automatic) is running: never two at once. */
  let cleaning = false;
  let returnFocus: HTMLElement | null = null;
  let afterJobTimer = 0;

  const storage = {
    get(): string | null {
      try { return window.localStorage.getItem(AUTO_CLEAN_KEY); } catch { return null; }
    },
    set(on: boolean): boolean {
      try {
        if (on) window.localStorage.removeItem(AUTO_CLEAN_KEY);
        else window.localStorage.setItem(AUTO_CLEAN_KEY, "0");
        return true;
      } catch { return false; }
    },
  };

  async function fetchReport(): Promise<StorageReport | null> {
    const outputs = options.outputs();
    return parseReport(await invoke<unknown>("storage_report", { outputDirs: [...outputs.finished, ...outputs.running], running: outputs.running }));
  }

  function refreshSummary(): void {
    summary.textContent = "Đang tính dung lượng…";
    void fetchReport().then((value) => {
      summary.textContent = value ? summaryLine(value) : "Không tính được dung lượng.";
    }).catch((error) => {
      summary.textContent = `Không tính được dung lượng: ${String(error).slice(0, 120)}`;
    });
  }

  function renderFooter(): void {
    const bytes = report ? cleanableBytes(report, chosen) : 0;
    total.textContent = report ? `Đã chọn ${sizeLabel(bytes)}` : "";
    run.textContent = cleanButtonLabel(bytes);
    run.disabled = cleaning || !report || chosen.size === 0;
    later.disabled = cleaning;
  }

  function renderList(): void {
    if (!report) {
      list.replaceChildren();
      return;
    }
    list.replaceChildren(...report.categories.map((category) => {
      const item = document.createElement("li");
      const label = document.createElement("label");
      const empty = category.busy || category.bytes === 0;
      label.className = `cleanup-item${empty ? " disabled" : ""}`;
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = chosen.has(category.id);
      box.disabled = empty || cleaning;
      box.addEventListener("change", () => {
        if (box.checked) chosen.add(category.id);
        else chosen.delete(category.id);
        renderFooter();
      });
      const text = document.createElement("span");
      text.className = "cleanup-text";
      const title = document.createElement("strong");
      title.textContent = CATEGORY_TEXT[category.id].title;
      const detail = document.createElement("small");
      detail.textContent = CATEGORY_TEXT[category.id].detail;
      text.append(title, detail);
      const size = document.createElement("span");
      size.className = "cleanup-size";
      size.textContent = categorySize(category);
      label.append(box, text, size);
      item.append(label);
      return item;
    }));
  }

  async function load(keepSelection: boolean): Promise<void> {
    state.textContent = "Đang tính dung lượng…";
    report = null;
    renderList();
    renderFooter();
    try {
      report = await fetchReport();
      if (!report) throw new Error("Không đọc được dung lượng");
      const available = defaultSelection(report);
      chosen = keepSelection ? new Set([...chosen].filter((id) => available.has(id))) : available;
      state.textContent = "";
      summary.textContent = summaryLine(report);
    } catch (error) {
      state.textContent = `Không tính được dung lượng: ${String(error).slice(0, 160)}`;
    }
    renderList();
    renderFooter();
  }

  function open(): void {
    returnFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    result.hidden = true;
    result.replaceChildren();
    dialog.hidden = false;
    later.focus();
    void load(false);
  }

  function close(): void {
    if (dialog.hidden || cleaning) return;
    dialog.hidden = true;
    if (returnFocus?.isConnected) returnFocus.focus();
  }

  function showResult(freed: number, skipped: string[]): void {
    const line = document.createElement("strong");
    line.textContent = resultLine({ freedBytes: freed, skipped });
    result.replaceChildren(line);
    if (skipped.length) {
      const note = document.createElement("p");
      note.textContent = "Đang được dùng, để lần sau:";
      const items = document.createElement("ul");
      items.append(...skipped.map((text) => {
        const entry = document.createElement("li");
        entry.textContent = text;
        return entry;
      }));
      result.append(note, items);
    }
    result.hidden = false;
  }

  async function cleanChosen(): Promise<void> {
    if (cleaning || !report || chosen.size === 0) return;
    cleaning = true;
    const categories = [...chosen];
    run.textContent = "Đang dọn…";
    renderList();
    run.disabled = true;
    later.disabled = true;
    result.hidden = true;
    let failure = "";
    try {
      const outputs = options.outputs();
      const cleaned = parseClean(await invoke<unknown>("storage_clean", {
        categories, outputDirs: outputs.finished, running: outputs.running, olderThanDays: null,
      }));
      showResult(cleaned.freedBytes, cleaned.skipped);
    } catch (error) {
      failure = `Không dọn được: ${String(error).slice(0, 160)}`;
    } finally {
      cleaning = false;
    }
    await load(true);
    if (failure) state.textContent = failure;
  }

  /** Silent unless it freed more than 1 GB. */
  async function autoClean(): Promise<void> {
    if (!autoCleanEnabled(storage.get()) || cleaning) return;
    cleaning = true;
    try {
      const outputs = options.outputs();
      const free = await invoke<number | null>("storage_free", { outputDirs: [...outputs.finished, ...outputs.running] }).catch(() => null);
      const cleaned = parseClean(await invoke<unknown>("storage_clean", {
        categories: [...AUTO_CATEGORIES], outputDirs: outputs.finished, running: outputs.running,
        olderThanDays: autoCleanDays(typeof free === "number" ? free : null),
      }));
      const text = autoNotice(cleaned.freedBytes);
      if (text) options.notice(text);
    } catch {
      // Best effort: the next launch or finished video tries again.
    } finally {
      cleaning = false;
    }
  }

  autoToggle.checked = autoCleanEnabled(storage.get());
  autoToggle.addEventListener("change", () => {
    if (!storage.set(autoToggle.checked)) autoToggle.checked = !autoToggle.checked;
  });
  $("#open-cleanup").addEventListener("click", open);
  $("#cleanup-close").addEventListener("click", close);
  later.addEventListener("click", close);
  run.addEventListener("click", () => void cleanChosen());
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  });
  dialog.addEventListener("mousedown", (event) => {
    if (event.target === dialog) close();
  });

  window.setTimeout(() => void autoClean(), AUTO_CLEAN_DELAY_MS);

  return {
    refreshSummary,
    jobFinished(): void {
      window.clearTimeout(afterJobTimer);
      afterJobTimer = window.setTimeout(() => void autoClean(), AFTER_JOB_DELAY_MS);
    },
  };
}

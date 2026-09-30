import { invoke } from "@tauri-apps/api/core";
import { connectDesktopShell } from "./shell/desktop_shell.ts";
import { createMockTransport } from "./shell/mock_transport.ts";
import { type SnapshotStorage } from "./features/queue/model.ts";

type ReleaseInfo = { version: string; channel: string; backend: string };

const STORAGE_KEY = "dubflow.preview.queue.v1";

function browserStorage(): SnapshotStorage {
  return {
    read: () => {
      try {
        return window.localStorage.getItem(STORAGE_KEY);
      } catch {
        return null;
      }
    },
    write: (value) => {
      try {
        window.localStorage.setItem(STORAGE_KEY, value);
      } catch {
        // The queue remains usable when a browser profile disallows storage.
      }
    },
  };
}

const transport = createMockTransport(browserStorage());

const $ = <T extends HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing desktop element: ${selector}`);
  return element;
};

const summary = $("#queue-summary");
const queueState = $("#queue-state");
const queueList = $("#queue-list");
const detail = $("#job-detail");

function render(model: Parameters<Parameters<typeof connectDesktopShell>[1]>[0]): void {
  const { view } = model;
  const total = view.summary.total;
  summary.textContent = `${total} video${total === 1 ? "" : "s"}`;
  queueState.textContent = total === 0 ? "Sẵn sàng" : `${view.summary.queued} đang chờ`;
  queueList.replaceChildren();
  if (view.rows.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "Chưa có video. Bấm “Thêm video” để bắt đầu.";
    queueList.append(empty);
  } else {
    for (const row of view.rows) {
      const item = document.createElement("li");
      item.className = `job${row.selected ? " selected" : ""}`;
      item.tabIndex = 0;
      item.innerHTML = `<span class="job-title"></span><span class="job-progress"></span><span class="job-status"></span>`;
      (item.querySelector(".job-title") as HTMLElement).textContent = row.title;
      (item.querySelector(".job-progress") as HTMLElement).textContent = row.progress;
      (item.querySelector(".job-status") as HTMLElement).textContent = row.status;
      const select = () => transport.queue.selectJob(row.id);
      item.addEventListener("click", select);
      item.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") select();
      });
      queueList.append(item);
    }
  }

  detail.replaceChildren();
  if (!view.selected) {
    const empty = document.createElement("div");
    empty.className = "detail-empty";
    empty.textContent = "Chọn một video trong hàng đợi.";
    detail.append(empty);
    return;
  }
  const card = document.createElement("dl");
  card.className = "detail-card";
  for (const [label, value] of [
    ["Tên", view.selected.displayName],
    ["Đường dẫn", view.selected.sourcePath],
    ["Trạng thái", view.selected.status.message],
  ]) {
    const term = document.createElement("dt");
    term.textContent = label;
    const description = document.createElement("dd");
    description.textContent = value;
    card.append(term, description);
  }
  detail.append(card);
}

const shell = connectDesktopShell(transport.queue, render);

$("#add-files").addEventListener("click", () => $("#file-picker").click());
$("#file-picker").addEventListener("change", (event) => {
  const input = event.currentTarget as HTMLInputElement;
  const paths = Array.from(input.files ?? []).map((file) => file.name);
  if (paths.length > 0) transport.queue.addPaths(paths);
  input.value = "";
});

$("#clear-queue").addEventListener("click", () => {
  for (const job of transport.queue.getSnapshot().jobs) transport.queue.removeJob(job.id);
});

void invoke<ReleaseInfo>("release_info")
  .then((info) => {
    $("#release-version").textContent = `v${info.version}`;
    $("#release-channel").textContent = info.channel;
  })
  .catch(() => {
    $("#release-version").textContent = "development";
  });

window.addEventListener("beforeunload", () => shell.dispose());

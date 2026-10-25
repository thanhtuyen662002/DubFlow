/** In-app updates: a sidebar card when a newer signed version exists, and a
 * dialog listing installed versions so the user can roll back. */
type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type UpdateInfo = { version: string; notes: string; sizeBytes: number | null };
export type InstalledVersion = { version: string; channel: string; installedAt: string };

const CHECK_KEY = "dubflow.updateCheckedAt.v1";
const SKIP_KEY = "dubflow.updateSkipped.v1";
const DAY_MS = 24 * 60 * 60 * 1000;

export function parseCheck(value: unknown): { ok: true; update: UpdateInfo | null; unsupported: boolean } | { ok: false; message: string } {
  const object = (value ?? {}) as Record<string, unknown>;
  if (object.ok !== true) return { ok: false, message: typeof object.message === "string" ? object.message.slice(0, 200) : "Không kiểm tra được bản mới" };
  if (object.available !== true || typeof object.version !== "string") return { ok: true, update: null, unsupported: object.unsupported === true };
  const setup = (object.setup ?? {}) as Record<string, unknown>;
  const size = typeof setup.size_bytes === "number" && setup.size_bytes > 0 ? setup.size_bytes : null;
  return { ok: true, unsupported: false, update: { version: object.version.slice(0, 64), notes: typeof object.notes === "string" ? object.notes.slice(0, 2000) : "", sizeBytes: size } };
}

export function parseVersions(value: unknown): { current: string | null; versions: InstalledVersion[] } | null {
  const object = (value ?? {}) as Record<string, unknown>;
  if (object.ok === false || !Array.isArray(object.versions)) return null;
  const versions = object.versions.flatMap((raw) => {
    const item = (raw ?? {}) as Record<string, unknown>;
    return typeof item.version === "string" ? [{ version: item.version, channel: typeof item.channel === "string" ? item.channel : "", installedAt: typeof item.installed_at_utc === "string" ? item.installed_at_utc : "" }] : [];
  });
  return { current: typeof object.current === "string" ? object.current : null, versions };
}

/** Whether a background check is due (once a day; failures of storage never block a manual check). */
export function checkDue(now: number, lastChecked: string | null): boolean {
  const last = Number(lastChecked);
  return !Number.isFinite(last) || last <= 0 || now - last >= DAY_MS || last > now;
}

const storage = {
  get(key: string): string | null {
    try { return window.localStorage.getItem(key); } catch { return null; }
  },
  set(key: string, value: string): void {
    try { window.localStorage.setItem(key, value); } catch { /* convenience only */ }
  },
};

const $ = <T extends HTMLElement>(selector: string): T => {
  const node = document.querySelector<T>(selector);
  if (!node) throw new Error(`Missing ${selector}`);
  return node;
};

export function mountUpdates(invoke: Invoke): void {
  const card = $("#update-card");
  const title = $("#update-title");
  const detail = $("#update-detail");
  const install = $<HTMLButtonElement>("#update-install");
  const later = $<HTMLButtonElement>("#update-later");
  const check = $<HTMLButtonElement>("#update-check");
  const dialog = $("#versions-dialog");
  const list = $("#versions-list");
  const state = $("#versions-state");
  let pending: UpdateInfo | null = null;
  let busy = false;

  function showCard(update: UpdateInfo): void {
    pending = update;
    title.textContent = `Có bản mới ${update.version}`;
    const size = update.sizeBytes ? ` · ${Math.round(update.sizeBytes / 1_048_576)} MB` : "";
    detail.textContent = `Tải và cài đặt bản mới${size}. Bản hiện tại vẫn được giữ để quay lại khi cần.`;
    install.disabled = false;
    install.textContent = "Cập nhật";
    card.hidden = false;
  }

  async function runCheck(manual: boolean): Promise<void> {
    if (busy) return;
    busy = true;
    check.disabled = true;
    if (manual) check.textContent = "Đang kiểm tra…";
    try {
      const result = parseCheck(await invoke<unknown>("check_update"));
      storage.set(CHECK_KEY, String(Date.now()));
      if (!result.ok) {
        if (manual) check.textContent = "Không kiểm tra được";
      } else if (result.update && (manual || storage.get(SKIP_KEY) !== result.update.version)) {
        showCard(result.update);
        check.textContent = "Kiểm tra cập nhật";
      } else {
        check.textContent = manual ? (result.unsupported ? "Bản phát triển" : "Đang dùng bản mới nhất") : "Kiểm tra cập nhật";
      }
    } catch (error) {
      if (manual) check.textContent = String(error).slice(0, 60);
    } finally {
      busy = false;
      check.disabled = false;
    }
  }

  install.addEventListener("click", async () => {
    if (!pending || busy) return;
    busy = true;
    install.disabled = true;
    later.disabled = true;
    install.textContent = "Đang tải…";
    detail.textContent = "Đang tải và kiểm tra chữ ký, có thể mất vài phút.";
    try {
      const result = (await invoke<Record<string, unknown>>("download_update")) ?? {};
      if (result.ok !== true || typeof result.path !== "string") throw new Error(typeof result.message === "string" ? result.message : "Tải bản cập nhật thất bại");
      install.textContent = "Đang mở bộ cài…";
      detail.textContent = "DubFlow sẽ đóng để cài bản mới, rồi tự mở lại.";
      await invoke("install_update", { path: result.path });
    } catch (error) {
      detail.textContent = `Không cập nhật được: ${String(error).slice(0, 160)}`;
      install.textContent = "Thử lại";
      install.disabled = false;
      later.disabled = false;
    } finally {
      busy = false;
    }
  });
  later.addEventListener("click", () => {
    if (pending) storage.set(SKIP_KEY, pending.version);
    card.hidden = true;
  });
  check.addEventListener("click", () => void runCheck(true));

  async function renderVersions(): Promise<void> {
    state.textContent = "Đang đọc…";
    list.replaceChildren();
    try {
      const parsed = parseVersions(await invoke<unknown>("list_versions"));
      if (!parsed) {
        state.textContent = "Không đọc được danh sách phiên bản.";
        return;
      }
      state.textContent = parsed.versions.length > 1 ? "Chọn bản muốn dùng; thay đổi có hiệu lực khi DubFlow khởi động lại." : "Chỉ có một phiên bản được cài.";
      list.replaceChildren(...parsed.versions.map((item) => {
        const row = document.createElement("li");
        const label = document.createElement("span");
        label.textContent = `${item.version}${item.channel && item.channel !== "stable" ? ` (${item.channel})` : ""}`;
        row.append(label);
        if (item.version === parsed.current) {
          const tag = document.createElement("span");
          tag.className = "tag";
          tag.textContent = "đang dùng";
          row.append(tag);
        } else {
          const use = document.createElement("button");
          use.type = "button";
          use.className = "btn";
          use.textContent = "Dùng bản này";
          use.addEventListener("click", async () => {
            use.disabled = true;
            state.textContent = `Đang kiểm tra và chuyển sang ${item.version}…`;
            try {
              const result = (await invoke<Record<string, unknown>>("activate_version", { version: item.version })) ?? {};
              if (result.ok === false) throw new Error(typeof result.message === "string" ? result.message : "không chuyển được");
              state.textContent = `Đã chuyển sang ${item.version}.`;
              const restart = document.createElement("button");
              restart.type = "button";
              restart.className = "btn primary";
              restart.textContent = "Khởi động lại ngay";
              restart.addEventListener("click", () => void invoke("restart_app"));
              state.append(" ", restart);
            } catch (error) {
              state.textContent = `Không chuyển được: ${String(error).slice(0, 160)}`;
              use.disabled = false;
            }
          });
          row.append(use);
        }
        return row;
      }));
    } catch (error) {
      state.textContent = String(error).slice(0, 200);
    }
  }
  $("#release-version").addEventListener("click", () => {
    dialog.hidden = false;
    void renderVersions();
  });
  $("#versions-close").addEventListener("click", () => { dialog.hidden = true; });
  dialog.addEventListener("keydown", (event) => { if (event.key === "Escape") dialog.hidden = true; });

  if (checkDue(Date.now(), storage.get(CHECK_KEY))) window.setTimeout(() => void runCheck(false), 4000);
}

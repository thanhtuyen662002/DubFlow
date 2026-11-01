/**
 * The sound-effect pack (downloaded on first use) and the user's own sound
 * folder, shared by Cài đặt and the editor's Âm thanh mode (Issue #420).
 */
type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

export type PackState = "idle" | "downloading" | "installing" | "failed";
export type PackStatus = {
  installed: boolean; version: string; count: number; size_bytes: number; state: PackState; received: number; error: string | null;
};
export type UserAudio = { path: string; name: string; size_bytes: number };
export type AudioFacts = { path: string; title: string; duration: number; size_bytes: number };

export type SfxLibrary = {
  status(): PackStatus | null;
  refresh(): Promise<PackStatus | null>;
  /** Download and install the pack when missing; true once it is installed. */
  install(): Promise<boolean>;
  /** Called after every status change; returns the unsubscribe. */
  subscribe(listener: () => void): () => void;
  folder(): string | null;
  setFolder(path: string | null): void;
  /** The folder dialog; the chosen folder is remembered. */
  pickFolder(): Promise<string | null>;
  /** The installed file of a sound effect, or null when the pack is not installed. */
  soundPath(id: string): Promise<string | null>;
};

export const AUDIO_FOLDER_KEY = "dubflow.audioFolder.v1";
export const AUDIO_EXTENSIONS = ["mp3", "wav", "ogg", "m4a", "aac", "flac"] as const;
const POLL_MS = 500;

export function isAudioPath(path: string): boolean {
  const extension = path.split(".").pop()?.toLowerCase() ?? "";
  return (AUDIO_EXTENSIONS as readonly string[]).includes(extension) && /[\\/]/.test(path);
}

/** "2 MB", "0,8 MB" */
export function sizeText(bytes: number): string {
  if (!(bytes > 0)) return "";
  const mb = bytes / 1_000_000;
  return mb >= 10 ? `${Math.round(mb)} MB` : `${String(Math.max(0.1, Math.round(mb * 10) / 10)).replace(".", ",")} MB`;
}

/** The pack line in Cài đặt and the library: not downloaded, downloading, installed or failed. */
export function packSummary(status: PackStatus | null): string {
  if (!status) return "Đang kiểm tra…";
  if (status.state === "downloading") {
    const percent = status.size_bytes > 0 ? Math.min(99, Math.floor(status.received / status.size_bytes * 100)) : 0;
    return `Đang tải ${percent}%`;
  }
  if (status.state === "installing") return "Đang giải nén…";
  if (status.installed) return `Đã tải ${status.count} âm thanh`;
  const size = sizeText(status.size_bytes);
  if (status.state === "failed") return `Chưa tải được${status.error ? `: ${status.error}` : ""}`;
  return `Chưa tải${size ? ` · ${size}` : ""}`;
}

export function createSfxLibrary(invoke: Invoke): SfxLibrary {
  let current: PackStatus | null = null;
  const listeners = new Set<() => void>();
  let polling: Promise<boolean> | null = null;
  const paths = new Map<string, string | null>();

  const notify = (): void => { for (const listener of [...listeners]) listener(); };

  async function refresh(): Promise<PackStatus | null> {
    try {
      current = await invoke<PackStatus>("sfx_status");
    } catch (error) {
      current = { installed: false, version: "", count: 0, size_bytes: 0, state: "failed", received: 0, error: String(error).slice(0, 160) };
    }
    if (!current.installed) paths.clear();
    notify();
    return current;
  }

  async function waitInstalled(): Promise<boolean> {
    for (;;) {
      const status = await refresh();
      if (!status || (status.state !== "downloading" && status.state !== "installing")) return !!status?.installed;
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    }
  }

  function install(): Promise<boolean> {
    if (current?.installed) return Promise.resolve(true);
    if (!polling) {
      polling = (async () => {
        try {
          current = await invoke<PackStatus>("sfx_install");
          notify();
        } catch (error) {
          current = { ...(current ?? { installed: false, version: "", count: 0, size_bytes: 0, received: 0 }), state: "failed", error: String(error).slice(0, 160) };
          notify();
          return false;
        }
        return waitInstalled();
      })().finally(() => { polling = null; });
    }
    return polling;
  }

  function folder(): string | null {
    try {
      return window.localStorage.getItem(AUDIO_FOLDER_KEY) || null;
    } catch {
      return null;
    }
  }

  function setFolder(path: string | null): void {
    try {
      if (path) window.localStorage.setItem(AUDIO_FOLDER_KEY, path);
      else window.localStorage.removeItem(AUDIO_FOLDER_KEY);
    } catch {
      // Private storage: the folder is kept for this run only by the caller.
    }
    notify();
  }

  void refresh();
  return {
    status: () => current,
    refresh,
    install,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    folder,
    setFolder,
    pickFolder: async () => {
      const picked = await invoke<string | null>("pick_audio_folder");
      if (picked) setFolder(picked);
      return picked;
    },
    soundPath: async (id) => {
      if (paths.has(id)) return paths.get(id) ?? null;
      const path = await invoke<string | null>("sfx_path", { id }).catch(() => null);
      if (path) paths.set(id, path);
      return path;
    },
  };
}

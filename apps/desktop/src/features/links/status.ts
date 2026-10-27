/** What a link row says while it waits, is analysed or downloads. */
export type FetchStatus = {
  scan_id: string; url: string; provider: string;
  state: "queued" | "preparing" | "downloading" | "done" | "failed";
  detail: string | null; queue_position: number | null; started_at_ms: number | null;
  downloaded_bytes: number; total_bytes: number | null; media_path: string | null; error: string | null;
};

/** `m:ss`, or `h:mm:ss` from one hour. */
export function clock(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

function megabytes(bytes: number): string {
  return (bytes / 1_048_576).toFixed(bytes < 10_485_760 ? 1 : 0);
}

export function fetchMessage(status: FetchStatus, nowMs: number): string {
  const elapsed = status.started_at_ms ? ` · ${clock(nowMs - status.started_at_ms)}` : "";
  switch (status.state) {
    case "failed":
      return status.error ?? "Tải link thất bại";
    case "queued":
      return status.queue_position && status.queue_position > 1
        ? `Đang chờ: còn ${status.queue_position - 1} link phía trước`
        : "Đang chờ link trước tải xong";
    case "preparing":
      return `${status.detail ?? "Đang phân tích link…"}${elapsed}`;
    case "downloading":
      return (status.total_bytes
        ? `Đang tải ${megabytes(status.downloaded_bytes)}/${megabytes(status.total_bytes)} MB`
        : `Đang tải ${megabytes(status.downloaded_bytes)} MB`) + elapsed;
    default:
      return "Đã tải xong";
  }
}

export function fetchBadge(state: FetchStatus["state"]): string {
  return { queued: "Đang chờ", preparing: "Đang phân tích", downloading: "Đang tải", done: "Xong", failed: "Lỗi" }[state];
}

/** The last two folders of a path, for a row that has little room. */
export function shortPath(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length > 3 ? `…\\${parts.slice(-3).join("\\")}` : path;
}

/** Download speed and time left for a link, from the byte counts seen at each poll. */
export type Sample = { bytes: number; atMs: number };

/** Speed is measured over this much recent history. */
export const SPEED_WINDOW_MS = 6000;
const MAX_SAMPLES = 40;

/**
 * The history with one more poll. Fewer bytes than before (a restarted
 * download) starts a new history; samples older than the window are dropped,
 * except the last one before it, which anchors the measurement.
 */
export function addSample(samples: readonly Sample[], bytes: number, atMs: number): Sample[] {
  const last = samples.at(-1);
  if (!Number.isFinite(bytes) || bytes < 0 || !Number.isFinite(atMs)) return [...samples];
  if (last && (bytes < last.bytes || atMs < last.atMs)) return [{ bytes, atMs }];
  const next = last && last.atMs === atMs ? [...samples.slice(0, -1), { bytes, atMs }] : [...samples, { bytes, atMs }];
  const from = atMs - SPEED_WINDOW_MS;
  const firstInside = next.findIndex((sample) => sample.atMs >= from);
  return next.slice(Math.max(0, firstInside - 1)).slice(-MAX_SAMPLES);
}

/** Bytes per second over the last ~6 s, or null until there is enough history. */
export function speed(samples: readonly Sample[], windowMs = SPEED_WINDOW_MS): number | null {
  const last = samples.at(-1);
  if (!last) return null;
  const first = samples.find((sample) => sample.atMs >= last.atMs - windowMs) ?? last;
  const seconds = (last.atMs - first.atMs) / 1000;
  if (seconds < 0.9) return null;
  return Math.max(0, (last.bytes - first.bytes) / seconds);
}

/** Milliseconds until done at the current speed, or null when unknown. */
export function remainingMs(downloaded: number, total: number | null, bytesPerSecond: number | null): number | null {
  if (!total || total <= downloaded || !bytesPerSecond || bytesPerSecond <= 0) return null;
  return ((total - downloaded) / bytesPerSecond) * 1000;
}

/** Vietnamese number: "." between thousands, "," before decimals. */
export function viNumber(value: number, decimals = 0): string {
  const [whole, fraction] = Math.max(0, value).toFixed(decimals).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return fraction ? `${grouped},${fraction}` : grouped;
}

/** Megabytes, with one decimal below 10 MB. */
export function megabytes(bytes: number): string {
  const mb = bytes / 1_048_576;
  return viNumber(mb, mb < 10 ? 1 : 0);
}

export function formatSpeed(bytesPerSecond: number): string {
  const mb = bytesPerSecond / 1_048_576;
  return mb >= 0.1 ? `${viNumber(mb, mb < 10 ? 1 : 0)} MB/s` : `${viNumber(bytesPerSecond / 1024)} KB/s`;
}

export function formatRemaining(ms: number): string {
  const seconds = Math.max(1, Math.ceil(ms / 1000));
  if (seconds < 90) return `còn ${seconds} giây`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `còn ${minutes} phút`;
  const hours = Math.floor(minutes / 60);
  return minutes % 60 ? `còn ${hours} giờ ${minutes % 60} phút` : `còn ${hours} giờ`;
}

/** Fraction done (0–1), or null when the size is unknown. */
export function fractionDone(downloaded: number, total: number | null): number | null {
  return total && total > 0 ? Math.min(1, Math.max(0, downloaded / total)) : null;
}

/**
 * `42% · 120 / 286 MB · 3,1 MB/s · còn 54 giây`, or `120 MB · 3,1 MB/s` when
 * the size is unknown; speed and time left appear once they can be measured.
 * `compact` drops the sizes when the percent is known, for narrow list rows.
 */
export function progressLine(downloaded: number, total: number | null, samples: readonly Sample[], compact = false): string {
  const rate = speed(samples);
  const fraction = fractionDone(downloaded, total);
  const parts = fraction === null
    ? [`${megabytes(downloaded)} MB`]
    : compact ? [`${Math.floor(fraction * 100)}%`] : [`${Math.floor(fraction * 100)}%`, `${megabytes(downloaded)} / ${megabytes(total!)} MB`];
  if (rate !== null && rate > 0) {
    parts.push(formatSpeed(rate));
    const left = remainingMs(downloaded, total, rate);
    if (left !== null) parts.push(formatRemaining(left));
  }
  return parts.join(" · ");
}

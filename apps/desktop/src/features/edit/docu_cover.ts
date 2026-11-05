/**
 * The cover a documentary burns in at render (Issue #520): per source, the
 * caption band stretched over (full width, top–bottom as fractions of the
 * source height) and the logo boxes blurred. Chỉnh sửa shows them on a frame
 * of each source so they can be resized or removed, then renders the film again.
 */

export type Logo = [number, number, number, number];
export type SourceCover = { id: string; title: string; video: string; band: { top: number; bottom: number } | null; logos: Logo[] };

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const round = (value: number): number => Math.round(value * 10_000) / 10_000;

function obj(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function logoOf(value: unknown): Logo | null {
  if (!Array.isArray(value) || value.length !== 4 || !value.every(finite)) return null;
  const [x, y, w, h] = value as number[];
  return x >= 0 && y >= 0 && w >= 0.01 && h >= 0.01 && x + w <= 1.000001 && y + h <= 1.000001 ? [x, y, w, h] : null;
}

/** Each source of the project with its current cover, from docu_open's spec and docu_plan_status's plan. */
export function sourceCovers(open: unknown, planStatus: unknown): SourceCover[] {
  const spec = obj(obj(open)?.spec);
  const plan = obj(obj(planStatus)?.plan);
  const framings = new Map<string, Record<string, unknown>>();
  for (const raw of Array.isArray(plan?.sources) ? plan.sources : []) {
    const source = obj(raw);
    if (source && typeof source.id === "string") framings.set(source.id, obj(source.framing) ?? {});
  }
  if (!plan) return [];
  return (Array.isArray(spec?.sources) ? spec.sources : []).flatMap((raw) => {
    const source = obj(raw);
    if (!source || typeof source.id !== "string" || typeof source.video !== "string") return [];
    const framing = framings.get(source.id);
    if (!framing) return [];
    const band = obj(framing.band);
    const top = band?.top, bottom = band?.bottom;
    return [{
      id: source.id, title: typeof source.title === "string" ? source.title : source.id, video: source.video,
      // A band framing hides by zooming is not drawn over; only one it cannot clear is stretched (roughcut.video_filter).
      band: finite(top) && finite(bottom) && top < bottom && framing.clear === false ? { top, bottom } : null,
      logos: (Array.isArray(framing.logos) ? framing.logos : []).map(logoOf).filter((logo): logo is Logo => !!logo).slice(0, 16),
    }];
  });
}

export function sameCover(a: SourceCover, b: SourceCover): boolean {
  return JSON.stringify([a.band, a.logos]) === JSON.stringify([b.band, b.logos]);
}

/** The band kept inside the frame, at least 1% tall, top above bottom. */
export function clampBand(top: number, bottom: number): { top: number; bottom: number } {
  const t = Math.min(0.99, Math.max(0, top));
  const b = Math.min(1, Math.max(t + 0.01, bottom));
  return { top: round(t), bottom: round(b) };
}

/** What docu_cover takes for one source. */
export function coverRequest(dir: string, cover: SourceCover): { dir: string; source: string; band: [number, number] | null; logos: Logo[] } {
  return { dir, source: cover.id, band: cover.band ? [round(cover.band.top), round(cover.band.bottom)] : null,
    logos: cover.logos.map((logo) => logo.map(round) as Logo) };
}

/** The project folder of a documentary's documentary.mp4. */
export function docuDir(outputPath: string): string {
  return outputPath.replace(/[\\/][^\\/]+$/, "");
}

/**
 * The apply button: enabled for unsaved changes, or to render again cover
 * edits already saved by a render that then failed or was cancelled.
 */
export function applyButton(changed: number, unrendered: boolean, rendering: boolean): { enabled: boolean; label: string; state: string } {
  if (rendering) return { enabled: false, label: "Đang dựng lại phim…", state: "" };
  if (changed) return { enabled: true, label: "Dựng lại phim với vùng che mới", state: `${changed} nguồn có vùng che mới, chưa dựng lại.` };
  if (unrendered) return { enabled: true, label: "Dựng lại phim", state: "Vùng che mới đã lưu nhưng phim chưa dựng lại xong." };
  return { enabled: false, label: "Dựng lại phim với vùng che mới", state: "" };
}


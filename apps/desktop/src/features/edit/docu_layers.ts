/**
 * A documentary opened as layers (Issue #525): what its film burns in (number
 * cards, English captions, the music bed) and what the editor puts back in
 * the draft once the film is rendered clean.
 */
import { calloutTexts, type Callout, type OpenDocuRun } from "../docu/model.ts";

export type Burned = { graphics: boolean; captions: boolean; music: string };
/** What docu_layers answers: what was burned before, and the kept bed. */
export type Split = Burned & { bed: string | null; music_path?: string | null };
/** What the reopened draft gets. */
export type Layers = { texts: Callout[]; burnCaptions: boolean; bed: string | null };

/** What the last render burned in, or null when the picture is already clean. */
export function burned(open: OpenDocuRun | null): Burned | null {
  const overlays = open?.render.status?.state === "COMPLETED" ? open.render.status.overlays : undefined;
  if (!overlays) return null;
  const music = overlays.music && overlays.music !== "none" ? overlays.music : "none";
  return overlays.graphics || overlays.captions || music !== "none" ? { graphics: overlays.graphics, captions: overlays.captions, music } : null;
}

/** "số liệu, phụ đề và nhạc nền". */
export function burnedWords(value: Burned): string {
  const parts = [value.graphics ? "số liệu" : "", value.captions ? "phụ đề" : "", value.music !== "none" ? "nhạc nền" : ""].filter(Boolean);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} và ${parts.at(-1)}` : parts[0] ?? "";
}

/** The number cards as Chữ texts (only those the film drew), the captions as burned Phụ đề, the bed as a sound. */
export function layersFrom(open: OpenDocuRun | null, split: Split): Layers {
  const texts = split.graphics && open ? calloutTexts(open.script, open.narration.timeline, [], { graphics: true }) : [];
  return { texts, burnCaptions: split.captions, bed: split.bed };
}

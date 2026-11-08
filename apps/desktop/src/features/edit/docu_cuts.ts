/**
 * The cuts of a documentary (Issue #630): every planned slice of the film as
 * its own clip in Chỉnh sửa. A cut can be trimmed against its neighbour in
 * the same scene, swapped with another, removed, pointed at other footage and
 * given its own cover; the narration timing of each scene stays, so the film
 * keeps its length and its draft (texts, sounds) still fits.
 */
import type { Logo } from "./docu_cover.ts";

export type CutCover = { band: { top: number; bottom: number } | null; logos: Logo[] };
export type Cut = {
  scene: string;
  /** Where the footage was planned: [scene_id, index]; the engine keeps how it was planned while it is unchanged. */
  from: [string, number] | null;
  source: string;
  /** Seconds into the source. */
  start: number;
  /** Seconds into the film. */
  at: number;
  /** A generated picture (map, data slide) instead of footage. */
  kind: string | null;
  /** Its own caption band and logo boxes; null: its source's. */
  cover: CutCover | null;
};
export type SceneSpan = { id: string; start: number; end: number };
export type CutPlan = { scenes: SceneSpan[]; cuts: Cut[]; durations: Record<string, number>; duration: number };

/** The shortest a cut may get (the engine's MIN_CUT). */
export const MIN_CUT = 0.5;

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const round = (value: number): number => Math.round(value * 1000) / 1000;

function obj(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function coverOf(value: unknown): CutCover | null {
  const cover = obj(value);
  if (!cover) return null;
  const band = obj(cover.band);
  const logos = (Array.isArray(cover.logos) ? cover.logos : []).filter((logo): logo is Logo =>
    Array.isArray(logo) && logo.length === 4 && logo.every(finite));
  return { band: band && finite(band.top) && finite(band.bottom) && band.top < band.bottom ? { top: band.top, bottom: band.bottom } : null, logos };
}

/** The film's cuts from docu_plan_status's plan, in film order; null without a usable plan. */
export function cutsFrom(planStatus: unknown): CutPlan | null {
  const plan = obj(obj(planStatus)?.plan);
  if (!plan || !Array.isArray(plan.scenes) || !Array.isArray(plan.sources)) return null;
  const durations: Record<string, number> = {};
  for (const raw of plan.sources) {
    const source = obj(raw);
    if (source && typeof source.id === "string" && finite(source.duration)) durations[source.id] = source.duration;
  }
  const scenes: SceneSpan[] = [];
  const cuts: Cut[] = [];
  for (const raw of plan.scenes) {
    const scene = obj(raw);
    if (!scene || typeof scene.scene_id !== "string" || !finite(scene.start) || !finite(scene.end) || !Array.isArray(scene.slices)) return null;
    scenes.push({ id: scene.scene_id, start: scene.start, end: scene.end });
    scene.slices.forEach((value, index) => {
      const piece = obj(value);
      if (!piece || typeof piece.source_id !== "string" || !finite(piece.start) || !finite(piece.at)) return;
      cuts.push({ scene: scene.scene_id as string, from: [scene.scene_id as string, index], source: piece.source_id, start: piece.start, at: piece.at,
        kind: typeof piece.kind === "string" && piece.kind !== "footage" ? piece.kind : null, cover: coverOf(piece.cover) });
    });
  }
  if (!cuts.length) return null;
  return { scenes, cuts, durations, duration: finite(plan.duration) ? plan.duration : scenes.at(-1)!.end };
}

function sceneOf(plan: CutPlan, cut: Cut): SceneSpan {
  return plan.scenes.find((scene) => scene.id === cut.scene)!;
}

/** Where cut ``index`` ends in the film: the next cut of its scene, or the scene's end. */
export function cutEnd(plan: CutPlan, index: number): number {
  const cut = plan.cuts[index];
  const next = plan.cuts[index + 1];
  return next && next.scene === cut.scene ? next.at : sceneOf(plan, cut).end;
}

export function cutLength(plan: CutPlan, index: number): number {
  return cutEnd(plan, index) - plan.cuts[index].at;
}

/** The cut on screen at ``time`` seconds into the film. */
export function cutAt(plan: CutPlan, time: number): number {
  let found = 0;
  plan.cuts.forEach((cut, index) => { if (cut.at <= time + 1e-6) found = index; });
  return found;
}

/** The cut starts with its scene: its left edge follows the narration and cannot move. */
export function firstOfScene(plan: CutPlan, index: number): boolean {
  return index === 0 || plan.cuts[index - 1].scene !== plan.cuts[index].scene;
}

function fits(plan: CutPlan, cut: Cut, length: number): boolean {
  return cut.kind !== null || length <= (plan.durations[cut.source] ?? 0) + 1e-3;
}

/** Footage kept inside its source for ``length`` seconds. */
function clampStart(plan: CutPlan, cut: Cut, length: number): Cut {
  if (cut.kind !== null) return cut;
  const room = (plan.durations[cut.source] ?? 0) - length;
  return { ...cut, start: round(Math.max(0, Math.min(cut.start, room))) };
}

function withCuts(plan: CutPlan, cuts: Cut[]): CutPlan {
  return { ...plan, cuts };
}

/** How far the left edge of cut ``index`` may move: a trim against the cut before it, in the same scene. */
export function edgeRange(plan: CutPlan, index: number): { min: number; max: number } | null {
  if (firstOfScene(plan, index)) return null;
  const before = plan.cuts[index - 1], cut = plan.cuts[index];
  const end = cutEnd(plan, index);
  let min = before.at + MIN_CUT, max = end - MIN_CUT;
  // Neither footage may run past its source's end.
  if (before.kind === null) max = Math.min(max, before.at + (plan.durations[before.source] ?? 0));
  if (cut.kind === null) min = Math.max(min, end - (plan.durations[cut.source] ?? 0));
  return min <= max + 1e-9 ? { min, max } : null;
}

/** Move the boundary before cut ``index`` to ``at`` seconds into the film (kept in range). */
export function trimEdge(plan: CutPlan, index: number, at: number): CutPlan {
  const range = edgeRange(plan, index);
  if (!range) return plan;
  const next = round(Math.min(range.max, Math.max(range.min, at)));
  if (Math.abs(next - plan.cuts[index].at) < 1e-6) return plan;
  const cuts = plan.cuts.slice();
  const end = cutEnd(plan, index);
  // The footage stays where it was in the film: a shorter cut drops its first frames, a longer one shows earlier footage.
  const moved = { ...cuts[index], at: next, start: cuts[index].kind === null ? cuts[index].start + (next - cuts[index].at) : cuts[index].start };
  cuts[index] = clampStart(plan, moved, end - next);
  cuts[index - 1] = clampStart(plan, cuts[index - 1], next - cuts[index - 1].at);
  return withCuts(plan, cuts);
}

/** Exchange the footage of two cuts; each keeps its place and length in the film. Null when one does not fit. */
export function swapCuts(plan: CutPlan, a: number, b: number): CutPlan | null {
  if (a === b || !plan.cuts[a] || !plan.cuts[b]) return null;
  const first = plan.cuts[a], second = plan.cuts[b];
  const footage = (cut: Cut) => ({ from: cut.from, source: cut.source, start: cut.start, kind: cut.kind, cover: cut.cover });
  const nextA = { ...first, ...footage(second) }, nextB = { ...second, ...footage(first) };
  const lengthA = cutLength(plan, a), lengthB = cutLength(plan, b);
  if (!fits(plan, nextA, lengthA) || !fits(plan, nextB, lengthB)) return null;
  const cuts = plan.cuts.slice();
  cuts[a] = clampStart(plan, nextA, lengthA);
  cuts[b] = clampStart(plan, nextB, lengthB);
  return withCuts(plan, cuts);
}

/** Remove a cut: the cut before it in the scene (or after it, for the first) takes its time. Null for a scene's only cut. */
export function removeCut(plan: CutPlan, index: number): CutPlan | null {
  const cut = plan.cuts[index];
  if (!cut) return null;
  const first = firstOfScene(plan, index);
  const next = plan.cuts[index + 1];
  const neighbour = first ? (next && next.scene === cut.scene ? index + 1 : -1) : index - 1;
  if (neighbour < 0) return null;
  const cuts = plan.cuts.slice();
  const end = cutEnd(plan, index);
  if (first) {
    // The next cut starts with the scene, earlier in its footage when there is room.
    const grown = { ...cuts[neighbour], at: cut.at, start: cuts[neighbour].kind === null ? cuts[neighbour].start - (cuts[neighbour].at - cut.at) : cuts[neighbour].start };
    const length = cutEnd(plan, neighbour) - cut.at;
    if (!fits(plan, grown, length)) return null;
    cuts[neighbour] = clampStart(plan, grown, length);
  } else {
    const length = end - cuts[neighbour].at;
    if (!fits(plan, cuts[neighbour], length)) return null;
    cuts[neighbour] = clampStart(plan, cuts[neighbour], length);
  }
  cuts.splice(index, 1);
  return withCuts(plan, cuts);
}

/** Show other footage in a cut: ``source`` from ``start`` seconds, kept inside it. Null when the source is too short. */
export function replaceFootage(plan: CutPlan, index: number, source: string, start: number): CutPlan | null {
  const cut = plan.cuts[index];
  if (!cut || !(source in plan.durations)) return null;
  const length = cutLength(plan, index);
  const next: Cut = { ...cut, source, start: Math.max(0, start), kind: null };
  if (!fits(plan, next, length)) return null;
  const cuts = plan.cuts.slice();
  cuts[index] = clampStart(plan, next, length);
  return withCuts(plan, cuts);
}

/** Give a cut its own cover (null: back to its source's). */
export function setCutCover(plan: CutPlan, index: number, cover: CutCover | null): CutPlan {
  const cuts = plan.cuts.slice();
  cuts[index] = { ...cuts[index], cover: cover ? structuredClone(cover) : null };
  return withCuts(plan, cuts);
}

function sceneKey(plan: CutPlan, scene: string): string {
  return JSON.stringify(plan.cuts.filter((cut) => cut.scene === scene));
}

/** The scenes whose cuts differ between the saved plan and the edited one. */
export function changedScenes(base: CutPlan, plan: CutPlan): string[] {
  return plan.scenes.map((scene) => scene.id).filter((id) => sceneKey(base, id) !== sceneKey(plan, id));
}

function cutKeys(plan: CutPlan): string[] {
  return plan.cuts.map((cut, index) => JSON.stringify([cut, cutEnd(plan, index)]));
}

/** Per cut of the edited plan: whether it differs from every cut the saved plan had (footage, place, length, cover). */
export function changedFlags(base: CutPlan, plan: CutPlan): boolean[] {
  const saved = new Set(cutKeys(base));
  return cutKeys(plan).map((key) => !saved.has(key));
}

/** Cuts edited or removed since the saved plan, for the count on the apply button. */
export function changedCuts(base: CutPlan, plan: CutPlan): number {
  const now = new Set(cutKeys(plan));
  const removed = Math.max(0, base.cuts.length - plan.cuts.length);
  const gone = cutKeys(base).filter((key) => !now.has(key)).length;
  return Math.max(changedFlags(base, plan).filter(Boolean).length + removed, gone);
}

const round4 = (value: number): number => Math.round(value * 10_000) / 10_000;

/** What docu_slices takes: the changed scenes, each with all of its cuts. */
export function slicesRequest(plan: CutPlan, scenes: string[]): Array<{ scene_id: string; slices: unknown[] }> {
  return scenes.map((id) => ({
    scene_id: id,
    slices: plan.cuts.filter((cut) => cut.scene === id).map((cut) => ({
      from: cut.from, source_id: cut.source, start: round(cut.start), at: round(cut.at),
      cover: cut.cover ? { band: cut.cover.band ? { top: round4(cut.cover.band.top), bottom: round4(cut.cover.band.bottom) } : null,
        logos: cut.cover.logos.map((logo) => logo.map(round4)) } : null,
    })),
  }));
}

/** "Bản đồ vệ tinh", "Đồ họa số liệu" or the footage's source name. */
export function cutKindName(kind: string | null): string {
  return kind === "map" ? "Bản đồ vệ tinh" : kind ? "Đồ họa số liệu" : "";
}

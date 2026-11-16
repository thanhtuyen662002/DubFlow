/**
 * Hoàn tác that survives closing the editor and restarting DubFlow (Issue #896):
 * the steps before and after the draft's edit, saved in the draft as `undo`,
 * newest first, within a size budget so the draft stays small and fast to save.
 */

import { parseProject, serializeProject, type Edit, type MediaInfo } from "./model.ts";

/** Steps kept before the current edit, and after it (redo). */
export const KEPT_PAST = 40;
export const KEPT_FUTURE = 10;
/** The saved steps together stay under this many bytes of JSON (the draft's limit is 1 MiB). */
export const KEPT_BYTES = 400_000;

type Packed = { past: unknown[]; future: unknown[] };

function step(edit: Edit): Record<string, unknown> {
  const { changes: _changes, selected: _selected, ...rest } = serializeProject(edit, 0, null);
  return rest;
}

/** The steps to save around `states[index]`, nearest first, cut to the budget; null when there are none. */
export function packHistory(states: readonly Edit[], index: number): Packed | null {
  let bytes = 0;
  const take = (edits: readonly Edit[], limit: number): unknown[] => {
    const kept: unknown[] = [];
    for (const edit of edits.slice(0, limit)) {
      const value = step(edit);
      const size = JSON.stringify(value).length;
      if (bytes + size > KEPT_BYTES) break;
      bytes += size;
      kept.push(value);
    }
    return kept;
  };
  const past = take([...states.slice(0, index)].reverse(), KEPT_PAST);
  const future = take(states.slice(index + 1), KEPT_FUTURE);
  return past.length || future.length ? { past, future } : null;
}

/** The saved steps back as edits (oldest first for `past`), stopping at the first one that no longer reads. */
export function unpackHistory(value: unknown, duration: number, info: Partial<MediaInfo>): { past: Edit[]; future: Edit[] } {
  const raw = (value && typeof value === "object" ? value : {}) as Partial<Packed>;
  const read = (list: unknown, limit: number): Edit[] => {
    const edits: Edit[] = [];
    for (const entry of Array.isArray(list) ? list.slice(0, limit) : []) {
      const edit = entry && typeof entry === "object" ? parseProject({ ...(entry as object), changes: 0, selected: null }, duration, info)?.edit : undefined;
      if (!edit) break;
      edits.push(edit);
    }
    return edits;
  };
  return { past: read(raw.past, KEPT_PAST).reverse(), future: read(raw.future, KEPT_FUTURE) };
}

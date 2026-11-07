/**
 * Resizable, foldable side panels: a thin handle on one edge of a panel is
 * dragged to change its width (or height), a double click or its small arrow
 * folds the panel into a narrow strip, and the strip opens it again. Each
 * panel remembers its size and fold state per viewer (localStorage).
 */

/** Which edge of the panel carries the handle: the edge that faces the main area. */
export type PanelEdge = "left" | "right" | "top" | "bottom";

export type PanelState = { size: number | null; collapsed: boolean };

/** The width (or height) of a folded panel's strip. */
export const COLLAPSED_PX = 32;
/** Dragging this far under the smallest size folds the panel. */
export const SNAP_FOLD_PX = 56;
const STORAGE_PREFIX = "dubflow.panel.";

export type PanelOptions = {
  /** Storage key, e.g. "queue.list". */
  key: string;
  panel: HTMLElement;
  edge: PanelEdge;
  /** Element that receives `cssVar` (defaults to the panel). */
  host?: HTMLElement;
  /** CSS custom property holding the size; defaults to `--panel-size`. */
  cssVar?: string;
  min: number;
  /** Largest size; a function is read on every drag and window resize. */
  max: number | (() => number);
  /** Name in Vietnamese for labels, e.g. "danh sách". */
  label: string;
  /** Size used before the panel is first resized (for the keyboard and the first drag). */
  fallback: () => number;
  /** Folding allowed (e.g. not while the panel is a drawer). Default: always. */
  foldable?: () => boolean;
  /** Resizing allowed at all (e.g. not when the page stacks its columns). Default: always. */
  enabled?: () => boolean;
  /** Push the handle this many px outside the panel (into a gap between columns). */
  outset?: number;
  /** Called after the size or fold state changes. */
  onChange?: (state: PanelState) => void;
};

export type PanelHandle = {
  state(): PanelState;
  setCollapsed(collapsed: boolean): void;
  /** Re-apply limits (window or layout changed). */
  refit(): void;
  /** Forget the dragged size and fold state: back to the page's own size. */
  reset(): void;
  /** Read the stored state again (another panel with the same key changed it). */
  reload(): void;
};

export function clampSize(size: number, min: number, max: number): number {
  const top = Math.max(min, max);
  return Math.round(Math.min(top, Math.max(min, size)));
}

/** The stored state, or the default when it is missing or broken. */
export function parsePanelState(raw: string | null): PanelState {
  if (!raw) return { size: null, collapsed: false };
  try {
    const value = JSON.parse(raw) as Partial<PanelState>;
    const size = typeof value.size === "number" && Number.isFinite(value.size) && value.size > 0 ? Math.round(value.size) : null;
    return { size, collapsed: value.collapsed === true };
  } catch {
    return { size: null, collapsed: false };
  }
}

/** Which way the size grows when the pointer moves by +delta on the handle's axis. */
export function growSign(edge: PanelEdge): 1 | -1 {
  return edge === "right" || edge === "bottom" ? 1 : -1;
}

/**
 * The size a drag gives: the start size plus the pointer travel, folding when
 * pulled well under the smallest size.
 */
export function dragResult(edge: PanelEdge, startSize: number, delta: number, min: number, max: number, foldable: boolean): PanelState {
  const wanted = startSize + growSign(edge) * delta;
  if (foldable && wanted < min - SNAP_FOLD_PX) return { size: null, collapsed: true };
  return { size: clampSize(wanted, min, max), collapsed: false };
}

/** Keyboard on the handle: arrows step the size, Home/End go to the limits, Enter folds. */
export function keyResult(key: string, shift: boolean, edge: PanelEdge, size: number, min: number, max: number): number | "toggle" | null {
  const horizontal = edge === "left" || edge === "right";
  const step = shift ? 64 : 16;
  const grow = growSign(edge);
  if (key === "Enter" || key === " ") return "toggle";
  if (key === "Home") return min;
  if (key === "End") return Math.max(min, max);
  const forward = horizontal ? key === "ArrowRight" : key === "ArrowDown";
  const back = horizontal ? key === "ArrowLeft" : key === "ArrowUp";
  if (!forward && !back) return null;
  return clampSize(size + (forward ? step : -step) * grow, min, max);
}

const ARROW = {
  left: '<path d="M10 4 6 8l4 4" />',
  right: '<path d="M6 4l4 4-4 4" />',
  up: '<path d="M4 10l4-4 4 4" />',
  down: '<path d="M4 6l4 4 4-4" />',
};
function arrowSvg(direction: keyof typeof ARROW): string {
  return `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ARROW[direction]}</svg>`;
}
/** The arrow that points the way the panel folds (towards its own side). */
function foldArrow(edge: PanelEdge): keyof typeof ARROW {
  return edge === "right" ? "left" : edge === "left" ? "right" : edge === "top" ? "down" : "up";
}
function openArrow(edge: PanelEdge): keyof typeof ARROW {
  return edge === "right" ? "right" : edge === "left" ? "left" : edge === "top" ? "up" : "down";
}

function readState(key: string): PanelState {
  try {
    return parsePanelState(window.localStorage.getItem(STORAGE_PREFIX + key));
  } catch {
    return { size: null, collapsed: false };
  }
}
function writeState(key: string, state: PanelState): void {
  try {
    window.localStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(state));
  } catch {
    // Sizes are a per-viewer convenience; the defaults come back next start.
  }
}

const mounted: PanelHandle[] = [];
let resizeHooked = false;

export function mountPanel(options: PanelOptions): PanelHandle {
  const { panel, edge, key, label } = options;
  const host = options.host ?? panel;
  const cssVar = options.cssVar ?? "--panel-size";
  const horizontal = edge === "left" || edge === "right";
  const foldable = () => options.foldable?.() ?? true;
  const enabled = () => options.enabled?.() ?? true;
  const maxNow = () => (typeof options.max === "function" ? options.max() : options.max);
  let state = readState(key);

  panel.classList.add("panel-resizable");
  panel.dataset.panelEdge = edge;

  const handle = document.createElement("div");
  handle.className = `panel-handle ${edge}`;
  handle.setAttribute("role", "separator");
  handle.setAttribute("tabindex", "0");
  handle.setAttribute("aria-orientation", horizontal ? "vertical" : "horizontal");
  handle.setAttribute("aria-label", `Đổi cỡ ${label}`);
  handle.title = `Kéo để đổi cỡ ${label} · Bấm đúp để thu gọn`;
  if (options.outset) handle.style.setProperty("--panel-outset", `${options.outset}px`);
  const fold = document.createElement("button");
  fold.type = "button";
  fold.className = "panel-fold";
  fold.innerHTML = arrowSvg(foldArrow(edge));
  fold.title = `Thu gọn ${label}`;
  fold.setAttribute("aria-label", `Thu gọn ${label}`);
  handle.append(fold);

  const reveal = document.createElement("button");
  reveal.type = "button";
  reveal.className = `panel-reveal ${horizontal ? "vertical" : "horizontal"}`;
  reveal.title = `Mở ${label}`;
  reveal.setAttribute("aria-label", `Mở ${label}`);
  reveal.innerHTML = `${arrowSvg(openArrow(edge))}<span>${label.charAt(0).toUpperCase()}${label.slice(1)}</span>`;
  panel.append(handle, reveal);

  const currentSize = () => {
    const box = panel.getBoundingClientRect();
    const measured = horizontal ? box.width : box.height;
    return state.size ?? (measured > COLLAPSED_PX ? measured : options.fallback());
  };

  const apply = () => {
    const on = enabled();
    handle.hidden = !on;
    const collapsed = on && state.collapsed && foldable();
    panel.classList.toggle("panel-collapsed", collapsed);
    fold.hidden = !foldable();
    if (!on) {
      host.style.removeProperty(cssVar);
      panel.removeAttribute("data-panel-sized");
      return;
    }
    if (collapsed) {
      host.style.setProperty(cssVar, `${COLLAPSED_PX}px`);
      panel.setAttribute("data-panel-sized", "");
    } else if (state.size != null) {
      host.style.setProperty(cssVar, `${clampSize(state.size, options.min, maxNow())}px`);
      panel.setAttribute("data-panel-sized", "");
    } else {
      host.style.removeProperty(cssVar);
      panel.removeAttribute("data-panel-sized");
    }
    const size = collapsed ? COLLAPSED_PX : clampSize(currentSize(), options.min, maxNow());
    handle.setAttribute("aria-valuemin", String(options.min));
    handle.setAttribute("aria-valuemax", String(Math.max(options.min, Math.round(maxNow()))));
    handle.setAttribute("aria-valuenow", String(Math.round(size)));
  };

  const commit = (next: PanelState) => {
    state = next;
    writeState(key, state);
    apply();
    options.onChange?.(state);
    // Players and timelines lay themselves out on resize.
    window.dispatchEvent(new Event("resize"));
  };

  const setCollapsed = (collapsed: boolean) => {
    if (collapsed && !foldable()) return;
    commit({ size: state.size, collapsed });
  };

  // The arrow sits in the middle of the handle: a drag that starts on it resizes, a plain press folds
  // (handled on pointerup below, since the handle captures the pointer). Keyboard presses arrive as clicks.
  fold.addEventListener("click", (event) => {
    event.stopPropagation();
    if (event.detail === 0) setCollapsed(true);
  });
  reveal.addEventListener("click", () => setCollapsed(false));
  handle.addEventListener("dblclick", (event) => {
    if (fold.contains(event.target as Node)) return;
    setCollapsed(!(state.collapsed && foldable()));
  });

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !enabled()) return;
    event.preventDefault();
    const onArrow = fold.contains(event.target as Node);
    const collapsedAtStart = state.collapsed && foldable();
    const startSize = collapsedAtStart ? COLLAPSED_PX : clampSize(currentSize(), options.min, maxNow());
    const startPos = horizontal ? event.clientX : event.clientY;
    const max = maxNow();
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add(horizontal ? "panel-dragging-x" : "panel-dragging-y");
    handle.classList.add("dragging");
    let last: PanelState | null = null;
    const move = (moveEvent: PointerEvent) => {
      const delta = (horizontal ? moveEvent.clientX : moveEvent.clientY) - startPos;
      if (!last && Math.abs(delta) < 3) return;
      last = dragResult(edge, startSize, delta, options.min, max, foldable());
      // Live preview without writing storage on every move.
      state = { size: last.collapsed ? state.size : last.size, collapsed: last.collapsed };
      apply();
      window.dispatchEvent(new Event("resize"));
    };
    const end = (endEvent: PointerEvent) => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      document.body.classList.remove("panel-dragging-x", "panel-dragging-y");
      handle.classList.remove("dragging");
      if (last) commit(state);
      else if (onArrow && endEvent.type === "pointerup") setCollapsed(true);
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  });

  handle.addEventListener("keydown", (event) => {
    if (event.target !== handle || !enabled()) return;
    const collapsed = state.collapsed && foldable();
    const result = keyResult(event.key, event.shiftKey, edge, collapsed ? options.min : currentSize(), options.min, maxNow());
    if (result == null) return;
    event.preventDefault();
    if (result === "toggle") setCollapsed(!collapsed);
    else commit({ size: result, collapsed: false });
  });

  apply();
  const api: PanelHandle = { state: () => ({ ...state }), setCollapsed, refit: apply, reset: () => commit({ size: null, collapsed: false }),
    reload: () => { state = readState(key); apply(); } };
  mounted.push(api);
  if (!resizeHooked && typeof window.addEventListener === "function") {
    resizeHooked = true;
    let queued = false;
    window.addEventListener("resize", (event) => {
      // Our own synthetic resize events come from apply(); skip them to avoid loops.
      if (!event.isTrusted || queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        for (const item of mounted) item.refit();
      });
    });
  }
  return api;
}

/** Every panel back to its default size, unfolded (Cài đặt › Giao diện). */
export function resetPanels(): void {
  for (const item of mounted) item.reset();
}

/** Re-apply every panel's limits, e.g. after a page becomes visible. */
export function refitPanels(): void {
  for (const item of mounted) item.refit();
}

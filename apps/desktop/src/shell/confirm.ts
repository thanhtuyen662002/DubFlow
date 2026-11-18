/** In-app replacement for window.confirm, styled like the rest of the app. */
export type ConfirmOptions = {
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** The confirm button is red: the action removes or discards something. */
  danger?: boolean;
  /**
   * The button focused when the dialog opens (default confirm). "cancel" is for an opt-in such as paid AI use: Enter
   * then confirms only while the confirm button itself has focus, never by default.
   */
  initialFocus?: "confirm" | "cancel";
  /** A press on the dimmed area outside cancels (default). false: it does nothing, so a stray click is no answer. */
  backdropCancels?: boolean;
};

/** "stay": the key is taken (nothing underneath sees it) but does nothing. */
export type ConfirmKeyAction = "confirm" | "cancel" | "next" | "previous" | "stay" | null;

export function confirmLabels(options: ConfirmOptions): { confirm: string; cancel: string } {
  return { confirm: options.confirmLabel?.trim() || "Đồng ý", cancel: options.cancelLabel?.trim() || "Hủy" };
}

/** Which button has focus when the dialog opens. */
export function initialFocus(options: ConfirmOptions): "confirm" | "cancel" {
  return options.initialFocus === "cancel" ? "cancel" : "confirm";
}

/**
 * What a key does inside the dialog: Esc cancels, Enter confirms (unless the
 * cancel button has focus, where Enter presses it), Tab cycles the two buttons.
 * `confirmOnlyFocused` (a dialog opened on cancel): Enter confirms only while the
 * confirm button has focus; with focus anywhere else (the text clicked, the page)
 * it does nothing.
 */
export function confirmKeyAction(key: string, shift: boolean, cancelFocused: boolean,
  confirmOnlyFocused = false, confirmFocused = false): ConfirmKeyAction {
  if (key === "Escape") return "cancel";
  if (key === "Enter") {
    if (cancelFocused) return "cancel";
    return !confirmOnlyFocused || confirmFocused ? "confirm" : "stay";
  }
  if (key === "Tab") return shift ? "previous" : "next";
  return null;
}

/** Focus index after Tab/Shift+Tab, wrapping so focus never leaves the dialog. */
export function cycleFocus(current: number, count: number, backwards: boolean): number {
  if (count <= 0) return -1;
  const start = current < 0 || current >= count ? (backwards ? 0 : -1) : current;
  return (start + (backwards ? count - 1 : 1)) % count;
}

/** Whether `backdrop` is the topmost dialog: the last `.confirm-backdrop` among `siblings` (document.body's children). */
export function isTopmostDialog(backdrop: unknown, siblings: ArrayLike<{ classList: { contains(name: string): boolean } }>): boolean {
  for (let index = siblings.length - 1; index >= 0; index -= 1) {
    if (siblings[index].classList.contains("confirm-backdrop")) return siblings[index] === backdrop;
  }
  return false;
}

export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  const labels = confirmLabels(options);
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const backdrop = document.createElement("div");
  backdrop.className = "confirm-backdrop";
  const panel = document.createElement("div");
  panel.className = "confirm-panel";
  panel.setAttribute("role", "alertdialog");
  panel.setAttribute("aria-modal", "true");
  const id = `confirm-${Math.random().toString(36).slice(2, 10)}`;
  panel.setAttribute("aria-labelledby", `${id}-title`);
  panel.setAttribute("aria-describedby", `${id}-message`);
  const title = document.createElement("h2");
  title.id = `${id}-title`;
  title.textContent = options.title;
  const message = document.createElement("p");
  message.id = `${id}-message`;
  message.textContent = options.message;
  const actions = document.createElement("div");
  actions.className = "confirm-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "btn";
  cancel.textContent = labels.cancel;
  const confirm = document.createElement("button");
  confirm.type = "button";
  confirm.className = options.danger ? "btn danger-solid" : "btn primary";
  confirm.textContent = labels.confirm;
  actions.append(cancel, confirm);
  panel.append(title, message, actions);
  backdrop.append(panel);

  return new Promise<boolean>((resolve) => {
    let settled = false;
    const buttons = [cancel, confirm];
    const guarded = initialFocus(options) === "cancel";
    // Captured on the document so keys never reach the screen underneath. Only the topmost dialog takes a key, and
    // no other listener sees it: one Enter never answers two stacked dialogs.
    const onKey = (event: KeyboardEvent): void => {
      if (!isTopmostDialog(backdrop, document.body.children)) return;
      const action = confirmKeyAction(event.key, event.shiftKey, document.activeElement === cancel, guarded, document.activeElement === confirm);
      if (!action) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      if (action === "stay") return;
      if (action === "confirm") finish(true);
      else if (action === "cancel") finish(false);
      else buttons[cycleFocus(buttons.indexOf(document.activeElement as HTMLButtonElement), buttons.length, action === "previous")].focus();
    };
    const finish = (value: boolean): void => {
      if (settled) return;
      settled = true;
      document.removeEventListener("keydown", onKey, true);
      backdrop.remove();
      if (previous?.isConnected) previous.focus();
      resolve(value);
    };
    document.addEventListener("keydown", onKey, true);
    cancel.addEventListener("click", () => finish(false));
    confirm.addEventListener("click", () => finish(true));
    backdrop.addEventListener("mousedown", (event) => {
      if (event.target === backdrop && options.backdropCancels !== false) finish(false);
    });
    document.body.append(backdrop);
    (initialFocus(options) === "cancel" ? cancel : confirm).focus();
  });
}

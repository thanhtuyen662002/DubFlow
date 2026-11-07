/** How long a toast stays before it slides back out. */
export const TOAST_MS = 3000;

/**
 * A short notice that slides in from the bottom-right corner, stays
 * `TOAST_MS`, then slides back out and is removed. Newer toasts stack above.
 */
export function showToast(text: string, tone: "ok" | "err" = "ok"): void {
  let stack = document.querySelector<HTMLElement>(".toast-stack");
  if (!stack) {
    stack = document.createElement("div");
    stack.className = "toast-stack";
    stack.setAttribute("role", "status");
    stack.setAttribute("aria-live", "polite");
    document.body.append(stack);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${tone}`;
  toast.textContent = text;
  stack.append(toast);
  // Next frame: the start position is laid out first, so the slide-in runs.
  requestAnimationFrame(() => requestAnimationFrame(() => toast.classList.add("shown")));
  const remove = () => toast.remove();
  setTimeout(() => {
    toast.classList.remove("shown");
    toast.addEventListener("transitionend", remove, { once: true });
    // No transition (reduced motion, hidden window): still go.
    setTimeout(remove, 400);
  }, TOAST_MS);
}

/**
 * A side list that slides over the page below 1200 px: Esc, a click outside it
 * or widening the window closes it.
 */
export function closeDrawerOutside(screen: HTMLElement, column: HTMLElement, toggle: HTMLElement, close: () => void): void {
  // Test doubles of the page have no global listeners; the toggle still works there.
  if (typeof document.addEventListener !== "function") return;
  const open = () => screen.classList.contains("list-open") && !screen.closest("[hidden]");
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !open() || document.querySelector(".confirm-backdrop")) return;
    close();
    toggle.focus();
  });
  document.addEventListener("mousedown", (event) => {
    if (!open()) return;
    const target = event.target as Node;
    if (!column.contains(target) && !toggle.contains(target)) close();
  });
  if (typeof window !== "undefined") window.addEventListener("resize", () => {
    if (window.innerWidth >= 1200 && screen.classList.contains("list-open")) close();
  });
}

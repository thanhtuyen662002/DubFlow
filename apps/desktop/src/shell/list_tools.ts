/**
 * One shape for every page's video list column: a count beside the title and a
 * search box under the head, like Hàng đợi. Rows are filtered by their text.
 */

/** Lower case without Vietnamese marks, so "long tieng" finds "Lồng tiếng". */
export function foldText(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/đ/g, "d").replace(/Đ/g, "d").toLowerCase();
}

export function mountListTools(column: HTMLElement | null, listSelector: string, unit: string, placeholder: string): void {
  if (!column) return;
  const list = column.querySelector<HTMLElement>(listSelector);
  const head = column.querySelector<HTMLElement>(":scope > .queue-col-head");
  if (!list || !head) return;
  const count = document.createElement("span");
  count.className = "hint col-count";
  head.querySelector("h2")?.after(count);
  const label = document.createElement("label");
  label.className = "media-search";
  const input = document.createElement("input");
  input.type = "search";
  input.placeholder = placeholder;
  input.autocomplete = "off";
  input.setAttribute("aria-label", placeholder);
  label.append(input);
  // Under the filter tabs when the page has them, else under the head.
  const after = column.querySelector<HTMLElement>(":scope > .queue-filter") ?? head;
  after.after(label);

  const apply = () => {
    const query = foldText(input.value.trim());
    const rows = [...list.children] as HTMLElement[];
    for (const row of rows) row.classList.toggle("search-miss", !!query && !foldText(row.textContent ?? "").includes(query));
    count.textContent = rows.length ? `${rows.length} ${unit}` : "";
  };
  input.addEventListener("input", apply);
  new MutationObserver(apply).observe(list, { childList: true });
  apply();
}

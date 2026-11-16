/**
 * Thuật ngữ và ngữ cảnh (Issue #903): the shared glossaries in Cài đặt › Dịch
 * phụ đề, and a video's own terms, glossaries and context in its Thiết lập.
 */

import {
  CONTEXT_LABELS, MAX_BOOKS, MAX_TERMS, booksFor, emptyJobGlossary, exportCsv, formatTerms, isEmptyContext, loadBooks, newBookId,
  normalizeContext, parseTerms, saveBooks, searchTerms, termIssues, termKey,
  type GlossBook, type GlossContext, type GlossTerm, type JobGlossary,
} from "./glossary.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;
const $ = <T extends HTMLElement = HTMLElement>(selector: string): T => document.querySelector(selector) as T;

function issueText(terms: readonly GlossTerm[]): string {
  const { duplicates, conflicts } = termIssues(terms);
  const parts: string[] = [];
  if (conflicts.length) {
    parts.push(`${conflicts.length} thuật ngữ có nhiều cách viết (${conflicts.slice(0, 3).map((item) => `${item.source}: ${item.targets.join(" / ")}`).join("; ")}); dòng bắt buộc được dùng trước, rồi tới dòng đứng trước.`);
  }
  if (duplicates.length) parts.push(`${duplicates.length} dòng trùng.`);
  return parts.join(" ");
}

export type GlossaryBooks = { books: () => GlossBook[]; onChange: (listener: () => void) => void };

/** Cài đặt › Dịch phụ đề › Bộ thuật ngữ dùng chung. */
export function mountGlossaryBooks(invoke: Invoke): GlossaryBooks {
  const select = $<HTMLSelectElement>("#gloss-book");
  const body = $("#gloss-body");
  const nameInput = $<HTMLInputElement>("#gloss-name");
  const auto = $<HTMLInputElement>("#gloss-auto");
  const search = $<HTMLInputElement>("#gloss-search");
  const rows = $("#gloss-rows");
  const issues = $("#gloss-issues");
  const state = $("#gloss-state");
  const file = $<HTMLInputElement>("#gloss-file");
  const deleteButton = $<HTMLButtonElement>("#gloss-delete");
  let books = loadBooks();
  let current = books[0]?.id ?? "";
  let deleteArmed = 0;
  const listeners: Array<() => void> = [];

  const book = (): GlossBook | undefined => books.find((item) => item.id === current);

  function store(): void {
    if (!saveBooks(books)) state.textContent = "Không lưu được bộ thuật ngữ trên máy này.";
    for (const listener of listeners) listener();
  }

  function update(change: (item: GlossBook) => GlossBook, redraw = false): void {
    books = books.map((item) => item.id === current ? change(item) : item);
    store();
    if (redraw) render();
    else issues.textContent = issueText(book()?.terms ?? []);
  }

  function renderRows(): void {
    const item = book();
    if (!item) return rows.replaceChildren();
    const { duplicates, conflicts } = termIssues(item.terms);
    const flagged = new Set([...duplicates, ...conflicts.flatMap((conflict) => conflict.rows)]);
    const shown = searchTerms(item.terms, search.value);
    rows.replaceChildren(...shown.slice(0, 300).map((index) => {
      const term = item.terms[index];
      const row = document.createElement("div");
      row.className = "gloss-row" + (flagged.has(index) ? " flagged" : "");
      row.setAttribute("role", "row");
      const field = (name: "source" | "target" | "note", label: string, limit: number): HTMLInputElement => {
        const input = document.createElement("input");
        input.value = term[name];
        input.maxLength = limit;
        input.setAttribute("aria-label", label);
        input.addEventListener("change", () => update((book) => ({
          ...book, terms: book.terms.map((each, at) => at === index ? { ...each, [name]: input.value.normalize("NFC").replace(/\s+/g, " ").trim() } : each)
            .filter((each) => each.source && each.target),
        }), true));
        return input;
      };
      const mandatory = document.createElement("input");
      mandatory.type = "checkbox";
      mandatory.checked = term.mandatory;
      mandatory.setAttribute("aria-label", "Bắt buộc");
      mandatory.addEventListener("change", () => update((book) => ({
        ...book, terms: book.terms.map((each, at) => at === index ? { ...each, mandatory: mandatory.checked } : each),
      })));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "link";
      remove.textContent = "Xóa";
      remove.setAttribute("aria-label", `Xóa ${term.source}`);
      remove.addEventListener("click", () => update((book) => ({ ...book, terms: book.terms.filter((_, at) => at !== index) }), true));
      row.append(field("source", "Nguồn", 80), field("target", "Tiếng Việt", 80), mandatory, field("note", "Ghi chú", 200), remove);
      return row;
    }));
    if (search.value.trim() && !shown.length) {
      const none = document.createElement("p");
      none.className = "hint";
      none.textContent = "Không có thuật ngữ nào khớp.";
      rows.append(none);
    }
  }

  function render(): void {
    select.replaceChildren(...books.map((item) => new Option(`${item.name} (${item.terms.length})${item.auto ? " · mọi video" : ""}`, item.id)));
    select.value = current;
    select.hidden = !books.length;
    $("#gloss-empty").hidden = books.length > 0;
    $<HTMLButtonElement>("#gloss-new").disabled = books.length >= MAX_BOOKS;
    const item = book();
    body.hidden = !item;
    if (!item) return;
    if (document.activeElement !== nameInput) nameInput.value = item.name;
    auto.checked = item.auto;
    $<HTMLButtonElement>("#gloss-add").disabled = item.terms.length >= MAX_TERMS;
    deleteButton.textContent = deleteArmed ? "Bấm lần nữa để xóa" : "Xóa bộ này";
    issues.textContent = issueText(item.terms);
    renderRows();
  }

  select.addEventListener("change", () => {
    current = select.value;
    search.value = "";
    render();
  });
  $("#gloss-new").addEventListener("click", () => {
    if (books.length >= MAX_BOOKS) return;
    const item: GlossBook = { id: newBookId(), name: `Bộ thuật ngữ ${books.length + 1}`, auto: false, terms: [] };
    books = [...books, item];
    current = item.id;
    store();
    render();
    nameInput.focus();
    nameInput.select();
  });
  nameInput.addEventListener("change", () => update((item) => ({ ...item, name: nameInput.value.normalize("NFC").replace(/\s+/g, " ").trim().slice(0, 60) || item.name }), true));
  auto.addEventListener("change", () => update((item) => ({ ...item, auto: auto.checked }), true));
  search.addEventListener("input", renderRows);
  $("#gloss-add").addEventListener("click", () => {
    const item = book();
    if (!item) return;
    // A new row is the first one shown, ready to type into; it is kept once both sides are filled.
    search.value = "";
    const row = document.createElement("div");
    row.className = "gloss-row gloss-new-row";
    const source = document.createElement("input"), target = document.createElement("input");
    source.placeholder = "Nguồn (中文 / English)";
    target.placeholder = "Tiếng Việt";
    source.setAttribute("aria-label", "Nguồn");
    target.setAttribute("aria-label", "Tiếng Việt");
    const keep = (): void => {
      const term = parseTerms(`${source.value.replace(/=/g, " ")} = ${target.value.replace(/=/g, " ")}`)[0];
      if (!term) return;
      update((each) => ({ ...each, terms: [term, ...each.terms].slice(0, MAX_TERMS) }), true);
    };
    source.addEventListener("change", keep);
    target.addEventListener("change", keep);
    row.append(source, target);
    renderRows();
    rows.prepend(row);
    source.focus();
  });
  $("#gloss-import").addEventListener("click", () => file.click());
  file.addEventListener("change", () => {
    const picked = file.files?.[0];
    file.value = "";
    if (!picked) return;
    if (picked.size > 2 * 1024 * 1024) {
      state.textContent = "File quá lớn (tối đa 2 MB).";
      return;
    }
    void picked.text().then((text) => {
      const terms = parseTerms(text);
      if (!terms.length) {
        state.textContent = `Không đọc được thuật ngữ nào trong ${picked.name}. Mỗi dòng cần “nguồn, tiếng Việt” (CSV/TSV) hoặc “nguồn = tiếng Việt”.`;
        return;
      }
      if (!book()) {
        const item: GlossBook = { id: newBookId(), name: picked.name.replace(/\.[^.]+$/, "").slice(0, 60) || "Bộ thuật ngữ", auto: false, terms: [] };
        books = [...books, item].slice(0, MAX_BOOKS);
        current = item.id;
      }
      let added = 0;
      update((item) => {
        const have = new Set(item.terms.map((term) => `${termKey(term.source)}\u0000${termKey(term.target)}`));
        const fresh = terms.filter((term) => !have.has(`${termKey(term.source)}\u0000${termKey(term.target)}`));
        added = Math.min(fresh.length, MAX_TERMS - item.terms.length);
        return { ...item, terms: [...item.terms, ...fresh].slice(0, MAX_TERMS) };
      }, true);
      state.textContent = `Đã nhập ${added} thuật ngữ từ ${picked.name}` + (terms.length > added ? ` (${terms.length - added} dòng đã có hoặc quá giới hạn ${MAX_TERMS}).` : ".");
    }).catch(() => { state.textContent = "Không đọc được file này."; });
  });
  $("#gloss-export").addEventListener("click", () => {
    const item = book();
    if (!item) return;
    void invoke<string | null>("glossary_export", { fileName: `${item.name}.csv`, text: exportCsv(item.terms) })
      .then((path) => { if (path) state.textContent = `Đã xuất ${item.terms.length} thuật ngữ ra ${path}.`; })
      .catch((error) => { state.textContent = `Không xuất được: ${String(error).slice(0, 160)}`; });
  });
  deleteButton.addEventListener("click", () => {
    if (!deleteArmed) {
      deleteArmed = window.setTimeout(() => { deleteArmed = 0; render(); }, 4000);
      return render();
    }
    window.clearTimeout(deleteArmed);
    deleteArmed = 0;
    const gone = book();
    books = books.filter((item) => item.id !== current);
    current = books[0]?.id ?? "";
    store();
    render();
    if (gone) state.textContent = `Đã xóa bộ “${gone.name}”. Video đã xử lý không đổi.`;
  });
  render();
  return { books: () => books, onChange: (listener) => { listeners.push(listener); } };
}

export type JobGlossaryHost = {
  /** The video shown in Thiết lập, and whether its options may change. */
  job: () => { id: string; glossary?: JobGlossary; editable: boolean } | null;
  set: (jobId: string, glossary: JobGlossary | null) => void;
  books: () => GlossBook[];
};

/** Thiết lập › Thuật ngữ & ngữ cảnh of one video. */
export function mountJobGlossary(host: JobGlossaryHost): { render: () => void } {
  const terms = $<HTMLTextAreaElement>("#job-gloss-terms");
  const issues = $("#job-gloss-issues");
  const bookList = $("#job-gloss-books");
  const fields: Record<keyof GlossContext, HTMLInputElement | HTMLTextAreaElement> = {
    topic: $("#job-ctx-topic"), audience: $("#job-ctx-audience"), tone: $("#job-ctx-tone"), people: $("#job-ctx-people"), notes: $("#job-ctx-notes"),
  };
  let shownJob: string | null = null;

  function current(): JobGlossary {
    return host.job()?.glossary ?? emptyJobGlossary();
  }

  function save(change: Partial<JobGlossary>): void {
    const job = host.job();
    if (!job?.editable) return;
    const next = { ...current(), ...change };
    const empty = !next.terms.length && next.books === null && isEmptyContext(next.context);
    host.set(job.id, empty ? null : next);
  }

  function summary(context: GlossContext): string {
    const filled = (Object.keys(CONTEXT_LABELS) as Array<keyof GlossContext>).filter((name) => context[name]).length;
    return filled ? `(${filled} mục)` : "(chưa có)";
  }

  function render(): void {
    const job = host.job();
    const glossary = job?.glossary ?? emptyJobGlossary();
    const fresh = shownJob !== (job?.id ?? null);
    shownJob = job?.id ?? null;
    if (fresh || document.activeElement !== terms) terms.value = formatTerms(glossary.terms);
    issues.textContent = issueText(glossary.terms);
    for (const [name, input] of Object.entries(fields) as Array<[keyof GlossContext, HTMLInputElement | HTMLTextAreaElement]>) {
      if (fresh || document.activeElement !== input) input.value = glossary.context[name];
    }
    $("#job-ctx-summary").textContent = summary(glossary.context);
    const books = host.books();
    const using = new Set(booksFor(glossary, books).map((book) => book.id));
    bookList.replaceChildren(...books.map((book) => {
      const label = document.createElement("label");
      label.className = "gloss-toggle";
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = using.has(book.id);
      box.addEventListener("change", () => {
        const chosen = new Set(booksFor(current(), host.books()).map((item) => item.id));
        if (box.checked) chosen.add(book.id);
        else chosen.delete(book.id);
        save({ books: host.books().filter((item) => chosen.has(item.id)).map((item) => item.id) });
      });
      label.append(box, ` ${book.name} (${book.terms.length} thuật ngữ)`);
      return label;
    }));
    if (!books.length) {
      const none = document.createElement("small");
      none.className = "hint";
      none.textContent = "Chưa có bộ thuật ngữ dùng chung.";
      bookList.append(none);
    }
  }

  terms.addEventListener("change", () => {
    const parsed = parseTerms(terms.value);
    save({ terms: parsed });
    terms.value = formatTerms(parsed);
    issues.textContent = issueText(parsed);
  });
  for (const input of Object.values(fields)) {
    input.addEventListener("change", () => {
      const context = normalizeContext(Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.value])));
      save({ context });
      $("#job-ctx-summary").textContent = summary(context);
    });
  }
  return { render };
}

/**
 * Thuật ngữ và ngữ cảnh (Issue #903): the user's spellings and background for
 * a video's translation. Reusable glossaries ("bộ thuật ngữ dùng chung") live
 * in local storage like the other translation preferences; a video's own terms,
 * its chosen glossaries and its context live on the queue job. The engine
 * (engine/dubflow/translation/glossary.py) resolves them with the same rules.
 *
 * Priority, highest first: the user's direct edit of a line > mandatory term of
 * the video > mandatory term of a shared glossary > the video's term > a shared
 * term > the video's context > the automatic summary > the default prompt.
 */

export type GlossTerm = { source: string; target: string; mandatory: boolean; note: string };
export type GlossContext = { topic: string; audience: string; tone: string; people: string; notes: string };
export type GlossBook = { id: string; name: string; auto: boolean; terms: GlossTerm[] };
/** A video's own glossary; `books`: the shared glossaries it uses (null: those marked for every new video). */
export type JobGlossary = { terms: GlossTerm[]; books: string[] | null; context: GlossContext };

export const MAX_TERMS = 500;
export const MAX_TERM_CHARS = 80;
export const MAX_NOTE_CHARS = 200;
export const MAX_BOOKS = 10;
export const CONTEXT_LIMITS: Readonly<Record<keyof GlossContext, number>> = { topic: 300, audience: 120, tone: 120, people: 800, notes: 800 };
export const CONTEXT_LABELS: Readonly<Record<keyof GlossContext, string>> = {
  topic: "Chủ đề", audience: "Người xem", tone: "Giọng điệu", people: "Nhân vật và cách xưng hô", notes: "Ghi chú cho người dịch",
};
const STORAGE = "dubflow.glossaries.v1";

export function emptyContext(): GlossContext {
  return { topic: "", audience: "", tone: "", people: "", notes: "" };
}

/** NFC, no control characters, spaces collapsed, cut to `limit`. */
export function cleanText(value: unknown, limit: number): string {
  if (typeof value !== "string") return "";
  return value.normalize("NFC").replace(/[\u0000-\u001f\u007f-\u009f\u200b-\u200f\u2028-\u202e\u2060-\u2064\ufeff]/g, " ")
    .replace(/\s+/g, " ").trim().slice(0, limit);
}

/** How two sources are compared: NFC, lower case, spaces collapsed (the engine's `_key`). */
export function termKey(text: string): string {
  return text.normalize("NFC").toLocaleLowerCase("vi").replace(/\s+/g, " ").trim();
}

/** For search: without accents, "đ" as "d". */
function folded(text: string): string {
  return text.normalize("NFD").replace(/\p{M}+/gu, "").replace(/đ/gi, "d").toLowerCase();
}

export function normalizeTerm(value: unknown): GlossTerm | null {
  if (!value || typeof value !== "object") return null;
  const row = value as Record<string, unknown>;
  const source = cleanText(row.source, MAX_TERM_CHARS), target = cleanText(row.target, MAX_TERM_CHARS);
  if (!source || !target) return null;
  return { source, target, mandatory: row.mandatory === true, note: cleanText(row.note, MAX_NOTE_CHARS) };
}

export function normalizeTerms(value: unknown): GlossTerm[] {
  return Array.isArray(value) ? value.map(normalizeTerm).filter((term): term is GlossTerm => !!term).slice(0, MAX_TERMS) : [];
}

export function normalizeContext(value: unknown): GlossContext {
  const row = (value && typeof value === "object" ? value : {}) as Record<string, unknown>;
  const context = emptyContext();
  for (const name of Object.keys(CONTEXT_LIMITS) as Array<keyof GlossContext>) context[name] = cleanText(row[name], CONTEXT_LIMITS[name]);
  return context;
}

export function isEmptyContext(context: GlossContext): boolean {
  return Object.values(context).every((value) => !value);
}

export type TermIssues = {
  /** Rows (indexes) that repeat an earlier row with the same spelling. */
  duplicates: number[];
  /** Sources written two or more ways; the first row wins, as in the engine. */
  conflicts: Array<{ source: string; targets: string[]; rows: number[] }>;
};

export function termIssues(terms: readonly GlossTerm[]): TermIssues {
  const seen = new Map<string, { first: number; targets: Map<string, string>; rows: number[] }>();
  const duplicates: number[] = [];
  terms.forEach((term, index) => {
    const key = termKey(term.source);
    if (!key) return;
    const entry = seen.get(key);
    if (!entry) {
      seen.set(key, { first: index, targets: new Map([[termKey(term.target), term.target]]), rows: [index] });
      return;
    }
    entry.rows.push(index);
    if (entry.targets.has(termKey(term.target))) duplicates.push(index);
    else entry.targets.set(termKey(term.target), term.target);
  });
  const conflicts = [...seen.values()].filter((entry) => entry.targets.size > 1)
    .map((entry) => ({ source: terms[entry.first].source, targets: [...entry.targets.values()], rows: entry.rows }));
  return { duplicates, conflicts };
}

/** Rows matching the query in source, Vietnamese or note; accents optional ("ly minh" finds "Lý Minh"). */
export function searchTerms(terms: readonly GlossTerm[], query: string): number[] {
  const wanted = folded(query.normalize("NFC").trim());
  return terms.flatMap((term, index) => !wanted || [term.source, term.target, term.note].some((text) => folded(text).includes(wanted)) ? [index] : []);
}

const YES = new Set(["1", "x", "yes", "y", "true", "có", "co", "bắt buộc", "bat buoc", "bb", "!"]);

/** One CSV/TSV line's cells (quotes as in RFC 4180). */
function cells(line: string, separator: string): string[] {
  const result: string[] = [];
  let cell = "", quoted = false;
  for (let index = 0; index < line.length; index += 1) {
    const ch = line[index];
    if (quoted) {
      if (ch === "\"" && line[index + 1] === "\"") { cell += "\""; index += 1; }
      else if (ch === "\"") quoted = false;
      else cell += ch;
    } else if (ch === "\"" && !cell.trim()) quoted = true;
    else if (ch === separator) { result.push(cell); cell = ""; }
    else cell += ch;
  }
  result.push(cell);
  return result;
}

/**
 * Terms from a pasted list or an imported file: JSON (a list, or `{terms}`), a
 * CSV/TSV with columns source, Vietnamese, mandatory, note (a header row is
 * skipped), or lines "nguồn = tiếng Việt" with a leading "!" for mandatory and
 * "# note" after.
 */
export function parseTerms(text: string): GlossTerm[] {
  const body = text.replace(/^﻿/, "").trim();
  if (!body) return [];
  if (body.startsWith("[") || body.startsWith("{")) {
    try {
      const value = JSON.parse(body) as unknown;
      return normalizeTerms(Array.isArray(value) ? value : (value as { terms?: unknown })?.terms);
    } catch {
      // Not JSON after all: read it as lines.
    }
  }
  const lines = body.split(/\r?\n/).filter((line) => line.trim());
  const sample = lines.slice(0, 5).join("\n");
  const separator = sample.includes("\t") ? "\t" : /[=→]|->/.test(sample) ? "" : sample.includes(";") && !sample.includes(",") ? ";" : ",";
  const terms: GlossTerm[] = [];
  for (const [index, line] of lines.entries()) {
    let row: GlossTerm | null;
    if (separator) {
      const [source = "", target = "", mandatory = "", note = ""] = cells(line, separator);
      if (index === 0 && /^(source|nguồn|nguon|gốc|goc|từ gốc|term)$/i.test(source.trim())) continue;
      row = normalizeTerm({ source, target, mandatory: YES.has(mandatory.trim().toLowerCase()), note });
    } else {
      const [main, ...comment] = line.split(/\s#\s?/);
      const parts = main.split(/\s*(?:=|→|->)\s*/);
      const bang = parts[0].trim().startsWith("!");
      row = normalizeTerm({ source: parts[0].trim().replace(/^!\s*/, ""), target: parts.slice(1).join(" "), mandatory: bang, note: comment.join(" # ") });
    }
    if (row) terms.push(row);
    if (terms.length >= MAX_TERMS) break;
  }
  return terms;
}

/** The editable text form ("!" marks mandatory), the inverse of `parseTerms` for such lines. */
export function formatTerms(terms: readonly GlossTerm[]): string {
  return terms.map((term) => `${term.mandatory ? "!" : ""}${term.source} = ${term.target}${term.note ? ` # ${term.note}` : ""}`).join("\n");
}

const csvCell = (text: string): string => /[",\n\r]/.test(text) ? `"${text.replace(/"/g, "\"\"")}"` : text;

/** A CSV Excel opens with the right letters (UTF-8 mark), columns source, target, mandatory, note. */
export function exportCsv(terms: readonly GlossTerm[]): string {
  return "﻿" + ["source,target,mandatory,note", ...terms.map((term) =>
    [term.source, term.target, term.mandatory ? "1" : "", term.note].map(csvCell).join(","))].join("\r\n") + "\r\n";
}

export function exportJson(book: GlossBook): string {
  return JSON.stringify({ name: book.name, terms: book.terms }, null, 2);
}

let made = 0;
export function newBookId(): string {
  made += 1;
  return `g${Date.now().toString(36)}${made.toString(36)}`;
}

export function normalizeBooks(value: unknown): GlossBook[] {
  if (!Array.isArray(value)) return [];
  const ids = new Set<string>();
  return value.flatMap((row): GlossBook[] => {
    if (!row || typeof row !== "object") return [];
    const raw = row as Record<string, unknown>;
    const id = typeof raw.id === "string" && /^[a-z0-9]{1,40}$/i.test(raw.id) && !ids.has(raw.id) ? raw.id : newBookId();
    ids.add(id);
    return [{ id, name: cleanText(raw.name, 60) || "Bộ thuật ngữ", auto: raw.auto === true, terms: normalizeTerms(raw.terms) }];
  }).slice(0, MAX_BOOKS);
}

export function loadBooks(): GlossBook[] {
  try {
    const text = window.localStorage.getItem(STORAGE);
    return text ? normalizeBooks(JSON.parse(text)) : [];
  } catch {
    return [];
  }
}

export function saveBooks(books: readonly GlossBook[]): boolean {
  try {
    window.localStorage.setItem(STORAGE, JSON.stringify(books));
    return true;
  } catch {
    return false;
  }
}

export function emptyJobGlossary(): JobGlossary {
  return { terms: [], books: null, context: emptyContext() };
}

export function normalizeJobGlossary(value: unknown): JobGlossary | null {
  if (!value || typeof value !== "object") return null;
  const raw = value as Record<string, unknown>;
  const books = Array.isArray(raw.books) ? raw.books.filter((id): id is string => typeof id === "string" && /^[a-z0-9]{1,40}$/i.test(id)).slice(0, MAX_BOOKS) : null;
  return { terms: normalizeTerms(raw.terms), books, context: normalizeContext(raw.context) };
}

export function isJobGlossary(value: unknown): value is JobGlossary {
  const normal = normalizeJobGlossary(value);
  return !!normal && JSON.stringify(normal) === JSON.stringify(value);
}

/** The shared glossaries a video uses: its own choice, else those marked for every new video. */
export function booksFor(job: JobGlossary | undefined, books: readonly GlossBook[]): GlossBook[] {
  const chosen = job?.books;
  return chosen ? books.filter((book) => chosen.includes(book.id)) : books.filter((book) => book.auto);
}

/** The `glossary` argument of `start_job`, or null when there is nothing to send. */
export function glossaryArgs(job: JobGlossary | undefined, books: readonly GlossBook[]): Record<string, unknown> | null {
  const shared = booksFor(job, books).filter((book) => book.terms.length);
  const terms = job?.terms ?? [];
  const context = job?.context ?? emptyContext();
  if (!terms.length && !shared.length && isEmptyContext(context)) return null;
  return {
    project: terms,
    shared: shared.map((book) => ({ name: book.name, terms: book.terms })),
    ...(isEmptyContext(context) ? {} : { context }),
  };
}

/** One term as the translation uses it: `shared` tells a shared glossary's term from the video's own. */
export type ResolvedTerm = GlossTerm & { shared: boolean };
/** The video's terms (one per source, highest priority first) and its context, as the engine resolves them. */
export type Guide = { terms: ResolvedTerm[]; context: GlossContext };

/**
 * The engine's `resolve` (engine/dubflow/translation/glossary.py) for a video: one term per source,
 * mandatory of the video > mandatory shared > the video's > shared; within a level the first listed wins.
 */
export function resolveGuide(job: JobGlossary | undefined, books: readonly GlossBook[]): Guide {
  const candidates: ResolvedTerm[] = [
    ...(job?.terms ?? []).map((term) => ({ ...term, shared: false })),
    ...booksFor(job, books).flatMap((book) => book.terms.map((term) => ({ ...term, shared: true }))),
  ];
  const level = (term: ResolvedTerm): number => (term.mandatory ? 0 : 2) + (term.shared ? 1 : 0);
  const ordered = candidates.map((term, index) => ({ term, index })).sort((a, b) => level(a.term) - level(b.term) || a.index - b.index);
  const chosen = new Map<string, ResolvedTerm>();
  for (const { term } of ordered) {
    const key = termKey(term.source);
    if (key && !chosen.has(key)) chosen.set(key, term);
  }
  return { terms: [...chosen.values()].slice(0, MAX_TERMS), context: job?.context ?? emptyContext() };
}

function isCjk(ch: string): boolean {
  return /[㐀-鿿豈-﫿぀-ヿ가-힯]/u.test(ch);
}

const LATIN = "0-9A-Za-z_\\u00C0-\\u024F\\u1E00-\\u1EFF";

/** The engine's `_pattern`: a whole word, case-insensitive; CJK text has no spaces, so it matches anywhere. */
export function termPattern(term: string, flags = "iu"): RegExp {
  const text = term.normalize("NFC").trim();
  const body = text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
  const head = isCjk(text[0] ?? "") ? "" : `(?<![${LATIN}])`;
  const tail = isCjk(text[text.length - 1] ?? "") ? "" : `(?![${LATIN}])`;
  return new RegExp(head + body + tail, flags);
}

export function containsTerm(text: string, term: string): boolean {
  return Boolean(term.trim()) && termPattern(term).test(text.normalize("NFC"));
}

/** The terms whose source occurs in `sources` (all of them when `sources` is null). */
export function termsUsed(guide: Guide, sources: Iterable<string> | null): ResolvedTerm[] {
  const text = sources === null ? null : [...sources].join("\n");
  return guide.terms.filter((term) => text === null || containsTerm(text, term.source));
}

/**
 * What an AI rewrite is told before the lines (the engine's `brief_lines`): the user's context, then the
 * mandatory and the preferred terms that occur in `sources`. Context stays context: it is never listed as a term.
 */
export function guideLines(guide: Guide, sources: Iterable<string> | null): string {
  const used = termsUsed(guide, sources);
  const lines: string[] = [];
  if (!isEmptyContext(guide.context)) {
    lines.push("Bối cảnh do người dùng ghi:");
    for (const name of Object.keys(CONTEXT_LABELS) as Array<keyof GlossContext>) {
      if (guide.context[name]) lines.push(`- ${CONTEXT_LABELS[name]}: ${guide.context[name]}`);
    }
  }
  const must = used.filter((term) => term.mandatory).map((term) => `${term.source} → ${term.target}`);
  const prefer = used.filter((term) => !term.mandatory).map((term) => `${term.source} → ${term.target}`);
  if (must.length) lines.push(`Thuật ngữ BẮT BUỘC, luôn viết đúng như sau: ${must.slice(0, 120).join("; ")}`);
  if (prefer.length) lines.push(`Thuật ngữ nên dùng (trừ khi sai ngữ cảnh): ${prefer.slice(0, 120).join("; ")}`);
  return lines.join("\n").slice(0, 6000);
}

/** "Anh" for "anh" at the start of a sentence; a target with its own capitals is kept as written. */
function keepCase(found: string, replacement: string): string {
  if (replacement !== replacement.toLowerCase()) return replacement;
  return found[0] && found[0] !== found[0].toLowerCase() ? replacement[0].toUpperCase() + replacement.slice(1) : replacement;
}

export type TermCheck = { text: string; replaced: number; missing: string[] };

/**
 * One rewritten line checked against the mandatory terms of its source: a term left in its source spelling is
 * replaced by the user's Vietnamese (as the engine's `enforce` does); one that is simply absent is reported, never guessed.
 */
export function checkTerms(source: string, text: string, guide: Guide): TermCheck {
  let line = text;
  let replaced = 0;
  const missing: string[] = [];
  for (const term of guide.terms) {
    if (!term.mandatory || !containsTerm(source, term.source) || containsTerm(line, term.target)) continue;
    if (termKey(term.source) !== termKey(term.target) && containsTerm(line, term.source)) {
      line = line.normalize("NFC").replace(termPattern(term.source, "giu"), (found) => keepCase(found, term.target));
      replaced += 1;
    } else {
      missing.push(term.source);
    }
  }
  return { text: line, replaced, missing };
}

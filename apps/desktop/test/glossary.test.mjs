// Thuật ngữ và ngữ cảnh (Issue #903): the desktop's glossary model.
import assert from "node:assert/strict";
import {
  booksFor, cleanText, emptyJobGlossary, exportCsv, exportJson, formatTerms, glossaryArgs, isJobGlossary, normalizeBooks,
  normalizeJobGlossary, parseTerms, searchTerms, termIssues, termKey,
} from "../src/features/ai/glossary.ts";

const t = (source, target, mandatory = false, note = "") => ({ source, target, mandatory, note });

// Text is NFC, without control or zero-width characters, spaces collapsed.
assert.equal(cleanText("  Lý   Minh​ \n", 80), "Lý Minh");
assert.equal(cleanText(3, 80), "");
assert.equal(termKey("  TikTok   Shop "), "tiktok shop");

// Lines "nguồn = tiếng Việt", "!" mandatory, "# note"; Chinese, Vietnamese and English kept.
assert.deepEqual(parseTerms("!李明 = Lý Minh # nhân vật chính\n王爷 → Vương gia\nGPU -> card đồ họa\n\n= thiếu nguồn\nthiếu đích ="), [
  t("李明", "Lý Minh", true, "nhân vật chính"), t("王爷", "Vương gia"), t("GPU", "card đồ họa"),
]);
// CSV with a header, quotes and a mandatory column; TSV; semicolons.
assert.deepEqual(parseTerms("﻿source,target,mandatory,note\n李明,Lý Minh,1,\"chính, quan trọng\"\n\"A \"\"B\"\"\",C,,\n"), [
  t("李明", "Lý Minh", true, "chính, quan trọng"), t("A \"B\"", "C"),
]);
assert.deepEqual(parseTerms("长安\tTrường An\tcó\n洛阳\tLạc Dương"), [t("长安", "Trường An", true), t("洛阳", "Lạc Dương")]);
assert.deepEqual(parseTerms("a;b;x\nc;d"), [t("a", "b", true), t("c", "d")]);
// JSON: a list or an exported glossary.
assert.deepEqual(parseTerms(JSON.stringify([t("a", "b", true)])), [t("a", "b", true)]);
assert.deepEqual(parseTerms(exportJson({ id: "g1", name: "Phim", auto: false, terms: [t("x", "y", false, "n")] })), [t("x", "y", false, "n")]);
assert.deepEqual(parseTerms("   "), []);
assert.equal(parseTerms(Array.from({ length: 900 }, (_, i) => `a${i} = b${i}`).join("\n")).length, 500, "at most 500 terms");

// The text form and CSV export read back the same.
const sample = [t("李明", "Lý Minh", true, "nhân vật"), t("Phở, \"bò\"", "phở bò"), t("王爷", "Vương gia")];
assert.deepEqual(parseTerms(formatTerms(sample.filter((term) => !term.source.includes(",")))), sample.filter((term) => !term.source.includes(",")));
const csv = exportCsv(sample);
assert.ok(csv.startsWith("﻿source,target,mandatory,note\r\n"), "Excel reads UTF-8 with the mark");
assert.deepEqual(parseTerms(csv), sample);

// Duplicates and conflicts, case- and space-insensitively; the first row wins.
const issues = termIssues([t("李明", "Lý Minh"), t("李明 ", "lý minh"), t("李明", "Lí Minh"), t("长安", "Trường An"), t("TikTok", "TikTok"), t("tiktok", "Tóp tóp")]);
assert.deepEqual(issues.duplicates, [1]);
assert.deepEqual(issues.conflicts, [
  { source: "李明", targets: ["Lý Minh", "Lí Minh"], rows: [0, 1, 2] },
  { source: "TikTok", targets: ["TikTok", "Tóp tóp"], rows: [4, 5] },
]);

// Search: source, Vietnamese or note, with or without accents.
const rows = [t("李明", "Lý Minh", true, "nhân vật chính"), t("长安", "Trường An"), t("GPU", "card đồ họa")];
assert.deepEqual(searchTerms(rows, "ly minh"), [0]);
assert.deepEqual(searchTerms(rows, "Trường"), [1]);
assert.deepEqual(searchTerms(rows, "do hoa"), [2]);
assert.deepEqual(searchTerms(rows, "长"), [1]);
assert.deepEqual(searchTerms(rows, "chinh"), [0]);
assert.deepEqual(searchTerms(rows, ""), [0, 1, 2]);

// Stored glossaries are checked: ids kept unique, names and terms cleaned, at most ten.
const books = normalizeBooks([
  { id: "a1", name: " Phim  cổ trang ", auto: true, terms: [t("王爷", "Vương gia"), { source: "x" }] },
  { id: "a1", name: "", terms: "x" },
  null,
  ...Array.from({ length: 12 }, (_, i) => ({ id: `b${i}`, name: `B${i}`, terms: [] })),
]);
assert.equal(books.length, 10);
assert.deepEqual(books[0], { id: "a1", name: "Phim cổ trang", auto: true, terms: [t("王爷", "Vương gia")] });
assert.notEqual(books[1].id, "a1");
assert.equal(books[1].name, "Bộ thuật ngữ");

// A video's glossary: its own terms, its chosen glossaries (or those marked for every video) and its context.
const shared = [
  { id: "phim", name: "Phim", auto: true, terms: [t("王爷", "Vương gia", true)] },
  { id: "tech", name: "Công nghệ", auto: false, terms: [t("GPU", "card đồ họa")] },
  { id: "empty", name: "Trống", auto: true, terms: [] },
];
assert.equal(glossaryArgs(undefined, []), null);
assert.equal(glossaryArgs(emptyJobGlossary(), []), null, "nothing to send");
assert.deepEqual(booksFor(undefined, shared).map((book) => book.id), ["phim", "empty"]);
assert.deepEqual(glossaryArgs(undefined, shared), { project: [], shared: [{ name: "Phim", terms: [t("王爷", "Vương gia", true)] }] });
const job = { terms: [t("李明", "Lý Minh", true)], books: ["tech"], context: { ...emptyJobGlossary().context, topic: "Review card đồ họa" } };
assert.deepEqual(glossaryArgs(job, shared), {
  project: [t("李明", "Lý Minh", true)],
  shared: [{ name: "Công nghệ", terms: [t("GPU", "card đồ họa")] }],
  context: { topic: "Review card đồ họa", audience: "", tone: "", people: "", notes: "" },
});
assert.deepEqual(glossaryArgs({ ...job, books: [] }, shared).shared, [], "no shared glossary when the video turns them all off");

// The job's stored form is validated as the queue loads it.
assert.equal(isJobGlossary(job), true);
assert.equal(isJobGlossary({ ...job, terms: [{ source: "", target: "x", mandatory: false, note: "" }] }), false);
assert.equal(isJobGlossary("x"), false);
assert.deepEqual(normalizeJobGlossary({ terms: [t("a", "b")], books: ["ok", "bad id!"], context: { tone: " vui " } }),
  { terms: [t("a", "b")], books: ["ok"], context: { topic: "", audience: "", tone: "vui", people: "", notes: "" } });

console.log("glossary tests passed");

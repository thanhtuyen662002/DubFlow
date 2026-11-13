// The "Duyệt cảnh" panel itself, on a small DOM: "Bỏ cả cảnh gốc" is rendered on an unused alternative and in
// "Mọi cảnh đã nhập", shows the pieces it leaves out, and sends the parent's exclusion only once confirmed (#805).
import assert from "node:assert/strict";

class Node {
  constructor(tag) {
    this.tag = tag; this.children = []; this.listeners = {}; this.attrs = {}; this.text = ""; this.className = "";
    this.hidden = false; this.disabled = false; this.parent = null; this.id = ""; this.style = {};
  }
  get textContent() { return this.text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this.text = String(value); this.children = []; }
  append(...nodes) {
    for (const value of nodes) {
      const node = typeof value === "string" ? text(value) : value;
      node.remove(); node.parent = this; this.children.push(node);
    }
  }
  prepend(...nodes) { const rest = this.children; this.children = []; this.append(...nodes); for (const node of rest) this.children.push(node); }
  replaceChildren(...nodes) { for (const node of this.children) node.parent = null; this.children = []; this.append(...nodes); }
  remove() { if (this.parent) { this.parent.children = this.parent.children.filter((node) => node !== this); this.parent = null; } }
  replaceWith(node) {
    const parent = this.parent; if (!parent) return;
    node.remove(); parent.children[parent.children.indexOf(this)] = node; node.parent = parent; this.parent = null;
  }
  get isConnected() { let node = this; while (node.parent) node = node.parent; return node === document.body; }
  get childElementCount() { return this.children.filter((node) => node.tag !== "#text").length; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name] ?? null; }
  removeAttribute(name) { delete this.attrs[name]; }
  addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
  removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] ?? []).filter((item) => item !== fn); }
  click() { for (const fn of this.listeners.click ?? []) fn({ target: this, preventDefault() {}, stopPropagation() {} }); }
  get classList() {
    const node = this;
    const names = () => node.className.split(/\s+/).filter(Boolean);
    return {
      add: (name) => { if (!names().includes(name)) node.className = [...names(), name].join(" "); },
      remove: (name) => { node.className = names().filter((item) => item !== name).join(" "); },
      contains: (name) => names().includes(name),
    };
  }
  *walk() { for (const child of this.children) { yield child; yield* child.walk(); } }
  querySelectorAll(selector) {
    const wanted = selector.split(".").filter(Boolean);
    return [...this.walk()].filter((node) => wanted.every((name) => node.classList.contains(name)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  focus() { document.activeElement = this; }
  scrollIntoView() {}
  pause() {}
  play() { return Promise.resolve(); }
}
function text(value) { const node = new Node("#text"); node.text = value; return node; }
globalThis.HTMLElement = Node;
const body = new Node("body");
const docListeners = {};
globalThis.document = {
  body, activeElement: null,
  createElement: (tag) => new Node(tag),
  createTextNode: text,
  getElementById: (id) => [...body.walk()].find((node) => node.id === id) ?? null,
  querySelector: (selector) => body.querySelector(selector),
  addEventListener: (type, fn) => { (docListeners[type] ??= []).push(fn); },
  removeEventListener: (type, fn) => { docListeners[type] = (docListeners[type] ?? []).filter((item) => item !== fn); },
};

const { openReviewPanel } = await import("../src/features/mastering/review_panel.ts");

const shot = (id, start, end, more = {}) => ({ id, source_id: "v1", start_ms: start, end_ms: end, keyframe: null, ...more });
const family = (id, start, end, pieces) => ({ id, source_id: "v1", start_ms: start, end_ms: end, pieces });
function reviewView({ excluded = [] } = {}) {
  const left = (item) => excluded.includes(item.parent ?? item.id) ? { ...item, excluded: true, excluded_by: "s1" } : item;
  return {
    kind: "editorial_review_view", plan_id: "product-1", mode: "content", revision: 1, can_undo: excluded.length > 0,
    units: [{
      unit_id: "s1", text: "Câu một", start_ms: 0, end_ms: 4000, coverage: { status: "generic" }, notes: [], evidence: "NOT_NEEDED",
      state: "READY", user_state: null, decision: null, unreviewed: false,
      chosen: [shot("v1c3f1", 4000, 5000, { parent: "v1c3" })],
      pieces: [{ source_id: "v1", source_start: 4000, source_end: 5000, timeline_start: 0, timeline_end: 1000 }],
      alternatives: [left(shot("v1c3f2", 5000, 6000, { parent: "v1c3", score: 400 })), shot("v1c5", 9000, 11000, { score: 300 })],
    }],
    // every shot of the footage: a whole one, and the two pieces of v1c7 that no sentence shows
    shots: [shot("v1c3f1", 4000, 5000, { parent: "v1c3" }), left(shot("v1c3f2", 5000, 6000, { parent: "v1c3" })),
      shot("v1c5", 9000, 11000), shot("v1c7f1", 20000, 21000, { parent: "v1c7" }), shot("v1c7f2", 21000, 22500, { parent: "v1c7" })],
    families: {
      v1c3: family("v1c3", 4000, 7000, [{ id: "v1c3f1", start_ms: 4000, end_ms: 5000, used_by: ["s1"] },
        { id: "v1c3f2", start_ms: 5000, end_ms: 6000, used_by: [] }]),
      v1c7: family("v1c7", 20000, 22500, [{ id: "v1c7f1", start_ms: 20000, end_ms: 21000, used_by: [] },
        { id: "v1c7f2", start_ms: 21000, end_ms: 22500, used_by: [] }]),
    },
    exclusions: excluded.map((id) => ({ id, by: "s1", whole: true, covers: [`${id}f1`, `${id}f2`], reused: [] })),
    warnings: [], unreviewed: [], queue: [], accept_unreviewed: false, summary: { READY: 1 },
  };
}

const calls = [];
let excluded = [];
const invoke = async (command, args) => {
  if (command !== "mastering_review") throw new Error(`unexpected ${command}`);
  if (args.action === "decide" && args.what === "exclude") { calls.push(args); excluded = [...excluded, ...args.ids]; }
  else if (args.action === "decide" && args.what === "include") { calls.push(args); excluded = excluded.filter((id) => !args.ids.includes(id)); }
  else if (args.action === "undo") { calls.push(args); excluded = excluded.slice(0, -1); }
  return reviewView({ excluded });
};
const flush = async () => { for (let i = 0; i < 5; i += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };
const closed = openReviewPanel(invoke, {
  outputDir: "D:/o/run-1", video: 1, videoPath: "D:/o/run-1/video-1.mp4", title: "Video 1",
  mediaUrl: async () => null, cpuOnly: () => true, rerendered: () => undefined,
});
await flush();

const unitNode = () => document.getElementById("mreview-s1");
const SPAN = { v1c3f2: "5,0 s–6,0 s", v1c5: "9,0 s–11,0 s", v1c7f1: "20,0 s–21,0 s" };  // how each card shows its time
const buttonsOf = (card) => [...card.walk()].filter((node) => node.tag === "button").map((node) => node.textContent);
const buttonIn = (card, label) => [...card.walk()].find((node) => node.tag === "button" && node.textContent === label);
const altCards = () => unitNode().querySelector("mreview-shots.alt").children;

// (a) an unused alternative piece offers "Bỏ đoạn này" and "Bỏ cả cảnh gốc"; a whole shot offers no "cả cảnh gốc"
const altPiece = altCards().find((card) => card.textContent.includes(SPAN.v1c3f2));
assert.ok(altPiece, "the unused piece is listed as an alternative");
assert.deepEqual(buttonsOf(altPiece), ["Dùng cảnh này", "Bỏ đoạn này", "Bỏ cả cảnh gốc"]);
const altWhole = altCards().find((card) => card.textContent.includes(SPAN.v1c5));
assert.deepEqual(buttonsOf(altWhole), ["Dùng cảnh này", "Bỏ cảnh này"]);
// the chosen piece offers it too
const chosenCard = unitNode().querySelector("mreview-shots").children[0];
assert.ok(buttonsOf(chosenCard).includes("Bỏ cả cảnh gốc"));

// cancelling the confirmation sends nothing
buttonIn(altPiece, "Bỏ cả cảnh gốc").click();
let confirm = document.querySelector(".confirm-backdrop");
assert.ok(confirm, "the parent's exclusion asks first");
assert.match(confirm.textContent, /Bỏ cả cảnh gốc v1c3\?/);
assert.match(confirm.textContent, /2 đoạn sẽ không được dùng ở mọi câu của video này/);
assert.match(confirm.textContent, /Đoạn 1 · 4,0 s–5,0 s · Câu 1 đang dùng/);
assert.match(confirm.textContent, /Đoạn 2 · 5,0 s–6,0 s · chưa câu nào dùng/);
buttonIn(confirm, "Hủy").click();
await flush();
assert.equal(document.querySelector(".confirm-backdrop"), null);
assert.deepEqual(calls, []);

// confirming sends the parent, not the piece
buttonIn(altPiece, "Bỏ cả cảnh gốc").click();
buttonIn(document.querySelector(".confirm-backdrop"), "Bỏ cả cảnh gốc").click();
await flush();
assert.deepEqual(calls.at(-1), { outputDir: "D:/o/run-1", video: 1, action: "decide", unit: "s1", what: "exclude", ids: ["v1c3"] });
// the panel now shows the piece as left out, with "Bỏ loại", and the side list names the whole shot
const leftOut = altCards().find((card) => card.textContent.includes(SPAN.v1c3f2));
assert.match(leftOut.textContent, /Đã loại ở câu 1 \(cả cảnh gốc v1c3\)/);
assert.deepEqual(buttonsOf(leftOut), ["Bỏ loại"]);
const side = document.querySelector(".mreview-exclusions");
assert.match(side.textContent, /Câu 1 đã loại cả cảnh gốc v1c3 \(2 đoạn\)/);

// undo takes it back
buttonIn(body, "Hoàn tác").click();
await flush();
assert.deepEqual(calls.at(-1), { outputDir: "D:/o/run-1", video: 1, action: "undo" });
assert.deepEqual(buttonsOf(altCards().find((card) => card.textContent.includes(SPAN.v1c3f2))), ["Dùng cảnh này", "Bỏ đoạn này", "Bỏ cả cảnh gốc"]);

// (b) "Mọi cảnh đã nhập": a piece no sentence shows offers "Bỏ cả cảnh gốc" too
buttonIn(unitNode(), "Chọn cảnh khác…").click();
await flush();
const lists = unitNode().querySelectorAll("mreview-shots.alt");
assert.equal(lists.length, 2);
assert.ok(unitNode().textContent.includes("Mọi cảnh đã nhập"));
const every = lists[1].children;
const listed = every.find((card) => card.textContent.includes(SPAN.v1c7f1));
assert.deepEqual(buttonsOf(listed), ["Dùng cảnh này", "Bỏ đoạn này", "Bỏ cả cảnh gốc"]);
buttonIn(listed, "Bỏ cả cảnh gốc").click();
confirm = document.querySelector(".confirm-backdrop");
assert.match(confirm.textContent, /Bỏ cả cảnh gốc v1c7\?/);
assert.match(confirm.textContent, /20,0 s–22,5 s của nguồn, 2 đoạn/);
buttonIn(confirm, "Bỏ cả cảnh gốc").click();
await flush();
assert.deepEqual(calls.at(-1), { outputDir: "D:/o/run-1", video: 1, action: "decide", unit: "s1", what: "exclude", ids: ["v1c7"] });

// the side list's "Bỏ loại" lets the whole shot back in
buttonIn(document.querySelector(".mreview-exclusions"), "Bỏ loại").click();
await flush();
assert.deepEqual(calls.at(-1), { outputDir: "D:/o/run-1", video: 1, action: "decide", unit: "s1", what: "include", ids: ["v1c7"] });

buttonIn(body, "Đóng").click();
await closed;
assert.equal(body.children.length, 0, "closing removes the panel");
console.log("mastering panel tests passed");

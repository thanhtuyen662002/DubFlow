import assert from "node:assert/strict";
import { confirmDialog, confirmKeyAction, confirmLabels, cycleFocus, initialFocus, isTopmostDialog } from "../src/shell/confirm.ts";

// Labels fall back to a plain "Đồng ý / Hủy" pair.
assert.deepEqual(confirmLabels({ title: "t", message: "m" }), { confirm: "Đồng ý", cancel: "Hủy" });
assert.deepEqual(confirmLabels({ title: "t", message: "m", confirmLabel: "Xóa 2 video", cancelLabel: "Giữ lại" }),
  { confirm: "Xóa 2 video", cancel: "Giữ lại" });
assert.deepEqual(confirmLabels({ title: "t", message: "m", confirmLabel: "  ", cancelLabel: "" }), { confirm: "Đồng ý", cancel: "Hủy" });

// Esc cancels; Enter confirms unless the cancel button has focus; Tab moves focus.
assert.equal(confirmKeyAction("Escape", false, false), "cancel");
assert.equal(confirmKeyAction("Escape", false, true), "cancel");
assert.equal(confirmKeyAction("Enter", false, false), "confirm");
assert.equal(confirmKeyAction("Enter", false, true), "cancel");
assert.equal(confirmKeyAction("Tab", false, false), "next");
assert.equal(confirmKeyAction("Tab", true, false), "previous");
assert.equal(confirmKeyAction("a", false, false), null);
assert.equal(confirmKeyAction(" ", false, true), null);

// The dialog opens on the confirm button unless asked otherwise; on cancel, an Enter right away cancels
// (so a stray Enter can never opt into paid AI translation).
assert.equal(initialFocus({ title: "t", message: "m" }), "confirm");
assert.equal(initialFocus({ title: "t", message: "m", initialFocus: "confirm" }), "confirm");
assert.equal(initialFocus({ title: "t", message: "m", initialFocus: "cancel" }), "cancel");
assert.equal(confirmKeyAction("Enter", false, initialFocus({ title: "t", message: "m", initialFocus: "cancel" }) === "cancel"), "cancel");
// A dialog opened on cancel: Enter confirms only from the confirm button itself; anywhere else it is taken but does nothing.
assert.equal(confirmKeyAction("Enter", false, false, true, false), "stay");
assert.equal(confirmKeyAction("Enter", false, false, true, true), "confirm");
assert.equal(confirmKeyAction("Enter", false, true, true, false), "cancel");
assert.equal(confirmKeyAction("Escape", false, false, true, false), "cancel");
assert.equal(confirmKeyAction("Tab", false, false, true, false), "next");
// Other dialogs are as before: Enter confirms unless the cancel button has focus.
assert.equal(confirmKeyAction("Enter", false, false, false, false), "confirm");

// Only the topmost dialog (the last .confirm-backdrop in body) takes keys.
{
  const node = (className) => ({ classList: { contains: (name) => className.split(" ").includes(name) } });
  const [a, b, other] = [node("confirm-backdrop"), node("confirm-backdrop"), node("toast")];
  assert.equal(isTopmostDialog(b, [a, b, other]), true);
  assert.equal(isTopmostDialog(a, [a, b, other]), false);
  assert.equal(isTopmostDialog(a, [a, other]), true);
  assert.equal(isTopmostDialog(a, [b]), false);
  assert.equal(isTopmostDialog(a, []), false);
}

// Focus stays inside the dialog: Tab wraps in both directions.
assert.equal(cycleFocus(0, 2, false), 1);
assert.equal(cycleFocus(1, 2, false), 0);
assert.equal(cycleFocus(0, 2, true), 1);
assert.equal(cycleFocus(1, 2, true), 0);
assert.equal(cycleFocus(-1, 2, false), 0);
assert.equal(cycleFocus(-1, 2, true), 1);
assert.equal(cycleFocus(0, 0, false), -1);

// The real dialog in a small stand-in DOM. Like a browser: listeners on one node all run, in order, unless one calls
// stopImmediatePropagation (stopPropagation does not stop them); a press on text that cannot take focus moves focus
// to body.
{
  class FakeElement {
    constructor(tag) { Object.assign(this, { tag, children: [], parentNode: null, listeners: {}, attributes: {}, className: "", textContent: "", type: "" }); }
    get classList() { return { contains: (name) => this.className.split(" ").includes(name) }; }
    get isConnected() { for (let node = this; node; node = node.parentNode) if (node === document.body) return true; return false; }
    append(...nodes) { for (const node of nodes) { node.parentNode = this; this.children.push(node); } }
    remove() { if (this.parentNode) this.parentNode.children = this.parentNode.children.filter((node) => node !== this); this.parentNode = null; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    addEventListener(type, listener) { (this.listeners[type] ??= []).push(listener); }
    focus() { document.activeElement = this; }
    click() { for (const listener of this.listeners.click ?? []) listener({ target: this }); }
    mousedown(target = this) { for (const listener of this.listeners.mousedown ?? []) listener({ target }); }
  }
  const documentListeners = [];
  globalThis.HTMLElement = FakeElement;
  globalThis.document = {
    body: new FakeElement("body"), activeElement: null,
    createElement: (tag) => new FakeElement(tag),
    addEventListener: (type, listener) => documentListeners.push({ type, listener }),
    removeEventListener: (type, listener) => {
      const at = documentListeners.findIndex((entry) => entry.type === type && entry.listener === listener);
      if (at >= 0) documentListeners.splice(at, 1);
    },
  };
  const press = (key) => {
    let stopped = false;
    const event = { key, shiftKey: false, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, stopImmediatePropagation() { stopped = true; } };
    for (const { type, listener } of [...documentListeners]) {
      if (type === "keydown") listener(event);
      if (stopped) break;
    }
    return event;
  };
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  const open = (options) => {
    const state = { done: false, value: undefined };
    void confirmDialog(options).then((value) => Object.assign(state, { done: true, value }));
    const backdrop = document.body.children.at(-1);
    const [cancel, confirm] = backdrop.children[0].children.at(-1).children;
    return { state, backdrop, cancel, confirm, text: backdrop.children[0].children[1] };
  };
  const optIn = { title: "Dịch phụ đề bằng AI theo ngữ cảnh?", message: "…", confirmLabel: "Bật dịch AI", cancelLabel: "Không, dịch offline", initialFocus: "cancel", backdropCancels: false };
  const plain = { title: "Xóa video?", message: "…", danger: true };
  const page = new FakeElement("button");
  document.body.append(page);
  page.focus();

  // Opened on cancel: an Enter right away answers "no".
  {
    const dialog = open(optIn);
    assert.equal(document.activeElement, dialog.cancel);
    press("Enter");
    await settle();
    assert.deepEqual(dialog.state, { done: true, value: false });
    assert.equal(document.activeElement, page, "focus back where it was");
  }
  // The text clicked (focus on body), then Enter: no opt-in, the dialog stays and the key goes nowhere else.
  {
    const dialog = open(optIn);
    dialog.text.mousedown();
    document.activeElement = document.body;
    const event = press("Enter");
    await settle();
    assert.equal(dialog.state.done, false, "Enter with focus on body does not opt in");
    assert.equal(event.defaultPrevented, true);
    assert.ok(dialog.backdrop.isConnected);
    // Enter from the confirm button itself still turns it on.
    dialog.confirm.focus();
    press("Enter");
    await settle();
    assert.deepEqual(dialog.state, { done: true, value: true });
  }
  // A press beside the opt-in (the second click of a double-click) is no answer; Esc is "no".
  {
    const dialog = open(optIn);
    dialog.backdrop.mousedown();
    await settle();
    assert.equal(dialog.state.done, false);
    press("Escape");
    await settle();
    assert.deepEqual(dialog.state, { done: true, value: false });
  }
  // Two stacked dialogs, one Enter: only the top one acts, and the one below sees nothing of it.
  {
    const below = open(plain);
    const top = open(optIn);
    press("Enter");
    await settle();
    assert.deepEqual(top.state, { done: true, value: false });
    assert.equal(below.state.done, false, "the dialog below did not take the same Enter");
    assert.equal(document.activeElement, below.confirm);
    press("Enter");
    await settle();
    assert.deepEqual(below.state, { done: true, value: true });
  }
  {
    // The rc case: a dialog open, the AI question on top of it, focus on body, Enter: neither answers.
    const below = open(plain);
    const top = open(optIn);
    document.activeElement = document.body;
    press("Enter");
    await settle();
    assert.equal(top.state.done, false);
    assert.equal(below.state.done, false);
    press("Escape");
    await settle();
    assert.deepEqual(top.state, { done: true, value: false });
    assert.equal(below.state.done, false, "Esc closed only the top dialog");
    press("Escape");
    await settle();
    assert.deepEqual(below.state, { done: true, value: false });
  }
  {
    // Two plain dialogs: one Enter confirms only the top one.
    const below = open(plain);
    const top = open({ title: "Bỏ thay đổi?", message: "…" });
    press("Enter");
    await settle();
    assert.deepEqual(top.state, { done: true, value: true });
    assert.equal(below.state.done, false);
    press("Escape");
    await settle();
    assert.deepEqual(below.state, { done: true, value: false });
  }
  // Other dialogs keep their behaviour: Enter confirms with focus on body, a press beside cancels.
  {
    const dialog = open(plain);
    assert.equal(document.activeElement, dialog.confirm);
    document.activeElement = document.body;
    press("Enter");
    await settle();
    assert.deepEqual(dialog.state, { done: true, value: true });
    const again = open(plain);
    again.backdrop.mousedown();
    await settle();
    assert.deepEqual(again.state, { done: true, value: false });
  }
  assert.equal(documentListeners.length, 0, "every dialog removed its key listener");
  assert.equal(document.body.children.length, 1);
  delete globalThis.document;
  delete globalThis.HTMLElement;
}

console.log("confirm tests passed");

// Deleting a project says what goes and that the user's own media stays.
{
  const { deleteMessage, runFolder } = await import("../src/shell/project_delete.ts");
  assert.equal(runFolder("D:\\V\\DubFlow Output\\Phim tai lieu - A\\run-0123456789ab\\documentary.mp4"), "D:\\V\\DubFlow Output\\Phim tai lieu - A\\run-0123456789ab");
  assert.equal(runFolder("D:/o/run-2/video-1.mp4"), "D:/o/run-2");
  for (const kind of ["docu", "compilation", "product"]) {
    assert.match(deleteMessage(kind), /Thùng rác/);
    assert.match(deleteMessage(kind), /gốc bạn đã chọn không bị xóa/);
  }
}

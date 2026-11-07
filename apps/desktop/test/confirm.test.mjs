import assert from "node:assert/strict";
import { confirmKeyAction, confirmLabels, cycleFocus } from "../src/shell/confirm.ts";

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

// Focus stays inside the dialog: Tab wraps in both directions.
assert.equal(cycleFocus(0, 2, false), 1);
assert.equal(cycleFocus(1, 2, false), 0);
assert.equal(cycleFocus(0, 2, true), 1);
assert.equal(cycleFocus(1, 2, true), 0);
assert.equal(cycleFocus(-1, 2, false), 0);
assert.equal(cycleFocus(-1, 2, true), 1);
assert.equal(cycleFocus(0, 0, false), -1);

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

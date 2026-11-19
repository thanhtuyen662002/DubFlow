import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

// Issue #962: "Chỉ xuất âm thanh (.m4a)" is for one export. The other Tùy chọn khác (hình/giây, dung lượng, đoạn) are
// kept while the app runs (Issue #526), but every opened video starts with its picture exported again.
const view = readFileSync(new URL("../src/features/edit/view.ts", import.meta.url), "utf8").replace(/\r\n/g, "\n")
  .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
const between = (text, start, end) => {
  const from = text.indexOf(start);
  assert.ok(from >= 0, `missing: ${start}`);
  const to = text.indexOf(end, from + start.length);
  assert.ok(to > from, `no end after: ${start}`);
  return text.slice(from, to);
};
assert.ok(/const options = \{ fps: "", rate: "", range: "all" as "all" \| "marked", audioOnly: false \};/.test(view), "options start without audio only");
const open = between(view, "async function open(target: EditTarget", "\n  }\n");
const set = open.indexOf("session = {");
const reset = open.indexOf("options.audioOnly = false;");
assert.ok(set >= 0 && reset > set, "open(): audio only is cleared once the new video's session is set");
assert.ok(!/options\.(fps|rate|range) = /.test(open), "open(): the other export options are kept");
assert.equal(view.match(/options\.audioOnly = /g).length, 2, "audio only is set only by its checkbox and cleared only by open()");
console.log("edit export options ok");

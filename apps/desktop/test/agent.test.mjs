import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { AGENT_NAV, callTime, navOfTool } from "../src/features/agent/model.ts";

// Where Claude works over MCP (Issue #1004): the rail's busy dot follows the tool's page.
assert.equal(navOfTool("search_sources"), "nav-discover");
assert.equal(navOfTool("create_product_video"), "nav-product");
assert.equal(navOfTool("queue_dubbing_job"), "nav-queue");
assert.equal(navOfTool("make_shorts"), "nav-queue", "Shorts, Đổi khung and Gói đăng bài are panels of a finished video in Hàng đợi");
assert.equal(navOfTool("create_compilation"), "nav-compilation");
assert.equal(navOfTool("save_edit"), "nav-edit");
assert.equal(navOfTool("render_rough_cut"), "nav-docu", "any other tool is a Phim tài liệu tool");
assert.equal(navOfTool(null), "nav-docu");

// Every page the dot can go to is a rail item.
const html = readFileSync(new URL("../index.html", import.meta.url), "utf8");
for (const id of AGENT_NAV) assert.ok(html.includes(`id="${id}"`), id);
// The Cài đặt tab and its pane.
for (const id of ["settings-tab-agent", "settings-pane-agent", "agent-mcp-code", "agent-mcp-desktop", "agent-mcp-current", "agent-mcp-recent"]) {
  assert.ok(html.includes(`id="${id}"`), id);
}

assert.equal(callTime("not a time"), "");
assert.equal(callTime(undefined), "");
assert.match(callTime("2026-11-28T10:05:00Z"), /^\d{2}:\d{2}$/);

console.log("agent tests passed");

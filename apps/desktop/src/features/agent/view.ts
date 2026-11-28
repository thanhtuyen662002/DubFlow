import { parseMcp, parseMcpActivity, readable } from "../docu/model.ts";
import { callTime } from "./model.ts";

type Invoke = <T>(command: string, args?: Record<string, unknown>) => Promise<T>;

const $ = <T extends HTMLElement = HTMLElement>(selector: string): T => {
  const element = document.querySelector<T>(selector);
  if (!element) throw new Error(`Missing element ${selector}`);
  return element;
};

/**
 * Cài đặt › Agent AI (MCP) (Issue #1004): how to connect Claude to all of DubFlow, and what it is doing, wherever the
 * user is. The same host commands as the Phim tài liệu page's panel.
 */
export function mountAgentSettings(invoke: Invoke): void {
  const pane = $("#settings-pane-agent");
  for (const [button, field, idle] of [["#agent-mcp-copy-code", "#agent-mcp-code", "Sao chép lệnh"],
    ["#agent-mcp-copy-desktop", "#agent-mcp-desktop", "Sao chép cấu hình"]] as const) {
    $(button).addEventListener("click", () => {
      const text = $<HTMLTextAreaElement>(field).value;
      if (!text) return;
      void navigator.clipboard?.writeText(text).then(() => {
        $(button).textContent = "Đã sao chép";
        window.setTimeout(() => { $(button).textContent = idle; }, 1500);
      }).catch(() => undefined);
    });
  }
  void invoke("docu_mcp_connection").then((value) => {
    const connection = parseMcp(value);
    if (!connection) return;
    $<HTMLTextAreaElement>("#agent-mcp-code").value = connection.claudeCode;
    $<HTMLTextAreaElement>("#agent-mcp-desktop").value = connection.claudeDesktop;
  }).catch(() => {
    $("#agent-mcp-connect-note").textContent = "Bản DubFlow này không có máy chủ MCP đi kèm.";
  });

  async function poll(): Promise<void> {
    if (pane.hidden || pane.closest<HTMLElement>("[hidden]")) return;
    let activity = null;
    try {
      activity = parseMcpActivity(await invoke("docu_mcp_activity"));
    } catch {
      return;
    }
    const current = activity?.current ?? null;
    const now = $("#agent-mcp-current");
    now.textContent = current ? `Đang làm: ${readable(current.label)}` : activity ? "Không làm gì lúc này." : "Chưa có hoạt động nào từ agent.";
    now.classList.toggle("busy", Boolean(current));
    const list = $("#agent-mcp-recent");
    list.replaceChildren(...(activity?.recent ?? []).slice(0, 8).map((call) => {
      const item = document.createElement("li");
      item.classList.toggle("failed", call.state === "failed");
      const time = document.createElement("time");
      time.textContent = callTime(call.finished ?? call.started);
      const label = document.createElement("span");
      label.textContent = readable(call.label);
      if (call.state === "failed" && call.message) label.title = call.message;
      item.append(time, label);
      return item;
    }));
  }
  window.setInterval(() => void poll(), 3000);
  // Opening the tab shows the latest at once.
  $("#settings-tab-agent").addEventListener("click", () => window.setTimeout(() => void poll(), 0));
}

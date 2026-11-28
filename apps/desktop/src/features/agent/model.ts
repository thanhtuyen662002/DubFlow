/**
 * DubFlow's MCP server drives the whole app (#994–#1002): which page each of its tools works on, so the rail's busy
 * dot sits where Claude is working (Issue #1004). A tool missing here is a Phim tài liệu tool.
 */
const PAGES: ReadonlyArray<readonly [string, readonly string[]]> = [
  ["nav-discover", ["search_sources", "list_footage_suffixes", "get_trends", "suggest_keywords", "discover_videos", "download_video"]],
  ["nav-product", ["list_vietnamese_voices", "create_product_video", "get_product_video", "list_product_videos"]],
  ["nav-queue", ["queue_dubbing_job", "get_dubbing_job", "list_dubbing_jobs", "list_dubbed_videos", "get_dubbed_video", "make_shorts",
    "make_landscape_16x9", "package_video", "get_video_job", "cancel_video_job", "suggest_post"]],
  ["nav-compilation", ["create_compilation", "get_compilation", "list_compilations"]],
  ["nav-edit", ["get_edit", "save_edit", "get_edit_job"]],
];

/** The rail items a busy dot can sit on. */
export const AGENT_NAV = ["nav-discover", "nav-product", "nav-queue", "nav-compilation", "nav-edit", "nav-docu"] as const;

export function navOfTool(tool: string | null | undefined): (typeof AGENT_NAV)[number] {
  const found = PAGES.find(([, tools]) => tools.includes(String(tool)));
  return (found?.[0] ?? "nav-docu") as (typeof AGENT_NAV)[number];
}

/** "14:05" for a call's ISO time (local), "" when there is none. */
export function callTime(iso: string | undefined): string {
  const at = Date.parse(String(iso ?? ""));
  if (Number.isNaN(at)) return "";
  const date = new Date(at);
  return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

import type { JobStatus } from "../features/job_status/status.ts";
import { parseSourceCredit, type SourceCredit } from "../features/credit/model.ts";

/**
 * Browser-only stand-in for the Tauri host so the UI can be designed with
 * `npm run dev`. It simulates the supervisor status file; it never runs media.
 */
type Running = { startedAt: number; dubbing: boolean; cancelled: boolean };

const running = new Map<string, Running>();
const DURATION_MS = 24_000;
const STEPS: Array<[number, string]> = [
  [0.02, "Đang kiểm tra media nguồn"],
  [0.08, "Đang trích xuất audio"],
  [0.18, "Đang nhận dạng lời thoại"],
  [0.44, "Đang dịch cục bộ sang tiếng Việt"],
  [0.66, "Đã tạo subtitle SRT/ASS"],
  [0.7, "Đang lồng tiếng Việt"],
  [0.86, "Đang render video H.264/AAC"],
  [0.88, "Đang kiểm tra codec, thời lượng và khả năng đọc output"],
];

const voices = [
  ["vi-truc-ly-vieneu3-v1", "Trúc Ly", "Nữ", "Nam", "tự nhiên", "Giọng nữ miền Nam, tự nhiên cho đối thoại phim."],
  ["vi-adam-bua-vieneu3-v1", "Adam Bựa", "Nam", "Bắc", "tự nhiên", "Giọng nam miền Bắc, trẻ trung."],
  ["vi-minh-khang-vieneu3-v1", "Minh Khang", "Nam", "Nam", "kể chuyện", "Giọng nam trầm, phù hợp thuyết minh."],
  ["vi-thu-ha-vieneu3-v1", "Thu Hà", "Nữ", "Bắc", "tin tức", "Giọng nữ miền Bắc rõ ràng, chuẩn đọc tin."],
  ["vi-bao-ngoc-vieneu3-v1", "Bảo Ngọc", "Nữ", "Trung", "tự nhiên", "Giọng nữ miền Trung mềm mại."],
].map(([voice_id, name, gender, accent, style, description]) => ({
  voice_id, name, gender, accent, style, description, voice_version: "1.0.0", approved: true,
}));

function status(id: string): { status: JobStatus; output_path: string | null } | null {
  const job = running.get(id);
  if (!job) return null;
  const fraction = Math.min(1, (Date.now() - job.startedAt) / DURATION_MS);
  const base: JobStatus = {
    schema_version: 1, state: "RUNNING", reason: "progress", message: "",
    checkpoint_id: null,
    progress: { completed_units: String(Math.min(999, Math.round(fraction * 1000))), total_units: "1000", heartbeat_sequence: "1" },
    retry: { attempt: "1", max_attempts: "3", condition_fingerprint: null },
    resource: { kind: "CPU", held: true, release_requested: false },
  };
  if (job.cancelled) {
    return { status: { ...base, state: "FAILED", reason: "cancelled", message: "Đã hủy theo yêu cầu", resource: { ...base.resource, held: false } }, output_path: null };
  }
  if (fraction >= 1) {
    return {
      status: { ...base, state: "COMPLETED", reason: job.dubbing ? "completed_partial_dubbing" : "completed", message: job.dubbing ? "Đã xuất video; 1 câu chưa lồng tiếng được. Audio gốc được giữ ở các đoạn lỗi." : "Hoàn tất video Vietsub", progress: { ...base.progress, completed_units: "1000" }, resource: { ...base.resource, held: false } },
      output_path: `D:\\Videos\\DubFlow Output\\${id}\\final_vi.mp4`,
    };
  }
  let message = STEPS[0][1];
  for (const [from, label] of STEPS) if (fraction >= from) message = label;
  return { status: { ...base, message }, output_path: null };
}

const reviewCues = [
  ["c1", 1010, 10630, "Twitter来自维基百科,自由的百科全书", "Twitter từ Wikipedia, bách khoa toàn thư mở", false],
  ["c2", 11890, 19150, "Twitter是一个社交网络及微博客服务", "Twitter là một mạng xã hội và dịch vụ blog nhỏ.", false],
  ["c3", 38970, 45610, "每则更新文字会显示在用户页面上,并会实时提供给每位用户", "Mỗi bản cập nhật văn bản sẽ được hiển thị trên trang người dùng và sẽ có sẵn cho mỗi người dùng một cách đúng lúc", true],
  ["c4", 64570, 72810, "目前,只有美国、加拿大和英国提供免费的手机短信更新", "Hiện chỉ Mỹ, Canada và Anh miễn phí cập nhật qua tin nhắn điện thoại.", false],
].map((row) => {
  const [cue_id, start_ms, end_ms, source_text, machine_text, dub_failed] = row as [string, number, number, string, string, boolean];
  return { cue_id, start_ms, end_ms, source_text, machine_text, dub_failed };
});
let savedOverrides: Record<string, string> = {};
const linkFetches = new Map<string, { url: string; title: string | null; startedAt: number }>();
let douyinSession = false;

/**
 * A pretend download: 3 s preparing, then ~45 MB over 9 s. Bilibili links
 * report their size; other links do not, so both bar styles show. Douyin
 * fails for want of a session until one is "saved".
 */
function fetchStatus(scanId: string) {
  const fetch = linkFetches.get(scanId);
  if (!fetch) return null;
  const elapsed = Date.now() - fetch.startedAt;
  const provider = /bilibili|b23\.tv/.test(fetch.url) ? "bilibili" : /douyin/.test(fetch.url) ? "douyin" : "generic";
  const base = { scan_id: scanId, url: fetch.url, provider, title: fetch.title, detail: null as string | null, queue_position: null, started_at_ms: fetch.startedAt,
    downloaded_bytes: 0, total_bytes: null as number | null, media_path: null as string | null, error: null as string | null, error_code: null as string | null };
  if (provider === "douyin" && !douyinSession) {
    return elapsed > 3000
      ? { ...base, state: "failed", error_code: "AUTH_REQUIRED", error: "Douyin yêu cầu đăng nhập để tải video này. Bấm “Đăng nhập Douyin”, đăng nhập rồi thử lại." }
      : { ...base, state: "preparing", detail: "Đang mở trang video…" };
  }
  if (elapsed < 3000) return { ...base, state: "preparing", detail: elapsed < 1500 ? "Đang mở trang video…" : "Đang đọc thông tin video…" };
  const total = 47_065_346;
  // Uneven pace so the speed and time left move a little.
  const t = (elapsed - 3000) / 9000;
  const done = Math.min(total, Math.round(total * (t + Math.sin(t * 9) * 0.02)));
  const known = provider === "bilibili" ? total : null;
  return t >= 1
    ? { ...base, state: "done", downloaded_bytes: total, total_bytes: total, media_path: `C:/DubFlow/source-media/${scanId}.mp4` }
    : { ...base, state: "downloading", downloaded_bytes: Math.max(0, done), total_bytes: known };
}

/** Pretend disk usage per cleanup category, in bytes. */
const GB = 1_073_741_824;
const previewStorage: Record<string, number> = { work: 3.4 * GB, versions: 10.5 * GB, profiles: 0.62 * GB, link_cache: 2.4 * GB };
/** Every other report shows the link cache as busy, so both states can be designed. */
let previewLinkBusy = true;
/** The first clean of old versions finds one in use. */
let previewVersionInUse = true;

function previewStorageReport() {
  const downloading = [...linkFetches.keys()].some((scan) => !["done", "failed"].includes(String(fetchStatus(scan)?.state)));
  previewLinkBusy = !previewLinkBusy;
  const busy = downloading || previewLinkBusy;
  return {
    categories: Object.entries(previewStorage).map(([id, bytes]) => ({
      id, bytes: id === "link_cache" && busy ? 0 : Math.round(bytes), items: bytes > 0 ? (id === "versions" ? 12 : id === "profiles" ? 9 : 3) : 0,
      busy: id === "link_cache" && busy,
    })),
    total_used: Math.round(3.6 * GB + Object.values(previewStorage).reduce((sum, bytes) => sum + bytes, 0)),
    install_free_bytes: Math.round(96.2 * GB), output_free_bytes: Math.round(123.1 * GB),
  };
}

function previewStorageClean(categories: string[], olderThanDays: unknown) {
  // The automatic clean only finds older videos' work files.
  if (typeof olderThanDays === "number") {
    const freed = Math.min(2.3 * GB, previewStorage.work);
    previewStorage.work -= freed;
    return { freed_bytes: Math.round(freed), skipped: [] };
  }
  let freed = 0;
  const skipped: string[] = [];
  for (const id of categories) {
    if (id === "link_cache" && previewLinkBusy) { skipped.push("Bộ đệm tải link (đang tải link)"); continue; }
    if (id === "versions" && previewVersionInUse) {
      previewVersionInUse = false;
      freed += 9.6 * GB;
      previewStorage.versions -= 9.6 * GB;
      skipped.push("Bản cài 1.1.18");
      continue;
    }
    freed += previewStorage[id] ?? 0;
    previewStorage[id] = 0;
  }
  return { freed_bytes: Math.round(freed), skipped };
}

let picked = 0;
const sourceCredits = new Map<string, SourceCredit>();
const packages = new Map<string, { started: number; cancelled: boolean }>();
const compilations = new Map<string, { started: number; cancelled: boolean; total: number }>();
const shorts = new Map<string, { started: number; cancelled: boolean; items: Array<{ title: string }> }>();
const reframes = new Map<string, { started: number; cancelled: boolean }>();
/** Edit drafts and exports per finished video (Cắt ghép). */
const editProjects = new Map<string, unknown>();
type PreviewExport = { path: string; srt_path: string; created: number; duration: number; quality: string; segments: Array<{ start: number; end: number; speed: number; volume: number }> };
const editExports = new Map<string, PreviewExport[]>();
const editRuns = new Map<string, { started: number; cancelled: boolean; entry: PreviewExport; recorded: boolean }>();
const EDIT_EXPORT_MS = 5000;

function previewEditStatus(outputPath: string) {
  const run = editRuns.get(outputPath);
  const exports = editExports.get(outputPath) ?? [];
  if (!run) return { status: null, exports };
  const elapsed = Date.now() - run.started;
  if (run.cancelled) return { status: { schema_version: 1, state: "CANCELLED", percent: 0, message: "Đã hủy xuất video.", output_path: null }, exports };
  if (elapsed < EDIT_EXPORT_MS) {
    const percent = Math.floor(elapsed / EDIT_EXPORT_MS * 100);
    return { status: { schema_version: 1, state: "RUNNING", percent, message: `Đang xuất video… ${percent}%`, output_path: run.entry.path }, exports };
  }
  if (!run.recorded) {
    run.recorded = true;
    exports.push(run.entry);
    editExports.set(outputPath, exports);
  }
  return { status: { schema_version: 1, state: "COMPLETED", percent: 100, message: "Đã xuất video.", output_path: run.entry.path }, exports };
}
export async function previewInvoke<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  await new Promise((resolve) => setTimeout(resolve, 120));
  switch (command) {
    case "edit_project_load": return {
      duration: 154.2, width: 1024, height: 576, size_bytes: 46_000_000, has_audio: true,
      project: editProjects.get(String(args.outputPath)) ?? null,
      cues: reviewCues.map(({ cue_id, start_ms, end_ms, machine_text }) => ({ start_ms, end_ms, text: savedOverrides[cue_id] ?? machine_text })),
    } as T;
    case "edit_project_save": editProjects.set(String(args.outputPath), JSON.parse(JSON.stringify(args.project))); return null as T;
    case "edit_export": {
      const request = args.request as { outputPath: string; folder: string; fileName: string; quality: string; segments: PreviewExport["segments"] };
      if (editRuns.get(request.outputPath) && previewEditStatus(request.outputPath).status?.state === "RUNNING") throw new Error("Video này đang được xuất");
      const taken = new Set((editExports.get(request.outputPath) ?? []).map((entry) => entry.path));
      const stem = request.fileName.replace(/\.mp4$/i, "").trim();
      let path = `${request.folder}/${stem}.mp4`;
      for (let number = 2; taken.has(path); number++) path = `${request.folder}/${stem} (${number}).mp4`;
      const duration = request.segments.reduce((total, segment) => total + (segment.end - segment.start) / segment.speed, 0);
      editRuns.set(request.outputPath, { started: Date.now(), cancelled: false, recorded: false,
        entry: { path, srt_path: path.replace(/\.mp4$/, ".srt"), created: Math.floor(Date.now() / 1000), duration, quality: request.quality, segments: request.segments } });
      return { path } as T;
    }
    case "edit_status": return previewEditStatus(String(args.outputPath)) as T;
    case "edit_cancel": {
      const run = editRuns.get(String(args.outputPath));
      if (run && Date.now() - run.started < EDIT_EXPORT_MS) run.cancelled = true;
      return null as T;
    }
    case "reframe_info": return { width: 1080, height: 1920, vertical: true } as T;
    case "reframe_render": {
      const request = args.request as { outputPath: string };
      reframes.set(request.outputPath, { started: Date.now(), cancelled: false });
      return null as T;
    }
    case "reframe_status": {
      const path = String(args.outputPath), run = reframes.get(path);
      if (!run) return null as T;
      const state = run.cancelled ? "CANCELLED" : Date.now() - run.started > 6000 ? "COMPLETED" : "RUNNING";
      return { schema_version: 1, state,
        message: state === "COMPLETED" ? "Đã tạo video 16:9." : state === "CANCELLED" ? "Đã hủy đổi khung 16:9." : "Đang tạo video 16:9…",
        ...(state === "COMPLETED" ? { path: path.replace(/final_vi\.mp4$/i, "final_vi_16x9.mp4"), duration_ms: 30000 } : {}),
      } as T;
    }
    case "reframe_cancel": {
      const run = reframes.get(String(args.outputPath));
      if (run) run.cancelled = true;
      return null as T;
    }
    case "shorts_render": {
      const request = args.request as { outputPath: string; items: Array<{ title: string }> };
      shorts.set(request.outputPath, { started: Date.now(), cancelled: false, items: request.items });
      return null as T;
    }
    case "shorts_status": {
      const path = String(args.outputPath), run = shorts.get(path);
      if (!run) return null as T;
      const state = run.cancelled ? "CANCELLED" : Date.now() - run.started > 6000 ? "COMPLETED" : "RUNNING";
      const outputs = state === "COMPLETED" ? run.items.map((item, i) => ({
        path: path.replace(/final_vi\.mp4$/i, `shorts/short-${i + 1}.mp4`),
        text_path: path.replace(/final_vi\.mp4$/i, `shorts/short-${i + 1}.txt`), title: item.title,
        text: `${item.title}\nXem bản đầy đủ: Video mẫu trên kênh.\n`,
      })) : [];
      return { schema_version: 1, state, done: outputs.length, total: run.items.length, outputs,
        message: state === "COMPLETED" ? "Đã tạo Shorts." : state === "CANCELLED" ? "Đã hủy cắt Shorts." : "Đang tạo Shorts…" } as T;
    }
    case "shorts_cancel": {
      const run = shorts.get(String(args.outputPath));
      if (run) run.cancelled = true;
      return null as T;
    }
    case "compilation_render": {
      const request = args.request as { items: { lead: string }[]; intro: string; outro: string };
      const output = `D:/Videos/DubFlow Output/Tong hop - preview/run-${compilations.size.toString(16).padStart(12, "0")}`;
      const total = request.items.length + request.items.filter(item => item.lead).length + Number(Boolean(request.intro)) + Number(Boolean(request.outro));
      compilations.set(output, { started: Date.now(), cancelled: false, total });
      return output as T;
    }
    case "compilation_status": {
      const output = String(args.outputDir), run = compilations.get(output);
      if (!run) throw new Error("Không tìm thấy phiên tổng hợp");
      const done = Math.min(run.total, Math.floor((Date.now() - run.started) / 1000));
      const state = run.cancelled ? "CANCELLED" : done === run.total ? "COMPLETED" : "RUNNING";
      return { schema_version: 1, state, done, total: run.total, message: state === "COMPLETED" ? "Đã tạo video tổng hợp." : state === "CANCELLED" ? "Đã hủy tổng hợp." : "Đang tạo video tổng hợp…",
        path: state === "COMPLETED" ? `${output}/compilation.mp4` : null } as T;
    }
    case "compilation_cancel": {
      const run = compilations.get(String(args.outputDir));
      if (run) run.cancelled = true;
      return null as T;
    }
    case "compilation_description": return "00:00 Mở đầu\n00:05 Phần 1: Video thứ nhất\n00:30 Phần 2: Video thứ hai\n\nNguồn: Tác giả (Bilibili) – https://example.org/video\n" as T;
    case "package_info": return { has_background_stem: true, voice_id: voices[0].voice_id } as T;
    case "package_video": {
      const request = args.request as { outputPath: string };
      packages.set(request.outputPath, { started: Date.now(), cancelled: false });
      return null as T;
    }
    case "package_status": {
      const path = String(args.outputPath), run = packages.get(path);
      if (!run) return null as T;
      const state = run.cancelled ? "CANCELLED" : Date.now() - run.started > 6000 ? "COMPLETED" : "RUNNING";
      return { schema_version: 1, state, message: state === "COMPLETED" ? "Đã tạo video đóng gói." : state === "CANCELLED" ? "Đã hủy đóng gói." : "Đang tạo video đóng gói…",
        ...(state === "COMPLETED" ? { path: path.replace(/final_vi\.mp4$/i, "final_vi_packaged.mp4") } : {}) } as T;
    }
    case "package_cancel": {
      const run = packages.get(String(args.outputPath));
      if (run) run.cancelled = true;
      return null as T;
    }
    case "source_credit_get": return (sourceCredits.has(String(args.outputPath)) ? { ...sourceCredits.get(String(args.outputPath))! } : null) as T;
    case "source_credit_set": {
      const credit = args.credit === null ? null : parseSourceCredit(args.credit);
      if (!credit) throw new Error("Thông tin nguồn không hợp lệ.");
      sourceCredits.set(String(args.outputPath), credit);
      return null as T;
    }
    case "ai_key_get": return (previewKeys.get(String(args.provider)) ?? null) as T;
    case "ai_key_set": previewKeys.set(String(args.provider), String(args.key).trim()); return `…${String(args.key).trim().slice(-4)}` as T;
    case "ai_key_delete": previewKeys.delete(String(args.provider)); return null as T;
    case "release_info": return { version: "0.1.0", channel: "preview", backend: "mock" } as T;
    case "voice_catalog": return { default_voice_id: voices[0].voice_id, voices } as T;
    case "pick_files": {
      picked += 1;
      return [`D:\\Videos\\Douyin\\phim-ngan-tap-${picked}.mp4`, `D:\\Videos\\Bilibili\\review-dien-thoai-${picked}.mkv`] as T;
    }
    case "expand_video_paths": return (args.paths as string[]).filter((path) => /\.(mp4|mkv|mov|avi|webm|m4v)$/i.test(path)) as T;
    case "pick_folder": return "D:/Videos/Kết quả DubFlow" as T;
    case "start_job": running.set(String(args.jobId), { startedAt: Date.now(), dubbing: !!args.enableDubbing, cancelled: false }); return null as T;
    case "cancel_job": { const job = running.get(String(args.jobId)); if (job) job.cancelled = true; return null as T; }
    case "job_status": return status(String(args.jobId)) as T;
    case "open_path": case "reveal_path": case "reveal_download": console.info(`[preview] ${command}`, args.path); return null as T;
    case "fetch_url": {
      const scanId = `link-${Date.now().toString(16)}${Math.random().toString(16).slice(2, 6)}`;
      const title = typeof args.title === "string" && args.title.trim() ? args.title.trim().slice(0, 200) : null;
      linkFetches.set(scanId, { url: String(args.url), title, startedAt: Date.now() });
      return { status: fetchStatus(scanId), output_dir: `D:/Videos/DubFlow Output/${title ?? scanId} - vi/${scanId}` } as T;
    }
    case "fetch_status": return fetchStatus(String(args.scanId)) as T;
    case "withdraw_fetch": case "cancel_fetch": return linkFetches.delete(String(args.scanId)) as T;
    case "default_download_dir": return "D:/Videos/DubFlow Downloads" as T;
    case "keep_download": {
      const name = String(args.title ?? "video").replace(/[<>:"/\\|?*\u0000-\u001f]/g, "").trim().slice(0, 120) || "video";
      return `${typeof args.folder === "string" && args.folder ? args.folder : "D:/Videos/DubFlow Downloads"}/${name}.mp4` as T;
    }
    // A plain check is "ready"; a forced refresh stands for a fresh session.
    case "ensure_douyin_session": if (args.force) douyinSession = true; return "ready" as T;
    case "douyin_account": return { logged_in: false } as T;
    case "douyin_logout": douyinSession = false; return null as T;
    case "douyin_view_open": case "douyin_view_bounds": case "douyin_view_close": return null as T;
    case "check_update": return { ok: true, available: true, version: "0.3.0", channel: "candidate", notes: "DubFlow 0.3.0", setup: { size_bytes: 325_000_000 } } as T;
    case "download_update": return { ok: true, path: "C:/Users/preview/AppData/Local/DubFlow/updates/DubFlow-0.3.0-windows-x64-setup.exe", version: "0.3.0" } as T;
    case "install_update": case "restart_app": return null as T;
    case "storage_report": await new Promise((resolve) => setTimeout(resolve, 700)); return previewStorageReport() as T;
    case "storage_clean": await new Promise((resolve) => setTimeout(resolve, 1200)); return previewStorageClean(args.categories as string[], args.olderThanDays) as T;
    case "storage_free": return Math.round(123.1 * GB) as T;
    case "list_versions": return { ok: true, current: "0.2.0-rc6", versions: [
      { version: "0.2.0-rc6", channel: "candidate", installed_at_utc: "2026-10-25T00:00:00Z" },
      { version: "0.2.0-rc5", channel: "candidate", installed_at_utc: "2026-10-24T00:00:00Z" },
    ] } as T;
    case "activate_version": return { ok: true, current_version: String(args.version) } as T;
    case "review_speakers": return [
      { id: "S1", gender: "female", voice_id: "vi-truc-ly-vieneu3-v1", cues: 9 },
      { id: "S2", gender: "male", voice_id: "vi-adam-bua-vieneu3-v1", cues: 5 },
    ] as T;
    case "post_suggestions": return { ok: true,
      titles: ["Tiên hiệp: Bí mật linh kiếm thức tỉnh (Vietsub)", "Thanh linh kiếm này đã ngủ yên suốt 300 năm!", "Linh kiếm khiến ai cũng bất ngờ"],
      description: "Thanh linh kiếm này đã ngủ yên suốt 300 năm! Vì sao một đệ tử tu tiên lại đánh thức được linh kiếm?\n#vietsub",
      hashtags: ["#linhkiem", "#tienhiep", "#phimtienhiep", "#vietsub", "#trungquoc"], keywords: [] } as T;
    case "discover": return previewDiscover(String(args.platform), String(args.action), Number(args.page ?? 1)) as T;
    case "open_discovery": return `https://example.invalid/${String(args.platform)}` as T;
    case "scroll_discovery": return null as T;
    case "translate_terms": {
      const terms = args.terms as string[];
      // Titles take a moment, like the local translator, so "Đang dịch…" shows.
      if (terms.some((term) => PREVIEW_GLOSS.has(term))) await new Promise((resolve) => setTimeout(resolve, 900));
      return { ok: true, items: terms.map((term) => PREVIEW_GLOSS.get(term) ?? `(nghĩa) ${term}`) } as T;
    }
    case "collect_discovery": return previewCollected(String(args.platform)) as T;
    case "review_clip": return previewTone(Number(args.endMs) - Number(args.startMs), Boolean(args.source)) as T;
    // No asset protocol in a browser: the player shows its placeholder.
    case "allow_media": return null as T;
    case "video_frame": return await previewFrame(Number(args.position), /final_vi\.mp4$/i.test(String(args.sourcePath))) as T;
    case "save_douyin_session": douyinSession = true; return 12 as T;
    case "review_cues": return reviewCues.map((cue) => ({ ...cue, text: savedOverrides[cue.cue_id] ?? cue.machine_text, edited: cue.cue_id in savedOverrides })) as T;
    case "save_review": savedOverrides = { ...(args.overrides as Record<string, string>) }; return "D:\Videos\DubFlow Output\preview" as T;
    case "pick_product_files": return (args.kind === "music" ? ["D:/Nhac/nen.mp3"] : args.kind === "background" ? ["D:/Anh/nen-1080x720.png"] : args.kind === "media" ? ["D:/Clip/doi-tac.mp4"] : ["D:/Anh/hop.jpg", "D:/Anh/nhan.png"]) as T;
    case "product_image": return await previewFrame(String(args.path).length % 3 / 3) as T;
    case "product_render": productStarted = Date.now(); productCancelled = 0; return "D:/Videos/DubFlow Output/Ban hang - preview/run-000000000000" as T;
    case "product_status": return previewProductStatus() as T;
    case "product_cancel": productCancelled = Date.now(); return null as T;
    default: throw new Error(`preview host does not implement ${command}`);
  }
}

/** A short sine tone WAV so the review "listen" buttons work in the browser preview. */
const PREVIEW_WORDS = ["仙侠剧名场面", "美食探店", "国漫新番", "古装剧高能", "萌宠日常", "搞笑配音", "旅行vlog", "手工制作", "健身打卡", "动画短片"];

function previewDiscover(platform: string, action: string, page: number): unknown {
  if (action === "trends" || action === "suggest") {
    return { ok: true, next_page: null, items: PREVIEW_WORDS.map((keyword, index) => ({ platform, keyword, rank: index + 1, heat: index < 6 ? 9_800_000 - index * 1_250_000 : null, url: "" })) };
  }
  if (platform !== "bilibili") return { ok: false, code: "UNSUPPORTED", message: "preview" };
  return {
    ok: true, next_page: page < 3 ? page + 1 : null,
    items: Array.from({ length: 8 }, (_, index) => {
      // BV + 10 characters, like real Bilibili ids.
      const bvid = `BV1Xe4y1${page}${index}7Q`;
      return {
        platform, video_id: bvid, title: PREVIEW_TITLES[(index + page - 1) % PREVIEW_TITLES.length][0], author: PREVIEW_AUTHORS[index % PREVIEW_AUTHORS.length],
        author_id: String(546195 + index), duration_s: 95 + index * 37, plays: 12_000 * (index + 1) * page, likes: null,
        cover_url: `https://picsum.photos/seed/${bvid}/480/300`, page_url: `https://www.bilibili.com/video/${bvid}`,
      };
    }),
  };
}

/** Result titles as the platforms write them, with what the local translator would give for the cleaned text. */
const PREVIEW_TITLES: ReadonlyArray<[string, string, string]> = [
  ["中国少有的R级犯罪片《猎虎贰》 #电影解说 #犯罪", "中国少有的R级犯罪片《猎虎贰》", "Phim tội phạm hiếm có của Trung Quốc: Săn hổ 2"],
  ["1939年，穷女孩混进上流舞会🔥🔥 @电影小助手", "1939年，穷女孩混进上流舞会", "Năm 1939, cô gái nghèo lẻn vào dạ tiệc thượng lưu"],
  ["男人抠出一块骨头，鼻子里竟藏着活物😱 #悬疑", "男人抠出一块骨头，鼻子里竟藏着活物", "Người đàn ông rút ra mẩu xương, trong mũi còn có vật sống"],
  ["难道这就是传说中的完美犯罪吗？", "难道这就是传说中的完美犯罪吗？", "Đây có phải là tội ác hoàn hảo trong truyền thuyết?"],
  ["仙侠剧名场面合集 太燃了 #仙侠 #古装", "仙侠剧名场面合集 太燃了", "Tổng hợp cảnh kinh điển phim tiên hiệp, quá cháy"],
  ["农村生活vlog｜奶奶做的手工面条", "农村生活vlog｜奶奶做的手工面条", "Vlog đời sống nông thôn | Mì tự làm của bà"],
  ["Vlog du lịch Trùng Khánh 4K", "Vlog du lịch Trùng Khánh 4K", "Vlog du lịch Trùng Khánh 4K"],
  ["萌宠日常：猫咪第一次见到雪❄️ #萌宠", "萌宠日常：猫咪第一次见到雪", "Thú cưng mỗi ngày: mèo lần đầu thấy tuyết"],
  ["国漫新番 第3集 高能片段", "国漫新番 第3集 高能片段", "Hoạt hình Trung Quốc mới tập 3, đoạn gay cấn"],
];
const PREVIEW_GLOSS = new Map(PREVIEW_TITLES.map(([, cleaned, vi]) => [cleaned, vi]));
const PREVIEW_AUTHORS = ["影视飓风", "小片片说大片", "李子柒", "萌宠研究所"];

function previewCollected(platform: string): unknown {
  return Array.from({ length: 6 }, (_, index) => {
    const [title] = PREVIEW_TITLES[(index + 3) % PREVIEW_TITLES.length];
    if (platform === "kuaishou") {
      const id = `3x${["k8f2", "9qzm", "tw4e", "c7ua", "h2pn", "r5vd"][index]}${"abcdefghjk".slice(index, index + 4)}`;
      // Kuaishou pages carry a direct media link on its CDN.
      return { page_url: `https://www.kuaishou.com/short-video/${id}`, title, author: PREVIEW_AUTHORS[index % PREVIEW_AUTHORS.length],
        cover_url: `https://picsum.photos/seed/${id}/300/480`, fetch_url: `https://v2.kwaicdn.com/ksc2/preview-${id}.mp4?pkey=preview` };
    }
    const host = platform === "douyin" ? "www.douyin.com/video" : "www.bilibili.com/video";
    const id = platform === "douyin" ? `7431852694117${String(28_610 + index * 7_919).padStart(6, "0")}` : `BV1Kq4y1${index}m7Z`;
    return { page_url: `https://${host}/${id}`, title, author: PREVIEW_AUTHORS[index % PREVIEW_AUTHORS.length],
      cover_url: `https://picsum.photos/seed/${id}/${platform === "douyin" ? "300/480" : "480/300"}`, fetch_url: `https://${host}/${id}` };
  });
}

/** A product batch of three videos, one every 3 s, that can be cancelled. */
/** Stand-in for Windows Credential Manager. */
const previewKeys = new Map<string, string>();
let productStarted = 0;
let productCancelled = 0;
function previewProductStatus() {
  const now = productCancelled || Date.now();
  const done = Math.min(3, Math.floor((now - productStarted) / 3000));
  const videos = Array.from({ length: done }, (_, index) => ({ index: index + 1, ok: true, path: `D:/Videos/DubFlow Output/Ban hang - preview/run-000000000000/video-${index + 1}.mp4`, duration_ms: 31_000 }));
  if (productCancelled && done < 3) return { schema_version: 1, state: "CANCELLED", done, total: 3, message: done ? `Đã hủy. Giữ lại ${done} video đã xong.` : "Đã hủy.", videos };
  return done < 3
    ? { schema_version: 1, state: "RUNNING", done, total: 3, message: `Đang render video ${done + 1}/3…`, videos }
    : { schema_version: 1, state: "COMPLETED", done, total: 3, message: "Đã tạo 3/3 video", videos };
}

/** A 16:9 stand-in frame with a burned-in Chinese caption near the bottom. */
async function previewFrame(position: number, vertical = false): Promise<ArrayBuffer> {
  const canvas = document.createElement("canvas");
  canvas.width = 960;
  canvas.height = 540;
  const context = canvas.getContext("2d")!;
  const sky = context.createLinearGradient(0, 0, 0, 540);
  sky.addColorStop(0, `hsl(${210 + position * 90} 35% 30%)`);
  sky.addColorStop(1, "hsl(220 30% 10%)");
  context.fillStyle = sky;
  context.fillRect(0, 0, 960, 540);
  context.fillStyle = "rgba(0,0,0,.55)";
  context.beginPath();
  context.ellipse(300 + position * 300, 330, 160, 190, 0, 0, Math.PI * 2);
  context.fill();
  context.font = "bold 40px 'Microsoft YaHei', sans-serif";
  context.textAlign = "center";
  context.lineWidth = 6;
  context.strokeStyle = "#000";
  context.fillStyle = "#fff";
  const caption = position < 0.5 ? "今天我教大家做一道简单的面条" : "我们明天再来吧";
  context.strokeText(caption, 480, 470);
  context.fillText(caption, 480, 470);
  if (vertical) {
    const portrait = document.createElement("canvas");
    portrait.width = 360; portrait.height = 640;
    portrait.getContext("2d")!.drawImage(canvas, 328, 0, 304, 540, 0, 0, 360, 640);
    const blob = await new Promise<Blob | null>(resolve => portrait.toBlob(resolve, "image/jpeg", 0.85));
    return blob!.arrayBuffer();
  }
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  return blob!.arrayBuffer();
}

function previewTone(durationMs: number, source: boolean): ArrayBuffer {
  const rate = 24000, samples = Math.min(rate * 2, Math.max(1, Math.round(rate * durationMs / 1000)));
  const buffer = new ArrayBuffer(44 + samples * 2), view = new DataView(buffer);
  const text = (offset: number, value: string) => [...value].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); text(8, "WAVEfmt "); view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); view.setUint16(22, 1, true); view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, samples * 2, true);
  for (let i = 0; i < samples; i++) view.setInt16(44 + i * 2, Math.sin(i * 2 * Math.PI * (source ? 330 : 440) / rate) * 8000, true);
  return buffer;
}

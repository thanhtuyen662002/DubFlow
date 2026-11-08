import type { JobStatus } from "../features/job_status/status.ts";
import { parseSourceCredit, type SourceCredit } from "../features/credit/model.ts";

/**
 * Browser-only stand-in for the Tauri host so the UI can be designed with
 * `npm run dev`. It simulates the supervisor status file; it never runs media.
 */
type Running = { startedAt: number; dubbing: boolean; cancelled: boolean };

const running = new Map<string, Running>();
/** Tách phụ đề in the browser preview: when it started, per video, and its language. */
const previewExtracts = new Map<string, { at: number; language: string; cancelled: boolean }>();
const previewSplits = new Map<string, number>();
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
/** Phụ đề "Áp dụng": what each line's voice speaks where it differs from the subtitle, and the look. */
let savedVoice: Record<string, string> = {};
let savedSubtitleStyle: unknown = null;
// Che: a new job's cover.json (edge stretch) and the caption scan's regions:
// one line at slightly different widths through the video, and a short upper line.
let savedCover: unknown = { style: { mode: "stretch", direction: "top", strip_px: 4, color: "#000000", block: 24, feather: 0 }, regions: [], disabled_auto: [], fixed: null };
const previewCaptions = [
  ...Array.from({ length: 37 }, (_, index) => ({ x: 0.22 + (index % 4) * 0.01, y: 0.84, w: 0.56 - (index % 4) * 0.02, h: 0.085, start_ms: 4000 * index + 1200, end_ms: 4000 * index + 3600 })),
  ...[52_000, 71_000, 109_000].map((start) => ({ x: 0.3, y: 0.74, w: 0.4, h: 0.07, start_ms: start, end_ms: start + 2500 })),
].map((region, index) => ({ ...region, index }));
const linkFetches = new Map<string, { url: string; title: string | null; startedAt: number }>();
let douyinSession = false;
/** The discovery page "open" in the panel, or null. */
let previewDiscoveryUrl: string | null = null;

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
type PreviewDocuProject = { dir: string; created: number; spec: Record<string, unknown>; contexts: Array<Record<string, unknown>>; script: Record<string, unknown> | null };
const PREVIEW_DOCU_DIR = "D:\\Videos\\DubFlow Output\\Phim tai lieu - Three Gorges\\run-00000000d049";
const previewDocuProject: PreviewDocuProject = {
  dir: PREVIEW_DOCU_DIR, created: 1_793_500_200,
  spec: { schema_version: 1, title: "Three Gorges Dam", topic: "Engineering scale and community impact", target_minutes: 11,
    cinematic_framing_preset: "none", burn_end_credits: false, sources: [
      { id: "src_01", title: "Building the Three Gorges Dam", video: "D:\\Documentary\\three-gorges-build.mp4", transcript: "D:\\Documentary\\three-gorges-build.srt", origin_url: null, credit: null },
      { id: "src_02", title: "Inside the world's largest dam", video: "D:\\Documentary\\three-gorges-tour.mp4", transcript: "D:\\Documentary\\three-gorges-tour.srt", origin_url: "https://example.org/dam", credit: { url: "https://example.org/dam", creator: "Engineering Archive" } },
    ] },
  contexts: [
    { schema_version: 1, source_id: "src_01", title: "Building the Three Gorges Dam", video_path: "D:\\Documentary\\three-gorges-build.mp4", duration: 945.5,
      segments: [
        { seg_id: "src_01_seg_001", start: 12, end: 18.5, original_text: "工程建设期间，数百万人搬迁，三峡大坝改变了长江的水流。", extracted_keywords: ["三峡大坝"], quantities: [] },
        { seg_id: "src_01_seg_002", start: 41, end: 49.4, original_text: "水库总库容达到四百亿立方米。", extracted_keywords: ["水库"], quantities: [{ text: "400亿立方米", dimension: "volume", si: 40000000000, imperial: "1.41 trillion cubic feet", comparisons: ["about 11 times the volume of Lake Mead"] }] },
      ] },
    { schema_version: 1, source_id: "src_02", title: "Inside the world's largest dam", video_path: "D:\\Documentary\\three-gorges-tour.mp4", duration: 620.2,
      segments: [
        { seg_id: "src_02_seg_001", start: 3.2, end: 9.8, original_text: "发电站安装了三十二台水轮发电机组。", extracted_keywords: ["发电站"], quantities: [{ text: "32台机组", dimension: "count", si: 32, imperial: "32 generating units", comparisons: [] }] },
        { seg_id: "src_02_seg_002", start: 27.5, end: 35, original_text: "工程师必须平衡防洪、发电和航运需求。", extracted_keywords: ["工程师"], quantities: [] },
      ] },
  ],
  script: { project_title: "The Three Gorges Dam: A River Rewritten", target_Coordinates_3D: { lat: 30.8231, lng: 111.003, location_name: "Yichang, China" },
    review_notes: ["Một số con số cần người dùng xác nhận."], chapters: [
      { chapter_type: "HOOK", scenes: [{ scene_id: "sc_001", narration_text: "A wall across the Yangtze changed the scale of a river—and the lives built around it.", matched_source_refs: [{ source_id: "src_01", start: 12, end: 18.5 }], visual_search_tags: ["dam spillway", "Yangtze River"], on_screen_graphic: null, ambient_sfx_tag: "water_turbine_roar", recommended_bgm_mood: "cinematic_tension_drone", needs_review: true, review_notes: ["Cần xác minh con số 40 triệu người trong lời dẫn."], review_numbers: ["40 million"], acknowledged_numbers: [] }] },
      { chapter_type: "PROBLEM", scenes: [] }, { chapter_type: "ENGINEERING_SOLUTION", scenes: [] }, { chapter_type: "CRITICAL_EVALUATION", scenes: [] },
    ] },
};
const docuProjects = new Map<string, PreviewDocuProject>([[PREVIEW_DOCU_DIR, previewDocuProject]]);
/** Simulated narration per project: a run finishes after a few polls. */
const docuNarration = new Map<string, { polls: number; total: number; script: string; cancelled: boolean } | null>();
const previewDocuHash = (script: unknown): string => {
  const spoken = JSON.stringify(((script as { chapters?: Array<{ scenes?: Array<{ scene_id?: string; narration_text?: string }> }> })?.chapters ?? [])
    .map(chapter => (chapter.scenes ?? []).map(scene => [scene.scene_id, scene.narration_text])));
  let hash = 0;
  for (const char of spoken) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash.toString(16).padStart(64, "0");
};
/** Simulated rough cuts: a few polls, then the sample video stands in for documentary.mp4. */
const docuRenders = new Map<string, { polls: number; cancelled: boolean }>();
/** Documentaries split into layers in Chỉnh sửa (Issue #525): rendered clean since. */
const docuSplit = new Set<string>();
function previewRender(dir: string): Record<string, unknown> {
  const run = docuRenders.get(dir);
  const overlays = docuSplit.has(dir) ? { graphics: false, captions: false, music: "none" } : { graphics: true, captions: false, music: "generated" };
  // The sample film was rendered with its number cards and music before the preview started.
  if (!run && dir === PREVIEW_DOCU_DIR) return { video: `${dir}\\documentary.mp4`, status: { state: "COMPLETED", done: 6, total: 6, message: "Đã dựng bản nháp", overlays } };
  if (!run) return { video: null, status: null };
  if (run.cancelled) return { video: null, status: { state: "CANCELLED", done: 0, total: 6, message: "Đã hủy dựng bản nháp" } };
  run.polls += 1;
  if (run.polls < 4) return { video: null, status: { state: "RUNNING", done: run.polls, total: 6, message: `Đang dựng lát cắt ${run.polls}/5` } };
  return { video: "D:\\Videos\\DubFlow Output\\Phim tai lieu - Three Gorges\\documentary.mp4", status: { state: "COMPLETED", done: 6, total: 6, message: "Đã dựng bản nháp", overlays } };
}
/** Simulated visual plans: three polls, then slices alternating the two sources across each scene's slot. */
const docuPlans = new Map<string, { polls: number; cancelled: boolean }>();
/** Che › "Che có sẵn trong phim" (Issue #520): bands and logos set by hand, per project and source. */
const docuCovers = new Map<string, Map<string, { band: [number, number] | null; logos: number[][] }>>();
function previewCoverSources(dir: string): Array<Record<string, unknown>> {
  const project = docuProjects.get(dir);
  const edits = docuCovers.get(dir);
  return ((project?.spec.sources ?? []) as Array<{ id: string }>).map((source, index) => {
    const edit = edits?.get(source.id);
    const band = edit ? edit.band : index === 0 ? [0.78, 0.97] : null;
    return { id: source.id, duration: 60, framing: { band: band ? { top: band[0], bottom: band[1], share: 0.9 } : null, clear: !band,
      zoom: band ? 1.14 : 1, shift: 0, letterbox: 0, logos: edit ? edit.logos : index === 0 ? [[0.03, 0.04, 0.2, 0.09]] : [] } };
  });
}
/** The sample film's cuts (Issue #630): scenes of about 20 s, cuts of 2–5 s, a map in the second scene; edited by docu_slices. */
type SampleScene = { scene_id: string; start: number; end: number; warnings: string[]; slices: Array<Record<string, unknown>> };
let sampleScenes: SampleScene[] | null = null;
function previewSampleScenes(): SampleScene[] {
  if (sampleScenes) return sampleScenes;
  const scenes: SampleScene[] = [];
  let source = 0;
  for (let start = 0, number = 1; start < 640.4 - 1; number += 1) {
    const end = Math.min(640.4, start + 18 + (number % 4) * 2);
    const slices: Array<Record<string, unknown>> = [];
    for (let at = start, step = 0; at < end - 0.05; step += 1) {
      const duration = Math.min(2 + ((number + step) % 4), end - at), id = `src_0${source % 2 + 1}`, from = (source * 7) % 50;
      slices.push({ source_id: id, start: from, end: from + duration, at: Math.round(at * 1000) / 1000, duration, tier: 0,
        ...(number === 2 && step === 1 ? { kind: "map", ref: "g1" } : {}) });
      at += duration; source += 1;
    }
    scenes.push({ scene_id: `sc_${String(number).padStart(3, "0")}`, start, end, warnings: [], slices });
    start = end;
  }
  sampleScenes = scenes;
  return scenes;
}
function previewVisual(dir: string): Record<string, unknown> {
  const run = docuPlans.get(dir);
  // The sample film was planned and rendered before the preview started.
  if (!run && dir === PREVIEW_DOCU_DIR) return { status: null, plan: { script_sha256: "", duration: 640.4, scenes: previewSampleScenes(), warnings: [], sources: previewCoverSources(dir) } };
  if (!run) return { plan: null, status: null };
  if (run.cancelled) return { plan: null, status: { state: "CANCELLED", done: 0, total: 5, message: "Đã hủy lập kế hoạch hình ảnh" } };
  run.polls += 1;
  if (run.polls < 3) return { plan: null, status: { state: "RUNNING", done: run.polls * 2, total: 5, message: run.polls === 1 ? "Đang tách cảnh src_01…" : "Đang dò phụ đề gốc của src_02…" } };
  const narration = previewNarration(dir) as { timeline: { duration: number; scenes: Array<{ scene_id: string; start: number; slot_end: number }> } | null };
  const project = docuProjects.get(dir)!;
  let source = 0;
  const scenes = (narration.timeline?.scenes ?? []).map((scene, index) => {
    const slices = [];
    for (let at = index ? scene.start : 0; at < scene.slot_end - 0.05;) {
      const duration = Math.min(4, scene.slot_end - at), id = `src_0${source % 2 + 1}`, start = 12 + at * 2;
      slices.push({ source_id: id, start, end: start + duration, at, duration, tier: index === 1 ? 1 : 0 });
      at += duration; source += 1;
    }
    return { scene_id: scene.scene_id, start: index ? scene.start : 0, end: scene.slot_end, slices,
      warnings: index === 1 ? ["Một số lát cắt lấy ngay quanh đoạn nguồn kịch bản gợi ý"] : [] };
  });
  return { status: { state: "COMPLETED", done: 5, total: 5, message: "Đã lập kế hoạch" },
    plan: { script_sha256: String(project.script?.script_sha256 ?? ""), duration: narration.timeline?.duration ?? 0, scenes, sources: previewCoverSources(dir),
      warnings: ["Phụ đề gốc của src_01 vẫn lộ một phần sau khi phóng 114%; nên chọn khung Viền đen điện ảnh 2,35:1 cho dự án"] } };
}
function previewNarration(dir: string): Record<string, unknown> {
  const run = docuNarration.get(dir);
  if (!run) return { audio: null, timeline: null, status: null };
  if (run.cancelled) return { audio: null, timeline: null, status: { state: "CANCELLED", done: 0, total: run.total, message: "Đã hủy tạo lời đọc" } };
  run.polls += 1;
  const done = Math.min(run.total, run.polls * 2);
  if (done < run.total) return { audio: null, timeline: null, status: { state: "RUNNING", done, total: run.total, message: `Đang đọc câu ${done}/${run.total}` } };
  const project = docuProjects.get(dir)!;
  const scenes = ((project.script?.chapters ?? []) as Array<{ chapter_type: string; scenes: Array<{ scene_id: string; narration_text: string }> }>)
    .flatMap(chapter => chapter.scenes.map(scene => ({ ...scene, chapter_type: chapter.chapter_type })));
  let at = 0;
  const timed = scenes.map(scene => {
    const seconds = Math.max(2, scene.narration_text.split(/\s+/).length * 0.4), start = at;
    at += seconds + 0.8;
    return { scene_id: scene.scene_id, chapter_type: scene.chapter_type, start, end: start + seconds, slot_end: at, exact_duration_ms: Math.round(seconds * 1000) };
  });
  return { audio: "D:\\Documentary\\narration.wav", status: { state: "COMPLETED", done: run.total, total: run.total, message: "Đã tạo lời đọc" },
    timeline: { script_sha256: run.script, voice_id: "am_michael", speed: 1, duration: at, scenes: timed } };
}
const shorts = new Map<string, { started: number; cancelled: boolean; items: Array<{ title: string }> }>();
const reframes = new Map<string, { started: number; cancelled: boolean }>();
/** Edit drafts and exports per finished video (Cắt ghép, Chèn). */
const editProjects = new Map<string, unknown>();
/** Tách giọng in the browser preview: when each video's started. */
const previewSeparations = new Map<string, number>();
type PreviewSegment = { media?: number; start: number; end: number; speed: number; volume: number };
type PreviewExport = {
  path: string; srt_path: string; created: number; duration: number; quality: string; segments: PreviewSegment[];
  media?: string[]; transitions?: Array<{ after: number; type: string; duration: number }>; overlays?: unknown[];
  audio?: unknown[]; main_volume?: number; texts?: unknown[];
};

/** Finished videos the browser preview offers in "Chèn › Từ DubFlow" (it has no real queue jobs to insert). */
export const PREVIEW_EDIT_LIBRARY = [
  { id: "preview-intro", title: "Mở đầu kênh Phim Hay", outputPath: "D:\\Videos\\DubFlow Output\\Mo dau kenh - vi\\run-1\\final_vi.mp4" },
  { id: "preview-trailer", title: "Trailer phim cổ trang tập 2", outputPath: "D:\\Videos\\DubFlow Output\\Trailer co trang - vi\\run-1\\final_vi.mp4" },
];
const editExports = new Map<string, PreviewExport[]>([[PREVIEW_EDIT_LIBRARY[1].outputPath, [{
  path: "D:\\Videos\\Trailer co trang (sửa 1).mp4", srt_path: "D:\\Videos\\Trailer co trang (sửa 1).srt", created: 1_790_000_000, duration: 42.5,
  quality: "source", segments: [{ start: 0, end: 42.5, speed: 1, volume: 1 }],
}]]]);

/** Facts of a local video for "Chèn": portrait clips and a silent one, so every case can be designed. */
/** A speech-like waveform: phrases of varying loudness with short pauses, 50 levels a second for 15 minutes. */
function previewPeaks(path: string): ArrayBuffer {
  let seed = 7;
  for (const char of path) seed = (seed * 31 + char.charCodeAt(0)) >>> 0;
  const random = (): number => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  const levels = new Uint8Array(50 * 900);
  let phrase = 0, loud = 0;
  for (let index = 0; index < levels.length; index++) {
    if (phrase <= 0) {
      phrase = 20 + Math.floor(random() * 120);
      loud = random() < 0.18 ? 0 : 140 + random() * 100;
    }
    phrase -= 1;
    levels[index] = loud ? Math.max(0, Math.min(255, loud - random() * 70)) : random() * 30;
  }
  return levels.buffer;
}

function previewMediaInfo(path: string) {
  const name = path.split(/[\\/]/).pop() ?? path;
  let hash = 0;
  for (const char of path) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  const portrait = /doc|dọc|douyin|short/i.test(path);
  const silent = /khong-tieng|im-lang|silent/i.test(path);
  // A picture (Issue #521): an hour long, no sound.
  if (/\.(png|jpe?g|webp|bmp)$/i.test(path)) {
    return { path, title: name.replace(/\.[^.]*$/, ""), duration: 3600, width: 512, height: 512, fps: 30, has_audio: false, size_bytes: 40_000 };
  }
  return {
    path, title: name.replace(/\.[^.]*$/, ""), duration: /final_vi/i.test(name) ? 60 + hash % 120 : 8 + hash % 50,
    width: portrait ? 1080 : 1280, height: portrait ? 1920 : 720, fps: hash % 2 ? 30 : 25, has_audio: !silent, size_bytes: 9_000_000 + hash % 40_000_000,
  };
}
// Âm thanh (Issue #420): the sound-effect pack "downloads" in about three seconds.
const SFX_PACK_BYTES = 2_194_688;
let sfxDownloadStarted = 0;
const PREVIEW_AUDIO_FOLDER = "D:\\Âm thanh\\Hiệu ứng của tôi";
const PREVIEW_AUDIO_FILES = ["nhac-nen-vui-tuoi.mp3", "tieng-cuoi-khan-gia.wav", "trong-don-dap.m4a", "chuong-bao-thuc.ogg", "nhac-kich-tinh-ngan.mp3"];
function previewSfxStatus() {
  const elapsed = sfxDownloadStarted ? Date.now() - sfxDownloadStarted : -1;
  const installed = elapsed >= 3500;
  const state = elapsed < 0 || installed ? "idle" : elapsed < 3000 ? "downloading" : "installing";
  return { installed, version: installed ? "v1" : "", count: installed ? 199 : 0, size_bytes: SFX_PACK_BYTES, state,
    received: state === "downloading" ? Math.floor(SFX_PACK_BYTES * elapsed / 3000) : installed ? SFX_PACK_BYTES : 0, error: null };
}
function previewAudioSeconds(path: string): number {
  let hash = 0;
  for (const char of path) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return 1.5 + (hash % 900) / 10;
}
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
/** Phiên bản và mẫu saved in the browser preview (kept for the page's life). */
let previewVersions: Array<{ id: string; name: string; saved_at: number; summary: string | null; project: unknown }> = [];

/** Project folders deleted in the preview (Phim tài liệu, Tổng hợp, Bán hàng). */
const deletedRuns = new Set<string>();

export async function previewInvoke<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  await new Promise((resolve) => setTimeout(resolve, 120));
  switch (command) {
    case "project_delete": {
      const dir = String(args.dir);
      deletedRuns.add(dir); docuProjects.delete(dir); productRuns.delete(dir); compilations.delete(dir);
      return undefined as T;
    }
    case "docu_runs":
      return [...docuProjects.values()].sort((a, b) => b.created - a.created).map(project => ({ dir: project.dir, created: project.created,
        spec: project.spec, script: Boolean(project.script) })) as T;
    case "docu_open": {
      const project = docuProjects.get(String(args.dir));
      if (!project) throw new Error("Không tìm thấy dự án phim tài liệu");
      return JSON.parse(JSON.stringify({ dir: project.dir, spec: project.spec, contexts: project.contexts, script: project.script, narration: previewNarration(project.dir), visual: previewVisual(project.dir), render: previewRender(project.dir) })) as T;
    }
    case "docu_create": {
      const request = args.request as { title: string; topic: string; targetMinutes: number; cinematicFramingPreset: string; burnEndCredits: boolean; sources: Array<Record<string, string>> };
      const dir = `D:\\Videos\\DubFlow Output\\Phim tai lieu - ${request.title.replace(/[<>:"/\\|?*]/g, " ").slice(0, 40)}\\run-${(docuProjects.size + 1).toString(16).padStart(12, "0")}`;
      const sources = request.sources.map((source, index) => ({ id: `src_${String(index + 1).padStart(2, "0")}`, title: source.title,
        video: source.video ?? source.outputPath ?? `D:\\Documentary\\source-${index + 1}.mp4`, transcript: source.subtitles ?? `D:\\Documentary\\source-${index + 1}.srt`, origin_url: null, credit: null }));
      const project: PreviewDocuProject = { dir, created: Math.floor(Date.now() / 1000), spec: { schema_version: 1, title: request.title, topic: request.topic,
        target_minutes: request.targetMinutes, cinematic_framing_preset: request.cinematicFramingPreset, burn_end_credits: request.burnEndCredits, sources },
        contexts: sources.map(source => ({ schema_version: 1, source_id: source.id, title: source.title, video_path: source.video, duration: 60,
          segments: [{ seg_id: `${source.id}_seg_001`, start: 0, end: 12, original_text: "工程师必须解决复杂的施工和环境问题。", extracted_keywords: ["工程"], quantities: [] }] })), script: null };
      docuProjects.set(dir, project);
      return { dir, sources: project.contexts.map(context => ({ id: context.source_id, duration: context.duration, segments: (context.segments as unknown[]).length })) } as T;
    }
    case "docu_check_script": {
      const project = docuProjects.get(String(args.dir));
      if (!project) throw new Error("Không tìm thấy dự án phim tài liệu");
      const script = JSON.parse(JSON.stringify(args.script)) as Record<string, unknown>;
      const chapters = Array.isArray(script.chapters) ? script.chapters as Array<Record<string, unknown>> : [];
      for (const chapter of chapters) for (const scene of Array.isArray(chapter.scenes) ? chapter.scenes as Array<Record<string, unknown>> : []) {
        const numbers = Array.isArray(scene.review_numbers) ? scene.review_numbers.map(String) : [];
        const acknowledged = Array.isArray(scene.acknowledged_numbers) ? scene.acknowledged_numbers.map(String) : [];
        scene.needs_review = numbers.some(number => !acknowledged.includes(number));
        if (!scene.needs_review) scene.review_notes = [];
      }
      script.script_sha256 = previewDocuHash(script);
      project.script = script;
      return script as T;
    }
    case "docu_voices": return { defaultVoiceId: "am_michael", voices: [
      { voiceId: "am_michael", name: "Michael", gender: "Nam", accent: "Mỹ", description: "trầm ấm, hợp phim tài liệu" },
      { voiceId: "af_heart", name: "Heart", gender: "Nữ", accent: "Mỹ", description: "tự nhiên, ấm" },
      { voiceId: "bm_george", name: "George", gender: "Nam", accent: "Anh", description: "giọng Anh trang trọng" }] } as T;
    case "docu_narrate": {
      const project = docuProjects.get(String(args.dir));
      if (!project?.script) throw new Error("Hãy viết và lưu kịch bản trước khi tạo lời đọc");
      docuNarration.set(project.dir, { polls: 0, total: 8, script: String(project.script.script_sha256 ?? ""), cancelled: false });
      return undefined as T;
    }
    case "docu_narration_status": return previewNarration(String(args.dir)) as T;
    case "docu_plan": docuPlans.set(String(args.dir), { polls: 0, cancelled: false }); return undefined as T;
    case "docu_plan_status": return previewVisual(String(args.dir)) as T;
    case "docu_cover": {
      const dir = String(args.dir);
      const edits = docuCovers.get(dir) ?? new Map();
      edits.set(String(args.source), { band: (args.band as [number, number] | null) ?? null, logos: (args.logos as number[][]) ?? [] });
      docuCovers.set(dir, edits);
      return { state: "COMPLETED", plan: true, warnings: [] } as T;
    }
    case "docu_slices": {
      const scenes = previewSampleScenes();
      for (const edited of args.scenes as Array<{ scene_id: string; slices: Array<{ source_id: string; start: number; at: number; cover: unknown }> }>) {
        const scene = scenes.find((item) => item.scene_id === edited.scene_id);
        if (!scene) throw new Error(`Không có cảnh ${edited.scene_id} trong kế hoạch hình ảnh`);
        scene.slices = edited.slices.map((piece, index) => {
          const end = edited.slices[index + 1]?.at ?? scene.end;
          return { source_id: piece.source_id, start: piece.start, end: piece.start + end - piece.at, at: piece.at, duration: end - piece.at, tier: -1,
            ...(piece.cover ? { cover: piece.cover } : {}) };
        });
      }
      return { state: "COMPLETED", scenes: (args.scenes as unknown[]).length, slices: scenes.reduce((sum, scene) => sum + scene.slices.length, 0) } as T;
    }
    case "docu_render": docuRenders.set(String(args.dir), { polls: 0, cancelled: false }); return undefined as T;
    case "docu_layers": {
      const dir = String(args.dir);
      docuSplit.add(dir);
      docuRenders.set(dir, { polls: 0, cancelled: false });
      return { graphics: true, captions: false, music: "generated", bed: `${dir}\\music_bed.wav` } as T;
    }
    case "docu_render_status": return previewRender(String(args.dir)) as T;
    case "docu_render_options": {
      // A cancelled split puts the cards and music back.
      if (args.graphics || args.music !== "none") docuSplit.delete(String(args.dir));
      return { graphics: !!args.graphics, captions: !!args.captions, music: String(args.music), music_path: args.musicPath ?? null, sfx: args.sfx !== false } as T;
    }
    case "docu_capcut": return { state: "COMPLETED", folder: "C:\\Users\\me\\AppData\\Local\\CapCut\\User Data\\Projects\\com.lveditor.draft\\DubFlow - Three Gorges",
      name: "DubFlow - Three Gorges", clips: 18, duration: 64.1, captions: "D:\\Videos\\captions_en.srt" } as T;
    case "docu_launch_capcut": return undefined as T;
    case "docu_mcp_activity": {
      // A Claude session working over MCP: busy for 12 s, idle for 8 s.
      const project = [...docuProjects.values()][0];
      const now = Date.now(), busy = now % 20_000 < 12_000;
      const at = (ms: number) => new Date(now - ms).toISOString();
      return { schema_version: 1, updated: at(0),
        current: busy ? { tool: "render_rough_cut", label: "Dựng bản nháp", project: project?.dir ?? null, started: at(now % 20_000) } : null,
        recent: [
          { tool: "set_framing", label: "Đổi khung hình: letterbox_235", project: project?.dir ?? null, started: at(60_000), finished: at(59_000), state: "done", message: "Dựng lại bản nháp (render_rough_cut) để thấy khung mới" },
          { tool: "replace_timeline_clip", label: "Thay lát cắt sc_022 #2 bằng src_04 từ 112.5 s", project: project?.dir ?? null, started: at(90_000), finished: at(89_500), state: "done", message: "Dựng lại bản nháp (render_rough_cut) để thấy thay đổi" },
          { tool: "discover_videos", label: "Tìm video trên bilibili: 湖北养猪大楼", project: null, started: at(300_000), finished: at(297_000), state: "failed", message: "bilibili: Platform requires verification in its browser." },
        ] } as T;
    }
    case "docu_mcp_connection": return { python: "D:\\Tools\\DubFlow\\versions\\1.1.47\\runtime\\python.exe", app: "D:\\Tools\\DubFlow\\versions\\1.1.47\\app",
      claudeCode: "claude mcp add dubflow --scope user -e PYTHONPATH=\"D:\\Tools\\DubFlow\\versions\\1.1.47\\app\" -- \"D:\\Tools\\DubFlow\\versions\\1.1.47\\runtime\\python.exe\" -B -m engine.dubflow.mcp",
      claudeDesktop: JSON.stringify({ mcpServers: { dubflow: { command: "D:\\Tools\\DubFlow\\versions\\1.1.47\\runtime\\python.exe", args: ["-B", "-m", "engine.dubflow.mcp"],
        env: { PYTHONPATH: "D:\\Tools\\DubFlow\\versions\\1.1.47\\app" } } } }, null, 2) } as T;
    case "docu_render_cancel": { const run = docuRenders.get(String(args.dir)); if (run) run.cancelled = true; return undefined as T; }
    case "docu_plan_cancel": { const run = docuPlans.get(String(args.dir)); if (run) run.cancelled = true; return undefined as T; }
    case "docu_narration_cancel": { const run = docuNarration.get(String(args.dir)); if (run) run.cancelled = true; return undefined as T; }
    case "docu_pick_file": return (args.kind === "video" ? "D:\\Documentary\\local-source.mp4" : "D:\\Documentary\\local-source.srt") as T;
    case "edit_project_load": {
      const path = String(args.outputPath);
      const product = /[\\/]video-(\d+)\.mp4$/i.exec(path);
      if (product) {
        // A re-made video (Mẫu) is a little longer when its script changed.
        const run = productRuns.get(path.replace(/[\\/]video-\d+\.mp4$/i, ""));
        return { duration: run?.durationMs ? run.durationMs / 1000 : 31.4, width: 1080, height: 1920, fps: 30, size_bytes: 9_800_000, has_audio: true,
          project: editProjects.get(path) ?? null, kind: "product", reviewable: false, variant: Number(product[1]),
          cues: [{ start_ms: 300, end_ms: 2600, text: "Nồi chiên không dầu 5 lít" }, { start_ms: 2850, end_ms: 6200, text: "chiên giòn mà không cần dầu" }] } as T;
      }
      if (/[\\/]compilation\.mp4$/i.test(path)) {
        return { duration: 202.5, width: 1920, height: 1080, fps: 30, size_bytes: 88_000_000, has_audio: true, project: editProjects.get(path) ?? null,
          kind: "compilation", reviewable: false, variant: null, cues: [] } as T;
      }
      if (/[\\/]documentary\.mp4$/i.test(path)) {
        return { duration: 640.4, width: 1920, height: 1080, fps: 30, size_bytes: 412_000_000, has_audio: true, project: editProjects.get(path) ?? null,
          kind: "docu", reviewable: false, variant: null, cues: [] } as T;
      }
      if (!/[\\/]final_vi\.mp4$/i.test(path)) {
        // Chỉnh sửa › Mở video trên máy: any video.
        return { duration: 96.8, width: 1920, height: 1080, fps: 30, size_bytes: 61_000_000, has_audio: true, project: editProjects.get(path) ?? null,
          kind: "file", reviewable: false, variant: null, cues: [] } as T;
      }
      return {
        duration: 154.2, width: 1024, height: 576, fps: 25, size_bytes: 46_000_000, has_audio: true,
        project: editProjects.get(path) ?? null, kind: "job", reviewable: true, variant: null,
        cues: reviewCues.map(({ cue_id, start_ms, end_ms, machine_text }) => ({ start_ms, end_ms, text: savedOverrides[cue_id] ?? machine_text })),
      } as T;
    }
    case "edit_stems": {
      // A dubbing job has its Vietnamese voice and original sound; Tách giọng adds the accompaniment.
      const path = String(args.outputPath);
      const dir = path.replace(/[\\/][^\\/]*$/, "");
      const run = previewSeparations.get(path);
      const progress = run ? Math.min(100, (Date.now() - run) / 60) : 0;
      const done = run !== undefined && progress >= 100;
      const job = /[\\/]final_vi\.mp4$/i.test(path);
      const splitAt = previewSplits.get(path);
      const splitProgress = splitAt === undefined ? 0 : Math.min(100, (Date.now() - splitAt) / 40);
      const splitDone = splitProgress >= 100;
      return {
        voice: job ? `${dir}\\editable\\dialogue_stem.wav` : null,
        source: job ? `${dir}\\editable\\source_audio.wav` : done ? `${dir}\\.dubflow-work\\edit\\stems\\source.wav` : null,
        // A job's own accompaniment came from the older model until Tách giọng runs again.
        background: done ? `${dir}\\.dubflow-work\\edit\\stems\\background.wav` : job ? `${dir}\\editable\\background_stem.wav` : null,
        separation: run === undefined ? null : done ? { state: "COMPLETED", progress: 100, message: "Đã tách giọng, nhạc và tiếng động." }
          : { state: "RUNNING", progress, message: progress < 8 ? "Đang tải bộ tách giọng lần đầu: 31/67 MB" : "Đang tách giọng gốc khỏi nhạc nền…" },
        outdated: job && !done,
        // Tách giọng also splits the accompaniment; a job's older one splits on request (edit_split).
        ...(done || (splitAt !== undefined && splitDone) ? { music: `${dir}\\.dubflow-work\\edit\\stems\\music.wav`, sfx: `${dir}\\.dubflow-work\\edit\\stems\\sfx.wav` } : {}),
        split: splitAt === undefined ? null : splitDone ? { state: "COMPLETED", progress: 100, message: "Đã tách nhạc và tiếng động." }
          : { state: "RUNNING", progress: splitProgress, message: "Đang tách nhạc và tiếng động…" },
      } as T;
    }
    case "edit_subs_extract": previewExtracts.set(String(args.outputPath), { at: Date.now(), language: String(args.language ?? "zh"), cancelled: false }); return undefined as T;
    case "edit_subs_extract_cancel": {
      const run = previewExtracts.get(String(args.outputPath));
      if (run) run.cancelled = true;
      return undefined as T;
    }
    case "edit_subs_extract_status": {
      const run = previewExtracts.get(String(args.outputPath));
      if (!run) return { state: "NONE" } as T;
      if (run.cancelled) return { state: "CANCELLED", progress: 0, message: "Đã hủy tách phụ đề." } as T;
      const progress = Math.min(100, (Date.now() - run.at) / 40);
      if (progress < 100) return { state: "RUNNING", progress, message: progress < 60 ? "Đang nghe lời thoại…" : "Đang dịch theo ngữ cảnh bằng Claude…" } as T;
      const zh = ["大家好，欢迎来到我的频道", "今天我们去看一座养猪的高楼", "这里一共有二十六层", "每一层都住着上千头猪"];
      const en = ["Hello everyone, welcome to my channel", "Today we visit a pig skyscraper", "It has twenty-six floors", "Each floor houses a thousand pigs"];
      const vi = ["Chào mọi người, chào mừng đến với kênh của mình", "Hôm nay chúng ta đi xem tòa nhà nuôi lợn", "Ở đây có tất cả hai mươi sáu tầng", "Mỗi tầng nuôi hàng nghìn con lợn"];
      const at = (index: number) => ({ start_ms: 1000 + index * 3000, end_ms: 3600 + index * 3000 });
      return { state: "COMPLETED", progress: 100, message: `Đã tách 4 câu ${run.language === "en" ? "tiếng Anh" : "tiếng Trung"}, 4 câu dịch bằng Claude.`, language: run.language,
        source: (run.language === "en" ? en : zh).map((text, index) => ({ ...at(index), text })), vi: vi.map((text, index) => ({ ...at(index), text })), warnings: [] } as T;
    }
    case "edit_split": previewSplits.set(String(args.outputPath), Date.now()); return undefined as T;
    case "edit_separate": previewSeparations.set(String(args.outputPath), Date.now()); return undefined as T;
    case "edit_separate_cancel": previewSeparations.delete(String(args.outputPath)); return undefined as T;
    case "edit_file_sizes": return (args.paths as string[]).map(() => 46_000_000) as T;
    case "edit_library": {
      // The other pages' finished videos, with made-up dates so the list has its groups.
      const now = Math.floor(Date.now() / 1000);
      const products = previewProductRuns().flatMap((run, runIndex) => (run.status?.videos ?? []).map((video: { index: number }) => ({
        path: `${run.dir}\\video-${video.index}.mp4`, kind: "product", title: `${run.name} · video ${video.index}`, modified: now - 3_600 * (2 + runIndex) - video.index * 60, size_bytes: 9_800_000 })));
      const compilations = previewCompilationRuns().filter((run) => run.video).map((run, index) => ({
        path: run.video!, kind: "compilation", title: run.spec.title, modified: now - 86_400 * (1 + index * 3), size_bytes: 88_000_000 }));
      const films = [...docuProjects.values()].map((project, index) => ({
        path: `${project.dir}\\documentary.mp4`, kind: "docu", title: project.spec.title, modified: now - 86_400 * (9 + index), size_bytes: 412_000_000 }));
      return [...products, ...compilations, ...films] as T;
    }
    case "edit_project_save": editProjects.set(String(args.outputPath), JSON.parse(JSON.stringify(args.project))); return null as T;
    case "edit_export": {
      const request = args.request as { outputPath: string; folder: string; fileName: string; quality: string; segments: PreviewSegment[];
        media?: string[]; transitions?: Array<{ after: number; type: string; duration: number }>; overlays?: unknown[];
        audio?: unknown[]; mainVolume?: number; texts?: unknown[] };
      if (editRuns.get(request.outputPath) && previewEditStatus(request.outputPath).status?.state === "RUNNING") throw new Error("Video này đang được xuất");
      const taken = new Set((editExports.get(request.outputPath) ?? []).map((entry) => entry.path));
      const stem = request.fileName.replace(/\.mp4$/i, "").trim();
      let path = `${request.folder}/${stem}.mp4`;
      for (let number = 2; taken.has(path); number++) path = `${request.folder}/${stem} (${number}).mp4`;
      // Every transition but fade through black overlaps its neighbours.
      const duration = request.segments.reduce((total, segment) => total + (segment.end - segment.start) / segment.speed, 0)
        - (request.transitions ?? []).filter((joint) => joint.type !== "fade").reduce((total, joint) => total + joint.duration, 0);
      editRuns.set(request.outputPath, { started: Date.now(), cancelled: false, recorded: false,
        entry: { path, srt_path: path.replace(/\.mp4$/, ".srt"), created: Math.floor(Date.now() / 1000), duration, quality: request.quality,
          segments: request.segments, media: request.media ?? [], transitions: request.transitions ?? [], overlays: request.overlays ?? [],
          audio: request.audio ?? [], main_volume: request.mainVolume ?? 1, texts: request.texts ?? [] } });
      return { path } as T;
    }
    case "edit_subtitles_load": return {
      cues: reviewCues.map((cue) => {
        const text = savedOverrides[cue.cue_id] ?? cue.machine_text;
        return { ...cue, text, voice_text: savedVoice[cue.cue_id] ?? text, edited: cue.cue_id in savedOverrides };
      }),
      style: savedSubtitleStyle,
      // No asset protocol in a browser: the preview falls back to the final video with its note.
      source_path: "D:\\Videos\\Douyin\\phim-ngan-tap-1.mp4",
      mix_path: "D:\\Videos\\DubFlow Output\\preview\\editable\\final_mix.wav",
      dubbed: true,
      blur_regions: previewCaptions,
      cover: savedCover,
    } as T;
    case "edit_subtitles_apply": {
      const style = args.subtitleStyle as { font?: unknown } | null;
      if (!style || typeof style.font !== "string") throw new Error("Kiểu phụ đề không hợp lệ");
      savedOverrides = { ...(args.overrides as Record<string, string>) };
      savedVoice = { ...(args.voice as Record<string, string>) };
      savedSubtitleStyle = JSON.parse(JSON.stringify(style));
      const cover = args.cover as { style?: { mode?: unknown } } | null | undefined;
      if (cover && cover.style && typeof cover.style.mode !== "string") throw new Error("Kiểu che không hợp lệ");
      if (cover) savedCover = JSON.parse(JSON.stringify(cover));
      return "D:\\Videos\\DubFlow Output\\preview (sửa 1)" as T;
    }
    case "edit_status": return previewEditStatus(String(args.outputPath)) as T;
    case "edit_media_info": return previewMediaInfo(String(args.path)) as T;
    case "edit_audio_peaks": return previewPeaks(String(args.path)) as T;
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
    case "compilation_runs": return previewCompilationRuns() as T;
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
    case "ai_key_page": return null as T;
    case "release_info": return { version: "0.1.0", channel: "preview", backend: "mock" } as T;
    // Nghe thử: a short tone stands in for the voice; the first one takes a moment, like the real model.
    case "voice_sample": await new Promise((resolve) => setTimeout(resolve, 1200)); return `C:\\Users\\preview\\AppData\\Local\\DubFlow\\voice-samples\\${String(args.voiceId)}.wav` as T;
    case "voice_catalog": return { default_voice_id: voices[0].voice_id, voices } as T;
    case "pick_files": {
      picked += 1;
      return [`D:\\Videos\\Douyin\\phim-ngan-tap-${picked}.mp4`, `D:\\Videos\\Bilibili\\review-dien-thoai-${picked}.mkv`] as T;
    }
    case "pick_edit_files": {
      picked += 1;
      return [`D:\\Videos\\Douyin\\phim-ngan-tap-${picked}.mp4`, `D:\\Logo\\logo-kenh-${picked}.png`] as T;
    }
    case "edit_save_recording": return "D:\\Videos\\DubFlow Output\\preview\\thu-am\\giong-1.m4a" as T;
    case "edit_versions": return previewVersions.filter((item) => item.id.startsWith("v-")).map(({ project: _, ...item }) => item) as T;
    case "edit_templates": return previewVersions.filter((item) => item.id.startsWith("t-")).map(({ project: _, ...item }) => item) as T;
    case "edit_version_save": case "edit_template_save": {
      const id = `${command === "edit_version_save" ? "v" : "t"}-${Date.now()}`;
      const entry = { id, name: String(args.name), saved_at: Math.floor(Date.now() / 1000), summary: typeof args.summary === "string" ? args.summary : null,
        project: command === "edit_version_save" ? args.project : args.layers };
      previewVersions.unshift(entry);
      return { id, name: entry.name, saved_at: entry.saved_at } as T;
    }
    case "edit_version_load": case "edit_template_load": return (previewVersions.find((item) => item.id === args.id)?.project ?? null) as T;
    case "edit_version_delete": case "edit_template_delete": previewVersions = previewVersions.filter((item) => item.id !== args.id); return null as T;
    case "pick_lut": return "D:\\LUT\\Teal Orange Cinematic.cube" as T;
    // Bản xem nhẹ: the browser preview plays nothing heavy.
    case "edit_proxy": return { state: "none" } as T;
    case "edit_lut_frame": return await previewFrame(Number(args.time), false) as T;
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
    case "link_titled_output": return String(args.outputDir) as T;
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
    // The page itself is not shown in a browser: the panel's placeholder box stands in for it.
    case "open_discovery": previewDiscoveryUrl = previewDiscoveryPage(String(args.platform), String(args.kind), String(args.query)); return previewDiscoveryUrl as T;
    case "discovery_view_url": return previewDiscoveryUrl as T;
    case "discovery_view_close": previewDiscoveryUrl = null; console.info("[preview] discovery_view_close"); return null as T;
    case "discovery_view_show": case "discovery_view_bounds": case "discovery_view_hide": case "discovery_view_back": case "discovery_view_reload":
      if (previewDiscoveryUrl === null && command === "discovery_view_show") throw "Hãy mở trang khám phá trước";
      console.info(`[preview] ${command}`, args ?? "");
      return null as T;
    case "scroll_discovery": return null as T;
    case "translate_terms": {
      const terms = args.terms as string[];
      // Titles take a moment, like the local translator, so "Đang dịch…" shows.
      if (terms.some((term) => PREVIEW_GLOSS.has(term))) await new Promise((resolve) => setTimeout(resolve, 900));
      return { ok: true, items: terms.map((term) => PREVIEW_GLOSS.get(term) ?? `(nghĩa) ${term}`) } as T;
    }
    case "translate_query": {
      const text = String(args.text ?? "").trim();
      await new Promise((resolve) => setTimeout(resolve, 400));
      const known: Record<string, string> = { "nuôi lợn": "养猪", "tháp nuôi lợn": "养猪大楼", "ẩm thực": "美食", "tiên hiệp": "仙侠" };
      return { ok: true, text: known[text.toLowerCase()] ?? `中文 ${text}`, engine: "preview" } as T;
    }
    case "collect_discovery": return previewCollected(String(args.platform)) as T;
    case "review_clip": return previewTone(Number(args.endMs) - Number(args.startMs), Boolean(args.source)) as T;
    // No asset protocol in a browser: the player shows its placeholder.
    case "allow_media": return null as T;
    case "sfx_status": return previewSfxStatus() as T;
    case "sfx_install": if (!sfxDownloadStarted) sfxDownloadStarted = Date.now(); return previewSfxStatus() as T;
    case "sfx_path": return (previewSfxStatus().installed ? `C:\\Users\\preview\\AppData\\Local\\DubFlow\\sfx\\v1\\sounds\\${String(args.id)}.ogg` : null) as T;
    case "pick_audio_folder": return PREVIEW_AUDIO_FOLDER as T;
    case "user_audio_list": return PREVIEW_AUDIO_FILES.map((name, index) => ({
      path: `${String(args.folder)}\\${name}`, name, size_bytes: 180_000 + index * 731_000 })) as T;
    case "audio_info": {
      const path = String(args.path);
      return { path, title: (path.split(/[\\/]/).pop() ?? path).replace(/\.[^.]*$/, ""), duration: previewAudioSeconds(path), size_bytes: 900_000 } as T;
    }
    case "pick_audio_files": return [`${PREVIEW_AUDIO_FOLDER}\\tieng-vo-tay.mp3`] as T;
    case "expand_audio_paths": return (args.paths as string[]).filter((path) => /\.(mp3|wav|ogg|m4a|aac|flac)$/i.test(path)) as T;
    case "video_frame": return await previewFrame(Number(args.position), /final_vi\.mp4$/i.test(String(args.sourcePath))) as T;
    case "save_douyin_session": douyinSession = true; return 12 as T;
    case "review_cues": return reviewCues.map((cue) => ({ ...cue, text: savedOverrides[cue.cue_id] ?? cue.machine_text, edited: cue.cue_id in savedOverrides })) as T;
    case "save_review": savedOverrides = { ...(args.overrides as Record<string, string>) }; return "D:\Videos\DubFlow Output\preview" as T;
    case "pick_product_files": return (args.kind === "music" ? ["D:/Nhac/nen.mp3"] : args.kind === "background" ? ["D:/Anh/nen-doc-1080x1920.png"] : args.kind === "media" ? ["D:/Anh/hop.jpg", "D:/Anh/nhan.png", "D:/Clip/doi-tac.mp4"] : ["D:/Anh/hop.jpg", "D:/Anh/nhan.png"]) as T;
    case "product_image": {
      const background = previewBackgrounds.find((entry) => entry.path === String(args.path));
      return await (background ? previewBackgroundImage(background.width, background.height, background.hue) : previewFrame(String(args.path).length % 3 / 3)) as T;
    }
    case "product_backgrounds": return previewBackgrounds.map(({ hue: _hue, ...entry }) => entry) as T;
    case "product_background_add": {
      const id = "c".repeat(64);
      if (!previewBackgrounds.some((entry) => entry.id === id)) {
        previewBackgrounds.unshift({ id, extension: "png", name: "nen-doc-1080x1920.png", width: 1080, height: 1920, path: `C:\\Users\\preview\\AppData\\Local\\DubFlow\\backgrounds\\${id}.png`, hue: 330 });
      }
      const { hue: _hue, ...entry } = previewBackgrounds.find((item) => item.id === id)!;
      return entry as T;
    }
    case "product_background_delete": {
      const index = previewBackgrounds.findIndex((entry) => entry.id === String(args.id));
      if (index >= 0) previewBackgrounds.splice(index, 1);
      return null as T;
    }
    case "product_render": {
      const request = args.request as { variants: string[]; reuseAudioFrom?: string; name: string };
      const dir = `D:\\Videos\\DubFlow Output\\Ban hang - ${request.name || "san pham"}\\run-${productRuns.size.toString(16).padStart(12, "0")}`;
      const before = request.reuseAudioFrom ? previewSpec(request.reuseAudioFrom) : null;
      const reused = !!before && before.spec.variants.includes(request.variants[0]);
      productRuns.set(dir, { started: Date.now(), cancelled: 0, count: request.variants.length, reused, name: request.name,
        durationMs: reused ? 31_400 : 31_400 + 2_600 * Math.sign(request.variants[0].length - 60), variants: request.variants });
      return dir as T;
    }
    case "product_status": return previewProductStatus(String(args.outputDir)) as T;
    case "product_cancel": { const run = productRuns.get(String(args.outputDir)); if (run) run.cancelled = Date.now(); return null as T; }
    case "product_spec": {
      const path = String(args.outputPath);
      const run = path.replace(/[\\/]video-\d+\.mp4$/i, "");
      const variant = Number(/video-(\d+)\.mp4$/i.exec(path)?.[1] ?? 1);
      return { ...previewSpec(run), variant, run, duration_ms: 31_400, narration: true } as T;
    }
    case "product_runs": return previewProductRuns() as T;
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

/** Roughly the address the real host would open (see discovery.rs `page_url`). */
function previewDiscoveryPage(platform: string, kind: string, query: string): string {
  const text = query.trim();
  if (!text) throw "Hãy nhập từ khóa hoặc link kênh hợp lệ";
  if (kind === "creator") return text;
  const keyword = encodeURIComponent(text);
  if (platform === "bilibili") return `https://search.bilibili.com/video?keyword=${keyword}`;
  if (platform === "kuaishou") return `https://www.kuaishou.com/search/video?searchKey=${keyword}`;
  return `https://www.douyin.com/search/${keyword}?type=video`;
}

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

/** Stand-in for Windows Credential Manager. */
const previewKeys = new Map<string, string>();
/** Product runs: one video every 3 s (1.5 s when the narration is reused), cancellable. */
type PreviewProductRun = { started: number; cancelled: number; count: number; reused: boolean; name: string; durationMs: number; variants: string[] };
const PREVIEW_PRODUCT_RUN = "D:\\Videos\\DubFlow Output\\Ban hang - Nồi chiên không dầu 5L\\run-0a1b2c3d4e5f";
const productRuns = new Map<string, PreviewProductRun>([[PREVIEW_PRODUCT_RUN, {
  started: 0, cancelled: 0, count: 2, reused: false, name: "Nồi chiên không dầu 5L", durationMs: 31_400,
  variants: ["Nồi chiên không dầu 5 lít, chiên giòn mà không cần dầu. Hẹn giờ tới 60 phút, dễ rửa. Giá chỉ 299 nghìn, đặt ngay hôm nay!",
    "Bạn muốn món chiên giòn mà ít dầu mỡ? Nồi chiên 5 lít này là lựa chọn cho cả nhà."],
}]]);
function previewProductStatus(dir: string) {
  const run = productRuns.get(dir);
  if (!run) return null;
  const step = run.reused ? 1500 : 3000;
  const now = run.cancelled || Date.now();
  const done = Math.min(run.count, Math.floor((now - run.started) / step));
  const videos = Array.from({ length: done }, (_, index) => ({ index: index + 1, ok: true, path: `${dir}\\video-${index + 1}.mp4`, duration_ms: run.durationMs, reused_voice: run.reused }));
  if (run.cancelled && done < run.count) return { schema_version: 1, state: "CANCELLED", done, total: run.count, message: done ? `Đã hủy. Giữ lại ${done} video đã xong.` : "Đã hủy.", videos };
  return done < run.count
    ? { schema_version: 1, state: "RUNNING", done, total: run.count, message: run.reused ? `Dùng lại giọng đọc cũ, đang render video ${done + 1}/${run.count}…` : `Đang render video ${done + 1}/${run.count}…`, videos }
    : { schema_version: 1, state: "COMPLETED", done, total: run.count, message: `Đã tạo ${done}/${run.count} video`, videos };
}
function previewSpec(dir: string) {
  const run = productRuns.get(dir) ?? productRuns.get(PREVIEW_PRODUCT_RUN)!;
  return { spec: { schema_version: 1, name: run.name, price: "299.000đ", voice_id: voices[0].voice_id, variants: run.variants,
    visuals: ["D:\\Anh\\noi-chien.jpg", "D:\\Clip\\noi-chien-demo.mp4"], music: null, music_volume: 0.15,
    background: `C:\\Users\\preview\\AppData\\Local\\DubFlow\\backgrounds\\${"b".repeat(64)}.png`, template: "ban-tin", speed: 1,
    features: ["Dung tích 5 lít", "Hẹn giờ 60 phút", "Lòng nồi chống dính"], focus: { x: 0.3, y: 0.5 } } };
}
function previewProductRuns() {
  return [...productRuns.entries()].reverse().map(([dir, run]) => ({
    dir, name: run.name, template: "ban-tin", created: Math.floor((run.started || Date.now() - 86_400_000) / 1000), status: previewProductStatus(dir),
  }));
}
/** Earlier compilations in the output folders, and the ones started in the preview. */
function previewCompilationRuns() {
  const spec = (title: string) => ({ schema_version: 1, title, layout: "bang-thong-tin", voice_id: voices[0].voice_id, speed: 1.1, music: null, music_volume: 0.15,
    intro: "Chào mừng các bạn quay lại kênh.", outro: "Cảm ơn đã xem, hẹn gặp lại.",
    items: PREVIEW_EDIT_LIBRARY.map((item, index) => ({ video: item.outputPath, title: item.title, lead: index ? "" : "Mở màn bằng một đoạn rất cuốn.", score: 8 - index,
      pros: ["Hình đẹp"], cons: [], start_ms: null, end_ms: null })) });
  const started = [...compilations.entries()].reverse().map(([dir, run]) => {
    const done = Math.min(run.total, Math.floor((Date.now() - run.started) / 1000));
    const state = run.cancelled ? "CANCELLED" : done === run.total ? "COMPLETED" : "RUNNING";
    return { dir, created: Math.floor(run.started / 1000), spec: spec("Tổng hợp mới"),
      status: { schema_version: 1, state, done, total: run.total, message: state === "RUNNING" ? "Đang tạo video tổng hợp…" : state === "CANCELLED" ? "Đã hủy tổng hợp." : "Đã tạo video tổng hợp.",
        path: state === "COMPLETED" ? `${dir}/compilation.mp4` : null },
      video: state === "COMPLETED" ? `${dir}/compilation.mp4` : null };
  });
  const old = "D:\\Videos\\DubFlow Output\\Tong hop - Top 3 phim ngan\\run-00000000a1b2";
  const failed = "D:\\Videos\\DubFlow Output\\Tong hop - Phim co trang\\run-00000000c3d4";
  return [...started.filter(run => !deletedRuns.has(run.dir)), ...[
    { dir: old, created: 1_793_500_000, spec: spec("Top 3 phim ngắn đáng xem tuần này"), video: `${old}\\compilation.mp4`,
      status: { schema_version: 1, state: "COMPLETED", done: 7, total: 7, message: "Đã tạo video tổng hợp.", path: `${old}\\compilation.mp4`, duration_ms: 202_500 } },
    { dir: failed, created: 1_793_400_000, spec: spec("Phim cổ trang hay nhất"), video: null,
      status: { schema_version: 1, state: "FAILED", done: 2, total: 6, message: "Phần 2: Trailer phim cổ trang tập 2: Mốc cắt nằm ngoài thời lượng video", path: null } },
  ].filter(run => !deletedRuns.has(run.dir))];
}

/** The preview's background library: one panel-sized image and one that needs cropping. */
const previewBackgrounds = [
  { id: "a".repeat(64), extension: "jpg", name: "nen-go.jpg", width: 1080, height: 720, path: `C:\\Users\\preview\\AppData\\Local\\DubFlow\\backgrounds\\${"a".repeat(64)}.jpg`, hue: 30 },
  { id: "b".repeat(64), extension: "png", name: "hoa-van-rong.png", width: 1600, height: 900, path: `C:\\Users\\preview\\AppData\\Local\\DubFlow\\backgrounds\\${"b".repeat(64)}.png`, hue: 200 },
];

/** A stand-in background: a gradient with a grid, so dragging the focus shows. */
async function previewBackgroundImage(width: number, height: number, hue: number): Promise<ArrayBuffer> {
  const scale = Math.min(1, 720 / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(width * scale);
  canvas.height = Math.round(height * scale);
  const context = canvas.getContext("2d")!;
  const fill = context.createLinearGradient(0, 0, canvas.width, canvas.height);
  fill.addColorStop(0, `hsl(${hue} 55% 38%)`);
  fill.addColorStop(1, `hsl(${hue + 60} 45% 18%)`);
  context.fillStyle = fill;
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.strokeStyle = "rgba(255,255,255,.18)";
  context.lineWidth = 2;
  for (let x = 0; x < canvas.width; x += 60) { context.beginPath(); context.moveTo(x, 0); context.lineTo(x, canvas.height); context.stroke(); }
  for (let y = 0; y < canvas.height; y += 60) { context.beginPath(); context.moveTo(0, y); context.lineTo(canvas.width, y); context.stroke(); }
  context.fillStyle = "rgba(255,255,255,.5)";
  context.font = "bold 28px sans-serif";
  context.fillText(`${width}×${height}`, 16, 40);
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
  return blob!.arrayBuffer();
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

import type { JobStatus } from "../features/job_status/status.ts";

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
  [0.7, "Đang tổng hợp giọng Việt CPU và trộn audio"],
  [0.74, "Đang render video H.264/AAC"],
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
      status: { ...base, state: "COMPLETED", reason: "completed", message: job.dubbing ? "Hoàn tất video lồng tiếng Việt" : "Hoàn tất video Vietsub", progress: { ...base.progress, completed_units: "1000" }, resource: { ...base.resource, held: false } },
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
const linkFetches = new Map<string, { url: string; startedAt: number }>();
let douyinSession = false;

function fetchStatus(scanId: string) {
  const fetch = linkFetches.get(scanId);
  if (!fetch) return null;
  const elapsed = Date.now() - fetch.startedAt;
  const provider = /bilibili|b23\.tv/.test(fetch.url) ? "bilibili" : /douyin/.test(fetch.url) ? "douyin" : "generic";
  const base = { scan_id: scanId, url: fetch.url, provider, downloaded_bytes: 0, total_bytes: null as number | null, media_path: null as string | null, error: null as string | null };
  if (provider === "douyin" && !douyinSession) return { ...base, state: elapsed > 3000 ? "failed" : "preparing", error: elapsed > 3000 ? "Douyin cần phiên truy cập: bấm “Mở Douyin”, đợi trang hiện video, rồi bấm “Lưu phiên & thử lại”." : null };
  if (elapsed < 3000) return { ...base, state: "preparing" };
  const total = 47_065_346;
  const done = Math.min(total, Math.round(total * (elapsed - 3000) / 6000));
  return done >= total
    ? { ...base, state: "done", downloaded_bytes: total, total_bytes: total, media_path: `C:/DubFlow/source-media/${scanId}.mp4` }
    : { ...base, state: "downloading", downloaded_bytes: done, total_bytes: total };
}

let picked = 0;
export async function previewInvoke<T>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  await new Promise((resolve) => setTimeout(resolve, 120));
  switch (command) {
    case "release_info": return { version: "0.1.0", channel: "preview", backend: "mock" } as T;
    case "voice_catalog": return { default_voice_id: voices[0].voice_id, voices } as T;
    case "pick_files": {
      picked += 1;
      return [`D:\\Videos\\Douyin\\phim-ngan-tap-${picked}.mp4`, `D:\\Videos\\Bilibili\\review-dien-thoai-${picked}.mkv`] as T;
    }
    case "start_job": running.set(String(args.jobId), { startedAt: Date.now(), dubbing: !!args.enableDubbing, cancelled: false }); return null as T;
    case "cancel_job": { const job = running.get(String(args.jobId)); if (job) job.cancelled = true; return null as T; }
    case "job_status": return status(String(args.jobId)) as T;
    case "open_path": case "reveal_path": console.info(`[preview] ${command}`, args.path); return null as T;
    case "fetch_url": {
      const scanId = `link-${Date.now().toString(16)}`;
      linkFetches.set(scanId, { url: String(args.url), startedAt: Date.now() });
      return { status: fetchStatus(scanId), output_dir: `D:/Videos/DubFlow Output/${scanId}` } as T;
    }
    case "fetch_status": return fetchStatus(String(args.scanId)) as T;
    case "open_douyin_login": return null as T;
    case "review_clip": return previewTone(Number(args.endMs) - Number(args.startMs), Boolean(args.source)) as T;
    case "save_douyin_session": douyinSession = true; return 12 as T;
    case "review_cues": return reviewCues.map((cue) => ({ ...cue, text: savedOverrides[cue.cue_id] ?? cue.machine_text, edited: cue.cue_id in savedOverrides })) as T;
    case "save_review": savedOverrides = { ...(args.overrides as Record<string, string>) }; return "D:\Videos\DubFlow Output\preview" as T;
    default: throw new Error(`preview host does not implement ${command}`);
  }
}

/** A short sine tone WAV so the review "listen" buttons work in the browser preview. */
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

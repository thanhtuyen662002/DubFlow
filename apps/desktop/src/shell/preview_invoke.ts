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
    default: throw new Error(`preview host does not implement ${command}`);
  }
}

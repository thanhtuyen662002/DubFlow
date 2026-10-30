export const MAX_TEXT_CHARS = 600;
export type Background = "keep" | "replace" | "duck";
export type PackageInfo = { has_background_stem: boolean; voice_id: string };
export type PackageStatus = { state: "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED"; message: string; path?: string };
export type PackageForm = {
  outputPath: string; intro: string; outro: string; voiceId: string; speed: number;
  music: string | null; musicVolume: number; background: string; cpuOnly: boolean;
};

export function charCount(text: string): number { return Array.from(text).length; }

export function buildRequest(form: PackageForm, info: PackageInfo): PackageForm | string {
  for (const [text, label] of [[form.intro, "Mở đầu"], [form.outro, "Kết thúc"]]) {
    if (charCount(text) > MAX_TEXT_CHARS) return `${label} tối đa ${MAX_TEXT_CHARS} ký tự.`;
    if (/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]/u.test(text)) return `${label} không được chứa ký tự điều khiển.`;
    if (text.trim() && !/[\p{L}\p{N}]/u.test(text)) return `${label} không có câu nào để đọc.`;
  }
  if (!form.intro.trim() && !form.outro.trim() && !form.music) return "Hãy nhập mở đầu, kết thúc hoặc chọn nhạc.";
  if (!form.outputPath || !form.voiceId) return "Chưa đọc được thông tin video và giọng đọc.";
  if (!Number.isFinite(form.speed) || form.speed < 1 || form.speed > 1.3) return "Tốc độ đọc không hợp lệ.";
  if (!Number.isFinite(form.musicVolume) || form.musicVolume < 0 || form.musicVolume > 1) return "Âm lượng nhạc không hợp lệ.";
  if (form.music && !/\.(mp3|m4a|aac|wav|ogg|flac)$/i.test(form.music)) return "File nhạc không được hỗ trợ.";
  if (!["keep", "replace", "duck"].includes(form.background)) return "Cách dùng nhạc nền không hợp lệ.";
  if (form.background !== "keep" && !form.music) return "Hãy chọn nhạc nền.";
  if (form.background === "duck" && !info.has_background_stem) return "Không có nền gốc đã tách để giữ nhỏ.";
  return { ...form, intro: form.intro.trim(), outro: form.outro.trim() };
}

export function parseInfo(value: unknown): PackageInfo | null {
  if (!value || typeof value !== "object") return null;
  const info = value as Record<string, unknown>;
  return typeof info.has_background_stem === "boolean" && typeof info.voice_id === "string" && !!info.voice_id
    ? { has_background_stem: info.has_background_stem, voice_id: info.voice_id } : null;
}

export function parseStatus(value: unknown): PackageStatus | null {
  if (!value || typeof value !== "object") return null;
  const status = value as Record<string, unknown>;
  if (!["RUNNING", "COMPLETED", "FAILED", "CANCELLED"].includes(String(status.state)) || typeof status.message !== "string") return null;
  if (status.state === "COMPLETED" && (typeof status.path !== "string" || !status.path)) return null;
  return { state: status.state as PackageStatus["state"], message: status.message,
    ...(typeof status.path === "string" ? { path: status.path } : {}) };
}

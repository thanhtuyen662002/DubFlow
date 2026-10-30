export type SourceCredit = {
  schema_version: 1;
  creator: string;
  platform: string;
  url: string;
  rights: "own" | "permission" | "cc" | "unknown";
  note: string;
};

const FIELDS = [
  ["creator", 120, "Tác giả"], ["platform", 40, "Nền tảng"],
  ["url", 500, "Link"], ["note", 500, "Ghi chú"],
] as const;
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/u;

export function emptySourceCredit(): SourceCredit {
  return { schema_version: 1, creator: "", platform: "", url: "", rights: "unknown", note: "" };
}

function isWebUrl(value: string): boolean {
  if (!/^https?:\/\//.test(value)) return false;
  try { return Boolean(new URL(value).hostname); } catch { return false; }
}

/** A Vietnamese error for the form; the host checks the same limits again. */
export function validateSourceCredit(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return "Thông tin nguồn không hợp lệ.";
  const credit = value as Record<string, unknown>;
  if (credit.schema_version !== 1) return "Phiên bản thông tin nguồn không được hỗ trợ.";
  for (const [field, limit, label] of FIELDS) {
    const text = credit[field];
    if (typeof text !== "string") return `${label} không hợp lệ.`;
    if (CONTROL.test(text)) return `${label} không được chứa ký tự điều khiển.`;
    // Count Unicode characters, just like Rust's chars().count().
    if ([...text].length > limit) return `${label} tối đa ${limit} ký tự.`;
  }
  if (!["own", "permission", "cc", "unknown"].includes(credit.rights as string)) return "Quyền sử dụng không hợp lệ.";
  if (credit.url !== "" && !isWebUrl(credit.url as string)) return "Link phải bắt đầu bằng http:// hoặc https:// và có tên miền hợp lệ.";
  return null;
}

/** The host returns null only when no file exists. Invalid files stay errors. */
export function parseSourceCredit(value: unknown): SourceCredit | null {
  if (value === null) return emptySourceCredit();
  if (validateSourceCredit(value)) return null;
  const credit = value as SourceCredit;
  return { schema_version: 1, creator: credit.creator, platform: credit.platform, url: credit.url, rights: credit.rights, note: credit.note };
}

export function platformFromUrl(value: string): string {
  if (CONTROL.test(value) || !isWebUrl(value)) return "";
  const host = new URL(value).hostname.toLowerCase().replace(/\.$/, "");
  const platforms = [
    ["Bilibili", ["bilibili.com", "b23.tv"]], ["Douyin", ["douyin.com", "iesdouyin.com"]],
    ["TikTok", ["tiktok.com"]], ["YouTube", ["youtube.com", "youtu.be"]],
    ["Kuaishou", ["kuaishou.com", "gifshow.com", "chenzhongtech.com", "kwai.com"]],
  ] as const;
  return platforms.find(([, domains]) => domains.some((domain) => host === domain || host.endsWith(`.${domain}`)))?.[0] ?? "";
}

/** Prefill only a missing file, including after the queue has been restored. */
export function sourceCreditForJob(value: unknown, originUrl = ""): SourceCredit {
  const credit = parseSourceCredit(value);
  if (!credit) throw new Error("Thông tin nguồn đã lưu không hợp lệ.");
  const prefill = { ...credit, url: originUrl, platform: platformFromUrl(originUrl) };
  return value === null && !validateSourceCredit(prefill) ? prefill : credit;
}

export function formatCreditLine(credit: SourceCredit): string {
  const creator = credit.creator.trim(), platform = credit.platform.trim(), url = credit.url.trim();
  const author = creator && platform ? `${creator} (${platform})` : creator || platform;
  const parts = [author, url].filter(Boolean);
  return parts.length ? `Nguồn: ${parts.join(" – ")}` : "";
}

export function appendCredit(description: string, credit: SourceCredit): string {
  const line = formatCreditLine(credit);
  return line ? [description, line].filter(Boolean).join("\n\n") : description;
}

export function sourceRightsHint(credit: SourceCredit): string {
  return credit.rights === "unknown"
    ? "Chưa ghi quyền sử dụng video này; hãy điền mục “Nguồn & quyền” trước khi đăng."
    : "";
}

export type ImportPackStatus =
  | { state: "checking" }
  | { state: "available"; path: string; manifestSha256: string; capcutReason: string | null }
  | { state: "unavailable"; reason: string };

export function parseImportPackStatus(value: unknown): ImportPackStatus {
  if (!value || typeof value !== "object") throw new Error("Invalid import pack report");
  const report = value as Record<string, unknown>;
  if (report.schema_version !== 1) throw new Error("Unsupported import pack report");
  if (report.state === "unavailable" && typeof report.reason === "string" && report.reason.length <= 256) {
    return { state: "unavailable", reason: report.reason };
  }
  if (report.state !== "available" || typeof report.path !== "string" || report.path.length > 32768 ||
      /[\u0000-\u001f]/.test(report.path) || !/^(?:[a-z]:[\\/]|\\\\|\/)/i.test(report.path) ||
      typeof report.manifest_sha256 !== "string" || !/^sha256:[0-9a-f]{64}$/.test(report.manifest_sha256)) {
    throw new Error("Import pack was not independently validated");
  }
  const capcut = report.capcut as Record<string, unknown> | undefined;
  return { state: "available", path: report.path, manifestSha256: report.manifest_sha256,
    capcutReason: capcut && typeof capcut.reason === "string" ? capcut.reason.slice(0, 256) : null };
}

export function importPackMessage(status: ImportPackStatus | undefined): string {
  if (!status || status.state === "checking") return "Đang kiểm tra gói chỉnh sửa…";
  if (status.state === "available") return status.path;
  if (status.reason === "LEGACY_EXPORT_WITHOUT_IMPORT_PACK") return "Bản trước chỉ có video và tài nguyên tiêu chuẩn.";
  return "Chưa xác minh được gói chỉnh sửa. Video và phụ đề tiêu chuẩn vẫn có thể sử dụng.";
}

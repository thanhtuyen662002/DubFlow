import { parseSourceInput, supervisorSourceItems, type SourceRejection } from "./model.ts";

export type SourceReply = { event: string; rejected?: Array<{ index: number; code: string; field: string }> };
export type IntakeHost = {
  addLocal(paths: string[]): unknown;
  enqueue(items: Array<Record<string, string>>): Promise<SourceReply>;
};
export type IntakeResult = { local: number; online: number; rejected: SourceRejection[] };

/** Each invocation admits local work synchronously before starting online I/O.
 * No shared busy state: another local submission remains usable during an
 * outstanding/failed source call. Small network chunks stay below wire caps.
 */
export async function submitSources(input: string, host: IntakeHost): Promise<IntakeResult> {
  const batch = parseSourceInput(input);
  const paths = batch.items.filter((item) => item.kind === "local").map((item) => item.local_path!);
  if (paths.length) host.addLocal(paths);
  const result = { local: paths.length, online: 0, rejected: [...batch.rejected] };
  const urls = batch.items.filter((item) => item.kind === "url");
  for (let start = 0; start < urls.length; start += 64) {
    const chunk = urls.slice(start, start + 64);
    try {
      const reply = await host.enqueue(supervisorSourceItems({ ...batch, items: chunk }));
      if (reply.event !== "source_scan_accepted" && reply.event !== "source_scan_rejected") throw new Error("SOURCE_RESPONSE_INVALID");
      const rejected = reply.rejected ?? [];
      const indices = new Set(rejected.map((item) => item.index));
      if (indices.size !== rejected.length || rejected.some((item) => !Number.isInteger(item.index) || item.index < 0 || item.index >= chunk.length)
          || (reply.event === "source_scan_rejected" && rejected.length !== chunk.length)) throw new Error("SOURCE_RESPONSE_INVALID");
      result.online += chunk.length - indices.size;
      for (const failure of rejected) result.rejected.push({ line: 0, input: chunk[failure.index].display_ref,
        code: failure.code, message: "Nguồn bị từ chối tại bộ tiếp nhận" });
    } catch {
      for (const item of chunk) result.rejected.push({ line: 0, input: item.display_ref,
        code: "SOURCE_UNAVAILABLE", message: "Không nhận được xác nhận nguồn; kiểm tra trạng thái trước khi thêm lại" });
    }
  }
  return result;
}

import { ReviewConnection, type ReviewTransport } from "./connection.ts";
import { mountReviewCenter, type ReviewVoice } from "./view.ts";

/** Persistent optional panel: unrelated queue polls must not reset playback. */
export function mountDesktopReview(container: HTMLElement, transport: ReviewTransport) {
  const connection = new ReviewConnection(transport), doc = container.ownerDocument;
  let selected: { id: string; completed: boolean } | null = null;
  let generation = 0, disposed = false;
  let view: ReturnType<typeof mountReviewCenter> | null = null;
  let voices: readonly ReviewVoice[] = [];
  const controls = doc.createElement("div"), load = doc.createElement("button"), status = doc.createElement("p"), body = doc.createElement("div");
  container.setAttribute("aria-label", "Kiểm tra lời thoại của video");
  load.type = "button"; load.textContent = "Kiểm tra lời thoại";
  status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
  controls.append(load, status); container.append(controls, body);

  load.addEventListener("click", () => { void reload(); });
  async function reload() {
    if (!selected?.completed || disposed) return;
    const at = ++generation, jobId = selected.id;
    view?.destroy(); view = null; connection.clear(); body.replaceChildren();
    load.disabled = true; status.textContent = "Đang đọc lời thoại đã xuất…";
    try {
      const snapshot = await connection.load(jobId);
      if (at !== generation || disposed || !snapshot) return;
      view = mountReviewCenter(body, snapshot, connection.actions(voices));
      status.textContent = snapshot.schema_version === 3 ?
        (snapshot.mode === "b2" ? "Đang xem phiên bản hiện hành. Sửa bản dịch hoặc giọng, kiểm tra phạm vi chạy lại rồi xác nhận. Bản xuất hiện tại được giữ đến khi bản thay thế hợp lệ." :
          "Đang xem phiên bản phụ đề hiện hành. Sửa bản dịch, kiểm tra phạm vi chạy lại rồi xác nhận. Bản xuất hiện tại được giữ đến khi bản thay thế hợp lệ.") :
        "Có thể xem lời thoại và nghe từng câu. Chọn bật chỉnh sửa để lưu bản gốc vào lịch sử trước khi sửa. Bản xuất hiện tại vẫn dùng được.";
      load.textContent = "Tải lại lời thoại";
    } catch (error) {
      if (at !== generation || disposed) return;
      connection.clear();
      status.textContent = "Chưa mở được review cho video này. Bản xuất hiện tại vẫn dùng được. " +
        (typeof error === "string" ? error.slice(0, 512) : "Hãy tải lại lời thoại hoặc kiểm tra dữ liệu đầu ra.");
    } finally { if (at === generation && !disposed) load.disabled = false; }
  }

  return {
    select(job: { id: string; completed: boolean } | null) {
      if (disposed || (selected?.id === job?.id && selected?.completed === job?.completed)) return;
      generation++; connection.clear(); view?.destroy(); view = null; body.replaceChildren(); selected = job;
      container.hidden = !job?.completed; load.disabled = !job?.completed; load.textContent = "Kiểm tra lời thoại";
      status.textContent = "Xem các câu cần kiểm tra và nghe lại giọng đọc.";
    },
    setVoices(choices: readonly ReviewVoice[]) { voices = choices.map(item => ({ ...item })); },
    destroy() { disposed = true; generation++; connection.dispose(); view?.destroy(); container.replaceChildren(); },
  };
}

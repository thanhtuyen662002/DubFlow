import type { SourceRow } from "./service_controller.ts";

export function cancellationMessage(delivery: SourceRow["delivery"]): string {
  if (delivery === "submitted") return "Đã gửi yêu cầu hủy, chờ xác nhận";
  if (delivery === "pending") return "Đang gửi yêu cầu hủy";
  return "Chưa xác nhận yêu cầu hủy đã được gửi";
}

export function inputProblemMessage(code: string): string | null {
  const messages: Record<string, string> = {
    INPUT_TOO_LONG: "Dòng này vượt giới hạn độ dài. Hãy dùng đường dẫn hoặc URL ngắn hơn.",
    URL_INVALID: "URL chưa hợp lệ. Hãy kiểm tra địa chỉ video.",
    LOCAL_PATH_INVALID: "Chưa nhận ra đường dẫn video. Hãy nhập đường dẫn đầy đủ hoặc dùng nút Thêm video.",
    INPUT_INVALID: "Dòng nguồn chưa hợp lệ. Mỗi dòng cần một đường dẫn đầy đủ hoặc URL công khai.",
    QUEUE_LIMIT: "Đã đạt giới hạn danh sách. Hãy kiểm tra hàng đợi trước khi thêm nguồn.",
    LIMIT_INVALID: "Giới hạn tiếp nhận chưa hợp lệ. Bạn vẫn có thể dùng nút Thêm video.",
  };
  return messages[code] ?? null;
}

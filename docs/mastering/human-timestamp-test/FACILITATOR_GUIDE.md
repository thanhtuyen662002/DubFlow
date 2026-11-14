# Hướng dẫn cho người điều phối

Đi kèm [PROTOCOL.md](PROTOCOL.md). Người điều phối chuẩn bị tài liệu, ghi lại phiên và chấm điểm sau phiên. Người điều phối **không** gợi ý cho người tham gia, và không được là người tham gia.

Mọi lệnh dưới đây chỉ đọc file run đã lưu. Chúng không gọi AI, không cần mạng, không tốn chi phí. Các đường dẫn trong `<…>` là chỗ cần thay bằng thư mục trên máy.

## 1. Trước phiên (một lần)

1. **Chủ dự án duyệt §8 của PROTOCOL.md:** người tham gia, video thứ hai cùng nhãn của nó, số câu, tiêu chí, thời gian. Chưa duyệt thì không chạy.
2. **Tạo thư mục làm việc ngoài repo**, ví dụ `<_sandbox>\human-ts\<mã phiên>\`. Mọi biểu mẫu đã điền, kết quả, ghi chú đều nằm ở đây.
3. **Khoá nhãn** cho từng video: tệp nhãn JSON và tệp `.lock` chứa sha256. Video 1 dùng nhãn W3 đã khoá. Nhãn của video 2 phải được khoá **trước** khi chọn câu, và người gắn nhãn không phải người tham gia.
4. **Chọn câu** bằng quy tắc cố định, rồi điền `forms/selection.csv`:
   - lấy mọi câu không có footage của video (cột `has_footage` = 0);
   - lấy các câu có footage mà production **không** để READY trong run đã chọn, xếp theo thứ tự trong nhãn, rồi lấy cách đều (mỗi câu thứ k, k cố định trước) cho đủ số câu đã duyệt;
   - cột `production_rank_bucket`: `top1`, `top2-5` hoặc `none`, tuỳ cảnh đúng đứng ở đâu trong top 5 của production (lấy từ kết quả `w3.py score` đã có, không phải từ người tham gia);
   - chia câu thành bốn khối V1-a, V1-b, V2-a, V2-b, cân bằng số câu, số câu không có footage và `production_rank_bucket`;
   - gán cách làm theo ABBA (người thứ hai BAAB) vào cột `condition`.
5. **Khoá danh sách câu:** tính sha256 của `selection.csv` và ghi vào `forms/session.csv` (cột `selection_sha256`). Sau đó không sửa.
6. **Chuẩn bị máy:** DubFlow.exe bản candidate với **thư mục dữ liệu riêng**, mở đúng run của mỗi video. Video nguồn mở trong trình phát thường. Tắt thông báo. Không mở nhãn hay báo cáo W3 trên màn hình.
7. **In hoặc mở** `PARTICIPANT_GUIDE.md` và `forms/post-session.md` cho người tham gia.

## 2. Trong phiên

Điền `forms/units.csv`, mỗi dòng là một câu đã làm:

| Cột | Cách ghi |
|---|---|
| `session_id`, `participant_code` | mã phiên, mã người (P1, P2); không ghi tên |
| `block`, `video`, `unit_id`, `condition` | chép từ `selection.csv` (`video` là tên ngắn, ví dụ `v1`, khớp với tham số `--run`/`--gt` khi chấm) |
| `position` | thứ tự câu trong phiên (1, 2, 3…) |
| `ts_hint` | **TS:** mốc **đầu tiên** người tham gia ghi, dạng `mm:ss` hoặc `h:mm:ss`. Mốc sau (nếu đánh lại) ghi vào `notes` |
| `ts_precision_s` | **TS:** độ chắc chắn người tham gia chọn: 5, 15 hoặc 30 |
| `ts_step1_s` | **TS:** số giây từ lúc đọc câu tới lúc ghi xong mốc |
| `ts_retries` | **TS:** số lần đánh mốc lại |
| `suggested_ids` | **TS:** id các gợi ý đã đưa, theo thứ tự, ngăn bằng `;` |
| `chosen_id` | id cảnh cuối cùng được chọn. **RP:** id trong bản ghi quyết định của Review Panel; nếu bấm "Duyệt cảnh này" thì là id cảnh đang dùng. Để trống nếu đánh "không có cảnh phù hợp" |
| `marked_missing` | `1` nếu người tham gia đánh "không có cảnh phù hợp", để trống nếu không |
| `step2_s` | **TS:** số giây từ lúc nhận gợi ý tới lúc chọn xong |
| `total_s` | **RP:** từ lúc đọc câu tới lúc bấm quyết định. **TS:** `ts_step1_s + step2_s`, cộng thời gian các lần đánh lại; không tính thời gian chạy lệnh |
| `actions` | số thao tác: RP lấy từ bản ghi quyết định cộng đếm tay (mở "Chọn cảnh khác…", phát cảnh); TS đếm tay (tua, phát, ghi mốc, chọn) |
| `re_searches` | RP: số lần mở lại "Chọn cảnh khác…" sau lần đầu; TS: bằng `ts_retries` |
| `confidence_1_5` | độ tự tin người tham gia tự chấm |
| `assisted` | `1` nếu người tham gia hỏi và người điều phối phải trả lời; chép câu hỏi vào `notes` |
| `notes` | ghi chú, không chép nội dung câu lời dẫn |

**Lệnh gợi ý cho TS** (chạy trên máy người điều phối, không cho người tham gia xem màn hình lệnh):

```
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1
py -3.12 -B tests\mastering\longform\hintexp.py suggest --run <thư mục run của video> --unit <unit_id> --at <mm:ss> --within <5|15|30>
```

Lệnh in tối đa 5 dòng gồm hạng, id cảnh, nguồn, giây bắt đầu và giây kết thúc. Đọc cho người tham gia các khoảng thời gian (ví dụ "gợi ý 1: 12:04 đến 12:09"), không đọc điểm hay lý do. Nếu phần có nhiều nguồn và người tham gia đánh mốc trên một nguồn cụ thể, thêm `--source <id nguồn>`.

**Sai lệch giao thức** (ghi vào cột `deviations` của `session.csv`):

- người tham gia nói đã biết cảnh đúng của một câu;
- một câu bị làm bằng cả hai cách;
- thứ tự khối khác với đã gán;
- người điều phối lỡ gợi ý.

Có sai lệch thì kết quả của phiên là INCONCLUSIVE (PROTOCOL.md §6). Không xoá dòng để "sửa" sai lệch.

## 3. Sau phiên

1. Người tham gia điền `forms/post-session.md`.
2. Chấm điểm:

```
py -3.12 -B tests\mastering\longform\hintexp.py score-human ^
  --units <thư mục phiên>\units.csv ^
  --gt v1=<nhãn video 1>.json,<nhãn video 1>.lock --gt v2=<nhãn video 2>.json,<nhãn video 2>.lock ^
  --run v1=<run video 1> --run v2=<run video 2> ^
  --out <thư mục phiên>\human-results.json
```

   Lệnh kiểm sha256 của mỗi nhãn, chấm từng câu, áp tiêu chí PROTOCOL.md §6 và in kết luận (SUCCESS, INCONCLUSIVE hoặc NEGATIVE) cùng lý do. Kết quả có nhãn `HUMAN (real participants)`.

   Lệnh này chỉ được chạy trên biểu mẫu do người thật điền. Không chạy nó trên số tự tạo hay trên kết quả tự động hoá CDP rồi gọi đó là kết quả người dùng.

3. **Giới hạn cần ghi trong báo cáo:** TS không có giao diện riêng (chọn trên biểu mẫu); mẫu nhỏ, chỉ có số mô tả; mức quen phim của người tham gia.
4. **Đưa vào repo:** chỉ số tổng hợp từ `human-results.json` và sha256 của các biểu mẫu, qua một PR riêng, sau khi quét rò rỉ bằng 0 (đường dẫn máy, e-mail, chuỗi giống khoá, nội dung câu). Biểu mẫu đã điền, video và khung hình ở lại trên máy.
5. Kết quả không đổi gate nào (PROTOCOL.md §6).

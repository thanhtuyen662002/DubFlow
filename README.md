# DubFlow Local

Local-first, one-click video localization pipeline focused on Douyin and Bilibili.

## Hướng dẫn sử dụng

DubFlow chuyển video tiếng Trung/Anh thành video tiếng Việt **ngay trên máy bạn**: nhận dạng lời thoại, dịch, tạo phụ đề và (tùy chọn) lồng tiếng Việt. Không cần GPU, không gửi video lên dịch vụ đám mây.

### Cài đặt (Windows 10/11 x64)
1. Vào [Releases](https://github.com/thanhtuyen662002/DubFlow/releases), tải `DubFlow-<phiên bản>-windows-x64-setup.exe`.
2. Chạy file cài đặt (không cần quyền quản trị, không cần cài Python hay Node):
   - chọn **thư mục cài đặt** (mặc định `%LOCALAPPDATA%\DubFlow`; có thể chọn ổ khác, ví dụ `D:\Apps\DubFlow`) và có tạo lối tắt trên Desktop hay không;
   - bấm **Cài đặt** và chờ thanh tiến trình chạy hết (thường 1–3 phút);
   - bấm **Hoàn tất** để mở DubFlow.
3. Sau này mở DubFlow từ menu Start hoặc lối tắt trên Desktop (file `DubFlow.exe` nằm ngay trong thư mục cài đặt). Gỡ cài đặt trong **Settings > Apps > Installed apps > DubFlow**; video kết quả của bạn không bị xóa.
4. Lần xử lý đầu tiên sẽ tải model nhận dạng/dịch/giọng đọc (khoảng 1 GB, lưu trong thư mục cài đặt); các lần sau chạy ngoại tuyến.

Khi app báo có bản mới, bấm **Cập nhật**: bản mới được cài vào đúng thư mục đang dùng và app tự mở lại. Cài bằng dòng lệnh: `DubFlow-<phiên bản>-windows-x64-setup.exe --silent [--install-root <thư mục>] [--no-launch]`.

### Xử lý một video
1. **Thêm video**: kéo thả file hoặc cả thư mục vào cửa sổ, bấm **Thêm video**, hoặc dán link vào ô **Dán link…** rồi bấm **Tải link**.
2. Chọn video trong danh sách và chọn tùy chọn ở bảng bên phải:
   - **Lồng tiếng Việt**: bật để có giọng đọc (chọn giọng bên dưới); tắt để chỉ tạo phụ đề và giữ audio gốc. Khi bật có thêm hai tùy chọn thử nghiệm:
     - *Giữ nhạc nền, bỏ giọng gốc*: tách giọng nói gốc khỏi nhạc nền/hiệu ứng rồi lồng tiếng Việt lên nhạc nền (lần đầu tải thêm khoảng 80 MB). Có thể còn sót chút giọng gốc hoặc méo nhẹ ở đoạn nhạc phức tạp.
     - *Nhiều giọng theo người nói*: nhận diện từng người nói và gán giọng Việt riêng cho mỗi người (lần đầu tải thêm khoảng 28 MB). Có thể nhận nhầm khi giọng giống nhau hoặc câu quá ngắn — đổi lại trong **Xem & sửa bản dịch**.
   - **Ngôn ngữ gốc**: để *Tự động*, hoặc chọn *Tiếng Trung* / *Tiếng Anh* nếu nhận diện sai (video nhiều nhạc).
   - **Bộ dịch tiếng Trung**: mặc định *Argos*. *HachimiMT-60 (thử nghiệm)* dịch tiếng Trung thường tự nhiên hơn (tải thêm khoảng 58 MB ở lần dùng đầu), nhưng có thể dùng văn phong kiểu truyện (ta/ngươi). **Giấy phép chưa được xác minh cho mục đích thương mại** (model huấn luyện có dữ liệu chưng cất từ Gemini và một bộ dữ liệu GPL-3.0), nên chỉ dùng cá nhân/thử nghiệm; xem [docs/licenses/hachimi-mt-60.md](docs/licenses/hachimi-mt-60.md). Video tiếng Anh luôn dịch bằng Argos.
   - **Gắn phụ đề vào video**: tắt nếu chỉ muốn file phụ đề SRT/ASS rời.
   - **Làm mờ phụ đề gốc** (che chữ Trung in sẵn trong video, phụ đề tiếng Việt vẽ đè lên trên):
     - *Tự động*: app dò dòng phụ đề ở nửa dưới khung hình và làm mờ đúng lúc nó xuất hiện cùng lời thoại; logo/watermark đứng yên được giữ nguyên. Lần đầu tải thêm model dò chữ khoảng 5 MB.
     - *Thủ công*: bấm **Chọn vùng…**, kéo khung (hoặc kéo từng mép, ví dụ hai mép trái/phải vào giữa) cho phủ kín chữ gốc; vùng này được làm mờ suốt video. Kéo thanh **Vị trí** để kiểm tra ở nhiều cảnh. **Áp dụng cho mọi video đang chờ** dùng cùng một vùng cho cả lô.

   Tùy chọn bạn chọn gần nhất sẽ tự áp dụng cho các video thêm sau.
3. Bấm **Bắt đầu xử lý**, hoặc **Xử lý tất cả** để chạy lần lượt mọi video đang chờ. Bảng bên phải hiện từng bước, thời gian đã chạy và ước tính còn lại. Có thể **Hủy xử lý**, hoặc đóng ứng dụng giữa chừng — lần sau sẽ tiếp tục từ checkpoint.
4. Khi xong, bấm **Mở video** hoặc **Mở thư mục**. Kết quả gồm `final_vi.mp4`, phụ đề `captions_vi.srt` / `captions_vi.ass` và gói chỉnh sửa trong thư mục `editable`.

**Lưu kết quả vào** (thanh bên trái): mặc định lưu vào thư mục `DubFlow Output` cạnh video gốc (video tải từ link: `Videos\DubFlow Output`); bấm **Chọn thư mục…** để gom mọi kết quả về một nơi.

### Sửa bản dịch
Bấm **Xem & sửa bản dịch** ở video đã xong. Mỗi dòng hiện câu gốc và câu tiếng Việt, với nút **▶ Gốc** / **▶ Lồng tiếng** để nghe thử; câu lồng tiếng lỗi hoặc đọc quá nhanh được đánh dấu. Sửa câu (ngắn gọn, tự nhiên hơn) rồi bấm **Lưu & xử lý lại** — DubFlow chỉ chạy lại phần cần thiết và chỉ lồng tiếng lại các câu đã sửa. Bản sửa được lưu vào một thư mục mới bên cạnh (tên kết thúc bằng “(sửa 1)”, “(sửa 2)”…); video và phụ đề cũ được giữ nguyên.

Với lời thoại nhanh, DubFlow tự gộp các câu liền nhau thành cụm để đọc cho vừa, tự rút gọn từ đệm và đọc nhanh hơn một chút khi cần (tối đa 1,5 lần). Nếu một câu vẫn quá dài so với thời lượng, câu đó giữ tiếng gốc và thẻ kết quả sẽ cảnh báo — hãy rút gọn nó ở đây.

Bấm **Hoàn tác** (hoặc Ctrl+Z khi không gõ trong ô) để bỏ lần sửa gần nhất. Với video lồng *nhiều giọng*, mỗi câu có nhãn người nói (S1, S2…) và mục **Giọng theo người nói** để đổi giọng cho từng người; bấm **Lưu & xử lý lại** để lồng tiếng lại.

### Thêm mở đầu, kết thúc và nhạc của bạn

Ở video đã hoàn tất, mở **Đóng gói video** bên dưới **Nguồn & quyền**. Nhập mở đầu và/hoặc kết thúc (mỗi phần tối đa 600 ký tự), chọn tốc độ đọc rồi bấm **Tạo video đóng gói**. App dùng giọng đã lồng tiếng để đọc, trên nền khung hình làm mờ và tối cùng phụ đề ở giữa.

Bạn có thể chọn nhạc riêng, chỉnh âm lượng và chọn giữ âm thanh gốc, thay nhạc nền hoặc giữ nền gốc ở mức 25% cùng nhạc mới. Hai cách thay nhạc cần bản giọng Việt riêng; giữ nền gốc nhỏ cần thêm bản nền đã tách. Kết quả là `final_vi_packaged.mp4` cạnh `final_vi.mp4`; video gốc và các bản âm thanh được giữ nguyên. Có thể hủy trong lúc xử lý, hoặc thử lại để dùng lại các phần đã tạo xong.

### Gợi ý tiêu đề & hashtag khi đăng lại
Ở video đã xong, điền mục **Nguồn & quyền** để lưu tác giả, nền tảng, link nguồn, quyền sử dụng và ghi chú, rồi bấm **Lưu**. Video tải từ link được điền sẵn link và nền tảng khi chưa có thông tin đã lưu. Thông tin nằm trong `source_credit.json` cạnh video kết quả; link nguồn không tự xác nhận quyền sử dụng.

Gợi ý đăng bài sẽ thêm dòng nguồn vào mô tả để sao chép cùng nội dung và nhắc nếu quyền sử dụng còn **Chưa rõ**. Thông tin nguồn và ghi chú không được gửi tới AI.

Ở video đã xong, bấm **Gợi ý tiêu đề & hashtag**: app tạo sẵn vài tiêu đề, mô tả ngắn và hashtag tiếng Việt từ phụ đề (không cần mạng), mỗi mục có nút **Sao chép**. Chọn **AI** (Claude hoặc Gemini) rồi bấm **Viết bằng AI** để AI viết lại hay hơn — cần khóa API của bạn, lưu một lần trong **Cài đặt AI** (Windows Credential Manager); phụ đề tiếng Việt được gửi tới nhà cung cấp đó và tính phí theo tài khoản của bạn.

### Khám phá video (xu hướng, từ khóa, lấy video hàng loạt)
Bấm **Khám phá** ở thanh bên trái, chọn **Douyin**, **Bilibili** hoặc **Kuaishou**:
- **Đang hot**: từ khóa đang thịnh hành của nền tảng; bấm một từ để tìm ngay.
- **Từ khóa**: gõ từ khóa tiếng Trung, app gợi ý từ khóa liên quan (Douyin, Bilibili). Bilibili hiện kết quả ngay trong app (có **Thịnh hành** và **Tải thêm**).
- **Douyin / Kuaishou**, hoặc khi Bilibili tạm chặn: trang tìm kiếm mở trong cửa sổ **Khám phá video**; cuộn trang (hoặc bấm **Cuộn thêm**) rồi bấm **Lấy video từ trang**.
- **Trang kênh**: chọn *Trang kênh* và dán link kênh (ví dụ `https://space.bilibili.com/…`, trang người dùng Douyin/Kuaishou) để lấy video của kênh đó.
- Tích chọn video rồi bấm **Thêm vào hàng đợi**: video được tải về và xếp vào hàng đợi như khi dán link.

### Link Bilibili / Douyin
- **Link video trực tiếp** (`.mp4`, …): dán và tải ngay.
- **Bilibili**: dán link video `bilibili.com/video/BV…`. Nếu trang báo giới hạn truy cập, đợi vài phút rồi thử lại.
- **Douyin**: lần đầu, dòng tải sẽ báo cần phiên truy cập. Bấm **Mở Douyin**, đợi trang hiện video (có thể đăng nhập nếu muốn), rồi bấm **Lưu phiên & thử lại**. Phiên được mã hóa trên máy và hết hạn sau khoảng 23 giờ.

Chỉ tải và xử lý video bạn có quyền sử dụng.

### Cập nhật & quay lại bản cũ
DubFlow tự kiểm tra bản mới mỗi ngày (hoặc bấm **Kiểm tra cập nhật** ở góc dưới thanh bên trái). Khi có bản mới, bấm **Cập nhật**: app tải bộ cài đã được ký số, kiểm tra chữ ký và mã băm rồi cài bên cạnh bản hiện tại và mở lại. Bấm vào số phiên bản để xem **Phiên bản đã cài** và **Dùng bản này** nếu muốn quay lại bản trước.

### Gặp lỗi?
- Thông báo lỗi hiện ngay trong dòng video; có thể bấm **Xử lý lại với tùy chọn khác** để tạo bản mới (bản cũ vẫn được giữ).
- Báo lỗi tại [Issues](https://github.com/thanhtuyen662002/DubFlow/issues), kèm thông báo lỗi và file `job_manifest.json` / `qc_report.json` trong thư mục kết quả nếu có.

## For contributors

GitHub is the durable engineering control plane for requirements, architecture, execution state, CI evidence, risks, decisions, Issues, pull requests, and releases.

### Start here

1. `AGENTS.md` — engineering constitution and non-negotiable invariants.
2. `docs/PRODUCT_SYSTEM_DESIGN.md` — product, UX, backend/frontend and AI pipeline design.
3. `docs/RISK_REGISTER.md` — product + engineering kill paths and recovery rules.
4. `docs/ENGINEERING_EXECUTION_PROTOCOL.md` — Issue/PR leases, anti-stall rules and CI lanes.
5. `docs/PROJECT_STATE.yaml` — durable current project state.
6. `docs/AUDIT_BOOTSTRAP.md` — bootstrap architecture/delivery audit.
7. `docs/adr/` — accepted architecture decisions.

Agents must not begin implementation from an Epic and must not push directly to `main`.

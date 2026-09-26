# DubFlow Local — Product & System Design

> Tên đề xuất: **DubFlow Local**
>
> Mục tiêu sản phẩm: một ứng dụng desktop chạy local, ưu tiên Windows, biến video nguồn (URL, danh sách URL, folder, hoặc toàn bộ kênh) thành video đã Việt hóa bằng một quy trình gần như **1-click**: tải → phân tích → nhận diện thoại/sub → dịch → phân vai → lồng tiếng đa nhân vật → xử lý sub gốc → chèn Vietsub → mix âm thanh → render → QC → xuất video hoàn chỉnh và gói có thể tiếp tục chỉnh sửa trong CapCut.

---

## 1. Product promise

Người dùng bình thường không cần biết:

- Python là gì.
- FFmpeg là gì.
- CUDA/CuDNN là gì.
- OCR model nào đang chạy.
- ASR/TTS/diarization dùng model nào.
- Phải tải model từ đâu.
- Cần chia video thành chunk ra sao.
- Phải xử lý lỗi VRAM hay codec thế nào.
- Phải cập nhật yt-dlp/model/runtime ra sao.

Người dùng chỉ cần:

1. Mở DubFlow Local.
2. Dán URL hoặc chọn video/folder.
3. Chọn đầu ra mong muốn:
   - Video hoàn chỉnh.
   - Video + file chỉnh sửa.
   - CapCut Project / CapCut Import Pack.
4. Bấm **Bắt đầu**.
5. Nhận kết quả.

Mọi lựa chọn nâng cao phải tồn tại nhưng không được ép người dùng mới phải hiểu.

---

## 2. Nguyên tắc UX trước kỹ thuật

### 2.1. Hai chế độ giao diện

**Quick Mode — mặc định**

Chỉ hiển thị:

- Nguồn video.
- Nơi lưu.
- Ngôn ngữ đích: mặc định Tiếng Việt.
- Tùy chọn:
  - Vietsub.
  - Lồng tiếng.
  - Giữ nhạc/hiệu ứng.
  - Xuất CapCut.
- Nút **Bắt đầu**.

Tool tự quyết định toàn bộ thông số còn lại.

**Studio Mode — nâng cao**

Cho phép kiểm soát:

- Chất lượng video.
- Encoder.
- OCR.
- TTS.
- Danh sách nhân vật.
- Voice mapping.
- Subtitle style.
- Translation glossary.
- Model profile.
- GPU/CPU.
- CapCut compatibility.
- Threshold confidence.
- Chế độ xóa sub cũ.
- Render profile.

Quick Mode và Studio Mode dùng cùng pipeline; khác nhau ở mức độ hiển thị cấu hình.

### 2.2. Không bắt người dùng “setup AI”

Lần chạy đầu:

1. App kiểm tra CPU/RAM/GPU/VRAM/driver/dung lượng ổ đĩa.
2. Tự chọn hardware profile.
3. Tự tải runtime/model pack phù hợp.
4. Kiểm tra checksum.
5. Test inference cực ngắn.
6. Nếu GPU không dùng được, tự chuyển CPU hoặc model nhẹ.
7. Sau đó mở màn hình chính.

Không yêu cầu người dùng cài Python, FFmpeg, Git, CUDA Toolkit, Conda.

### 2.3. Không để pipeline chết vì một bước phụ

Một lỗi không quan trọng không được làm mất cả job.

Ví dụ:

- Không xóa được sub hoàn hảo → vẫn xuất bản với phương án che sub an toàn.
- Active-speaker không chắc chắn → dùng voice cluster + continuity.
- CapCut adapter không tương thích → vẫn xuất video + SRT + audio stems + timeline manifest.
- Hardware encoder lỗi → fallback software encoder.
- Một model hết VRAM → unload model khác, giảm batch/chunk rồi retry.

---

## 3. Phạm vi input

### 3.1. URL

- Một URL video.
- Nhiều URL.
- URL playlist.
- URL kênh/profile.
- Bilibili.
- Douyin là ưu tiên.
- Các nguồn khác đi qua adapter chung.

### 3.2. File local

- Một file video.
- Nhiều file video.
- Cả folder.
- Recursive folder tùy chọn.

### 3.3. Dedup

Mỗi media có identity:

```text
source_provider
source_video_id
source_url
content_hash
```

Nếu video đã tải/xử lý:

- Skip.
- Re-render bằng cấu hình mới.
- Re-run từ stage được chọn.
- Force full rebuild.

---

## 4. Output contract

Mỗi job nên sinh:

```text
output/<job-name>/
├─ final/
│  ├─ final_vi.mp4
│  └─ final_vi.srt
├─ editable/
│  ├─ clean_video.mp4
│  ├─ background_music_sfx.wav
│  ├─ dub_mix.wav
│  ├─ speaker_001.wav
│  ├─ speaker_002.wav
│  ├─ captions_vi.srt
│  ├─ captions_vi.ass
│  ├─ timeline.json
│  ├─ character_voice_map.json
│  └─ qc_report.json
├─ capcut/
│  ├─ draft/                 # khi adapter phiên bản được hỗ trợ
│  └─ import_pack/           # fallback ổn định
├─ debug/                    # mặc định có thể dọn tự động
└─ job_manifest.json
```

### 4.1. Video hoàn chỉnh

Mặc định:

- Giữ resolution/aspect ratio nguồn.
- Giữ FPS nếu hợp lệ.
- H.264/AAC MP4 là profile tương thích cao nhất.
- Có thể chọn HEVC/AV1 trong Studio Mode.
- 9:16, 16:9, 1:1, 4:3 hoặc ratio lạ đều được đọc từ media metadata; không hard-code.

### 4.2. Editable output

Không được phụ thuộc duy nhất vào CapCut.

Luôn sinh bộ tài nguyên chuẩn:

- Clean video.
- Dialogue/dub audio.
- Background/M&E.
- SRT/ASS.
- Timeline JSON.

Nhờ vậy nếu CapCut đổi project format thì người dùng vẫn không mất kết quả.

---

## 5. Kiến trúc tổng thể

```text
┌──────────────────────────────┐
│      Tauri Desktop UI        │
│ React + TypeScript + shadcn  │
└──────────────┬───────────────┘
               │ commands/events
┌──────────────▼───────────────┐
│      Rust App Supervisor     │
│ install/update/job/process   │
│ health/retry/resource locks  │
└─────┬──────────┬─────────────┘
      │          │
      │          ├──────────────► SQLite job/state DB
      │
      └── JSONL/stdio worker protocol
                │
┌───────────────▼──────────────────────────────────────┐
│              Python AI Engine                       │
│ download | media | ASR | OCR | speaker | translate │
│ TTS | separation | inpaint | subtitles | render    │
└──────┬──────────┬──────────┬────────────┬───────────┘
       │          │          │            │
     FFmpeg     Models     Cache      Job Artifacts
```

### 5.1. Vì sao Tauri + Rust supervisor

- Desktop binary nhẹ hơn Electron.
- Có updater ký số.
- Rust phù hợp quản lý process, file, update, lock, checksum và crash recovery.
- UI vẫn dùng React/shadcn nên tốc độ làm frontend nhanh.
- Không để UI trực tiếp quản lý model AI.

### 5.2. Vì sao AI engine vẫn là Python

Hệ sinh thái OCR/ASR/TTS/diarization/video AI mạnh nhất vẫn tập trung ở Python/PyTorch/ONNX.

Python engine không được chạy như một script tùy tiện. Nó là worker có contract rõ ràng.

---

## 6. Runtime/installer architecture

### 6.1. Một installer, không prerequisite

Installer chứa:

- DubFlow shell.
- Rust supervisor.
- FFmpeg/ffprobe build đã pin.
- Python runtime portable hoặc runtime do app quản lý.
- Core AI engine.
- Model Manager.

Model nặng được tải sau theo hardware profile.

### 6.2. Không dùng system Python

Không chạy:

```text
pip install ...
python from PATH
conda ...
```

trên máy người dùng.

App phải sở hữu runtime riêng để tránh:

- xung đột phiên bản;
- Python bị xóa;
- PATH sai;
- pip package bị app khác sửa;
- DLL hell.

### 6.3. Model packs

Ví dụ:

```text
model-packs/
├─ core-asr/
├─ chinese-ocr/
├─ speaker-basic/
├─ speaker-av/
├─ translation-vi/
├─ tts-vi/
├─ inpaint-standard/
└─ inpaint-quality/
```

Mỗi pack có:

```json
{
  "id": "speaker-av",
  "version": "1.3.0",
  "files": [],
  "sha256": {},
  "min_vram_mb": 6144,
  "dependencies": [],
  "fallback_pack": "speaker-basic"
}
```

### 6.4. Auto-update

Tách thành ba loại:

1. **App update**
2. **Engine update**
3. **Model pack update**

Không update model/runtime giữa một stage đang chạy.

Quy trình:

```text
download -> verify signature/checksum -> stage update
-> wait safe checkpoint -> snapshot state
-> switch atomically -> healthcheck
-> commit
```

Nếu healthcheck fail:

```text
rollback version cũ -> tiếp tục job
```

---

## 7. Hardware profiler

Lúc đầu app thu thập:

- OS.
- CPU.
- RAM.
- GPU vendor.
- GPU model.
- VRAM.
- Driver.
- Storage free.
- Hardware encode/decode capabilities.

Sinh profile:

### Tier CPU

- OCR nhẹ.
- ASR quantized.
- TTS nhẹ.
- Inpainting hạn chế.
- Chạy được nhưng chậm.

### Tier GPU 8 GB

- ASR medium/large quantized.
- OCR GPU.
- diarization.
- TTS.
- inpainting theo chunk.

### Tier GPU 12–16 GB

- profile mặc định chất lượng cao.

### Tier GPU 24 GB+

- model chất lượng cao hơn.
- batch lớn hơn.
- parallel inference có kiểm soát.

Không được assume GPU NVIDIA luôn tồn tại.

---

# 8. Job State Machine

Một job không phải một hàm dài.

```text
CREATED
  ↓
INPUT_RESOLVED
  ↓
DOWNLOADED
  ↓
MEDIA_ANALYZED
  ↓
PROXY_READY
  ↓
AUDIO_ANALYZED
  ↓
VIDEO_ANALYZED
  ↓
TRANSCRIPT_READY
  ↓
TEXT_TRACKS_READY
  ↓
SPEAKERS_READY
  ↓
TRANSLATED
  ↓
DUB_READY
  ↓
SUBTITLE_CLEANED
  ↓
MIX_READY
  ↓
RENDERED
  ↓
QC_PASSED
  ↓
EXPORTED
  ↓
DONE
```

Mỗi stage ghi checkpoint trước và sau.

Mỗi artifact có:

```text
artifact_id
stage
path
content_hash
created_at
producer_version
model_version
input_hash
```

Nếu crash, app tiếp tục từ artifact hợp lệ gần nhất.

---

# 9. Pipeline chi tiết

## Stage 0 — Preflight

Trước khi tải hoặc xử lý:

- Xác nhận output folder writable.
- Ước lượng dung lượng.
- Probe GPU.
- Healthcheck model.
- Check FFmpeg.
- Check temp directory.
- Acquire resource lock.

Không nên đợi đến 95% mới báo ổ đĩa đầy.

### Storage estimate

Không dùng con số cố định.

Estimate dựa trên:

```text
source size
proxy size
decoded audio
stems
frame cache
inpaint cache
render temp
output profile
```

Cho phép auto-clean intermediate đã không còn cần.

---

## Stage 1 — Source Resolver / Downloader

Thiết kế adapter:

```text
SourceAdapter
├─ can_handle(url)
├─ inspect(url)
├─ enumerate()
├─ download()
├─ subtitles()
├─ metadata()
└─ auth_requirements()
```

Implement:

```text
YtDlpAdapter
BilibiliAdapter
DouyinAdapter
GenericFileAdapter
```

Không rải logic Douyin/Bilibili khắp code.

### Downloader strategy

Primary:

- yt-dlp subprocess/library adapter.

Fallback:

- source-specific adapter.

Session:

- Có thể đọc browser session/cookies khi người dùng đã đăng nhập.
- Cookie không lưu plain text lâu dài.
- Mọi auth data phải nằm trong OS protected storage.

### Whole-channel download

Phải checkpoint pagination.

```text
channel_scan_id
page_cursor
video_id
status
downloaded_bytes
retry_count
```

Nếu Douyin/Bilibili thay API giữa chừng, danh sách đã enumerate không bị mất.

---

## Stage 2 — Media Probe & Normalization

Dùng ffprobe lấy:

- duration;
- streams;
- codecs;
- width/height;
- SAR/DAR;
- rotation;
- FPS;
- VFR/CFR;
- time base;
- HDR/SDR;
- color space;
- audio layout;
- sample rate;
- subtitle streams;
- PTS start offset.

Không transcode source ngay nếu không cần.

Tạo:

- analysis proxy video;
- analysis mono audio;
- render metadata.

### Proxy

AI không cần đọc 4K gốc mọi bước.

Proxy có thể:

- 720p.
- giữ tỷ lệ.
- CFR phục vụ analysis.
- PTS map về timeline gốc.

Phải giữ `proxy_time -> source_time` mapping.

---

## Stage 3 — Existing subtitle discovery

Ưu tiên nguồn text theo thứ tự:

1. Soft subtitle stream trong media.
2. Subtitle metadata/platform download.
3. Speech transcript.
4. Burned-in subtitle OCR.

Điểm rất quan trọng:

**Không dịch toàn bộ chữ OCR trên khung hình.**

OCR có thể bắt:

- logo;
- tên tài khoản;
- watermark;
- biển hiệu;
- bình luận;
- title;
- menu game;
- text trang trí;
- danmaku;
- số điện thoại;
- giá;
- subtitle thật.

---

# 10. Subtitle Intelligence System

Đây là subsystem quan trọng nhất.

## 10.1. Bài toán đúng

Không phải:

> “OCR chữ trong video.”

Mà là:

> “Phát hiện mọi vùng text theo thời gian, track chúng, hiểu vai trò của từng vùng, xác định vùng nào là lời thoại, ghép với speech, rồi mới quyết định xóa/dịch/chèn.”

## 10.2. Detection representation

Mỗi detection:

```json
{
  "frame_ts": 12.520,
  "polygon": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
  "orientation_deg": -7.4,
  "text": "我不知道",
  "ocr_confidence": 0.94,
  "script": "zh-Hans"
}
```

Không dùng bbox axis-aligned duy nhất.

Quadrilateral polygon cần cho:

- chữ ngang;
- chữ dọc;
- chữ xiên;
- perspective text.

## 10.3. Temporal text tracking

OCR từng frame độc lập sẽ rất tốn và rất nhiễu.

Pipeline:

```text
scene/keyframe selection
→ text detection
→ optical/visual tracking
→ periodic OCR refresh
→ polygon smoothing
→ temporal grouping
→ text track
```

TextTrack:

```json
{
  "track_id": "txt_41",
  "start": 11.20,
  "end": 13.88,
  "polygons": [],
  "text_candidates": [],
  "motion_vector": {},
  "role": "dialogue_subtitle",
  "role_confidence": 0.96
}
```

## 10.4. Text role classifier

Các class tối thiểu:

- dialogue_subtitle
- narrator_caption
- title
- lower_third
- watermark
- username
- logo
- signage
- ui_text
- danmaku
- karaoke
- decorative
- unknown

Signals:

- vị trí;
- duration;

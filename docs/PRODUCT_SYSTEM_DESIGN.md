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
- repetition;
- font/stroke;
- motion;
- scene persistence;
- alignment với speech;
- OCR language;
- text length;
- xuất hiện theo nhịp câu nói;
- similarity với ASR;
- region history.

Không hard-code “phụ đề luôn ở dưới”.

## 10.5. Vertical/diagonal subtitles

Pipeline phải:

1. detect polygon;
2. estimate text direction;
3. rectify crop;
4. OCR rectified crop;
5. preserve polygon để removal/inpaint;
6. render Vietsub theo layout mới do tool quyết định.

Không cố chèn tiếng Việt đúng orientation nguồn nếu gây khó đọc.

Mặc định Vietsub nên ưu tiên readability.

## 10.6. Speech/OCR fusion

Đây là cách giảm việc dịch sai do trên hình có nhiều chữ.

Song song:

```text
AUDIO → ASR transcript with timestamps
VIDEO → OCR text tracks with timestamps
```

Sau đó align:

```text
ASR utterance
↔ text tracks cùng thời gian
↔ language-aware textual similarity
↔ semantic similarity
↔ appearance/disappearance timing
```

Ví dụ:

- Speech nói “我不知道”.
- OCR phát hiện:
  - “我不知道” dưới màn hình.
  - logo “爱奇艺”.
  - biển “北京站”.
- “我不知道” có temporal + semantic alignment cao → subtitle.
- Các vùng còn lại không được coi là dialogue.

### Nguyên tắc

Khi có speech rõ:

**ASR là nguồn nghĩa chính; OCR là nguồn vị trí + đối chiếu + sửa lỗi.**

Khi không có speech:

OCR text mới có thể được dịch như caption/on-screen text tùy class.

## 10.7. OCR consensus

Một subtitle tồn tại nhiều frame.

Không lấy text của một frame.

Dùng:

```text
frame OCR candidates
→ normalize
→ confidence weighted voting
→ language model correction
→ final text
```

---

# 11. Speech / ASR

Output cần word-level timing:

```json
{
  "utterance_id": "utt_12",
  "start": 8.30,
  "end": 11.21,
  "language": "zh",
  "text": "...",
  "words": [],
  "asr_confidence": 0.92
}
```

Các yêu cầu:

- VAD.
- word timestamps.
- long-form chunking.
- overlap-safe merge.
- punctuation restoration nếu model cần.
- language auto detect.
- confidence.
- hallucination guards.

ASR phải chạy trên dialogue-enhanced audio nếu separation giúp rõ hơn, nhưng cần so sánh với bản mix gốc để không mất speech.

---

# 12. Speaker Intelligence System

Đây là vấn đề lớn thứ hai.

## 12.1. Phân biệt ba khái niệm

**Voice cluster**

> Giọng A khác giọng B.

**Visible character**

> Nhân vật/khuôn mặt nào đang xuất hiện.

**Speaking character**

> Tại timestamp đó, ai thực sự đang nói.

Ba thứ này không đồng nghĩa.

## 12.2. Audio diarization

Output:

```text
0.20–2.45  speaker_voice_01
2.60–4.10  speaker_voice_02
4.11–5.02  speaker_voice_01
```

Diarization trả lời “giọng nào nói lúc nào”, nhưng không biết tên nhân vật.

## 12.3. Visual identity tracking

Video pipeline:

```text
shot detection
→ face/character detection
→ visual embedding
→ multi-object tracking
→ cross-shot identity linking
```

Kết quả:

```text
character_visual_01
character_visual_02
character_visual_03
```

Với animation/anime:

- không phụ thuộc duy nhất face detector người thật;
- cần visual embedding/object tracker;
- có thể dùng crop đầu/thân/character embedding;
- identity linking theo scene.

## 12.4. Active speaker detection

Với người/nhân vật có miệng nhìn thấy:

```text
face/character track
+ mouth motion
+ audio features
→ active speaker probability
```

Ví dụ:

```text
t=10.0
char_01 = 0.04
char_02 = 0.91
```

## 12.5. Audio–visual association graph

Tạo graph:

```text
VoiceCluster ↔ VisualCharacter
```

Edge score dựa trên:

- co-occurrence;
- active speaker probability;
- lip motion;
- temporal continuity;
- scene continuity;
- voice similarity;
- character visibility.

Solve mapping theo toàn clip, không theo từng frame.

## 12.6. Narrator/off-screen voice

Nếu speech tồn tại nhưng không có visible active speaker:

```text
role = narrator_or_offscreen
```

Không gán bừa cho nhân vật đang hiện trên màn hình.

## 12.7. Một diễn viên lồng nhiều nhân vật

Audio diarization có thể gom hai nhân vật thành cùng một voice cluster.

Khi active-speaker/visual evidence cho thấy hai visual identity khác nhau, system phải được phép tách:

```text
voice_cluster_01
  ├─ character_A
  └─ character_B
```

TTS vẫn dùng hai giọng khác nhau nếu mục tiêu là “nhân vật khác nhau → giọng khác nhau”.

## 12.8. Hai nhân vật có giọng rất giống

Không dựa duy nhất voice embedding.

Visual association tăng trọng số.

## 12.9. Overlapping speech

Không flatten thành một speaker.

Data model phải hỗ trợ:

```text
utterance_1 speaker_A 10.0–12.0
utterance_2 speaker_B 11.3–12.4
```

TTS render thành track riêng rồi mix.

---

# 13. Character Registry

Trong một video:

```json
{
  "character_id": "char_001",
  "visual_embeddings": [],
  "voice_embeddings": [],
  "gender_style": "auto",
  "age_style": "auto",
  "assigned_tts_voice": "vi_voice_07"
}
```

Tùy chọn mở rộng cho toàn series/kênh:

- Nhân vật gặp lại ở video sau dùng lại TTS voice.
- Cho phép người dùng rename:
  - char_001 → “Tiểu Minh”.
- Voice mapping được lưu ở project/channel scope.

Quick Mode tự làm hoàn toàn.

Studio Mode cho sửa.

---

# 14. Translation Engine

## 14.1. Không dịch từng subtitle độc lập

Dịch từng dòng mất context, sai:

- đại từ;
- tên người;
- giới tính;
- joke;
- thuật ngữ;
- câu nối;
- chủ thể.

Dịch theo dialogue windows:

```text
previous context
current utterances
next context
speaker ids
scene context
glossary
```

Sau đó trả output theo `utterance_id`.

## 14.2. Translation source

Dialogue:

```text
ASR normalized transcript
+ OCR verification
+ optional platform subtitle
```

Không đưa mọi OCR text vào cùng prompt.

## 14.3. Glossary

Project có:

```text
glossary.json
```

Ví dụ:

```text
character_name
place_name
catchphrase
technical_term
do_not_translate
preferred_translation
```

Có thể học dần từ correction người dùng.

## 14.4. Vietnamese subtitle constraints

Subtitle formatter phải kiểm tra:

- chars/line;
- lines/cue;
- reading speed;
- minimum duration;
- shot boundary;
- punctuation;
- orphan line;
- safe area.

Không ép câu dài vào 1.2 giây.

Nếu bản dịch dài quá:

1. rewrite ngắn hơn;
2. chia cue hợp lý;
3. kéo nhẹ timing nếu khoảng trống cho phép;
4. không làm lệch speech tùy tiện.

---

# 15. TTS / Dubbing

## 15.1. Voice casting

Mỗi character nhận một `VoiceProfile`.

```json
{
  "voice_id": "vi_07",
  "character_id": "char_001",
  "timbre": "...",
  "speaking_rate": 1.0,
  "pitch_style": "auto",
  "emotion_mode": "follow_source"
}
```

### Auto casting signals

- perceived age range;
- voice pitch;
- speaking style;
- energy;
- character continuity;
- source voice embedding;
- narration vs dialogue.

## 15.2. TTS engines phải pluggable

Interface:

```text
TtsEngine
├─ capabilities()
├─ synthesize()
├─ clone/reference mode
├─ multi_speaker()
├─ languages()
└─ healthcheck()
```

Không khóa toàn sản phẩm vào một model.

## 15.3. Duration fitting

Không được chỉ time-stretch cực mạnh.

Quy trình:

1. Generate bản tự nhiên.
2. So duration với target.
3. Nếu dài:
   - rewrite bản dịch ngắn hơn trước;
   - tăng speaking rate trong khoảng an toàn.
4. Nếu vẫn dài:
   - cho phép nhỏ hơn một phần overshoot nếu khoảng lặng kế tiếp cho phép.
5. Cuối cùng mới dùng high-quality time-stretch nhẹ.

### Guardrail

Không để:

- robot voice do 1.6x;
- kéo dài âm cuối kỳ quặc;
- cắt câu đang nói.

## 15.4. Emotion/prosody

Extract source prosody:

- energy;
- pitch contour;
- speech rate;
- pause;
- emotion class.

TTS nhận style hint nếu engine hỗ trợ.

## 15.5. Voice consistency

Một character phải giữ voice ID xuyên suốt.

Không để model tự random seed làm mỗi câu nghe thành người khác.

---

# 16. Audio Source Separation & Mix

Mục tiêu:

> bỏ/giảm thoại nguồn nhưng giữ nhạc và hiệu ứng.

Không thể giả định mọi video có clean M&E track.

Pipeline:

```text
original mix
→ speech/music/effects separation
→ dialogue attenuation/removal
→ background repair
→ Vietnamese dialogue tracks
→ ducking
→ loudness normalization
→ final mix
```

Nếu separation tạo artifact nặng:

- dùng less-aggressive removal;
- hoặc giữ voice nguồn ở gain rất thấp;
- QC đánh dấu.

### Không dùng vocal separator âm nhạc như giải pháp duy nhất

Một số model tách “vocals” được train chủ yếu cho bài hát; dialogue + SFX là bài toán khác.

Engine interface phải cho phép:

- speech/music/effects model;
- vocal separator;
- center-channel heuristic;
- fallback mix strategy.

---

# 17. Xóa sub nguồn

Ba mode:

### Smart Inpaint — mặc định chất lượng

- Dùng text polygons theo thời gian.
- Expand mask có kiểm soát.
- Temporal stabilization.
- Video inpainting.
- Kiểm tra flicker.

### Adaptive Cover — fallback

Nếu inpaint risk cao:

- overlay box/gradient/background phù hợp.
- đặt Vietsub che vùng sub nguồn.

### Keep Original

Không xóa, chỉ chèn Vietsub ở vùng khác.

Quick Mode chọn Smart Inpaint nhưng tự fallback khi cần.

## 17.1. Mask lifecycle

Mask không sinh lại độc lập mỗi frame.

```text
TextTrack polygon
→ temporal smoothing
→ motion-aware mask
→ edge expansion
→ occlusion handling
```

Nếu mask rung, video sẽ nhấp nháy rất khó chịu.

## 17.2. Không xóa nhầm chữ quan trọng

Chỉ `dialogue_subtitle` hoặc class được user chọn mới được removal.

Biển hiệu/logo/UI không được xóa chỉ vì OCR thấy chữ.

---

# 18. Render Engine

Render phải tách khỏi AI analysis.

Input:

```text
source video
subtitle removal result
dub mix
subtitle track
render profile
```

Output:

```text
final container
```

### Encoder strategy

1. Test hardware encoder.
2. Nếu pass → dùng.
3. Nếu fail → software fallback.

Không để NVENC/QSV/AMF lỗi làm fail toàn job.

### Timestamp correctness

Mọi stage phải dùng timeline chuẩn thống nhất.

Không dùng frame index làm nguồn sự thật trên VFR media.

---

# 19. Automated QC

Trước DONE chạy QC tự động.

## 19.1. Video checks

- Output mở được.
- Duration sai lệch trong tolerance.
- Không frame đen dài bất thường.
- Không freeze segment bất thường.
- Resolution đúng.
- Aspect ratio đúng.
- Không crop ngoài ý muốn.

## 19.2. Subtitle checks

- Không overlap vô lý.
- Không ra ngoài safe area.
- Không cue duration âm.
- Không text rỗng.
- Reading speed.
- Kiểm tra còn text nguồn trong vùng đã remove.
- Kiểm tra Vietsub không che mặt quá nhiều.

## 19.3. Audio checks

- Không clipping.
- Không silence toàn video.
- Dub segment coverage.
- Loudness.
- sync drift.
- voice continuity.
- background stem tồn tại.

## 19.4. Semantic checks

Sample theo:

- đầu;
- giữa;
- cuối;
- scene có nhiều người nói;
- scene confidence thấp.

So:

```text
source ASR
→ Vietnamese translation
→ ASR lại từ TTS Vietnamese
```

Back-ASR giúp bắt:

- TTS đọc sai tên;
- bỏ chữ;
- phát âm số sai;
- generation hỏng.

---

# 20. Confidence system

Mỗi segment có:

```text
asr_confidence
ocr_confidence
text_role_confidence
speaker_confidence
translation_confidence
tts_confidence
inpaint_risk
```

Final:

```text
segment_risk_score
```

Quick Mode mặc định:

- Không dừng vì uncertainty thông thường.
- Chọn phương án tốt nhất.
- Vẫn xuất video.
- Đánh dấu các đoạn nghi ngờ trong `qc_report`.

Studio Mode có thể bật:

```text
pause_on_low_confidence = true
```

---

# 21. CapCut strategy

CapCut là **adapter**, không phải core data model.

## 21.1. Output ổn định nhất

Luôn xuất:

- video;
- voice/audio stems;
- SRT;
- ASS;
- timeline.json.

CapCut Desktop hỗ trợ import SRT, nên ít nhất subtitle vẫn editable bằng đường chuẩn.

## 21.2. Direct CapCut Project

Có thể hỗ trợ bằng versioned `CapCutAdapter`.

```text
CapCutAdapter
├─ detect_installation()
├─ detect_version()
├─ compatibility()
├─ create_draft()
└─ validate_draft()
```

Không viết trực tiếp logic `draft_content.json` khắp app.

### Compatibility matrix

```text
capcut_version
adapter_version
tested
read/write
known_limitations
```

Nếu version không được xác nhận:

- Không phá job.
- Xuất `CapCut Import Pack`.

### Lý do

CapCut/JianYing draft format là implementation detail có thể thay đổi. Một vài tool cộng đồng có thể tạo draft, nhưng đây không phải API ổn định chính thức.

---

# 22. UI screens

## 22.1. Home

```text
[ Dán link video/kênh...                    ]
[ + Thêm file ] [ + Thêm folder ]

Đầu ra: C:\DubFlow\Output

[x] Vietsub
[x] Lồng tiếng Việt
[x] Giữ nhạc và hiệu ứng
[x] Tạo file CapCut

              [ BẮT ĐẦU ]
```

## 22.2. Queue

Mỗi video:

```text
Tên
thumbnail
source
stage
progress
ETA nội bộ
status
warning
```

Người dùng có thể:

- pause;
- resume;
- cancel;
- retry;
- open output.

## 22.3. Job Detail

Hiển thị timeline stage:

```text
✓ Tải video
✓ Phân tích
✓ Nhận diện thoại
● Phân vai
○ Dịch
○ Lồng tiếng
○ Xử lý phụ đề
○ Render
○ Kiểm tra
```

Không show log kỹ thuật mặc định.

Có nút:

```text
Xem chi tiết kỹ thuật
```

## 22.4. Review Center

Chỉ ưu tiên các đoạn:

- speaker confidence thấp;
- OCR/ASR conflict;
- translation unusual;
- inpaint artifact risk;
- TTS duration khó fit.

Không bắt người dùng review toàn bộ video.

## 22.5. Characters

Card:

```text
[ảnh nhân vật]
Nhân vật 01
Giọng Việt: Voice 07
[nghe thử]
```

User có thể đổi voice một lần → regenerate chỉ các segment nhân vật đó.

---

# 23. Batch / whole-channel scheduling

Không chạy mọi video cùng lúc.

Resource Scheduler quản lý:

```text
CPU slots
GPU slots
VRAM budget
disk IO budget
render slots
download slots
```

Ví dụ:

- Download 3 video song song.
- OCR 1–2 job.
- TTS batch.
- Render 1 job.
- Không load ASR + TTS + inpaint model lớn cùng lúc nếu VRAM không đủ.

### Model residency manager

```text
load ASR
process ASR queue
unload ASR
load diarization
...
```

Tối ưu theo hardware tier.

---

# 24. Long video architecture

“Không giới hạn thời lượng” = không hard-limit.

Yêu cầu bắt buộc:

- chunked decode;
- chunked ASR;
- chunked OCR;
- chunked inpaint;
- chunked TTS generation;
- chunked render khi thích hợp;
- on-disk artifacts;
- bounded RAM;
- resumable stages.

Không lưu toàn bộ frames của video 6 giờ vào RAM.

### Chunk overlap

Các chunk temporal cần overlap để tránh mất:

- subtitle ở boundary;
- utterance cắt giữa;
- speaker identity;
- scene transition.

Sau đó merge bằng canonical timeline.

---

# 25. Persistence

SQLite WAL.

Tables tối thiểu:

```text
jobs
job_inputs
stages
artifacts
downloads
media_info
scenes
text_tracks
utterances
speaker_clusters
visual_characters
speaker_character_edges
characters
translations
tts_segments
render_outputs
qc_findings
model_versions
app_settings
```

Không serialize toàn bộ state thành một JSON khổng lồ.

`job_manifest.json` vẫn được xuất để portability/debug.

---

# 26. Worker protocol

Worker nhận JSON line:

```json
{
  "type": "run_stage",
  "job_id": "job_123",
  "stage": "ocr_tracks",
  "input_manifest": "..."
}
```

Event:

```json
{
  "type": "progress",
  "job_id": "job_123",
  "stage": "ocr_tracks",
  "completed": 721,
  "total": 1200
}
```

Checkpoint:

```json
{
  "type": "checkpoint",
  "artifact_id": "artifact_991"
}
```

Failure:

```json
{
  "type": "failure",
  "code": "GPU_OOM",
  "retryable": true,
  "context": {}
}
```

Rust supervisor quyết định retry/fallback, không để Python worker tự loop vô hạn.

---

# 27. Error taxonomy

Không dùng lỗi generic “Something went wrong”.

Ví dụ:

```text
DOWNLOAD_AUTH_REQUIRED
DOWNLOAD_SITE_CHANGED
DOWNLOAD_RATE_LIMIT
MEDIA_CORRUPT
MEDIA_CODEC_UNSUPPORTED
DISK_FULL
GPU_DRIVER_UNSUPPORTED
GPU_OOM
MODEL_LOAD_FAILED
MODEL_CHECKSUM_FAILED
ASR_FAILED
OCR_FAILED
DIARIZATION_FAILED
INPAINT_FAILED
TTS_FAILED
RENDER_FAILED
CAPCUT_INCOMPATIBLE
UPDATE_ROLLBACK
```

Mỗi error có:

- human_message;
- technical_message;
- retry policy;
- fallback policy;
- suggested action.

---

# 28. Self-healing rules

Ví dụ GPU OOM:

```text
Attempt 1: clear model cache
Attempt 2: reduce batch
Attempt 3: reduce chunk size
Attempt 4: unload concurrent model
Attempt 5: lower-memory model
Attempt 6: CPU fallback
```

Không retry cùng một cấu hình 10 lần.

Downloader:

```text
normal
→ refresh extractor
→ refresh session
→ adapter fallback
→ mark auth/site-change
```

Render:

```text
hardware encode
→ alternate hw encoder
→ software encoder
```

---

# 29. Update safety

Updater tuyệt đối không:

- kill job đang render rồi xóa temp;
- migrate DB không rollback;
- update model đang được worker mmap/load;
- thay engine contract nhưng không thay app.

Mỗi release có compatibility:

```text
app_version
engine_api_version
db_schema_version
model_manifest_version
```

Migration phải:

```text
backup DB
→ migrate
→ validate
→ commit
```

---

# 30. Security / privacy

Local-first:

- Media không cần upload cloud.
- Logs mặc định không chứa raw cookies.
- Cookies/token dùng OS credential storage.
- Temp auth file xóa sau use.
- App update phải ký số.
- Model/download manifest có checksum.
- Downloader chạy quyền user thường.
- Không yêu cầu admin trừ installer thực sự cần.
- Local IPC không expose ra LAN.

---

# 31. Observability

Mỗi job sinh structured logs.

User-facing:

```text
DubFlow finished 23/24 videos.
1 video cần đăng nhập lại Douyin.
```

Developer:

```text
trace_id
job_id
stage_id
worker_id
model_id
model_version
elapsed
memory_peak
vram_peak
retry_count
```

Có nút:

```text
Export Diagnostic Bundle
```

Bundle loại bỏ cookie/token.

---

# 32. Tech stack đề xuất

## Desktop

- Tauri 2
- Rust
- React
- TypeScript
- shadcn/ui

## State

- SQLite WAL
- SQL migrations
- JSON manifests cho artifact interchange

## Python

- Python runtime app-owned
- uv lockfile trong development/build pipeline
- PyTorch / ONNX Runtime tùy module

## Media

- FFmpeg / ffprobe

## Download

- yt-dlp adapter
- source adapters cho Douyin/Bilibili

## ASR

- faster-whisper / WhisperX-style alignment architecture

## OCR

- PaddleOCR family làm baseline mạnh cho Chinese/multilingual scene text
- custom temporal text tracker quanh OCR

## Diarization

- pyannote-style offline diarization baseline
- abstraction để thay engine

## Active speaker

- audio-visual active speaker detector kiểu TalkNet hoặc model tương đương

## TTS

Engine plugin. Candidate cần benchmark thực tế:

- Fish Audio/Fish Speech generation phù hợp multi-speaker/multilingual nếu license/weights phù hợp mục tiêu dự án.
- F5-TTS là candidate cho voice reference/multi-speaker.
- Có thể thêm engine khác mà không đổi pipeline.

## Audio separation

- speech/music/effects engine nếu có model phù hợp;
- Music-Source-Separation-Training / RoFormer-family candidate;
- Demucs chỉ nên là một backend/fallback, không là abstraction duy nhất.

## Scene

- PySceneDetect hoặc detector nội bộ tương đương.

## Inpaint

- video inpainting engine plugin.
- Chọn model sau benchmark subtitle-specific dataset, không khóa kiến trúc ở bản thiết kế.

---

# 33. Repo layout đề xuất

```text
dubflow-local/
├─ AGENTS.md
├─ README.md
├─ apps/
│  └─ desktop/
│     ├─ src/
│     └─ src-tauri/
├─ crates/
│  ├─ job-supervisor/
│  ├─ updater/
│  ├─ artifact-store/
│  ├─ media-contracts/
│  └─ diagnostics/
├─ engine/
│  ├─ pyproject.toml
│  ├─ uv.lock
│  └─ dubflow/
│     ├─ worker/
│     ├─ download/
│     ├─ media/
│     ├─ scene/
│     ├─ asr/
│     ├─ ocr/
│     ├─ text_fusion/
│     ├─ diarization/
│     ├─ visual_identity/
│     ├─ active_speaker/
│     ├─ translation/
│     ├─ tts/
│     ├─ separation/
│     ├─ inpaint/
│     ├─ subtitle/
│     ├─ mix/
│     ├─ render/
│     ├─ qc/
│     └─ capcut/
├─ contracts/
├─ models/
├─ fixtures/
├─ tests/
├─ packaging/
├─ scripts/
└─ docs/
```

---

# 34. Agent development rules

1. Không code UI gọi AI trực tiếp; mọi module qua contract.
2. Không merge model mới nếu chưa benchmark.
3. Mọi stage phải resumable.
4. External dependency phải nằm sau adapter.
5. Low confidence là dữ liệu bình thường, không phải exception.
6. Không chỉ test clip ngắn sạch; phải có long-form và corrupted media.

Benchmark set phải có Douyin portrait, Bilibili landscape, vertical/diagonal subtitles, nhiều speaker, narrator/offscreen, overlap, animation/live-action, text-heavy, music-heavy và source nén xấu.

---

# 35. Test matrix

Phải phủ:

- aspect ratio: 9:16, 16:9, 1:1, 4:3, ultrawide, rotation metadata;
- resolution: 480p→4K;
- timing: 23.976/25/29.97/30/60/VFR;
- subtitle: bottom/top/center/vertical/diagonal/animated/karaoke/multi-text;
- speaker: 1/2/4+, similar voices, same actor multiple characters, narrator, overlap, crowd;
- content: live action, anime, 3D animation, gameplay, slideshow, screen recording;
- audio: clean, music loud, noise, echo, telephone, stereo, mono.

---

# 36. Golden evaluation dataset

Mỗi test clip cần ground truth cho transcript, subtitle polygons/role, speaker segments, character identity, speaker-character mapping, Vietnamese translation và expected TTS voice.

Metrics tối thiểu:

```text
ASR WER/CER
OCR CER
subtitle detection precision/recall
text-role F1
speaker DER
speaker-character association accuracy
translation human score
subtitle timing error
TTS intelligibility
render A/V sync
inpaint artifact score
pipeline success rate
resume success rate
```


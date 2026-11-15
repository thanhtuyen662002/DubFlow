# Gói chuẩn bị REAL-DEV / X2 ($0)

Epic #637 · 2026-11-15 · chỉ thị "CONTROLLED SAFETY MERGE & NEXT-PHASE PREPARATION" §7–§8.

**Trạng thái: PREPARATION ONLY. Dữ liệu NOT READY ở cả hai trạng thái (READY FOR PAID SCRIPT DRAFT, READY FOR EVALUATION).** Cập nhật 2026-11-15 06:50Z: tách hai trạng thái theo chỉ thị Owner Merge Readiness Final Check.

- Chưa có nguồn nào được xác nhận quyền.
- Chưa có người viết lời dẫn, người gắn nhãn hay người phân xử.
- Chưa có câu hoặc nhãn nào.
- Chi phí AI: **$0**. Không gọi API, không tải model mới, không chạy Vision.
- Ngân sách X2 $6/$19 **chưa được duyệt**.
- Planner, schema và UI không đổi.

Gói này bổ sung (không thay thế) các tài liệu cùng thư mục `docs/mastering/x2/`:

- `X2_EVALUATION_DATASET_PROTOCOL.md`: kích thước, thành phần, quy trình gắn nhãn;
- `X2_POC_DESIGN_V2.md`: các nhánh A/B/C/D, chỉ số, tiêu chí, ngân sách;
- `AUTOMATION_EVALUATION_INTEGRITY.md`: hợp đồng X0, khóa, chống rò rỉ.

Khi có mâu thuẫn, ba tài liệu đó được ưu tiên.

## 0. Nội dung

| File | Dùng để |
|---|---|
| `dataset-manifest.template.json` | Mẫu manifest DEV + đề cương HOLDOUT. Mọi trường quyền, sha và khóa để `null` |
| `tests/mastering/x2/validate_manifest.py` | Không có tham số: chỉ kiểm cấu trúc và rò rỉ. `--stage draft --split REAL-DEV` kiểm **READY FOR PAID SCRIPT DRAFT**. `--stage eval --split …` kiểm **READY FOR EVALUATION** (§3.1). Template hiện cho: `STRUCTURE OK`, NOT READY ở cả hai trạng thái |
| `tests/mastering/x2/lock.py` | `write <file> <lockfile>` / `verify`: khóa sha256, chỉ ghi một lần |
| `tests/mastering/x2/agreement.py` | Cohen κ trên lớp phù hợp hình, đồng thuận khoảng (chồng ≥ 50 %), danh sách câu cần phân xử |

Các công cụ chỉ dùng thư viện chuẩn Python, không gọi mạng. Đã chạy thử trên dữ liệu giả.

## 1. Nguồn tiềm năng (TẤT CẢ UNVERIFIED)

Danh sách dưới đây chỉ là **loại nguồn** cần tìm, chưa phải file cụ thể. Mỗi file phải qua quy trình ở §2 trước khi được đưa vào manifest.

| Mã dự kiến | Tập | Loại | Vì sao | Nơi có thể tìm (phải kiểm giấy phép TỪNG FILE) |
|---|---|---|---|---|
| d-s1 | DEV | Tư liệu có giọng đọc, en | Lời nói gần hình, có tên riêng/số/thời gian | Tác phẩm của cơ quan chính phủ Mỹ (phạm vi công cộng), kho Prelinger |
| d-s2 | DEV | Ít thoại hoặc vlog, zh/vi | Lời nói không mô tả hình; đường không transcript | Nguồn Owner đã có quyền dùng (chỉ giữ trên máy) hoặc CC-BY |
| h-s1…h-s6 | HOLDOUT | ≥ 3 thể loại: sản phẩm/review có thoại; tin/tư liệu; vlog/hướng dẫn; footage gần như không lời | Đa dạng, không chỉ phim chiến tranh | Như trên, cộng kho CC0 |

Không dùng:

- W3, Gate S REAL-DEV, hay bất kỳ phim nào đã dùng để chỉnh code (#836/#837/#842/#843). Đó là dữ liệu dev cũ.
- Nguồn có người thật riêng tư khi chưa có đồng ý.
- Nguồn mà giấy phép không cho gửi tới bên thứ ba. Khung hình gửi Vision là gửi ra ngoài.

## 2. Quy trình xác nhận quyền

Làm cho từng nguồn, do Owner hoặc người được Owner giao:

1. Ghi URL gốc, tác giả, giấy phép (CC0 / CC-BY x.y / phạm vi công cộng / quyền của Owner) và ảnh chụp trang giấy phép kèm ngày.
2. Kiểm: được sửa đổi? được dùng cho đánh giá? được gửi tới API bên thứ ba (`third_party_processing_allowed`)? có người thật cần đồng ý?
3. Tải file về máy kiểm thử, tính sha256, đo thời lượng, ngôn ngữ, tỉ lệ có lời nói (ASR local, $0).
4. Điền `license`, `rights_confirmed_by`, `rights_confirmed_at`, `third_party_processing_allowed`, `sha256` trong manifest.
5. Nếu thiếu bất kỳ mục nào ở bước 1–4, nguồn bị loại. Không có ngoại lệ "tạm dùng".

Agent **không** được tự điền các trường quyền. `validate_manifest.py --stage draft` sẽ từ chối khi các trường này là `null`.

## 3. REAL-DEV: 20–30 câu từ 2 nguồn

### 3.1 Hai trạng thái sẵn sàng riêng biệt

| Trạng thái | Nghĩa | Cần có | **Chưa** cần |
|---|---|---|---|
| **READY FOR PAID SCRIPT DRAFT** | Đầu vào của bước AI viết nháp lời dẫn có cue đã là dữ liệu thật và hợp lệ | Đủ số nguồn **của split đang xét**: DEV cần 2 nguồn DEV hợp lệ, HOLDOUT cần 4–6 nguồn HOLDOUT. READY của DEV **không** chờ nguồn HOLDOUT; chỉ cần không trùng nguồn. Mỗi nguồn có: quyền do người có tên xác nhận kèm bằng chứng giấy phép; đường dẫn, sha256, thời lượng; `third_party_processing_allowed: true`; file cue từ ASR local (`cues_sha256`, `cues_method: local-asr`, `asr_status: ok`, `cue_count`, `asr_model`; §3.3). Không trùng nguồn giữa DEV và HOLDOUT | Lời dẫn, cue đã khóa, nhãn người |
| **READY FOR EVALUATION** | Được phép đo split này | Mọi điều của trạng thái trên. Thêm: lời dẫn/cue đã khóa (sha256, `locked_at`, method, writer); đã ghi số cue không hợp lệ bị từ chối; đúng số câu; nhãn **người** (không phải AI-judged) đã khóa; quy tắc chấm khóa vào commit. HOLDOUT thêm: 2 người gắn nhãn + người phân xử riêng, κ, tham số khóa vào commit, đủ tỉ lệ loại câu, hard negative, loại lỗi | |

Quy tắc chung cho cả hai trạng thái:

- Đạt một trạng thái chỉ nói về **dữ liệu**. Nó không cho phép chi tiền. Mỗi lần chi vẫn cần Owner phê duyệt riêng (§8).
- Nguồn, câu hoặc nhãn có `synthetic: true` không bao giờ được tính. Nhãn AI-judged không phải nhãn người.
- Không ai được điền dữ liệu giả để qua kiểm tra. Template này đang NOT READY ở cả hai trạng thái, và đó là trạng thái đúng.

### 3.2 Các bước

| Bước | Ai | Đầu ra |
|---|---|---|
| 1. Chốt d-s1, d-s2 qua §2; chạy ASR local để có file cue ($0) | Owner | Manifest có quyền + sha + cue → **READY FOR PAID SCRIPT DRAFT** |
| 2. Viết lời dẫn có cue từ tiêu đề, ghi chú, transcript. Không xem footage. Nếu dùng AI viết nháp: cần **phê duyệt chi phí riêng**. Nếu người viết: $0 | AI (đã duyệt) hoặc người viết A (`writer_saw_footage: false`) | 15–22 câu có footage |
| 3. Viết câu không có footage + hard negative từ mô tả chủ đề | Người viết B (khác A) | 5–8 câu, ≥ một nửa là hard negative |
| 4. Khóa lời dẫn: `lock.py write narration.json narration.lock` | Người giữ khóa | sha256 |
| 5. Gắn nhãn (DEV cho phép một người; khuyến nghị hai người để tập dượt quy trình HOLDOUT) | Người gắn nhãn ≠ người viết | labels.json |
| 6. Khóa nhãn DEV và commit quy tắc chấm | | → **READY FOR EVALUATION** (DEV). Sau khóa vẫn được sửa nhãn khi phát hiện sai, nhưng phải ghi lại và khóa lại |

Phân bổ gợi ý cho 25 câu:

- 9 trực tiếp (gồm ≥ 4 câu `factual`: tên, số, thời gian, chữ trên hình);
- 5 khái quát;
- 5 minh họa;
- 6 không có footage (≥ 3 hard negative).

Phải có ít nhất một câu cho mỗi loại lỗi ở §5.

DEV **được** dùng để phân tích lỗi và chỉnh harness, prompt, cửa sổ W. Mọi tham số chốt trên DEV được ghi vào commit trước khi động tới HOLDOUT.

### 3.3 Nguồn không lời và cách đo `speech_ratio`

**File cue.** Mỗi nguồn có một file JSON do `tests/mastering/x2/asr_cues.py` ghi ra (ASR local, `local_files_only`) và lưu nguyên:

```json
{"asr_model": "faster-whisper-small", "params": {"vad_filter": true, "beam_size": 5, "...": "..."},
 "versions": {"faster-whisper": "...", "ctranslate2": "..."}, "media_sha256": "...", "duration_ms": 248000,
 "asr_status": "ok",
 "cues": [{"start_ms": 1200, "end_ms": 3400, "text": "..."}],
 "rejected": []}
```

Thời điểm cue được chép từ model (giây → mili giây nguyên), không sửa. Mỗi cue bắt đầu ở **từ đầu tiên** và kết thúc ở **từ cuối cùng** (`word_timestamps`), không theo khung của segment Whisper, vì khung segment hay trùm cả khoảng im lặng. Track A ngày 2026-11-15 cho thấy: 3 cue phủ 25,9 s trong khi chỉ có 12,4 s lời nói, `speech_ratio` 0,432 thay vì 0,206. Khung segment được giữ trong `segment_start_ms`/`segment_end_ms` để kiểm tra. Khi model không trả về thời điểm từng từ, cue dùng khung segment và ghi `timing: "segment"`. Cue **không thể có** với file này (kết thúc ≤ bắt đầu, bắt đầu < 0, hoặc kết thúc sau `duration_ms` đo bằng ffprobe) được chuyển vào `rejected` kèm lý do. Cue đó được giữ lại để kiểm tra, không tính vào `cue_count` và `speech_ratio`, và được đếm ở `cue_rejected_count`. `rejected` khác với `narration.cue_rejections` (cue hợp lệ về thời gian nhưng ảo về nội dung, xem bên dưới).

Khi ASR lỗi, file không đọc được (ffprobe lỗi) hoặc video không có luồng âm thanh, file cue vẫn được ghi với `asr_status: "failed"` và lý do, **không** có khóa `cues`, nên không bao giờ qua được như nguồn không lời.

`tests/mastering/x2/probe_source.py` đo `sha256` (đọc theo khối), `duration_ms` và codec bằng ffprobe.

**`cue_count`** là số phần tử của `cues`.

**`speech_ratio`** được **tính** từ file cue, không nhập tay:

- công thức: tổng độ dài hợp các khoảng `[start_ms, end_ms]` (gộp chỗ chồng nhau) chia cho `duration_ms` của nguồn;
- **không làm tròn** khi lưu. Làm tròn 3 chữ số từng biến một cue ngắn thật (ví dụ 100 ms trong nguồn 4 giờ) thành `0.000`, tức "không lời". Chỉ được làm tròn khi hiển thị;
- đây là số đo xấp xỉ của "phần có lời theo ASR", không phải nhãn người. `0` nghĩa là lần chạy này **không phát hiện** lời nói, không chứng minh nguồn im lặng.

**Nguồn không lời.** Một nguồn được coi là không lời khi và chỉ khi:

- ASR local chạy **xong** (`asr_status: ok`) với model và tham số đã ghi;
- ASR trả về **0 cue**.

Khi đó `speech_ratio` bằng 0 theo đúng công thức, không phải vì ai sửa số. File cue rỗng (`"cues": []`) vẫn được lưu và có `cues_sha256`.

Không được:

- Sửa `speech_ratio` thành 0, hoặc xóa cue để nguồn trở thành "không lời". Nếu ASR sinh cue ảo trên đoạn nhạc hoặc im lặng, nguồn đó là **ít lời** (`cue_count > 0`). Cue ảo được ghi vào danh sách từ chối (`narration.cue_rejections`) kèm lý do. File cue gốc và `speech_ratio` giữ nguyên.
- Ghi ASR lỗi, bị dừng giữa chừng, thiếu model hoặc thiếu file thành `asr_status: ok`. Những trường hợp đó ghi `failed`, và nguồn không đạt READY cho tới khi chạy lại xong.
- Tạo cue hoặc transcript giả, hay ghi `cues_method` khác `local-asr` cho cue không do ASR local sinh ra.

`validate_manifest.py --verify-files <thư mục>` tính lại từ file thật rồi so với manifest:

- `sha256` của file nguồn (`path`); thiếu file thì báo lỗi;
- `cues_sha256`, `cue_count`;
- mỗi cue phải có `0 ≤ start_ms < end_ms ≤ duration_ms`, là số mili giây nguyên;
- `speech_ratio` (sai lệch tối đa 1e-9).

### 3.4 Tiêu chí chọn REAL-HOLDOUT (ghi trước, 2026-11-15)

Ghi lại **trước** lần đo DEV đầu tiên. Không được đổi các tiêu chí này dựa trên kết quả DEV của bất kỳ nhánh nào.

- 4–6 nguồn, không trùng nguồn DEV, W3, Gate S REAL-DEV hay bất kỳ phim nào đã dùng để chỉnh hệ thống.
- ≥ 3 thể loại trong 4 nhóm sau:
  - sản phẩm/review có thoại (zh/vi);
  - tin/tư liệu có giọng đọc (en);
  - vlog/hướng dẫn có người nói;
  - footage gần như không lời.
- Có ít nhất một nguồn trong nhóm "gần như không lời".
- Mỗi nguồn dài 3–30 phút.
- Quyền xác nhận theo §2, giống hệt DEV.
- Chọn nguồn theo thứ tự tìm được trong mỗi nhóm cho tới khi đủ, không chọn lọc theo việc nhánh nào sẽ thắng.
- Nếu chọn nguồn HOLDOUT sau khi đã có kết quả DEV: người chọn **không** xem bảng kết quả theo nhánh của DEV, và ghi lý do loại từng nguồn bị bỏ.

## 4. Đề cương REAL-HOLDOUT (chưa dựng)

- ≥ 100 câu sau phân xử (mục tiêu 120), từ 4–6 nguồn **không trùng** nguồn DEV (tách theo video).
- Tỉ lệ tối thiểu theo `sentence_mix_min` trong template:
  - trực tiếp 35 %;
  - khái quát 20 %;
  - minh họa 20 %;
  - không có footage 20 %, trong đó ≥ 50 % là hard negative.
- Hai người gắn nhãn độc lập + một người phân xử (§6).
- Khóa sha256 **trước** phép đo:
  - manifest nguồn;
  - lời dẫn;
  - nhãn;
  - quy tắc chấm;
  - commit code đánh giá.
- Đo **một lần**. Không dùng HOLDOUT để chọn threshold, prompt, W hay heuristic. Nếu phải chỉnh sau khi đã nhìn HOLDOUT thì HOLDOUT đó trở thành DEV và cần một HOLDOUT mới.

## 5. Hướng dẫn gắn nhãn

Mỗi câu có hai trục độc lập.

**Trục 1: phù hợp hình** (một nhãn cho mỗi khoảng nguồn, và đánh dấu MỌI khoảng chấp nhận được, độ phân giải ≤ 1 s):

| Nhãn | Khi nào |
|---|---|
| DIRECT | Cảnh thể hiện đúng điều câu nói: đúng thực thể, đúng sự kiện, đúng hành động, và đúng thời điểm nếu câu nêu |
| ILLUSTRATIVE | Cảnh hợp chủ đề, dùng làm B-roll được, nhưng không chứng minh câu |
| MISMATCH | Có vẻ liên quan (cùng từ, cùng chủ đề) nhưng sai chi tiết. Ghi loại lỗi ở dưới. Khoảng MISMATCH **không** phải khoảng chấp nhận được |
| NONE | Không có cảnh nào trong nguồn chấp nhận được |
| UNKNOWN | Không quyết được. Bắt buộc ghi lý do; câu này đi phân xử |

**Loại lỗi MISMATCH** (#844; bắt buộc ghi cho mọi khoảng MISMATCH và mọi hard negative):

| Mã | Ví dụ |
|---|---|
| `entity_mismatch` | Câu nói "Thượng Hải", cảnh là Bắc Kinh; sai sản phẩm, sai người |
| `event_mismatch` | Câu nói "lễ khánh thành", cảnh là thi công |
| `time_mismatch` | Câu nói "năm 1975", cảnh rõ là hiện đại; "ban đêm" mà cảnh ban ngày |
| `action_mismatch` | Câu nói "cất cánh", cảnh là hạ cánh |
| `factual_number` | Số trên hình khác số trong câu |
| `title_card` | Câu khớp chữ trong thẻ tiêu đề/phụ đề, nhưng hình không thể hiện điều đó |
| `same_words_different_scene` | Lời nói trong nguồn trùng từ với câu, nhưng hình lúc đó là cảnh khác (người nói, cảnh chuyển). Đây đúng là rủi ro transcript-as-evidence của #836 |

**Trục 2: xác minh claim:**

- `factual`: câu có tên riêng, số, thời gian, hoặc chữ cần thấy trên hình?
- `claim_visible`: claim đó có hiện trên hình trong khoảng đã đánh dấu? (có / không / một phần)

**Hard negative** là câu không có footage nhưng gần giống cảnh có thật: cùng chủ đề, khác đúng một chi tiết (loại lỗi ghi theo bảng trên).

Quy tắc chung:

- Xem ở tốc độ bình thường.
- Không xem kết quả của bất kỳ phép chạy nào.
- Không dùng AI để gợi ý nhãn. Nhãn AI chỉ có thể có ở DEV và phải ghi `AI-judged`.

## 6. Hai người gắn nhãn + phân xử

1. Người gắn nhãn 1 và 2 làm độc lập, không trao đổi. Không ai trong họ là Owner, người viết lời dẫn, hay agent.
2. `python -I tests/mastering/x2/agreement.py labels1.json labels2.json` cho κ và danh sách bất đồng. Đồng ý = cùng nhãn trục 1 và các khoảng chồng ≥ 50 %.
3. Người thứ ba phân xử các câu bất đồng. Câu không phân xử được bị loại và **được đếm, báo cáo**.
4. κ < 0,6 trên HOLDOUT → kết quả X2 là INCONCLUSIVE (theo POC v2 §4).
5. Khối lượng ước tính khoảng 15–16 giờ công (giao thức §5).

## 7. Khóa và chống rò rỉ

- Thứ tự khóa: nguồn → lời dẫn → nhãn → quy tắc chấm + commit code đánh giá → chạy.
- Mỗi khóa ghi bằng `tests/mastering/x2/lock.py write`. File khóa không được ghi lại (công cụ từ chối). Trước khi chạy, `verify` tất cả.
- Không trùng nguồn giữa DEV và HOLDOUT; `validate_manifest.py` kiểm điều này.
- Người viết không xem footage; người gắn nhãn không phải người viết. `validate_manifest.py` kiểm các cờ khai báo này, nhưng không thể kiểm hành vi thật, nên cần chữ ký của từng người.
- Repo chỉ giữ code, schema và số tổng hợp. Lời dẫn, nhãn, khung hình, transcript và câu trả lời Vision chỉ giữ trên máy kiểm thử.
- Không dùng GT để chọn cue. Cue chỉ đến từ bước viết lời dẫn.

## 8. Tiêu chí go/no-go cho X2

Chi phí DEV ($6, chưa duyệt) chia làm hai lần xin, mỗi lần gắn với một trạng thái ở §3.1.

**1a. Xin phê duyệt chi phí cho bước AI viết nháp lời dẫn DEV.** Tất cả phải đúng:

1. `validate_manifest.py --stage draft --split REAL-DEV` không báo lỗi (**READY FOR PAID SCRIPT DRAFT**).
2. Prompt và schema cue đã commit. Harness từ chối cue không tồn tại.

**1b. Xin phê duyệt chi phí cho phép đo DEV (Vision, 4 nhánh).** Tất cả phải đúng:

1. `validate_manifest.py --stage eval --split REAL-DEV` không báo lỗi (**READY FOR EVALUATION**).
2. Có người viết B (câu không footage) và ít nhất một người gắn nhãn, đều có tên.
3. Owner đã đặt giới hạn chi tiêu trên workspace/key riêng bằng đúng trần (lớp 1 của POC v2 §6).
4. Harness có lớp 2: đọc `usage` của mọi response, dừng khi thiếu `usage` hoặc khi sắp vượt trần. Đã kiểm bằng transport giả ở $0.
5. Bốn nhánh A/B/C/D dùng cùng chính sách an toàn (#836/#837/#842/#843 đã merge). Transcript không tạo READY ở mọi nhánh.

Mục 3 và 4 ở trên áp dụng cho cả 1a và 1b.

**Điều kiện để xin phê duyệt lần 2 (HOLDOUT, trần $19):**

- Báo cáo DEV xong.
- Tham số đã khóa trong commit.
- Viết nháp HOLDOUT (nếu dùng AI): `--stage draft --split REAL-HOLDOUT` đạt, rồi xin duyệt riêng.
- Đo HOLDOUT: `--stage eval --split REAL-HOLDOUT` đạt (≥ 100 câu, 2 người gắn + phân xử, κ, tham số và quy tắc chấm đã khóa), và mọi khóa đã `verify`.

**Đọc kết quả** (giữ nguyên POC v2 §4):

- **Tín hiệu tích cực:** C hơn A ≥ 10 điểm % recall@5 **và** hơn D có ý nghĩa; severe escape và no-footage false assignment của C không cao hơn A.
- **Bác bỏ:** vùng cue chứa khoảng chấp nhận được ở < 50 % câu có cue; hoặc C không hơn D; hoặc severe escape tăng.
- **INCONCLUSIVE:** holdout < 100 câu, κ < 0,6, hoặc chạm trần chi phí.

Ngoài ra phải báo riêng tỉ lệ lỗi theo từng loại MISMATCH (§5). Một nhánh làm tăng `same_words_different_scene` hoặc `entity_mismatch` bị coi là tăng severe, kể cả khi recall tăng.

Một kết quả tích cực chỉ cho phép **đề xuất** bước tiếp theo. Nó không thay đổi Gate B/D, không mở Documentary Phase 4, không phải release.

## 9. Trạng thái hiện tại

| Mục | Trạng thái |
|---|---|
| Manifest template + công cụ | Xong, đã chạy thử |
| Nguồn DEV/HOLDOUT | Chưa có nguồn được xác nhận. d-s1 đề xuất: USGS "Yellowstone Monthly Update" (trang ghi Public Domain, không credit nhạc). *The River*/*Plow* chưa được Owner duyệt vì hồ sơ NARA còn ghi hạn chế. d-s2 chờ footage của Owner. Danh sách ứng viên và gói xác nhận giữ ngoài repo, trong thư mục dự án (2026-11-15) |
| Xác nhận quyền | Chờ Owner |
| Người viết / người gắn nhãn / người phân xử | Chưa có |
| Lời dẫn, nhãn, khóa | Chưa có |
| READY FOR PAID SCRIPT DRAFT (DEV / HOLDOUT) | **NOT READY** / **NOT READY** (thiếu nguồn có quyền và cue) |
| READY FOR EVALUATION (DEV / HOLDOUT) | **NOT READY** / **NOT READY** |
| Nguồn không lời / DEV vs HOLDOUT | Validator đòi `asr_status`, `cue_count`, `asr_model`; DEV không cần nguồn HOLDOUT. Test `tests/mastering/x2/test_validate_manifest.py` |
| Gói xác nhận quyền | Giữ ngoài repo (thư mục dự án), chờ người có thẩm quyền |
| Baseline từ số đo cũ | Giữ ngoài repo (thư mục dự án, 2026-11-15) |
| Ngân sách X2 | Chưa duyệt |
| **REAL-DEV PREPARATION** | **PARTIAL** (giao thức và công cụ sẵn; dữ liệu và con người chờ) |

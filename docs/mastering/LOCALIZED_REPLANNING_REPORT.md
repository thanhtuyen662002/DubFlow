# Đổi kế hoạch cục bộ: giữ quyết định của người (Workstream A, P2-N5)

Epic #637 · 2026-11-12 · chỉ thị "DUBFLOW — ASSISTED PRODUCTION STABILIZATION & DOCUMENTARY PILOT ENTRY GATES" · mốc rc10 `a4ec540` · PR #782 (issue #781).

## 1. Kết luận

**Workstream A (P2-N5): ĐẠT trên Windows rc15** (`6b1a0cf`, chỉ CPU, candidate).

| Kịch bản | rc15 |
|---|---|
| W1 Bán hàng: duyệt 4, Ghim / Bỏ / Thay câu khác | giữ 4/4, huỷ 0 ở cả 3 bước; chỉ câu bị sửa được lập lại |
| W2 Tổng hợp: bỏ cảnh ở i1 | dựng 1 phần (i1), dùng lại 7; giữ 4/4, huỷ 0; HELD_FRAME 0 ở 8 phần; xuất "Kiểm tra cuối: đạt." |
| W5 trên W2: ghim i3.u2 | dựng 0, dùng lại 8; 0 INVALIDATED; i1 giữ khoảnh khắc |

Đi tới đây cần năm bản candidate (rc11–rc15) và PR #782, #797, #798, #799, #803, #804. Chi tiết từng bản ở §6. Còn mở (không chặn A): mảnh cảnh không dùng không bỏ được từ bảng duyệt, rồi quay lại i1 khi dựng lại (#805, §8), "Còn N câu…" vẫn hiện sau khi duyệt hết (P3-N21), "Tạo 2 video" khi có 3 kịch bản (P3-N13).

## 2. Lỗi (tái hiện trên rc10)

P2-N5: một thao tác của người trên **một** câu (ghim, bỏ cảnh, thay cảnh) làm planner lập lại cả phần. Các câu khác đã duyệt có thể nhận cảnh khác, và lời duyệt của chúng bị huỷ dù người dùng không đụng tới.

- rc10, Windows (§1b bước 6): 2 trong 5 lời duyệt bị huỷ sau một lần ghim.
- rc10, Tổng hợp (§2 bước 3): bỏ một cảnh ở i1 làm cảnh đó được dời sang phần kết, huỷ lời duyệt của `outro.u1`.

Đo lại bằng 300 lần đổi kế hoạch ngẫu nhiên cho mỗi chế độ, bằng một script đo dùng cùng bộ sinh dữ liệu với test A5 (bản thân test A5 chạy 150 lần mỗi chế độ, 300 lần tổng):

| Chế độ | Lời duyệt | Bị huỷ mà tránh được (rc10) | Câu bị lập lại (rc10) |
|---|---|---|---|
| reorder | 1796 | 233 | 663 |
| forward-only | 1796 | 398 | 842 |

"Tránh được" nghĩa là câu bị huỷ không dùng cảnh nào mà thao tác của người đụng tới.

## 3. Thiết kế

### 3.1 Lời duyệt là ràng buộc

- Duyệt một câu lưu đúng các mảnh cảnh đang hiển thị (`pieces`: shot id, offset, độ dài, cờ lặp) và `basis` (băm lời câu và vật liệu nguồn).
- Lần lập kế hoạch sau nhận các câu đã duyệt qua `Edits.keep`:
  - câu đã duyệt được lấp **trước** các câu khác, chỉ sau câu được ghim;
  - cảnh của nó được giữ chỗ, không câu nào khác lấy được;
  - ở chế độ forward-only, không câu nào chạy qua cảnh đã giữ.
- Ghim thắng giữ: ghim một cảnh đang thuộc câu đã duyệt là một xung đột có ghi lại (`pinned_elsewhere`).
- `keep` **không** thuộc định danh kế hoạch (`to_dict`). Chỉ duyệt thì không bao giờ lập lại hay dựng lại.

### 3.2 Mỗi lần huỷ có nguyên nhân thật

`Review._reconcile` so lời duyệt với kế hoạch mới theo thứ tự:

| Nguyên nhân | Khi nào |
|---|---|
| `sentence_removed` | câu không còn trong kịch bản |
| `sentence_changed` | lời câu đổi (theo `basis`) |
| `source_changed` | nội dung nguồn đổi dù shot id giữ nguyên (theo `basis`) |
| `timing_changed` | tổng độ dài các mảnh đổi |
| `excluded` | người dùng bỏ cảnh đó (bản ghi nêu câu đã bỏ) |
| `pinned_elsewhere` | người dùng ghim cảnh đó cho câu khác |
| `shot_gone` | cảnh không còn trong footage |
| `plan_changed` | mọi trường hợp còn lại (không có ví dụ nào trong A5) |

- Mỗi lần huỷ ghi một bản ghi INVALIDATED kèm `conflict`: {nguyên nhân, câu liên quan, cảnh, các lựa chọn xử lý}.
- Bảng duyệt hiện lý do bằng tiếng Việt trên câu đó ("Cần chọn lại …").
- Cùng một shot nhưng khác khoảng, hoặc đổi cờ lặp, **không bao giờ** được coi là "như cũ".

### 3.3 Định danh tách biệt

- Định danh kế hoạch: giữ như cũ.
- Định danh quyết định (`decision_revision`): tăng theo từng thao tác.
- `basis`: định danh của chính câu và vật liệu của nó.

Ba thứ này không thay thế nhau.

### 3.4 Lập lại theo giao dịch

- Nhật ký được ghi trước kế hoạch.
- Một lần lập lại thất bại không ghi gì, không làm mất quyết định nào, và có thể chạy lại (test giao dịch).

## 4. Test (`tests/mastering/test_localized_replanning.py`)

| Test | Nội dung | Kết quả |
|---|---|---|
| A1 | Thay cảnh một câu: mọi lời duyệt khác và mảnh của chúng giữ nguyên; chỉ duyệt thì định danh kế hoạch không đổi | đạt |
| A2 | Bỏ một cảnh mà câu khác đã duyệt: xung đột `excluded` có ghi lại, không im lặng; "Không có cảnh phù hợp" không bao giờ thành khớp của AI | đạt |
| A3 | Chỉ đổi revision: giữ; nguồn đổi nội dung cùng shot id: huỷ (`source_changed`); sửa một câu: chỉ câu đó bị huỷ; file review cũ (trước `pieces`/`basis`) vẫn đọc được và được bổ sung | đạt |
| A4 | Forward-only nhiều phần: sửa một phần, các phần khác giữ lời duyệt | đạt |
| Giao dịch | Lập lại ném lỗi: kế hoạch dự phòng, review và nhật ký giữ nguyên từng byte, chạy lại được | đạt |
| A5 | Ngẫu nhiên cả hai chế độ: 0 huỷ tránh được, 0 thay đổi im lặng, có huỷ thật | đạt |

## 5. Số đo sau sửa (script đo, 300 lần mỗi chế độ, cùng bộ sinh)

| Chế độ | Lời duyệt | Huỷ tránh được | Huỷ có nguyên nhân | Câu bị lập lại |
|---|---|---|---|---|
| reorder | 1796 | **0** (rc10: 233) | 152 | **452** (rc10: 663) |
| forward-only | 1796 | **0** (rc10: 398) | 251 | **541** (rc10: 842) |

- "Huỷ có nguyên nhân" là câu dùng đúng cảnh mà người dùng đã ghim cho câu khác hoặc đã bỏ, hoặc (forward-only) cảnh đã duyệt nay bị chiếu lại. Đó là xung đột thật và được ghi lại.
- Reorder: 112 `pinned_elsewhere`, 40 `excluded`.
- Forward-only: 75 `pinned_elsewhere`, 96 `excluded`, 80 `shown_again`.
- 80 lời duyệt `shown_again` trước PR #788 vẫn giữ nguyên **mà không báo**: đoạn đã duyệt nay chiếu lại phần đã chiếu, vì cảnh mới của câu khác lấy từ phía sau trong nguồn.
- Số câu bị lập lại ở forward-only tăng từ 482 lên 541 vì mảnh giờ ghi cả cờ "chiếu lại". Một mảnh thành chiếu lại được tính là đổi, không còn bị bỏ qua.

## 5b. Bổ sung sau review độc lập (PR #788, issue #787)

Một review độc lập của #782 tìm ra 7 lỗi. Mỗi lỗi có test tái hiện (`ReviewFollowUpTests`, `SourceIdentityTests`, `PlanLogPairingTests`):

| Lỗi | Sửa |
|---|---|
| Duyệt một câu làm câu khác đổi cảnh (469 + 312 trên 729 lần thử) | Planner lập kế hoạch không kèm lời duyệt trước. Nếu kế hoạch đó đã giữ đúng mọi câu đã duyệt thì dùng nó. Chỉ khi không giữ được, câu đã duyệt mới được lấp trước. Kết quả: 0 câu đổi trên hơn 300 lần thử |
| Lời duyệt cũ của câu đã đổi lời vẫn điều khiển kế hoạch mới | `Edits.keep_words`: câu giữ chỗ phải có cùng băm lời |
| Đổi độ dài câu bị ghi là `plan_changed` | Thành `timing_changed`: 0 `plan_changed` (trước là 242) |
| Mảnh đã duyệt thành chiếu lại mà không báo | Nguyên nhân mới `shown_again` |
| Lỗi KeyError khi cảnh đã duyệt không còn dùng được | Cảnh đã duyệt được coi là dùng được |
| Định danh nguồn có cả recipe phân tích, nên đổi recipe làm huỷ nhầm thành `source_changed` | `sources` của nhật ký chỉ chứa dấu vân tay nội dung. Nguồn không đọc được khi ghi nhật ký thì bỏ ra (không rõ), không bao giờ thay bằng định danh cache có recipe. `basis` chỉ so khi mọi nguồn của câu đều có định danh nội dung. |
| Kế hoạch cũ có thể đi cặp với nhật ký mới sau khi ghi lỗi rồi hoàn tác | Nhật ký Bán hàng ghi `plan_inputs`. Kế hoạch đã lưu chỉ được dùng lại cùng nhật ký của chính nó. Có test chèn lỗi: ghi nhật ký mới, lưu kế hoạch thất bại, hoàn tác thì phải lập lại. |

Lead review trên #788 nêu hai điểm cuối (định danh không rõ, test giao dịch). Cả hai đã sửa trước khi merge (`7e1f564`, merge `c8340bf`).

## 5c. Sửa sau Windows rc12 (PR #797, #798)

Windows rc12 (§6) tìm ra ba lỗi thuộc phạm vi đổi kế hoạch cục bộ. Cả ba đã sửa trên `main`: PR #797 (issue #793, merge `17d5803`) và PR #798 (issue #794, merge `7e27ba0`). Một lỗi cùng họ còn mở (P2-N22). Các bản sửa nằm trong rc13 (`f46723a`). Chưa có bằng chứng Windows cho chúng.

### P2-N17: giữ khung hình để có moment (#797)

Nguyên nhân (điều tra §2: replay `raw.run_program` của bundle trên bản sao W2, khoá receipt trùng từng byte với file thật):

- i1 là nguồn 21.5 s, lời bình 9490 ms. Người dùng bỏ `i1-s1c1` (0–6000 ms).
- Kế hoạch narration-first đi tiến tốt, nhưng phía sau không còn moment ≥ 2000 ms. Planner rẽ sang nhánh "moment trước".
- Ở nhánh đó, `_moment(..., need_before_ms=9490)` đếm footage phía trước trên `pool` **còn cả cảnh đã bỏ**. Moment rơi vào 13950 ms, như thể có 13950 ms phía trước. Thật ra chỉ có 6000–13950 (7950 ms) cho 9490 ms lời bình.
- Aligner đi tiến hết cảnh. Mọi mảnh sau đó thành `repeat`, và planner giữ một khung hình cho từng mảnh: 5 HELD_FRAME, 7540 ms giữ khung, chỉ 1950 ms là hình chuyển động.
- Bản nháp đã có 1 HELD_FRAME (1540 ms) theo cùng cơ chế. Bỏ cảnh làm số này tăng từ 1 lên 5.
- Không phải lỗi render (trim, fps), cũng không phải nguồn hết sớm.

Sửa (`planners/compilation.py`, `plan_part`):

- `allowed` = `pool` trừ cảnh và nguồn người dùng đã bỏ. Cả ba lời gọi `_moment` (moment sau lời bình, moment trước, moment chọn lại sau một cảnh ghim) đều dùng `allowed`.
- `_held_ms` đo thời lượng giữ khung của một kế hoạch. Nếu kế hoạch "moment trước" giữ khung lâu hơn kế hoạch narration-first, planner dùng kế hoạch narration-first. Moment khi đó lấy từ phần còn lại sau lời bình, hoặc không có.

### P2-N18: phần không ai đụng bị lập lại (#797)

Nguyên nhân (điều tra §1):

- Không input nào trong digest kế hoạch của i4 đổi.
- Phần vision của mỗi phần tính theo lượt chạy: `fair() = (budget − taken) // số phần còn lại`. Nó phụ thuộc vào những gì các phần trước đã tiêu.
- Bỏ cảnh ở i1 và duyệt ở i2 khoá cảnh, nên hai phần đó nhìn ít frame hơn (`taken` sau i3: 62 ở bản nháp, 58 ở lần dựng lại).
- i4 được room 6 thay vì 5, multi-frame limit 3 thay vì 2, nên nhìn thêm `i4-s1c4` (2 frame mới, đúng 2 lần "asked" của report). Bằng chứng của cảnh đó đổi, nên i4.u1 đổi cảnh, khoá receipt đổi, và i4 bị dựng lại.

Sửa (`compilation/raw.py`, lớp `PartLooks`):

- Phần chia (`share`) của mỗi phần cố định **một lần**, trước khi lập kế hoạch phần nào: (ngân sách − phần broad look đã dùng) // số phần.
- `room()` = phần chia trừ những gì chính phần đó đã tiêu, không bao giờ quá phần ngân sách còn lại.
- Cái giá: frame mà một phần đầu không dùng hết **không còn được chuyển** cho các phần sau.

### Lời duyệt bị huỷ khi một câu chiếu một cảnh hai lần (#798)

- Nguyên nhân: phát hiện phụ ở §6. Lời duyệt lưu cảnh đã khử trùng lặp, còn `reconcile` so với `unit.chosen` còn cảnh lặp, nên báo `plan_changed`.
- Sửa (`mastering/review.py`): `approve`, `reconcile` và `basis()` đều lấy mỗi cảnh một lần, giữ thứ tự.
- Tương thích với lời duyệt rc12 đã lưu: `basis` của rc12 băm cảnh lặp hai lần, nên đọc theo cách mới thì phần vật liệu khác. `reconcile` giờ coi vật liệu là như cũ nếu khớp cách mới hoặc cách cũ (`basis(..., repeats=True)`), rồi ghi lại `basis` theo cách mới. Nguồn đổi nội dung thật vẫn là `source_changed`.

### Test

`tests/mastering/test_compilation_raw.py` (#797) và `tests/mastering/test_localized_replanning.py` (#798). Cột cuối là kết quả khi chạy chính test đó trên mã engine của rc12 (`a064807`).

| Test | Ghim điều gì | Trên mã rc12 |
|---|---|---|
| `MomentTest.test_no_frame_is_held_to_buy_a_moment_and_an_excluded_shot_is_never_shown` | P2-N17, hình học i1 của rc12 (21.5 s, cảnh chia 1–2 s), có và không bỏ `i1-s1c1`: 0 mảnh giữ khung, lời bình đủ 9490 ms, không mảnh nào từ cảnh đã bỏ | FAIL (còn mảnh `still`) |
| `MomentTest.test_excluded_opening_shots_are_never_counted_as_footage_ahead_of_the_moment` | P2-N17: bỏ 2 hoặc 3 cảnh đầu, lời bình 3000 hoặc 6000 ms: không giữ khung, không chiếu cảnh đã bỏ | ERROR (`no footage left after the edits`) |
| `MomentTest.test_an_excluded_shot_is_never_the_moment` | P2-N17: cảnh đã bỏ không bao giờ là moment, kể cả khi một cảnh ghim chạy qua moment | FAIL (moment lấy từ cảnh đã bỏ) |
| `PartIndependenceTest.test_what_an_earlier_part_spends_never_changes_a_later_parts_room` | P2-N18: room của mỗi phần không đổi theo những gì phần trước tiêu | ERROR (chưa có `PartLooks`) |
| `PartIndependenceTest.test_a_part_never_spends_more_than_its_share_or_what_the_budget_has_left` | P2-N18: không quá phần chia, không quá ngân sách còn lại | ERROR (chưa có `PartLooks`) |
| `PartIndependenceTest.test_what_one_item_spends_never_changes_what_the_later_items_look_at` (cần FFmpeg) | P2-N18 đầu cuối: chạy chương trình 4 mục hai lần, lần hai mục 1 tốn gấp đôi frame. Các phần sau hỏi đúng các frame cũ, không dựng lại phần nào, mọi `plan-*.json` giữ nguyên từng byte | FAIL (các phần sau hỏi frame khác) |
| `ReviewFollowUpTests.test_a_sentence_that_shows_one_shot_twice_keeps_its_approval` | #798: câu chiếu một cảnh hai lần giữ lời duyệt qua một lần chạy lại | FAIL (lời duyệt còn cảnh lặp) |
| `ReviewFollowUpTests.test_an_approval_saved_by_rc12_on_a_shot_shown_twice_is_kept` | #798: lời duyệt rc12 (cảnh lặp, `basis` băm hai lần) vẫn giữ và được ghi lại `basis` mới; nguồn đổi thật vẫn `source_changed` | ERROR (chưa có tham số `repeats`) |

Bằng chứng, tách theo loại:

- **Cục bộ (Linux, có FFmpeg), chạy khi viết báo cáo này:** 8 test trên đạt trên `origin/main` (`f46723a`). Trên mã engine `a064807` (test giữ nguyên): 4 FAIL, 4 ERROR. Ba ERROR chỉ là API mới chưa có (`PartLooks`, `repeats`). ERROR còn lại là hành vi sai thật.
- **CI hosted (theo thân PR):** #797 trên `d2ba877`, run `43247787049`: tests/ci 38 OK, tests/mastering 304 OK và 14 skipped (các test FFmpeg, gồm `PartIndependenceTest` đầu cuối; test quy tắc phần chia và `MomentTest` có chạy), tests/compilation 9 OK và 1 skipped. #798 trên `24cd3ee`, run `43248973399`: tests/ci 38 OK, tests/mastering 306 OK và 14 skipped (test FFmpeg). Governance đạt ở cả hai. Không phải toàn bộ registry.
- **Windows:** chưa có. rc13 đang chạy (§6).

### Review độc lập

Trước khi merge, một review độc lập xem toàn bộ các bản sửa rc12 (mỗi mảng một người review, mỗi điểm được một người khác kiểm lại trên bản sao `a064807` và bản sửa). Bốn điểm được xác nhận (chi tiết trong mục "Independent review before merge" của thân PR #797 và #798):

- Test N17 đầu tiên vẫn qua khi đặt `pool` lại chỗ `allowed` ở hai lời gọi `_moment` (cơ chế lùi về kế hoạch lời bình che mất). Đã thêm hai test hỏng với đột biến đó.
- Test N18 cả chương trình đầu tiên vẫn qua trên quy tắc chia cũ. Đã thay bằng test hỏng trên quy tắc cũ.
- Lời duyệt rc12 đã lưu trên câu chiếu một cảnh hai lần bị huỷ nhầm thành `source_changed`. Đã sửa trong #798 (`repeats=True`).
- Thao tác ở intro/outro/hook vẫn có thể lập lại một mục: chưa sửa, backlog #796 (dưới đây).

Ba điểm đầu đã sửa trước khi merge.

### Giới hạn đã biết: P2-N22 (#796, chưa sửa)

- Hook, intro và outro lập kế hoạch trên footage của mọi mục.
- Multi-frame look của chúng tách cảnh của các mục (`candidates.update(mine)`, rồi dựng lại `pool`, `groups`, `part_pools`). Targeted look của chúng ghi vào `evidence` và `looked` dùng chung.
- Cả hai phụ thuộc kế hoạch của intro, tức là phụ thuộc thao tác của người trên intro. Vì vậy một thao tác chỉ ở intro/outro/hook vẫn có thể lập lại một mục không ai đụng.
- Reviewer tái hiện trên `390e162`: chương trình 4 mục, intro 3 câu. Ghim một cảnh ở intro.u1, bỏ ba cảnh của i2 ở intro.u2. Lần chạy lại dựng `['intro', 'i2', 'outro']`, và `plan-i2.json` đổi dù không ai đụng i2.
- Ngoài phạm vi chỉ thị này (chỉ P2-N5, P2-N11, P2-9). Ghi vào backlog, chưa sửa. `PartLooks` chỉ cố định phần chia giữa các phần, nên không che trường hợp này.

## 5d. Sửa sau Windows rc13 (PR #803, issue #801)

**Lỗi.** rc13 W2 còn 1 khung giữ ở i1.u2 (1126 ms), nên Kiểm tra cuối chặn xuất. Ở bước 3 của `align._assign` (cảnh trung tính, đi tiến), u2 lấy cảnh 1225 ms và bỏ qua cảnh 1138 ms ngay trước nó. Câu hết footage phía trước 1126 ms, rồi chiếu lại cảnh cũ thành khung đứng. `safe()` chỉ kiểm các câu sau, không tính phần câu này mà cảnh quá ngắn để phủ.

**Sửa.** `safe()` giữ nguyên. Chỉ đổi bước 3, chỉ khi đi tiến:
- Hàm mới `short_of(c)`: phần footage các câu sau **và phần còn lại của câu này** sẽ thiếu sau khi chiếu `c`, tức số ms sẽ phải giữ khung.
- Sau `rank()`, điểm của mỗi cảnh trung tính trừ `HELD_PENALTY × min(2·wanted, short_of) / wanted`, cùng dạng với `LOSS_PENALTY`. `HELD_PENALTY = 2000` vì mọi khung giữ đều chặn xuất.
- Khi mọi cảnh an toàn đều để thiếu ít nhất `SHORT_FLOOR`, các cảnh ngắn hơn ngưỡng cảnh trung tính (từ `SHORT_FLOOR`) mà để thiếu ít hơn được **thêm** vào để so.
- Không lọc bỏ cảnh nào. Thứ hạng (cảnh khớp của chính câu, `LOSS_PENALTY`) vẫn quyết định, có thêm chi phí khung giữ.

**Review độc lập trước merge.** Ba bản trước bị bác:
- `safe()` chặt hơn ở mọi bước: thêm cảnh lặp và bỏ qua footage khi footage vừa đủ; một hình học giữ khung 6,1 s.
- Lọc cứng "chỉ cảnh đủ dài": bỏ qua thứ hạng, có câu mất cảnh khớp của mình.
- Thêm cảnh ngắn khi thiếu bất kỳ ms nào: chạy cả khi thiếu vài ms vốn không bao giờ bị giữ, mất 10,7 s cảnh khớp ở 14 phần. Cổng `SHORT_FLOOR` và quy tắc "để thiếu ít hơn" đến từ review này.

**Test** (`MomentTest`, không cần FFmpeg):
- `test_a_short_shot_ahead_is_never_skipped_for_a_held_frame`: đúng hình học và bằng chứng i1 của rc13 (replay trùng từng byte trên Windows). **Trượt trên main.**
- `test_filler_that_runs_the_sentence_out_of_footage_never_goes_before_one_that_lasts`: hình học mà bản đầu giữ khung 6,1 s. Đạt trên main, **trượt trên bản đầu**.

**Số đo** (Linux, thế giới ngẫu nhiên, không phải footage thật):

| Mức | Kết quả so với main |
|---|---|
| Planner (`plan_part`, 3 415 phần) | khung giữ 9 145 s → 7 755 s; 662 phần tốt hơn, 7 phần giữ thêm (cả 7 đã giữ khung trên main); không phần nào mới bị giữ khung; minh hoạ −9,8 s |
| Aligner (3 000 thế giới × 4 kiểu sửa) | cảnh lặp −766 s; minh hoạ −60 s; 0 kế hoạch mới có cảnh lặp |
| Lời duyệt (300 lần mỗi chế độ) | 0 lần huỷ tránh được, như main |
| Bộ test | mastering 310 OK, compilation 9 OK, product 50 OK (2 skip) |

Còn lại: một lần lập lại có giữ lời duyệt lặp thêm 2,5 s (giới hạn cũ của bước giữ). Windows: kiểm trên rc14 (§6).

## 5e. Sửa sau Windows rc14 (PR #804, issue #796, P2-N22)

**Lỗi.** Hook, intro và outro lập kế hoạch trên footage của mọi mục. Lần nhìn có mục tiêu và lần nhìn nhiều khung của các phần này ghi vào trạng thái chung (`candidates`, `evidence`, `looked`) mà các mục dùng sau đó. Nên một thao tác ở intro (sửa, hay chỉ duyệt) có thể đổi kế hoạch của một mục không ai đụng. rc14 W5 trên W2 cho thấy đúng lỗi này (§6 rc14).

**Sửa.**
- `compilation/raw.py`: hook, intro, outro **nhìn footage cho riêng mình**. Lần nhìn của chúng chạy trên bản sao cục bộ của `candidates`, `evidence`, `looked`, `sampled`; trạng thái chung được trả lại sau phần đó. Kế hoạch của một mục không còn phụ thuộc vào việc gì xảy ra ở các phần này.
- `align.left_out`: bỏ một mảnh (`c3f2`) mà lần này cảnh không bị tách thì bỏ cả cảnh, để đoạn đã bỏ không bao giờ hiện lại. Nếu như vậy không còn footage nào, chỉ bỏ đúng id đã ghi và phần đó có cảnh báo yêu cầu chọn lại. Khoảnh khắc âm thanh gốc dùng cùng quy tắc.
- `review.reconcile`: lệnh bỏ một mảnh mà cảnh cha còn trong kế hoạch không bị huỷ là "cảnh không còn".

**Test.**
- `test_an_edit_in_the_intro_never_changes_what_an_item_sees` (FFmpeg): ghim và bỏ cảnh chỉ trong intro. **Trên main lần dựng lại dựng `intro`, `i2`, `outro` và `plan-i2.json` đổi**; sau sửa không mục nào bị dựng lại, kế hoạch mọi mục trùng từng byte.
- `test_a_left_out_part_of_a_shot_that_is_not_split_leaves_out_the_whole_shot` và phần mới của `test_an_excluded_shot_is_never_the_moment`: trượt trên main.

**Review độc lập trước merge (2 vòng).** Vòng 1 tìm ra: dựng thất bại khi quy tắc bỏ cả cảnh làm hết footage; khoảnh khắc vẫn dùng cảnh có mảnh đã bỏ; `reconcile` huỷ các lệnh bỏ đó. Vòng 2: không có cảnh báo khi phải giữ cả cảnh. Tất cả đã sửa trước merge.

**Giới hạn đã biết.** Mục không còn dùng lại những gì intro đã nhìn; câu trả lời có sẵn trong cache vẫn tính vào phần nhìn của mục. Kiểm tra "outro chiếu cảnh mới" so id chính xác, nên mảnh `Xf1` của intro không khớp với cảnh `X` nguyên.

**Số đo** (Linux): mastering 312 OK, compilation 9 OK, product 50 OK (2 skip); lời duyệt 0 lần huỷ tránh được. CI: integration 312 OK, 15 skip (test FFmpeg).

## 6. Windows E2E (W1, W2)

### rc12 (a064807)

Nguồn: `assisted-e2e/rc12/report-rc12.md` (W1, W2, W5) và `assisted-e2e/rc12/investigation-rc12.md` (§1, §2). Máy Windows 11 của chủ dự án, chỉ dùng CPU, bản `1.1.96-rc12` candidate chưa ký. Các bước "UI" chạy `DubFlow.exe` thật, điều khiển bằng tự động hoá CDP. Đây **không** phải review của người. Các bước "file check" đọc snapshot và file chạy bằng module engine của bundle, chỉ đọc.

**W1, Bán hàng (`run-8d5a6a8c0678`, 1 video, 6 câu): ĐẠT.**

| Bước | Cách | Kết quả | Bằng chứng |
|---|---|---|---|
| Bản nháp | UI | COMPLETED sau 156 s | `logs\W1-log.txt` |
| Duyệt 4 câu (u1, u4, u5, u6) | UI | 4 USER_APPROVED | `snap-W1-0-approved4.json` |
| Ghim (u3), dựng lại | UI | 10.9 s. Giữ **4/4**, huỷ **0**. Câu bị lập lại: 0 | `snap-W1-1-pin.json` |
| Bỏ cảnh `v2c2` (u3), dựng lại | UI | 10.4 s. Giữ **4/4**, huỷ **0**. Chỉ u3 bị lập lại | `snap-W1-2-exclude.json` |
| Thay cảnh (u3, `v1c8`), dựng lại | UI | 10.9 s. Giữ **4/4**, huỷ **0**. Chỉ u3 bị lập lại | `snap-W1-3-replace.json` |
| Editor sau Final QC | UI | Mở, không có khối QC; `unreviewed 0`, `user_settled 5` | `W1-5-editor.png` |

- Mỗi lần dựng lại: 1 phần (`video-1`; video sản phẩm là một phần). 0 câu phải xem lại.
- rc10 (§2): 2 trên 5 lời duyệt bị huỷ sau một lần ghim. rc12: 0 trên 4, qua ba thao tác trên một câu khác.
- Dựng lại mất 10.4–10.9 s, chậm hơn rc10 (4.7–8.8 s).
- Report rc12 không ghi bước xuất hay số HELD_FRAME cho W1.

**W2, Tổng hợp (`run-6838a67c5795`, Top 5, 8 phần, bật AI nhìn hình): đổi kế hoạch ĐẠT, xuất KHÔNG ĐẠT.**

| Bước | Cách | Kết quả | Bằng chứng |
|---|---|---|---|
| Bản nháp | UI | COMPLETED sau 263 s; dựng 8 phần, dùng lại 0 | `snap-W2-0-*.json` |
| Duyệt cả i2 và outro | UI | 4 câu USER_APPROVED. `outro.u2` dùng `i1-s1c1`, một cảnh của i1 | `W2-review-log.txt` |
| Bỏ cảnh `i1-s1c1` ở i1.u1 | UI | Bản ghi EXCLUDE | log |
| "Dựng lại các phần đã đổi" | UI | 31.3 s. **Dựng 2 phần (i1, i4), dùng lại 6** (hook, intro, i2, i3, i5, outro) | `W2-log.txt`, `W2-parts-mtime-*.txt` |
| Đếm | file check | Giữ **4/4**, huỷ **0**. `outro.u1` không còn bị huỷ (ca rc10, §2). Bị lập lại: i1.u1, i1.u2 (đúng) **và i4.u1**, phần không ai đụng: `[i4-s1c2f2, i4-s1c3f3]` thành `[i4-s1c4]` | `snap-W2-2-post-*.json` |
| Xuất bản cuối | UI | **"Chưa xuất. Kiểm tra cuối chưa đạt"**. **5 HELD_FRAME, cả 5 ở i1** (765 đến 1950 ms). Không có `final.mp4` | `W2-export-line-watch.json`, `W2-03-export-qc-heldframe.png`, `logs\W2W3-final-qc.json` |

- i4 không có quyết định nào, nên không lời duyệt nào bị huỷ. Nhưng cảnh của i4 đổi mà không báo (P2-N18).
- Kế hoạch mới của i1 dùng `i1-s1c3f1` hai lần liên tiếp trong i1.u1. Final QC từ chối xuất (P2-N17).
- Nguyên nhân và cách sửa của cả hai: §5c.

**Phát hiện phụ (report rc12 bỏ sót).** Ở W5, trên chính dự án W2: ghim i3.u2 rồi "Dựng lại các phần đã đổi" (dựng i3, dùng lại 7). Lần dựng lại này ghi một bản ghi `INVALIDATED` cho `i1.u1`, nguyên nhân `plan_changed`, dù pieces của câu không đổi.

- Lời duyệt lưu `['i1-s1c3f1', 'i1-s1c2f1']` (đã khử trùng lặp). `unit.chosen` là `['i1-s1c3f1', 'i1-s1c3f1', 'i1-s1c2f1']`. `review.reconcile` so hai danh sách và báo `plan_changed`.
- Report rc12 ghi "0 INVALIDATED" vì `logs\records.json` được quét trước W5. Con số đó đã cũ: sau W5 có 1 bản ghi `INVALIDATED` không có nguyên nhân thật.
- Sửa ở PR #798 (§5c).

### rc13

Bản 1.1.96-rc13 (`f46723a`, run 43250339299; rc12 cộng PR #797, #798, #799), Windows, chỉ CPU. Nguồn: `rc13\report-rc13.md`, `rc13\results.json`, `rc13\investigate\` trên máy Windows.

**W1, Bán hàng (Espresso): ĐẠT.** Bản nháp 146 s (rc12 156 s). Duyệt 4 câu, rồi Ghim, Bỏ cảnh, Thay cảnh trên câu 3, dựng lại sau mỗi bước.
- Giữ **4/4**, huỷ **0** sau cả ba thao tác. Câu bị lập lại: 0 / 1 / 1 (chỉ u3).
- Dựng lại 9,8 / 9,3 / 9,3 s (rc12 10,9 / 10,4 / 10,9 s).
- Không chạy: kiểm tra trình chỉnh sửa mở được sau Final QC.

**W2, Tổng hợp (Top 5, cùng chương trình rc12): đổi kế hoạch ĐẠT, xuất KHÔNG ĐẠT.**

| Bước | Kết quả |
|---|---|
| Bản nháp | COMPLETED sau 255 s (rc12 263 s) |
| Duyệt i2 và outro, bỏ cảnh `i1-s1c1` ở i1, "Dựng lại các phần đã đổi" | 12,1 s (rc12 31,3 s). **Dựng 1 phần (i1), dùng lại 7.** rc12 dựng i1 và i4 |
| Kế hoạch các mục không ai đụng | i3, i4, i5 giữ nguyên từng byte, 0 lần hỏi vision mới (P2-N18 đã sửa trong ca này) |
| Lời duyệt | giữ **4/4**, huỷ **0** |
| Final QC | **1 HELD_FRAME ở i1.u2** (1126 ms; rc12 có 5). Mọi phần khác 0 |
| Xuất bản cuối | **Bị chặn**: "Chưa xuất. Kiểm tra cuối chưa đạt. Dòng thời gian: i1: HELD_FRAME i1.u2 giữ một khung hình 1126 ms" |

- i1 cũng mất khoảnh khắc âm thanh gốc: kế hoạch đặt khoảnh khắc trước lời giữ 7540 ms khung đứng, nên quy tắc của #797 chọn kế hoạch theo lời (1126 ms). Quy tắc làm đúng việc của nó.
- Nguyên nhân khung còn lại (replay chỉ đọc, kế hoạch trùng từng byte với bản thật; chi tiết trong `rc13\investigate\answer.md` trên máy Windows, không chép vào repo; [report-rc13.md](assisted-e2e/rc13/report-rc13.md) có bản tóm tắt): `_assign` bắt đầu u1 ở cảnh khớp nhất (12000 ms), nên 6 s footage chưa bỏ phía trước chỉ còn quay lại được dưới dạng cảnh lặp; ở bước 3 của `align._assign` (cảnh trung tính, đi tiến), ngưỡng là 1200 ms, nên u2 bỏ qua `c4f1` (1138 ms) để lấy `c4f2`. Câu hết footage phía trước 1126 ms rồi chiếu lại cảnh cũ thành khung đứng. `safe()` không thấy, vì nó không tính phần câu mà ứng viên quá ngắn để phủ. Sửa ở issue #801 (§5d).

**W5 trên dự án W2 (ghim i3.u2, dựng lại): ĐẠT.** 31 s, chỉ dựng i3, dùng lại 7. **0 INVALIDATED**: i1.u1 không còn bị huỷ như ở rc12.

### rc14

Bản 1.1.96-rc14 (`96400fb`, run 43279224888; rc13 cộng PR #803), Windows, chỉ CPU. Nguồn: `rc14\report-rc14.md` trên máy Windows.

**W1, Bán hàng (Espresso): ĐẠT.** Giữ **4/4**, huỷ **0** sau Ghim, Bỏ cảnh, Thay cảnh trên câu 3; chỉ u3 bị lập lại. Dựng lại 9,1 / 8,3 / 8,3 s. Trình chỉnh sửa mở được sau Final QC (bước NOT RUN ở rc13); trước đó app hỏi "Còn câu chưa duyệt cảnh" (1 câu).

**W2, Tổng hợp (cùng chương trình): ĐẠT, xuất được.**
- Ở rc14, cảnh `i1-s1c1` (0–6 s) bị lần nhìn nhiều khung tách thành 3 mảnh, nên để bỏ đúng đoạn như rc13 phải bấm "Bỏ cảnh này" 3 lần.
- "Dựng lại các phần đã đổi": 12,4 s, **dựng 1 phần (i1), dùng lại 7**. Kế hoạch hook, intro, i3, i4, i5 trùng từng byte. 0 INVALIDATED.
- **HELD_FRAME 0 ở cả 8 phần** (rc13: 1 ở i1). i1 giữ khoảnh khắc âm thanh gốc (18,0–21,5 s).
- Duyệt nốt các câu rồi xuất: **"Kiểm tra cuối: đạt."** Bản cuối 76,4 s, 1080p30. Kiểm i1 bằng FFmpeg (tự động, không phải người xem): 0 đoạn đứng hình ≥ 0,5 s.

**W5 trên dự án W2 (ghim i3.u2, dựng lại): KHÔNG ĐẠT.**
- 0 INVALIDATED, HELD_FRAME 0, nhưng phần bị dựng lại là **i1**, không phải i3 (i3 chỉ đổi cờ cần xem, receipt giữ nguyên nên được dùng lại).
- **i1 mất khoảnh khắc âm thanh gốc**, dù đầu vào của i1 không đổi.
- Nguyên nhân (replay chỉ đọc): sau khi người dùng duyệt các câu của intro, cảnh của intro bị khoá và không bao giờ được tách lại, nên lần nhìn nhiều khung của intro chuyển sang `i1-s1c4` và tách nó thành 3 mảnh khoảng 1,2 s. i1 không còn cảnh nào ≥ 2 s cho khoảnh khắc. Đây là P2-N22 (#796), trước đó nằm trong backlog. Sửa ở PR #804 (§5e).

### rc15

Bản 1.1.96-rc15 (`6b1a0cf`, run 43298731065, zip `efafe2cf…fc74`; rc14 cộng PR #804), Windows, chỉ CPU. Nguồn: `rc15\report-rc15.md`, `rc15\results.json` trên máy Windows (bản sao trong [assisted-e2e/rc15](assisted-e2e/rc15/)).

**W1, Bán hàng (Espresso): ĐẠT.** Giữ **4/4**, huỷ **0** sau Ghim, Bỏ cảnh, Thay cảnh trên câu 3; chỉ u3 bị lập lại. Dựng lại 8,3 / 7,7 / 7,8 s. Bản nháp 166 s.

**W2, Tổng hợp (cùng chương trình): ĐẠT, xuất được.**
- Lần nhìn nhiều khung lại tách `i1-s1c1` thành 3 mảnh. Người dùng bỏ `i1-s1c1f2` và `f3`. Mảnh `f1` (0–1,95 s) **không bỏ được từ giao diện**: nó không được dùng, và danh sách cảnh thay thế chỉ có nút "Dùng cảnh này". Hệ quả: sau khi dựng lại, u1 của i1 dùng chính mảnh này (0–1950 ms), nên một phần ba đoạn 0–6 s người dùng định bỏ vẫn hiện trong i1. Planner làm đúng các lệnh bỏ nó nhận được; lỗ hổng nằm ở bảng duyệt. Ghi vào backlog #805 (§8).
- "Dựng lại các phần đã đổi": 12,7 s, **dựng 1 phần (i1), dùng lại 7**. Giữ **4/4**, **0** INVALIDATED.
- **HELD_FRAME 0 ở cả 8 phần.** i1 giữ khoảnh khắc âm thanh gốc (18,0–21,5 s).
- Hai mục nghiêm trọng của Kiểm tra (i1.u2, i5.u2 MATCH_NOT_FOUND) được bấm duyệt bằng tự động hoá CDP như mọi câu còn lại (không phải người xem); xuất bản cuối: **"Kiểm tra cuối: đạt."** i1 trong bản cuối: `freezedetect` 0 đoạn đứng hình.

**W5 trên dự án W2 (ghim i3.u2, dựng lại): ĐẠT.** "Dựng 0 phần, dùng lại 8". `plan-i1.json` trùng từng byte; 0 INVALIDATED; i1 giữ khoảnh khắc. Lần dựng lại hỏi vision thêm 6 khung (intro, i3, i4); chữ trong kế hoạch đổi nhưng hình học và receipt không đổi, nên không phần nào phải dựng. Lỗi rc14 (#796, P2-N22) đã sửa cho trường hợp đã chạy.

## 7. Không đổi

- Định dạng review v2 vẫn đọc được bản v1.
- Mastering Tắt, đường Bán hàng cũ và spec Tổng hợp v1 không đổi.
- Không ngưỡng nào bị hạ.

## 8. Còn mở (backlog, không chặn Workstream A)

| Mã | Việc | Nguồn |
|---|---|---|
| P3-N24 (#805) | Bảng duyệt không bỏ được một mảnh cảnh không dùng (chỉ có "Dùng cảnh này"); sau khi dựng lại, mảnh đó vào kế hoạch | Windows rc15 W2 |
| P3-N21 | "Còn N câu…" vẫn hiện sau khi đã duyệt hết | Windows rc12–rc15 |
| P3-N13 | "Tạo 2 video" khi có 3 kịch bản | Windows rc12–rc15 |
| — | Khi bỏ một mảnh của cảnh đã duyệt, nguyên nhân huỷ ghi là `plan_changed` thay vì lý do đúng | PR #804 (giới hạn đã ghi) |
| — | Các mục hỏi lại khung intro đã mô tả; câu trả lời có sẵn vẫn tính vào phần của mục | PR #804 (giới hạn đã ghi) |

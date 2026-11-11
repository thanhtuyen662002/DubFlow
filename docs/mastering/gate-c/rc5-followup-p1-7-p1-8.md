# rc5 follow-up: P1-7, P1-8, the LOOSE_WORD_SHARE effect, P2-13

Read-only analysis. The replays used scratch copies of the rc5 app and of the run folders under the session scratchpad. The repo and the rc5 outputs were not changed.

## 1. P1-7: B1 intro is READY but shows another sentence's key shot

| Field | Value |
|---|---|
| Set | B1 "Top 5 old" = `phase2-e2e\specs-u\B1.json`, "Top 5 cỗ máy phản ứng dây chuyền ấn tượng" |
| Items | goldberg-lop-hoc #5 (`Maquina_Goldberg_Ok-20190908-143524.webm`), goldberg-lop-hoc-2 #4 (`…-145054.mkv`), mo-phong-3d #3 (`A_simple_Rube_goldberg_machine.webm`), may-y-tuong #2 (`The_Rube_Goldberg_Idea_Machine.webm`), purdue-2019 #1 (`2019_Purdue_National_Chain_Reaction_Competition_Winner.webm`) |
| Mastering | no `mastering` key in the spec; plan options `{level: balanced, style: entertainment}` |
| Vision | Gemini `gemini-3.5-flash-lite`. The scored run is the identical rerun s-B1b (asked 0) |
| Unit | `intro.u1`, function `intro`, part kind **intro** (a separate part, not the hook and not an item sentence) |
| Text | "Cùng điểm lại năm cỗ máy phản ứng dây chuyền khiến người xem phải nín thở." |
| Plan segment | `intro.s1`: source `purdue-2019-s1` 27356–31556, timeline 0–4200, role `broll`, **needs_review false**, confidence **672** |
| Segment reasons | "minh họa câu …"; "khớp lời “người xem, cỗ máy, máy phản, phải” (AI gemini/gemini-3.5-flash-lite, tên file)"; "cảnh toàn cảnh bối cảnh hợp câu intro" |
| Coverage | `{need_ms 4200, illustrated_ms 4200, milli 1000, status full, needs_review false, matched_footage 1}` |
| Review | evidence MATCH_AVAILABLE, state **READY**, notes [] |
| Chosen / alternatives | chosen `[purdue-2019-s1c11]`; alternatives c13 (578), goldberg-lop-hoc-2-s1c2f1 (504), …c2f3 (502) |
| Shot taken | `purdue-2019-s1c11` 27356–35082. Its vision texts (marked, 950 each): "Toàn cảnh cỗ máy phản ứng dây chuyền lớn bằng gỗ với hai người đang quan sát…", "…với người xem bên phải và mũi tên trắng…", "…hai người đang quan sát mô hình…" |
| Whose key | truth `purdue-2019.u2` key 21000–34000 "đội ngồi xem" (also 39000–42000). Stolen: 4200 ms, unflagged |
| Relevance of c11 | for `intro.u1`: **357**, the best in the intro part (words: người xem, cỗ máy, máy phản, phải) |
| Relevance of c11 for the owner | for `purdue-2019.u2` ("Hàng chục bước nối tiếp nhau chạy trơn tru trong khi cả đội ngồi theo dõi từng chuyển động."): **14** (only "động"). The best over the purdue part is 58 < MIN_RELEVANCE 60, so **u2's accept list is empty** (replay matches the logged `matched_footage 0`) |
| Owner's state | `purdue-2019.u2` is SOURCE_INSUFFICIENT, 0 % coverage. It plays c11 27356–32261 too, so the same shot appears twice in the video: intro and #1 |

Why nothing caught it:

- The intro is planned as its own part. `owners_after` (check A) only sees sentences inside the same part.
- Even inside the purdue part, u2 accepts nothing ("ngồi theo dõi" vs vision's "quan sát", plus "hai người" vs "cả đội"), so there is no owner to protect.
- The intro has a strong match (357), so it is "full" and its piece is above REVIEW_BELOW (672 ≥ 600), which makes it READY.

## 2. P1-8: SOURCE_INSUFFICIENT where the source has the shot

The rc5 code was replayed with LOOSE_WORD_SHARE = 2 (as shipped) and = 1. Both give the same numbers.

### 2a. Desktop C1 #5 Câu 2 (`i1.u2`, part i1, single source `m2-lop-hoc-2995.webm`)

- Text: "Hàng domino gỗ đổ nối tiếp nhau dọc mặt bàn trong khi cả lớp đứng xem hồi hộp."
- Best relevance over the part: **37** (share 2) and **37** (share 1), below MIN_RELEVANCE 60.

| Shot | Times | AI description, verbatim (Claude claude-opus-5-5, WITH marks) | Vision conf. | Rel. (share 2 / 1) |
|---|---|---|---|---|
| i1-s1c2f1 | 6000–7950 | "Các học sinh mỉm cười theo dõi hàng domino dài xếp trên bàn cùng dốc gỗ; phía trước là ống nhựa trắng đặt trên giá. học sinh, domino, dốc gỗ, ống nhựa trắng, bàn mỉm cười, theo dõi domino" | 750 | 31 / 31 |
| i1-s1c2f3 | 10050–12000 | "Hai nam sinh đứng cạnh bàn có hàng domino, lon, cốc nhựa và vật nhỏ; …" | 300 | 12 / 12 |
| i1-s1c3f1 | 12000–13950 | "Trên bàn có mô hình phản ứng dây chuyền: hàng quân domino xếp đứng, tượng nhỏ, cốc nhựa, lon, chậu nhựa xanh; học sinh đứng phía sau. …" | 325 | 30 / 30 |
| i1-s1c3f3 | 16050–18000 | "Cận cảnh các bàn có khung gỗ, thanh trượt, tượng nhỏ và hàng domino; …" | 275 | 11 / 11 |

Why it is not accepted (term breakdown for c2f1):

- The words "hàng", "domino", "bàn", "gỗ", "đứng" and "lớp" are all **COMMON** (present in more than 50 % of the 12 described shots of this one-source part), so they score 0.
- Only the pair "hàng domino" (and "dọc", "đứng xem", "xem" on other shots) counts.
- The multi-frame sub-shots also carry low vision confidence (275–325).
- Not loose-word halving: all matches are exact. LOOSE_WORD_SHARE = 1 **does not make it accepted**.
- Then `review.evidence_state` → SOURCE_INSUFFICIENT, because every usable shot has content.

### 2b. Máy thổi bụi `u4` (P2, product)

- Text: "Hai anh em ngồi thử máy ngay trước cửa hàng và cười thích thú vì gió quá mạnh."
- Best relevance: **57** (share 2) and **57** (share 1), below 60.

| Shot | Times | AI description, verbatim (Gemini flash-lite) | Marks | Rel. (share 2 / 1) |
|---|---|---|---|---|
| v2c1 | 0–2449 | "hai người đàn ông đang ngồi cầm máy thổi bụi và ca nhựa máy thổi bụi, ca nhựa ngồi nói chuyện" (vision 900) | WITH marks | 57 / 57 |

Why it is not accepted:

- Matched terms: "hai" and "ngồi" (exact). "máy" is COMMON.
- "anh em", "thử", "cửa hàng", "cười", "thích thú" and "gió mạnh" are not in the description.
- 57 < 60. Not loose-word halving: LOOSE_WORD_SHARE = 1 **does not make it accepted**.
- rc4's description of the same shot ("Hai người nam đang ngồi cạnh nhau, một người cầm máy thổi bụi mini và một người cầm ly nhựa") scored exactly 60 and was accepted. This is vision wording variance on a threshold edge.

## 3. Effect of the #745 loose-word halving alone (replay with rc5 code on the saved rc5 evidence)

Method:

- Copy the rc5 run folder.
- Delete `mastering_plan-1.json` and `editorial-1.json` so the plan is made again: `reused: false`.
- The vision cache is reused: asked 0 for P1/P2, and 1 frame for P3.
- Score with rc5 `quality`.
- The control (same replay with shipped code) reproduces the rc5 run exactly.

| Set | Variant | Coverage % | Units ok | Silent | False alarms | Contra ms | Stolen ms flagged / unflagged | Vision asked | Per unit (coverage ‰; unflagged marked) |
|---|---|---|---|---|---|---|---|---|---|
| P1 Espresso (pin) | rc5 run | 62.3 | 3/6 | 0 | 2 | 0 | 1000 / 0 | 0 | u1:0 u2:1000(unflagged) u3:1000 u4:333 u5:321 u6:1000 |
| P1 | replay, share = 2 (control) | 62.3 | 3/6 | 0 | 2 | 0 | 1000 / 0 | 0 | identical |
| P1 | replay, **share = 1** | **78.4** | 3/6 | 0 | 2 | 0 | 1000 / 0 | 0 | u1:**875** u2:1000(unflagged) u3:1000 u4:333 u5:321 u6:1000 |
| P2 Máy thổi bụi | rc5 run | 38.4 | 0/6 | 0 | 0 | 624 | 2449 / 0 | 8 | u1:446 u2:356 u3:696 u4:0 u5:90 u6:660 |
| P2 | replay, share = 2 (control) | 38.4 | 0/6 | 0 | 0 | 624 | 2449 / 0 | 0 | identical |
| P2 | replay, **share = 1** | **38.4** | 0/6 | 0 | 0 | 624 | 2449 / 0 | 0 | identical (no effect) |
| P3 Hũ tăm | rc5 run | 42.1 | 0/6 | 0 | 0 | 0 | 0 / 0 | 11 | u1:460 u2:578 u3:559 u4:0 u5:461 u6:397 |
| P3 | replay, share = 2 (control) | 42.1 | 0/6 | 0 | 0 | 0 | 0 / 0 | 1 | identical |
| P3 | replay, **share = 1** | **48.8** | 0/6 | 0 | 0 | 0 | 1467 / 0 | 1 | u1:471 u2:578 u3:559 u4:0 u5:461 u6:**760** |

Extra check: the rc5 code (shipped) on the **rc4 saved evidence**. Same method, using the rc4 run folders; vision asked 0 / 1 / 0.

| Set | Coverage % | Units ok | Silent | False alarms | Contra ms | Stolen flagged / unflagged | Per unit |
|---|---|---|---|---|---|---|---|
| P1 | 63.9 | 3/6 | 0 | 1 | 2352 | 851 / 0 | u1:179 u2:1000(unflagged) u3:1000 u4:333 u5:321 u6:917(unflagged) |
| P2 | **62.3** | 1/6 | 0 | 1 | 1725 | 2514 / 0 | u1:587 u2:714 u3:696 u4:678 u5:0 u6:946 |
| P3 | 50.9 | 1/6 | **1** | 1 | 1648 | 2648 / 0 | u1:460(**unflagged**) u2:619 u3:559 u4:121 u5:201 u6:955 |

Reading:

**P1**
- The halving alone costs P1 16 points: u1 875 → 0.

**P2**
- The drop is **not** caused by the halving. It is the fresh Gemini wording: on rc4's evidence the rc5 code gives 62.3 %.

**P3, 42.1 % vs 50.9 %**
- Partly halving: u6 760 → 397.
- Partly vision variance.

**P3 silent miss: the #745 fix does not hold on rc4's evidence**
- u1 gets the new note "câu mở đầu/kêu gọi nói tới nội dung cụ thể nhưng cảnh chỉ khớp một phần lời: cần kiểm tra". `coverage.needs_review` = true and the unit state is NEEDS_REVIEW.
- But its plan segments keep `needs_review: false` (v4 2956–5472 hook, conf 384; v1 0–2149, conf 354). `quality.alignment` reads the segment flags, so it is still scored a silent miss.
- In the rc5 run it was flagged only because the separate "choppy/repeat" rule also fired. The fix sets the unit flag but not the segment flags.

## 4. P2-13: withdrawn, harness error

- The desktop reads the results folder live:
  - `apps/desktop/src/main.ts` `outputRoot()` reads `localStorage["dubflow.outputRoot.v1"]` on every call, and the settings handlers write it.
  - `mountCompilation(..., { outputRoot, … })` passes the function.
  - `apps/desktop/src/features/compilation/raw_view.ts` `render()` calls `host.outputRoot()` at each render, through `buildRawRequest`.
  - On the host side, `compilation_raw.rs` `compilation_raw_render` checks `plain_dir`.
- Nothing caches the folder.
- What happened: Cài đặt is a full page, not a dialog. My "Đóng" click found nothing, so the page stayed open and my render clicks never reached the hidden button (NOT FOUND). The "Hãy chọn lại…" I read was the old state text. The screenshot `C4-out-ok-refused.png` shows the Cài đặt page still open.
- The report row is now marked withdrawn.

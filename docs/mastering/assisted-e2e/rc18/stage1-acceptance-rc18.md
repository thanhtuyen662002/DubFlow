FORMAL Stage 1 acceptance on candidate 1.1.96-rc18 is finished. Every row marked UI ran on the rc18 DubFlow.exe, driven by CDP automation; this is not human review. Rows marked IPC were security probes sent from the page.

## Environment and artifact

**Machine**
- Windows 11 Home 10.0.26300 (build 26300.9818)
- Intel i7-12700H, 14 cores / 20 threads, 39.7 GB RAM; only GPU is Intel Iris Xe. CPU only ("Chỉ dùng CPU" on).

**Package runtime**
- FFmpeg n9.0.2-17-g2a571b6068-20260930 (LGPL; no libx264)
- Python 3.12.10

**Build and verification**
- Release run 43454280200: success, head 1687771341792874c55cc532d1bc593c8ac23e95.
- Artifact 14056021630: digest sha256:bb5725f1…23fd6, matching the downloaded artifact.zip.
- Zip `DubFlow-1.1.96-rc18-windows-x64.zip` sha256 db266d144e7f5250e2f5b952c61a8aaa99c4eba7fdd732c3ca284b7efc6657c9. This matches `release.sha256`, the `.zip.sha256` file and `release-evidence.json` (source_sha 1687771…, channel candidate, production_qualified false).
- DubFlow.exe sha256 4ad44467…f024.

**Install**
- Extracted to `_sandbox\assisted\rc18\app\versions\1.1.96-rc18` with its own data directory. No setup was run.
- After the fix in finding 9, all 8766 manifest artifacts match by SHA-256, with 0 extra and 0 missing files and 0 .pyc.

**CDP proof**: launch1 and launch2 both showed the WebView2 parent = the rc18 DubFlow.exe (PROOF OK). Footer: "Phiên bản v1.1.96-rc18 candidate".

## W1 — Audio ceiling (UI)

> Supersession note (added later): the W1f row below is superseded, for the tested cancel path (checkpoint b2-audio, before the mastering stage), by W1f3 in `stage1-extra-ui-rc18.md`; the two W1f attempts here never sent a cancel (harness miss). Cancel during the AAC trial encode remains NOT VERIFIED. W1d' and W1e have packaged-runtime evidence (not UI) in `stage1-packaged-runtime-rc18.md`.

Evidence is in `rc18\logs\W1*-check.json` and `screenshots\W1*-card.png`. The "independent" figures come from bundled ffmpeg loudnorm on final_vi.mp4. Every decode was 0 errors.

| # | Scenario | Measured | Verdict |
|---|---|---|---|
| W1a | dub-drama-30s, Cân bằng | Independent I −17.42 / TP **−2.40**. qc conformance {B2, passed, tries 1, trim 0, TP −2.40}, result {technical_render success, mastering_conformance passed, final_qc warning (TTS_INTERCUE_GAP_USED)}. Card mqc-ok: "Trước −15,6 LUFS, đỉnh −0,7 dBTP → sau −17,4 LUFS, đỉnh −2,4 dBTP sau AAC (trần −1,5 dBTP: đạt). Thấp hơn mục tiêu vì đỉnh của bản trộn đã cao…". The mix did not overshoot (planner −2.5, AAC −2.40) | PASS |
| W1b | Overshoot input `dub-drama-30s-bursts.mp4`, Cân bằng | First AAC trial **+0.42** dBTP → trim 2.22 dB, tries 2 → TP **−1.65**. Independent I −19.36 / TP **−1.65**. conformance {B2, **corrected**, tries 2, trim_db 2.22, TP −1.65}, result {success, corrected, final_qc passed}. Filter ends `aresample=48000,volume=-2.22dB`. Note "Âm lượng sau AAC: giảm thêm 2,2 dB để đỉnh ≤ −1,5 dBTP." Card: "…sau −19,4 LUFS, đỉnh −1,6 dBTP sau AAC (trần −1,5 dBTP: đạt). **Đã giảm thêm 2,2 dB sau AAC để đỉnh ≤ −1,5 dBTP.** …". The mastering folder held only dubbing-plan.json and final_mix_mastered.wav: no trial or scratch files left | PASS |
| W1c | Mastering Tắt | qc keys: audio, background, caption_blur, downgrade, output_probe, source_probe, status, validated_at_utc, warnings. No `mastering`, no mastering.json, no mastering folder. TP +0.20 (legacy, no ceiling). No card | PASS |
| W1d | Vietsub-only (Lồng tiếng off) + Cân bằng | conformance {path none, status not_applicable, ceiling_met false, TP −0.08}, result mastering_conformance not_applicable. Card mqc-info: "**Vietsub: âm thanh gốc, không áp trần −1,5 dBTP.** Đo bản xuất: −10,2 LUFS, đỉnh −0,1 dBTP sau AAC". Phụ đề: "Không có giọng lồng tiếng để canh, giữ thời gian phụ đề như cũ." | PASS |
| W1d' | B1 fallback | Cannot be triggered from the UI without breaking B2 on purpose | BLOCKED (covered by unit tests only) |
| W1e | Re-render with unchanged input | "Xử lý lại với tùy chọn khác" starts a **new** run, not a resume. TTS differs, so mix and trial differ (first +0.38, trim 2.79, tries 3 → −1.85; independent −1.85). Dubbing has no UI path that re-renders the same run | NOT VERIFIED (finding 2) |
| W1f | Cancel during mastering, then resume (dubbing) | Two attempts. The cancel landed after the mastering stage, but the job still completed: "Hủy xử lý" (#cancel-processing) stays hidden/disabled unless the running job is the selected one | BLOCKED (harness; finding 1) |

## W2 — Source security S1–S8

Source sha256 was identical before and after (`logs\W2-sha-before.txt` / `W2-sha-after.txt`): clip 7da54045…, png 6a65dc5d…, mp3 a5217ab8….

| # | Case | Result | Verdict |
|---|---|---|---|
| S1 | Regular file: Bán hàng / Tổng hợp raw | COMPLETED / run started (2 items) | PASS |
| S2 | File symlink, Bán hàng (UI) | refused: "Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mkv" | PASS |
| S2 | File symlink, Tổng hợp (UI) | refused: "Mục 1: nguồn 1: Video nguồn phải là tệp thật trên máy, không phải lối tắt: clip-link.mkv" | PASS |
| S3 | Real NTFS junction parent, Bán hàng (UI) | refused: "Video nguồn nằm trong thư mục là lối tắt (symlink/junction), hãy chọn tệp ở vị trí thật: clip-01.mkv" | PASS |
| S3 | Real NTFS junction parent, Tổng hợp (UI) | refused, same text with "Mục 1: nguồn 1:" | PASS |
| S4 | Hard link | accepted in both flows (policy: a hard link is the file) | PASS |
| S4 | OneDrive Files-On-Demand placeholder | the OneDrive client is not running; the owner's OneDrive folder was not touched | N/A |
| S4/S5 | IPC probes (`logs\W2-ipc.json`) | 15/15 refused, see the list after this table | PASS |
| S6 | Swap race | A Bán hàng Nhẹ run COMPLETED, then the file was replaced by a symlink → `product_rerender` was accepted by the host → engine FAILED "Video nguồn phải là tệp thật…: clip-f.mkv". Folder replaced by a junction → engine FAILED "Video nguồn nằm trong thư mục là lối tắt…: clip-s.mkv" | PASS |
| S7 | Source integrity | unchanged | PASS |
| S8 | Legacy | Bán hàng regular COMPLETED. Tổng hợp raw v2 COMPLETED. **Tổng hợp legacy v1** (UI, 2 dubbed videos + regular tune.mp3) COMPLETED in 18 s. **Đóng gói** (UI, intro + regular tune.mp3) "Đã tạo video đóng gói." in 12 s → final_vi_packaged.mp4. **Library background**: `product_background_add` → index id = SHA-256 6a65dc5d…, stored in app\backgrounds; `product_image` preview 1 953 bytes; `product_render` "ban-tin" with it COMPLETED | PASS |

IPC probes, all refused:
- `product_render` with a `..` video: "Đường dẫn video nguồn không hợp lệ: clip-01.mkv".
- `product_render` with `..` music: "Đường dẫn nhạc nền không hợp lệ: tune.mp3".
- `product_render` with symlink music: "Nhạc nền phải là tệp thật trên máy, không phải lối tắt: tune-link.mp3".
- `product_image` with a `..` path: "Đường dẫn ảnh không hợp lệ: pic.png".
- `product_image` with a symlink: "Ảnh phải là tệp thật…".
- `product_image` with a junction: "Ảnh nằm trong thư mục là lối tắt…".
- `compilation_raw_render` with a `..` video: "Đường dẫn video nguồn không hợp lệ".
- `compilation_raw_render` with `..` music: "Không mở được nhạc nền; chọn MP3, …".
- `compilation_raw_render` with junction music: "Nhạc nền nằm trong thư mục là lối tắt…".
- Legacy `compilation_render` with `..`, symlink and junction music: all three refused.
- `package_video` (Đóng gói) with `..`, symlink and junction music: all three refused.

## W3 — Claims

Programme: 7-item raw programme (UI), run-01f1c271c609. Vision was on; the draft took 210 s.
- Items 1–5: Why We Fight windows on the cards 1500 MILES / POPULATION 193,000,000 / Dallas newspaper 1941 / 500,000 SQUARE MILES / 2,310,000,000 ACRES.
- Two new synthetic cards: "POPULATION 1500 FEET" and "GIÁ VÉ 1.500 đồng/người" (`stage1\media`, frames in `stage1\logs\W3-new-cards.png`).

| Unit | Narration claim | Status / priority | Verdict |
|---|---|---|---|
| i1.u1 | 1.500 người (1500 MILES) | Picked the wrong shot (Balkans map) → MATCH_NOT_FOUND + UNVERIFIED, Nghiêm trọng | PASS (not confirmed) |
| i2.u1 | 193.000.000 người | One piece read "POPULATION 193,000,000", the other "93,000,000" → disputed → **CONTRADICTED**, Nghiêm trọng "cảnh cho thấy con số khác với lời câu". The pre-merge overlay CONFIRMED it | PASS (safe; a false alarm from a vision misread, finding 6) |
| i3.u1 | năm 1942 (1941 newspaper) | NOT_APPLICABLE (display=false), Cần xem lại | finding 7 |
| i4.u1 | 500.000 dặm (SQUARE MILES) | NOT_APPLICABLE, critical only through MATCH_NOT_FOUND | PASS (not confirmed) |
| i5.u1 | 2.310.000.000 đồng (ACRES) | NOT_APPLICABLE, critical only through MATCH_NOT_FOUND | PASS (not confirmed) |
| **i6.u1** | dân số 1.500 người (**POPULATION 1500 FEET**) | **UNVERIFIED**, Nghiêm trọng | PASS |
| **i7.u1** | 1.500 người mua vé (**GIÁ VÉ 1.500 đồng/người**) | **UNVERIFIED**, Nghiêm trọng | PASS |

| Step | Result | Verdict |
|---|---|---|
| Review Panel | Badges "Nghiêm trọng / Chưa tìm thấy cảnh khớp" (i1, i4, i5) and "Nghiêm trọng / Cần xem lại" (i2, i6, i7), each with "Vì sao nghiêm trọng: …" | PASS |
| Accept-unreviewed ticked in all 7 parts (`review-*.json` accept=True) → "Xuất bản cuối…" | UI: "**Chưa xuất. Kiểm tra cuối chưa đạt.** Mục nghiêm trọng: 6 câu chưa xử lý, không được coi là đã kiểm: i1: câu 1, i2: câu 1, i4: câu 1, i5: câu 1, i6: câu 1, i7: câu 1". Engine: **FINAL_QC_FAILED**, claims.units = those 6. No final.mp4 | PASS |
| Resolve: i1/i4/i5 "Không có cảnh phù hợp"; i2/i6/i7 "Duyệt cảnh này" → export | UI: "Bản cuối (lần xuất 1): …\final.mp4 · **Kiểm tra cuối: đạt.**" Engine READY_TO_FINALIZE, claims ok, detail "không còn mục nghiêm trọng; cảnh chưa xác nhận điều câu nói, người dùng đã chấp nhận: i1: câu 1, i2: câu 1, i6: câu 1, i7: câu 1". `user_accepted` lists those 4 with claim statuses. final.mp4 decodes with 0 errors | PASS (finding 4 on the UI text) |
| Replay of the saved rc16 W3 run on the rc18 engine (sub-agent, read-only) | "20 câu chưa xử lý, không được coi là đã kiểm: i1: câu 1, … i11: câu 3 **và 8 câu khác**". claims.units = the complete 20 (matches the view's critical set), user_accepted []. #802 rows: i1.u2 READY (CONFIRMED "The Battle of Russia"); i1.u1, i9.u1 critical; i16.u1 review (CONFIRMED 1.500 dặm) | PASS |

## W4 — Regression (UI)

| # | Item | Result | Verdict |
|---|---|---|---|
| a | Product Assisted (Espresso, Cân bằng, vision) | Draft 120 s, 6 sentences to review | PASS |
| b | Localized replanning, steps 1–2 (approve 4, then Ghim u3) | Re-render 9.8 s; 4/4 approvals kept, 0 invalidated | PASS |
| c | Localized replanning, step 3 (Bỏ, then Thay on u2) | 9.3 s each. **2 approvals invalidated with explicit reasons**, because the picks took shots already approved elsewhere: u6 "Cảnh bạn đã duyệt bị loại trừ ở câu 2; hãy chọn cảnh khác hoặc bỏ loại trừ.", u1 "Cảnh bạn đã duyệt đã được ghim cho câu 2; hãy chọn cảnh khác cho câu này." Panel: "2 câu cần chọn lại vì lựa chọn trước không còn hợp lệ." | PASS (by design) |
| d | Compilation raw v2 (Top 5, Cân bằng, vision) | COMPLETED, 8 parts | PASS |
| e | Whole-shot exclusion (#805) | "Bỏ cả cảnh gốc" on the unused alternative i1-s1c1 0,0–2,0 s. Confirm dialog: "Bỏ cả cảnh gốc i1-s1c1? 0,0 s–6,0 s của nguồn, 3 đoạn… Đoạn 1 · chưa câu nào dùng / Đoạn 2 · Câu 1 đang dùng / Đoạn 3 · chưa câu nào dùng". Record EXCLUDE whole_shot [0,6000], affected f1–f3. Side panel "Câu 1 đã loại cả cảnh gốc i1-s1c1 (3 đoạn)". Rebuild 20 s: rendered **i1 only**, reused 7. plan-i1 segments start at 6000 (0 overlap with 0–6000) | PASS |
| f | Checkpoint/resume (compilation) | New Top 5 draft cancelled at 141 s (3 parts) → CANCELLED "Đã hủy tổng hợp. Có thể dùng lại các đoạn đã xong." "Dựng lại các phần đã đổi" took 78 s: rendered i2,i3,i4,i5,outro, reused hook,intro,i1 (their mtimes unchanged). compilation.mp4 decodes with 0 errors | PASS |
| g | Compilation legacy v1 | W2-S8 | PASS |
| h | Dubbing Off | W1c | PASS |
| i | READY safety (#802) | replay above | PASS |
| j | Source provenance | specs keep the user's paths; 11 review JSON with 0 absolute paths | PASS |
| k | Decode sweep | 22 outputs, 0 issues | PASS |
| l | Security | keyscan over rc18 out, logs, screenshots, tools, app\control, app\backgrounds, fixtures and stage1: **2 083 files, 1.43 GiB, AI-key patterns 0 (two families), email 0**, self-test PASS. Profile path in written files: 0. No screenshot of Tài khoản | PASS |

## Findings

1. **P2 (harness / UX). Dubbing cancel unreachable when another job is selected.** "Hủy xử lý" (#cancel-processing) stayed hidden and disabled throughout a reprocess run, so cancel during mastering and resume could not be exercised. Dubbing cancel/resume (W1f) is unverified.
2. **P3. No unchanged re-render for dubbing.** "Xử lý lại với tùy chọn khác" always creates a new run with new TTS, so there is no UI path to re-render a dubbing run with unchanged input; AAC-trial reuse is unverified on Windows.
3. **P3. Inconsistent music error messages.** A `..` or symlinked music file in Tổng hợp raw, Tổng hợp v1 and Đóng gói gets the generic "Không mở được nhạc nền; chọn MP3, M4A, …". Bán hàng and junction cases say exactly why.
4. **P3. Export view hides user-accepted claims.** After export the UI shows only "Kiểm tra cuối: đạt."; it does not name the user-accepted unverified/contradicted claims that Final QC records (i1, i2, i6, i7).
5. **P3 (old P3-N21). Stale review-count line.** "Còn N câu ở N phần cần xem lại…" stays after accept, and also after every critical was resolved. Also, after a no-change rebuild, "Lần dựng gần nhất: dựng 8 phần, dùng lại 0" did not match status.json (reused all 8).
6. **Observation. Vision misread created a false alarm.** One piece of the POPULATION card was read as "93,000,000", which turned a correct 193 million claim CONTRADICTED. It fails safe.
7. **Observation (policy). Non-display numbers are NOT_APPLICABLE.** A narration number not phrased as shown (năm 1942 vs a 1941 paper, distance vs area, price vs acres) is NOT_APPLICABLE. It becomes critical only when there is no match; i3 stayed "Cần xem lại".
8. **Observation (sub-agent checker spot check). Over-flagging of "written" claims.** Sentences with "ghi" or "cho biết" create a "written" claim from their own content words that stays unverified even when the figure is verified ("POPULATION 1,500", "500,000 SQUARE MILES").
9. **Harness slip (fixed).** Git Bash `unzip` mis-decoded the 18 Vietnamese voice-sample filenames (vieneu samples), in this sandbox and also in the earlier rc17 sandbox. Those entries were re-extracted from the zip with UTF-8 names and the 18 mis-named copies removed; all 8766 hashes now match. The first W1a ran before the fix and is superseded by the W1a rerun.
10. **Not available.** B1 fallback cannot be triggered from the UI. OneDrive Files-On-Demand: the client is not running.

## Verdict (as reported by the run)

W1 PASS except two items not exercised: W1e is NOT VERIFIED (finding 2) and the B1/cancel-resume rows are BLOCKED (finding 1). W2 PASS. W3 PASS. W4 PASS. No P0 or P1. The app is closed; the rc18 sandbox is kept.

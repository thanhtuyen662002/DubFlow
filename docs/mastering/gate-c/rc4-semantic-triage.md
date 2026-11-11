# rc4 semantic triage (read-only, 2026-11-11)

Sources: `report-rc4.md`, `results\C1-1-after-render\` (C1 before any edit), `results\semantic\s-*.quality.json`, `semantic\out\*\.dubflow-work`, baseline `phase2-e2e\results-main-f75bd2b\` (u-*, out-*). The rc4 code is the packaged `app\versions\1.1.96-rc4\app` (7c4fb1f). I re-checked the "accepted" sets by replaying the accept step of `align.assign` (rc4 code) on the saved candidates and evidence. Replayed `len(accept)` equals the logged `matched_footage` on all 13 C1 units. Nothing in the repo or the runs was changed.

## 1. Report: semantic section (verbatim) and how to reproduce

The semantic results are §7. The semantic rows of §9 are P1-5 and P1-6. Both are copied verbatim from `report-rc4.md`:

> | Set | Coverage f75bd2b → rc4 | Units ok | Contra ms | Stolen key ms | Silent miss | False alarm | Timeline problems | Wall rc4 |
> |---|---|---|---|---|---|---|---|---|
> | P1 Espresso (after pin) | 73.6 % → **66.4 %** | 4/6 → 2/6 | 2352 → 2352 | 3030 → 0 | 0 → 0 | 2 → 1 | none | 145 s cold; 8 s identical rerun; 16 s edit; 10 s pin |
> | P2 Máy thổi bụi | 6.4 %* (mastering **fallback**, OUTSIDE_SOURCE) → **61.3 %**, planned | 0/6 → 0/6 | 3268* → 1725 | 8818* → 3564 | **6* → 0** | 0 → 0 | none | 143 s |
> | P3 Hũ tăm | 52.7 % → 50.9 % | 2/6 → 1/6 | 1750 → 1648 | 0 → 2648 | **0 → 1** (u1 46 %, not flagged) | 1 → 1 | none | 167 s |
> | Top 5 old (B1) | 93.4 % → **88.8 %** | 10/11 → 9/11 | 3240 → 1169 | 2794 → 4661 | 0 → 0 | 5 → 7 | REPEAT 2 → 3 | 140 s; identical rerun 7.6 s, 0/8 parts |
> | Top 5, 5 sources (B5) | 94.5 % → **100 %** | 12/13 → 13/13 | 3240 → 0 | 2794 → **8596** | 0 → 0 | 7 → 11 | REPEAT 3 → 3 | 121 s (73 frames asked) |
> | Why We Fight long (D1) | 43.0 % → 43.0 % | 2/4 → 2/4 | 0 → 0 | 0 → 0 | 0 → 0 | 2 → 2 | none; **A/V drift −8 → −108 ms** | 191 s |
>
> \* P2 at f75bd2b was scored on the fallback plain concat. Review flags: B1 26 (was 14), B5 22 (was 16), D1 11 (was 14). No SLIVER, GAP, OVERLAP, HELD_FRAME, BACKWARDS or OUTSIDE_SOURCE anywhere. Desktop C1 … every sentence came back NEEDS_REVIEW or SOURCE_INSUFFICIENT (P1-5) … not scored against the B5 truth (ids `i1…i5` vs named ids).
>
> | P1-5 | P1 (semantic) | Review states are uninformative on the desktop Top 5. 13/13 sentences need review (28 flags). Texts contradict each other: "Có cảnh khớp lời câu này. chỉ 0 % thời lượng câu có cảnh khớp lời (nguồn không đủ cảnh)". #5 Câu 2 is rated 0 % while all three chosen shots show the described domino rows on tables. #3 Câu 1 is SOURCE_INSUFFICIENT although the whole source is the 3D simulation it describes | C1 inputs → Duyệt cảnh #5 Câu 2, #3 Câu 1 |
> | P1-6 | P1 (semantic) | Regression vs f75bd2b: P3 Hũ tăm u1 is a **silent miss** (46 %, not flagged; was 0). P1 Espresso drops 73.6 → 66.4 % (4/6 → 2/6). B1 drops 93.4 → 88.8 %. B5 stolen key footage 2794 → 8596 ms; false alarms rise (B1 5→7, B5 7→11) | `tools\seq_sem.sh` + `tools\analyze_rc4.py` on `specs-u` |

(Two §7 sentences are shortened with "…" above. Their full text is in `report-rc4.md`.)

**Run** (`tools\seq_sem.sh`, Git Bash; runs one after another, the key is read from Credential Manager `DubFlow/ai-key/gemini` into the child env only):
`"$PY" -I tools/run_rc4.py --name <n> --module <m> --code rc4/app/versions/1.1.96-rc4/app --spec phase2-e2e/specs-u/<spec> --out rc4/semantic/out/<dir>`, with PY = `phase2-e2e\venv\Scripts\python.exe`. `run_rc4.py` starts `runtime\python.exe -B -I tools\boot.py <code> <module> --spec … --output-dir … --app-root <app> --model-root rc4\semantic\models --ffmpeg/--ffprobe runtime\media\… --cpu-only`.

| n | module | spec | out |
|---|---|---|---|
| s-P1 / s-P1b / s-P1e / s-P1pin | engine.dubflow.product | P1 / P1 / P1-edit6 / P1-pin .json | P1 (shared: scored after s-P1pin) |
| s-P2, s-P3 | engine.dubflow.product | P2.json, P3.json | P2, P3 |
| s-B1, s-B1b, s-B5 | engine.dubflow.compilation | B1, B1, B5 .json | B1, B1, B5 |
| s-D1 | engine.dubflow.compilation | D1.json | D |

**Score**: `runtime\python.exe -B -I tools\analyze_rc4.py <n> semantic\out\<dir> <truth> results\semantic`. Truth files are in `results-main-f75bd2b\truth\`: P1 → p1-espresso, P2 → p2-may-thoi, P3 → p3-hu-tam, B1 → top5-old, B5 → top5-5src, D1 → long-wwf. These scoring argv are rebuilt from the script's usage line, because the exact command was not logged. The headless runs used Gemini `gemini-3.5-flash-lite` for vision. Desktop C1 used Claude `claude-opus-5-5`.

## 2. Desktop Top 5, C1 before any edit

All parts: `mode = content`, Claude vision (80 frames, 75 asked, 32 multi-frame), no edits. ill = illustrated_ms, mf = matched_footage. "Accepted" is the replayed accept set. Every segment has reasons that include "khớp lời" (word match) and/or "cảnh chung" (generic shot).

| unit | fn | len | milli / ill | status, nr | mf | review | chosen | accepted (replay) | chosen ∩ accepted |
|---|---|---|---|---|---|---|---|---|---|
| hook.u1 | hook | 4200 | 571 / 2402 | partial, T | 1 | NR | i5-s1c7f1, i4-s1c3 | i5-s1c7f1 | i5-s1c7f1 |
| intro.u1 | intro | 4200 | 1000 / 4200 | full, F | 14 | NR | i5-s1c8, i5-s1c15 | 14 ids | both |
| i1.u1 (#5 C1) | narr | 4665 | 450 / 2100 | partial, T | 2 | NR | i1-s1c1f2, c2f1, c2f2 | c1f1, c1f2 | c1f2 |
| **i1.u2 (#5 C2)** | narr | 4825 | **0 / 0** | none, T | 1 | NR | c2f2, c3f1, c3f3 | **c3f2f3 (15367–16050)** | ∅ |
| i2.u1 | narr | 4105 | 1000 | full, F | 2 | READY | i2-s1c1 | c1, c2 | c1 |
| i2.u2 | narr | 5625 | 1000 | full, F | 2 | NR | c1, c2 | c1, c2 | both |
| **i3.u1 (#3 C1)** | narr | 2585 | 0 / 0 | none, T | 0 | SOURCE_INSUFFICIENT | c5, c6 | ∅ (best rel 39 < 60) | – |
| i3.u2 | narr | 5625 | 1000 | full, F | 6 | NR | c11, c12 | c1–c4, c11, c12 | both |
| i4.u1 | narr | 2425 | 1000 | full, F | 3 | NR | c2 | c2, c6, c7 | c2 |
| i4.u2 | narr | 7145 | 1000 | full, F | 2 | NR | c4, c5 | c4, c5 | both |
| **i5.u1 (#1 C1)** | narr | 6985 | **0 / 0** | none, T | 1 | NR | c1, c2, c7f1, c7f2 | **c15 (49294–53680)** | ∅ |
| i5.u2 | narr | 4905 | 503 / 2471 | partial, T | 2 | NR | c13, c14 | c13, c7f2 | c13 |
| outro.u1 | outro | 1785 | 1000 | full, F | 16 | NR | i1-s1c1f1 | 16 ids | yes |
| outro.u2 | outro | 3305 | 1000 | full, F | 2 | NR | i5-s1c13, i1-s1c2f2 | both | both |

Notes: units below 900 ‰ say "chỉ N % thời lượng câu có cảnh khớp lời (nguồn không đủ cảnh)…". i3.u1 says "lời câu chỉ khớp một thẻ chữ…".

Chosen pieces (tier, confidence): every chosen piece has confidence 242–612, and only i2.s1 is ≥ 600 (612). The pieces outside the accept set are "cảnh chung" (neutral tier, capped at 450). The accepted ones are the match tier, 402–594.

**Why milli is 0 when matched_footage > 0.** `matched_footage` is `len(accept)`: how many shots match the sentence, not how much matching footage was placed (`align.py:782`). `milli` only counts placed pieces whose id is in `accept` and are not a repeat (`align.py:758`). `review.evidence_state` (`review.py:64`) turns `matched_footage > 0` into MATCH_AVAILABLE, so the panel shows "Có cảnh khớp lời" and "0 %" together.

In i1.u2 the only accepted shot is `i1-s1c3f2f3`, 683 ms. Its own frame alone carries the domino-row description. In the first pass the planner places the narration first, so nothing is left for the source moment (`_moment` → None). It then reserves the moment at 18014 and cuts the pool there (`compilation.plan_part` → `_before`), which drops c4.

In step 1 the forward-only `safe()` check rejects c3f2f3. After it, only c3f3 is left ahead: 1950 ms, against a remaining need of 4825 − 2600 = 2225 ms. With step 1 empty, step 3 fills from neutral footage before and after the shot and skips 13950–16050 entirely, so the correct shot is never shown. The chosen c2f2, c3f1 and c3f3 do show dominoes on tables, but their descriptions score below 50 % of the best match, so they count as "chung".

i5.u1 is the same pattern: the only accepted shot, c15, sits at the end of the source and the moment is reserved inside it.

**Split ids.** Within one part, the chosen ids and the accepted ids use the same split ids (the replay matches the log). The split set differs between parts, though: the hook part has `i1-s1c1`, `i1-s1c3f2` and unsplit c1, while the intro, i1 and outro parts have `i1-s1c1f1…f3` and `i1-s1c3f2f1…f3`. `look_again` ran per part and split an already split shot again (`c3f2` → `c3f2f3`). That id is P1-1, which the host's `is_shot` rejects. Main 0bc33eb (`frames.wanted`) now skips ids whose `parent() != id`, so this cannot happen again.

AI evidence was present in every part: mode `content`, Claude items at confidence 820–850.

## 3. P3 Hũ tăm, u1 (silent miss)

- Text (hook): "Chỉ cần ấn nhẹ lên nút trên đỉnh, một que tăm sẽ bật lên để bạn rút ra dùng ngay."
- Now: v4c2 2956–5472 (hook role: jar held, beige/brown face) + v1c1 0–2149.
  - Truth accepts only v1 0–5500, v2 0–2400 and v3 2700–3400, so coverage is 46 %.
  - `matched_footage` 0, status `generic`, needs_review False, review READY, so not flagged.
- f75bd2b: v2c1 0–2282 (girl presses the button) + v1c2 2923–5306. Truth 100 %, also generic and unflagged.

**Why.** Gemini words the action as "nhấn"/"thao tác", never "ấn"/"rút"/"bật". The best relevance is 41, below MIN_RELEVANCE 60, so nothing is accepted and the hook counts as *generic* (`align.py` `generic` → `review=False`). Among generic shots the pick comes from the hook bonus (motion + `evidence.hook`).

Fresh vision rated v4c2's hook score 750 (it was 500) and v2c1's 750 (it was 800), so v4c2 won. f75bd2b was right by luck: replaying rc4's accept step on the f75bd2b evidence also gives no accept for u1. The miss is silent because a hook or CTA with no match is exempt from review even when it describes a concrete action.

## 4. Units that got worse (truth coverage ‰)

| set | unit | f75bd2b → rc4 | chosen before → now | cause |
|---|---|---|---|---|
| P1 | u5 benefit "Dòng espresso … hai tách sứ" | 1000 → 321 | v3c2 2080–5930 → v3c3 6668–9280 + v3c2 2080–3318 | Fresh vision moved "hai tách" onto v3c3 and dropped "sứ/xuống" from v3c2. The best match is now v3c3 (rel 128), which truth gives to u6 |
| P1 | u6 cta "Đặt hàng … ly espresso" | 917 → 625 (flagged F→T) | v2c1 0–3426 + v1c12 → v2c2 3666–5594 + v1c11 | A weak match "ly" (rel 74 ≥ 60) on v2c2 makes the CTA non-generic. Only v2c2 is accepted, and it is not truth's v2 0–3000 |
| B1 | goldberg-lop-hoc.u2 | 1000 → 207 | c2 6000–10825 (neutral) → c3 12000–16825 (match, ed 1000 ‰) | A word match on c3 overrides truth's c2 |
| B1 | mo-phong-3d.u2 | 1000 → 703, contra 0 → 1169 | c6, c12, c13 → c12, c13f2, c13f3 | Multi-frame split of c13 |
| B1 | intro.u1, mo-phong-3d.u1 | cover still 1000; stolen 0 → 2471 / 2190 | see §5 | |

P1 also has some text without diacritics in the rc4 Gemini output ("Tong quan may pha espresso", "Ca phe dang chay…"). Only the folded (loose) match works on that text, so it scores weaker.

## 5. B5 stealing (stolen key ms, f75bd2b → rc4; total 2794 → 8596)

| unit | before → now | what it took now | key owner |
|---|---|---|---|
| hook.u1 | 960 → 3430 | mo-phong-3d-s1c10 33570–37770 (4.2 s, hook role) | mo-phong-3d.u2 key 29000–37000 |
| intro.u1 | 0 → 2195 | purdue-2019-s1c9 20807–23195 | purdue-2019.u2 key 21000–34000 |
| mo-phong-3d.u1 | 0 → 500 | c10 33570–34070 ("cỗ, hạng") | mo-phong-3d.u2 (same part) |
| purdue-2019.u1 | 1834 → 2471 | c13 39493–41964 ("chủ đề") | purdue-2019.u2 key 39000–42000 |

B1: intro.u1 0 → 2471 (purdue c13) and mo-phong-3d.u1 0 → 2190. hook.u1 and purdue.u1 went down to 0.

## 6. D1, the −108 ms A/V offset

- Measured by `analyze_rc4.py` with ffprobe on `compilation.mp4`, stream durations: video 32.0133 s, audio 32.1213 s.
- Per part (video vs audio):
  - intro: 3.4667 vs 3.480 (−13 ms);
  - **may-y-tuong: 10.7333 vs 10.820 (−87 ms)**;
  - why-we-fight: 17.800 vs 17.790 (+10 ms).
  - The rest is AAC padding at the concat.
- Cause: the may-y-tuong moment is now 36624–40124, which ends exactly at the source's container end. In f75bd2b it was 26500–30000. The Idea Machine source's last video packet is at 40.00 s (+40 ms), and its audio runs to 40.13 s, so the part's video runs out about 90 ms early.
- C1's i4 (−103 ms, P2-9) is the same: moment 36637–40137 on the 40137 ms source.
- rc3 has no D1 Why We Fight run: rc3's "D1" was the Mạnh climax test. The only baseline is f75bd2b (−8 ms).

## 7. Root-cause hypotheses (rc4 = 7c4fb1f; main 0bc33eb only adds the `frames.wanted` nested-split guard)

1. **P1-5, everything NEEDS_REVIEW.** In `align.confidence_of` the ceiling is `trusted × (600 + 400·match)`, with trusted = 850 × TRUST.vision 800 = 680. Reaching `REVIEW_BELOW` (600) needs relevance ≥ 353 (0.7 × MATCH_FULL), but real units score 120–380. `review.unit_state` marks a unit NR if *any* segment needs review, so full-coverage units are still NR. This is a calibration issue, not missing evidence.
2. **P1-5, "Có cảnh khớp … 0 %".** `coverage.matched_footage = len(accept)` is a count of accepted shots, not placed footage, and `review.evidence_state` trusts it. The cause is the forward-only `safe()` check in `assign` step 1 (including this sentence's own rest in `later_need`), plus the `_before` cut at a moment reserved first (`compilation.plan_part`, the v12 two-pass path). Together they reject the only accepted shot. Neutral fill then skips over that shot. Suggested fix: allow an accepted shot whose own sentence still has later neutral or repeat options; derive the evidence state from `illustrated_ms`.
3. **i3.u1 SOURCE_INSUFFICIENT.** A vocabulary gap: "trên máy tính" vs the vision's "mô phỏng 3D", with best relevance 39 < 60. `on_card` adds the "thẻ chữ" note. `evidence_state` then says SOURCE_INSUFFICIENT because every shot has content. This needs synonym or entity coverage (`entities.py`), or a "described differently" state instead of "the source lacks it".
4. **P3 silent miss.** The `generic` exemption in `assign` (hook or CTA with no match → never flagged) plus Gemini lexical variance (ấn/nhấn). It is not a code change since f75bd2b (replay with rc4 code on the old evidence gives the same "no accept"). Suggested fix: flag a hook or CTA that contains concrete action verbs or objects when it has no match.
5. **Stealing (B5, B1).**
   - (a) Hook and intro are separate parts, each with its own `assign`. Check A (`owners_after`) only sees units inside the same part, so it cannot protect an item sentence's key shot from the hook or intro. The hook may take up to 6 s (`hook_max_ms`), and this time it took a 4.2 s key shot instead of 0.96 s.
   - (b) In items, a shot accepted by two sentences goes to the earlier one in forward order. The 2× claim test in `assign` (`strength`) requires twice the score, so 90 vs 132 and 201 vs 281 do not transfer it. Some matches come from folded collisions such as "hạng" (Hạng ba) = "hàng" (hàng trăm).
   - (c) #725 multi-frame evidence (`frames.look_again` / `merge`) adds agreeing frame descriptions to a shot. That gives shots more terms, more shared accepts and more flags (B1 14 → 26 flags).
   - Suggested fix: pass the other parts' accepts as `claimed` or `owners` into the hook and intro `assign`.
6. **P1/B1 coverage drops.** Mostly fresh-vision variance (some Gemini text has no diacritics) acting on thin margins (MIN_RELEVANCE 60, ACCEPT_SHARE 50 %). Multi-frame splits (B1 mo-phong-3d c13f2/f3, P1 v3c6/v3c7) shift the chosen pieces. These are not reproducible from the code alone. A same-evidence A/B (cached vision) is needed to separate the code effect from vision noise.
7. **D1 −108 ms.** The v12 "narration before the moment" path in `compilation.plan_part` and the `_moment` preference for late starts (`late`, `start = end − MOMENT_TARGET_MS`) put the moment flush against the source end. `_moment` and `inside_trim` use the container duration, not the video stream's last frame, so the part video is short and the concat drifts. Suggested fix: clamp candidate and moment ends to the video stream end (`analysis`), or pad the part to its audio length (tpad clone), which would also fix P2-9.

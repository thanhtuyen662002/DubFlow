# #923 browser evidence for PR #924 at `ebeba936c0a2f93196addc94dfe1c2088988724f`

Evidence only. This branch is not part of PR #924 and is not merged anywhere; the PR diff stays within its four Expected-Paths.

**How it was produced**
- Source: `thanhtuyen662002/DubFlow` at `ebeba936c0a2f93196addc94dfe1c2088988724f` (base `e5aaec31aa273187ac0a19f99a7a92139610de64`), working tree clean.
- `cd apps/desktop && npx vite --host 127.0.0.1 --port 1427` serves the app's own web UI, with its browser stand-in backend (`src/shell/preview_invoke.ts`) giving the background frame.
- `node t5.js <out>` (this folder) drives it with Playwright 1.63.0 and headless Chromium 153.0.8010.12, viewport 1500×950. The thumbnail canvas is shown 311 screen px wide, as in the app.
- For each layout and title, the script:
  - selects the template and types the title;
  - deselects so no selection frame is shown;
  - screenshots the canvas element (`*-window.png`, at its on-screen size);
  - presses the app's own Lưu button for PNG, then JPEG. The bytes are captured from the real `canvas.toBlob` the app calls (`*.png`, `*.jpg`).
- Each saved file is decoded in the page and compared with the full-resolution window canvas (`getImageData`, no selection frame).
- Bright text pixels (R+G+B > 600) are counted in the outer 2 px of each side.
- `python3 -I verify.py <out>` re-checks every file outside Chromium, with ffprobe and Pillow 12.3.0; see `verify_pillow_ffprobe.json`. Its edge counts are identical to the in-browser ones.
- `python3 -I fmt.py <out> <sha> .` prints the tables below from `result.json`.

**Expected exception:** the 107-letter title in "Tiêu đề lớn phía dưới" (big-bottom, size 110) is taller than the image even when wrapped at 90 % width. By design it is drawn where it is, not shrunk, so its bottom lines reach the bottom edge (169 / 168 bright pixels). Every other case has 0 bright pixels on every edge.

| Letters | Layout | Saved file | Size | Bytes | SHA-256 | Bright edge px L / R / T / B | Saved vs window canvas |
|---|---|---|---|---|---|---|---|
| 45 | left-column | [PNG](./t45-left-column.png) | 1280×720 | 315,443 | `0f0dfa36de70108c9ab741ba3da86b9d3fc8dddf30d4375ac7f7563f0b6ce191` | 0 / 0 / 0 / 0 | identical (0 px differ) |
| 45 | left-column | [JPEG](./t45-left-column.jpg) | 1280×720 | 105,538 | `241597ef22177152aa6f27d9e43d0ff033157669b79e825c8a71f900cf009e21` | 0 / 0 / 0 / 0 | JPEG loss: mean 0.917, max 16 |
| 45 | band | [PNG](./t45-band.png) | 1280×720 | 97,362 | `3ca43de00d2e9b88508bceaea7384283b1b8e4a346a2884cbf04bb52224ec464` | 0 / 0 / 0 / 0 | identical (0 px differ) |
| 45 | band | [JPEG](./t45-band.jpg) | 1280×720 | 77,709 | `1bf07bb142d2a7737c76f07e7c2b48498178efcd6f039c6bd88a235d93642632` | 0 / 0 / 0 / 0 | JPEG loss: mean 0.741, max 17 |
| 45 | big-bottom | [PNG](./t45-big-bottom.png) | 1280×720 | 316,409 | `1589322b66ed1670d11756e62d150e889f8025d17295dbf2ebd07e404fbcded8` | 0 / 0 / 0 / 0 | identical (0 px differ) |
| 45 | big-bottom | [JPEG](./t45-big-bottom.jpg) | 1280×720 | 112,666 | `96c9b4df5cd6e5c3ef1dfb5d3f5e8f7ef88c731e9e4c764c1f89cd72c9af3536` | 0 / 0 / 0 / 0 | JPEG loss: mean 0.951, max 15 |
| 45 | two-lines | [PNG](./t45-two-lines.png) | 1280×720 | 296,614 | `287f74f2d2c93518e416d28689c8cae1d617bfbe24df1d569fb08de387bf7849` | 0 / 0 / 0 / 0 | identical (0 px differ) |
| 45 | two-lines | [JPEG](./t45-two-lines.jpg) | 1280×720 | 127,313 | `a53d0112b418395854453cc6f2f92694360290a028863267b63445a727e1cf6c` | 0 / 0 / 0 / 0 | JPEG loss: mean 2.523, max 121 |
| 107 | left-column | [PNG](./t107-left-column.png) | 1280×720 | 410,523 | `b1920f311262043472c16fe560091b58989869a7c110a19ab642551b3c724f1b` | 0 / 0 / 0 / 0 | identical (0 px differ) |
| 107 | left-column | [JPEG](./t107-left-column.jpg) | 1280×720 | 159,604 | `79f2cdc1cf44494d971ac80945ed414ec95408ecc2588645dc5abb282cbe826b` | 0 / 0 / 0 / 0 | JPEG loss: mean 1.026, max 19 |
| 107 | band | [PNG](./t107-band.png) | 1280×720 | 116,702 | `9700b008a019426b131a2d5dc743f9785ea7f817f9910bd2c9f58f0fb05cb415` | 0 / 0 / 0 / 0 | identical (0 px differ) |
| 107 | band | [JPEG](./t107-band.jpg) | 1280×720 | 122,310 | `d705fc9705186dc7b9487a65bcab0f79344b5aed1e6620a66689e258f5fc1b5e` | 0 / 0 / 0 / 0 | JPEG loss: mean 1.205, max 19 |
| 107 | big-bottom | [PNG](./t107-big-bottom.png) | 1280×720 | 400,526 | `c06e398f20a79792ca358b2fed9312f9b5160dd325c0262febf5d63a92b5f013` | 0 / 0 / 0 / 169 **expected exception** | identical (0 px differ) |
| 107 | big-bottom | [JPEG](./t107-big-bottom.jpg) | 1280×720 | 146,293 | `429d0acdf086c052e671269ece7cf570e7883b97ea1d55e660c5f2947af52fda` | 0 / 0 / 0 / 168 **expected exception** | JPEG loss: mean 1.047, max 19 |
| 107 | two-lines | [PNG](./t107-two-lines.png) | 1280×720 | 460,253 | `552c0a6abc7e02dbb44fa95b3dab840753a7c1719c1aa7d7dc53954522e8c72f` | 0 / 0 / 0 / 0 | identical (0 px differ) |
| 107 | two-lines | [JPEG](./t107-two-lines.jpg) | 1280×720 | 210,477 | `07a25ac06d0c28331d63673a23f7a9ef061bb20813454c55ce0afd5055f61ab2` | 0 / 0 / 0 / 0 | JPEG loss: mean 3.87, max 111 |

| Edge | Ink after the push (x, y) | Drag back (thumbnail px) | Ink after | Moved by |
|---|---|---|---|---|
| left | x 15–450, y 298–389 | [40, 0] | x 55–490, y 298–389 | [40, 0] |
| right | x 831–1266, y 314–405 | [-40, 0] | x 791–1226, y 314–405 | [-40, 0] |
| bottom | x 423–858, y 590–681 | [0, -40] | x 423–858, y 550–641 | [0, -40] |
| top | x 422–857, y 25–116 | [0, 40] | x 422–857, y 65–156 | [0, 40] |

| Real-mouse scenario | Result |
|---|---|
| A. big-bottom 45: 120 px right, release, 120 px back, each stroke with a 4 screen px up/down wobble (> DRAG_START_PX) | middle ink y 193–681 (re-wrapped and lifted from 319–681); end canvas hash equals start (`7aa9d2ed0fa02877`) |
| B. big-bottom 45: one stroke 300 px right and straight back, 4 px wobble | end canvas hash equals start (`7aa9d2ed0fa02877`) |
| C. big-bottom 45: 200 px right ending 2 screen px higher (< DRAG_START_PX), release, back ending 2 px higher | end canvas hash equals start |
| D. left-column 107 (held at the left edge): 100 screen px into the edge, 30 px up/down with 2 px sideways jitter, back | ink x 14–906 at every one of 6 steps: True; end hash equals start |
| D2. then pulled 40 screen px away from the edge | ink x 14–906 → 65–1184 (leaves the edge, re-wrapped wider to its new room) |
| E. big-bottom 45: 200 px right (stretched), 20 px up, back down, back across, one stroke | up moved 20 px (pointer 20); back down equals stretched: True; back across equals start: True |
| F. 'NGHIÊNG AB CD' held at the left edge where its wrap depends on its room (x 0.1306): 4 strokes of 40 screen px down/up each ending 2 px toward the edge, then 5 × ArrowLeft | ink x 15–450, height 201 px before; after each stroke [202, 201, 202, 201], after the keys 201 (same x every time: True); on the previous head 8e53eda the same run re-wraps to one more line (height 312) from the second stroke |

Browser: Chromium 153.0.8010.12 (Playwright, headless), canvas stage 311 screen px wide, source `ebeba936c0a2f93196addc94dfe1c2088988724f`.

Independent decode (ffprobe codec and size, Pillow edge counts PNG / JPEG):

| Case | ffprobe PNG | ffprobe JPEG | Edge px PNG L/R/T/B | Edge px JPEG L/R/T/B | JPEG vs PNG mean abs |
|---|---|---|---|---|---|
| t45-left-column | png 1280x720 | mjpeg 1280x720 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0.51 |
| t45-band | png 1280x720 | mjpeg 1280x720 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0.37 |
| t45-big-bottom | png 1280x720 | mjpeg 1280x720 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0.53 |
| t45-two-lines | png 1280x720 | mjpeg 1280x720 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 1.22 |
| t107-left-column | png 1280x720 | mjpeg 1280x720 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0.62 |
| t107-band | png 1280x720 | mjpeg 1280x720 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0.61 |
| t107-big-bottom | png 1280x720 | mjpeg 1280x720 | 0 / 0 / 0 / 169 | 0 / 0 / 0 / 168 | 0.61 |
| t107-two-lines | png 1280x720 | mjpeg 1280x720 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 1.88 |

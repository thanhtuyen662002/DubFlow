# ADR-0046: Editorial compilations of finished dubbing jobs

Status: Proposed. Issue #378.

## Decision

The optional “Tổng hợp” page takes 2–30 completed `final_vi.mp4` exports in the
user's order. The host applies `review_dir`, voice-catalog and bounded form
validation, then writes a version 1 compilation spec in a new
`DubFlow Output/Tong hop - <title>/run-<id>/.dubflow-work` directory. The installed
app-owned Python runs `engine.dubflow.compilation`; source exports stay read-only.

Intro, per-part lead and outro cards reuse product's VieNeu narration, sentence
splitting and caption timing. Their background is a blurred still. Optional user
music loops under these cards only. Clip segments keep the finished export's audio
and optional integer-millisecond trim. Both layouts preserve foreground aspect and
frame order. The information layout has a left panel for vertical clips and title
and score overlays for landscape clips. The full-screen layout uses a blurred fill
behind the fitted foreground, with a small title.
The renderer uses boxblur when available and the existing Gaussian blur approach
when the bundled LGPL FFmpeg omits that filter.

All segments use 1920×1080, 30 fps, yuv420p, H.264 Media Foundation at 8 Mb/s and
48 kHz stereo AAC at 192 kb/s. Encoding is injectable for deterministic FFmpeg
tests. Segment receipts bind content hashes, recipe code, voice profile, encoder
and output hashes. Verified completed segments survive cancellation and retries.
FFmpeg is cooperatively killed through the product runner. One failed part fails
the compilation, naming that part; it cannot publish a compilation missing clips.

After checking summed duration, geometry and frame rate, the engine atomically
publishes `compilation.mp4`. `chapters.txt` uses accumulated rendered segment
durations; `description.txt` adds credits from each source's `source_credit.json`
using the existing credit-line format. A host crash watcher reaps the child and
turns abandoned RUNNING status into FAILED or CANCELLED.

AI writing is explicit and uses the saved provider through `askJson`. Only the
topic, part titles and up to 60 Vietnamese subtitle lines per part are sent; the
page identifies the recipient. Stable ids bind normalized replies to editable
fields without reordering the selected clips. The prompt forbids invented facts
and requests an empty score when the excerpts provide insufficient evidence.

## Compatibility and migration

This adds private versioned spec/status/checkpoint artifacts. Existing canonical
timelines, worker protocols, SQLite state, standard exports and source credits
need no migration. Old applications ignore compilation runs. Incompatible recipes
must invalidate receipts; the code/profile/input hashes already invalidate changed
producers. Media, model weights and credentials are not stored in Git.

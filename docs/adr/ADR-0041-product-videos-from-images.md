# ADR-0041: Product videos from product images

Status: Proposed, Issue #347.

## Context
Sellers have product photos, and often the partner's own clips. The product facts (name, price, features) are printed on the photos, so typing them into a form is slow. They want short vertical sales videos with a Vietnamese voice and subtitles, in several script variants at once.

DubFlow already has:
- the VieNeu Vietnamese voices with an optional GPU (ADR-0039);
- the bundled FFmpeg with libass;
- the session-only Claude key used by posting suggestions.

## Decision
1. **Claude reads and writes, the user decides.**
   - The desktop sends downscaled JPEG copies of the chosen photos to Claude (`claude-opus-5-5`, structured output). Claude returns the product name, price, features and audience into an editable form.
   - From the form, Claude writes 1–5 script variants. The prompt forbids inventing facts, prices, discounts or health claims, and asks for prices and numbers written as spoken words.
   - The user edits the scripts before rendering. The key stays in memory for the session, and nothing is sent without the user pressing a button.
2. **The timeline follows the voice.** `engine.dubflow.product` splits each script into sentences (at most 180 characters each).
   - It speaks each sentence once with VieNeu at natural pace (`natural_speech`, no duration fitting) and places the sentences one after another with short gaps.
   - Captions of at most 48 characters share each sentence's time in proportion to their length.
   - The video lasts as long as the narration.
3. **One "Bản tin" template** at 1080×1920, 30 fps:
   - the user's photos or clips fill the top 1080×1200;
   - a green panel below carries the product name, the captions and the price;
   - photos get a slow pan; clips are cut into segments of at most 5 seconds, in the user's order, repeated as needed;
   - optional background music the user supplies is looped under the voice at a set volume.
   - an optional background image replaces the green: one at least 1.2 times taller than wide (1080×1920) covers the whole frame under the media, any other (1080×720) covers the panel. It is scaled to cover and cropped, and the name and captions then get a dark outline (Issue #358).
4. **Batch.**
   - Each variant becomes `video-<n>.mp4` with its `script-<n>.txt` and `captions-<n>.srt` in `DubFlow Output\Ban hang - <product>\run-<id>`.
   - A status file reports progress to the desktop, and one variant's failure does not stop the others.
   - Sentences repeated across variants are synthesized once.
5. **Only the user's own material.** The feature takes local files the user picks. It never downloads, transforms or re-posts other people's videos.

## Consequences
- Script quality depends on Claude and on what is legible in the photos. The editable form and scripts are the safeguard.
- CPU narration takes about real time; the GPU path is used when available, with the same CPU fallback as dubbing.
- More templates, voice speed and per-sentence visuals are possible later. They are not part of this change.

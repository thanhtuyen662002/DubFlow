# Vietnamese Subtitle Contract

Issue #42 owns deterministic timed subtitle composition from translated segments.
It emits standards-compliant SRT and ASS text with canonical integer-tick
conversion, bounded reading speed, safe-area placement for portrait,
landscape and square video, and explicit warnings when a fallback font is used.

The compositor does not infer OCR roles or remove source burned-in text. It
never changes source timing identity, emits negative or overlapping cues, or
requires a subtitle to be at the bottom of the source frame. SRT remains a
usable editable fallback when ASS styling or a render font is unavailable.

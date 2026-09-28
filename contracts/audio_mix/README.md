# Local audio-mix contract boundary

Issue #43 owns the versioned AUD-0 baseline mix boundary. The baseline keeps
the source layout, derives a time-aligned duck envelope from validated TTS
segments, preserves original audio, and publishes editable final-mix and
dialogue-stem artifacts. Missing or failed TTS is represented as data so a
single segment cannot erase the source mix.

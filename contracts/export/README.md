# Standard Export Contract

Issue #44 owns the stable export boundary between canonical media and optional
localization assets. An export request explicitly identifies the source video,
optional subtitle and optional dubbed-audio assets, output profile and
provenance. The adapter preserves source timing/aspect/rotation metadata,
validates the completed output before publishing it, and writes partial work to
a temporary name so a failed render cannot masquerade as a final artifact.

The editable pack is usable without CapCut, TTS, audio mixing, OCR cleanup or a
GPU. Subtitle-only mode preserves original audio; optional assets are omitted
from the manifest when unavailable rather than replaced with empty placeholders.
Hardware encoders are adapters with a software fallback, and all paths are
content-hash addressed so moving an output folder does not change identity.

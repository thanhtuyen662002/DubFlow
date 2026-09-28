# Local Translation Contract

Issue #40 owns the versioned boundary between canonical ASR utterances and
Vietnamese translation candidates. The adapter preserves every source
`utterance_id`, canonical start/end tick and source language while attaching a
translated text, normalized text, glossary/config/model provenance and
structured fallback or backend failure evidence.

The contract is backend-neutral and offline-capable. A local model or the
small deterministic fixture backend implements the same interface; a cloud
credential is never required for the standard path. Context windows are
explicitly bounded and checkpointable so a long transcript can resume without
retranslating completed utterances.

Translation is semantic text only. It does not infer speaker identity, OCR
roles or visual characters, and it never changes canonical timeline identity.

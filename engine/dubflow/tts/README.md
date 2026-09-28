# TTS adapter implementation boundary

`adapter.py` is the backend-neutral implementation for Issue #41. It consumes
translated segment-shaped inputs through the public request type, validates
engine capabilities and WAV output, and publishes typed segment artifacts plus
provenance. It maps canonical ticks to sample boundaries with integer floor /
ceil rules, writes artifacts atomically, and records checkpoint invalidation
inputs. Real local runtimes can implement `TtsEngine` without changing the
contract; the stock fixture is CPU-only and has no network or credential path.

# Audio cleanup benchmark v1

The benchmark records speech attenuation, residual speech, background artifact
score, latency, memory and long-form stability for speech+music+SFX fixtures.
The policy promotes a candidate only after conservative thresholds pass. Any
failure or regression selects the AUD-0 conservative fallback, preserving the
standard usable export.

# Baseline audio mixer

The AUD-0 adapter will provide deterministic PCM fixture mixing behind a
backend-neutral interface. It will preserve supported source channels, use
integer timeline/sample mapping for duck envelopes, validate loudness and
clipping, and publish atomic final-mix and dialogue-stem artifacts without
destructively removing dialogue from the original track.

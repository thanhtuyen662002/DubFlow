# Speaker diarization contract v1

The contract keeps each detected speaker interval as a separate segment. Two
segments may share time and an `overlap_group_id`; consumers must never flatten
them into one speaker. Narrator, offscreen and unresolved observations carry an
empty cluster list so uncertainty remains explicit.

Cluster IDs are job-scoped and derived from ordered observations plus model
provenance. They are suitable for stable TTS casting within a job, not as a
cross-video identity. The CPU baseline is deterministic and can be replaced by
an adapter-backed model without changing the durable shape.

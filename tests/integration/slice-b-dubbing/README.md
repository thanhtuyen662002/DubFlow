# Slice B baseline dubbing integration

These tests exercise the #45 extension of the proven B1 Vietsub pipeline:
translation output flows through the validated local TTS adapter and AUD-0
source ducking before export. Portrait and landscape fixtures stay offline and
CPU-capable. TTS or mix failures retain the #56 Vietsub output and publish
capability downgrade evidence instead of losing the job.

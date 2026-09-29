# Character and active-speaker contract v1

Visual tracks are separate from audio speaker clusters. An actor may have
multiple visual character tracks, and a track may return across scenes. The
association result can be `visible`, `offscreen` or `unresolved`; weak evidence
keeps the audio cluster as the fallback rather than forcing a character.

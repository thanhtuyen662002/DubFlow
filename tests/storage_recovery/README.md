# Storage topology recovery fixtures

The deterministic tests exercise fake volume probes and temporary directories
for output/cache removal, low-space separation, moved-folder rebinding,
network-path policy, long/non-ASCII relative paths, partial-file quarantine,
and sleep/resume generation invalidation. They never require a real NAS,
removable drive, Windows power event or large media file.

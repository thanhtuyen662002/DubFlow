# Project ownership lock v1

One supervisor owns the lock covering the project database, artifact namespace,
and temp namespace. The lock metadata records a supervisor role, OS PID,
process-start token, scope, and acquisition time. A second instance can open
read-only or request handoff; it cannot become a second writer.

Stale metadata is reclaimable only after a process probe confirms that the same
PID/start-token owner is no longer live. Reclamation first renames the old
metadata to a unique `.stale-*` record, then uses atomic create-new for the new
owner. Corrupt metadata is never guessed or silently overwritten. Workers have
no API role capable of acquiring this lock.

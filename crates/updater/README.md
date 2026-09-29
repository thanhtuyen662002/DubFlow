# DubFlow updater controller

This crate owns the stage → verify → checkpoint → atomic switch → healthcheck
→ commit sequence. Package verification, health checks and database migration
hooks are injected so fault-injection tests stay deterministic. Versioned
directories are never deleted by the updater; pinned/last-known-good versions
therefore remain launchable after a failed update or power-loss recovery.

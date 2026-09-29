# Bootstrap manifest boundary

Bootstrap artifacts are pinned by ID, version, byte size, SHA-256 and detached
signature metadata. The runtime controller writes only into an app-owned
install directory, resumes `.partial` files, verifies before publication and
records an atomic state file. Hardware detection is advisory; CPU remains the
portable fallback when accelerator-specific artifacts fail.

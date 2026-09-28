# DubFlow security boundary

This dependency-free Rust crate is the policy boundary for untrusted media,
archive members, filenames, subprocess arguments, and diagnostic text. It
returns validated plans rather than performing extraction or spawning a shell.
Callers must write only the destinations returned by `plan_archive_extract`,
pass `CommandInvocation::command()` directly to the process API, and redact
diagnostics before persistence or upload.

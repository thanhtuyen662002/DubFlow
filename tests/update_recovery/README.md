# Update recovery tests

Fault-injection tests live with `crates/updater` because the state machine is
Rust-owned. They cover verification failure, writer-lock contention, atomic
switch health rollback, migration hooks, unsafe paths and interrupted recovery.

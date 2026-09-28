# Worker protocol fixtures and tests

`fixtures/valid.jsonl` is the cross-language v1 stream used by the Rust crate
and the dependency-free Python reference. It covers a command, liveness,
bounded progress, a reusable checkpoint, cooperative cancellation and a
terminal shutdown. The tests intentionally exercise malformed lines,
duplicate/unknown fields, future versions, sequence gaps, heartbeat expiry,
bounded progress coalescing and cancellation safety without a model, GPU or
live service.

# Local-file slice integration boundary

Issue #14 owns this narrow harness namespace. The executable wiring consumes
the merged timeline, worker-protocol, and durable-state crates through their
public interfaces; it does not fork or redefine any contract.

Implementation and deterministic fixtures are added in the next checkpoint.

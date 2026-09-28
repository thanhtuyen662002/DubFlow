# Job status presentation

`status.ts` is a thin presentation adapter for the versioned supervisor status
contract. It does not compute liveness, use a timer, or treat a percentage as
proof of progress; the Rust supervisor remains the authority.

# ADR-0009: Fair resource scheduling and rolling disk guards

## Status

Accepted — 2026-09-29

## Decision

DubFlow schedules fake/real consumers through bounded resource classes (CPU,
GPU, VRAM, render, download, and disk). Queue selection uses explicit priority
plus capped aging, while work is dispatched in checkpoint-safe quanta. A
consumer must persist its checkpoint before yielding/completing a quantum;
resource usage is then released for the next eligible job. A large job cannot
hold every slot across the queue indefinitely, and a poisoned job releases its
usage without failing unrelated jobs.

Pause/resume requires the same checkpoint identifier. Rolling disk decisions
use checked subtraction and `u128` ratio arithmetic, and distinguish continue,
pause-before-write, and unknown/invalid observations. Debug and cache retention
budgets produce explicit eviction/rejection decisions instead of silently
filling the disk.

## Recovery and compatibility

The scheduler is pure policy and writes no SQLite state. It can be tested with
synthetic 100/500-job queues and fake resource consumers; GPU/model
availability is not a merge prerequisite. Future durable queue fields belong
to the state lane and require an additive migration.

# ADR0032: Bounded Review failure details v2

Status: proposed in an independent branch based on aff9d10; not adopted into the running Windows candidate.

## Context

The native Review adapter keeps at most 4096 raw stderr bytes. Its lossless diagnostic uses ASCII escaping. A byte such as 0xff expands to four printable bytes, so a complete 4096-byte capture can produce more than 16 KiB plus the status/header. The current desktop failure v1 diagnostic cap is 4096 UTF8 bytes. A valid, bounded native refusal can therefore become a generic error before the user can inspect its actual reason. The existing native formatter regression deliberately preserves all captured bytes.

The frontend also receives a closed native stage observation but currently omits that stage from its failure details. A failed operation must keep its durable outcome unconfirmed; a readable diagnostic cannot establish whether a commit happened.

## Decision

Introduce `desktop-request-failure-v2.json` with the same exact fields and invocation binding as v1, schema_version 2 and a 32768-byte UTF8 diagnostic limit. The message limit remains 1024 bytes. All outcomes remain `unconfirmed`. The last native stage is shown with a fixed Vietnamese label in the existing technical details text. Unavailable stage stays unavailable.

The Tauri observed Review request capture needs a dedicated bounded limit for this packet. Generic host command capture retains 4096 bytes. Native adapter raw capture retains 4096 bytes. Stream/line caps for native stage observations, stdout caps, deadlines, cancellation, child ownership and refusal on overflow remain enforced. No truncation or ellipsis may be presented as a complete native diagnostic. This branch's frontend/contract change alone does not prove the Tauri producer path.

No schema/database/worker/timeline mutation or data migration is needed. The version is an ephemeral error envelope only. Diagnostics remain inert textContent; they grant no path access, command execution, automatic retry, commit or rollback authority. A failed/lost/stale invocation still requires the existing fresh state read.

## Compatibility

| Producer | Consumer | Behavior |
| --- | --- | --- |
| Failure v1, diagnostic <=4096 B | Old or new frontend | Accept bound details under the original cap. |
| Failure v1, diagnostic >4096 B | Old or new frontend | Reject;v1 is not widened. |
| Failure v2, diagnostic <=32768 B | New frontend | Accept only after all existing closed-field, UTF8/control, request/job/operation and outcome checks. |
| Failure v2 | Old frontend | Reject as unsupported; retain generic unconfirmed/reload behavior. |
| Any unknown version, foreign invocation, overflow or extra authority field | New frontend | Reject typed details. |

Release the frontend and Tauri producer together in one source-matched whole producer. Never overlay this host/frontend onto an old installed producer or repin an existing job. No old job or successful output is modified by reading a failure.

## Verification and remaining gates

Focused frontend regressions cover the original v1 cap, maximum escaped raw adapter diagnostic retention, v2 UTF8 byte boundaries, unchanged message cap, fixed native stage display, stale binding, cloned packets, inert markup and rejection of extra fields/control characters/unknown versions. They must be invoked from the existing Review test entrypoint when this branch is integrated.

Tauri source review and focused capture tests must prove the dedicated observed-request cap reaches the error envelope and that generic captures stay unchanged. Real Windows raw capture, exact-head/current-base required CI, installed GUI rendering and actual failed-cue recovery remain separate gates. No frontend/component test is product acceptance, audio quality or AV sync evidence.

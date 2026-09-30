# Model and translation package distribution

DubFlow does not redistribute model weights in the Windows ZIP or setup
executable. The release contains only the pinned profile metadata; the first
run downloads the exact bytes into the current user's model directory over
HTTPS and publishes them only after the manifest size and SHA-256 match.

The current CPU profile references these upstream artifacts:

- Faster-Whisper `small` files from
  `https://huggingface.co/guillaumekln/faster-whisper-small`.
- Argos Translate English→Vietnamese package from
  `https://argos-net.com/v1/translate-en_vi-1_9.argosmodel`.

The profile is therefore an upstream-download integration rather than a
redistribution of those weights. Users and downstream distributors must review
the license and terms presented by each upstream source before enabling the
profile in a redistributed product. A missing, unverified or interrupted
artifact fails the job with a typed diagnostic; it is never replaced by an
untracked cache or fixture bytes.

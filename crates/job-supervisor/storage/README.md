# Supervisor storage topology

This crate owns the policy boundary between durable control state and
user-selected media/model/cache roots. It does not open SQLite and it does not
let workers write durable state. The existing `dubflow-job-state` crate remains
the SQLite owner.

The topology requires six independently configured roots and accepts an
injected `VolumeProbe`, keeping OS-specific volume serial, free-space and
sleep/resume adapters outside the deterministic policy. A filesystem probe is
provided for ordinary local checks; a production Windows/NAS adapter supplies
stable volume IDs and free-space snapshots.

The API deliberately uses integer byte counts and parts-per-million ratios.
No floating-point threshold or absolute drive letter is used for a durable
decision. See `contracts/storage/schema-v1.json` and ADR-0005 for the wire and
migration boundary.

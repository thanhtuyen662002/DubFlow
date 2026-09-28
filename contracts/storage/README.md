# Resilient storage topology contract v1

The storage contract separates six independently health-checked roots:

| Root | Durable responsibility | Loss policy |
| --- | --- | --- |
| `control` | Supervisor SQLite, migrations, job metadata and checkpoints | Must be app-owned local storage; loss is a hard recovery condition. |
| `source` | Input media and source-side metadata | Pause affected jobs; unrelated local jobs continue. |
| `output` | Published media and editable assets | Quarantine partial files and pause only jobs using the root. |
| `model` | Pinned model/runtime packs | Revalidate or use an approved CPU/alternate profile; never substitute silently. |
| `cache` | Rebuildable intermediate data | Discard/rebuild cache; durable job metadata remains usable. |
| `temp` | Atomic-write staging files | Quarantine unregistered partials; never treat them as final artifacts. |

The control root is never automatically moved to an output, removable, NAS or
cache root. Network/SMB paths are health checked separately and are not assumed
to provide local SQLite or atomic-rename semantics.

Artifact identity is `volume_id + root_kind + relative_path + content_hash +
size_bytes`; an absolute drive letter is only a transient resolution detail.
Moved-folder recovery must verify the supplied volume identity and recompute
the SHA-256 content hash before rebinding a committed artifact. A stale path
alone never makes an artifact valid.

On Windows sleep/hibernate/resume, the supervisor increments a resume
generation and re-probes roots/resources before resuming workers. A lease from
an older generation is invalid and must be reacquired.

# Retention policy

Non-terminal jobs and active workers protect their exact manifest versions.
Cleanup is bounded and resumable, evicting unreferenced caches before models or
runtimes. The reference policy is implemented in the supervisor model-refs
crate and consumes manifest hashes from Issue #9.

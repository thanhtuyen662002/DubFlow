# Versioned direct-draft boundary

Direct CapCut drafts are an optional adapter. DubFlow attempts them only for
an explicitly tested semantic version and writes only under a caller-provided
controlled directory. The canonical MP4 and portable import pack are produced
independently. Unknown versions, schema changes, validation failures and
CapCut update races return a structured fallback result instead of failing the
job.

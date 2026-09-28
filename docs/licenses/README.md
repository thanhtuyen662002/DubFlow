# Distribution decisions

Every runtime/model candidate has a license and distribution decision in
`models/manifests/catalog-v1.json`. The installer must not infer permission
from a model URL or repository name. `redistributable`, attribution, source,
and any notice/source-offer obligation are explicit manifest fields.

Code-bearing packages are signed and audited under the same update and rollback
policy as application code. Weights-only packages still require a license
review and checksum; they do not gain permission merely because they cannot
execute code.

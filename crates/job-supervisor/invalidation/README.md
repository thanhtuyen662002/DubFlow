# Selective invalidation facade

The supervisor consumes the artifact-store provenance graph through a versioned
facade. It owns durable mutation; the graph only computes affected descendants
and reuse decisions.

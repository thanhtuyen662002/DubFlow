# Artifact namespace ownership

Artifact storage reuses the supervisor project lock primitive so the database,
artifact, and temp namespaces have one writer. It does not create an
independent worker lock or a second ownership protocol.

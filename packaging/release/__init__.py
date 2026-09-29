"""Reproducible, fail-closed Windows release bundle tooling."""

from .manifest import (
    ManifestError,
    ReleaseArtifact,
    ReleaseManifest,
    hash_file,
)

__all__ = [
    "ManifestError",
    "ReleaseArtifact",
    "ReleaseManifest",
    "hash_file",
]

"""Untrusted-input boundary used by the Python adapters.

The module is intentionally dependency-free.  It validates before any media,
archive, subprocess, or diagnostic operation and returns structured plans that
callers can enforce at their I/O boundary.
"""

from .boundary import (
    ArchiveLimits,
    ArchiveMember,
    SecurityBoundaryError,
    SubprocessPlan,
    allocate_filename,
    build_subprocess_plan,
    plan_archive_extract,
    redact_diagnostics,
    sanitize_filename,
    validate_relative_path,
    verify_sha256,
)

__all__ = [
    "ArchiveLimits",
    "ArchiveMember",
    "SecurityBoundaryError",
    "SubprocessPlan",
    "allocate_filename",
    "build_subprocess_plan",
    "plan_archive_extract",
    "redact_diagnostics",
    "sanitize_filename",
    "validate_relative_path",
    "verify_sha256",
]

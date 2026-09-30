"""DubFlow release signing trust anchor.

The corresponding 32-byte Ed25519 private seed is stored only as the
``DUBFLOW_RELEASE_PRIVATE_KEY`` GitHub Actions secret. It is never committed
to the repository or copied into an installed bundle.
"""

RELEASE_KEY_ID = "dubflow-release-v1"
RELEASE_PUBLIC_KEY_B64 = "6JvouGaBOyu9H0prKtmH72DjmcWrxqE05a/EV1hkfZg="

__all__ = ["RELEASE_KEY_ID", "RELEASE_PUBLIC_KEY_B64"]

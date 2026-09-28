from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "0001_job_state.sql"
CRATE_MANIFEST = ROOT / "crates" / "job-supervisor" / "state" / "Cargo.toml"


class DurableStateContractTests(unittest.TestCase):
    def test_migration_is_versioned_and_contains_recovery_surfaces(self) -> None:
        sql = MIGRATION.read_text(encoding="utf-8")
        for table in ("jobs", "stages", "artifacts", "checkpoints", "schema_migrations"):
            self.assertIn(table, sql)
        for marker in ("WAL", "reusable", "retry_condition", "expected_hash", "quarantined", "missing"):
            self.assertIn(marker.lower(), sql.lower())
        self.assertIn("last_failure_retryable", sql)

    def test_state_crate_pins_sqlite_and_hash_dependencies(self) -> None:
        manifest = CRATE_MANIFEST.read_text(encoding="utf-8")
        self.assertIn('rusqlite = { version = "=0.31.0", features = ["bundled"] }', manifest)
        self.assertIn('sha2 = "=0.10.8"', manifest)

    def test_worker_namespace_has_no_direct_sqlite_dependency(self) -> None:
        worker_root = ROOT / "engine" / "dubflow" / "worker"
        source = "\n".join(path.read_text(encoding="utf-8") for path in worker_root.rglob("*.py"))
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("sqlite", source.lower())


if __name__ == "__main__":
    unittest.main()

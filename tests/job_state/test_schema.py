from __future__ import annotations

import ast
from pathlib import Path
import sqlite3
import unittest


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "migrations" / "0001_job_state.sql"
CRATE_MANIFEST = ROOT / "crates" / "job-supervisor" / "state" / "Cargo.toml"


class DurableStateContractTests(unittest.TestCase):
    def test_additive_start_request_migration_preserves_jobs_and_forbids_rebind(self) -> None:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.executescript(MIGRATION.read_text(encoding="utf-8"))
            connection.execute("INSERT INTO jobs(job_id,source_uri,status,created_at_ms,updated_at_ms) VALUES ('historic','file:///old.mp4','paused',1,1)")
            connection.executescript((ROOT / "migrations/0002_job_start_requests.sql").read_text(encoding="utf-8"))
            self.assertEqual(connection.execute("SELECT source_uri,status FROM jobs WHERE job_id='historic'").fetchone(), ("file:///old.mp4", "paused"))
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM job_start_requests").fetchone()[0], 0)
            connection.execute("INSERT INTO job_start_requests VALUES ('historic','{\"voice\":\"original\"}',2)")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO job_start_requests VALUES ('historic','{\"voice\":\"different\"}',3)")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("INSERT INTO job_start_requests VALUES ('absent','{}',3)")
            self.assertEqual(connection.execute("SELECT request_json FROM job_start_requests").fetchone()[0], '{"voice":"original"}')

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
        for path in worker_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
                    name = node.func.id if isinstance(node.func, ast.Name) else (node.func.attr if isinstance(node.func, ast.Attribute) else "")
                    if name in {"__import__", "import_module"} and isinstance(node.args[0].value, str):
                        modules = [node.args[0].value]
                self.assertFalse(any(module.split(".")[0] in {"sqlite3", "apsw"} for module in modules), f"worker imports a SQLite API: {path}:{getattr(node, 'lineno', 0)}")


if __name__ == "__main__":
    unittest.main()

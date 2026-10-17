"""Actual additive SQL constraints; native Rust CAS has its own crate tests."""
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parents[2]
REVIEW_SQL = (ROOT / "migrations/0003_review_graph.sql").read_text(encoding="utf-8")
PIN = "sha256:" + "a" * 64


def digest(value): return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


class NativeGraphMigrationTests(unittest.TestCase):
    def fixture(self):
        db = sqlite3.connect(":memory:"); self.addCleanup(db.close)
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript((ROOT/"migrations/0001_job_state.sql").read_text(encoding="utf-8") +
                        (ROOT/"migrations/0002_job_start_requests.sql").read_text(encoding="utf-8"))
        db.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at_ms INTEGER)")
        db.executemany("INSERT INTO schema_migrations VALUES (?,0)", [(1,), (2,)])
        for job in ("job-r", "other-job"):
            db.execute("INSERT INTO jobs VALUES (?, 'fixture', 'succeeded', 0, 0, NULL)", (job,))
            db.execute("INSERT INTO stages(job_id,stage_id,kind,status) VALUES (?, 'export', 'fixture', 'succeeded')", (job,))
            db.execute("INSERT INTO job_start_requests VALUES (?, '{\"schema_version\":1}', 0)", (job,))
        db.execute("INSERT INTO artifacts(artifact_id,job_id,stage_id,path,content_hash,size_bytes,state,reusable,created_at_ms) VALUES ('manifest','job-r','export','fixture',?,10,'committed',1,0)", (PIN,))
        db.commit(); return db

    def enable(self, db):
        db.executescript("BEGIN IMMEDIATE;\n" + REVIEW_SQL +
                        "\nINSERT INTO review_schema_migrations VALUES (1); COMMIT;")

    def revision(self, db, revision, *, job="job-r", wrong_bytes=False):
        snapshot = json.dumps({"revision": revision, "text": "Trúc Ly"}, ensure_ascii=False)
        graph = '{"graph":"fixture"}'
        count = len(snapshot) + len(graph) if wrong_bytes else len(snapshot.encode()) + len(graph.encode())
        db.execute("INSERT INTO review_graph_revisions VALUES (?,?,?,?,?,?,NULL,0,?,0)",
                   (job,revision,snapshot,digest(snapshot),graph,digest(graph),count))

    def test_additive_schema_preserves_core_rows_and_binds_original_job(self):
        db = self.fixture(); before = db.execute("SELECT * FROM artifacts").fetchall()
        self.enable(db); self.revision(db,"0"); self.revision(db,"0",job="other-job")
        db.execute("INSERT INTO review_graph_heads VALUES ('job-r','0','manifest',?)", (PIN,))
        with self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO review_graph_heads VALUES ('other-job','0','manifest',?)", (PIN,))
        self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone(), (2,))
        self.assertEqual(db.execute("SELECT * FROM artifacts").fetchall(), before)
        self.assertEqual(db.execute("SELECT version FROM review_schema_migrations").fetchone(), (1,))

    def test_exact_u64_text_revisions_and_utf8_byte_accounting(self):
        db = self.fixture(); self.enable(db)
        for revision in ("0", "9007199254740993", "18446744073709551615"):
            self.revision(db,revision)
        self.assertEqual(db.execute("SELECT revision,typeof(revision) FROM review_graph_revisions WHERE revision='9007199254740993'").fetchone(), ("9007199254740993", "text"))
        for bad in ("01", "-1", "1.0", "18446744073709551616", "a", ""):
            with self.assertRaises(sqlite3.IntegrityError): self.revision(db,bad)
        with self.assertRaises(sqlite3.IntegrityError): self.revision(db,"1",wrong_bytes=True)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM review_graph_revisions").fetchone(), (3,))

    def test_interrupted_ddl_rolls_back_feature_and_preserves_core(self):
        db = self.fixture(); before = db.execute("SELECT * FROM jobs").fetchall()
        with self.assertRaises(sqlite3.OperationalError):
            try:
                db.executescript("BEGIN IMMEDIATE;\n" + REVIEW_SQL +
                                "\nINSERT INTO review_schema_migrations VALUES (1); SELECT * FROM injected_missing_table; COMMIT;")
            finally: db.rollback()
        self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'review_%'").fetchall(), [])
        self.assertEqual(db.execute("SELECT * FROM jobs").fetchall(), before)
        self.assertEqual(db.execute("SELECT MAX(version) FROM schema_migrations").fetchone(), (2,))
        self.enable(db)


if __name__ == "__main__": unittest.main()

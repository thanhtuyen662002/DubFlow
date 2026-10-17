-- Optional Review feature schema1. Explicit supervisor admission only.
-- Core schema_migrations stays at2; older core readers ignore these tables.
-- The complete DDL and feature-version row are one immediate transaction.
CREATE TABLE review_schema_migrations (
    version INTEGER PRIMARY KEY NOT NULL CHECK (version > 0)
);

CREATE UNIQUE INDEX review_artifact_job_binding ON artifacts(job_id,artifact_id);

CREATE TABLE review_graph_revisions (
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    revision TEXT NOT NULL CHECK (
        revision = '0' OR (substr(revision,1,1) GLOB '[1-9]'
        AND revision NOT GLOB '*[^0-9]*'
        AND (length(revision) < 20 OR (length(revision) = 20 AND revision <= '18446744073709551615')))
    ),
    snapshot_json TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL CHECK (length(snapshot_sha256)=71 AND substr(snapshot_sha256,1,7)='sha256:' AND substr(snapshot_sha256,8) NOT GLOB '*[^0-9a-f]*'),
    graph_json TEXT NOT NULL,
    graph_sha256 TEXT NOT NULL CHECK (length(graph_sha256)=71 AND substr(graph_sha256,1,7)='sha256:' AND substr(graph_sha256,8) NOT GLOB '*[^0-9a-f]*'),
    undo_target_revision TEXT,
    undo_depth INTEGER NOT NULL CHECK (undo_depth BETWEEN 0 AND 256),
    payload_bytes INTEGER NOT NULL CHECK (payload_bytes > 0 AND payload_bytes <= 67108864
        AND payload_bytes = length(CAST(snapshot_json AS BLOB)) + length(CAST(graph_json AS BLOB))),
    created_at_ms INTEGER NOT NULL CHECK (created_at_ms >= 0),
    PRIMARY KEY (job_id,revision),
    FOREIGN KEY (job_id,undo_target_revision) REFERENCES review_graph_revisions(job_id,revision),
    CHECK ((undo_depth=0 AND undo_target_revision IS NULL) OR (undo_depth>0 AND undo_target_revision IS NOT NULL))
);

CREATE TABLE review_graph_heads (
    job_id TEXT PRIMARY KEY NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    revision TEXT NOT NULL,
    original_artifact_id TEXT NOT NULL,
    original_manifest_sha256 TEXT NOT NULL CHECK (length(original_manifest_sha256)=71 AND substr(original_manifest_sha256,1,7)='sha256:' AND substr(original_manifest_sha256,8) NOT GLOB '*[^0-9a-f]*'),
    FOREIGN KEY (job_id,revision) REFERENCES review_graph_revisions(job_id,revision),
    FOREIGN KEY (job_id,original_artifact_id) REFERENCES artifacts(job_id,artifact_id)
);

-- ap-rag-agent SQLite schema (architecture §9). Applied idempotently at startup.
-- Connection pragmas (WAL, foreign_keys, busy_timeout) are set in db.py, not here.
-- LangGraph's SqliteSaver creates and owns its own checkpoint tables.
-- JSON columns hold canonical JSON (sorted keys); money is stored as TEXT decimals.

-- Current picture of each run: overwritten, used to resume and for Get run.
CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    case_id           TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('RUNNING','AWAITING_APPROVAL','COMPLETED','FAILED')),
    current_node      TEXT,
    request_redacted  TEXT NOT NULL,          -- JSON, allow-listed fields only
    config_snapshot   TEXT NOT NULL,          -- JSON: model id, prompt version, corpus hash
    budget            TEXT NOT NULL,          -- JSON: counters and limits
    result            TEXT,                   -- JSON FinalResult
    failure_reason    TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_status ON runs(status);
CREATE INDEX IF NOT EXISTS runs_case ON runs(case_id);

-- Append-only history, used to explain. Every run, every outcome.
CREATE TABLE IF NOT EXISTS audit_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    type         TEXT NOT NULL,
    node         TEXT,
    outcome      TEXT NOT NULL,
    duration_ms  INTEGER,
    payload      TEXT NOT NULL               -- JSON from a per-type allow-listed model
);
CREATE INDEX IF NOT EXISTS audit_events_run ON audit_events(run_id, event_id);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;

CREATE TABLE IF NOT EXISTS control_results (
    run_id        TEXT NOT NULL,
    control_id    TEXT NOT NULL,
    status        TEXT NOT NULL,
    outcome_floor TEXT NOT NULL,
    body          TEXT NOT NULL,             -- JSON ControlResult
    calculations  TEXT NOT NULL,             -- JSON list of the Calculations it references
    PRIMARY KEY (run_id, control_id)
);

-- Minimal: what the citation check (TB3) needs.
CREATE TABLE IF NOT EXISTS retrievals (
    retrieval_id  TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    query_label   TEXT NOT NULL,             -- C1..C6, C4-FX, C4-NOPO, CASE
    query_text    TEXT NOT NULL,
    filters       TEXT NOT NULL,             -- JSON
    chunk_ids     TEXT NOT NULL,             -- JSON list
    scores        TEXT NOT NULL,             -- JSON list, aligned with chunk_ids
    ts            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS retrievals_run ON retrievals(run_id);

-- One row per attempt.
CREATE TABLE IF NOT EXISTS tool_calls (
    tool_call_id       TEXT PRIMARY KEY,
    run_id             TEXT NOT NULL,
    node               TEXT NOT NULL,
    tool               TEXT NOT NULL,
    attempt            INTEGER NOT NULL,
    args_redacted      TEXT NOT NULL,        -- JSON
    response_redacted  TEXT,                 -- JSON
    outcome            TEXT NOT NULL,        -- ok | timeout | transient | error
    error              TEXT,
    duration_ms        INTEGER NOT NULL,
    ts                 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tool_calls_run ON tool_calls(run_id);

CREATE TABLE IF NOT EXISTS model_calls (
    model_call_id    TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    node             TEXT NOT NULL,
    attempt          INTEGER NOT NULL,
    prompt_sha256    TEXT NOT NULL,
    prompt_redacted  TEXT NOT NULL,
    completion_text  TEXT,
    parse_result     TEXT NOT NULL,          -- ok | invalid: <summary> | error
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    duration_ms      INTEGER NOT NULL,
    ts               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS model_calls_run ON model_calls(run_id);

-- Duplicate approval requests collapse onto one row (RA#4). The grant lives on the row.
CREATE TABLE IF NOT EXISTS approvals (
    approval_id           TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL,
    decision_fingerprint  TEXT NOT NULL,
    status                TEXT NOT NULL CHECK (status IN ('PENDING','APPROVED','REJECTED')),
    amount                TEXT NOT NULL,
    currency              TEXT NOT NULL,
    approver_id           TEXT,
    verdict               TEXT,
    grant_id              TEXT UNIQUE,
    grant_consumed_at     TEXT,
    stored_response       TEXT,               -- JSON, returned verbatim on replay
    created_at            TEXT NOT NULL,
    resolved_at           TEXT,
    UNIQUE (run_id, decision_fingerprint)
);

-- One effective decision per idempotency key (RA#4, RA#5).
CREATE TABLE IF NOT EXISTS decisions (
    decision_id      TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    case_id          TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL UNIQUE,
    decision         TEXT NOT NULL CHECK (decision IN ('POST_FOR_PAYMENT','HOLD','REJECTED')),
    payload_sha256   TEXT NOT NULL,           -- detects same key + different payload
    computed_total   TEXT NOT NULL,
    currency         TEXT NOT NULL,
    grant_id         TEXT,
    ledger_ref       TEXT NOT NULL,
    response         TEXT NOT NULL,           -- JSON, returned on replay
    created_at       TEXT NOT NULL
);

-- Simulated posting/hold/rejection API (sandbox, §8). Its own unique key.
CREATE TABLE IF NOT EXISTS sim_ledger (
    ledger_ref       TEXT PRIMARY KEY,
    idempotency_key  TEXT NOT NULL UNIQUE,
    case_id          TEXT NOT NULL,
    decision         TEXT NOT NULL,
    amount           TEXT NOT NULL,
    currency         TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

-- RAG index (written by `ap ingest`, P2).
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id         TEXT PRIMARY KEY,        -- {doc_id}§{n}@v{version}
    doc_id           TEXT NOT NULL,
    version          TEXT NOT NULL,
    section          INTEGER NOT NULL,
    heading          TEXT NOT NULL,
    text             TEXT NOT NULL,
    text_sha256      TEXT NOT NULL,
    effective_date   TEXT,
    superseded_date  TEXT,
    jurisdiction     TEXT,                    -- lowercased at ingestion
    status           TEXT,                    -- display only; never a filter
    classification   TEXT
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
-- Porter stemming so "invoices" matches "invoice" (our decision).
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED, text, tokenize = 'porter unicode61'
);

CREATE TABLE IF NOT EXISTS policy_registry (
    doc_id          TEXT PRIMARY KEY,
    pinned_version  TEXT,                     -- NULL: never eligible (O2)
    superseded_by   TEXT,
    doc_type        TEXT NOT NULL
);

-- Ingestion idempotency: corpus + registry content hash.
CREATE TABLE IF NOT EXISTS ingest_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

PRAGMA user_version = 1;

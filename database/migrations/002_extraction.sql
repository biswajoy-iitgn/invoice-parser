-- 002_extraction.sql: model registry, extraction runs, jobs, review, audit.
-- The invoices table carries NO model-specific columns; provenance lives here.

-- ---------- model registry ----------
CREATE TABLE IF NOT EXISTS models (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,  -- e.g. 'hunyuanocr', 'laya', 'omnisr', 'qwen3-vl'
    description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS model_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id UUID NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
    version TEXT NOT NULL,  -- e.g. 'Q8_0', 'invoice-ft-7313', 'x2-doc-epoch23'
    architecture TEXT,
    checkpoint_uri TEXT,
    training_dataset TEXT,
    dataset_version TEXT,
    configuration JSONB,
    hyperparameters JSONB,
    commit_hash TEXT,
    trained_at TIMESTAMPTZ,
    metrics JSONB,
    hardware TEXT,
    status TEXT NOT NULL DEFAULT 'EXPERIMENTAL'
        CHECK (status IN ('EXPERIMENTAL','VALIDATION','PRODUCTION','RETIRED')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_id, version)
);

CREATE TABLE IF NOT EXISTS model_configurations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,  -- e.g. 'invoice-extraction-v4'
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS model_configuration_models (
    model_configuration_id UUID NOT NULL REFERENCES model_configurations(id) ON DELETE CASCADE,
    model_version_id UUID NOT NULL REFERENCES model_versions(id) ON DELETE RESTRICT,
    role TEXT NOT NULL,  -- 'ocr' | 'reader' | 'semantic' | 'sr'
    PRIMARY KEY (model_configuration_id, role)
);

-- ---------- extraction runs (append-only history) ----------
CREATE TABLE IF NOT EXISTS extraction_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    document_version_id UUID REFERENCES document_versions(id) ON DELETE SET NULL,
    pipeline_version TEXT NOT NULL,
    model_configuration_id UUID REFERENCES model_configurations(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'RUNNING'
        CHECK (status IN ('QUEUED','RUNNING','COMPLETED','FAILED')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    processing_time_ms INT,
    input_hash TEXT,
    raw_output_json JSONB,
    final_output_json JSONB,
    error_code TEXT,
    error_message TEXT
);

ALTER TABLE invoices
    ADD CONSTRAINT invoices_current_run_fk
    FOREIGN KEY (current_extraction_run_id) REFERENCES extraction_runs(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS extraction_field_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_run_id UUID NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
    canonical_code TEXT NOT NULL,
    value TEXT,
    confidence NUMERIC,
    source_text TEXT,
    source_bbox NUMERIC[4],
    source_page INT,
    evidence_score NUMERIC,
    semantic_score NUMERIC,
    validation_score NUMERIC,
    route TEXT CHECK (route IS NULL OR route IN ('AUTO_ACCEPT','REVIEW','REJECT'))
);

-- ---------- async processing jobs ----------
CREATE TABLE IF NOT EXISTS processing_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    idempotency_key TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL DEFAULT 'QUEUED'
        CHECK (state IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')),
    attempt INT NOT NULL DEFAULT 0,
    worker TEXT,
    pipeline_version TEXT,
    error TEXT,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- review workflow (original prediction never destroyed) ----------
CREATE TABLE IF NOT EXISTS review_tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    extraction_run_id UUID REFERENCES extraction_runs(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','IN_REVIEW','APPROVED','REJECTED','REPROCESSED')),
    assigned_to UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS review_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_task_id UUID NOT NULL REFERENCES review_tasks(id) ON DELETE CASCADE,
    reviewer_id UUID REFERENCES users(id) ON DELETE SET NULL,
    canonical_code TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- audit log (no secrets ever) ----------
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    entity TEXT NOT NULL,
    entity_id UUID,
    action TEXT NOT NULL CHECK (action IN ('CREATE','UPDATE','DELETE','REVIEW','APPROVE','REJECT','REPROCESS','EXPORT')),
    old_json JSONB,
    new_json JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_entity_idx ON audit_logs (entity, entity_id);
CREATE INDEX IF NOT EXISTS audit_org_idx ON audit_logs (organization_id, created_at);

CREATE INDEX IF NOT EXISTS runs_doc_idx ON extraction_runs (document_id, started_at);
CREATE INDEX IF NOT EXISTS field_results_run_idx ON extraction_field_results (extraction_run_id);
CREATE INDEX IF NOT EXISTS jobs_state_idx ON processing_jobs (state);

-- 006_analytics_audit.sql: analytics run history + a tamper-evident, append-only audit log.
-- Additive only: no existing column, row or view is changed (reporting.invoice_export is untouched).

-- ---------- analytics runs (one row per periodic / manual analysis) ----------
CREATE TABLE IF NOT EXISTS analytics_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID REFERENCES organizations(id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'RUNNING' CHECK (status IN ('RUNNING','COMPLETED','FAILED')),
    trigger TEXT NOT NULL DEFAULT 'manual',          -- manual | schedule | api
    engine_version TEXT NOT NULL,
    parameters JSONB NOT NULL DEFAULT '{}'::jsonb,
    input_invoices INT,
    input_fingerprint TEXT,                          -- SHA-256 of the rows that were analysed
    period_from DATE,
    period_to DATE,
    insights JSONB,                                  -- KPIs and findings, each with the invoices behind it
    outputs JSONB,                                   -- [{file, sha256, bytes}]
    error TEXT
);
CREATE INDEX IF NOT EXISTS analytics_runs_org_idx ON analytics_runs (organization_id, started_at DESC);

-- ---------- audit log: ordered, hash-chained, append-only ----------
CREATE SEQUENCE IF NOT EXISTS audit_logs_seq;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS seq BIGINT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS prev_hash TEXT;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS row_hash TEXT;

-- The digest covers every business column plus the previous row's digest, so changing, removing or
-- re-ordering any past entry breaks every digest after it.
CREATE OR REPLACE FUNCTION audit_row_digest(prev TEXT, r audit_logs) RETURNS TEXT
LANGUAGE sql STABLE AS $$
    SELECT encode(sha256(convert_to(concat_ws('|',
        coalesce(prev, ''), r.seq::text, r.id::text, coalesce(r.organization_id::text, ''),
        coalesce(r.user_id::text, ''), r.entity, coalesce(r.entity_id::text, ''), r.action,
        coalesce(r.old_json::text, ''), coalesce(r.new_json::text, ''),
        to_char(r.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')), 'UTF8')), 'hex')
$$;

-- Existing entries join the chain in the order they were written.
DO $$
DECLARE r audit_logs; prev TEXT := NULL;
BEGIN
    IF EXISTS (SELECT 1 FROM audit_logs WHERE row_hash IS NULL) AND
       NOT EXISTS (SELECT 1 FROM audit_logs WHERE row_hash IS NOT NULL) THEN
        FOR r IN SELECT * FROM audit_logs ORDER BY created_at, id LOOP
            r.seq := nextval('audit_logs_seq');
            UPDATE audit_logs SET seq = r.seq, prev_hash = prev, row_hash = audit_row_digest(prev, r)
             WHERE id = r.id;
            prev := audit_row_digest(prev, r);
        END LOOP;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS audit_logs_seq_idx ON audit_logs (seq);

-- New entries: the database (not the caller) assigns the position, the time and the digests.
CREATE OR REPLACE FUNCTION audit_logs_chain() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE prev TEXT;
BEGIN
    PERFORM pg_advisory_xact_lock(734003106);          -- one writer at a time keeps the chain linear
    SELECT row_hash INTO prev FROM audit_logs ORDER BY seq DESC LIMIT 1;
    NEW.seq := nextval('audit_logs_seq');
    NEW.created_at := clock_timestamp();               -- entries cannot be back-dated
    NEW.prev_hash := prev;
    NEW.row_hash := audit_row_digest(prev, NEW);
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION audit_logs_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_logs is append-only: % is not allowed', TG_OP;
END $$;

DROP TRIGGER IF EXISTS audit_logs_chain_insert ON audit_logs;
CREATE TRIGGER audit_logs_chain_insert BEFORE INSERT ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_chain();
DROP TRIGGER IF EXISTS audit_logs_no_change ON audit_logs;
CREATE TRIGGER audit_logs_no_change BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION audit_logs_immutable();
DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs;
CREATE TRIGGER audit_logs_no_truncate BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT EXECUTE FUNCTION audit_logs_immutable();

-- Recompute the whole chain. first_bad_seq is NULL when every entry is intact.
CREATE OR REPLACE FUNCTION audit_chain_verify() RETURNS TABLE (checked BIGINT, first_bad_seq BIGINT)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
DECLARE r audit_logs; prev TEXT := NULL; n BIGINT := 0;
BEGIN
    FOR r IN SELECT * FROM audit_logs ORDER BY seq LOOP
        n := n + 1;
        IF r.prev_hash IS DISTINCT FROM prev OR r.row_hash IS DISTINCT FROM audit_row_digest(prev, r) THEN
            checked := n; first_bad_seq := r.seq;
            RETURN NEXT;
            RETURN;
        END IF;
        prev := r.row_hash;
    END LOOP;
    checked := n; first_bad_seq := NULL;
    RETURN NEXT;
END $$;

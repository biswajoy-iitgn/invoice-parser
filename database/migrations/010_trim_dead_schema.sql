-- 010_trim_dead_schema.sql: drop 6 tables nothing reads or writes, merge 3 write-only ones.
-- Re-runnable: every statement is IF EXISTS / OR REPLACE.
--
-- Dropped outright (zero application SQL; display blurbs only):
--   users, roles, user_roles, processing_jobs, review_tasks, review_decisions
-- Merged:
--   analytics_runs      -> each run is already recorded in audit_logs; stop writing it
--   document_versions   -> re-runs already version via extraction_runs; version_id threading removed
--   invoice_fields      -> same values already in extraction_runs.final_output_json;
--                          reporting.invoice_export reads ex_rate from there now
-- Untouched: field_definitions / charge_types seeds, audit_logs rows, everything else.

-- ---------- Step 1. reporting.invoice_export without invoice_fields ----------
-- ex_rate now comes from the current run's stored fields (same source the app renders).
-- Column names, order, and one-row-per-invoice shape are unchanged (frozen Excel/ODBC contract).
-- Verified: old text_value and new JSON lookup agree on stored invoices (see plan notes).
CREATE OR REPLACE VIEW reporting.invoice_export AS
SELECT
    i.id AS invoice_id,
    i.organization_id,
    i.invoice_number,
    i.invoice_date,
    v.name::text AS vendor_name,
    i.container_number,
    i.mawb_no,
    i.hawb_no,
    i.bill_of_lading_hbl,
    i.weight,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'sea_freight') AS sea_freight,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'air_freight') AS air_freight,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'terminal_handling_charges') AS terminal_handling_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'airway_waybill_charges') AS airway_waybill_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'bl_charges') AS bl_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'ens_charges') AS ens_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'seal_charges') AS seal_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'vgm') AS vgm,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'edi_fees') AS edi_fees,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'dap_charges') AS dap_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'other_charges') AS other_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'ams_charges') AS ams_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'handling_charges') AS handling_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'pre_carriage_haulage') AS pre_carriage_haulage,
    r.final_output_json->'fields'->>'Ex Rate' AS ex_rate,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'misc') AS misc,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'transportation') AS transportation,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'warehouse_charges_port') AS warehouse_charges_port,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'examination_charges') AS examination_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'fuel_charges') AS fuel_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'cargo_screening') AS cargo_screening,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'agency_charges') AS agency_charges,
    MAX(c.amount) FILTER (WHERE ct.canonical_code = 'shipping_bill_charges') AS shipping_bill_charges,
    i.status AS invoice_status,
    i.review_status,
    i.extraction_status,
    i.updated_at,
    i.evidence_url
FROM invoices i
LEFT JOIN vendors v ON v.id = i.vendor_id
LEFT JOIN invoice_charges c ON c.invoice_id = i.id
LEFT JOIN charge_types ct ON ct.id = c.charge_type_id
LEFT JOIN extraction_runs r ON r.id = i.current_extraction_run_id
GROUP BY i.id, v.name, r.final_output_json;
GRANT SELECT ON reporting.invoice_export TO invoice_reporting_ro;

-- ---------- Step 2. RLS policy on a dropped table ----------
DROP POLICY IF EXISTS org_isolation ON users;

-- ---------- Step 3. FK constraints from kept tables to dropped tables ----------
ALTER TABLE audit_logs      DROP CONSTRAINT IF EXISTS audit_logs_user_id_fkey;
ALTER TABLE documents       DROP CONSTRAINT IF EXISTS documents_created_by_fkey;
ALTER TABLE invoices        DROP CONSTRAINT IF EXISTS invoices_document_version_id_fkey;
ALTER TABLE extraction_runs DROP CONSTRAINT IF EXISTS extraction_runs_document_version_id_fkey;

-- ---------- Step 4. Columns that only served the dropped versioning ----------
ALTER TABLE invoices        DROP COLUMN IF EXISTS document_version_id;
ALTER TABLE extraction_runs DROP COLUMN IF EXISTS document_version_id;

-- ---------- Step 5. Drop tables, children before parents ----------
DROP TABLE IF EXISTS review_decisions;
DROP TABLE IF EXISTS review_tasks;
DROP TABLE IF EXISTS processing_jobs;
DROP TABLE IF EXISTS user_roles;
DROP TABLE IF EXISTS analytics_runs;
DROP TABLE IF EXISTS document_versions;
DROP TABLE IF EXISTS invoice_fields;
DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS roles;

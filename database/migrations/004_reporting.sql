-- 004_reporting.sql: stable Excel contract. Model changes must never alter this view.
-- Charge columns pivot invoice_charges (one row per invoice per charge type).

CREATE SCHEMA IF NOT EXISTS reporting;

CREATE OR REPLACE VIEW reporting.invoice_export AS
SELECT
    i.id AS invoice_id,
    i.organization_id,
    i.invoice_number,
    i.invoice_date,
    v.name AS vendor_name,
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
    f_ex.text_value AS ex_rate,
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
    i.updated_at
FROM invoices i
LEFT JOIN vendors v ON v.id = i.vendor_id
LEFT JOIN invoice_charges c ON c.invoice_id = i.id
LEFT JOIN charge_types ct ON ct.id = c.charge_type_id
LEFT JOIN invoice_fields f_ex ON f_ex.invoice_id = i.id
    AND f_ex.field_definition_id = (SELECT id FROM field_definitions WHERE canonical_code = 'exchange_rate')
GROUP BY i.id, v.name, f_ex.text_value;

-- ---------- roles (conceptual; created here for local parity, in Neon by admin) ----------
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'invoice_reporting_ro') THEN
        CREATE ROLE invoice_reporting_ro NOLOGIN;
    END IF;
END $$;
GRANT USAGE ON SCHEMA reporting TO invoice_reporting_ro;
GRANT SELECT ON reporting.invoice_export TO invoice_reporting_ro;
-- NOTE: invoice_reporting_ro intentionally has no table grants and no
-- INSERT/UPDATE/DELETE anywhere. invoice_app_rw / invoice_db_admin are
-- provisioned by the DBA (see docs/NEON_SETUP.md), never by the app.

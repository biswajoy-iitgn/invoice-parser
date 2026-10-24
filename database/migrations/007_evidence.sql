-- 007_evidence.sql: the annotated page images kept for every processed invoice, and a link to them.
-- Additive only: one new table, one new nullable column, and one column appended to the END of
-- reporting.invoice_export (existing column names and their order are unchanged).

-- ---------- one row per stored page image (boxes drawn on the extracted values) ----------
CREATE TABLE IF NOT EXISTS invoice_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    invoice_id UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    extraction_run_id UUID REFERENCES extraction_runs(id) ON DELETE SET NULL,
    page_number INT NOT NULL CHECK (page_number >= 1),
    storage_uri TEXT NOT NULL,          -- path below EVIDENCE_DIR, e.g. <invoice id>/<run id>/page-1.jpg
    sha256 TEXT NOT NULL,               -- of the stored image file
    width INT,
    height INT,
    boxes INT NOT NULL DEFAULT 0,       -- how many values are marked on this page
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (extraction_run_id, page_number)
);
CREATE INDEX IF NOT EXISTS invoice_evidence_invoice_idx ON invoice_evidence (invoice_id, created_at DESC);
ALTER TABLE invoice_evidence ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON invoice_evidence;
CREATE POLICY org_isolation ON invoice_evidence FOR ALL
    USING (organization_id::text = current_setting('app.current_org', TRUE));

-- ---------- where a person opens the evidence of the invoice's current extraction ----------
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS evidence_url TEXT;

-- ---------- reporting view: same columns as 005, evidence_url appended ----------
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
    i.updated_at,
    i.evidence_url
FROM invoices i
LEFT JOIN vendors v ON v.id = i.vendor_id
LEFT JOIN invoice_charges c ON c.invoice_id = i.id
LEFT JOIN charge_types ct ON ct.id = c.charge_type_id
LEFT JOIN invoice_fields f_ex ON f_ex.invoice_id = i.id
    AND f_ex.field_definition_id = (SELECT id FROM field_definitions WHERE canonical_code = 'exchange_rate')
GROUP BY i.id, v.name, f_ex.text_value;

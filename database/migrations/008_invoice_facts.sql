-- 008_invoice_facts.sql: full capture - everything printed on an invoice, one row per item, each with its box.
-- Additive only. The 31-field view reporting.invoice_export is not touched.

CREATE TABLE IF NOT EXISTS invoice_facts (
    id BIGSERIAL PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    invoice_id UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    extraction_run_id UUID NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
    seq INT NOT NULL,                         -- reading order on the document
    kind TEXT NOT NULL CHECK (kind IN ('pair','table','block','text')),
    section TEXT NOT NULL DEFAULT '',         -- the heading above the item, or which table row it is in
    label TEXT NOT NULL DEFAULT '',           -- the printed label / column title ('' for plain text)
    value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'text',  -- gstin, pan, irn, email, ifsc, phone, amount, percent, date, number, ...
    output_field TEXT NOT NULL DEFAULT '',    -- which of the 31 output fields this item is the source of, if any
    page_number INT NOT NULL CHECK (page_number >= 1),
    bbox JSONB NOT NULL,                      -- [x0, y0, x1, y1] of the value (PDF points, or pixels for an image)
    label_bbox JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (extraction_run_id, seq)
);
CREATE INDEX IF NOT EXISTS invoice_facts_invoice_idx ON invoice_facts (invoice_id, extraction_run_id, seq);
CREATE INDEX IF NOT EXISTS invoice_facts_type_idx ON invoice_facts (organization_id, value_type) WHERE value_type <> 'text';
ALTER TABLE invoice_facts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON invoice_facts;
CREATE POLICY org_isolation ON invoice_facts FOR ALL
    USING (organization_id::text = current_setting('app.current_org', TRUE));

-- What Excel and reports read: the items of each invoice's current extraction, in reading order.
CREATE OR REPLACE VIEW reporting.invoice_details AS
SELECT
    i.id AS invoice_id,
    i.organization_id,
    i.invoice_number,
    v.name::text AS vendor_name,
    f.seq,
    f.page_number,
    f.kind,
    f.section,
    f.label,
    f.value,
    f.value_type,
    f.output_field
FROM invoice_facts f
JOIN invoices i ON i.id = f.invoice_id AND i.current_extraction_run_id = f.extraction_run_id
LEFT JOIN vendors v ON v.id = i.vendor_id;

GRANT SELECT ON reporting.invoice_details TO invoice_reporting_ro;

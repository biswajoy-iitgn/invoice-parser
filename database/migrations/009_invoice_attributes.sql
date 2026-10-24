-- 009_invoice_attributes.sql: the extracted items of an invoice, arranged under named details by the organizer
-- (invoice_extractor/organizer.py). Additive only.

CREATE TABLE IF NOT EXISTS invoice_attributes (
    id BIGSERIAL PRIMARY KEY,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    invoice_id UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    extraction_run_id UUID NOT NULL REFERENCES extraction_runs(id) ON DELETE CASCADE,
    seq INT NOT NULL,
    grp TEXT NOT NULL,                        -- Vendor, Customer, Invoice, Shipment, Totals, Payment, E-invoice
    detail TEXT NOT NULL,                     -- vendor_gstin, due_date, port_of_loading, invoice_total, ...
    value TEXT NOT NULL,                      -- exactly as printed (text of the extracted item it came from)
    normalized TEXT NOT NULL DEFAULT '',      -- ISO date, plain number, upper-case id
    value_type TEXT NOT NULL DEFAULT 'text',
    valid BOOLEAN NOT NULL DEFAULT TRUE,      -- passed the check for its type (a GSTIN looks like a GSTIN, ...)
    page_number INT NOT NULL CHECK (page_number >= 1),
    bbox JSONB NOT NULL,
    source_items JSONB NOT NULL DEFAULT '[]'::jsonb,   -- invoice_facts.seq of the item(s) the value was found in
    printed_label TEXT NOT NULL DEFAULT '',
    grounding TEXT NOT NULL DEFAULT '',       -- how the value was tied to an extracted item
    arranged_by TEXT NOT NULL DEFAULT '',     -- the model that proposed the arrangement
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (extraction_run_id, seq)
);
CREATE INDEX IF NOT EXISTS invoice_attributes_invoice_idx ON invoice_attributes (invoice_id, extraction_run_id, seq);
CREATE INDEX IF NOT EXISTS invoice_attributes_detail_idx ON invoice_attributes (organization_id, detail, normalized);
ALTER TABLE invoice_attributes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON invoice_attributes;
CREATE POLICY org_isolation ON invoice_attributes FOR ALL
    USING (organization_id::text = current_setting('app.current_org', TRUE));

-- one row per arranged detail (current extraction of each invoice)
CREATE OR REPLACE VIEW reporting.invoice_attributes AS
SELECT
    i.id AS invoice_id,
    i.organization_id,
    i.invoice_number,
    v.name::text AS vendor_name,
    a.seq,
    a.grp AS detail_group,
    a.detail,
    a.value,
    a.normalized,
    a.value_type,
    a.valid,
    a.page_number,
    a.printed_label,
    a.grounding,
    a.arranged_by
FROM invoice_attributes a
JOIN invoices i ON i.id = a.invoice_id AND i.current_extraction_run_id = a.extraction_run_id
LEFT JOIN vendors v ON v.id = i.vendor_id;

GRANT SELECT ON reporting.invoice_attributes TO invoice_reporting_ro;

-- one row per invoice, one column per detail (values that failed their check are left out; several values are
-- joined with '; '). Generated from invoice_extractor/organizer.py DETAILS.
CREATE OR REPLACE VIEW reporting.invoice_attributes_wide AS
SELECT
    i.id AS invoice_id,
    i.organization_id,
    i.invoice_number,
    i.invoice_date,
    v.name::text AS vendor,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vendor_name' AND a.valid) AS vendor_name,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vendor_address' AND a.valid) AS vendor_address,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vendor_gstin' AND a.valid) AS vendor_gstin,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vendor_pan' AND a.valid) AS vendor_pan,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vendor_state' AND a.valid) AS vendor_state,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vendor_phone' AND a.valid) AS vendor_phone,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vendor_email' AND a.valid) AS vendor_email,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vendor_cin' AND a.valid) AS vendor_cin,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'customer_name' AND a.valid) AS customer_name,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'customer_address' AND a.valid) AS customer_address,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'customer_gstin' AND a.valid) AS customer_gstin,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'customer_pan' AND a.valid) AS customer_pan,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'customer_code' AND a.valid) AS customer_code,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'invoice_type' AND a.valid) AS invoice_type,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'due_date' AND a.valid) AS due_date,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'payment_terms' AND a.valid) AS payment_terms,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'place_of_supply' AND a.valid) AS place_of_supply,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'po_number' AND a.valid) AS po_number,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'job_number' AND a.valid) AS job_number,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'commercial_invoice_number' AND a.valid) AS commercial_invoice_number,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'shipping_bill_number' AND a.valid) AS shipping_bill_number,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'shipping_bill_date' AND a.valid) AS shipping_bill_date,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'currency' AND a.valid) AS currency,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'shipper' AND a.valid) AS shipper,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'consignee' AND a.valid) AS consignee,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'incoterm' AND a.valid) AS incoterm,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'port_of_loading' AND a.valid) AS port_of_loading,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'port_of_discharge' AND a.valid) AS port_of_discharge,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'place_of_delivery' AND a.valid) AS place_of_delivery,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'vessel_voyage' AND a.valid) AS vessel_voyage,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'etd' AND a.valid) AS etd,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'eta' AND a.valid) AS eta,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'master_bl_number' AND a.valid) AS master_bl_number,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'packages' AND a.valid) AS packages,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'net_weight' AND a.valid) AS net_weight,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'chargeable_weight' AND a.valid) AS chargeable_weight,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'volume' AND a.valid) AS volume,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'container_type' AND a.valid) AS container_type,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'goods_description' AND a.valid) AS goods_description,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'hs_code' AND a.valid) AS hs_code,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'taxable_total' AND a.valid) AS taxable_total,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'cgst_total' AND a.valid) AS cgst_total,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'sgst_total' AND a.valid) AS sgst_total,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'igst_total' AND a.valid) AS igst_total,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'invoice_total' AND a.valid) AS invoice_total,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'amount_in_words' AND a.valid) AS amount_in_words,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'bank_name' AND a.valid) AS bank_name,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'bank_account_number' AND a.valid) AS bank_account_number,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'bank_ifsc' AND a.valid) AS bank_ifsc,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'bank_swift' AND a.valid) AS bank_swift,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'irn' AND a.valid) AS irn,
    string_agg(a.value, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'ack_number' AND a.valid) AS ack_number,
    string_agg(a.normalized, '; ' ORDER BY a.seq) FILTER (WHERE a.detail = 'ack_date' AND a.valid) AS ack_date
FROM invoices i
LEFT JOIN vendors v ON v.id = i.vendor_id
LEFT JOIN invoice_attributes a ON a.invoice_id = i.id AND a.extraction_run_id = i.current_extraction_run_id
GROUP BY i.id, v.name;

GRANT SELECT ON reporting.invoice_attributes_wide TO invoice_reporting_ro;

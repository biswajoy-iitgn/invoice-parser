-- 011_tenant_gaps_and_vendor_key.sql: close the RLS gaps left by 003, add a
-- spelling-insensitive vendor key. Append-only: never edits an applied migration.
--
-- (f) 003 enabled RLS only on organizations/users/vendors/documents/invoices (and
-- 007-009 covered invoice_evidence/facts/attributes). The remaining tenant tables
-- get org_isolation here. Tables without organization_id enforce it through their
-- parent (runs -> documents, field results -> runs -> documents, charges -> invoices).
-- Global reference data (models, model_versions, model_configurations,
-- model_configuration_models, charge_types, field_definitions) is shared across
-- orgs and intentionally has no RLS. Tables 010 dropped need nothing.
--
-- (g) NOTE on the 003 seed: 003 inserts header + charge rows into field_definitions
-- (mirroring charge_types, incl. the "Handlign Charges" contract typo). After 010
-- dropped invoice_fields -- per-invoice values now live in
-- extraction_runs.final_output_json -- those field_definitions rows are
-- reference/seed data only: nothing writes per-invoice rows against them. They are
-- kept as-is (contracts assert the 31 + 22 seeds) but write-dead.

-- ---------- (e) spelling-insensitive vendor key ----------
-- Server-side twin of database.db.vendor_key (Python): case, punctuation,
-- "&"/"and", and Pvt Ltd / Private Limited fold to one key.
CREATE OR REPLACE FUNCTION vendor_normalized(name TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT btrim(regexp_replace(regexp_replace(regexp_replace(regexp_replace(regexp_replace(
        regexp_replace(regexp_replace(lower(replace(name, '&', ' and ')), '[^a-z0-9]+', ' ', 'g'),
        '\ypvt\y', 'private', 'g'),
        '\yltd\y', 'limited', 'g'),
        '\yco\y', 'company', 'g'),
        '\ycorp\y', 'corporation', 'g'),
        '\yinc\y', 'incorporated', 'g'),
        '\s+', ' ', 'g'))
$$;

ALTER TABLE vendors ADD COLUMN IF NOT EXISTS normalized_name TEXT;
-- Earliest-created row per (org, key) keeps the key; older normalized-duplicates
-- stay NULL (unique indexes ignore NULLs), so the backfill cannot fail and the
-- keyed row is exactly the one the old first-created-wins scan returned.
UPDATE vendors v SET normalized_name = vendor_normalized(v.name)
WHERE v.normalized_name IS NULL
  AND NOT EXISTS (
      SELECT 1 FROM vendors w
      WHERE w.organization_id = v.organization_id
        AND w.id <> v.id
        AND vendor_normalized(w.name) = vendor_normalized(v.name)
        AND (w.created_at, w.id) < (v.created_at, v.id));
CREATE UNIQUE INDEX IF NOT EXISTS vendors_org_normalized_uidx
    ON vendors (organization_id, normalized_name);

-- ---------- (f) missing RLS ----------
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON audit_logs;
CREATE POLICY org_isolation ON audit_logs FOR ALL
    USING (organization_id::text = current_setting('app.current_org', TRUE));

ALTER TABLE extraction_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON extraction_runs;
CREATE POLICY org_isolation ON extraction_runs FOR ALL
    USING (EXISTS (SELECT 1 FROM documents d WHERE d.id = extraction_runs.document_id
                   AND d.organization_id::text = current_setting('app.current_org', TRUE)));

ALTER TABLE extraction_field_results ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON extraction_field_results;
CREATE POLICY org_isolation ON extraction_field_results FOR ALL
    USING (EXISTS (SELECT 1 FROM extraction_runs r JOIN documents d ON d.id = r.document_id
                   WHERE r.id = extraction_field_results.extraction_run_id
                   AND d.organization_id::text = current_setting('app.current_org', TRUE)));

ALTER TABLE invoice_charges ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS org_isolation ON invoice_charges;
CREATE POLICY org_isolation ON invoice_charges FOR ALL
    USING (EXISTS (SELECT 1 FROM invoices i WHERE i.id = invoice_charges.invoice_id
                   AND i.organization_id::text = current_setting('app.current_org', TRUE)));

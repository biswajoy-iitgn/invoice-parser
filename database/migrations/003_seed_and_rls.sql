-- 003_seed_and_rls.sql: canonical ontology seed + row-level security.
-- Seed mirrors invoice_extractor/ontology.py (external contract incl. the
-- "Handlign Charges" typo -> handling_charges).

INSERT INTO field_definitions (canonical_code, canonical_name, description, data_type, is_critical) VALUES
    ('invoice_number', 'Invoice No', 'issuer invoice/document number', 'TEXT', TRUE),
    ('invoice_date', 'Invoice Date', 'invoice/document date', 'DATE', TRUE),
    ('container_number', 'Container Number', 'ISO 6346 container numbers', 'TEXT', TRUE),
    ('mawb_number', 'MAWB No', 'master air waybill (air only)', 'TEXT', TRUE),
    ('hawb_number', 'HAWB No', 'house air waybill (air only)', 'TEXT', TRUE),
    ('vendor_name', 'Vendor Name', 'invoice issuer name', 'TEXT', TRUE),
    ('bill_of_lading_hbl', 'Bill of Lading / HBL', 'house (or master) B/L number', 'TEXT', TRUE),
    ('weight', 'WEIGHT', 'gross/chargeable weight with unit', 'TEXT', TRUE),
    ('exchange_rate', 'Ex Rate', 'foreign-currency -> INR rates', 'TEXT', FALSE)
ON CONFLICT (canonical_code) DO NOTHING;

INSERT INTO charge_types (canonical_code, canonical_name, description) VALUES
    ('sea_freight', 'Sea Freight', 'ocean/sea freight'),
    ('air_freight', 'Air Freight', 'air freight'),
    ('terminal_handling_charges', 'Terminal Handling Charges', 'THC origin/destination'),
    ('airway_waybill_charges', 'Airway WayBill Charges', 'AWB issuance fee'),
    ('bl_charges', 'BL Charges', 'B/L issuance/documentation fee'),
    ('ens_charges', 'ENS Charges', 'EU ENS/ICS2 filing'),
    ('seal_charges', 'Seal Charges', 'container seal fee'),
    ('vgm', 'VGM', 'verified gross mass fee'),
    ('edi_fees', 'EDI Fees', 'EDI/e-filing fees'),
    ('dap_charges', 'DAP Charges', 'DAP/DDP destination charges'),
    ('other_charges', 'Other Charges', 'real charge fitting no other category'),
    ('ams_charges', 'AMS CHARGES', 'US AMS/ISF filing'),
    ('handling_charges', 'Handlign Charges', 'cargo handling ex-terminal (external name keeps the contract typo)'),
    ('pre_carriage_haulage', 'Pre Carriage Haulage', 'inland haulage to port of loading'),
    ('misc', 'Misc', 'explicitly miscellaneous/sundry lines'),
    ('transportation', 'Transportation', 'local trucking/cartage'),
    ('warehouse_charges_port', 'Warehouse charges Port', 'CFS/storage/ground rent'),
    ('examination_charges', 'Examination charges', 'customs exam/inspection'),
    ('fuel_charges', 'Fuel Charges', 'fuel/bunker surcharges'),
    ('cargo_screening', 'Cargo Screening', 'security screening/X-ray'),
    ('agency_charges', 'Agency Charges', 'CHA/clearing agency fee'),
    ('shipping_bill_charges', 'Shipping bill charges', 'export shipping-bill filing')
ON CONFLICT (canonical_code) DO NOTHING;

-- Charge categories are also addressable as fields for extraction results.
INSERT INTO field_definitions (canonical_code, canonical_name, description, data_type, is_critical)
    SELECT canonical_code, canonical_name, description, 'NUMERIC', FALSE FROM charge_types
ON CONFLICT (canonical_code) DO NOTHING;

-- ---------- row-level security ----------
-- Tenancy is enforced by organization_id. The app sets:
--   SET LOCAL app.current_org = '<org uuid>';
-- for every transactional statement (see database/db.py).
ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendors ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE invoices ENABLE ROW LEVEL SECURITY;

CREATE POLICY org_isolation ON organizations FOR ALL
    USING (id::text = current_setting('app.current_org', TRUE));
CREATE POLICY org_isolation ON users FOR ALL
    USING (organization_id::text = current_setting('app.current_org', TRUE));
CREATE POLICY org_isolation ON vendors FOR ALL
    USING (organization_id::text = current_setting('app.current_org', TRUE));
CREATE POLICY org_isolation ON documents FOR ALL
    USING (organization_id::text = current_setting('app.current_org', TRUE));
CREATE POLICY org_isolation ON invoices FOR ALL
    USING (organization_id::text = current_setting('app.current_org', TRUE));

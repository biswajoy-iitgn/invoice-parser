-- 001_core.sql: organizations, identity, documents, invoices, canonical fields, charges.
-- Idempotent where cheap (IF NOT EXISTS); versioned — never edit after apply.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS citext;

-- ---------- multi-tenancy ----------
CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE CHECK (name IN ('ADMIN','EXTRACTOR','REVIEWER','VIEWER','AUDITOR'))
);

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    email CITEXT UNIQUE,
    password_hash TEXT NOT NULL,  -- never plaintext (bcrypt/argon2 by the app)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ
);
-- (citext extension created at top of this migration)

CREATE TABLE IF NOT EXISTS user_roles (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, role_id)
);

-- ---------- vendors ----------
CREATE TABLE IF NOT EXISTS vendors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    UNIQUE (organization_id, name)
);

-- ---------- documents ----------
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    document_type TEXT NOT NULL DEFAULT 'invoice',
    original_filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_size BIGINT NOT NULL CHECK (file_size >= 0),
    storage_uri TEXT NOT NULL,
    document_hash TEXT NOT NULL,  -- sha256 hex, idempotency key per org
    page_count INT CHECK (page_count IS NULL OR page_count >= 0),
    source TEXT NOT NULL DEFAULT 'upload',
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','ARCHIVED')),
    created_by UUID REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at TIMESTAMPTZ,
    UNIQUE (organization_id, document_hash)
);

CREATE TABLE IF NOT EXISTS document_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version_number INT NOT NULL CHECK (version_number >= 1),
    source_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_number)
);

-- ---------- invoices ----------
CREATE TABLE IF NOT EXISTS invoices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE RESTRICT,
    document_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    document_version_id UUID REFERENCES document_versions(id) ON DELETE SET NULL,
    invoice_number TEXT,
    invoice_date DATE,
    vendor_id UUID REFERENCES vendors(id) ON DELETE SET NULL,
    container_number TEXT,
    mawb_no TEXT,
    hawb_no TEXT,
    bill_of_lading_hbl TEXT,
    weight TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','IN_REVIEW','APPROVED','REJECTED','REPROCESSED')),
    review_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (review_status IN ('PENDING','IN_REVIEW','APPROVED','REJECTED','REPROCESSED')),
    extraction_status TEXT NOT NULL DEFAULT 'PENDING' CHECK (extraction_status IN ('PENDING','RUNNING','COMPLETED','FAILED')),
    current_extraction_run_id UUID,  -- FK added in 002 (extraction_runs)
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Invoice numbers are NOT globally unique (same number across orgs/vendors);
-- uniqueness is per (organization, vendor, number) only when all are known.
CREATE UNIQUE INDEX IF NOT EXISTS invoices_org_vendor_number_uidx
    ON invoices (organization_id, vendor_id, invoice_number)
    WHERE invoice_number IS NOT NULL AND vendor_id IS NOT NULL;

-- ---------- canonical field ontology ----------
CREATE TABLE IF NOT EXISTS field_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_code TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    data_type TEXT NOT NULL CHECK (data_type IN ('TEXT','NUMERIC','DATE','JSON')),
    is_critical BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

-- ---------- per-invoice field values ----------
CREATE TABLE IF NOT EXISTS invoice_fields (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    field_definition_id UUID NOT NULL REFERENCES field_definitions(id) ON DELETE RESTRICT,
    text_value TEXT,
    numeric_value NUMERIC,
    date_value DATE,
    json_value JSONB,
    unit TEXT,
    currency TEXT,
    confidence NUMERIC CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    source_text TEXT,
    source_bbox NUMERIC[4],
    source_page INT,
    validation_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (validation_status IN ('PENDING','SUPPORTED','UNSUPPORTED','REVIEW')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (invoice_id, field_definition_id)
);

-- ---------- charges (no per-type columns) ----------
CREATE TABLE IF NOT EXISTS charge_types (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_code TEXT NOT NULL UNIQUE,
    canonical_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    is_active BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE IF NOT EXISTS invoice_charges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id UUID NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    charge_type_id UUID NOT NULL REFERENCES charge_types(id) ON DELETE RESTRICT,
    original_description TEXT,
    amount NUMERIC,  -- NUMERIC for money, never FLOAT
    currency TEXT,
    exchange_rate NUMERIC,
    quantity NUMERIC,
    unit TEXT,
    source_text TEXT,
    source_bbox NUMERIC[4],
    source_page INT,
    confidence NUMERIC CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- indexes ----------
CREATE INDEX IF NOT EXISTS documents_org_idx ON documents (organization_id);
CREATE INDEX IF NOT EXISTS invoices_org_idx ON invoices (organization_id);
CREATE INDEX IF NOT EXISTS invoices_number_idx ON invoices (organization_id, invoice_number);
CREATE INDEX IF NOT EXISTS invoices_date_idx ON invoices (organization_id, invoice_date);
CREATE INDEX IF NOT EXISTS invoices_vendor_idx ON invoices (vendor_id);
CREATE INDEX IF NOT EXISTS invoices_container_idx ON invoices (container_number);
CREATE INDEX IF NOT EXISTS invoices_mawb_idx ON invoices (mawb_no);
CREATE INDEX IF NOT EXISTS invoices_hawb_idx ON invoices (hawb_no);
CREATE INDEX IF NOT EXISTS invoices_status_idx ON invoices (organization_id, status);
CREATE INDEX IF NOT EXISTS invoice_fields_invoice_idx ON invoice_fields (invoice_id);
CREATE INDEX IF NOT EXISTS invoice_charges_invoice_idx ON invoice_charges (invoice_id);

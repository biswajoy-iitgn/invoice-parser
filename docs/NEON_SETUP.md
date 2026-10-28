# Neon setup (PostgreSQL system of record)

## Environments

- One Neon project; branches: `dev`, `staging`, `prod` (or separate
  projects per environment if compliance requires).
- Local development uses local PostgreSQL, never a Neon branch, unless
  explicitly testing migrations against a throwaway branch.

## Environment variables (never committed)

```sh
DATABASE_URL=postgres://invoice_app_rw:<pw>@<host>/<db>?sslmode=require
DATABASE_URL_READONLY=postgres://invoice_reporting_ro:<pw>@<host>/<db>?sslmode=require
INVOICE_ORG_ID=<default organization uuid>   # service-level persistence only
```

SSL (`sslmode=require`) is mandatory; `database/db.py` appends it for any
non-local host that lacks it.

## Roles (created once by the DBA, not the app)

```sql
CREATE ROLE invoice_app_rw LOGIN PASSWORD '<vault>';
GRANT CONNECT ON DATABASE <db> TO invoice_app_rw;
GRANT USAGE ON SCHEMA public, reporting TO invoice_app_rw;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO invoice_app_rw;
ALTER DEFAULT PRIVILEGES GRANT SELECT, INSERT, UPDATE ON TABLES TO invoice_app_rw;

CREATE ROLE invoice_reporting_ro LOGIN PASSWORD '<vault>';
GRANT CONNECT ON DATABASE <db> TO invoice_reporting_ro;
GRANT USAGE ON SCHEMA reporting TO invoice_reporting_ro;
GRANT SELECT ON reporting.invoice_export TO invoice_reporting_ro;
```

The app never uses an admin role; Excel/ODBC uses `invoice_reporting_ro`.

## Migrations

```sh
DATABASE_URL='<dev-branch-url>' .venv/bin/python -c "from database.db import migrate; print(migrate())"
```

- Migrations live in `database/migrations/`, applied in filename order,
  tracked in `schema_migrations`. Re-runs are no-ops (verified).
- `migrate()` refuses non-local hosts without `ALLOW_PROD_MIGRATE=1`.
- Promote branches dev → staging → prod via Neon branching/merge; run
  migrations per branch after promotion.

## Backup / recovery

- Neon provides point-in-time recovery per branch (retention per plan);
  restore = create a branch from the recovery point, verify, promote.
- Additionally: nightly `pg_dump --schema-only` + weekly full dump of the
  reporting-critical tables to encrypted object storage (runbook TBD by ops).
- Restore test: NOT PERFORMED (no Neon project exists yet).

## Current status (2026-10-03)

- Neon project is live (PostgreSQL 18, `neondb`); migrations 001-005 applied; organization
  "Harsha Engineering"; `invoice_reporting_ro` has SELECT on `reporting.invoice_export` only.
- The app reads `DATABASE_URL` and `INVOICE_ORG_ID` from `.env.local` (gitignored, mode 600), loaded by
  `scripts/run_app.sh`. `GET /ready` reports the database check; each batch item reports `persisted`.
- The app currently connects as the database OWNER. That works, but the owner bypasses row-level
  security and can change the schema: create `invoice_app_rw` (see Roles above) and switch
  `DATABASE_URL` to it. `invoice_app_rw` does not exist yet.
- Each invoice is written in one connection and one transaction (about 1 s from this Mac).
- Contract tests: `tests/test_database.py` (6) and `tests/test_odbc.py` (2) pass on a scratch cluster.

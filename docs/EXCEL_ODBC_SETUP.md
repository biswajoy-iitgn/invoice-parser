# ODBC + Excel setup — macOS (Actual Technologies) and Windows (psqlODBC)

Target DSN name on both platforms: `NEON_INVOICE_DB`.
Database account: `invoice_reporting_ro` (SELECT on `reporting.invoice_export` only).
Credentials live in the OS credential store / DSN prompt — never in Excel
cells, formulas, VBA, source code, or Git.

## Verified environment facts (2026-10-02, this Mac)

- Excel for Mac installed, universal binary (arm64 + x86_64).
- iODBC runtime present (`/usr/local/lib/libiodbc*.dylib`); Excel for Mac
  uses iODBC only.
- Actual Technologies "Open Source Databases" bundle present in
  `/Library/ODBC`, universal binary, PostgreSQL-capable (© 2026 Actual
  Technologies, LLC — the user's licensed driver).
- unixODBC + psqlODBC installed via Homebrew for CLI/tests; live ODBC query
  of `reporting.invoice_export` verified (parameterised invoice lookup).

## Verified on this Mac (2026-10-03)

Checked with `scripts/check_excel_odbc.py` (iODBC + Actual driver, the chain Excel uses) and with a
real import inside Excel (query table, DSN `NEON_INVOICE_DB`) against Neon:

- **`Type = PostgreSQL` is required in the DSN.** Without it the Actual "Open Source Databases" driver
  speaks MySQL to the PostgreSQL server and Excel hangs. `SSLMode` must be lowercase `require`
  (`REQUIRE` is rejected as an invalid sslmode).
- Single-invoice lookup: correct values, dates as dates, charges as numbers, blanks for absent fields.
- **The Actual driver is running on an evaluation licence: every query returns at most 3 rows.**
  A one-invoice lookup is unaffected; importing the whole view silently stops at 3 rows until a
  licence key is entered (ODBC Manager -> the driver's licence tab).
- iODBC symlinks `~/.odbc.ini` and `~/.odbcinst.ini` to `~/Library/ODBC/`; the psqlODBC entries used
  by the command-line tests therefore live in `~/Library/ODBC/odbcinst.ini` next to the Actual driver.
- The system DSN `Neon_IDP` in `/Library/ODBC/odbc.ini` (database `postgres`, SSL disabled, owner
  account) cannot connect to Neon; use `NEON_INVOICE_DB`.
- Not verified: logging in as `invoice_reporting_ro` (its password is not stored anywhere on this Mac).

## Free driver inside Excel, and the live workbook (2026-10-04)

The 3-row cap of the unlicensed Actual driver is no longer a constraint: the free psqlODBC driver now works inside
Excel for Mac as the data source `NEON_INVOICE_PG`, and `excel/Invoice_Live_Query.xlsx` reads the database through
it on Mac and Windows. Setup, sheets and what was verified: **`docs/EXCEL_LIVE_QUERY.md`**.

What had to be solved on macOS (all handled by `odbc/build_excel_pg_driver.py`):

- Excel is sandboxed and cannot load a driver from Homebrew's folder (`/opt/homebrew`): the driver and the libraries
  it needs are copied into Excel's own data folder, re-linked to each other and re-signed.
- Homebrew's psqlODBC is built for unixODBC; Excel uses iODBC. The ANSI build (`psqlodbca.so`) is used and its
  installer library is re-pointed to iODBC's.
- Replacing the driver files while Excel has them loaded crashes Excel, so the script builds in a scratch folder
  and moves the files into place.
- `~/Library/ODBC/odbc.ini` is the same file Excel sees inside its sandbox (a hard link): the script rewrites it in
  place and never replaces it.

Two limits of Excel's query engine that the workbook works around: a text parameter may not be longer than 255
characters (the list of looked-up invoice numbers is passed in 240-character pieces), and a query sheet cannot hold
a formula column inside the query range (the "Open" links sit in their own column).

## One-command export (no row limit, macOS and Windows)

For a full extract, use the export command instead of a live Excel query; it reads the same view with a
free PostgreSQL driver (psqlODBC through pyodbc when installed, otherwise psycopg) and is not subject to
the 3-row cap of an unlicensed Actual driver.

```sh
./export_invoices.sh                               # macOS / Linux: all invoices -> exports/invoices_<time>.xlsx
export_invoices.bat                                # Windows (double-click works); needs Python 3.10+
./export_invoices.sh --from 2026-04-01 --to 2026-06-30 --vendor dachser
```

Sheets: Invoices (the 31 fields with typed dates and amounts, filters on), Charges (one row per charge,
ready for a pivot table), By vendor, By category, Export info (who, when, driver, row count, data
fingerprint). The database address is read from `DATABASE_URL_READONLY` or `DATABASE_URL` in the
environment or `.env.local`; nothing secret is written to the workbook. Each export is noted in the audit
log when the account may write there. Verified on macOS with both drivers; the Windows launcher is
untested on a Windows machine.

## macOS — Excel via Actual Technologies (iODBC)

1. Open the ODBC Administrator (installed with the Actual drivers, or
   `/Applications/Utilities/ODBC Administrator.app`).
2. System DSN (or User DSN) → Add → "Actual Open Source Databases".
3. Name: `NEON_INVOICE_DB`. Type **PostgreSQL**, Server: `<neon-host>`, Port `5432`,
   Database `<db>`, User `invoice_reporting_ro`, SSL mode `require`.
   Save the password in the macOS Keychain entry the driver offers
   (or leave blank to be prompted per refresh — never type it into Excel).
4. Test: the administrator's Test button should connect.
5. In Excel: Data → Get Data → From Database → From ODBC → DSN
   `NEON_INVOICE_DB` → enter the lookup SQL from `docs/EXCEL_LOOKUP.md`.

DSN file equivalent (`~/Library/ODBC/odbc.ini`) — see
`odbc/odbc.ini.template` (iODBC section).

## Windows — Excel via psqlODBC (64-bit)

1. Install the 64-bit psqlODBC driver (matching Excel's bitness: 64-bit
   Excel needs the x64 driver): https://odbc.postgresql.org/
2. Open "ODBC Data Sources (64-bit)" → System DSN → Add →
   "PostgreSQL Unicode(x64)". Name: `NEON_INVOICE_DB`.
   Server `<neon-host>`, Port `5432`, Database `<db>`,
   User `invoice_reporting_ro`, SSL Mode `require`.
   PowerShell equivalent (run as admin for System DSN):
   ```powershell
   Add-OdbcDsn -Name "NEON_INVOICE_DB" -DriverName "PostgreSQL Unicode(x64)" `
     -DsnType System -SetPropertyValue @("Servername=<neon-host>", "Port=5432",
     "Database=<db>", "Username=invoice_reporting_ro", "SSLMode=require")
   ```
3. In Excel: Data → Get Data → From Other Sources → From ODBC → DSN
   `NEON_INVOICE_DB` → same lookup SQL as macOS.

## Troubleshooting matrix

| Symptom | Likely cause | Fix |
|---|---|---|
| Driver not listed (Mac) | Actual bundle not installed for all users | Reinstall Actual pack; check `/Library/ODBC` |
| Architecture mismatch (Win) | 32-bit driver vs 64-bit Excel | Install x64 psqlODBC; use 64-bit administrator |
| SSL error | Neon requires TLS | Set SSLMode=require on the DSN |
| Auth failure | wrong role/password | Use `invoice_reporting_ro`; rotate via DBA, not Excel |
| Empty result | invoice number not present | Controlled "not found" message (see EXCEL_LOOKUP.md) |
| Timeout | network/VPN | Retry; check Neon branch status |

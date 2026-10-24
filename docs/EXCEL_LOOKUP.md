# Excel invoice lookup (macOS + Windows)

Queries `reporting.invoice_export` through DSN `NEON_INVOICE_DB` with a
parameterised invoice number. No VBA required; Power Query (Get Data →
From ODBC) on both platforms.

## Worksheet layout (sheet "Lookup")

- `B2`: invoice number input (e.g. `INV-2026-00125`).
- `A5:AE5`: headers = view columns (see below).
- `A6:AE6`: query result (single row expected).

## Query

Advanced-editor (Power Query) form; the ODBC statement itself:

```sql
SELECT * FROM reporting.invoice_export WHERE invoice_number = ?
```

Power Query M (parameter from cell B2 via a named range `InvoiceNo`):

```m
let
    inv = Excel.CurrentWorkbook(){[Name="InvoiceNo"]}[Content]{0}[Column1],
    src = Odbc.Query("DSN=NEON_INVOICE_DB",
        "SELECT * FROM reporting.invoice_export WHERE invoice_number = '" &
        Text.Replace(inv, "'", "''") & "'")
in
    src
```

(ODBC `?` parameters are not portable across the Mac/Windows Excel ODBC
layers, so the value is inlined with quote-escaping; the DB account is
read-only regardless.)

MS Query alternative (Windows legacy): Data → From Other Sources →
From Microsoft Query → `NEON_INVOICE_DB` → same SQL with `[?]` prompt.

## Expected columns (view contract)

invoice_id, organization_id, invoice_number, invoice_date, vendor_name,
container_number, mawb_no, hawb_no, bill_of_lading_hbl, weight,
sea_freight, air_freight, terminal_handling_charges, airway_waybill_charges,
bl_charges, ens_charges, seal_charges, vgm, edi_fees, dap_charges,
other_charges, ams_charges, handling_charges, pre_carriage_haulage,
ex_rate, misc, transportation, warehouse_charges_port, examination_charges,
fuel_charges, cargo_screening, agency_charges, shipping_bill_charges,
invoice_status, review_status, extraction_status, updated_at.

Blank cells = field absent on the invoice (never guessed).

## Controlled behaviours (implement as cell formulas around the query)

- Not found (0 rows): show `Invoice not found: <B2>`.
- Duplicate (>1 row): show `Duplicate invoice <B2> — contact support`
  (same number under two vendors/orgs; refine by vendor).
- DB/ODBC/auth/timeout: Power Query error → wrapper cell shows
  `Reporting database unavailable — try again or contact support`.
  Never surface connection strings or credentials.

## Security

- Connection uses `invoice_reporting_ro`: SELECT-only (verified:
  INSERT denied). Refresh prompts for the password unless stored in the
  OS keychain via the driver dialog.
- Do not enable "save password in workbook". Do not embed the password
  in M code, cells, or VBA.

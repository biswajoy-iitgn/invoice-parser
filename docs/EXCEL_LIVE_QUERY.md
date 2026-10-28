# Excel live query — `excel/Invoice_Live_Query.xlsx`

One workbook, no macros, the same file on Excel for Mac and Excel for Windows. It reads the invoice database
through the data source `NEON_INVOICE_PG` (free psqlODBC driver, no row limit) and contains no data and no login.

## What the sheets do

| Sheet | What you do | What you get |
|---|---|---|
| **Query** | Type a filter: part of an invoice number, part of a vendor name, a date range, a review status. Leave everything empty for all invoices. | The sheets *Invoices* and *Charges* refresh by themselves. Counters show how many rows came back. |
| **Lookup** | Type or paste invoice numbers into column A (up to 200). | The rest of each row fills in from the database: the 31 fields, review status, last update, and an **Open** link to the evidence page. A number that is not in the database says so. |
| **Invoices** | — | One row per invoice that matches the filters; column A is an **Open** link to the invoice's evidence page (first 5,000 rows). |
| **Charges** | — | One row per charge of those invoices (invoice, date, vendor, charge, amount): ready for a pivot table. |
| **Details** | — | The other details of the invoices typed on *Lookup*, arranged under named details: vendor and customer (name, address, GSTIN, PAN), due date, PO and job numbers, shipping bills, ports, vessel, dates, packages, totals, bank, e-invoice. One row per detail with its group, the value as printed, a normalized form (ISO date, plain number), whether it passed its check, the page and the printed label. |
| **More fields** | — | The same details as columns: one row per invoice that matches the filters on *Query* (53 columns). Values that failed their check are left out; several values are joined with "; ". |

Matching is case-insensitive and ignores spaces around what you type. Dates can be typed as `2026-08-31`.

**Live data.** Every query re-reads the database when a filter or a looked-up number changes, when you choose
*Data ▸ Refresh All*, and by itself every minute while the workbook is open. An invoice processed in the app is in
the database as soon as it finishes, so it appears in Excel at the next refresh (at most about a minute later).

**Evidence link.** *Open* goes to `…/evidence/<invoice id>` in the app: the invoice's values and its pages with a box
on everything that was read (see `docs/AUDIT.md`). The link works on any computer that can reach the app; set
`PUBLIC_BASE_URL` (for example `http://invoice-server:8000`) before starting the app when Excel runs on other
computers. Links stored earlier keep the address they were stored with.

## One-time setup per computer

### macOS

```sh
brew install psqlodbc                          # the free driver
python3 odbc/build_excel_pg_driver.py           # copies it where Excel can load it and creates NEON_INVOICE_PG
python3 odbc/build_excel_pg_driver.py --save-login prompt    # optional: store a login so Excel never asks
```

Why the copy: Excel for Mac is sandboxed and cannot read Homebrew's folder, so the driver and the libraries it needs
are copied into Excel's own data folder, re-linked and re-signed. Run the command again after `brew upgrade psqlodbc`.
iODBC must be installed (it comes with the Actual drivers, or from iodbc.org).

### Windows

1. Install psqlODBC x64 once: <https://odbc.postgresql.org/> (`psqlodbc_x64.msi`; the x86 one for 32-bit Office).
2. `powershell -ExecutionPolicy Bypass -File odbc\setup_windows.ps1` (add `-SaveLogin` to store a login).
3. Open the workbook and choose **Enable Content** when Excel asks about external data connections.

### Which login

The data source is created for the read-only account `invoice_reporting_ro`, which can read the two reporting views
and nothing else. Its password has to be set once by the database owner (Neon console ▸ SQL editor):

```sql
ALTER ROLE invoice_reporting_ro WITH LOGIN PASSWORD '<a new password>';
```

Then run the setup command with `--save-login prompt` (macOS) or `-SaveLogin` (Windows) and type that user and
password. A stored login lives in the data source of that computer user only (macOS: `~/Library/ODBC/odbc.ini`,
readable by that user; Windows: the user's registry), never in the workbook.

Without a stored login: Excel for Windows shows the driver's login box on refresh. On this Mac a refresh without a
stored login returned no rows; whether Excel showed a prompt could not be observed. Store the login.

## What was checked, and what was not

Checked on this Mac with `excel/check_live_workbook.py` (it drives the real Excel and compares with the database):

- the free driver inside Excel returns every row (the Actual driver's trial licence stops at 3);
- seven filter combinations: the invoice list, the number of charge lines and the charge total match the database,
  both after a manual refresh and when only the filter cell was changed;
- lookup: 125 pasted numbers (a 1,500-character list, sent in pieces), every filled column identical to the
  database, unknown numbers reported as such, lower-case and padded input accepted;
- details: the number of arranged details and the first rows match the database; *More fields* has one row per
  invoice and all 55 columns;
- live: an invoice processed by the app showed its new *Last updated* in the untouched workbook within the refresh
  interval;
- the workbook file contains no login and no invoice data.

Not checked: **Excel for Windows** (no Windows machine here — the workbook uses only standard query-table features,
and `odbc/setup_windows.ps1` was not run); clicking *Open* inside Excel (the link formula and the page it points to
were checked separately); logging in as `invoice_reporting_ro` (its password is not known on this Mac — the checks
used the app's own login, handed to Excel for the duration of the check only).

## Rebuilding the workbook

`excel/build_live_workbook.py` (developer step, needs Excel for Mac): Excel itself creates the four queries, then the
file is patched to bind them to the filter cells and to remove the build-time login. Change `COLUMNS`, `LOOKUP_ROWS`,
`LINK_ROWS` or `REFRESH_MINUTES` there and run it again.

## Limits

- *Lookup* takes 200 numbers (about 5,700 characters in total; the column heading turns into a warning when the list
  is too long); *Invoices* carries the Open link on its first 5,000 rows (the data itself is not capped).
- Excel's own limit of 255 characters per query parameter is why the looked-up list is sent in pieces; a single
  filter text on *Query* longer than about 250 characters is refused by Excel ("Bad parameter type").
- A filter change refreshes two queries; over the internet each refresh takes one to a few seconds.
- For a one-off extract without Excel's data connection, `./export_invoices.sh` still writes a finished workbook.

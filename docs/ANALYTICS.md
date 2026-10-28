# Analytics engine

Reads the invoices from the database (view `reporting.invoice_export`, plus run history when the role
may see it), computes MIS figures and findings, and writes three files per run:

| File | For | Content |
|---|---|---|
| `dashboard.html` | anyone | charts, findings, data completeness, review queue; served by the app at `/analytics` |
| `mis_report.xlsx` | finance / management | Summary, Monthly, Vendors, Categories, Vendor x category, Freight per kg, Findings, Review queue, Data quality, About (with Excel charts) |
| `insights.json` | other systems | the same numbers, machine-readable |

Runs are written to `reports/analytics/<timestamp>/` and copied to `reports/analytics/latest/`.

## Running it

```sh
.venv/bin/python -m analytics.run                          # everything
.venv/bin/python -m analytics.run --from 2026-04-01 --to 2026-06-30
.venv/bin/python -m analytics.run --if-changed             # do nothing if no invoice changed
```

* **Periodic, inside the app:** when the app has a database, it re-analyses every
  `ANALYTICS_EVERY_MINUTES` (default 360) and skips the run when no invoice was added or changed.
  `ANALYTICS_EVERY_MINUTES=0` switches it off. `POST /api/analytics/run` runs it now;
  `GET /api/analytics/status` shows the last run.
* **Periodic, outside the app:** schedule the `--if-changed` command with cron / launchd (macOS) or
  Task Scheduler (Windows).
* The database address comes from `DATABASE_URL_READONLY` or `DATABASE_URL` (environment or `.env.local`).

## What is computed

* KPIs, charges by month / vendor / category, each vendor's split by category, freight per kg.
* **Findings**, each with the invoices behind it:
  duplicate invoice numbers and possible double billing; charges far from what peers pay (robust z-score);
  invoices with an unusual mix of charges (Isolation Forest); exchange rates out of line with invoices of
  the same period; a vendor's typical amount moving 20%+ month over month; vendor names that look like
  one company; missing data.
* **Forecast** of the next three months (Holt linear trend) with an 80% range and a back-test error.
* Data completeness per field, review backlog, routing of the latest runs.

Each method states its minimum data and reports "not enough data" below it (for example the forecast needs
six months with invoices and 30 invoices; outlier checks need 12 invoices with that charge). The exact
rules are printed in the dashboard ("How each figure is produced") and in the workbook's About sheet.

## Traceability

Every run adds an entry to the audit log: the SHA-256 fingerprint of the rows analysed, the period,
the findings count and the SHA-256 of each output file. A report can therefore be tied to the exact data
it was built from.

Amounts are INR before GST, as stored. `reports/analytics_demo/` holds a demonstration run on 2,500
synthetic invoices (a throwaway local database, never Neon) to show the charts at volume.

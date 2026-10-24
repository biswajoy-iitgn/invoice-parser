# Auditability

What is recorded for every invoice, and how to check it.

| Question | Where the answer is |
|---|---|
| Which file did this come from? | `documents`: file name, size, pages, **SHA-256 of the file** |
| What produced the values? | `extraction_runs` (one row per run, never overwritten): pipeline version, duration, routing, warnings, the reader's raw output, and a **model configuration** naming every model and rule file by SHA-256 (`models`, `model_versions`, `model_configurations`) |
| Where on the page was each value read? | `extraction_field_results`: value, text on the page, page number, bounding box, confidence |
| Why was a charge put in that category? | the run's `final_output_json.line_items`: reader's reading, final category, confidence and which layers decided (alias, rule, encoder, LAYA, reader) |
| What happened afterwards? | `audit_logs`: CREATE / REPROCESS (each extraction stored), REVIEW (category corrections made in the review screen, with reviewer), EXPORT (every database-to-Excel export: who, when, rows, file fingerprint), and analytics runs |

## The audit log cannot be quietly changed

Migration `006_analytics_audit.sql` makes `audit_logs` append-only and hash-chained:

* UPDATE, DELETE and TRUNCATE are rejected by triggers.
* The database, not the caller, assigns each entry's position, timestamp and digest. The digest is the
  SHA-256 of the entry's content plus the previous entry's digest, so changing, removing or re-ordering
  any past entry breaks every digest after it.
* `SELECT * FROM audit_chain_verify()` recomputes the chain; `first_bad_seq` is NULL when intact.
  The app shows the same at `/audit` and `GET /api/audit/verify`.

Limits to know: a database owner can disable the triggers and edit a row, but the chain then fails
verification from that row on (covered by a test). An organization that has audit entries cannot be
deleted (the foreign key would have to edit those entries).

## Looking up one invoice

* Browser: `/audit` in the app, enter the invoice number.
* API: `GET /api/audit/invoice/<invoice number>`.

The trail shows the source file and its hash, every extraction run with its model configuration, where
each current value was read (page and box), and the audit-log entries for the invoice. In the review
screen (`/`), the same boxes are drawn on the page image.

## Reproducing a result

The model configuration name (for example `invoice-v2-34e1ceab`) is derived from the SHA-256 of the
reader base model, the fine-tuned adapter, the semantic encoder, LAYA, the category rules, the fusion
weights and the learned aliases. Changing any of them gives a new name, and cached results are not reused
across names (`invoice_extractor/batch.py: result_tag`).

## Evidence images (added 2026-10-04)

Every invoice the app processes gets one image per page that carries anything that was read:

- the 31 output values have a red (invoice details) or blue (charge amount) box and a small number; a panel beside
  the page lists number, field, value and confidence;
- every other printed item that was captured (see *Full capture* below) has a thin green box and is listed in the
  same panel with its label;
- a caption strip names the invoice, the route (AUTO_ACCEPT / REVIEW), the time and the pipeline revision.

Where they live:

| What | Where |
|---|---|
| image files | `EVIDENCE_DIR` (default `data/evidence/<invoice id>/<extraction run id>/page-N.jpg`; ignored by git) |
| one row per image | table `invoice_evidence`: page, path below `EVIDENCE_DIR`, SHA-256 of the file, size, number of boxes |
| link | `invoices.evidence_url` → column `evidence_url` (last column) of `reporting.invoice_export` → **Open** in Excel |
| audit log | the CREATE / REPROCESS entry of the invoice lists each page's SHA-256 and the number of captured items |

The app serves `GET /evidence/<invoice id>` (values, review reasons, all pages, the full list of captured items) and
`GET /evidence/<invoice id>/page/<n>.jpg`. Each time an image is served its file is hashed again and compared with
the hash stored when it was made; the page says "matches the recorded hash" or "DIFFERS", and the image response
carries `X-Evidence-Intact`. Re-processing an invoice writes a new set of images for the new run and keeps the old
ones on disk; the link always shows the current run.

`PUBLIC_BASE_URL` is the address written into the link (default `http://127.0.0.1:<port>`): set it to the address
other computers use for the app. `EVIDENCE_IMAGES=0` turns the images off; `EVIDENCE_MAX_PAGES` (default 20) caps a
long attachment.

The image files are not in the database (about 150–450 KB per page). They are part of what has to be backed up
together with the database; a missing file shows as "missing on this server" on the evidence page.

## Full capture (added 2026-10-04)

The 31 fields are the business output. Besides them, everything printed on the document is stored, one row per item,
in the table `invoice_facts` (view `reporting.invoice_details` for Excel and reports):

| column | meaning |
|---|---|
| `kind` | `pair` (a printed label and its value), `table` (one cell, `label` = its column title), `block` (a heading and the lines under it), `text` (anything else) |
| `section` | the heading above the item, or which table row it belongs to |
| `label`, `value` | as printed |
| `value_type` | gstin, pan, irn, email, ifsc, phone, amount, percent, date, number, container, text |
| `output_field` | which of the 31 fields this item is the source of, if any |
| `page_number`, `bbox` | where it is on the page |

How it is made:

1. **Document parser — PaddleOCR-VL 1.6** (Apache-2.0; `invoice_extractor/vl_client.py`, `vl_worker.py`). The page
   is read as blocks in reading order: text, titles, tables, page header and footer, stamps, pictures. A paragraph
   stays in one piece (no break at every printed line), a table comes back as rows and cells, and text that is part
   of a picture (a logo, a stamp) is read too. This is the same model as in PaddleOCR's own demo.
2. **Blocks to items** (`facts.facts_from_blocks`): a table with a title row gives one item per cell with its column
   title; a form drawn as a table ("Document No. / 7044012289") gives label / value pairs; "label: value" inside a
   paragraph gives pairs; everything else is one item per block.
3. **Boxes:** an item's box is measured on the text layer (or the OCR words of a scan) when its text is found there
   inside the block, which is the usual case; otherwise it is the block's box.

On this Mac the parser runs on CPU, about 90 s per page (results are cached per page image, so a page is parsed
once); on a GPU server it takes a second or two. `DOC_PARSER=off` switches it off: the items then come from layout
rules only (columns that line up, gaps, colons), which break paragraphs at every printed line, mislabel irregular
layouts and do not read text inside pictures — the state the app was in before this change.

Not measured: how often an item's label is right. On the Dachser page every form field, the charge table and the
bank details came out correctly labelled; that is one page, read by eye.

## Arranged details — the organizer (added 2026-10-04)

The captured items are in page order and carry whatever label the vendor printed. A language model (the base
Qwen3-VL-8B, without the reader's adapter) arranges them under 53 named details in seven groups — Vendor, Customer,
Invoice, Shipment, Totals, Payment, E-invoice (`invoice_extractor/organizer.py`, list `DETAILS`).

What keeps it grounded:

- the model is given only the extracted items, grouped by the printed line they came from, and returns
  `{detail, value, line}`;
- a value is stored only if it is text inside an extracted item (the cited line first, then its neighbours, then
  any item). Otherwise it is dropped and listed under `dropped` in the run's record. The stored page and box are the
  extracted item's;
- each detail has a type check (GSTIN, PAN, IFSC, IRN, e-mail, CIN, date, amount, reference number). A value that
  fails is kept, marked `valid = false`, shown as "check failed", and left out of the one-row-per-invoice view.

Stored per detail (table `invoice_attributes`): group, detail, value as printed, normalized form, type, check
result, page, box, the ids of the extracted items it came from, their printed label, how it was matched, and the
model. Views: `reporting.invoice_attributes` (one row per detail) and `reporting.invoice_attributes_wide` (one row
per invoice, one column per detail) — Excel sheets *Details* and *More fields*.

**What it does not guarantee:** that the model filed a value under the right detail. The value is always printed
on the invoice; the choice of detail is the model's. On the two real invoices read line by line, about nine in ten
choices were right; typical mistakes are a wrong party (shipper filed as customer), "PO" from an address taken as
a PO number (caught by the type check), or a tax-table figure filed as the taxable total. This has not been
measured on a labelled set. Cost: one more model call, 45–75 s per invoice on this Mac (`ORGANIZE=0` turns it off).

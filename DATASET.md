# DATASET.md — InvoiceLab v2 corpus (built 2026-10-03)

The training corpus is rendered by **InvoiceLab Studio** (`../Table_Parsing/invoice-maker.html`),
driven headlessly through its agent API. It replaces the earlier Pillow-rendered `dataset/`
(5 layouts, 27 charge strings, 9 label strings), which taught models to memorize one template.

## How the app generates variation (freight v2 engine inside `invoice-maker.html`)

| Axis | What varies |
|---|---|
| Layout | 72 **families** = header (7) × meta block (6: key-value columns, label-over-value grid, boxed rows, title bar, right box, plain runs) × parties (4) × shipment refs (6: list, grid, goods sentence, remarks line, marks-and-numbers table, merged) × charge table (10: code/HSN/base, GST split, charge+tax, units+GST, CCY+ex-rate, wide multi-currency landscape, assessable, monospace decomposition, simple, qty/rate) × skin (6) × totals (4, incl. HSN/SAC tax summaries) × footer (4) × typography (16 font stacks, size, case, colon style, accent) |
| Vendors | 300 synthetic forwarders, each bound to one family, with its own logo, address/GSTIN, invoice-numbering scheme and date format |
| Labels | 295 surface forms for the target fields + decoys (`Inv. Dt.`, `H B/L No.`, `Cntr Nos`, `Gr. Wt.`, `M B/L No. / MAWB No.` ...) |
| Charges | 370 aliases over the 22 charge categories + context-resolved wording (`Freight` = sea or air; `Security Manifest` = ENS for EU, AMS for US/CA), qualifiers (`FCL`, `POL`, `- 40HC`), generic titles with sub-descriptions (`OTHER CHARGES` / `THC`), reference codes, GST-% tags → **19,992 distinct printed descriptions** |
| Shipment | sea FCL/LCL (multi-container) and air (MAWB with valid mod-7 check digits, HAWB, chargeable weight); freight / local CHA / destination invoice kinds |
| Money | INR/USD/EUR lines with per-invoice exchange rates printed as columns, sub-lines, inline `@ rate`, footer lines or header fields; Indian/Western/plain number formats; CGST+SGST or IGST |
| Pages | 20% multi-page: continuation with optional B/F rows, e-invoice IRN page, attachment page with per-lot HBLs/seals (decoys) |
| Decoys | seal no, net weight, party/shipper invoice no, IRN, ack, job/customer no, SB no/date, master B/L next to house B/L, tax summaries, B/F rows, stamps/watermarks |
| Abstention | fields omitted at realistic rates (GT `""`): container 44%, B/L 42%, weight 33%, MAWB/HAWB on most sea |

Container numbers carry valid ISO 6346 check digits. Every printed element is tagged
(`data-k="<field>:key|value"`), so word boxes and field roles are **measured from the DOM**.

## Splits and overfitting controls

* **Layout holdout**: families 1–50 train, 51–61 val, 62–72 test; every component option still appears in every split, so val/test measure unseen *combinations*.
* **Vendor holdout**: vendors (names, logos, numbering) are bound to families, so they never cross splits.
* **Wording holdout**: each label/charge alias pool is split train/val/test by hash rank (the core form is always train); val/test documents draw ~50% of their wording from their own unseen bucket. Measured: 49–50% of val/test charge lines and 48% of labels use wording never seen in training.
* **Real-invoice block**: entities of the real benchmark invoices (vendor names, GSTINs, invoice/B-L/container numbers, numbering formats) are blocked from generation and audited.

`scripts/check_leakage.py` result (`dataset_v2/leakage_report.json`): 0 families / vendors / page hashes crossing splits, 0 near-duplicates (max layout-skeleton Jaccard vs train 0.375; mean between random train pairs 0.049), 0 real-entity hits.

## What is on disk (`dataset_v2/`, gitignored)

| Item | Count |
|---|---|
| Invoices (train/val/test) | 15,000 (12,000 / 1,500 / 1,500), 18,749 pages, PNG at ~198 DPI |
| DOM annotations `ann/<id>.json` | every word: text, box, field role, line-item index |
| Text-layer PDFs `pdf/` (Chromium print) | 7,820 (all val/test + fixed 40% of train) |
| Scan-degraded copies `scans/` | 10,209 (60% of train, all val/test); image-only PDFs for val/test; recipes: flatbed 4,056 · office copier 2,585 · phone 1,526 · fax 1,049 · old photocopy 993; 100–300 DPI |

`scripts/check_invoicelab_v2.py` verifies, for all 15,000 documents, that every ground-truth
value, line-item description, amount and exchange rate is printed exactly where its tags say
(result: 0 failures).

## Derived training sets

| Set | Builder | Content |
|---|---|---|
| Semantic items | `scripts/build_semantic_items.py` | 366k train items (labels + charge rows + non-charge rows, 30% OCR-noised), val/test with seen/unseen buckets |
| LAYA decisions | `scripts/build_laya_v2_items.py` | 20,104 train (capped per alias), 6,000 val/test |
| Reader examples | `scripts/build_reader_v2.py` | line-numbered layout text from the **production ingest code** (PDF text layer; PP-OCRv6 on scans) → cited JSON; targets only contain values readable in that input |
| SR pairs | `training/train_swinir.py` | HR train pages; LR synthesized on the fly |

## Real benchmark (held out, gitignored with `sample invoices/`)

`eval/real_invoices/gold.json`: the 7 unique real invoices (47 charge lines), hand-labelled
with documented conventions and acceptable alternatives for genuinely ambiguous lines.
`scripts/make_real_scans.py` adds 14 image-only scanned copies (office copier + phone).
Never used for training, alias banks, prompts or tuning.

## Reproduce

```sh
cd ../Table_Parsing && python3 -m http.server 8901 &   # or let the driver start it
.venv/bin/python scripts/build_invoicelab_v2.py --train 12000 --val 1500 --test 1500 --workers 8 --out dataset_v2
.venv/bin/python scripts/check_invoicelab_v2.py dataset_v2
.venv/bin/python scripts/check_leakage.py dataset_v2 --output dataset_v2/leakage_report.json
.venv/bin/python scripts/build_invoicelab_v2.py --pdf --train 12000 --val 1500 --test 1500 --out dataset_v2
.venv/bin/python scripts/make_scans.py dataset_v2 --fraction-train 0.6
```

Generation is deterministic per chunk seed (`build_invoicelab_v2.py: SEED0`). On Linux the
app's CSS fonts map to open fonts (Liberation, Nimbus, C059, P052, URW Gothic, Lato, Roboto ...;
see `scripts/remote_stage.sh fonts`); records and labels are identical, glyph metrics differ.

# invoice-parser

Freight-forwarding invoice extractor: PDF or image in, a fixed 31-field JSON out. Built for mostly scanned invoices. Runs on CPU. Results and method: [EVALUATION.md](EVALUATION.md). Model survey: [research.md](research.md).

## Install

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
brew install tesseract            # Linux: apt install tesseract-ocr wamerican
```

## Run

```bash
python -m invoice_extractor path/to/invoices/ -o output/
```

For every input it writes `output/<name>.json` (the 31 fields) and `output/<name>.audit.json` (per-field confidence and evidence, line items with their category and arithmetic checks, subtotal reconciliation, warnings). It also writes `output/all_invoices.json`.

| Option | Choices |
|---|---|
| `--ocr` | `ensemble` (default: Tesseract x2 + PP-OCRv4 with verification), `tesseract`, `tesseract-raw`, `rapidocr`, `hunyuan` (llama.cpp, feeds `--backend llm` only) |
| `--backend` | `layout` (default, deterministic, copies values from the page), `layout+llm` (LLM classifies only charge lines the lexicon cannot place), `llm` (research.md path: page text to Qwen3-VL via Ollama) |

## Output contract

Exactly these keys, in this order. Text fields are strings. Charge fields and Ex Rate are numbers. A field that is not on the invoice is `""`.

`Invoice No`, `Invoice Date` (YYYY-MM-DD), `Container Number` (comma separated), `MAWB No`, `HAWB No`, `Vendor Name`, `Bill of Lading / HBL` (house BL, else master), `Weight` (gross, "1234.5 KG"), `Sea Freight`, `Air Freight`, `Terminal Handling Charges`, `Airway Bill Charges`, `BL Charges`, `ENS Charges`, `Seal Charges`, `VGM`, `EDI Fees`, `DAP Charges`, `Other Charges`, `AMS Charges`, `Handling Charges`, `Pre Carriage Haulage`, `Ex Rate`, `Misc`, `Transportation`, `Warehouse Charges Port`, `Examination Charges`, `Fuel Charges`, `Cargo Screening`, `Agency Charges`, `Shipping Bill Charges`.

Charge fields hold the INR amount before GST (taxable plus non-taxable value), summed over every invoice line whose meaning maps to that field. Foreign-currency lines use the INR amount printed on the invoice. The charge categories, their meanings and aliases are in `invoice_extractor/schema.py`.

## Synthetic benchmark

`invoice-maker.html` now includes a "Freight Forwarding Invoice (31 fields)" type: 8 templates, sea/air/clearance modes, label and charge-wording synonyms, and 31-field ground truth (Ground truth tab, format "31-field freight output").

```bash
python dataset/build_dataset.py                                        # 150 invoices, seeded
python -m invoice_extractor.evaluate --split test --run my_run          # metrics + summary
python -m pytest -q tests
```

## Layout

```
invoice_extractor/   ingest.py (PDF text layer / OCR readings), ocr.py (preprocessing, engines),
                     layout.py (rows, cells), header.py (label/value fields, vendor),
                     tables.py (charges table), semantic.py (label + charge meaning),
                     validators.py, schema.py (output contract), extract.py, llm.py, evaluate.py
dataset/             build_dataset.py, invoices/, ground_truth/, gold_text/, manifest.jsonl
evaluation/          results/ (metrics per run), charts/, make_charts.py
tests/               unit and end-to-end tests
```

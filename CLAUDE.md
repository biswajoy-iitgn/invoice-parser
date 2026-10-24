# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
cd /Users/dtadmin/Desktop/DevelopmentProjects/doc_parsing
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests   # 148 tests (9 skipped), must be green
.venv/bin/python -m unittest discover -s tests -p "test_service.py"  # single file
```

No linter/formatter config in repo (no pyproject/setup.cfg/pytest.ini); unittest is the check.
`tests/` is not a package — the dotted form (`-m unittest tests.test_x`) fails; use `discover -s tests -p`.

Live database tests need a scratch cluster (see `tests/test_database.py` header):
```sh
initdb -D /tmp/pgtest-pgdata -U postgres --auth=trust
mkdir -p /tmp/pgtest-sock && pg_ctl -D /tmp/pgtest-pgdata -o "-k /tmp/pgtest-sock -p 55433" -l /tmp/pgtest.log start
TEST_DATABASE_URL="postgres://postgres@127.0.0.1:55433/postgres?host=/tmp/pgtest-sock" \
    .venv/bin/python -m unittest discover -s tests -p "test_database.py"
```

Inference (v2 `pipeline.py` is current; `extract.py` is legacy kept for A/B):
```sh
.venv/bin/python -m invoice_extractor INPUT.pdf --out-dir output
.venv/bin/python scripts/run_eval.py --split val --count 20 --variant orig --output work/pred.jsonl
.venv/bin/python scripts/evaluate.py dataset/val.jsonl work/pred.jsonl
.venv/bin/python eval_sample.py [--engine mineru] [--files DACHSER]
PORT=8001 .venv/bin/python scripts/run_app.py   # blessed launcher: loads .env.local, ensures reader/OCR
```

Dataset / training (corpus is gitignored `dataset_v2/`; see DATASET.md):
```sh
.venv/bin/python scripts/build_invoicelab_v2.py --train 12000 --val 1500 --test 1500 --workers 8 --out dataset_v2
.venv/bin/python scripts/build_reader_v2.py dataset_v2 --out work/reader_v2_digital
.venv/bin/python training/train_laya.py --train-items work/laya_v2/train.jsonl --val-items work/laya_v2/val.jsonl --criteria work/laya_v2/criteria.json --output checkpoints/laya-invoice-v2 --epochs 3 --batch-size 16
.venv/bin/python training/train_swinir.py --hr-list work/sr_v2/train_hr.txt --val-list work/sr_v2/val_hr.txt --swinir-ref work/SwinIR --output checkpoints/swinir-doc-x2
```

Key docs: DESIGN.md (execution path + evidence), INFERENCE.md, EVALUATION.md (test runs once, frozen), TRAINING.md (all licenses commercial-safe), DATASET.md (InvoiceLab v2 corpus), reports/benchmark.md (TARGET|ACTUAL|STATUS is truth).

## Architecture

Two pipelines share the 31-key schema in `invoice_extractor/schema.py` (`OUTPUT_FIELDS`, `CHARGE_FIELDS`, order matters):

- `invoice_extractor/pipeline.py:extract_invoice` (v2, current): `load_pages` (PDF text-layer words with exact boxes, else PP-OCRv6 via `ppocr_client`; scanned pages render sequentially then OCR concurrently through the worker pool) → `layout.serialize` (one line-numbered column-aligned text) → `call_reader` (fine-tuned Qwen3-VL-8B-Instruct + LoRA, contract in `reader.py` shared verbatim with `scripts/build_reader_v2.py`) → `assemble` (cite every value to measured boxes via `locate`/`cite`; blank unprinted values, never guess) → `semantic.engine().classify` (alias-bank + bge-small embedding + LAYA cross-encoder + lexicon + reader vote) → checks (line-sum vs subtotal, ISO 6346 container, MAWB mod-7, dates) → REVIEW routing (auto-accept disabled, `configs/critical_fields.json`).
- `invoice_extractor/extract.py:extract_invoice` (legacy): Docling/HunyuanOCR ingest + Ollama Qwen3-VL + `validation.ground_fields`; used by `cli.py` and `service.py` legacy/v2 engine flag. HunyuanOCR/OmniSR/LayoutLMv3 removed from path for licensing (TRAINING.md); SwinIR replaces OmniSR, PP-OCRv6 replaces HunyuanOCR. The old `sr.py` OmniSR router is deleted; SR config in `quality.py` is mtime-cached.
- Sheet engines (`mineru_engine.py`, `paddle_engine.py`, `docling_engine.py`) map parsed docs straight to sheet columns; `service.py` selects via `INVOICE_ENGINE` (default `mineru`). `grounding.cite_fields` opens each PDF once per page, not per value.
- `service.py` is the FastAPI app. Pages: `/` (extract), `/settings` (database setup + engine updates), `/database` (table browser: schema, row preview, relationship diagram), `/audit` (hash-chain trail), `/analytics`, `/evidence/{id}`. APIs: `/extract`, `/api/export`, `/api/batches*`, `/api/semantic/feedback`, `/api/schema`, `/api/db/status|tables|tables/{name}|schema|test|configure` (passwords never echoed; table names quoted via `psycopg.sql`), `/api/updates|updates/refresh|updates/apply|updates/auto` (PyPI checks cached 6h; apply refuses while a batch runs; auto-update runs at next `run_app.py` start), `/health|/ready|/metrics`. `batch.py` (ThreadPoolExecutor) handles multi-file jobs; `excel/` batch export; `ui/` is vanilla JS (one IIFE per page, no framework — check with `node --check`).

Database (`database/`, 18 tables): `db.py` Store (tenant-scoped via `app.current_org`), `persist.py` (one transaction per invoice), `audit.py` (hash-chained trail). Migrations are append-only numbered files, never edited after apply; seeds pin `field_definitions` (31) and `charge_types` (22). `audit_logs` is append-only/tamper-evident (UPDATE/DELETE/TRUNCATE raise). `reporting.invoice_export` is the frozen Excel/ODBC column contract — `ex_rate` comes from the run's stored fields JSON. Dropping a table needs constraint/policy/view handling first (see `010_trim_dead_schema.sql`).

Env switches (defaults are production): `READER_BACKEND=llamacpp|ollama|transformers`, `LLAMACPP_HOST`, `READER_MODEL=invoice-reader-qwen3vl8b`, `SEMANTIC_ACCEPT=0.90`, `OCR_TRUST=0.95`, `AUTO_ACCEPT=1` (still REVIEW unless all checks pass), `PPOCR_WORKERS=2` (page OCR concurrency), `INVOICE_BATCH_WORKERS=4`, `ENGINE_AUTO_UPDATE=0`, `DOC_PARSER=off` + `ORGANIZE=0` in `tests/conftest.py` (tests skip model servers). SR gating `USE_OMNISR/FORCE_OMNISR/BYPASS_OMNISR` stays OFF (bicubic beats SR downstream). `PIPELINE_REV` in `pipeline.py` bumps when logic changes (cache key).

Splits enforce layout-family + vendor + wording holdout (`scripts/check_leakage.py`); `dataset/test.jsonl` and `eval/real_invoices/gold.json` are run-once, never tune on them. `PIPELINE_REV`-keyed caches, `checkpoints/` (atomic, ≤85 min/invocation, `--resume latest.pt`), GPU stages via `scripts/jl_stage.sh`.

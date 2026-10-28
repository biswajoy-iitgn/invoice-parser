# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```sh
cd /Users/dtadmin/Desktop/DevelopmentProjects/doc_parsing
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests   # 162 tests (9 skipped), must be green; no model loads (MODEL_POOL=off in the API tests)
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

Run (one pipeline: profiles; the Fluent UI is the only UI):
```sh
.venv/bin/python -m invoice_extractor DOC.pdf [FOLDER ...] --out-dir output          # --profile ID, default auto (the router picks)
PORT=8001 .venv/bin/python scripts/run_app.py   # blessed launcher: .env.local, UI build, NuExtract3 copy, uvicorn; models start on demand
.venv/bin/python -m invoice_extractor.auth create-admin EMAIL "NAME"   # first administrator (sign-in is on by default)
```

Dataset / training (corpus is gitignored `dataset_v2/`; see DATASET.md):
```sh
.venv/bin/python scripts/build_invoicelab_v2.py --train 12000 --val 1500 --test 1500 --workers 8 --out dataset_v2
.venv/bin/python scripts/build_reader_v2.py dataset_v2 --out work/reader_v2_digital
.venv/bin/python training/train_laya.py --train-items work/laya_v2/train.jsonl --val-items work/laya_v2/val.jsonl --criteria work/laya_v2/criteria.json --output checkpoints/laya-invoice-v2 --epochs 3 --batch-size 16
.venv/bin/python training/train_swinir.py --hr-list work/sr_v2/train_hr.txt --val-list work/sr_v2/val_hr.txt --swinir-ref work/SwinIR --output checkpoints/swinir-doc-x2
```

Document profiles (one YAML per document type in `configs/profiles/`, `PROFILES_DIR` overrides):
```sh
.venv/bin/python -m invoice_extractor --list-profiles
.venv/bin/python -m invoice_extractor DOC.pdf --profile gate_pass --out-dir output
```

Key docs: reports/reader_bakeoff.md and reports/parser_bakeoff.md (why each model is used), DESIGN.md (execution path + evidence), INFERENCE.md, EVALUATION.md (test runs once, frozen), TRAINING.md (all licenses commercial-safe), DATASET.md (InvoiceLab v2 corpus), reports/benchmark.md (TARGET|ACTUAL|STATUS is truth).

## Architecture

One pipeline, `invoice_extractor/profiles.py`, read through `profiles.extract(path, profile)`; the classic page, `extract.py`, the sheet engines (MinerU, Docling, PP-StructureV3/PP-DocLayout) and their scripts were decommissioned (in git history).

- Pages (`pipeline.load_pages`): PDF text-layer words with exact boxes, else PP-OCRv6 via `ppocr_client` (SwinIR x2 for low-resolution images) → `layout.serialize` (one line-numbered, column-aligned text).
- Routing (`router.py`, no model): profile "auto" scores each profile's `detect:` phrases and field labels (unclear → `general`); then only the models that profile needs: its reader (default Qwen3-VL-4B; roster uses NuExtract3; a reader not on disk falls back to the default; choices backed by reports/reader_bakeoff.md) and, for `general` only, PaddleOCR-VL-1.6 when the page looks tabular. `result["route"]` says what was chosen and why.
- Models (`model_pool.py`): one `mlx_vlm.server` per model, started on first use, LRU-stopped to stay under `MODEL_MEMORY_GB` (default 40% of RAM), stopped after `MODEL_IDLE_SECONDS`; `pipeline.chat()` is the one reader call (reading and image re-reads). PaddleOCR-VL runs layout (PP-DocLayoutV3) on CPU in `vl_worker.py` and the VLM on the pool's GPU server.
- Engines: `generic` (every profile with fields/tables, incl. `logistics_invoice`): the profile's fields/tables/checks become the reader prompt + JSON schema → `assemble` cites every value to measured boxes (`locate`/`cite`), keeps printed characters over the model's rewrite, validates identifiers, runs checks. `general`: every labelled value, every table (PaddleOCR-VL regions, cells checked inside their own table), full text. Values under `PROFILE_OCR_TRUST` (0.97) OCR confidence are re-read from a 300 dpi crop by the default reader (Qwen3-VL-4B, whatever the profile reads with); disagreement shows both. Anything missing/unverified/malformed/failing a check → REVIEW with reasons.
- Checks (`checks.py`, a profile's `checks:` + `configs/checks.yaml`): sum, total_row, row_formula, field_formula, range, compare (numbers/dates), same, unique, complete, pattern, date_window; identifiers (`identifiers.py`): GSTIN, PAN, IFSC, mobile, PIN, vehicle, container, MAWB, IRN, HSN, date.
- `pipeline.extract_invoice` (31-key `schema.py` contract, semantic engine, facts/organizer) is no longer reachable from the product; it remains for `database/persist.py` and offline research scripts.
- `service.py` (FastAPI): `/` serves the Fluent UI build (`UI/dist`, rebuilt by `run_app.py` when sources change); old `/settings`, `/database`, `/audit` redirect into it. APIs: `/api/auth/*`, `/api/users*`, `/api/profiles` (+ `/models`, `/extract`), `/api/models`, `/api/batches*` (profile per batch, default auto; `batch.py`: atomic `items.json`, bounded result cache, results reused only for the same file + profile revision), `/api/db/*` (passwords never echoed; table names quoted via `psycopg.sql`), `/api/audit/verify|entries`, `/api/updates*` (paddleocr/paddlex upgraded in `PPOCR_PYTHON`, mlx-vlm in the app venv), `/health|/ready|/metrics`. UI (`UI/`, Vite + Fluent, hash routes); `npm run profiles:export` refreshes the offline snapshot `UI/src/serverProfiles.json`.

Database (`database/`, 18 tables): `db.py` Store (tenant-scoped via `app.current_org`), `persist.py` (one transaction per invoice), `audit.py` (hash-chained trail). Migrations are append-only numbered files, never edited after apply; seeds pin `field_definitions` (31) and `charge_types` (22). `audit_logs` is append-only/tamper-evident (UPDATE/DELETE/TRUNCATE raise). `reporting.invoice_export` is the frozen Excel/ODBC column contract — `ex_rate` comes from the run's stored fields JSON. Dropping a table needs constraint/policy/view handling first (see `010_trim_dead_schema.sql`).

Security (`auth.py`): sign-in on by default (`AUTH=off` only for tests/demos, warned at start); local accounts in `work/auth.sqlite3` (scrypt, owner-only), session cookie HttpOnly/SameSite=Strict/Secure over HTTPS, lockout after 5 failures, per-IP sign-in limit; roles enforced in the middleware (`_access`: admin for /api/db, /api/updates, /api/users, /api/audit, profile model changes; `/metrics` for admins or loopback); temporary passwords must be changed. Also CSP/HSTS/Permissions-Policy headers, API docs off unless `API_DOCS=1`, `MAX_REQUEST_BYTES` body cap. TLS and the public name go on a reverse proxy (`TRUSTED_PROXY`, `COOKIE_SECURE=1`).

Env switches (defaults are production): `READER_BACKEND=pool|llamacpp` (pool default), `MODEL_MEMORY_GB`, `MODEL_IDLE_SECONDS=600`, `MODEL_POOL=off`, `READER_WARM=1`, `LLAMACPP_HOST`, `PROFILE_OCR_TRUST=0.97`, `PPOCR_WORKERS=2` (page OCR concurrency), `INVOICE_BATCH_WORKERS=2` (run_app), `INVOICE_MAX_PARALLEL` (documents read at once across singles and batches, default = batch workers; the rest queue, see `invoice_extractions_waiting` in /metrics), `INVOICE_BATCH_RESULTS_CACHED=64` (results in RAM; the rest are read from `work/batches`), `ENGINE_AUTO_UPDATE=0`, `ALLOWED_HOSTS=127.0.0.1,localhost,::1,testserver` (other Host headers and cross-origin writes get 403; `*` disables), API tests set `AUTH=off`, `MODEL_POOL=off`, `DOC_PARSER=off` at the top of each file (unittest doesn't read `tests/conftest.py`). `PIPELINE_REV` in `pipeline.py` bumps when logic changes (cache key).

Splits enforce layout-family + vendor + wording holdout (`scripts/check_leakage.py`); `dataset/test.jsonl` and `eval/real_invoices/gold.json` are run-once, never tune on them. `PIPELINE_REV`-keyed caches, `checkpoints/` (atomic, ≤85 min/invocation, `--resume latest.pt`), GPU stages via `scripts/jl_stage.sh`.

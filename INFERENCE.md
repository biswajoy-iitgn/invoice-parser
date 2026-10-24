# INFERENCE.md — one-command extraction

```sh
.venv/bin/python -m invoice_extractor INPUT.pdf --out-dir output
```

- Inputs: PDF, PNG, JPG/JPEG/TIFF/BMP/WEBP, or folders of them.
- Outputs per document: `<stem>.json` (exact 31-key schema, `""` for absent
  fields) + `<stem>.audit.json` (line items, evidence route, warnings, timing)
  + `all_invoices.json` (combined).
- Flags: `--model` (Ollama VLM, default `qwen3-vl:8b-instruct`), `--force-ocr`
  (OCR every page), `--no-audit` (skip sidecars).
- SR gating env: `USE_OMNISR=1` (+ `OMNISR_COMMAND`, default off),
  `FORCE_OMNISR=1`, `BYPASS_OMNISR=1`, `SR_CONFIG=configs/super_resolution.yaml`.
- Routing: every invoice currently routes to REVIEW (`needs_review: true`);
  auto-accept is disabled pending a calibrated confidence model.

Security: inputs are untrusted documents — the pipeline renders text/OCR only
and never executes embedded scripts, macros, or HTML/JS. Filenames are
sanitized to their output stems.

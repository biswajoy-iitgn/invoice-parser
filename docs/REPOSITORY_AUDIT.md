# REPOSITORY AUDIT (2026-10-02)

Target spec: HunyuanOCR + fine-tuned LAYA + pluggable SR + Neon PostgreSQL + ODBC/Excel.
Rule: reuse what exists; only build what is missing.

## 1. Existing architecture

```
PDF/image -> ingest.py (INVOICE_INGEST=auto: PP-StructureV3 [+gated VL] |
                         =legacy: Docling text-layer + HunyuanOCR GGUF scans)
             + quality.py gate -> optional OmniSR enhance
          -> extract.py: Qwen3-VL 8B via Ollama (JSON-schema constrained)
             -> lexicon cross-check + LAYA semantic vote (laya_client.py)
             -> validation.py evidence guard (ground_fields) + reconcile
          -> cli.py (JSON + audit sidecars) | service.py (FastAPI /extract)
```

- Reader LLM is Qwen3-VL via Ollama (`DEFAULT_MODEL`), not HunyuanOCR-alone:
  HunyuanOCR transcribes pages; Qwen3-VL does schema-constrained reading.
- LAYA role (verified in code): Choice-over-ontology semantic classifier.
  One vote per line-item description; constrained to charge fields; used as
  `semantic-override` / agreement signal in `resolve_category`. It does NOT
  do entity extraction, label/value association, or OCR.
- research.md (10-model benchmark): HunyuanOCR Q8_0 is the accuracy champion
  (91.0% F1, 4.7% hallucination, only model to parse 13-col borderless
  tables). This supports the spec's HunyuanOCR-first choice.

## 2. Discovered components

| Component | Location | State |
|---|---|---|
| HunyuanOCR GGUF via llama-mtmd-cli | ingest.py:62-77, ~/models/HunyuanOCR-GGUF | Works (Mac Metal) |
| Docling layout/tables (legacy path) | ingest.py:83-99 | Works (CPU) |
| PP-StructureV3 + PP-OCRv6 (auto path) | paddle_backends.py | Validated on RTX6000 2026-10-02 |
| PaddleOCR-VL 1.6 native | paddle_backends._vl, INVOICE_VL=on | HANGS on Blackwell/cu129 (trace: work/vl_hang_trace.txt) |
| Qwen3-VL 8B reader | extract.py:137-156, ollama 6.1GB | Works (Mac) |
| Fine-tuned LAYA | checkpoints/laya-invoice/laya-invoice-ft, laya_client.py | 500/500 val Choice (2026-10-02) |
| OmniSR official x2 + doc fine-tune | quality.py, training/train_omnisr.py, checkpoints/omnisr | Works; bicubic control in evals |
| Quality gate | quality.py assess/maybe_super_resolve | Works (dim/sharp/contrast) |
| 31-field schema + charge lexicon | schema.py OUTPUT_FIELDS/CHARGE_CATEGORIES | Matches spec contract exactly (incl. "Handlign" typo) |
| Evidence guard + router | validation.py ground_fields/confidence_route | Works; auto-accept disabled (always REVIEW) |
| Field ontology (31 fields, aliases, negatives) | configs/field_ontology.json | Works |
| 5000-invoice dataset 3000/1000/1000 + 5000 SR pairs | dataset/, scripts/generate_dataset.py | Done, validated, family holdout |
| Public adapters (DocILE/FATURA/MIDD) | dataset_adapters/ | Code ready; sources token-gated/imageless -> corpus 100% synthetic |
| Eval battery + error taxonomy + visual errors | run_eval/evaluate/error_analysis/visual_errors/dual_verify/profile_latency | Done; frozen numbers in reports/benchmark.md |
| FastAPI service | service.py (/extract /health /ready /metrics) | Done, 9 tests |
| Docker + compose + runbook | docker/, docs/RUNBOOK.md | Written, image NOT built |
| Training + checkpoint/resume | training/train_laya.py, train_omnisr.py, checkpointing.py | Resume-tested (2-leg OmniSR run) |
| Handwriting leg (PP-OCRv6 crop engine) | handwriting.py, hw_engine() | 28/30 exact, CER 0.036 (RTX6000) |

## 3. Missing components (to build)

1. **Neon Postgres**: no database code, no migrations, no DATABASE_URL
   anywhere. Local postgres exists (homebrew psql/pg_ctl) for migration tests.
2. **Reporting view + ODBC/Excel**: nothing exists. Mac has iODBC libs and
   Excel.app; no unixODBC/psqlODBC verified.
3. **Canonical internal ontology codes** (`invoice_number`, ...): only
   external names exist; mapping + doc missing.
4. **Evidence candidate objects** (§8 shape): only aggregated audit exists.
5. **Hallucination metrics** (rate, unsupported fill, evidence coverage):
   partially in evals; not as specified per-field metrics.
6. **SRRouter abstraction**: only single-command OmniSR + bypass; no
   model registry/selection API.
7. **CLI/SR/pipeline flags** (§76-77), **model registry tables**,
   **review workflow**, **audit log**, **acceptance report**.
8. **HunyuanOCR fine-tune script**: does not exist (GGUF is used as-is;
   train_hunyuanocr.py missing). No GPU available (Jarvis outage).

## 4. Technical debt / conflicts

- Default ingest is `auto` (Paddle-required); spec says HunyuanOCR-first.
  Fix: default back to the HunyuanOCR path (one-line + tests).
- `extract.py` couples reading to Ollama/Qwen3-VL; spec pipeline names
  HunyuanOCR->LAYA. Keep Qwen3-VL (measured, works) but document it as the
  schema-constrained reader stage; do not remove.
- Paddle code stays (validated, RULE 7) but is not the default path.
- `ground_fields` returns strings; DB needs typed values — convert at
  persistence boundary (NUMERIC/DATE), not in the extractor.
- confidence_route thresholds are function defaults, not config — move to
  configs/routing.yaml (new).
- service.py `/extract` does not persist to DB — add persistence behind
  env flag (DB optional; service must run without Neon).

## 5. Risks

- Neon credentials unavailable -> live DB/ODBC/Excel tests BLOCKED; mitigate
  with local-postgres migration tests + SQL-reviewed view + documented manual
  Excel steps.
- No GPU (outage) -> no new training; LAYA/OmniSR checkpoints reused as-is.
- Corpus 100% synthetic (public sources gated) -> acceptance targets on
  real-world invoices cannot be claimed.
- Auto-accept path unproven (router forced REVIEW) -> STP targets NOT MET.

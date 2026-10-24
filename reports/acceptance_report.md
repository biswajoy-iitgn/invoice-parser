# Acceptance report (2026-10-02)

Pipeline under test: HunyuanOCR ingest (default) + Qwen3-VL 8B reader +
fine-tuned LAYA semantic vote + evidence guard. Corpus: 100% local
synthetic (public sources gated), single-page. Small-n caveats apply.

## Gate table

| Metric | Prototype Target | Production Target | Actual | Status |
|---|---:|---:|---:|---|
| Header exact match | >=97% | >=99% | val-20 critical 98.1%; per-field: see work/eval_val_orig.json | PARTIAL* |
| Critical field exact match | >=98% | >=99.5% | val-20 98.1% / test-20 91.9% / val-10 (hunyuan+laya) 97.5% | FAIL (test) |
| Line item accuracy (row-associated) | >=93% | >=97% | NOT MEASURED (no row-association scorer) | NOT MEASURED |
| Whole invoice accuracy | >=75% | >=90% | val-20 70% / test-20 35% / val-10 80% | FAIL |
| Auto-accepted critical correctness | N/A | >=99.9% | N/A (auto-accept disabled; all REVIEW) | NOT MEASURED |
| STP coverage | N/A | >=70% | 0% (router forced REVIEW) | FAIL |
| Throughput | >=0.2 p/s | >=1 p/s | 0.07 p/s (10 docs / 139 s, Mac) | FAIL |
| Median latency | <=10s | <=10s p95 | 13.9 s/doc mean (Mac, incl. OCR+LLM) | FAIL |
| JSON compliance | 100% | 100% | 100% (all runs) | PASS |
| Required-key presence | 100% | 100% | 100% | PASS |
| Unexpected-key rate | 0% | 0% | 0% | PASS |
| Hallucination rate | measure | measure | 0.0 (all runs) | PASS |
| Unsupported fill rate | measure | measure | 0.0 overall / 0.0 critical (val-10) | PASS |
| Evidence coverage | measure | measure | 1.0 (val-10) | PASS |
| Missing-field rate | measure | measure | 0.023 (val-10) | PASS |

\* Header EM as a single aggregate is not computed; critical-field EM
(98.1% val) is the closest measured proxy. Per-field P/R/F1/EM exist in
`field_metrics` of every eval JSON.

## Supporting measurements

- LAYA Choice: 500/500 val (fine-tuned) vs 489/500 deterministic.
- Handwriting (PP-OCRv6 crop engine, RTX6000): 28/30 exact, CER 0.036.
- PP-StructureV3 (RTX6000): works, 84.4 s cold / 2 pages.
- PaddleOCR-VL 1.6 native: BLOCKED (kernel hang; `INVOICE_VL` opt-in).
- DB: 4 migrations apply idempotently; RLS isolation verified; read-only
  role verified (SELECT ok, INSERT denied); reporting-view lookup 0.1 ms
  (EXPLAIN ANALYZE, tiny data); ODBC parameterised lookup verified live.
- Suite: 43 unit tests green (+5 DB / +2 ODBC gated-live green).

## Verdict: NOT PRODUCTION READY

Blockers:

1. Whole-invoice accuracy 35% test (target 90%) with a val→test gap.
2. Critical-field EM 91.9% test (target 99.5%).
3. STP 0% / auto-accept unproven — review queue is the only control.
4. Throughput/latency miss on reference hardware (0.07 p/s, 13.9 s/doc).
5. No Neon project/credentials — live DB, backup/restore test, and
   Excel click-through unproven (local-PG + ODBC-stack proofs only).
6. Line-item row-associated accuracy unmeasured (no scorer).
7. Corpus 100% synthetic single-page; no real-world validation.
8. VL-markdown leg blocked (native backend hang); S3-only default.
9. HunyuanOCR fine-tune script missing; no GPU available (outage).
10. Docker image unbuilt.

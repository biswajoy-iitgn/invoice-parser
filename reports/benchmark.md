# Benchmark report (2026-10-01, synthetic corpus v3, frozen pipeline)

Hardware: Apple M5 Max (local). GPU-host numbers do not exist yet.
Samples: val-20 / test-20 for end-to-end; n=8 for the SR ablation; n=5 for
profiling. Small-n caveats apply throughout. Nothing below is fabricated:
unmeasured metrics say NOT MEASURED.

## 1-4. Dataset

- 5,000 PNG invoices, 3000/1000/1000, disjoint template/vendor/layout/id/hash
  families (`validate_dataset.py`: 0 errors). 100% local synthetic (public mix
  NOT ACHIEVED: adapters ready, sources token-gated or image-less; §15).
- Degradation: DPI {50,72,96,120,150}, blur, JPEG {20,30,50,70}, rotation,
  skew labels, contrast. 5,000 LR/HR SR pairs (scales 2/3/4) with provenance.
- Renderer: Pillow modelling `invoice-maker.html` freight families; 5 layout
  families, 50 vendors, sea/air/mixed rows, HBL label variants, seal decoys,
  Ex Rate lines. Single-page only (multi-page NOT DONE).

## 5-8. Models and training

- OmniSR (official, 784,604 params x2) + DF2K x2 weights; 95-epoch document
  fine-tune on RTX PRO 6000 (2 resume legs, best val_l1 0.00113 @ epoch 23;
  `checkpoints/omnisr/best.pt`, `experiments/omnisr_gpu_20261001.json`).
- Semantic ranker head: val MSE 0.68->0.13; loses to deterministic matcher.
  The CLM semantic vote (server->deterministic) is integrated in the
  extraction loop with charge-field constraining (regression-tested).
- PP-StructureV3 / PaddleOCR-VL 1.5: adopted as the primary ingest path
  (`INVOICE_INGEST=auto|paddle|legacy`, fallback to Docling/Hunyuan), but
  BLOCKED on Blackwell: GPU "Unsupported architecture", CPU OneDNN bug, VL
  requires CUDA. PP-OCR (det+rec) validated on CPU (reads invoices; CER
  0.17-0.52 on 5 val docs, order-sensitive measure). Needs H100/A100/L4.
- CLM server: client integrated; vLLM 0.30 + contrastive-lm installed on the
  VM, but Qwen3-8B download (16 GB) outpaced the window — serve path unrun.

## 9-24. Metrics (TARGET | ACTUAL | STATUS)

| Metric | Target (prototype) | Actual | Status |
|---|---|---|---|
| Header-field exact match (val-20) | >=97% | 98.1% | ACHIEVED |
| Header-field exact match (test-20, frozen) | >=97% | 91.9% | NOT ACHIEVED |
| Critical-field exact match (val-20) | >=98% | 98.1% | ACHIEVED |
| Critical-field exact match (test-20) | >=98% | 91.9% | NOT ACHIEVED |
| Line-item cell accuracy + row assoc. | >=93% | — | NOT MEASURED |
| Full-invoice correct, pre-review (val) | >=75% | 70% | NOT ACHIEVED |
| Full-invoice correct, pre-review (test) | >=75% | 35% | NOT ACHIEVED |
| Hallucination rate (val/test) | minimize | 0.0 / 0.0 | MEASURED |
| Unsupported fill rate (val/test) | minimize | 0.0 / 0.0 | MEASURED |
| Schema compliance (val/test) | 100% | 1.0 / 1.0 | ACHIEVED |
| Auto-accept critical correctness | review-all proto | review-all (auto-accept off) | POLICY |
| Straight-through coverage | — (prod >=70%) | 0.0 (by policy) | MEASURED |
| Throughput, clean (pages/sec) | >=0.2 | 0.095 | NOT ACHIEVED |
| Median latency, clean | <=10 s | 10.4 s (p50), 11.4 s (max, n=5) | NOT ACHIEVED |
| Throughput, LR | — | 0.137 | MEASURED |
| Median latency, LR | — | 7.1 s | MEASURED |

Per-field tables: `work/eval_val_orig.json`, `work/eval_test_orig.json`.
Error analysis: `reports/error_analysis.{json,csv}` — val-20: 10
MISSING_FIELD + 1 WRONG_FIELD, 0 hallucinations. Visuals:
`reports/visual_errors/` (6 cases + LR/HR pairs).

## 24. OmniSR impact (same 8 val LR docs, critical-field EM)

| Path | crit EM |
|---|---|
| raw LR | 0.500 |
| bicubic to HR canvas | 0.609 |
| pretrained OmniSR x2 | 0.438 |
| fine-tuned OmniSR x2 (95 epochs) | 0.469 |

Pixels (12 val pairs): fine-tuned PSNR 26.16/SSIM 0.574 vs bicubic
25.39/0.476 vs pretrained 25.59/0.481 — decisive pixel win that does NOT
transfer downstream on n=8. Gated SR stays OFF; next is a text-aware
(OCR/perceptual) loss + larger-n downstream eval. Dual-path check
(`scripts/dual_verify.py`, 6 LR docs): 6/6 routed REVIEW on LR/SR
critical-field disagreement — conservative and correct per spec.

## 25. Ablations

- Evidence guard: naive-substring guard blanked all normalized values (date,
  amounts); format-aware guard keeps them with 0.0 hallucination on val+test.
- Semantic layer: deterministic ontology matcher 16/17 vs trained ranker 1/6
  on the charge-label probe -> deterministic in path, ranker out.
- GT aggregation: generator emitted colliding fields (last-wins) vs pipeline
  sums -> fixed by distinct-field sampling + regen (invoice EM 0.35->0.70 val).
- PP-StructureV3 / PaddleOCR-VL / CLM-server vs current stack: NOT RUN
  (GPU host required).

## 26-28. Limitations and future work

1. Val/test gap (0.70->0.35 invoice EM): test failure modes are HBL misses
   (incl. `BL No:` variants), Sea->Air confusion on mixed docs, and two
   whole-doc OCR collapses. Characterized only; test must not drive tuning.
2. Corpus is 100% synthetic, single-page, 5 families; public mix, multi-page,
   multilingual, and browser-authentic renders are open.
3. OmniSR fine-tune, Paddle/CLM-server validation, CER/WER, IoU/mAP, and
   table-cell metrics all need the GPU window.
4. Latency/throughput miss prototype targets on M5 Max (LLM 6.8 s + OCR
   3.7 s p50); vLLM + batched OCR on the GPU host is the remediation path.

---

# Addendum (2026-10-02, RTX PRO 6000 sm_120, paddlepaddle-gpu 3.2.1 cu129)

Production stack re-validated on Blackwell after the cu126 failure. All
numbers below are measured on the Jarvis RTX6000 host unless noted.

## Paddle stack

- PP-StructureV3: WORKS. `sample invoices/DSV.pdf` (2 pages): 233 regions,
  121 table cells, 127 rec texts p1 (top score 0.96), 84.4 s cold e2e
  incl. model load. Source label `pp-structurev3`.
- PP-OCRv6: WORKS. Engine resolves `PP-OCRv6_medium_det` +
  `PP-OCRv6_medium_rec`. Printed-invoice OCR via S3 path above.
- PaddleOCR-VL 1.6 native backend: BLOCKED (upstream kernel hang). First
  `predict()` spins forever in `paddle/nn/functional/input.py:336
  embedding` — 100% single-core CPU, 0% GPU, log frozen after
  `generation_config` load. Faulthandler trace: `work/vl_hang_trace.txt`.
  Production default is S3-only; VL is opt-in via `INVOICE_VL=on` for use
  with a fixed wheel or a `*-server` backend (vLLM/SGLang/FastDeploy),
  which PaddleOCRVL supports but is not deployed here.

## Handwriting leg (PP-OCRv6 rec, crop-tuned engine)

- 30 script-font annotation crops from val: 28/30 exact, CER 0.036,
  2.7 s total (0.09 s/crop). The 2 misses are one overlapping-box
  dataset artifact (two annotations rendered on top of each other in
  INV_SYN_003036), not a model failure: 28/28 on clean crops.
- Key fix: the full-page doc preprocessor (UVDoc unwarping) destroys
  small crops (0/30, CER 0.98 with it on). The leg uses a dedicated
  `hw_engine()` with orientation/unwarping/textline-orientation off
  (`invoice_extractor/paddle_backends.py`).

## Semantic layer (LAYA replaces CLM)

- Fine-tuned LAYA (`checkpoints/laya-invoice/laya-invoice-ft`, 7313
  updates from convaiinnovations/laya, ModernBERT-large encoder): 500/500
  Choice accuracy on val decisions (0.05 s/item, local CPU) vs 489/500
  (0.978) for the deterministic matcher. The 11 deterministic misses are
  all "Agency Charges SB ..." lines misclassified as Shipping bill
  charges; LAYA gets all 11 right (head-noun dominance over the embedded
  "SB" token). No exact train/val state leakage (0.0%); 14.9% line-level
  template overlap.
- Bug fixed: `laya_client` used model key `"invoice"`, which laya 0.3.23
  rejects — the fine-tuned checkpoint could never load. Now overrides the
  `english` slot with the local checkpoint path.
- Eval: `scripts/eval_laya.py --limit 500 --output work/laya_eval_500.json`.

## Production service

- FastAPI service (`invoice_extractor/service.py`): `POST /extract`
  (size/type/page-capped uploads, request IDs, JSON logs), `/health`,
  `/ready` (ingest mode + paddle + LAYA checkpoint + Ollama), `/metrics`
  (Prometheus). Dockerfile (CUDA 12.9 + cu129 Paddle, non-root,
  healthcheck) + compose stack (API + Ollama) + `docs/RUNBOOK.md`.
- Suite: 27 tests green (`python -m unittest discover -s tests`).

## NOT done (GPU window ended before)

- Full production e2e (`extract_invoice` with S3 ingest + Qwen3-VL +
  fine-tuned LAYA) on a real invoice on the GPU host: NOT RUN.
- VL-markdown quality: NOT MEASURED (native backend hangs; server
  backend not deployed).
- Docker image build: NOT BUILT (needs a Linux/CUDA builder; the
  Dockerfile is untested).

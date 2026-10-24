# Invoice extraction system design

## Execution path (as implemented and measured)

1. **Ingest** (`invoice_extractor/ingest.py`): PP-StructureV3 +
   PaddleOCR-VL 1.5 when runnable (`INVOICE_INGEST=auto` prefers Paddle,
   `=paddle` forces, `=legacy` forces the fallback). Fallback: PDFs with a
   text layer go through Docling (RT-DETR layout + TableFormer); scanned
   pages and images go through HunyuanOCR Q8_0 (llama.cpp, Metal).
2. **Quality gate** (`invoice_extractor/quality.py`): min-dimension, edge
   sharpness, contrast. `USE_OMNISR=1` enables gated SR, `FORCE_OMNISR=1`
   forces it, `BYPASS_OMNISR=1` disables it. Production default: gated SR
   stays OFF until fine-tuned SR beats bicubic on validation (see below).
3. **VLM extraction** (`invoice_extractor/extract.py`): Qwen3-VL 8B via Ollama
   (JSON-schema constrained) proposes header fields + every charge line with a
   semantic category; a deterministic synonym lexicon cross-checks categories
   (lexicon overrides the LLM only for `Other Charges`/`Misc` fallbacks with a
   single specific match); ISO 6346 validates container numbers.
4. **Semantic layer** (`invoice_extractor/clm_client.py`): official CLM server
   (`Choice` over the 31-field ontology) when `CLM_BASE_URL` is set on the GPU
   host; offline, a deterministic ontology matcher (16/17 on the charge-label
   probe). The trained trigram-hash ranker is kept for ablation only: it
   underperforms the deterministic matcher (1/6 probe) and is not in the path.
5. **Evidence guard** (`invoice_extractor/validation.py`): every non-empty
   value must be supported by source text — format-aware (ISO dates,
   full-token number equality, per-entry rates). Unsupported values blank to
   `""` and are reported. Measured hallucination rate on val-20/test-20: 0.0.
6. **Schema + routing**: `fixed_json` guarantees the exact 31 keys as strings.
   All invoices route to REVIEW (auto-accept disabled: no calibrated
   per-field confidence model yet; see `configs/critical_fields.json`).

## Model roles and evidence (2026-10-01)

- **PP-StructureV3 / PaddleOCR-VL 1.5**: adopted as the primary ingest
  path (auto-selected when runnable). GPU-hour finding: Paddle 3.1.1 cannot
  execute S3/VL on RTX PRO 6000 Blackwell (no sm_120a kernels; CPU OneDNN
  bug; VL requires CUDA). PP-OCR validated on CPU. Needs H100/A100/L4.
- **OmniSR**: official CVPR'23 code (784,604 params at x2) + DF2K x2
  pretrained weights, 95-epoch document fine-tune on the RTX 6000 Pro
  (`checkpoints/omnisr/best.pt`). Downstream on 8 val LR docs (critical EM):
  bicubic 0.61 > raw LR 0.50 > fine-tuned SR 0.47 > pretrained SR 0.44,
  despite a decisive pixel win (PSNR +0.8dB, SSIM +0.10). Gated SR stays OFF;
  next is a text-aware loss. Code/weights are reference-only (no LICENSE in
  upstream repo; not redistributed).
- **CLM**: System-One decision model (Qwen3-8B embeddings + 75MB head).
  The semantic vote is integrated in the extraction loop (CLM server when
  `CLM_BASE_URL` is set, else deterministic matcher). Server validation on
  the VM was blocked by Qwen3-8B download bandwidth; vLLM 0.30 +
  contrastive-lm are installed there for the next window.
- **Docling + HunyuanOCR + Qwen3-VL 8B**: the measured stack (research.md
  two-tier blueprint). Val-20: invoice EM 0.70, critical EM 0.98. Test-20
  (frozen): invoice EM 0.35, critical EM 0.92, hallucination 0.0.

## Data, splits and limits

`scripts/generate_dataset.py` renders 5,000 reproducible synthetic freight
invoices (Pillow; freight families/aliases modelled on `invoice-maker.html`,
which also exposes a browser agent API for authentic exports — future work)
plus one LR/HR SR pair each. Split is a deterministic 3000/1000/1000 with
disjoint template/vendor/layout families (validator enforces: no id, hash,
template, vendor, or layout crosses splits). Public datasets are NOT in the
corpus: adapters exist (`dataset_adapters/`), MIDD is CC-BY-4.0 but its Zenodo
record carries annotations only, FATURA terms need per-record verification,
DocILE needs a user token. Corpus is 100% synthetic; all metrics are
synthetic-benchmark numbers, not deployment claims.
`reports/benchmark.md` is the honest TARGET|ACTUAL|STATUS record.

## Training/resume policy

Checkpoints are atomic and carry model/optimizer/scheduler/scaler, epoch,
step, config, dataset version, best metric, and RNG states. Every trainer
self-limits to <=85 min per invocation; resume with `--resume <latest.pt>`.
Full SR fine-tuning belongs on the RTX 6000 Pro (JarvisLabs); see TRAINING.md
for the exact GPU commands (requires spend approval).

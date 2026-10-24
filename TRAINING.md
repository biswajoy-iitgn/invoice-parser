# TRAINING.md — retraining run (2026-10-03)

All models were retrained on the InvoiceLab v2 corpus (DATASET.md). Every model is open source
with a license that allows free commercial use. Results: EVALUATION.md.

| Model | Role | License | Base | Where trained |
|---|---|---|---|---|
| Qwen3-VL-8B-Instruct + LoRA | reader: layout text -> cited JSON | Apache-2.0 | Qwen/Qwen3-VL-8B-Instruct | JarvisLabs A100-80GB |
| PP-OCRv6 (medium det + rec) | OCR for scanned pages (replaces HunyuanOCR) | Apache-2.0 | PaddleOCR 3.7 | not fine-tuned (see below) |
| bge-small-en-v1.5 bi-encoder | semantic engine: embedding layer | MIT | BAAI/bge-small-en-v1.5 | Mac (MPS), small model |
| LAYA choice model | semantic engine: cross-encoder layer (34 classes) | Apache-2.0 | convaiinnovations/laya | Mac (MPS), small model |
| SwinIR-light x2 | optional super-resolution (replaces OmniSR) | Apache-2.0 | DIV2K lightweight x2 | Mac (MPS), small model |

Removed for licensing: HunyuanOCR (Tencent community license, not open source; void in
EU/UK/South Korea), OmniSR (no license upstream), LayoutLMv3 (CC-BY-NC-SA, non-commercial).

GPU note: the Lightning AI teamspace `deploy-model-project` could not start any GPU (AWS cluster:
no accelerators for the account; Nebius/Lambda requests stayed Pending). With the owner's
approval the GPU stage ran on JarvisLabs; the small models trained locally to save GPU budget.

## Overfitting controls (all models)

* Splits by layout family + vendor + wording (DATASET.md); early stopping and model selection on
  validation only; the test split and the real invoices are evaluated once at the end.
* Reader: LoRA (r=16, alpha=32, dropout 0.05) on the 252 language-model projections only
  (43.6M of 8.77B parameters, vision tower frozen); loss on answer tokens only; 1 epoch with
  eval every 40 steps, early stopping (patience 3), best-checkpoint restore; inputs come from the
  production ingest code (PDF text layer, PP-OCRv6 on degraded scans), and targets contain only
  values readable in that input, so the model is never taught to emit text it cannot see.
* Semantic encoder / LAYA: duplicates collapsed, per-alias caps, OCR-noised copies, early stopping
  on the UNSEEN-wording validation slice.
* SwinIR: LR synthesized on the fly (no fixed degraded pixels to memorize); enabled in production
  only if OCR on low-res pages improves (scripts/eval_sr_ocr.py), never on PSNR alone.

## Commands

```sh
# semantic engine (CPU/MPS is enough)
.venv/bin/python scripts/build_semantic_items.py dataset_v2 --out work/sem_v2
.venv/bin/python training/train_semantic_encoder.py --data work/sem_v2 --output checkpoints/semantic-encoder
.venv/bin/python scripts/build_laya_v2_items.py --sem work/sem_v2 --out work/laya_v2
.venv/bin/python training/train_laya.py --train-items work/laya_v2/train.jsonl --val-items work/laya_v2/val.jsonl \
    --criteria work/laya_v2/criteria.json --output checkpoints/laya-invoice-v2 --epochs 3 --batch-size 16
.venv/bin/python scripts/eval_semantic.py --sem work/sem_v2 --tune       # writes configs/semantic_fusion.json (val only)

# reader (GPU): digital examples locally, scans OCR'd on the GPU host
.venv/bin/python scripts/build_reader_v2.py dataset_v2 --out work/reader_v2_digital
bash scripts/jl_stage.sh setup && bash scripts/jl_stage.sh unpack && bash scripts/jl_stage.sh ocr
bash scripts/jl_stage.sh readerdata && bash scripts/jl_stage.sh reader && bash scripts/jl_stage.sh export

# super-resolution
.venv/bin/python training/train_swinir.py --hr-list work/sr_v2/train_hr.txt --val-list work/sr_v2/val_hr.txt \
    --swinir-ref work/SwinIR --output checkpoints/swinir-doc-x2
.venv/bin/python scripts/eval_sr_ocr.py dataset_v2 --n 24
```

`scripts/remote_stage.sh` holds the equivalent stages for a Lightning AI studio (incl. rendering
the corpus on the studio with open fonts) for when GPU access there is restored.

## Why PP-OCRv6 is not fine-tuned

PP-OCRv6 already reads the degraded synthetic scans at 95–99.6% word recall and 80/81 key values
(fax, phone, 100–300 DPI). Fine-tuning OCR on synthetic renders risks overfitting to the
generator's fonts and losing real-world robustness; the reader is instead trained on PP-OCRv6's
actual output (OCR in the loop), which teaches it to work with real OCR noise.

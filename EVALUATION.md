# EVALUATION.md — metrics and how to run them

## One split, one variant

```sh
.venv/bin/python scripts/run_eval.py --split val --count 20 --variant orig \
  --output work/pred_val_orig.jsonl
.venv/bin/python scripts/evaluate.py dataset/val.jsonl work/pred_val_orig.jsonl \
  --output work/eval_val_orig.json
```

Variants: `orig` (rendered image), `lr` (degraded SR input), `bicubic`
(upsampled control), `srx2` (official OmniSR x2; needs `OMNISR_REF` and
`OMNISR_PRETRAINED`). Comparison: currency values by normalized number,
dates by ISO date, everything else case-insensitive.

## Analysis

```sh
.venv/bin/python scripts/error_analysis.py dataset/val.jsonl work/pred_val_orig.jsonl --out-dir reports
.venv/bin/python scripts/visual_errors.py dataset/val.jsonl work/pred_val_orig.jsonl   # reports/visual_errors/
.venv/bin/python scripts/dual_verify.py --split val --count 6 --output work/dual.json  # LR vs SR critical fields
.venv/bin/python scripts/profile_latency.py --split val --count 5 --output work/profile.json
```

## Test-set policy

`dataset/test.jsonl` was run exactly once (20 docs) after freezing the
pipeline, ontology, gating, and routing. Val-only tuning; the test numbers in
`reports/benchmark.md` are the frozen-config result. Do not re-run test for
iteration.

## What is NOT measured here

CER/WER, IoU/mAP, table cell/row association, PSNR/SSIM/LPIPS need dedicated
OCR/box/table/image prediction dumps; the evaluator refuses to fabricate them
(see `note` in every evaluation JSON).

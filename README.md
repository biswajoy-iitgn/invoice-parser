# Invoice extraction system (freight/logistics)

One-command inference:

```sh
.venv/bin/python -m invoice_extractor INPUT.pdf --out-dir output
```

Docs: [DESIGN.md](DESIGN.md) (architecture + evidence) ·
[SETUP.md](SETUP.md) · [DATASET.md](DATASET.md) · [TRAINING.md](TRAINING.md) ·
[EVALUATION.md](EVALUATION.md) · [INFERENCE.md](INFERENCE.md) ·
[reports/benchmark.md](reports/benchmark.md) (TARGET|ACTUAL|STATUS) ·
[research.md](research.md) (10-model benchmark background).

Dataset (5,000 invoices, 3000/1000/1000, 5,000 SR pairs):

```sh
.venv/bin/python scripts/generate_dataset.py --count 5000 --seed 91731 --output dataset --sr-pairs
.venv/bin/python scripts/validate_dataset.py dataset/manifests/all.jsonl --expected 5000
```

OmniSR fine-tuning (official model + DF2K x2 weights; see SETUP.md):

```sh
export OMNISR_REF=/opt/omni-sr-ref OMNISR_PRETRAINED=$OMNISR_REF/train_logs/OmniSR_X2_DF2K/checkpoints/epoch885_OmniSR.pth
.venv/bin/python training/train_omnisr.py --config configs/omnisr.yaml
.venv/bin/python training/train_omnisr.py --config configs/omnisr.yaml --resume checkpoints/omnisr/latest.pt
```

Eval battery (frozen-config numbers live in `reports/benchmark.md`):

```sh
.venv/bin/python scripts/run_eval.py --split val --count 20 --variant orig --output work/pred.jsonl
.venv/bin/python scripts/evaluate.py dataset/val.jsonl work/pred.jsonl
.venv/bin/python scripts/error_analysis.py dataset/val.jsonl work/pred.jsonl --out-dir reports
```

Tests: `.venv/bin/python -m unittest discover -s tests` (13 green).

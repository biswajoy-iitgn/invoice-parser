#!/usr/bin/env python3
"""Fine-tune LayoutLMv3 token classifier on invoice fields (MPS).

Labels: 31 canonical codes + 'O'. HF Trainer with early stopping on val
macro-F1 (patience 3 evals), best-model reload, resume from checkpoint.

Usage:
  .venv/bin/python training/train_layoutlmv3.py --epochs 10 --output checkpoints/layoutlmv3
  .venv/bin/python training/train_layoutlmv3.py --resume checkpoints/layoutlmv3/checkpoint-N
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LABELS = ["O"] + sorted(__import__("invoice_extractor.ontology", fromlist=["x"]).EXTERNAL_TO_CANONICAL.values())
LABEL2ID = {l: i for i, l in enumerate(LABELS)}


def load_rows(path: str) -> list[dict]:
    return [json.loads(l) for l in open(path) if l.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default="work/layoutlmv3_train.jsonl")
    ap.add_argument("--valid", default="work/layoutlmv3_val.jsonl")
    ap.add_argument("--model", default="microsoft/layoutlmv3-base")
    ap.add_argument("--output", default="checkpoints/layoutlmv3")
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--lr", type=float, default=5e-5)
    ap.add_argument("--resume", default="")
    a = ap.parse_args()

    import numpy as np
    import torch
    from torch.utils.data import Dataset
    from transformers import (AutoProcessor, EarlyStoppingCallback,
                              LayoutLMv3ForTokenClassification, Trainer,
                              TrainingArguments)

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"device: {device}", flush=True)
    processor = AutoProcessor.from_pretrained(a.model, apply_ocr=False)
    tok = processor.tokenizer

    class DocDS(Dataset):
        def __init__(self, rows):
            self.rows = rows

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, i):
            r = self.rows[i]
            enc = processor(r["words"], boxes=r["bboxes"], word_labels=[LABEL2ID[l] for l in r["labels"]],
                            truncation=True, max_length=512, return_tensors="pt")
            return {k: v.squeeze(0) for k, v in enc.items()}

    def compute_metrics(p):
        preds = np.argmax(p.predictions, axis=-1).ravel()
        labels = p.label_ids.ravel()
        mask = labels != -100
        f1s = []
        for c in range(len(LABELS)):
            tp = int(((preds == c) & (labels == c) & mask).sum())
            fp = int(((preds == c) & (labels != c) & mask).sum())
            fn = int(((preds != c) & (labels == c) & mask).sum())
            prec = tp / (tp + fp) if tp + fp else 0.0
            rec = tp / (tp + fn) if tp + fn else 0.0
            f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
        return {"macro_f1": float(np.mean(f1s))}

    model = LayoutLMv3ForTokenClassification.from_pretrained(
        a.model, num_labels=len(LABELS), id2label={i: l for i, l in enumerate(LABELS)},
        label2id=LABEL2ID)
    args = TrainingArguments(
        output_dir=a.output, use_mps_device=(device == "mps"),
        num_train_epochs=a.epochs, per_device_train_batch_size=a.batch_size,
        per_device_eval_batch_size=a.batch_size, learning_rate=a.lr,
        eval_strategy="epoch", save_strategy="epoch", load_best_model_at_end=True,
        metric_for_best_model="macro_f1", greater_is_better=True,
        save_total_limit=3, logging_steps=25, report_to="none",
        dataloader_pin_memory=False,
    )
    trainer = Trainer(
        model=model, args=args,
        train_dataset=DocDS(load_rows(a.train)), eval_dataset=DocDS(load_rows(a.valid)),
        compute_metrics=compute_metrics,
        callbacks=[EarlyStoppingCallback(early_stopping_patience=3)],
    )
    trainer.train(resume_from_checkpoint=a.resume or None)
    trainer.save_model(a.output)
    print(json.dumps(trainer.evaluate(), indent=2))


if __name__ == "__main__":
    main()

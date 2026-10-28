#!/usr/bin/env python3
"""LoRA fine-tune of Qwen3-VL-8B-Instruct (Apache-2.0) as the invoice reader (text path).

The production pipeline feeds the reader line-numbered layout text (PDF text layer or
PP-OCRv6 output) and expects cited JSON (invoice_extractor/reader.py). Training data comes
from scripts/build_reader_v2.py, i.e. from the same ingest code on held-out-family splits.

Overfitting controls
  * LoRA on language-model projections only (vision tower and embeddings frozen), dropout 0.05
  * loss on assistant tokens only (the prompt/invoice text is never a target)
  * 1 epoch by default, eval on val (unseen layout families + unseen aliases) every --eval-steps,
    early stopping (patience 3) and best-checkpoint restore; the loss curve is saved
  * val examples are a fixed mix of digital and scanned documents

Usage (Lightning GPU):
  python training/train_reader_qwen3vl.py --train work/reader_v2/train.jsonl --val work/reader_v2/val.jsonl \
      --output checkpoints/reader-qwen3vl-8b --max-train 8000
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import random
from pathlib import Path

import torch


def load(path: str, limit: int, seed: int, max_tokens: int, tok) -> list[dict]:
    rows = [json.loads(l) for l in open(path) if l.strip()]
    rnd = random.Random(seed)
    rnd.shuffle(rows)
    out = []
    for r in rows:
        if limit and len(out) >= limit:
            break
        ids, labels = encode(r["messages"], tok)
        if len(ids) > max_tokens:
            continue
        out.append({"input_ids": ids, "labels": labels, "source": r["meta"]["source"]})
    return out


def encode(messages: list[dict], tok) -> tuple[list[int], list[int]]:
    prompt = tok.apply_chat_template(messages[:-1], tokenize=False, add_generation_prompt=True)
    full = prompt + messages[-1]["content"] + tok.eos_token
    p_ids = tok(prompt, add_special_tokens=False).input_ids
    f_ids = tok(full, add_special_tokens=False).input_ids
    labels = [-100] * len(p_ids) + f_ids[len(p_ids):]
    return f_ids, labels


class Collate:
    def __init__(self, pad_id: int):
        self.pad = pad_id

    def __call__(self, batch):
        L = max(len(b["input_ids"]) for b in batch)
        ids = torch.full((len(batch), L), self.pad, dtype=torch.long)
        lab = torch.full((len(batch), L), -100, dtype=torch.long)
        att = torch.zeros((len(batch), L), dtype=torch.long)
        for i, b in enumerate(batch):
            n = len(b["input_ids"])
            ids[i, :n] = torch.tensor(b["input_ids"])
            lab[i, :n] = torch.tensor(b["labels"])
            att[i, :n] = 1
        return {"input_ids": ids, "labels": lab, "attention_mask": att}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="Qwen/Qwen3-VL-8B-Instruct")
    ap.add_argument("--train", required=True)
    ap.add_argument("--val", required=True)
    ap.add_argument("--output", default="checkpoints/reader-qwen3vl-8b")
    ap.add_argument("--max-train", type=int, default=8000)
    ap.add_argument("--max-val", type=int, default=240)
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--epochs", type=float, default=1.0)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--eval-steps", type=int, default=50)
    ap.add_argument("--patience", type=int, default=3)
    ap.add_argument("--seed", type=int, default=91731)
    ap.add_argument("--resume", default="")
    a = ap.parse_args()
    from peft import LoraConfig, get_peft_model
    from transformers import (AutoProcessor, AutoModelForImageTextToText, EarlyStoppingCallback, Trainer,
                              TrainerCallback, TrainingArguments)

    torch.manual_seed(a.seed)
    proc = AutoProcessor.from_pretrained(a.base)
    tok = proc.tokenizer
    train = load(a.train, a.max_train, a.seed, a.max_tokens, tok)
    val = load(a.val, a.max_val, a.seed + 1, a.max_tokens, tok)
    ntok = sum(len(x["input_ids"]) for x in train)
    print(json.dumps({"train_examples": len(train), "val_examples": len(val), "train_tokens": ntok,
                      "train_sources": {s: sum(x["source"] == s for x in train) for s in ("digital", "scan")},
                      "val_sources": {s: sum(x["source"] == s for x in val) for s in ("digital", "scan")}}), flush=True)
    attn = "flash_attention_2"
    try:
        import flash_attn  # noqa: F401
    except ImportError:
        attn = "sdpa"
    model = AutoModelForImageTextToText.from_pretrained(a.base, torch_dtype=torch.bfloat16, attn_implementation=attn)
    model.config.use_cache = False
    lora = LoraConfig(r=a.rank, lora_alpha=a.alpha, lora_dropout=a.dropout, bias="none", task_type="CAUSAL_LM",
                      target_modules=r".*language_model.*\.(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)")
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()
    model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.enable_input_require_grads()

    out = Path(a.output)
    steps_per_epoch = math.ceil(len(train) / a.grad_accum)
    # transformers 5 replaced group_by_length=True with train_sampling_strategy="group_by_length"
    fields = {f.name for f in dataclasses.fields(TrainingArguments)}
    by_length = ({"train_sampling_strategy": "group_by_length"} if "train_sampling_strategy" in fields
                 else {"group_by_length": True})
    args = TrainingArguments(
        output_dir=str(out), num_train_epochs=a.epochs, per_device_train_batch_size=1,
        per_device_eval_batch_size=1, gradient_accumulation_steps=a.grad_accum, learning_rate=a.lr,
        lr_scheduler_type="cosine", warmup_ratio=0.03, weight_decay=0.0, bf16=True, logging_steps=10,
        eval_strategy="steps", eval_steps=a.eval_steps, save_strategy="steps", save_steps=a.eval_steps,
        save_total_limit=3, load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False,
        report_to="none", dataloader_num_workers=2, seed=a.seed, **by_length,
        use_cpu=os.environ.get("READER_FORCE_CPU") == "1",   # local smoke tests only
        gradient_checkpointing=False, remove_unused_columns=False)
    curve = []

    class Curve(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kw):
            if logs:
                curve.append({"step": state.global_step, **{k: v for k, v in logs.items() if isinstance(v, (int, float))}})
                (out / "loss_curve.json").write_text(json.dumps(curve, indent=1))

    out.mkdir(parents=True, exist_ok=True)
    trainer = Trainer(model=model, args=args, train_dataset=train, eval_dataset=val, data_collator=Collate(tok.pad_token_id),
                      callbacks=[EarlyStoppingCallback(early_stopping_patience=a.patience), Curve()])
    print(f"steps/epoch={steps_per_epoch} eval every {a.eval_steps}", flush=True)
    trainer.train(resume_from_checkpoint=a.resume or None)
    final = trainer.evaluate()
    trainer.model.save_pretrained(str(out / "adapter"))
    proc.save_pretrained(str(out / "adapter"))
    best = trainer.state.best_metric
    summary = {"best_eval_loss": best, "final_eval": final, "best_checkpoint": trainer.state.best_model_checkpoint,
               "global_step": trainer.state.global_step, "train_examples": len(train), "train_tokens": ntok,
               "hparams": vars(a)}
    (out / "train_summary.json").write_text(json.dumps(summary, indent=2))
    print("READER_TRAIN_DONE", json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()

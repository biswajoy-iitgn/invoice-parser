#!/usr/bin/env python3
"""Supervised fine-tune of LAYA (base: convaiinnovations/laya) on invoice decisions.

Decisions come from scripts/build_laya_items.py: each corpus text region is
one Choice question over the 31 canonical fields. Supervised cross-entropy on
the choice logits (upstream uses RLCD policy-gradient; CE is the robust,
fast-converging choice for single-label classification and is documented as
such in DESIGN.md).

Usage (GPU host):
  .venv/bin/python scripts/build_laya_items.py --split train --output work/laya_train.jsonl
  python3 training/train_laya.py --train-items work/laya_train.jsonl \\
      --val-items work/laya_val.jsonl --output checkpoints/laya-invoice
  # resume:
  python3 training/train_laya.py --train-items ... --output checkpoints/laya-invoice \\
      --resume checkpoints/laya-invoice/latest.pt

Base weights: --base convaiinnovations/laya (Hub id, downloaded once) or a
local snapshot dir. Export layout matches Router.load(): model.safetensors +
rl_agent_config.json + tokenizer/ + encoder/.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import sys
from pathlib import Path

import torch
import torch.nn.functional as F
from huggingface_hub import snapshot_download
from safetensors.torch import load_file, save_file
from torch.utils.data import DataLoader, Dataset
from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checkpointing import Budget, load_checkpoint, save_checkpoint  # noqa: E402

from laya.agent import _fix_tokenizer_config  # noqa: E402
from laya.common import QTYPES, build_model, build_sequence  # noqa: E402

DATASET_VERSION = "invoice-laya-decisions-v1"


class Decisions(Dataset):
    def __init__(self, rows, criteria, instructions, tokenizer, max_len, head_max_len,
                 fields):
        self.items = []
        for r in rows:
            q = {"t": "choice", "ins": instructions, "crit": criteria}
            try:
                ids, markers = build_sequence(tokenizer, r["state"], q, max_len,
                                              head_max_len)
            except Exception:
                continue
            if len(markers) != len(criteria):
                continue
            self.items.append({"ids": ids, "markers": markers,
                               "label": fields.index(r["gold"])})

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        return self.items[i]


def collate(batch, pad_id):
    max_len = max(len(b["ids"]) for b in batch)
    max_m = max(len(b["markers"]) for b in batch)
    ids = torch.full((len(batch), max_len), pad_id, dtype=torch.long)
    attn = torch.zeros((len(batch), max_len), dtype=torch.long)
    pos = torch.zeros((len(batch), max_m), dtype=torch.long)
    mask = torch.zeros((len(batch), max_m), dtype=torch.bool)
    labels = []
    for i, b in enumerate(batch):
        n, m = len(b["ids"]), len(b["markers"])
        ids[i, :n] = torch.tensor(b["ids"])
        attn[i, :n] = 1
        pos[i, :m] = torch.tensor(b["markers"])
        mask[i, :m] = True
        labels.append(b["label"])
    return (ids, attn, pos, mask,
            torch.full((len(batch),), QTYPES["choice"], dtype=torch.long),
            torch.tensor(labels))


@torch.no_grad()
def evaluate(model, loader, device, pad_id):
    model.eval()
    correct, n, tot, nb = 0, 0, 0.0, 0
    for ids, attn, pos, mask, qt, labels in loader:
        logits, _ = model(ids.to(device), attn.to(device), pos.to(device),
                          mask.to(device), qt.to(device))
        tot += float(F.cross_entropy(logits.float(), labels.to(device)))
        correct += int((logits.argmax(-1).cpu() == labels).sum())
        n += len(labels)
        nb += 1
    return {"val_loss": tot / max(nb, 1), "val_acc": correct / max(n, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-items", required=True)
    ap.add_argument("--val-items", required=True)
    ap.add_argument("--criteria", default="")
    ap.add_argument("--base", default="convaiinnovations/laya")
    ap.add_argument("--base-dir", default="work/laya_base")
    ap.add_argument("--output", default="checkpoints/laya-invoice")
    ap.add_argument("--resume", default="")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--lr-encoder", type=float, default=2.5e-5)
    ap.add_argument("--lr-head", type=float, default=1e-4)
    ap.add_argument("--max-minutes", type=int, default=25)
    ap.add_argument("--max-steps", type=int, default=0)
    ap.add_argument("--save-every", type=int, default=100, help="steps between latest.pt writes (5 GB each)")
    ap.add_argument("--log-every", type=int, default=20, help="steps between progress lines")
    ap.add_argument("--seed", type=int, default=91731)
    a = ap.parse_args()
    random.seed(a.seed)
    torch.manual_seed(a.seed)

    base = Path(a.base_dir)
    if not (base / "model.safetensors").exists():
        print(f"downloading {a.base} ...", flush=True)
        snapshot_download(a.base, local_dir=str(base))
    _fix_tokenizer_config(str(base))
    cfg = json.loads((base / "rl_agent_config.json").read_text())
    max_len = cfg.get("max_len", 512)
    head_max_len = cfg.get("head_max_len", 192)
    tokenizer = AutoTokenizer.from_pretrained(base / "tokenizer")
    model = build_model(cfg, encoder_dir=base / "encoder")
    model.load_state_dict(load_file(str(base / "model.safetensors")), strict=True)
    model.float()
    device = "cuda" if torch.cuda.is_available() else (
        "mps" if torch.backends.mps.is_available() else "cpu")
    model.to(device)

    crit_path = a.criteria or a.train_items + ".criteria.json"
    crit = json.load(open(crit_path))
    fields = list(crit["criteria"].keys())
    train_rows = [json.loads(x) for x in open(a.train_items) if x.strip()]
    val_rows = [json.loads(x) for x in open(a.val_items) if x.strip()]
    train_ds = Decisions(train_rows, crit["criteria"], crit["instructions"], tokenizer,
                         max_len, head_max_len, fields)
    val_ds = Decisions(val_rows, crit["criteria"], crit["instructions"], tokenizer,
                       max_len, head_max_len, fields)
    loader = DataLoader(train_ds, batch_size=a.batch_size, shuffle=True,
                        collate_fn=lambda b: collate(b, tokenizer.pad_token_id))
    vloader = DataLoader(val_ds, batch_size=a.batch_size, shuffle=False,
                         collate_fn=lambda b: collate(b, tokenizer.pad_token_id))
    print(f"decisions train={len(train_ds)} val={len(val_ds)} "
          f"batches={len(loader)} device={device}", flush=True)

    enc = [p for n, p in model.named_parameters() if "encoder." in n]
    head = [p for n, p in model.named_parameters() if "encoder." not in n]
    opt = torch.optim.AdamW([{"params": enc, "lr": a.lr_encoder},
                             {"params": head, "lr": a.lr_head}], weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(
        opt, T_max=max(1, len(loader) * a.epochs), eta_min=1e-6)
    out = Path(a.output)
    start, step, best = 0, 0, 0.0
    if a.resume:
        z = load_checkpoint(a.resume, model=model, optimizer=opt, scheduler=sched,
                            map_location=device)
        start, step, best = z["epoch"], z["step"], z.get("best_metric", 0.0) or 0.0
        print(f"resumed epoch={start} step={step} best_acc={best:.4f}", flush=True)
    budget = Budget(a.max_minutes)
    for epoch in range(start, a.epochs):
        model.train()
        total = 0.0
        for ids, attn, pos, mask, qt, labels in loader:
            opt.zero_grad()
            logits, _ = model(ids.to(device), attn.to(device), pos.to(device),
                              mask.to(device), qt.to(device))
            loss = F.cross_entropy(logits.float(), labels.to(device))
            loss.backward()
            opt.step()
            step += 1
            total += float(loss.detach())
            if step % a.log_every == 0:
                print(f"step={step} loss={float(loss.detach()):.4f}", flush=True)
            if step % a.save_every == 0:
                save_checkpoint(out / "latest.pt", model=model, optimizer=opt,
                                scheduler=sched, epoch=epoch, step=step,
                                config=vars(a), dataset_version=DATASET_VERSION,
                                best_metric=best)
            if (a.max_steps and step >= a.max_steps) or budget.expired():
                save_checkpoint(out / "latest.pt", model=model, optimizer=opt,
                                scheduler=sched, epoch=epoch, step=step,
                                config=vars(a), dataset_version=DATASET_VERSION,
                                best_metric=best)
                print(f"stop epoch={epoch} step={step}; resume --resume {out}/latest.pt",
                      flush=True)
                return
        sched.step()
        m = evaluate(model, vloader, device, tokenizer.pad_token_id)
        improved = m["val_acc"] > best
        best = max(best, m["val_acc"])
        save_checkpoint(out / "latest.pt", model=model, optimizer=opt, scheduler=sched,
                        epoch=epoch + 1, step=step, config=vars(a),
                        dataset_version=DATASET_VERSION, best_metric=best)
        if improved:
            save_checkpoint(out / "best.pt", model=model, optimizer=opt, scheduler=sched,
                            epoch=epoch + 1, step=step, config=vars(a),
                            dataset_version=DATASET_VERSION, best_metric=best)
        print(f"epoch={epoch + 1} train_loss={total / max(1, len(loader)):.4f} "
              f"val_loss={m['val_loss']:.4f} val_acc={m['val_acc']:.4f} "
              f"{'BEST' if improved else ''}", flush=True)
    _export(base, out, crit_path)


def _export(base: Path, out: Path, crit_path: str = ""):
    """Materialize a Router.load()-compatible fine-tuned directory."""
    payload = torch.load(out / "best.pt", map_location="cpu", weights_only=False)
    exp = out / "laya-invoice-ft"
    if exp.exists():
        shutil.rmtree(exp)
    shutil.copytree(base, exp, ignore=shutil.ignore_patterns("*.pt"))
    save_file(payload["model"], str(exp / "model.safetensors"))
    if crit_path:
        # the engine scores options in exactly this order at inference
        shutil.copy(crit_path, exp / "criteria.json")
    print(f"exported {exp}", flush=True)


if __name__ == "__main__":
    main()

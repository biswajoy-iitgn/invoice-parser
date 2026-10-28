#!/usr/bin/env python3
"""SUPERSEDED (licensing): OmniSR removed in favor of SwinIR
(see training/train_swinir.py, invoice_extractor/sr_swinir.py). Kept for reference only.

Fine-tune the official OmniSR model on generated invoice LR/HR pairs.

Fine-tuning (preferred when weights exist):
  export OMNISR_REF=/path/to/Omni-SR OMNISR_PRETRAINED=/path/to/epoch885_OmniSR.pth
  OMNISR_FACTORY=training.omnisr_factory:build_official_omnisr \\
    .venv/bin/python training/train_omnisr.py --config configs/omnisr.yaml

Resume (required for the ~90 min GPU window; each invocation stops by itself):
  OMNISR_FACTORY=training.omnisr_factory:build_official_omnisr \\
    .venv/bin/python training/train_omnisr.py --config configs/omnisr.yaml \\
    --resume checkpoints/omnisr/latest.pt

No substitute architecture is silently presented as OmniSR: the factory must
construct the official model or raise.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import random
import sys
from pathlib import Path

import torch
from PIL import Image
from torch import nn
from torch.utils.data import Dataset, DataLoader
from torchvision.transforms.functional import to_tensor

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checkpointing import Budget, load_checkpoint, save_checkpoint  # noqa: E402

DATASET_VERSION = "invoice-sr-pairs-v1"


class Pairs(Dataset):
    """Aligned random patches from document-level LR/HR pairs.

    The generator's LR is an arbitrarily-sized degraded render, so it is
    resampled once to exactly HR/scale (degradation survives; standard
    practice when pair sizes mismatch), then aligned patches are cropped.
    """

    def __init__(self, rows: list[dict], root: Path, scale: int, patch: int, train: bool):
        self.rows = rows
        self.root = root
        self.scale = scale
        self.patch = patch
        self.train = train
        self._cache: dict[str, tuple] = {}

    def __len__(self) -> int:
        return len(self.rows)

    def _load(self, i: int):
        p = self.rows[i]["sr_pair"]
        key = p["lr_path"]
        if key not in self._cache:
            hr = Image.open(self.root / p["hr_path"]).convert("RGB")
            lr = Image.open(self.root / p["lr_path"]).convert("RGB")
            tw, th = hr.width // self.scale, hr.height // self.scale
            if (lr.width, lr.height) != (tw, th):
                lr = lr.resize((tw, th), Image.BICUBIC)
            self._cache[key] = (to_tensor(lr), to_tensor(hr))
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[key]

    def __getitem__(self, i: int):
        lr, hr = self._load(i)
        _, h, w = hr.shape
        P = min(self.patch, h - h % self.scale, w - w % self.scale)
        if self.train and (h > P or w > P):
            y = random.randrange(0, h - P + 1, self.scale)
            x = random.randrange(0, w - P + 1, self.scale)
        else:
            y, x = (h - P) // 2 // self.scale * self.scale, (w - P) // 2 // self.scale * self.scale
        s = self.scale
        return lr[:, y // s:(y + P) // s, x // s:(x + P) // s], hr[:, y:y + P, x:x + P]


@torch.no_grad()
def evaluate(model, loader, device) -> dict:
    model.eval()
    tot_l1, tot_psnr, n = 0.0, 0.0, 0
    for lr, hr in loader:
        lr, hr = lr.to(device), hr.to(device)
        pred = model(lr).clamp(0, 1)
        tot_l1 += float(nn.functional.l1_loss(pred, hr))
        mse = float(nn.functional.mse_loss(pred, hr))
        tot_psnr += -10.0 * __import__("math").log10(max(mse, 1e-12))
        n += 1
    return {"val_l1": tot_l1 / max(n, 1), "val_psnr": tot_psnr / max(n, 1)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="dataset/manifests/all.jsonl")
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--config", default="configs/omnisr.yaml")
    ap.add_argument("--resume", default="")
    ap.add_argument("--output", default="checkpoints/omnisr")
    ap.add_argument("--max-steps", type=int, default=0, help="pilot cap; 0 = none")
    a = ap.parse_args()
    import yaml
    cfg = yaml.safe_load(open(a.config))
    factory = os.getenv("OMNISR_FACTORY", "training.omnisr_factory:build_official_omnisr")
    mod, name = factory.split(":", 1)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    model = getattr(importlib.import_module(mod), name)(scale=cfg["scale"])

    torch.manual_seed(cfg.get("seed", 91731))
    random.seed(cfg.get("seed", 91731))
    device = "cuda" if torch.cuda.is_available() else (
        "mps" if torch.backends.mps.is_available() else "cpu")
    model.to(device)
    rows = [json.loads(x) for x in open(a.manifest) if x.strip()]
    rows = [r for r in rows if "sr_pair" in r
            and int(r["sr_pair"]["scale_factor"]) == int(cfg["scale"])]
    if not rows:
        raise SystemExit(f"No scale {cfg['scale']} pairs; regenerate with --sr-pairs.")
    # Invoice-level split: train invoices train, val invoices validate (no leakage).
    train_rows = [r for r in rows if r.get("split") == "train"] or rows
    val_rows = [r for r in rows if r.get("split") == "val"] or rows[-max(1, len(rows) // 10):]
    patch = int(cfg.get("patch_size", 256))
    train_ds = Pairs(train_rows, Path(a.root), cfg["scale"], patch, True)
    val_ds = Pairs(val_rows, Path(a.root), cfg["scale"], patch, False)
    loader = DataLoader(train_ds, batch_size=cfg["batch_size"], shuffle=True, num_workers=0)
    vloader = DataLoader(val_ds, batch_size=1, shuffle=False, num_workers=0)

    opt = torch.optim.AdamW(model.parameters(), lr=cfg["learning_rate"])
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=cfg["epochs"])
    scaler = torch.amp.GradScaler("cuda") if device == "cuda" else None
    start, step, best = 0, 0, float("inf")
    if a.resume:
        z = load_checkpoint(a.resume, model=model, optimizer=opt, scheduler=sched,
                            scaler=scaler, map_location=device)
        start, step = z["epoch"], z["step"]
        best = z.get("best_metric", best)
        print(f"resumed epoch={start} step={step} best_val_l1={best:.5f}")
    if device != "cuda":
        print(f"NOTICE: training device is {device}; this is not the requested RTX 6000 Pro")
    est_steps = len(loader) * (cfg["epochs"] - start)
    print(f"pairs train={len(train_ds)} val={len(val_ds)} patch={patch} "
          f"batches/epoch={len(loader)} est_steps={est_steps}")
    budget = Budget(min(85, cfg.get("max_minutes", 85)))
    out = Path(a.output)

    for epoch in range(start, cfg["epochs"]):
        model.train()
        total = 0.0
        for lr, hr in loader:
            lr, hr = lr.to(device), hr.to(device)
            opt.zero_grad()
            if scaler:
                with torch.amp.autocast("cuda"):
                    loss = nn.functional.l1_loss(model(lr), hr)
                scaler.scale(loss).backward()
                scaler.step(opt)
                scaler.update()
            else:
                loss = nn.functional.l1_loss(model(lr), hr)
                loss.backward()
                opt.step()
            step += 1
            total += float(loss.detach())
            if step % cfg["checkpoint_every_steps"] == 0:
                save_checkpoint(out / "latest.pt", model=model, optimizer=opt,
                                scheduler=sched, scaler=scaler, epoch=epoch, step=step,
                                config=cfg, dataset_version=DATASET_VERSION,
                                best_metric=best)
            if a.max_steps and step >= a.max_steps:
                save_checkpoint(out / "latest.pt", model=model, optimizer=opt,
                                scheduler=sched, scaler=scaler, epoch=epoch, step=step,
                                config=cfg, dataset_version=DATASET_VERSION,
                                best_metric=best)
                print(f"pilot cap reached; resume --resume {out}/latest.pt")
                return
            if budget.expired():
                save_checkpoint(out / "latest.pt", model=model, optimizer=opt,
                                scheduler=sched, scaler=scaler, epoch=epoch, step=step,
                                config=cfg, dataset_version=DATASET_VERSION,
                                best_metric=best)
                print(f"budget stop; resume --resume {out}/latest.pt")
                return
        sched.step()
        m = evaluate(model, vloader, device)
        improved = m["val_l1"] < best
        best = min(best, m["val_l1"])
        save_checkpoint(out / "latest.pt", model=model, optimizer=opt, scheduler=sched,
                        scaler=scaler, epoch=epoch + 1, step=step, config=cfg,
                        dataset_version=DATASET_VERSION, best_metric=best)
        if improved:
            save_checkpoint(out / "best.pt", model=model, optimizer=opt, scheduler=sched,
                            scaler=scaler, epoch=epoch + 1, step=step, config=cfg,
                            dataset_version=DATASET_VERSION, best_metric=best)
        print(f"epoch={epoch + 1} train_l1={total / max(1, len(loader)):.5f} "
              f"val_l1={m['val_l1']:.5f} val_psnr={m['val_psnr']:.2f} device={device} "
              f"{'BEST' if improved else ''}")


if __name__ == "__main__":
    main()

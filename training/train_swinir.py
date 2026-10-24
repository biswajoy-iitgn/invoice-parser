#!/usr/bin/env python3
"""Fine-tune SwinIR-light x2 (Apache-2.0; replaces OmniSR, which has no license) on invoice pages.

HR = clean InvoiceLab renders (train families only); LR is synthesized on the fly per patch
(random downscale kernel, mild blur, JPEG 35-90, sensor noise), so the model never sees the
same degraded pixels twice. Init: official DIV2K lightweight x2 weights.
Validation: fixed degraded crops from val-family pages; PSNR vs bicubic is logged each epoch and
the best checkpoint is kept. Whether SR is ever switched on in production is decided downstream
by OCR word recall (scripts/eval_sr_ocr.py), never by PSNR alone.

  git clone --depth 1 https://github.com/JingyunLiang/SwinIR /teamspace/studios/this_studio/SwinIR
  python training/train_swinir.py --hr-list work/sr_v2/train_hr.txt --val-list work/sr_v2/val_hr.txt \
      --swinir-ref /teamspace/studios/this_studio/SwinIR --output checkpoints/swinir-doc-x2
"""
from __future__ import annotations

import os
import argparse
import io
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageFilter
from torch.utils.data import DataLoader, Dataset

PRETRAINED = "https://github.com/JingyunLiang/SwinIR/releases/download/v0.0/002_lightweightSR_DIV2K_s64w8_SwinIR-S_x2.pth"


def build_model(ref: str, weights: str | None):
    sys.path.insert(0, ref)
    from models.network_swinir import SwinIR

    m = SwinIR(upscale=2, in_chans=3, img_size=64, window_size=8, img_range=1.0, depths=[6, 6, 6, 6], embed_dim=60,
               num_heads=[6, 6, 6, 6], mlp_ratio=2, upsampler="pixelshuffledirect", resi_connection="1conv")
    if weights:
        sd = torch.load(weights, map_location="cpu", weights_only=True)
        sd = sd.get("params", sd.get("params_ema", sd))
        m.load_state_dict(sd, strict=True)
    return m


def degrade(hr: Image.Image, rnd: random.Random) -> Image.Image:
    w, h = hr.size
    lr = hr.resize((w // 2, h // 2), rnd.choice([Image.Resampling.BICUBIC, Image.Resampling.BILINEAR,
                                                  Image.Resampling.BOX, Image.Resampling.LANCZOS]))
    if rnd.random() < 0.5:
        lr = lr.filter(ImageFilter.GaussianBlur(rnd.uniform(0.2, 0.8)))
    arr = np.asarray(lr).astype(np.float32)
    arr += np.random.default_rng(rnd.randrange(1 << 30)).normal(0, rnd.uniform(0, 6), arr.shape)
    lr = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    lr.save(buf, format="JPEG", quality=rnd.randint(35, 90))
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


class Patches(Dataset):
    def __init__(self, paths: list[str], patch: int, per_image: int, seed: int):
        self.paths, self.patch, self.per_image, self.seed = paths, patch, per_image, seed

    def __len__(self):
        return len(self.paths) * self.per_image

    def __getitem__(self, i):
        rnd = random.Random(self.seed * 1_000_003 + i + int(time.time() * 1000) % 997)
        img = Image.open(self.paths[i // self.per_image]).convert("RGB")
        P = self.patch
        for _ in range(10):  # prefer patches that contain ink
            x, y = rnd.randrange(0, img.width - P), rnd.randrange(0, img.height - P)
            crop = img.crop((x, y, x + P, y + P))
            if np.asarray(crop).min() < 128:
                break
        lr = degrade(crop, rnd)
        t = lambda im: torch.from_numpy(np.asarray(im, dtype=np.float32) / 255.0).permute(2, 0, 1)  # noqa: E731
        return t(lr), t(crop)


def psnr(a: torch.Tensor, b: torch.Tensor) -> float:
    return float(-10 * torch.log10(F.mse_loss(a.clamp(0, 1), b).clamp_min(1e-12)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hr-list", required=True)
    ap.add_argument("--val-list", required=True)
    ap.add_argument("--swinir-ref", required=True)
    ap.add_argument("--output", default="checkpoints/swinir-doc-x2")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--patch", type=int, default=128)
    ap.add_argument("--per-image", type=int, default=8)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--max-minutes", type=int, default=45)
    a = ap.parse_args()
    dev = os.environ.get("SWINIR_DEVICE") or ("cuda" if torch.cuda.is_available()
                                               else "mps" if torch.backends.mps.is_available() else "cpu")
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    wpath = out / "pretrained_x2.pth"
    if not wpath.exists():
        torch.hub.download_url_to_file(PRETRAINED, str(wpath))
    model = build_model(a.swinir_ref, str(wpath)).to(dev)
    train_paths = [l.strip() for l in open(a.hr_list) if l.strip()]
    val_paths = [l.strip() for l in open(a.val_list) if l.strip()]
    vds = Patches(val_paths, a.patch, 2, seed=7)
    vrnd = random.Random(7)
    val = []
    for i in range(min(len(vds), 400)):
        img = Image.open(val_paths[i // 2]).convert("RGB")
        x, y = vrnd.randrange(0, img.width - a.patch), vrnd.randrange(0, img.height - a.patch)
        crop = img.crop((x, y, x + a.patch, y + a.patch))
        lr = degrade(crop, vrnd)
        t = lambda im: torch.from_numpy(np.asarray(im, dtype=np.float32) / 255.0).permute(2, 0, 1)  # noqa: E731
        val.append((t(lr), t(crop)))
    loader = DataLoader(Patches(train_paths, a.patch, a.per_image, seed=91731), batch_size=a.batch, shuffle=True,
                        num_workers=6, drop_last=True)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=a.epochs * len(loader))

    @torch.no_grad()
    def evaluate() -> dict:
        model.eval()
        ps, pb = [], []
        for lr, hr in val:
            lr, hr = lr[None].to(dev), hr[None].to(dev)
            sr = model(lr)
            bic = F.interpolate(lr, scale_factor=2, mode="bicubic", align_corners=False)
            ps.append(psnr(sr, hr))
            pb.append(psnr(bic, hr))
        model.train()
        return {"val_psnr_sr": sum(ps) / len(ps), "val_psnr_bicubic": sum(pb) / len(pb)}

    hist = [{"epoch": 0, **evaluate()}]
    print(json.dumps(hist[-1]), flush=True)
    best, t0 = hist[-1]["val_psnr_sr"], time.time()
    torch.save(model.state_dict(), out / "best.pt")
    for ep in range(1, a.epochs + 1):
        tot = 0.0
        for i, (lr, hr) in enumerate(loader, 1):
            lr, hr = lr.to(dev), hr.to(dev)
            loss = F.l1_loss(model(lr), hr)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += float(loss.detach())
            if i % 25 == 0:
                print(f"step={i}/{len(loader)} epoch={ep} l1={tot / i:.5f}", flush=True)
        m = evaluate()
        hist.append({"epoch": ep, "train_l1": tot / len(loader), **m})
        print(json.dumps(hist[-1]), flush=True)
        if m["val_psnr_sr"] > best:
            best = m["val_psnr_sr"]
            torch.save(model.state_dict(), out / "best.pt")
        (out / "history.json").write_text(json.dumps(hist, indent=1))
        if (time.time() - t0) / 60 > a.max_minutes:
            print("budget stop", flush=True)
            break
    print("SWINIR_DONE", json.dumps({"best_val_psnr": best, "bicubic": hist[0]["val_psnr_bicubic"]}), flush=True)


if __name__ == "__main__":
    main()

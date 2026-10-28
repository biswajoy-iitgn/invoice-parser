#!/usr/bin/env python3
"""SUPERSEDED (licensing): OmniSR removed in favor of SwinIR
(see training/train_swinir.py, invoice_extractor/sr_swinir.py). Kept for reference only.

Image-level SR metrics: PSNR/SSIM of bicubic vs pretrained OmniSR vs LR.

Compares reconstructions against the HR reference on val SR pairs. Image
metrics alone never select the production SR path (downstream extraction
does), but they characterize what each method does to the pixels.

Usage:
  OMNISR_REF=/tmp/omni-sr-ref \
  OMNISR_PRETRAINED=.../epoch885_OmniSR.pth \
  .venv/bin/python scripts/sr_metrics.py --split val --count 12 --output work/sr_metrics.json
"""
import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity as ssim

ROOT = Path(__file__).resolve().parents[1]


def psnr(a: np.ndarray, b: np.ndarray) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 20.0 * math.log10(255) - 10.0 * math.log10(max(mse, 1e-12))


def gray_ssim(a: Image.Image, b: Image.Image) -> float:
    # Textbook SSIM (uniform 11x11 window). skimage 0.26 returns degenerate
    # values (>30) for these large near-uniform document images.
    from scipy.ndimage import uniform_filter
    x = np.array(a.convert("L"), dtype=np.float64) / 255.0
    y = np.array(b.convert("L"), dtype=np.float64) / 255.0
    c1, c2 = 1e-4, 9e-4
    mu1 = uniform_filter(x, 11)
    mu2 = uniform_filter(y, 11)
    v11 = np.maximum(uniform_filter(x * x, 11) - mu1 * mu1, 0)
    v22 = np.maximum(uniform_filter(y * y, 11) - mu2 * mu2, 0)
    v12 = uniform_filter(x * y, 11) - mu1 * mu2
    num = (2 * mu1 * mu2 + c1) * (2 * v12 + c2)
    den = (mu1 * mu1 + mu2 * mu2 + c1) * (v11 + v22 + c2)
    return float(np.mean(num / den))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="val")
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--output", default="work/sr_metrics.json")
    a = ap.parse_args()
    root = Path(a.root)
    rows = [json.loads(x) for x in open(root / f"{a.split}.jsonl") if x.strip()][:a.count]
    agg = {"bicubic": {"psnr": [], "ssim": []}, "srx2": {"psnr": [], "ssim": []}}
    with tempfile.TemporaryDirectory(prefix="sr-metrics-") as td:
        for rec in rows:
            pair = rec["sr_pair"]
            hr = Image.open(root / pair["hr_path"]).convert("RGB")
            lr = Image.open(root / pair["lr_path"]).convert("RGB")
            bi = lr.resize(hr.size, Image.BICUBIC)
            sr_path = str(Path(td) / f"{rec['id']}_sr.png")
            cmd = [sys.executable, str(ROOT / "scripts" / "super_resolve.py"),
                   "--input", str(root / pair["lr_path"]),
                   "--output", sr_path, "--scale", "2", "--tile", "512"]
            if os.environ.get("OMNISR_PREDICT_CKPT"):
                cmd += ["--checkpoint", os.environ["OMNISR_PREDICT_CKPT"]]
            subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=str(ROOT),
                           timeout=600)
            sr = Image.open(sr_path).convert("RGB").resize(hr.size, Image.BICUBIC)
            for name, img in (("bicubic", bi), ("srx2", sr)):
                arr, harr = np.array(img), np.array(hr)
                agg[name]["psnr"].append(psnr(arr, harr))
                agg[name]["ssim"].append(gray_ssim(img, hr))
            print(f"{rec['id']}: bi PSNR={agg['bicubic']['psnr'][-1]:.2f} "
                  f"SSIM={agg['bicubic']['ssim'][-1]:.4f} | sr PSNR={agg['srx2']['psnr'][-1]:.2f} "
                  f"SSIM={agg['srx2']['ssim'][-1]:.4f}", flush=True)
    out = {k: {"n": len(v["psnr"]),
               "psnr_mean": sum(v["psnr"]) / len(v["psnr"]),
               "ssim_mean": sum(v["ssim"]) / len(v["ssim"])} for k, v in agg.items()}
    Path(a.output).write_text(json.dumps({"summary": out, "detail": agg}, indent=2))
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

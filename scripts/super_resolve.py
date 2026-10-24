#!/usr/bin/env python3
"""SUPERSEDED (licensing): OmniSR removed in favor of SwinIR
(see training/train_swinir.py, invoice_extractor/sr_swinir.py). Kept for reference only.

Run official OmniSR super-resolution on one image (OMNISR_COMMAND target).

Usage:
  .venv/bin/python scripts/super_resolve.py --input lr.png --output sr.png --scale 2
  [--checkpoint checkpoints/omnisr/latest.pt] [--tile 512]

The checkpoint is a training/checkpointing.py payload (model key) or a raw
official state dict. Without --checkpoint, uses OMNISR_PRETRAINED or random
weights (the latter only for smoke-testing plumbing, never for eval claims).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import torch
from PIL import Image
from torchvision.transforms.functional import to_tensor, to_pil_image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from training.omnisr_factory import build_official_omnisr  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--scale", type=int, default=2)
    ap.add_argument("--checkpoint", default="")
    ap.add_argument("--tile", type=int, default=0, help="tile size; 0 = whole image")
    a = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else (
        "mps" if torch.backends.mps.is_available() else "cpu")
    model = build_official_omnisr(scale=a.scale).to(device).eval()
    def _load(pth, weights_only):
        payload = torch.load(pth, map_location="cpu", weights_only=weights_only)
        state = payload.get("model", payload) if isinstance(payload, dict) else payload
        if isinstance(state, dict) and any(k not in model.state_dict() for k in state):
            # Official checkpoints carry extra FLOPs buffers; keep model keys.
            own = model.state_dict()
            state = {k: v for k, v in state.items() if k in own}
        model.load_state_dict(state, strict=True)

    if a.checkpoint:
        _load(a.checkpoint, weights_only=False)
    elif os.environ.get("OMNISR_PRETRAINED"):
        _load(os.environ["OMNISR_PRETRAINED"], weights_only=True)

    img = Image.open(a.input).convert("RGB")
    with torch.no_grad():
        if a.tile and max(img.size) > a.tile:
            out = _tiled(model, img, a.scale, a.tile, device)
        else:
            x = to_tensor(img).unsqueeze(0).to(device)
            out = to_pil_image(model(x).clamp(0, 1).squeeze(0).cpu())
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    out.save(a.output)
    print(f"sr: {a.input} -> {a.output} scale={a.scale} device={device}")


def _tiled(model, img: Image.Image, scale: int, tile: int, device: str) -> Image.Image:
    import math
    w, h = img.size
    canvas = Image.new("RGB", (w * scale, h * scale))
    with torch.no_grad():
        for y in range(0, h, tile):
            for x in range(0, w, tile):
                crop = img.crop((x, y, min(x + tile, w), min(y + tile, h)))
                t = to_tensor(crop).unsqueeze(0).to(device)
                sr = to_pil_image(model(t).clamp(0, 1).squeeze(0).cpu())
                canvas.paste(sr, (x * scale, y * scale))
    return canvas


if __name__ == "__main__":
    main()

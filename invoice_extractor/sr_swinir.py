"""Document super-resolution with SwinIR-light x2 (Apache-2.0), fine-tuned on invoice pages.

Replaces OmniSR (no upstream license). Off by default: USE_SWINIR=1 enables it for image
inputs whose shorter side is below SR_MIN_SIDE pixels (low-DPI scans/photos). Whether it is
worth enabling is decided by scripts/eval_sr_ocr.py (OCR word recall vs bicubic), not PSNR.

  SWINIR_REF   path to a clone of https://github.com/JingyunLiang/SwinIR (network code)
  SWINIR_CKPT  fine-tuned weights (default checkpoints/swinir-doc-x2/best.pt)
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_STATE: dict = {}
_LOCK = threading.Lock()


def enabled() -> bool:
    return os.environ.get("USE_SWINIR", "0").lower() in {"1", "true", "yes"}


def should_upscale(width: int, height: int) -> bool:
    return enabled() and min(width, height) < int(os.environ.get("SR_MIN_SIDE", "1400"))


def _model():
    if "m" in _STATE:
        return _STATE["m"], _STATE["dev"]
    import torch

    ref = os.environ.get("SWINIR_REF", str(ROOT / "work" / "SwinIR"))
    sys.path.insert(0, ref)
    from models.network_swinir import SwinIR

    m = SwinIR(upscale=2, in_chans=3, img_size=64, window_size=8, img_range=1.0, depths=[6, 6, 6, 6], embed_dim=60,
               num_heads=[6, 6, 6, 6], mlp_ratio=2, upsampler="pixelshuffledirect", resi_connection="1conv")
    ckpt = os.environ.get("SWINIR_CKPT", str(ROOT / "checkpoints" / "swinir-doc-x2" / "best.pt"))
    m.load_state_dict(torch.load(ckpt, map_location="cpu", weights_only=True), strict=True)
    dev = "cuda" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
    m.eval().to(dev)
    _STATE.update(m=m, dev=dev)
    return m, dev


def upscale(src: str, dst: str, tile: int = 256, overlap: int = 16) -> None:
    """x2 SR of an image file, tiled so full pages fit in memory."""
    import numpy as np
    import torch
    from PIL import Image

    with _LOCK:
        m, dev = _model()
        img = np.asarray(Image.open(src).convert("RGB"), dtype=np.float32) / 255.0
        h, w, _ = img.shape
        out = np.zeros((h * 2, w * 2, 3), dtype=np.float32)
        weight = np.zeros((h * 2, w * 2, 1), dtype=np.float32)
        step = tile - overlap
        for y in range(0, max(1, h - overlap), step):
            for x in range(0, max(1, w - overlap), step):
                y1, x1 = min(h, y + tile), min(w, x + tile)
                y0, x0 = max(0, y1 - tile), max(0, x1 - tile)
                patch = torch.from_numpy(img[y0:y1, x0:x1]).permute(2, 0, 1)[None].to(dev)
                ph, pw = patch.shape[2], patch.shape[3]
                pad_h, pad_w = (8 - ph % 8) % 8, (8 - pw % 8) % 8
                if pad_h or pad_w:
                    patch = torch.nn.functional.pad(patch, (0, pad_w, 0, pad_h), mode="reflect")
                with torch.no_grad():
                    sr = m(patch).clamp(0, 1)[0, :, :ph * 2, :pw * 2].permute(1, 2, 0).cpu().numpy()
                out[y0 * 2:y1 * 2, x0 * 2:x1 * 2] += sr
                weight[y0 * 2:y1 * 2, x0 * 2:x1 * 2] += 1
        Image.fromarray((out / np.maximum(weight, 1e-6) * 255).round().astype("uint8")).save(dst)

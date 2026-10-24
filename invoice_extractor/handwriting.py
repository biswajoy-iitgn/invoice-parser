"""Handwriting leg (PP-OCRv6 recognition) for annotation scope.

Printed text stays on the primary PP-StructureV3/PaddleOCR-VL path. This leg
re-reads handwritten annotations (notes, corrections, signatures) with the
PP-OCRv6 recognition model (PaddleOCR trains explicitly for complex
handwriting, English included): small, fast, open-source.

Requires the Paddle stack (reads crops, so no detector needed). No TrOCR, no
VLM fallback: the production system sticks to Paddle readers.

EVAL-ONLY: no production caller — only scripts/eval_handwriting.py and
scripts/eval_hw_vm.py import second_opinion. Kept in place so those scripts
keep working; do not wire into the pipeline without a production need.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def enabled() -> bool:
    return os.environ.get("INVOICE_HANDWRITING", "on").lower() not in ("off", "0", "no")


def _engine():
    from .paddle_backends import hw_engine
    return hw_engine()


def read_crop(image) -> str:
    """Transcribe one handwritten annotation crop (PIL image)."""
    w, h = image.size
    if w < 8 or h < 8:
        return ""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        canvas = image.convert("RGB")
        path = f.name
        canvas.save(path)
    try:
        res = _engine().predict(path)
        texts = []
        for page in res:
            d = dict(page) if hasattr(page, "keys") else {}
            texts.extend(d.get("rec_texts", []))
        return " ".join(texts).strip()
    finally:
        Path(path).unlink(missing_ok=True)


def second_opinion(crops: list) -> list[str]:
    """Best-effort batch read; failures yield "" per crop, never raise."""
    if not enabled():
        return ["" for _ in crops]
    out = []
    for crop in crops:
        try:
            out.append(read_crop(crop))
        except Exception:
            out.append("")
    return out
